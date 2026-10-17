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
        3. Delete those entries from Garmin
        4. Clear them from sync state
        5. Re-fetch meals from Kuchnia Vikinga
        6. Re-log them to Garmin with correct data

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

        Tries two sources:
        1. Sync state (if we stored Garmin log IDs)
        2. Garmin API (list all logs for the date, filter by name "KV: ...")

        Args:
            target_date: The date to query

        Returns:
            Tuple of:
            - List of entries to delete with meal_key, meal_name, and garmin_log_id
            - List of unmatched state entries (state has entry but no Garmin match found)
        """
        entries_to_delete = []
        unmatched_state_entries = []

        # Source 1: Check sync state for stored IDs
        synced_meals = self.state.get_synced_meals_for_date(target_date)
        logger.info("Found %d meals in sync state for %s", len(synced_meals), target_date)

        # Build a map of state entries
        state_entries = {}
        for record in synced_meals:
            meal_key = record["key"]
            meal_data = record["data"]
            garmin_log_id = meal_data.get("garmin_log_id")
            meal_name = meal_data.get("meal_name", "Unknown")
            
            state_entries[meal_key] = {
                "meal_key": meal_key,
                "meal_name": meal_name,
                "garmin_log_id": garmin_log_id,
                "source": "state",
                "matched": False  # Will mark True if found in Garmin
            }

        # Source 2: Query Garmin API for entries named "KV: ..."
        # This catches entries if state was lost or IDs weren't stored
        try:
            # Enable dump_raw if DUMP_RAW env var is set
            import os
            dump_raw = os.getenv("DUMP_RAW", "").lower() == "true"
            
            garmin_logs = await self.garmin.list_nutrition_logs(target_date, dump_raw=dump_raw)
            logger.info("Found %d total nutrition logs in Garmin for %s", len(garmin_logs), target_date)

            for log in garmin_logs:
                # Extract log name and ID from real API structure
                # Real structure: {logId: "hex-string", foodMetaData: {foodName: "KV: ..."}, ...}
                # Old guessed structure: {foodLogId: int, name: "KV: ...", ...}
                
                # Try real structure first
                food_meta = log.get("foodMetaData", {})
                log_name = food_meta.get("foodName") if isinstance(food_meta, dict) else None
                log_id = log.get("logId")
                
                # Fallback to old structure
                if not log_name:
                    log_name = log.get("name") or log.get("foodName") or log.get("description") or ""
                if not log_id:
                    log_id = log.get("foodLogId") or log.get("nutritionLogId") or log.get("id")
                
                # Extract calories for matching when IDs aren't available
                nutrition_content = log.get("nutritionContent", {})
                log_calories = nutrition_content.get("calories") if isinstance(nutrition_content, dict) else None

                # Only consider entries created by this tool (named "KV: ...")
                if log_name and log_name.startswith("KV: ") and log_id:
                    # Check if this matches any state entry by ID (if IDs are stored)
                    matched = False
                    matched_by_name = False
                    
                    for state_entry in state_entries.values():
                        # Try matching by ID first (if we have it)
                        if state_entry["garmin_log_id"] and state_entry["garmin_log_id"] == log_id:
                            state_entry["matched"] = True
                            matched = True
                            break
                    
                    # If no ID match and state has no IDs, try matching by meal name
                    # Garmin truncates names to ~44 chars, so check if log_name starts with meal_name
                    if not matched:
                        for state_entry in state_entries.values():
                            if not state_entry["garmin_log_id"]:  # Only if state has no ID stored
                                # Extract meal name from KV-prefixed name
                                # Format: "KV: [Polish meal name] - [dish name]"
                                state_meal_name = state_entry["meal_name"]
                                # Check if the Garmin log name contains this meal name
                                if state_meal_name in log_name:
                                    state_entry["matched"] = True
                                    matched = True
                                    matched_by_name = True
                                    logger.info(
                                        "Matched by name: state '%s' -> Garmin '%s' (ID: %s)",
                                        state_meal_name,
                                        log_name[:50],
                                        log_id
                                    )
                                    break
                    
                    # Add to delete list (either matched state entry or orphan)
                    if not any(e.get("garmin_log_id") == log_id for e in entries_to_delete):
                        entry = {
                            "meal_key": f"garmin-{log_id}" if not matched else None,
                            "meal_name": log_name,
                            "garmin_log_id": log_id,
                            "source": "garmin" if not matched else ("matched-by-name" if matched_by_name else "matched"),
                        }
                        
                        # Update meal_key from state if matched
                        for state_entry in state_entries.values():
                            if (state_entry["garmin_log_id"] and state_entry["garmin_log_id"] == log_id) or \
                               (matched_by_name and state_entry["meal_name"] in log_name):
                                entry["meal_key"] = state_entry["meal_key"]
                                break
                        
                        entries_to_delete.append(entry)
                        
                        if not matched:
                            logger.info("Found Garmin log not in state: %s (ID: %s)", log_name[:50], log_id)

        except Exception as e:
            logger.error("Failed to query Garmin API for logs: %s", e)
            logger.warning("Cannot verify state entries against Garmin - unsafe to proceed")
            # If we can't query Garmin, mark all state entries as unmatched
            unmatched_state_entries = list(state_entries.values())
            return [], unmatched_state_entries

        # Identify unmatched state entries (in state but not found in Garmin)
        for state_entry in state_entries.values():
            if not state_entry["matched"]:
                unmatched_state_entries.append(state_entry)
                logger.warning(
                    "State entry has no Garmin match: %s (ID: %s)",
                    state_entry["meal_name"],
                    state_entry.get("garmin_log_id")
                )

        return entries_to_delete, unmatched_state_entries
