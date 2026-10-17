"""Test parsing with real Garmin API response structure."""

import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock

from kuchniavikinga_garmin_sync.garmin_client import GarminConnectClient


@pytest.mark.asyncio
async def test_list_nutrition_logs_real_structure():
    """Test parsing with actual production response structure."""
    client = GarminConnectClient()
    client._client = MagicMock()
    
    # Real production response structure from 2026-10-16
    mock_response = {
        "mealDate": "2026-10-16",
        "dayStartTime": "00:00:00",
        "dayEndTime": "23:59:59",
        "dailyViewType": "DETAILED",
        "dailyNutritionGoals": {},
        "dailyNutritionContent": {},
        "mealDetails": [
            {
                "meal": {
                    "mealId": 447717,
                    "mealIndex": 1,
                    "mealName": "BREAKFAST"
                },
                "loggedFoods": [
                    {
                        "logId": "1ecbb709157543948c25eafa2c74edc7",
                        "logTimestamp": "2026-10-16T07:00:00.000Z",
                        "logSource": "GCW",
                        "logCategory": "QUICK_ADD",
                        "foodMetaData": {
                            "foodName": "KV: Śniadanie - Gofr czekoladowy, jogurt malinowy",
                            "customFoodType": "FOOD"
                        },
                        "nutritionContent": {
                            "calories": 452,
                            "protein": 20.5,
                            "fat": 15.3,
                            "carbs": 50.2
                        },
                        "isFavorite": False,
                        "mealId": 447717,
                        "foodInactive": False,
                        "type": "FOOD"
                    }
                ]
            },
            {
                "meal": {
                    "mealId": 447718,
                    "mealIndex": 3,
                    "mealName": "LUNCH"
                },
                "loggedFoods": [
                    {
                        "logId": "2fdbca18268654a59d36fbeb3d85fef8",
                        "logTimestamp": "2026-10-16T13:00:00.000Z",
                        "logSource": "GCW",
                        "logCategory": "QUICK_ADD",
                        "foodMetaData": {
                            "foodName": "KV: Obiad - Pierś z kurczaka w sosie curry",
                            "customFoodType": "FOOD"
                        },
                        "nutritionContent": {
                            "calories": 620,
                            "protein": 35.0,
                            "fat": 18.0,
                            "carbs": 72.5
                        },
                        "isFavorite": False,
                        "mealId": 447718,
                        "foodInactive": False,
                        "type": "FOOD"
                    }
                ]
            }
        ],
        "loggedFoodsWithServingSizes": []
    }
    
    client._client.get_nutrition_log = AsyncMock(return_value=mock_response)
    
    # List logs
    logs = await client.list_nutrition_logs(date(2026, 10, 16))
    
    # Should extract all loggedFoods from all mealDetails with enriched data
    assert len(logs) == 2
    
    # First log - should have enriched fields and parent slot
    assert logs[0]["logId"] == "1ecbb709157543948c25eafa2c74edc7"
    assert logs[0]["name"] == "KV: Śniadanie - Gofr czekoladowy, jogurt malinowy"
    assert logs[0]["calories"] == 452
    assert logs[0]["mealSlotName"] == "BREAKFAST"
    assert "_raw" in logs[0]  # Raw entry preserved
    
    # Second log - different slot
    assert logs[1]["logId"] == "2fdbca18268654a59d36fbeb3d85fef8"
    assert logs[1]["name"] == "KV: Obiad - Pierś z kurczaka w sosie curry"
    assert logs[1]["calories"] == 620
    assert logs[1]["mealSlotName"] == "LUNCH"


