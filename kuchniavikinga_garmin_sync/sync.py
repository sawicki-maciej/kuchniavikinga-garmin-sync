"""Main sync orchestration."""

import asyncio
import logging
from datetime import date, timedelta

from .dietly_client import DietlyClient
from .garmin_client import GarminConnectClient
from .state import SyncState

logger = logging.getLogger(__name__)


class VikingaGarminSync:
    """Orchestrates syncing Kuchnia Vikinga meals to Garmin Connect."""

    def __init__(
        self,
        vikinga_email: str,
        vikinga_password: str,
        garmin_email: str,
        garmin_password: str,
        state_bucket: str | None = None,
        days_back: int = 1,
        dry_run: bool = False,
        dump_raw: bool = False,
    ):
        """Initialize the sync service.

        Args:
            vikinga_email: Kuchnia Vikinga panel account email
            vikinga_password: Kuchnia Vikinga panel account password
            garmin_email: Garmin Connect account email
            garmin_password: Garmin Connect account password
            state_bucket: GCS bucket for state storage (None for local)
            days_back: How many days back to sync (default: 1)
            dry_run: If True, don't write to Garmin (default: False)
            dump_raw: If True, log raw API responses with redacted values
        """
        self.vikinga_email = vikinga_email
        self.vikinga_password = vikinga_password
        self.garmin_email = garmin_email
        self.garmin_password = garmin_password
        self.state_bucket = state_bucket
        self.days_back = days_back
        self.dry_run = dry_run
        self.dump_raw = dump_raw

        self.state = SyncState(bucket_name=state_bucket)

    async def run(self) -> dict:
        """Run the sync operation.

        Returns:
            Dict with sync statistics
        """
        logger.info(
            "Starting Vikinga → Garmin sync (days_back=%d, dry_run=%s)",
            self.days_back,
            self.dry_run,
        )

        # Load sync state
        self.state.load()

        # Calculate date range
        end_date = date.today() - timedelta(days=1)  # Yesterday
        start_date = end_date - timedelta(days=self.days_back - 1)

        logger.info("Syncing meals from %s to %s", start_date, end_date)

        # Fetch meals from Kuchnia Vikinga
        async with DietlyClient(
            self.vikinga_email, self.vikinga_password, dump_raw=self.dump_raw
        ) as dietly:
            meals = await dietly.get_delivered_meals(start_date, end_date)

        if not meals:
            logger.info("No delivered meals found in date range")
            return {
                "total_meals": 0,
                "new_meals": 0,
                "synced": 0,
                "errors": 0,
                "skipped": 0,
            }

        # Filter out already-synced meals
        new_meals = [meal for meal in meals if not self.state.is_synced(meal)]

        logger.info(
            "Found %d delivered meals (%d new, %d already synced)",
            len(meals),
            len(new_meals),
            len(meals) - len(new_meals),
        )

        if not new_meals:
            logger.info("All meals already synced")
            return {
                "total_meals": len(meals),
                "new_meals": 0,
                "synced": 0,
                "errors": 0,
                "skipped": len(meals),
            }

        # Sync to Garmin
        synced_count = 0
        error_count = 0

        if self.dry_run:
            logger.info("DRY RUN: Would sync %d meals", len(new_meals))
            for meal in new_meals:
                logger.info(
                    "  [DRY RUN] %s - %s: %s (%.0f kcal)",
                    meal.delivery_date,
                    meal.meal_name,
                    meal.dish_name,
                    meal.nutrition.calories,
                )
            synced_count = len(new_meals)
        else:
            async with GarminConnectClient(
                self.garmin_email, self.garmin_password
            ) as garmin:
                for meal in new_meals:
                    try:
                        await garmin.log_meal(meal)
                        self.state.mark_synced(meal)
                        synced_count += 1

                        # Rate limiting: sleep between requests
                        if synced_count < len(new_meals):
                            await asyncio.sleep(2)

                    except Exception as e:
                        logger.error(
                            "Failed to sync meal %s - %s: %s",
                            meal.delivery_date,
                            meal.dish_name,
                            e,
                        )
                        error_count += 1

        # Save updated state (even on partial success)
        if synced_count > 0 and not self.dry_run:
            self.state.save()

        logger.info(
            "Sync completed: %d meals synced, %d errors", synced_count, error_count
        )

        return {
            "total_meals": len(meals),
            "new_meals": len(new_meals),
            "synced": synced_count,
            "errors": error_count,
            "skipped": len(meals) - len(new_meals),
        }
