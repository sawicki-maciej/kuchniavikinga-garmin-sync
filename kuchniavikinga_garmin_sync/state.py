"""State management for sync operations."""

import hashlib
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from google.cloud import storage

from .dietly_client import DeliveredMeal

logger = logging.getLogger(__name__)


class SyncState:
    """Manages sync state to prevent duplicate meal logging."""

    STATE_FILE = ".sync_state.json"

    def __init__(self, bucket_name: str | None = None):
        """Initialize state manager.

        Args:
            bucket_name: GCS bucket name. If None, uses local file storage.
        """
        self.bucket_name = bucket_name
        self._state: dict[str, Any] = {"synced_meals": {}}
        self._state_changed = False  # Track if state was modified (e.g., by migration)
        self._use_gcs = bucket_name is not None

        if self._use_gcs:
            self._storage_client = storage.Client()
            self._bucket = self._storage_client.bucket(bucket_name)
            self._blob = self._bucket.blob(self.STATE_FILE)
        else:
            self._local_path = Path.home() / ".kuchniavikinga-garmin-sync" / self.STATE_FILE
            self._local_path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> None:
        """Load sync state from storage.
        
        Starts with empty state only if state object doesn't exist (NotFound).
        Any other error (permissions, bad JSON, etc.) aborts the job.
        """
        try:
            if self._use_gcs:
                if self._blob.exists():
                    try:
                        content = self._blob.download_as_text()
                        self._state = json.loads(content)
                        logger.info(
                            "Loaded sync state from gs://%s/%s",
                            self.bucket_name,
                            self.STATE_FILE,
                        )
                    except json.JSONDecodeError as e:
                        logger.error("FATAL: Sync state file contains invalid JSON: %s", e)
                        raise RuntimeError(f"Cannot parse sync state JSON: {e}") from e
                    except Exception as e:
                        logger.error("FATAL: Failed to load sync state from GCS: %s", e)
                        raise RuntimeError(f"Cannot load sync state from GCS: {e}") from e
                else:
                    logger.info("No existing sync state in GCS, starting fresh")
                    self._state = {"synced_meals": {}}
            else:
                if self._local_path.exists():
                    try:
                        with open(self._local_path) as f:
                            self._state = json.load(f)
                        logger.info("Loaded sync state from %s", self._local_path)
                    except json.JSONDecodeError as e:
                        logger.error("FATAL: Sync state file contains invalid JSON: %s", e)
                        raise RuntimeError(f"Cannot parse sync state JSON: {e}") from e
                    except Exception as e:
                        logger.error("FATAL: Failed to read sync state file: %s", e)
                        raise RuntimeError(f"Cannot read sync state file: {e}") from e
                else:
                    logger.info("No existing sync state locally, starting fresh")
                    self._state = {"synced_meals": {}}

        except RuntimeError:
            # Re-raise our explicit RuntimeErrors
            raise
        except Exception as e:
            logger.error("FATAL: Unexpected error loading sync state: %s", e)
            raise

    def save(self) -> None:
        """Save sync state to storage."""
        try:
            content = json.dumps(self._state, indent=2, default=str)

            if self._use_gcs:
                self._blob.upload_from_string(
                    content, content_type="application/json"
                )
                logger.info(
                    "Saved sync state to gs://%s/%s", self.bucket_name, self.STATE_FILE
                )
            else:
                with open(self._local_path, "w") as f:
                    f.write(content)
                logger.info("Saved sync state to %s", self._local_path)
            
            # Reset changed flag after successful save
            self._state_changed = False

        except Exception as e:
            logger.error("Failed to save sync state: %s", e)
            raise

    def is_synced(self, meal: DeliveredMeal) -> bool:
        """Check if a meal has already been synced.

        Checks both new stable key format and old nutrition-hash format for migration.
        """
        import re
        
        meal_key = self._meal_key(meal)
        
        # Check new format first
        if meal_key in self._state["synced_meals"]:
            return True
        
        # Define format patterns
        # New format: YYYY-MM-DD:dDELIVERY_ID:mMEAL_ID (e.g., "2026-10-17:d12345:m67890")
        new_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}:d\d+:m\d+$")
        # Old format: YYYY-MM-DD:Meal Name:HASH (e.g., "2026-10-17:Obiad:d41f09ab")
        # Hash is 8-char hex that can start with any hex digit including 'd' or 'm'
        old_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}:.+:[0-9a-f]{8}$")
        
        # Check for old format migration: exact match on date + meal_name
        date_str = meal.delivery_date.isoformat()
        meal_name = meal.meal_name
        
        for old_key in list(self._state["synced_meals"].keys()):
            # Skip if this is already new format
            if new_pattern.match(old_key):
                continue
            
            # Check if this is old format for same date + meal_name
            if old_pattern.match(old_key):
                # Parse old key: date:meal_name:hash
                parts = old_key.split(":", 2)  # Split into at most 3 parts
                if len(parts) == 3:
                    old_date, old_meal_name, old_hash = parts
                    
                    # Match by exact date and meal_name
                    if old_date == date_str and old_meal_name == meal_name:
                        logger.info(
                            "Migrating old sync state key '%s' to new format '%s'",
                            old_key,
                            meal_key
                        )
                        # Copy the old entry to new key
                        self._state["synced_meals"][meal_key] = self._state["synced_meals"][old_key]
                        # Remove old key
                        del self._state["synced_meals"][old_key]
                        self._state_changed = True
                        return True
        
        return False

    def mark_synced(self, meal: DeliveredMeal, garmin_log_id: int | None = None) -> None:
        """Mark a meal as synced.
        
        Args:
            meal: The meal that was synced
            garmin_log_id: Optional Garmin nutrition log ID for this entry
        """
        meal_key = self._meal_key(meal)
        self._state["synced_meals"][meal_key] = {
            "delivery_date": meal.delivery_date.isoformat(),
            "meal_name": meal.meal_name,
            "dish_name": meal.dish_name,
            "synced_at": date.today().isoformat(),
        }
        if garmin_log_id is not None:
            self._state["synced_meals"][meal_key]["garmin_log_id"] = garmin_log_id
        self._state_changed = True
    
    def has_changes(self) -> bool:
        """Check if state has been modified since last save."""
        return self._state_changed

    @staticmethod
    def _meal_key(meal: DeliveredMeal) -> str:
        """Generate a unique stable key for a meal.

        Uses: delivery_date + delivery_id + meal_id
        These are stable IDs from Vikinga API that don't change with nutrition updates.
        """
        return f"{meal.delivery_date.isoformat()}:d{meal.delivery_id}:m{meal.meal_id}"

    def get_synced_meals_for_date(self, target_date: date) -> list[dict[str, Any]]:
        """Get all synced meals for a specific date.
        
        Args:
            target_date: The date to query
            
        Returns:
            List of synced meal records with their keys
        """
        date_str = target_date.isoformat()
        results = []
        for meal_key, meal_data in self._state["synced_meals"].items():
            if meal_data.get("delivery_date") == date_str:
                results.append({
                    "key": meal_key,
                    "data": meal_data
                })
        return results
    
    def clear_meal(self, meal_key: str) -> None:
        """Remove a meal from sync state.
        
        Args:
            meal_key: The meal key to remove
        """
        if meal_key in self._state["synced_meals"]:
            del self._state["synced_meals"][meal_key]
            logger.info("Cleared meal from sync state: %s", meal_key)
    
    def get_stats(self) -> dict[str, Any]:
        """Get sync statistics."""
        synced_count = len(self._state["synced_meals"])
        return {
            "total_synced": synced_count,
            "state_file": (
                f"gs://{self.bucket_name}/{self.STATE_FILE}"
                if self._use_gcs
                else str(self._local_path)
            ),
        }
