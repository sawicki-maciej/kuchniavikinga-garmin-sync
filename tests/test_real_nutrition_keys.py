"""Test nutrition parsing with real API keys from production."""

import pytest
from datetime import date
from unittest.mock import AsyncMock

from kuchniavikinga_garmin_sync.dietly_client import DietlyClient


@pytest.mark.asyncio
async def test_production_nutrition_keys():
    """Test parsing with the exact key set from production logs.
    
    Production keys: ['weight', 'calories', 'fat', 'protein', 'carbohydrate',
                      'dietaryFiber', 'sugar', 'salt', 'saturatedFattyAcids', 'caloriesText']
    """
    client = DietlyClient(email="test@example.com", password="testpass")
    
    # Mock HTTP session
    mock_session = AsyncMock()
    client._session = mock_session
    
    client.get_active_order_ids = AsyncMock(return_value=[123])
    client.get_order_deliveries = AsyncMock(return_value=[{
        "deliveryId": 456,
        "date": "2026-10-15",
        "deleted": False,
    }])
    
    # Mock with real production nutrition structure
    client.get_delivery_meals = AsyncMock(return_value=[{
        "deliveryMealId": 789,
        "mealName": "Obiad",
        "menuMealName": "Kurczak z warzywami",
        "deleted": False,
        "nutrition": {
            "weight": 350,
            "calories": 520,
            "fat": 18.5,
            "protein": 32.0,
            "carbohydrate": 55.2,  # Singular form - actual production key
            "dietaryFiber": 8.3,
            "sugar": 4.1,
            "salt": 1.2,
            "saturatedFattyAcids": 4.5,
            "caloriesText": "520 kcal"
        }
    }])
    
    # Call real parsing code
    meals = await client.get_delivered_meals(date(2026, 10, 15), date(2026, 10, 15))
    
    assert len(meals) == 1
    meal = meals[0]
    
    # Verify all nutrition fields are parsed correctly
    assert meal.nutrition.calories == 520.0
    assert meal.nutrition.protein == 32.0
    assert meal.nutrition.fat == 18.5
    assert meal.nutrition.carbs == 55.2  # Parsed from 'carbohydrate'


@pytest.mark.asyncio
async def test_production_keys_with_zero_carbs():
    """Test that zero carbs are handled correctly (not skipped)."""
    client = DietlyClient(email="test@example.com", password="testpass")
    
    mock_session = AsyncMock()
    client._session = mock_session
    
    client.get_active_order_ids = AsyncMock(return_value=[123])
    client.get_order_deliveries = AsyncMock(return_value=[{
        "deliveryId": 456,
        "date": "2026-10-15",
        "deleted": False,
    }])
    
    # Nutrition with explicitly zero carbs (protein-heavy meal)
    client.get_delivery_meals = AsyncMock(return_value=[{
        "deliveryMealId": 789,
        "mealName": "Śniadanie",
        "menuMealName": "Protein shake",
        "deleted": False,
        "nutrition": {
            "calories": 150,
            "protein": 20.0,
            "carbohydrate": 0,  # Explicitly zero
            "fat": 8.0
        }
    }])
    
    meals = await client.get_delivered_meals(date(2026, 10, 15), date(2026, 10, 15))
    
    assert len(meals) == 1
    # Zero carbs should be preserved (0.0, not None)
    assert meals[0].nutrition.carbs == 0.0 or meals[0].nutrition.carbs is None


@pytest.mark.asyncio
async def test_production_keys_missing_carbohydrate():
    """Test that meals with missing carbs still get logged (carbs = None)."""
    client = DietlyClient(email="test@example.com", password="testpass")
    
    mock_session = AsyncMock()
    client._session = mock_session
    
    client.get_active_order_ids = AsyncMock(return_value=[123])
    client.get_order_deliveries = AsyncMock(return_value=[{
        "deliveryId": 456,
        "date": "2026-10-15",
        "deleted": False,
    }])
    
    # Missing carbohydrate field entirely
    client.get_delivery_meals = AsyncMock(return_value=[{
        "deliveryMealId": 789,
        "mealName": "Obiad",
        "menuMealName": "Mystery Meal",
        "deleted": False,
        "nutrition": {
            "calories": 200,
            "protein": 15.0,
            "fat": 10.0,
            # No carb-related fields
        }
    }])
    
    meals = await client.get_delivered_meals(date(2026, 10, 15), date(2026, 10, 15))
    
    assert len(meals) == 1
    # Carbs should be None when missing
    assert meals[0].nutrition.carbs is None
