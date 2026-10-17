"""Test nutrition data parsing from Dietly API."""

import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from kuchniavikinga_garmin_sync.dietly_client import DietlyClient, DeliveredMeal


@pytest.mark.asyncio
async def test_carbs_field_parsing_carbohydrate():
    """Test that carbs are parsed from 'carbohydrate' field (actual API field)."""
    client = DietlyClient(email="test@example.com", password="testpass")
    
    # Mock the HTTP session
    mock_session = AsyncMock()
    client._session = mock_session
    
    # Mock active order IDs
    client.get_active_order_ids = AsyncMock(return_value=[123])
    
    # Mock order deliveries
    client.get_order_deliveries = AsyncMock(return_value=[{
        "deliveryId": 456,
        "date": "2026-10-15",
        "deleted": False,
    }])
    
    # Mock delivery meals with 'carbohydrate' field (singular)
    client.get_delivery_meals = AsyncMock(return_value=[{
        "deliveryMealId": 789,
        "mealName": "Śniadanie",
        "menuMealName": "Test Meal",
        "deleted": False,
        "nutrition": {
            "calories": 500,
            "protein": 30,
            "carbohydrate": 50.0,  # Actual API field name
            "fat": 20
        }
    }])
    
    # Call real parsing code
    meals = await client.get_delivered_meals(date(2026, 10, 15), date(2026, 10, 15))
    
    assert len(meals) == 1
    assert meals[0].nutrition.carbs == 50.0


@pytest.mark.asyncio
async def test_carbs_field_parsing_carbohydrates():
    """Test that carbs are parsed from 'carbohydrates' field (plural fallback)."""
    client = DietlyClient(email="test@example.com", password="testpass")
    
    mock_session = AsyncMock()
    client._session = mock_session
    
    client.get_active_order_ids = AsyncMock(return_value=[123])
    client.get_order_deliveries = AsyncMock(return_value=[{
        "deliveryId": 456,
        "date": "2026-10-15",
        "deleted": False,
    }])
    
    # Mock with 'carbohydrates' plural
    client.get_delivery_meals = AsyncMock(return_value=[{
        "deliveryMealId": 789,
        "mealName": "Śniadanie",
        "menuMealName": "Test Meal",
        "deleted": False,
        "nutrition": {
            "calories": 500,
            "protein": 30,
            "carbohydrates": 45.0,  # Plural
            "fat": 20
        }
    }])
    
    meals = await client.get_delivered_meals(date(2026, 10, 15), date(2026, 10, 15))
    
    assert len(meals) == 1
    assert meals[0].nutrition.carbs == 45.0


@pytest.mark.asyncio
async def test_carbs_field_parsing_wegle():
    """Test that carbs are parsed from 'wegle' field (Polish fallback)."""
    client = DietlyClient(email="test@example.com", password="testpass")
    
    mock_session = AsyncMock()
    client._session = mock_session
    
    client.get_active_order_ids = AsyncMock(return_value=[123])
    client.get_order_deliveries = AsyncMock(return_value=[{
        "deliveryId": 456,
        "date": "2026-10-15",
        "deleted": False,
    }])
    
    # Mock with Polish 'wegle' field
    client.get_delivery_meals = AsyncMock(return_value=[{
        "deliveryMealId": 789,
        "mealName": "Śniadanie",
        "menuMealName": "Test Meal",
        "deleted": False,
        "nutrition": {
            "calories": 500,
            "protein": 30,
            "wegle": 55.0,  # Polish
            "fat": 20
        }
    }])
    
    meals = await client.get_delivered_meals(date(2026, 10, 15), date(2026, 10, 15))
    
    assert len(meals) == 1
    assert meals[0].nutrition.carbs == 55.0


@pytest.mark.asyncio
async def test_carbs_missing():
    """Test that carbs are None when missing."""
    client = DietlyClient(email="test@example.com", password="testpass")
    
    mock_session = AsyncMock()
    client._session = mock_session
    
    client.get_active_order_ids = AsyncMock(return_value=[123])
    client.get_order_deliveries = AsyncMock(return_value=[{
        "deliveryId": 456,
        "date": "2026-10-15",
        "deleted": False,
    }])
    
    # Mock with no carbs field
    client.get_delivery_meals = AsyncMock(return_value=[{
        "deliveryMealId": 789,
        "mealName": "Śniadanie",
        "menuMealName": "Test Meal",
        "deleted": False,
        "nutrition": {
            "calories": 500,
            "protein": 30,
            "fat": 20
        }
    }])
    
    meals = await client.get_delivered_meals(date(2026, 10, 15), date(2026, 10, 15))
    
    assert len(meals) == 1
    assert meals[0].nutrition.carbs is None


@pytest.mark.asyncio
async def test_nutrition_null_skips_meal():
    """Test that meals with nutrition: null are skipped."""
    client = DietlyClient(email="test@example.com", password="testpass")
    
    mock_session = AsyncMock()
    client._session = mock_session
    
    client.get_active_order_ids = AsyncMock(return_value=[123])
    client.get_order_deliveries = AsyncMock(return_value=[{
        "deliveryId": 456,
        "date": "2026-10-15",
        "deleted": False,
    }])
    
    # Mock with nutrition: null
    client.get_delivery_meals = AsyncMock(return_value=[{
        "deliveryMealId": 789,
        "mealName": "Śniadanie",
        "menuMealName": "Test Meal",
        "deleted": False,
        "nutrition": None  # null
    }])
    
    meals = await client.get_delivered_meals(date(2026, 10, 15), date(2026, 10, 15))
    
    # Should skip this meal
    assert len(meals) == 0


@pytest.mark.asyncio
async def test_missing_calories_skips_meal():
    """Test that meals with missing or zero calories are skipped."""
    client = DietlyClient(email="test@example.com", password="testpass")
    
    mock_session = AsyncMock()
    client._session = mock_session
    
    client.get_active_order_ids = AsyncMock(return_value=[123])
    client.get_order_deliveries = AsyncMock(return_value=[{
        "deliveryId": 456,
        "date": "2026-10-15",
        "deleted": False,
    }])
    
    # Mock with missing and zero calories
    client.get_delivery_meals = AsyncMock(return_value=[
        {
            "deliveryMealId": 789,
            "mealName": "Śniadanie",
            "menuMealName": "Test Meal 1",
            "deleted": False,
            "nutrition": {
                "protein": 30,
                "carbohydrate": 50,
                "fat": 20
                # calories missing
            }
        },
        {
            "deliveryMealId": 790,
            "mealName": "Obiad",
            "menuMealName": "Test Meal 2",
            "deleted": False,
            "nutrition": {
                "calories": 0,  # Zero
                "protein": 30,
                "carbohydrate": 50,
                "fat": 20
            }
        }
    ])
    
    meals = await client.get_delivered_meals(date(2026, 10, 15), date(2026, 10, 15))
    
    # Both should be skipped
    assert len(meals) == 0


def test_safe_float_edge_cases():
    """Test _safe_float helper handles edge cases."""
    client = DietlyClient(email="test@example.com", password="testpass")
    
    assert client._safe_float(None) is None
    assert client._safe_float(0) == 0.0
    assert client._safe_float("") is None
    assert client._safe_float(42) == 42.0
    assert client._safe_float(42.5) == 42.5
    assert client._safe_float("42.5") == 42.5
