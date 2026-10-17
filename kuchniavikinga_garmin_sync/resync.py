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
        2. Verify all state entries have matching Garmin entries (safety check)
        3. Delete ALL matched entries from Garmin FIRST
        4. Verify deletions succeeded by re-listing
        5. Clear them from sync state
        6. Re-fetch meals from Kuchnia Vikinga
        7. Re-log them to Garmin with correct data

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
        entries_to_delete, unmatched_state_entries = await self._find_entries_to_delete(target_date)

        if not entries_to_delete:
            logger.info("No entries found to resync for %s", target_date)
            return stats
        
        # Step 2: Safety check - abort if any state entries have no Garmin match
        if unmatched_state_entries:
            logger.error(
                "SAFETY ABORT: %d state entries have no matching Garmin entry",
                len(unmatched_state_entries)
            )
            logger.error("This would cause duplicates. State entries without Garmin match:")
            for entry in unmatched_state_entries:
                logger.error("  - %s (key: %s)", entry.get("meal_name"), entry.get("meal_key"))
            
            if not self.dry_run:
                logger.error("Aborting live resync to prevent duplicates")
                raise Exception(
                    f"Cannot resync: {len(unmatched_state_entries)} state entries have no Garmin match. "
                    "This would create duplicates."
                )
            else:
                logger.warning("[DRY RUN] Would abort due to unmatched state entries")
                stats["skipped"] = len(unmatched_state_entries)
                return stats

        logger.info("Found %d entries to delete and re-log", len(entries_to_delete))

        # Step 3: Delete ALL entries from Garmin FIRST (safer - abort if any fails)
        deleted_log_ids = []
        meal_keys_to_clear = []
        
        if not self.dry_run:
            logger.info("Deleting ALL matched entries from Garmin...")
            for entry in entries_to_delete:
                log_id = entry.get("garmin_log_id")
                meal_key = entry.get("meal_key")
                meal_name = entry.get("meal_name", "Unknown")

                if not log_id:
                    logger.warning(
                        "No Garmin log ID for %s, cannot delete from Garmin",
                        meal_name,
                    )
                    stats["skipped"] += 1
                    continue

                try:
                    await self.garmin.delete_nutrition_log(log_id)
                    logger.info("Deleted Garmin log %s (%s)", log_id, meal_name[:50])
                    deleted_log_ids.append(log_id)
                    meal_keys_to_clear.append(meal_key)
                    stats["deleted"] += 1

                except Exception as e:
                    logger.error("ABORT: Failed to delete %s (ID: %s): %s", meal_name[:50], log_id, e)
                    logger.error("Aborting resync - will NOT re-log anything")
                    raise Exception(f"Delete failed for {log_id}, aborting to prevent duplicates") from e
            
            # Step 4: Verify deletions succeeded by re-listing
            logger.info("Verifying deletions...")
            remaining_logs = await self.garmin.list_nutrition_logs(target_date, dump_raw=False)
            remaining_ids = set()
            for log in remaining_logs:
                log_id = log.get("logId")
                if log_id:
                    remaining_ids.add(log_id)
            
            # Check if any deleted IDs still exist
            still_exist = [lid for lid in deleted_log_ids if lid in remaining_ids]
            if still_exist:
                logger.error("ABORT: %d deleted entries still exist in Garmin:", len(still_exist))
                for lid in still_exist:
                    logger.error("  - %s", lid)
                logger.error("Aborting resync - will NOT re-log anything")
                raise Exception(f"{len(still_exist)} deletions failed verification, aborting")
            
            logger.info("✓ All %d deletions verified", len(deleted_log_ids))
            
            # Step 5: Clear from state now that deletions are confirmed
            for meal_key in meal_keys_to_clear:
                self.state.clear_meal(meal_key)
            
            self.state.save()
            logger.info("Sync state updated after deletions")
        else:
            # Dry run - just log what would be deleted
            for entry in entries_to_delete:
                log_id = entry.get("garmin_log_id")
                meal_key = entry.get("meal_key")
                meal_name = entry.get("meal_name", "Unknown")
                logger.info(
                    "[DRY RUN] Would delete: %s (ID: %s, key: %s)",
                    meal_name[:50],
                    log_id if log_id else "not stored",
                    meal_key,
                )
                stats["deleted"] += 1

        # Step 6: Re-fetch meals from Kuchnia Vikinga
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
                    # logId is a 32-char hex string like '1ecbb709157543948c25eafa2c74edc7'
                    log_id = result.get("logId") or result.get("nutritionLogId")

                    if log_id:
                        logger.debug("Stored Garmin logId for re-logged meal: %s", log_id)

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

    async def _find_entries_to_delete(self, target_date: date) -> tuple[list[dict], list[dict]]:
        """Find Garmin nutrition log entries to delete for a date.

        Uses real Garmin API structure:
        - Food names are "KV: <dish_name>" (truncated to ~44 chars)
        - Slot name is in mealSlotName field (BREAKFAST, LUNCH, DINNER, SNACKS)
        - Match by: slot mapping + dish name prefix + calories

        Args:
            target_date: The date to query

        Returns:
            Tuple of:
            - List of entries to delete with meal_key, meal_name, dish_name, and garmin_log_id
            - List of unmatched state entries (state has entry but no Garmin match found)
        """
        entries_to_delete = []
        unmatched_state_entries = []

        # Get all state entries for this date
        synced_meals = self.state.get_synced_meals_for_date(target_date)
        logger.info("Found %d meals in sync state for %s", len(synced_meals), target_date)

        # Build a list of state entries we need to match
        state_entries = []
        for record in synced_meals:
            meal_key = record["key"]
            meal_data = record["data"]
            state_entries.append({
                "meal_key": meal_key,
                "meal_name": meal_data.get("meal_name", "Unknown"),
                "dish_name": meal_data.get("dish_name", ""),
                "calories": meal_data.get("calories", 0),
                "garmin_log_id": meal_data.get("garmin_log_id"),
                "matched": False,
            })

        # Query Garmin API for all entries
        try:
            import os
            dump_raw = os.getenv("DUMP_RAW", "").lower() == "true"
            
            garmin_logs = await self.garmin.list_nutrition_logs(target_date, dump_raw=dump_raw)
            logger.info("Found %d total nutrition logs in Garmin for %s", len(garmin_logs), target_date)

            # Filter for entries created by this tool (prefix "KV:")
            kv_entries = []
            for log in garmin_logs:
                log_name = log.get("name", "")
                log_id = log.get("logId")
                log_slot = log.get("mealSlotName")
                log_calories = log.get("calories")
                
                if log_name.startswith("KV: ") and log_id:
                    kv_entries.append({
                        "logId": log_id,
                        "name": log_name,
                        "mealSlotName": log_slot,
                        "calories": log_calories,
                    })

            logger.info("Found %d entries with 'KV:' prefix", len(kv_entries))

            # Match state entries to Garmin entries using slot + dish name + calories
            # Require unambiguous 1:1 mapping
            for state_entry in state_entries:
                state_meal_name = state_entry["meal_name"]
                state_dish_name = state_entry["dish_name"]
                state_calories = state_entry["calories"]
                
                # Map state meal name to Garmin slot
                expected_slot = GarminConnectClient.polish_meal_to_garmin_slot(state_meal_name)
                
                # Build expected Garmin name: "KV: <dish_name>"
                # Garmin truncates at ~44 chars
                expected_name_prefix = f"KV: {state_dish_name}"[:44]

                # Find matching Garmin entries
                candidates = []
                for garmin_entry in kv_entries:
                    garmin_name = garmin_entry["name"]
                    garmin_slot = garmin_entry["mealSlotName"]
                    garmin_calories = garmin_entry["calories"] or 0

                    # Match criteria: slot, name prefix (first 40 chars), and calories (±5)
                    slot_match = (garmin_slot == expected_slot)
                    name_match = garmin_name.startswith(expected_name_prefix[:40])
                    calorie_match = abs(garmin_calories - state_calories) < 5

                    if slot_match and name_match and calorie_match:
                        candidates.append(garmin_entry)

                if len(candidates) == 1:
                    # Unambiguous match
                    garmin_entry = candidates[0]
                    state_entry["matched"] = True
                    entries_to_delete.append({
                        "garmin_log_id": garmin_entry["logId"],
                        "garmin_name": garmin_entry["name"],
                        "garmin_slot": garmin_entry["mealSlotName"],
                        "meal_key": state_entry["meal_key"],
                        "meal_name": state_entry["meal_name"],
                        "dish_name": state_entry["dish_name"],
                    })
                    logger.info(
                        "Matched: state '%s - %s' -> Garmin '%s' / %s (ID: %s)",
                        state_meal_name,
                        state_dish_name[:30],
                        garmin_entry["mealSlotName"],
                        garmin_entry["name"][:50],
                        garmin_entry["logId"]
                    )
                elif len(candidates) == 0:
                    logger.warning(
                        "No Garmin match for state entry: %s - %s (expected slot: %s, cal: %d)",
                        state_meal_name,
                        state_dish_name[:30],
                        expected_slot,
                        state_calories
                    )
                else:
                    # Ambiguous: multiple candidates
                    logger.error(
                        "AMBIGUOUS: %d Garmin matches for state entry: %s - %s",
                        len(candidates),
                        state_meal_name,
                        state_dish_name[:30]
                    )
                    for candidate in candidates:
                        logger.error("  - %s / %s (ID: %s)", candidate["mealSlotName"], candidate["name"][:50], candidate["logId"])

        except Exception as e:
            logger.error("Failed to query Garmin API for logs: %s", e)
            logger.warning("Cannot verify state entries against Garmin - unsafe to proceed")
            # If we can't query Garmin, mark all state entries as unmatched
            unmatched_state_entries = state_entries
            return [], unmatched_state_entries

        # Identify unmatched state entries
        for state_entry in state_entries:
            if not state_entry["matched"]:
                unmatched_state_entries.append(state_entry)

        # Verify 1:1 mapping
        if len(entries_to_delete) != len(state_entries):
            logger.warning(
                "Match count mismatch: %d Garmin matches vs %d state entries",
                len(entries_to_delete),
                len(state_entries)
            )

        return entries_to_delete, unmatched_state_entries
