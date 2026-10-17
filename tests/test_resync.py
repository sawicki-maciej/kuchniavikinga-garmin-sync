"""Test resync functionality."""

import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from kuchniavikinga_garmin_sync.resync import ResyncService
from kuchniavikinga_garmin_sync.dietly_client import DeliveredMeal, NutritionInfo
from kuchniavikinga_garmin_sync.state import SyncState


@pytest.mark.asyncio
async def test_resync_with_stored_ids():
    """Test resync when Garmin log IDs are stored in state."""
    # Create mock clients
    dietly_mock = AsyncMock()
    garmin_mock = AsyncMock()
    
    # Create state with stored meals
    state = SyncState(bucket_name=None)
    state._state = {"synced_meals": {
        "2026-10-16:Śniadanie:abc12345": {
            "delivery_date": "2026-10-16",
            "meal_name": "Śniadanie",
            "dish_name": "Test Breakfast",
            "synced_at": "2026-10-16",
            "garmin_log_id": 12345
        },
        "2026-10-16:Obiad:def67890": {
            "delivery_date": "2026-10-16",
            "meal_name": "Obiad",
            "dish_name": "Test Lunch",
            "synced_at": "2026-10-16",
            "garmin_log_id": 67890
        }
    }}
    
    # Mock Garmin API responses - return logs that match the state IDs
    garmin_mock.list_nutrition_logs = AsyncMock(return_value=[
        {
            "name": "KV: Śniadanie - Test Breakfast",
            "foodLogId": 12345,
            "calories": 400
        },
        {
            "name": "KV: Obiad - Test Lunch",
            "foodLogId": 67890,
            "calories": 600
        }
    ])
    garmin_mock.delete_nutrition_log = AsyncMock(return_value=True)
    garmin_mock.log_meal = AsyncMock(return_value={"nutritionLogId": 99999})
    
    # Mock Dietly client to return fresh meals
    fresh_meals = [
        DeliveredMeal(
            delivery_date=date(2026, 10, 16),
            meal_name="Śniadanie",
            dish_name="Test Breakfast",
            nutrition=NutritionInfo(calories=400, protein=20, carbs=50, fat=10),
            delivery_id=1,
            meal_id=1
        ),
        DeliveredMeal(
            delivery_date=date(2026, 10, 16),
            meal_name="Obiad",
            dish_name="Test Lunch",
            nutrition=NutritionInfo(calories=600, protein=30, carbs=70, fat=15),
            delivery_id=1,
            meal_id=2
        )
    ]
    dietly_mock.get_delivered_meals = AsyncMock(return_value=fresh_meals)
    
    # Create resync service
    resync = ResyncService(dietly_mock, garmin_mock, state, dry_run=False)
    
    # Run resync
    result = await resync.resync_date(date(2026, 10, 16))
    
    # Verify results
    assert result["deleted"] == 2
    assert result["relogged"] == 2
    assert result["skipped"] == 0
    
    # Verify Garmin delete was called twice
    assert garmin_mock.delete_nutrition_log.call_count == 2
    garmin_mock.delete_nutrition_log.assert_any_call(12345)
    garmin_mock.delete_nutrition_log.assert_any_call(67890)
    
    # Verify meals were re-logged
    assert garmin_mock.log_meal.call_count == 2


@pytest.mark.asyncio
async def test_resync_dry_run():
    """Test resync in dry-run mode (no actual changes)."""
    dietly_mock = AsyncMock()
    garmin_mock = AsyncMock()
    
    state = SyncState(bucket_name=None)
    state._state = {"synced_meals": {
        "2026-10-16:Śniadanie:abc12345": {
            "delivery_date": "2026-10-16",
            "meal_name": "Śniadanie",
            "dish_name": "Test Meal",
            "synced_at": "2026-10-16",
            "garmin_log_id": 12345
        }
    }}
    
    # Mock Garmin API to return matching log
    garmin_mock.list_nutrition_logs = AsyncMock(return_value=[
        {
            "name": "KV: Śniadanie - Test Meal",
            "foodLogId": 12345,
            "calories": 400
        }
    ])
    
    fresh_meals = [
        DeliveredMeal(
            delivery_date=date(2026, 10, 16),
            meal_name="Śniadanie",
            dish_name="Test Meal",
            nutrition=NutritionInfo(calories=400, protein=20, carbs=50, fat=10),
            delivery_id=1,
            meal_id=1
        )
    ]
    dietly_mock.get_delivered_meals = AsyncMock(return_value=fresh_meals)
    
    # Create resync service in dry-run mode
    resync = ResyncService(dietly_mock, garmin_mock, state, dry_run=True)
    
    # Run resync
    result = await resync.resync_date(date(2026, 10, 16))
    
    # In dry-run, counts are logged but no actual API calls
    assert result["deleted"] == 1
    assert result["relogged"] == 1
    assert result["skipped"] == 0
    
    # Verify no Garmin API calls were made
    garmin_mock.delete_nutrition_log.assert_not_called()
    garmin_mock.log_meal.assert_not_called()


@pytest.mark.asyncio
async def test_resync_finds_garmin_entries_without_state():
    """Test resync finds entries from Garmin API when state doesn't have IDs."""
    dietly_mock = AsyncMock()
    garmin_mock = AsyncMock()
    
    # Empty state
    state = SyncState(bucket_name=None)
    state._state = {"synced_meals": {}}
    
    # Mock Garmin API to return entries created by this tool (named "KV: ...")
    garmin_mock.list_nutrition_logs = AsyncMock(return_value=[
        {
            "name": "KV: Śniadanie - Test Meal",
            "foodLogId": 11111,
            "calories": 400
        },
        {
            "name": "Random App Entry",
            "foodLogId": 22222,
            "calories": 300
        },
        {
            "name": "KV: Obiad - Another Meal",
            "foodLogId": 33333,
            "calories": 600
        }
    ])
    
    garmin_mock.delete_nutrition_log = AsyncMock(return_value=True)
    garmin_mock.log_meal = AsyncMock(return_value={"nutritionLogId": 99999})
    
    fresh_meals = [
        DeliveredMeal(
            delivery_date=date(2026, 10, 16),
            meal_name="Śniadanie",
            dish_name="Test Meal",
            nutrition=NutritionInfo(calories=400, protein=20, carbs=50, fat=10),
            delivery_id=1,
            meal_id=1
        ),
        DeliveredMeal(
            delivery_date=date(2026, 10, 16),
            meal_name="Obiad",
            dish_name="Another Meal",
            nutrition=NutritionInfo(calories=600, protein=30, carbs=70, fat=15),
            delivery_id=1,
            meal_id=2
        )
    ]
    dietly_mock.get_delivered_meals = AsyncMock(return_value=fresh_meals)
    
    resync = ResyncService(dietly_mock, garmin_mock, state, dry_run=False)
    result = await resync.resync_date(date(2026, 10, 16))
    
    # Should find and delete only the "KV: " entries, not "Random App Entry"
    assert result["deleted"] == 2  # Only KV entries
    assert garmin_mock.delete_nutrition_log.call_count == 2
    garmin_mock.delete_nutrition_log.assert_any_call(11111)
    garmin_mock.delete_nutrition_log.assert_any_call(33333)
    
    # Verify meals were re-logged
    assert result["relogged"] == 2
