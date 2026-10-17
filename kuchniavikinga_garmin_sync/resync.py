"""Resync mode: replace incorrectly logged Garmin nutrition entries."""

import logging
from datetime import date

from .dietly_client import DietlyClient
from .garmin_client import GarminConnectClient
from .state import SyncState

logger = logging.getLogger(__name__)


class ResyncService:
    """Service for resyncing nutrition data to Garmin Connect."""

    def __init__(
        self,
        dietly_client: DietlyClient,
        garmin_client: GarminConnectClient,
        state: SyncState,
        dry_run: bool = False,
    ):
        """Initialize resync service.

        Args:
            dietly_client: Client for fetching meal data from Kuchnia Vikinga
            garmin_client: Client for Garmin Connect operations
            state: Sync state manager
            dry_run: If True, only log actions without making changes
        """
        self.dietly = dietly_client
        self.garmin = garmin_client
        self.state = state
        self.dry_run = dry_run

    async def resync_date(self, target_date: date) -> dict[str, int]:
        """Resync all meals for a specific date.

        This will:
        1. Find Garmin nutrition logs created by this tool for the date
        2. Delete those entries from Garmin
        3. Clear them from sync state
        4. Re-fetch meals from Kuchnia Vikinga
        5. Re-log them to Garmin with correct data

        Args:
            target_date: The date to resync

        Returns:
            Dict with counts: deleted, relogged, skipped
        """
        logger.info("=" * 60)
        logger.info("RESYNC MODE: %s%s", target_date, " (DRY RUN)" if self.dry_run else "")
        logger.info("=" * 60)

        stats = {
            "deleted": 0,
            "relogged": 0,
            "skipped": 0,
        }

        # Step 1: Find entries to delete
        entries_to_delete = await self._find_entries_to_delete(target_date)

        if not entries_to_delete:
            logger.info("No entries found to resync for %s", target_date)
            return stats

        logger.info("Found %d entries to delete and re-log", len(entries_to_delete))

        # Step 2: Delete from Garmin
        for entry in entries_to_delete:
            log_id = entry.get("garmin_log_id")
            meal_key = entry.get("meal_key")
            meal_name = entry.get("meal_name", "Unknown")

            if self.dry_run:
                logger.info(
                    "[DRY RUN] Would delete: %s (ID: %s, key: %s)",
                    meal_name,
                    log_id if log_id else "not stored",
                    meal_key,
                )
                stats["deleted"] += 1
            else:
                try:
                    if log_id:
                        await self.garmin.delete_nutrition_log(log_id)
                        logger.info("Deleted Garmin log %s (%s)", log_id, meal_name)
                    else:
                        logger.warning(
                            "No Garmin log ID for %s, cannot delete from Garmin directly",
                            meal_name,
                        )

                    # Clear from state
                    self.state.clear_meal(meal_key)
                    stats["deleted"] += 1

                except Exception as e:
                    logger.error("Failed to delete %s: %s", meal_name, e)
                    stats["skipped"] += 1

        # Step 3: Save state after deletions
        if not self.dry_run:
            self.state.save()
            logger.info("Sync state updated after deletions")

        # Step 4: Re-fetch meals from Kuchnia Vikinga
        logger.info("Fetching fresh meal data from Kuchnia Vikinga for %s", target_date)
        meals = await self.dietly.get_delivered_meals(target_date, target_date)

        if not meals:
            logger.warning("No meals found in Kuchnia Vikinga for %s", target_date)
            return stats

        logger.info("Found %d meals to re-log", len(meals))

        # Step 5: Re-log to Garmin
        for meal in meals:
            meal_key = self.state._meal_key(meal)

            if self.dry_run:
                logger.info(
                    "[DRY RUN] Would re-log: %s - %s (C:%s P:%s F:%s)",
                    meal.meal_name,
                    meal.dish_name,
                    f"{meal.nutrition.carbs:.1f}g" if meal.nutrition.carbs else "0g",
                    f"{meal.nutrition.protein:.1f}g" if meal.nutrition.protein else "0g",
                    f"{meal.nutrition.fat:.1f}g" if meal.nutrition.fat else "0g",
                )
                stats["relogged"] += 1
            else:
                try:
                    result = await self.garmin.log_meal(meal)

                    # Extract log ID from result if available
                    log_id = result.get("nutritionLogId") or result.get("logId")

                    self.state.mark_synced(meal, garmin_log_id=log_id)
                    stats["relogged"] += 1

                    logger.info(
                        "Re-logged: %s - %s (Garmin ID: %s)",
                        meal.meal_name,
                        meal.dish_name,
                        log_id if log_id else "not returned",
                    )

                except Exception as e:
                    logger.error("Failed to re-log %s: %s", meal.dish_name, e)
                    stats["skipped"] += 1

        # Step 6: Save final state
        if not self.dry_run:
            self.state.save()
            logger.info("Sync state saved after re-logging")

        # Summary
        logger.info("=" * 60)
        logger.info("RESYNC COMPLETE%s", " (DRY RUN)" if self.dry_run else "")
        logger.info("  Deleted: %d", stats["deleted"])
        logger.info("  Re-logged: %d", stats["relogged"])
        logger.info("  Skipped: %d", stats["skipped"])
        logger.info("=" * 60)

        return stats

    async def _find_entries_to_delete(self, target_date: date) -> list[dict]:
        """Find Garmin nutrition log entries to delete for a date.

        Tries two sources:
        1. Sync state (if we stored Garmin log IDs)
        2. Garmin API (list all logs for the date, filter by name "KV: ...")

        Args:
            target_date: The date to query

        Returns:
            List of entries with meal_key, meal_name, and optionally garmin_log_id
        """
        entries = []

        # Source 1: Check sync state for stored IDs
        synced_meals = self.state.get_synced_meals_for_date(target_date)
        logger.info("Found %d meals in sync state for %s", len(synced_meals), target_date)

        for record in synced_meals:
            meal_key = record["key"]
            meal_data = record["data"]
            garmin_log_id = meal_data.get("garmin_log_id")
            meal_name = meal_data.get("meal_name", "Unknown")

            entries.append({
                "meal_key": meal_key,
                "meal_name": meal_name,
                "garmin_log_id": garmin_log_id,
                "source": "state",
            })

        # Source 2: Query Garmin API for entries named "KV: ..."
        # This catches entries if state was lost or IDs weren't stored
        try:
            garmin_logs = await self.garmin.list_nutrition_logs(target_date)
            logger.info("Found %d total nutrition logs in Garmin for %s", len(garmin_logs), target_date)

            for log in garmin_logs:
                log_name = log.get("name") or log.get("foodName") or ""
                log_id = log.get("nutritionLogId") or log.get("logId")

                # Only consider entries created by this tool (named "KV: ...")
                if log_name.startswith("KV: ") and log_id:
                    # Check if we already have it from state
                    if not any(e.get("garmin_log_id") == log_id for e in entries):
                        logger.info("Found Garmin log not in state: %s (ID: %s)", log_name, log_id)
                        entries.append({
                            "meal_key": f"garmin-{log_id}",  # Synthetic key
                            "meal_name": log_name,
                            "garmin_log_id": log_id,
                            "source": "garmin",
                        })

        except Exception as e:
            logger.warning("Could not query Garmin API for logs: %s", e)
            logger.info("Will rely on sync state only")

        return entries
