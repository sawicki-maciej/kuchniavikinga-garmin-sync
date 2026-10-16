"""Garmin Connect client wrapper using ha-garmin."""

import logging
from datetime import datetime
from pathlib import Path

from google.cloud import storage
from ha_garmin import GarminAuth, GarminClient
from ha_garmin.exceptions import GarminAuthError, GarminConnectError

from .dietly_client import DeliveredMeal

logger = logging.getLogger(__name__)


class GarminConnectClient:
    """Wrapper for ha-garmin library to log nutrition data.
    
    Uses token-based authentication stored in GCS.
    Never performs password login in Cloud Run to avoid datacenter IP blocks.
    """

    def __init__(
        self, 
        state_bucket: str | None = None,
        token_filename: str = ".garmin_tokens.json",
    ):
        """Initialize Garmin client with token-based auth.
        
        Args:
            state_bucket: GCS bucket name for token storage (None = local file only)
            token_filename: Name of the token file in GCS and locally
        """
        self.state_bucket = state_bucket
        self.token_filename = token_filename
        self.local_token_path = Path(f"/tmp/{token_filename}")
        self._auth: GarminAuth | None = None
        self._client: GarminClient | None = None
        self._authenticated = False
        self._tokens_changed = False

    async def __aenter__(self):
        """Async context manager entry."""
        await self.authenticate()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - persist tokens if refreshed."""
        # Save tokens back to GCS if they changed
        if self._authenticated and self._tokens_changed and self.state_bucket:
            logger.info("Uploading refreshed tokens to GCS...")
            try:
                await self._upload_tokens_to_gcs()
                logger.info("Refreshed tokens uploaded successfully")
            except Exception as e:
                logger.error("Failed to upload refreshed tokens: %s", e)
        
        # Clean up local token file
        try:
            self.local_token_path.unlink(missing_ok=True)
        except Exception as e:
            logger.debug("Failed to clean up local token file: %s", e)

    async def authenticate(self) -> None:
        """Authenticate with Garmin Connect using tokens from GCS.
        
        Raises:
            Exception: If tokens are missing or invalid. User must run bootstrap.
        """
        # Download tokens from GCS if bucket is configured
        if self.state_bucket:
            logger.info("Downloading Garmin tokens from gs://%s/%s...", 
                       self.state_bucket, self.token_filename)
            try:
                await self._download_tokens_from_gcs()
            except Exception as e:
                logger.error("Failed to download tokens from GCS: %s", e)
                raise Exception(
                    f"Garmin tokens not found in gs://{self.state_bucket}/{self.token_filename}. "
                    "Please run the bootstrap locally and upload the token file:\n"
                    "  1. python3 -m kuchniavikinga_garmin_sync.garmin_bootstrap\n"
                    f"  2. gsutil cp ~/.garmin_tokens.json gs://{self.state_bucket}/{self.token_filename}"
                ) from e
        else:
            logger.info("Using local token file: %s", self.local_token_path)

        # Verify token file exists
        if not self.local_token_path.exists():
            raise Exception(
                f"Token file not found: {self.local_token_path}. "
                "Please run the bootstrap to generate tokens."
            )

        try:
            self._auth = GarminAuth()
            
            # Load session from file
            logger.debug("Loading tokens from %s", self.local_token_path)
            success = self._auth.load_session(str(self.local_token_path))
            if not success:
                raise Exception("Failed to load Garmin session from token file")
            
            logger.info("Session loaded successfully")
            
            # Try to refresh session if needed
            try:
                refreshed = self._auth.refresh_session()
                if refreshed:
                    logger.info("Session tokens refreshed")
                    self._tokens_changed = True
                    # Save refreshed tokens to local file
                    self._auth.save_session(str(self.local_token_path))
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
                "Please re-run the bootstrap and re-upload the token file."
            ) from e

    async def _download_tokens_from_gcs(self) -> None:
        """Download token file from GCS bucket."""
        storage_client = storage.Client()
        bucket = storage_client.bucket(self.state_bucket)
        blob = bucket.blob(self.token_filename)
        
        blob.download_to_filename(str(self.local_token_path))
        logger.debug("Downloaded tokens to %s", self.local_token_path)
    
    async def _upload_tokens_to_gcs(self) -> None:
        """Upload refreshed token file to GCS bucket."""
        storage_client = storage.Client()
        bucket = storage_client.bucket(self.state_bucket)
        blob = bucket.blob(self.token_filename)
        
        blob.upload_from_filename(str(self.local_token_path))
        logger.debug("Uploaded tokens from %s", self.local_token_path)

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
