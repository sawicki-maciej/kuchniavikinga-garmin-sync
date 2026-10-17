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
        self._use_gcs = bucket_name is not None

        if self._use_gcs:
            self._storage_client = storage.Client()
            self._bucket = self._storage_client.bucket(bucket_name)
            self._blob = self._bucket.blob(self.STATE_FILE)
        else:
            self._local_path = Path.home() / ".kuchniavikinga-garmin-sync" / self.STATE_FILE
            self._local_path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> None:
        """Load sync state from storage."""
        try:
            if self._use_gcs:
                if self._blob.exists():
                    content = self._blob.download_as_text()
                    self._state = json.loads(content)
                    logger.info(
                        "Loaded sync state from gs://%s/%s",
                        self.bucket_name,
                        self.STATE_FILE,
                    )
                else:
                    logger.info("No existing sync state in GCS, starting fresh")
            else:
                if self._local_path.exists():
                    with open(self._local_path) as f:
                        self._state = json.load(f)
                    logger.info("Loaded sync state from %s", self._local_path)
                else:
                    logger.info("No existing sync state locally, starting fresh")

        except Exception as e:
            logger.warning("Failed to load sync state: %s. Starting fresh.", e)
            self._state = {"synced_meals": {}}

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

        except Exception as e:
            logger.error("Failed to save sync state: %s", e)
            raise

    def is_synced(self, meal: DeliveredMeal) -> bool:
        """Check if a meal has already been synced.

        Uses a hash of meal key attributes to track synced meals.
        """
        meal_key = self._meal_key(meal)
        return meal_key in self._state["synced_meals"]

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

    @staticmethod
    def _meal_key(meal: DeliveredMeal) -> str:
        """Generate a unique key for a meal.

        Uses: delivery_date + meal_name + nutrition hash
        This allows detecting the same meal even if dish name changes slightly.
        """
        nutrition_str = (
            f"{meal.nutrition.calories:.0f}:"
            f"{meal.nutrition.protein or 0:.0f}:"
            f"{meal.nutrition.carbs or 0:.0f}:"
            f"{meal.nutrition.fat or 0:.0f}"
        )
        nutrition_hash = hashlib.sha256(nutrition_str.encode()).hexdigest()[:8]

        return f"{meal.delivery_date.isoformat()}:{meal.meal_name}:{nutrition_hash}"

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
