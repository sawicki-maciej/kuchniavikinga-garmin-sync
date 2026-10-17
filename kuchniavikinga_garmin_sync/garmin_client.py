"""Garmin Connect client wrapper using ha-garmin."""

import logging
import tempfile
from datetime import date, datetime
from pathlib import Path

from google.cloud import secretmanager
from ha_garmin import GarminAuth, GarminClient
from ha_garmin.exceptions import GarminAuthError, GarminConnectError

from .dietly_client import DeliveredMeal

logger = logging.getLogger(__name__)


class GarminConnectClient:
    """Wrapper for ha-garmin library to log nutrition data.
    
    Uses token-based authentication stored in Secret Manager.
    Never performs password login in Cloud Run to avoid datacenter IP blocks.
    """

    def __init__(
        self, 
        project_id: str = "maciej-prod",
        tokens_secret: str = "kuchniavikinga-garmin-sync-garmin-tokens",
    ):
        """Initialize Garmin client with token-based auth from Secret Manager.
        
        Args:
            project_id: GCP project ID
            tokens_secret: Name of the Secret Manager secret containing tokens
        """
        self.project_id = project_id
        self.tokens_secret = tokens_secret
        self.local_token_path = Path(tempfile.mktemp(suffix='.json'))
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
        # Save tokens back to Secret Manager if they changed
        if self._authenticated and self._tokens_changed:
            logger.info("Uploading refreshed tokens to Secret Manager...")
            try:
                await self._add_secret_version()
                logger.info("Refreshed tokens uploaded successfully")
            except Exception as e:
                logger.error("Failed to upload refreshed tokens: %s", e)
        
        # Clean up local token file
        try:
            self.local_token_path.unlink(missing_ok=True)
        except Exception as e:
            logger.debug("Failed to clean up local token file: %s", e)

    async def authenticate(self) -> None:
        """Authenticate with Garmin Connect using tokens from Secret Manager.
        
        Raises:
            Exception: If tokens are missing or invalid. User must run bootstrap.
        """
        logger.info("Loading Garmin tokens from Secret Manager...")
        
        try:
            await self._download_tokens_from_secret_manager()
        except Exception as e:
            logger.error("Failed to load tokens from Secret Manager: %s", e)
            raise Exception(
                f"Garmin tokens not found in Secret Manager ({self.tokens_secret}). "
                "Please run the bootstrap from your Mac:\n"
                "  cd contrib/cloudrun && ./setup.sh garmin-login"
            ) from e

        try:
            self._auth = GarminAuth()
            
            # Load session from file
            logger.debug("Loading tokens from %s", self.local_token_path)
            success = self._auth.load_session(str(self.local_token_path))
            if not success:
                raise Exception("Failed to load Garmin session from token file")
            
            logger.info("Session loaded successfully")
            
            # Try to refresh session if needed (refresh_session is async in ha-garmin)
            try:
                refreshed = await self._auth.refresh_session()
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
                "Please re-run the bootstrap from your Mac:\n"
                "  cd contrib/cloudrun && ./setup.sh garmin-login"
            ) from e

    async def _download_tokens_from_secret_manager(self) -> None:
        """Download token from Secret Manager to local file."""
        client = secretmanager.SecretManagerServiceClient()
        secret_name = f"projects/{self.project_id}/secrets/{self.tokens_secret}/versions/latest"
        
        logger.debug("Accessing secret: %s", secret_name)
        response = client.access_secret_version(name=secret_name)
        
        token_data = response.payload.data.decode('UTF-8')
        
        # Write to temp file
        with open(self.local_token_path, 'w') as f:
            f.write(token_data)
        
        logger.debug("Downloaded tokens to %s", self.local_token_path)
    
    async def _add_secret_version(self) -> None:
        """Upload refreshed tokens as new secret version."""
        # Read refreshed tokens
        with open(self.local_token_path, 'r') as f:
            token_data = f.read()
        
        client = secretmanager.SecretManagerServiceClient()
        parent = f"projects/{self.project_id}/secrets/{self.tokens_secret}"
        
        logger.debug("Adding new version to secret: %s", parent)
        client.add_secret_version(
            parent=parent,
            payload={'data': token_data.encode('UTF-8')}
        )
        
        logger.debug("New token version added to Secret Manager")

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
    
    async def list_nutrition_logs(self, target_date: date, dump_raw: bool = False) -> list[dict]:
        """List nutrition log entries for a specific date.
        
        This queries the Garmin nutrition-service API which returns meal details
        containing individual food log entries.
        
        Args:
            target_date: The date to query
            dump_raw: If True, log the raw response structure (values redacted)
            
        Returns:
            List of nutrition log entries (food items logged for the day)
        """
        try:
            # Use ha-garmin's get_nutrition_log which fetches from
            # /nutrition-service/food/logs/{date}?includeGoals=true&includeNutrition=true
            data = await self._client.get_nutrition_log(target_date)
            
            if dump_raw:
                # Log the raw structure with redacted values for debugging
                redacted = self._redact_nutrition_response(data)
                logger.warning(
                    "DUMP_RAW nutrition log response for %s: %s",
                    target_date,
                    redacted
                )
            
            # Extract food logs from the response
            # Structure: {"mealDetails": [{"meal": {...}, "foodLogs": [...]}]}
            logs = []
            if isinstance(data, dict):
                meal_details = data.get("mealDetails", [])
                if isinstance(meal_details, list):
                    for meal_detail in meal_details:
                        if isinstance(meal_detail, dict):
                            food_logs = meal_detail.get("foodLogs", [])
                            if isinstance(food_logs, list):
                                logs.extend(food_logs)
                
                # If we didn't find logs, log the structure
                if not logs and data:
                    logger.warning(
                        "No food logs found for %s. Available keys: %s. mealDetails count: %d",
                        target_date,
                        list(data.keys()),
                        len(meal_details) if isinstance(meal_details, list) else 0
                    )
            
            logger.info("Found %d nutrition log entries for %s", len(logs), target_date)
            return logs
            
        except Exception as e:
            logger.error("Failed to list nutrition logs for %s: %s", target_date, e)
            raise
    
    @staticmethod
    def _redact_nutrition_response(data: dict) -> dict:
        """Redact sensitive values from nutrition response while keeping structure."""
        if not isinstance(data, dict):
            return data
        
        redacted = {}
        for key, value in data.items():
            if isinstance(value, dict):
                redacted[key] = GarminConnectClient._redact_nutrition_response(value)
            elif isinstance(value, list):
                if value and isinstance(value[0], dict):
                    # Keep first item structure, redact others
                    redacted[key] = [
                        GarminConnectClient._redact_nutrition_response(value[0]),
                        f"... {len(value)-1} more items ..."
                    ] if len(value) > 1 else [GarminConnectClient._redact_nutrition_response(value[0])]
                else:
                    redacted[key] = f"[{len(value)} items]"
            elif isinstance(value, (str, int, float, bool)):
                # Keep IDs, keys, counts, booleans; redact string values
                if any(k in key.lower() for k in ['id', 'count', 'type', 'name']) or isinstance(value, (int, bool)):
                    redacted[key] = value
                else:
                    redacted[key] = "<redacted>"
            else:
                redacted[key] = type(value).__name__
        
        return redacted
    
    async def delete_nutrition_log(self, log_id: int) -> bool:
        """Delete a nutrition log entry by ID.
        
        Uses the Garmin nutrition-service API endpoint to delete a food log entry.
        
        Args:
            log_id: The Garmin food log ID to delete (from foodLog.foodLogId)
            
        Returns:
            True if deletion succeeded
        """
        try:
            # Delete a food log entry
            # Endpoint: DELETE /nutrition-service/food/logs/{foodLogId}
            from ha_garmin.client import NUTRITION_LOGS_URL
            
            # NUTRITION_LOGS_URL is https://connect.garmin.com/gc-api/nutrition-service/food/logs
            # To delete: DELETE /nutrition-service/food/logs/{foodLogId}
            url = f"{NUTRITION_LOGS_URL}/{log_id}"
            
            # Access the internal _request method
            await self._client._request("DELETE", url)
            logger.info("Deleted Garmin food log %s", log_id)
            return True
            
        except Exception as e:
            logger.error("Failed to delete food log %s: %s", log_id, e)
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
