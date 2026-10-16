"""Test date filtering and deletion handling in DietlyClient."""

import pytest
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from kuchniavikinga_garmin_sync.dietly_client import DietlyClient, DeliveredMeal


@pytest.mark.asyncio
async def test_get_delivered_meals_includes_today():
    """Test that today's deliveries are included, not skipped."""
    client = DietlyClient(email="test@example.com", password="testpass")
    
    # Get today in Europe/Warsaw timezone
    warsaw_tz = ZoneInfo("Europe/Warsaw")
    today_warsaw = datetime.now(warsaw_tz).date()
    yesterday = today_warsaw - timedelta(days=1)
    tomorrow = today_warsaw + timedelta(days=1)
    
    # Mock the methods
    client._authenticated = True
    client._session = MagicMock()
    
    # Mock get_active_order_ids to return one order
    client.get_active_order_ids = AsyncMock(return_value=[123])
    
    # Mock get_order_deliveries to return today, yesterday, and tomorrow
    client.get_order_deliveries = AsyncMock(return_value=[
        {
            "deliveryId": 1,
            "date": yesterday.strftime("%Y-%m-%d"),
            "deleted": False,
        },
        {
            "deliveryId": 2,
            "date": today_warsaw.strftime("%Y-%m-%d"),
            "deleted": False,
        },
        {
            "deliveryId": 3,
            "date": tomorrow.strftime("%Y-%m-%d"),
            "deleted": False,
        },
    ])
    
    # Mock get_delivery_meals to return meals for each delivery
    async def mock_get_meals(delivery_id):
        return [{
            "deliveryMealId": delivery_id * 10,
            "mealName": "Śniadanie",
            "menuMealName": "Test Meal",
            "deleted": False,
            "nutrition": {
                "calories": 500,
                "protein": 30,
                "carbs": 50,
                "fat": 20,
            }
        }]
    
    client.get_delivery_meals = AsyncMock(side_effect=mock_get_meals)
    
    # Call get_delivered_meals for yesterday to today
    meals = await client.get_delivered_meals(yesterday, today_warsaw)
    
    # Should include yesterday and today, but NOT tomorrow
    assert len(meals) == 2, f"Expected 2 meals (yesterday + today), got {len(meals)}"
    
    # Verify the delivery dates
    delivery_dates = {meal.delivery_date for meal in meals}
    assert yesterday in delivery_dates, "Yesterday's meal should be included"
    assert today_warsaw in delivery_dates, "Today's meal should be included"
    assert tomorrow not in delivery_dates, "Tomorrow's meal should NOT be included"


@pytest.mark.asyncio
async def test_get_delivered_meals_skips_deleted():
    """Test that deleted deliveries and meals are skipped."""
    client = DietlyClient(email="test@example.com", password="testpass")
    
    warsaw_tz = ZoneInfo("Europe/Warsaw")
    today_warsaw = datetime.now(warsaw_tz).date()
    
    client._authenticated = True
    client._session = MagicMock()
    
    # Mock get_active_order_ids
    client.get_active_order_ids = AsyncMock(return_value=[123])
    
    # Mock get_order_deliveries - include one deleted delivery
    client.get_order_deliveries = AsyncMock(return_value=[
        {
            "deliveryId": 1,
            "date": today_warsaw.strftime("%Y-%m-%d"),
            "deleted": False,  # Not deleted
        },
        {
            "deliveryId": 2,
            "date": today_warsaw.strftime("%Y-%m-%d"),
            "deleted": True,  # Deleted - should be skipped
        },
    ])
    
    # Mock get_delivery_meals - delivery 1 has one normal and one deleted meal
    async def mock_get_meals(delivery_id):
        if delivery_id == 1:
            return [
                {
                    "deliveryMealId": 10,
                    "mealName": "Śniadanie",
                    "menuMealName": "Normal Meal",
                    "deleted": False,
                    "nutrition": {"calories": 500, "protein": 30, "carbs": 50, "fat": 20}
                },
                {
                    "deliveryMealId": 11,
                    "mealName": "Obiad",
                    "menuMealName": "Deleted Meal",
                    "deleted": True,  # Deleted - should be skipped
                    "nutrition": {"calories": 600, "protein": 35, "carbs": 55, "fat": 25}
                }
            ]
        return []
    
    client.get_delivery_meals = AsyncMock(side_effect=mock_get_meals)
    
    # Call get_delivered_meals
    meals = await client.get_delivered_meals(today_warsaw, today_warsaw)
    
    # Should only have 1 meal (not deleted delivery, not deleted meal)
    assert len(meals) == 1, f"Expected 1 meal, got {len(meals)}"
    assert meals[0].dish_name == "Normal Meal"
    assert meals[0].meal_id == 10


@pytest.mark.asyncio
async def test_get_delivered_meals_future_date_skipped():
    """Test that future deliveries are always skipped."""
    client = DietlyClient(email="test@example.com", password="testpass")
    
    warsaw_tz = ZoneInfo("Europe/Warsaw")
    today_warsaw = datetime.now(warsaw_tz).date()
    future_date = today_warsaw + timedelta(days=5)
    
    client._authenticated = True
    client._session = MagicMock()
    
    client.get_active_order_ids = AsyncMock(return_value=[123])
    
    # Only future delivery
    client.get_order_deliveries = AsyncMock(return_value=[
        {
            "deliveryId": 1,
            "date": future_date.strftime("%Y-%m-%d"),
            "deleted": False,
        },
    ])
    
    client.get_delivery_meals = AsyncMock(return_value=[{
        "deliveryMealId": 10,
        "mealName": "Śniadanie",
        "menuMealName": "Future Meal",
        "deleted": False,
        "nutrition": {"calories": 500, "protein": 30, "carbs": 50, "fat": 20}
    }])
    
    # Try to fetch including future date
    meals = await client.get_delivered_meals(today_warsaw, future_date)
    
    # Should be empty - future meals not included
    assert len(meals) == 0, f"Expected 0 meals (future delivery), got {len(meals)}"
