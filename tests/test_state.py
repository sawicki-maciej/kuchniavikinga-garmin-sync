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


def test_meal_key_migration_hash_starts_with_d():
    """Test migration when old hash starts with 'd' (hex digit that could confuse detection)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state = SyncState(bucket_name=None)
        state._local_path = Path(tmpdir) / ".sync_state.json"
        
        # Old key with hash starting with 'd'
        state._state = {
            "synced_meals": {
                "2026-10-17:Obiad:d41f09ab": {
                    "delivery_date": "2026-10-17",
                    "meal_name": "Obiad",
                    "dish_name": "Kurczak z ryżem",
                    "synced_at": "2026-10-17",
                }
            }
        }
        
        meal = DeliveredMeal(
            delivery_date=date(2026, 10, 17),
            meal_name="Obiad",
            dish_name="Kurczak z ryżem",
            nutrition=NutritionInfo(calories=500.0, protein=30.0, carbs=50.0, fat=15.0),
            delivery_id=99999,
            meal_id=88888,
        )
        
        # Should correctly identify as old format and migrate
        assert state.is_synced(meal)
        assert "2026-10-17:Obiad:d41f09ab" not in state._state["synced_meals"]
        assert "2026-10-17:d99999:m88888" in state._state["synced_meals"]


def test_meal_key_migration_meal_name_with_colon_and_space():
    """Test migration when meal name contains ':' or spaces like 'II śniadanie'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state = SyncState(bucket_name=None)
        state._local_path = Path(tmpdir) / ".sync_state.json"
        
        # Old key with complex meal name
        state._state = {
            "synced_meals": {
                "2026-10-16:II śniadanie:abc12345": {
                    "delivery_date": "2026-10-16",
                    "meal_name": "II śniadanie",
                    "dish_name": "Shake proteinowy",
                    "synced_at": "2026-10-16",
                }
            }
        }
        
        meal = DeliveredMeal(
            delivery_date=date(2026, 10, 16),
            meal_name="II śniadanie",
            dish_name="Shake proteinowy",
            nutrition=NutritionInfo(calories=300.0, protein=25.0, carbs=20.0, fat=10.0),
            delivery_id=11111,
            meal_id=22222,
        )
        
        # Should correctly parse and migrate (split on first 2 colons only)
        assert state.is_synced(meal)
        assert "2026-10-16:II śniadanie:abc12345" not in state._state["synced_meals"]
        assert "2026-10-16:d11111:m22222" in state._state["synced_meals"]


def test_meal_key_no_migration_for_new_format():
    """Test that new format keys are not mistaken for old format."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state = SyncState(bucket_name=None)
        state._local_path = Path(tmpdir) / ".sync_state.json"
        
        # New format key should not be migrated
        state._state = {
            "synced_meals": {
                "2026-10-16:d12345:m67890": {
                    "delivery_date": "2026-10-16",
                    "meal_name": "Śniadanie",
                    "dish_name": "Test",
                    "synced_at": "2026-10-16",
                }
            }
        }
        
        meal = DeliveredMeal(
            delivery_date=date(2026, 10, 16),
            meal_name="Śniadanie",
            dish_name="Test",
            nutrition=NutritionInfo(calories=400.0, protein=20.0, carbs=40.0, fat=10.0),
            delivery_id=12345,
            meal_id=67890,
        )
        
        # Should recognize as already synced, no migration
        assert state.is_synced(meal)
        # Key should remain unchanged
        assert "2026-10-16:d12345:m67890" in state._state["synced_meals"]
