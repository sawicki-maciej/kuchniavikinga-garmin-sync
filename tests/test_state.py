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
    """Test meal key generation for deduplication."""
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
    
    # Same meal, different delivery_id (shouldn't affect key)
    meal2 = DeliveredMeal(
        delivery_date=date(2026, 10, 15),
        meal_name="Śniadanie",
        dish_name="Jajecznica zmieniona nazwa",  # Different dish name
        nutrition=nutrition,
        delivery_id=99999,
        meal_id=99999,
    )
    
    # Same nutrition and date/meal_name should produce same key
    assert state._meal_key(meal1) == state._meal_key(meal2)
    
    # Different nutrition should produce different key
    meal3 = DeliveredMeal(
        delivery_date=date(2026, 10, 15),
        meal_name="Śniadanie",
        dish_name="Jajecznica",
        nutrition=NutritionInfo(calories=600.0, protein=30.0, carbs=50.0, fat=15.0),
        delivery_id=12345,
        meal_id=67890,
    )
    
    assert state._meal_key(meal1) != state._meal_key(meal3)
