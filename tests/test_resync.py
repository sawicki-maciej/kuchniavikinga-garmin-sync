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
            "calories": 400,
            "synced_at": "2026-10-16",
            "garmin_log_id": 12345
        },
        "2026-10-16:Obiad:def67890": {
            "delivery_date": "2026-10-16",
            "meal_name": "Obiad",
            "dish_name": "Test Lunch",
            "calories": 600,
            "synced_at": "2026-10-16",
            "garmin_log_id": 67890
        }
    }}

    # Mock Garmin API responses - first call returns entries, second call (verification) returns empty
    async def list_nutrition_logs_side_effect(target_date, dump_raw=False):
        if garmin_mock.list_nutrition_logs.call_count == 1:
            # First call: return entries to delete
            return [
                {
                    "logId": 12345,
                    "name": "KV: Test Breakfast",
                    "mealSlotName": "BREAKFAST",
                    "calories": 400
                },
                {
                    "logId": 67890,
                    "name": "KV: Test Lunch",
                    "mealSlotName": "LUNCH",
                    "calories": 600
                }
            ]
        else:
            # Second call (verification): return empty (all deleted)
            return []
    
    garmin_mock.list_nutrition_logs = AsyncMock(side_effect=list_nutrition_logs_side_effect)
    garmin_mock.delete_nutrition_log = AsyncMock(return_value=True)
    garmin_mock.log_meal = AsyncMock(return_value={"logId": "new_log_123"})
    
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
            "calories": 400,
            "synced_at": "2026-10-16",
            "garmin_log_id": 12345
        }
    }}

    # Mock Garmin API to return matching log with real structure
    garmin_mock.list_nutrition_logs = AsyncMock(return_value=[
        {
            "logId": 12345,
            "name": "KV: Test Meal",
            "mealSlotName": "BREAKFAST",
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
async def test_resync_real_five_meal_structure():
    """Test resync with real 5-meal 2026-10-16 structure and slot-based matching."""
    dietly_mock = AsyncMock()
    garmin_mock = AsyncMock()
    
    # State with 5 meals (no Garmin IDs stored, will match by slot+name+calories)
    state = SyncState(bucket_name=None)
    state._state = {"synced_meals": {
        "2026-10-16:Śniadanie:hash1": {
            "delivery_date": "2026-10-16",
            "meal_name": "Śniadanie",
            "dish_name": "Gofr czekoladowy, jogurt malinowy, winogrona, płatki owsiane",
            "calories": 452,
            "synced_at": "2026-10-16",
        },
        "2026-10-16:II Śniadanie:hash2": {
            "delivery_date": "2026-10-16",
            "meal_name": "II Śniadanie",
            "dish_name": "Batony proteinowe, jajko, orzechy włoskie",
            "calories": 380,
            "synced_at": "2026-10-16",
        },
        "2026-10-16:Obiad:hash3": {
            "delivery_date": "2026-10-16",
            "meal_name": "Obiad",
            "dish_name": "Pierś z kurczaka w sosie curry z warzywami i ryżem",
            "calories": 620,
            "synced_at": "2026-10-16",
        },
        "2026-10-16:Podwieczorek:hash4": {
            "delivery_date": "2026-10-16",
            "meal_name": "Podwieczorek",
            "dish_name": "Shake proteinowy, banan",
            "calories": 290,
            "synced_at": "2026-10-16",
        },
        "2026-10-16:Kolacja:hash5": {
            "delivery_date": "2026-10-16",
            "meal_name": "Kolacja",
            "dish_name": "Łosoś pieczony z batatem i brokułami",
            "calories": 540,
            "synced_at": "2026-10-16",
        },
    }}
    
    # Mock Garmin API with real structure (truncated dish names, slot in mealSlotName)
    garmin_mock.list_nutrition_logs = AsyncMock(return_value=[
        {
            "logId": "1ecbb709157543948c25eafa2c74edc7",
            "name": "KV: Gofr czekoladowy, jogurt malinowy, winog",  # Truncated at ~44 chars
            "mealSlotName": "BREAKFAST",
            "calories": 452,
        },
        {
            "logId": "a1b2c3d4e5f67890abcdef1234567890",
            "name": "KV: Batony proteinowe, jajko, orzechy włosk",
            "mealSlotName": "SNACKS",
            "calories": 380,
        },
        {
            "logId": "2fdbca18268654a59d36fbeb3d85fef8",
            "name": "KV: Pierś z kurczaka w sosie curry z warzyw",
            "mealSlotName": "LUNCH",
            "calories": 620,
        },
        {
            "logId": "fedcba0987654321abcdef0123456789",
            "name": "KV: Shake proteinowy, banan",
            "mealSlotName": "SNACKS",
            "calories": 290,
        },
        {
            "logId": "abcdef1234567890fedcba0987654321",
            "name": "KV: Łosoś pieczony z batatem i brokułami",
            "mealSlotName": "DINNER",
            "calories": 540,
        },
    ])
    
    # Mock re-listing after deletion (should return empty after successful deletes)
    garmin_mock.delete_nutrition_log = AsyncMock(return_value=True)
    
    # Second list call (verification after deletion) returns empty
    async def list_nutrition_logs_side_effect(target_date, dump_raw=False):
        if garmin_mock.list_nutrition_logs.call_count == 1:
            # First call: return all 5 entries
            return [
                {"logId": "1ecbb709157543948c25eafa2c74edc7", "name": "KV: Gofr czekoladowy, jogurt malinowy, winog", "mealSlotName": "BREAKFAST", "calories": 452},
                {"logId": "a1b2c3d4e5f67890abcdef1234567890", "name": "KV: Batony proteinowe, jajko, orzechy włosk", "mealSlotName": "SNACKS", "calories": 380},
                {"logId": "2fdbca18268654a59d36fbeb3d85fef8", "name": "KV: Pierś z kurczaka w sosie curry z warzyw", "mealSlotName": "LUNCH", "calories": 620},
                {"logId": "fedcba0987654321abcdef0123456789", "name": "KV: Shake proteinowy, banan", "mealSlotName": "SNACKS", "calories": 290},
                {"logId": "abcdef1234567890fedcba0987654321", "name": "KV: Łosoś pieczony z batatem i brokułami", "mealSlotName": "DINNER", "calories": 540},
            ]
        else:
            # Second call (verification): return empty (all deleted)
            return []
    
    garmin_mock.list_nutrition_logs = AsyncMock(side_effect=list_nutrition_logs_side_effect)
    garmin_mock.log_meal = AsyncMock(return_value={"logId": "new_log_id_123"})
    
    # Mock Dietly to return 5 fresh meals
    fresh_meals = [
        DeliveredMeal(
            delivery_date=date(2026, 10, 16),
            meal_name="Śniadanie",
            dish_name="Gofr czekoladowy, jogurt malinowy, winogrona, płatki owsiane",
            nutrition=NutritionInfo(calories=452, protein=20, carbs=50, fat=10),
            delivery_id=1,
            meal_id=1,
        ),
        DeliveredMeal(
            delivery_date=date(2026, 10, 16),
            meal_name="II Śniadanie",
            dish_name="Batony proteinowe, jajko, orzechy włoskie",
            nutrition=NutritionInfo(calories=380, protein=15, carbs=35, fat=12),
            delivery_id=1,
            meal_id=2,
        ),
        DeliveredMeal(
            delivery_date=date(2026, 10, 16),
            meal_name="Obiad",
            dish_name="Pierś z kurczaka w sosie curry z warzywami i ryżem",
            nutrition=NutritionInfo(calories=620, protein=35, carbs=70, fat=18),
            delivery_id=1,
            meal_id=3,
        ),
        DeliveredMeal(
            delivery_date=date(2026, 10, 16),
            meal_name="Podwieczorek",
            dish_name="Shake proteinowy, banan",
            nutrition=NutritionInfo(calories=290, protein=25, carbs=30, fat=5),
            delivery_id=1,
            meal_id=4,
        ),
        DeliveredMeal(
            delivery_date=date(2026, 10, 16),
            meal_name="Kolacja",
            dish_name="Łosoś pieczony z batatem i brokułami",
            nutrition=NutritionInfo(calories=540, protein=40, carbs=45, fat=20),
            delivery_id=1,
            meal_id=5,
        ),
    ]
    dietly_mock.get_delivered_meals = AsyncMock(return_value=fresh_meals)
    
    resync = ResyncService(dietly_mock, garmin_mock, state, dry_run=False)
    result = await resync.resync_date(date(2026, 10, 16))
    
    # Should match all 5 cleanly
    assert result["deleted"] == 5, "Should delete all 5 matched entries"
    assert result["relogged"] == 5, "Should re-log all 5 meals"
    assert result["skipped"] == 0, "No skipped entries"
    
    # Verify all 5 were deleted by correct IDs
    assert garmin_mock.delete_nutrition_log.call_count == 5
    garmin_mock.delete_nutrition_log.assert_any_call("1ecbb709157543948c25eafa2c74edc7")
    garmin_mock.delete_nutrition_log.assert_any_call("a1b2c3d4e5f67890abcdef1234567890")
    garmin_mock.delete_nutrition_log.assert_any_call("2fdbca18268654a59d36fbeb3d85fef8")
    garmin_mock.delete_nutrition_log.assert_any_call("fedcba0987654321abcdef0123456789")
    garmin_mock.delete_nutrition_log.assert_any_call("abcdef1234567890fedcba0987654321")
    
    # Verify all 5 were re-logged
    assert garmin_mock.log_meal.call_count == 5


@pytest.mark.asyncio
async def test_resync_no_entries_found():
    """Test resync when no entries exist for the date."""
    dietly_mock = AsyncMock()
    garmin_mock = AsyncMock()
    
    # Empty state (no synced meals)
    state = SyncState(bucket_name=None)
    state._state = {"synced_meals": {}}
    
    # Mock Garmin API to return some entries (including KV ones)
    garmin_mock.list_nutrition_logs = AsyncMock(return_value=[
        {
            "logId": "abc1111def2222ghi3333jkl4444mno5",
            "name": "KV: Test Meal",
            "mealSlotName": "BREAKFAST",
            "calories": 400
        },
    ])
    
    fresh_meals = []
    dietly_mock.get_delivered_meals = AsyncMock(return_value=fresh_meals)
    
    resync = ResyncService(dietly_mock, garmin_mock, state, dry_run=False)
    result = await resync.resync_date(date(2026, 10, 16))
    
    # With no state entries, nothing should match or be deleted
    assert result["deleted"] == 0
    assert result["relogged"] == 0
    assert result["skipped"] == 0
