"""Test list_nutrition_logs parsing."""

import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock

from kuchniavikinga_garmin_sync.garmin_client import GarminConnectClient


@pytest.mark.asyncio
async def test_list_nutrition_logs_parses_meal_details():
    """Test that list_nutrition_logs correctly parses mealDetails structure."""
    client = GarminConnectClient()
    
    # Mock the internal _client
    client._client = MagicMock()
    
    # Mock response structure from Garmin API
    # Structure: {"mealDetails": [{"meal": {...}, "foodLogs": [...]}]}
    mock_response = {
        "calendarDate": "2026-10-16",
        "mealDetails": [
            {
                "meal": {
                    "mealId": 1,
                    "mealName": "Breakfast"
                },
                "foodLogs": [
                    {
                        "foodLogId": 12345,
                        "name": "KV: Śniadanie - Test Meal",
                        "calories": 400
                    },
                    {
                        "foodLogId": 12346,
                        "name": "KV: Śniadanie - Another Meal",
                        "calories": 350
                    }
                ]
            },
            {
                "meal": {
                    "mealId": 3,
                    "mealName": "Lunch"
                },
                "foodLogs": [
                    {
                        "foodLogId": 67890,
                        "name": "KV: Obiad - Lunch Meal",
                        "calories": 600
                    }
                ]
            }
        ]
    }
    
    client._client.get_nutrition_log = AsyncMock(return_value=mock_response)
    
    # List logs
    logs = await client.list_nutrition_logs(date(2026, 10, 16))
    
    # Should extract all foodLogs from all mealDetails
    assert len(logs) == 3
    assert logs[0]["foodLogId"] == 12345
    assert logs[0]["name"] == "KV: Śniadanie - Test Meal"
    assert logs[1]["foodLogId"] == 12346
    assert logs[2]["foodLogId"] == 67890


@pytest.mark.asyncio
async def test_list_nutrition_logs_empty_response():
    """Test list_nutrition_logs with empty response."""
    client = GarminConnectClient()
    client._client = MagicMock()
    
    # Empty response
    client._client.get_nutrition_log = AsyncMock(return_value={})
    
    logs = await client.list_nutrition_logs(date(2026, 10, 16))
    
    assert logs == []


@pytest.mark.asyncio
async def test_list_nutrition_logs_no_food_logs():
    """Test list_nutrition_logs when mealDetails exist but no foodLogs."""
    client = GarminConnectClient()
    client._client = MagicMock()
    
    # Meals configured but no food logged
    mock_response = {
        "calendarDate": "2026-10-16",
        "mealDetails": [
            {
                "meal": {
                    "mealId": 1,
                    "mealName": "Breakfast"
                },
                "foodLogs": []
            }
        ]
    }
    
    client._client.get_nutrition_log = AsyncMock(return_value=mock_response)
    
    logs = await client.list_nutrition_logs(date(2026, 10, 16))
    
    assert logs == []
