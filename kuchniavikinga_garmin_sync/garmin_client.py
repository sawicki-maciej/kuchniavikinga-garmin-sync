"""Garmin Connect client wrapper using ha-garmin."""

import logging
from datetime import datetime

from ha_garmin import GarminAuth, GarminClient
from ha_garmin.exceptions import GarminAuthError, GarminConnectError

from .dietly_client import DeliveredMeal

logger = logging.getLogger(__name__)


class GarminConnectClient:
    """Wrapper for ha-garmin library to log nutrition data."""

    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self._auth: GarminAuth | None = None
        self._client: GarminClient | None = None
        self._authenticated = False

    async def __aenter__(self):
        """Async context manager entry."""
        await self.authenticate()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        pass

    async def authenticate(self) -> None:
        """Authenticate with Garmin Connect."""
        logger.info("Authenticating to Garmin Connect as %s", self.email)

        try:
            self._auth = GarminAuth()
            self._auth.login(self.email, self.password)

            self._client = GarminClient(auth=self._auth)

            self._authenticated = True
            logger.info("Garmin Connect authentication successful")

        except GarminAuthError as e:
            raise Exception(f"Garmin authentication failed: {e}") from e
        except GarminConnectError as e:
            raise Exception(f"Garmin connection failed: {e}") from e

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
