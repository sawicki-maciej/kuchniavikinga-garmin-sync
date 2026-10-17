"""Test for sync state management."""

import tempfile
from pathlib import Path
from datetime import date
from kuchniavikinga_garmin_sync.state import SyncState
from kuchniavikinga_garmin_sync.dietly_client import DeliveredMeal, NutritionInfo


def test_sync_state_local():
    """Test local sync state management."""
    # Use temporary directory
    with tempfile.TemporaryDirectory() as tmpdir:
        state = SyncState(bucket_name=None)
        state._local_path = Path(tmpdir) / ".sync_state.json"
        
        # Create a test meal
        nutrition = NutritionInfo(
            calories=500.0,
            protein=30.0,
            carbs=50.0,
            fat=15.0,
        )
        
        meal = DeliveredMeal(
            delivery_date=date(2026, 10, 15),
            meal_name="Śniadanie",
            dish_name="Jajecznica",
            nutrition=nutrition,
            delivery_id=12345,
            meal_id=67890,
        )
        
        # Initially not synced
        assert not state.is_synced(meal)
        
        # Mark as synced
        state.mark_synced(meal)
        assert state.is_synced(meal)
        
        # Save and reload
        state.save()
        
        new_state = SyncState(bucket_name=None)
        new_state._local_path = Path(tmpdir) / ".sync_state.json"
        new_state.load()
        
        # Should still be marked as synced
        assert new_state.is_synced(meal)


def test_meal_key_generation():
    """Test meal key generation uses stable Vikinga IDs."""
    state = SyncState(bucket_name=None)
    
    nutrition = NutritionInfo(calories=500.0, protein=30.0, carbs=50.0, fat=15.0)
    
    meal1 = DeliveredMeal(
        delivery_date=date(2026, 10, 15),
        meal_name="Śniadanie",
        dish_name="Jajecznica",
        nutrition=nutrition,
        delivery_id=12345,
        meal_id=67890,
    )
    
    # Same Vikinga IDs -> same key (even if nutrition changes)
    meal2 = DeliveredMeal(
        delivery_date=date(2026, 10, 15),
        meal_name="Śniadanie zmieniona",
        dish_name="Jajecznica zmieniona",
        nutrition=NutritionInfo(calories=600.0, protein=40.0, carbs=60.0, fat=20.0),
        delivery_id=12345,
        meal_id=67890,
    )
    
    assert state._meal_key(meal1) == state._meal_key(meal2)
    assert state._meal_key(meal1) == "2026-10-15:d12345:m67890"
    
    # Different Vikinga IDs -> different key
    meal3 = DeliveredMeal(
        delivery_date=date(2026, 10, 15),
        meal_name="Śniadanie",
        dish_name="Jajecznica",
        nutrition=nutrition,
        delivery_id=12345,
        meal_id=99999,
    )
    
    assert state._meal_key(meal1) != state._meal_key(meal3)


def test_meal_key_migration():
    """Test migration from old nutrition-hash keys to new Vikinga ID keys."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state = SyncState(bucket_name=None)
        state._local_path = Path(tmpdir) / ".sync_state.json"
        
        # Simulate old state with nutrition-hash key format
        state._state = {
            "synced_meals": {
                "2026-10-15:Śniadanie:29eab6ce": {
                    "delivery_date": "2026-10-15",
                    "meal_name": "Śniadanie",
                    "dish_name": "Jajecznica",
                    "synced_at": "2026-10-15",
                }
            }
        }
        state.save()
        
        # Create meal with same date + meal_name but different nutrition
        nutrition = NutritionInfo(calories=600.0, protein=40.0, carbs=60.0, fat=20.0)
        meal = DeliveredMeal(
            delivery_date=date(2026, 10, 15),
            meal_name="Śniadanie",
            dish_name="Jajecznica - updated",
            nutrition=nutrition,
            delivery_id=12345,
            meal_id=67890,
        )
        
        # is_synced should migrate the old key
        assert state.is_synced(meal)
        
        # Old key should be removed, new key should exist
        assert "2026-10-15:Śniadanie:29eab6ce" not in state._state["synced_meals"]
        assert "2026-10-15:d12345:m67890" in state._state["synced_meals"]
        
        # Subsequent checks should use new key
        assert state.is_synced(meal)
