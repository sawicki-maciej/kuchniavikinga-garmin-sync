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
    
    # Should extract all loggedFoods from all mealDetails
    assert len(logs) == 2
    
    # First log
    assert logs[0]["logId"] == "1ecbb709157543948c25eafa2c74edc7"
    assert logs[0]["foodMetaData"]["foodName"].startswith("KV: Śniadanie")
    assert logs[0]["nutritionContent"]["calories"] == 452
    
    # Second log
    assert logs[1]["logId"] == "2fdbca18268654a59d36fbeb3d85fef8"
    assert logs[1]["foodMetaData"]["foodName"].startswith("KV: Obiad")
    assert logs[1]["nutritionContent"]["calories"] == 620


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
