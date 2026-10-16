"""Basic test for DietlyClient."""

import pytest
from datetime import date
from kuchniavikinga_garmin_sync.dietly_client import DietlyClient, DeliveredMeal, NutritionInfo


@pytest.mark.asyncio
async def test_dietly_client_init():
    """Test DietlyClient initialization."""
    client = DietlyClient(email="test@example.com", password="testpass")
    assert client.email == "test@example.com"
    assert client.password == "testpass"
    assert not client._authenticated


def test_delivered_meal_creation():
    """Test DeliveredMeal dataclass."""
    nutrition = NutritionInfo(
        calories=500.0,
        protein=30.0,
        carbs=50.0,
        fat=15.0,
    )
    
    meal = DeliveredMeal(
        delivery_date=date(2026, 10, 15),
        meal_name="Śniadanie",
        dish_name="Jajecznica z warzywami",
        nutrition=nutrition,
        delivery_id=12345,
        meal_id=67890,
    )
    
    assert meal.delivery_date == date(2026, 10, 15)
    assert meal.meal_name == "Śniadanie"
    assert meal.nutrition.calories == 500.0