@pytest.mark.asyncio
async def test_list_nutrition_logs_full_day_five_meals():
    """Test parsing with actual full-day 5-meal structure from 2026-10-16."""
    client = GarminConnectClient()
    client._client = MagicMock()
    
    # Real production response with 5 meals (actual structure from 2026-10-16)
    mock_response = {
        "mealDate": "2026-10-16",
        "dayStartTime": "00:00:00",
        "dayEndTime": "23:59:59",
        "dailyViewType": "DETAILED",
        "dailyNutritionGoals": {},
        "dailyNutritionContent": {},
        "mealDetails": [
            {
                "meal": {
                    "mealId": 447717,
                    "mealIndex": 1,
                    "mealName": "BREAKFAST"
                },
                "loggedFoods": [
                    {
                        "logId": "1ecbb709157543948c25eafa2c74edc7",
                        "foodMetaData": {
                            "foodName": "KV: Gofr czekoladowy, jogurt malinowy, winog",
                            "customFoodType": "FOOD"
                        },
                        "nutritionContent": {
                            "calories": 452
                        }
                    }
                ]
            },
            {
                "meal": {
                    "mealId": 447718,
                    "mealIndex": 2,
                    "mealName": "SNACKS"
                },
                "loggedFoods": [
                    {
                        "logId": "a1b2c3d4e5f67890abcdef1234567890",
                        "foodMetaData": {
                            "foodName": "KV: Batony proteinowe, jajko, orzechy włosk",
                            "customFoodType": "FOOD"
                        },
                        "nutritionContent": {
                            "calories": 380
                        }
                    }
                ]
            },
            {
                "meal": {
                    "mealId": 447719,
                    "mealIndex": 3,
                    "mealName": "LUNCH"
                },
                "loggedFoods": [
                    {
                        "logId": "2fdbca18268654a59d36fbeb3d85fef8",
                        "foodMetaData": {
                            "foodName": "KV: Pierś z kurczaka w sosie curry z warzyw",
                            "customFoodType": "FOOD"
                        },
                        "nutritionContent": {
                            "calories": 620
                        }
                    }
                ]
            },
            {
                "meal": {
                    "mealId": 447720,
                    "mealIndex": 4,
                    "mealName": "SNACKS"
                },
                "loggedFoods": [
                    {
                        "logId": "fedcba0987654321abcdef0123456789",
                        "foodMetaData": {
                            "foodName": "KV: Shake proteinowy, banan",
                            "customFoodType": "FOOD"
                        },
                        "nutritionContent": {
                            "calories": 290
                        }
                    }
                ]
            },
            {
                "meal": {
                    "mealId": 447721,
                    "mealIndex": 5,
                    "mealName": "DINNER"
                },
                "loggedFoods": [
                    {
                        "logId": "abcdef1234567890fedcba0987654321",
                        "foodMetaData": {
                            "foodName": "KV: Łosoś pieczony z batatem i brokułami",
                            "customFoodType": "FOOD"
                        },
                        "nutritionContent": {
                            "calories": 540
                        }
                    }
                ]
            }
        ],
        "loggedFoodsWithServingSizes": []
    }
    
    client._client.get_nutrition_log = AsyncMock(return_value=mock_response)
    
    # List logs
    logs = await client.list_nutrition_logs(date(2026, 10, 16))
    
    # Should extract all 5 meals
    assert len(logs) == 5
    
    # Verify each meal has correct slot and data
    expected = [
        ("BREAKFAST", "KV: Gofr czekoladowy, jogurt malinowy, winog", 452),
        ("SNACKS", "KV: Batony proteinowe, jajko, orzechy włosk", 380),
        ("LUNCH", "KV: Pierś z kurczaka w sosie curry z warzyw", 620),
        ("SNACKS", "KV: Shake proteinowy, banan", 290),
        ("DINNER", "KV: Łosoś pieczony z batatem i brokułami", 540),
    ]
    
    for i, (expected_slot, expected_name, expected_cal) in enumerate(expected):
        assert logs[i]["mealSlotName"] == expected_slot, f"Meal {i} slot mismatch"
        assert logs[i]["name"] == expected_name, f"Meal {i} name mismatch"
        assert logs[i]["calories"] == expected_cal, f"Meal {i} calories mismatch"
        assert logs[i]["logId"] is not None, f"Meal {i} missing logId"


@pytest.mark.asyncio
async def test_list_nutrition_logs_backwards_compatibility():
    """Test that old guessed structure still works."""
    client = GarminConnectClient()
    client._client = MagicMock()
    
    # Old guessed structure (for backwards compatibility)
    mock_response = {
        "mealDetails": [
            {
                "meal": {"mealId": 1, "mealName": "Breakfast"},
                "foodLogs": [
                    {
                        "foodLogId": 12345,
                        "name": "KV: Old Structure Meal",
                        "calories": 400
                    }
                ]
            }
        ]
    }
    
    client._client.get_nutrition_log = AsyncMock(return_value=mock_response)
    
    logs = await client.list_nutrition_logs(date(2026, 10, 16))
    
    # Should still extract from foodLogs (old structure)
    assert len(logs) == 1
    assert logs[0]["foodLogId"] == 12345
    assert logs[0]["name"] == "KV: Old Structure Meal"
