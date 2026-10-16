"""Garmin Connect client wrapper using ha-garmin."""

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path

from google.cloud import secretmanager
from ha_garmin import GarminAuth, GarminClient
from ha_garmin.exceptions import GarminAuthError, GarminConnectError

from .dietly_client import DeliveredMeal

logger = logging.getLogger(__name__)


class GarminConnectClient:
    """Wrapper for ha-garmin library to log nutrition data.
    
    Uses token-based authentication from Google Secret Manager.
    Never performs password login in Cloud Run to avoid datacenter IP blocks.
    """

    def __init__(
        self, 
        project_id: str = "maciej-prod",
        tokens_secret: str = "kuchniavikinga-garmin-sync-garmin-tokens",
    ):
        """Initialize Garmin client with token-based auth.
        
        Args:
            project_id: GCP project ID
            tokens_secret: Name of the Secret Manager secret containing tokens
        """
        self.project_id = project_id
        self.tokens_secret = tokens_secret
        self._auth: GarminAuth | None = None
        self._client: GarminClient | None = None
        self._authenticated = False
        self._session_file: Path | None = None
        self._tokens_refreshed = False

    async def __aenter__(self):
        """Async context manager entry."""
        await self.authenticate()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - persist refreshed tokens."""
        # Save refreshed tokens back to Secret Manager if they changed
        if self._authenticated and self._tokens_refreshed and self._session_file:
            logger.info("Persisting refreshed tokens to Secret Manager...")
            try:
                await self._save_tokens_to_secret_manager()
                logger.info("Refreshed tokens saved successfully")
            except Exception as e:
                logger.error("Failed to save refreshed tokens: %s", e)
        
        # Clean up temp file
        if self._session_file:
            try:
                self._session_file.unlink(missing_ok=True)
            except Exception as e:
                logger.warning("Failed to clean up session file: %s", e)

    async def authenticate(self) -> None:
        """Authenticate with Garmin Connect using tokens from Secret Manager.
        
        Raises:
            Exception: If tokens are missing or invalid. User must run bootstrap.
        """
        logger.info("Loading Garmin tokens from Secret Manager...")

        # Load tokens from Secret Manager
        try:
            tokens_data = await self._load_tokens_from_secret_manager()
        except Exception as e:
            logger.error("Failed to load tokens from Secret Manager: %s", e)
            raise Exception(
                "Garmin tokens not found in Secret Manager. "
                "Please run the bootstrap command on your Mac: "
                "python -m kuchniavikinga_garmin_sync.garmin_bootstrap"
            ) from e

        # Save tokens to temporary file for ha-garmin
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write(tokens_data)
            self._session_file = Path(f.name)
        
        logger.debug("Tokens saved to temporary file: %s", self._session_file)

        try:
            self._auth = GarminAuth()
            
            # Load session from file
            success = self._auth.load_session(self._session_file)
            if not success:
                raise Exception("Failed to load Garmin session from tokens")
            
            logger.info("Session loaded successfully")
            
            # Try to refresh session if needed
            try:
                refreshed = self._auth.refresh_session()
                if refreshed:
                    logger.info("Session tokens refreshed")
                    self._tokens_refreshed = True
                    # Save refreshed tokens back to temp file
                    self._auth.save_session(self._session_file)
                else:
                    logger.debug("Session tokens are still valid, no refresh needed")
            except Exception as e:
                logger.warning("Failed to refresh session, continuing with existing tokens: %s", e)

            self._client = GarminClient(auth=self._auth)

            self._authenticated = True
            logger.info("Garmin Connect authentication successful")

        except (GarminAuthError, GarminConnectError) as e:
            logger.error("Garmin authentication failed: %s", e)
            raise Exception(
                f"Garmin authentication failed: {e}. "
                "Tokens may be expired or invalid. "
                "Please re-run the bootstrap command on your Mac: "
                "python -m kuchniavikinga_garmin_sync.garmin_bootstrap"
            ) from e

    async def _load_tokens_from_secret_manager(self) -> str:
        """Load tokens from Secret Manager.
        
        Returns:
            JSON string containing the session tokens.
        """
        client = secretmanager.SecretManagerServiceClient()
        secret_name = f"projects/{self.project_id}/secrets/{self.tokens_secret}/versions/latest"
        
        logger.debug("Accessing secret: %s", secret_name)
        response = client.access_secret_version(name=secret_name)
        
        return response.payload.data.decode('UTF-8')
    
    async def _save_tokens_to_secret_manager(self) -> None:
        """Save refreshed tokens back to Secret Manager."""
        if not self._session_file:
            raise Exception("No session file to save")
        
        # Read the refreshed tokens from file
        with open(self._session_file, 'r') as f:
            tokens_data = f.read()
        
        # Verify it's valid JSON
        json.loads(tokens_data)
        
        # Add new version to Secret Manager
        client = secretmanager.SecretManagerServiceClient()
        parent = f"projects/{self.project_id}/secrets/{self.tokens_secret}"
        
        logger.debug("Adding new version to secret: %s", parent)
        client.add_secret_version(
            parent=parent,
            payload={'data': tokens_data.encode('UTF-8')}
        )
        
        logger.info("New token version added to Secret Manager")

    async def log_meal(self, meal: DeliveredMeal) -> dict:
        """Log a meal to Garmin Connect nutrition.

        Args:
            meal: The delivered meal to log

        Returns:
            Response from Garmin API

        Raises:
            Exception: If not authenticated or if the API call fails
        """
        if not self._authenticated or not self._client:
            raise Exception("Not authenticated - call authenticate() first")

        # Prepare meal data
        # Garmin expects integers for macros
        calories = int(meal.nutrition.calories)
        carbs = int(meal.nutrition.carbs) if meal.nutrition.carbs is not None else None
        protein = (
            int(meal.nutrition.protein) if meal.nutrition.protein is not None else None
        )
        fat = int(meal.nutrition.fat) if meal.nutrition.fat is not None else None

        # Create a descriptive name
        name = f"KV: {meal.dish_name[:40]}"  # Limit length

        # Create timestamp for the meal
        # Use delivery date with approximate meal time based on meal name
        meal_time = self._estimate_meal_time(meal.meal_name)
        timestamp = datetime.combine(
            meal.delivery_date, datetime.strptime(meal_time, "%H:%M:%S").time()
        ).isoformat()

        logger.info(
            "Logging meal: %s on %s (%.0f kcal, P:%.0fg C:%.0fg F:%.0fg)",
            name,
            meal.delivery_date,
            calories,
            protein or 0,
            carbs or 0,
            fat or 0,
        )

        try:
            result = await self._client.add_nutrition_log(
                calories=calories,
                carbs=carbs,
                protein=protein,
                fat=fat,
                name=name,
                timestamp=timestamp,
            )
            logger.debug("Garmin API response: %s", result)
            return result

        except Exception as e:
            logger.error("Failed to log meal %s: %s", name, e)
            raise

    @staticmethod
    def _estimate_meal_time(meal_name: str) -> str:
        """Estimate meal time based on meal name.

        Maps Polish meal names to approximate times:
        - Śniadanie (Breakfast): 07:00
        - II Śniadanie (Second Breakfast): 10:00
        - Obiad (Lunch): 13:00
        - Podwieczorek (Snack): 16:00
        - Kolacja (Dinner): 19:00
        """
        meal_times = {
            "II Śniadanie": "10:00:00",
            "Śniadanie": "07:00:00",
            "Obiad": "13:00:00",
            "Podwieczorek": "16:00:00",
            "Kolacja": "19:00:00",
        }

        # Normalize meal name (case-insensitive match)
        # Check longer/more specific names first
        for key, time in meal_times.items():
            if key.lower() in meal_name.lower():
                return time

        # Default to lunch time if unknown
        logger.warning("Unknown meal name '%s', defaulting to 13:00:00", meal_name)
        return "13:00:00"
