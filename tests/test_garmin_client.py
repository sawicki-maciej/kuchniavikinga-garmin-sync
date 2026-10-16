"""Basic test for GarminConnectClient."""

import pytest
from datetime import date
from kuchniavikinga_garmin_sync.garmin_client import GarminConnectClient
from kuchniavikinga_garmin_sync.dietly_client import DeliveredMeal, NutritionInfo


def test_garmin_client_init():
    """Test GarminConnectClient initialization."""
    client = GarminConnectClient(email="test@example.com", password="testpass")
    assert client.email == "test@example.com"
    assert client.password == "testpass"
    assert not client._authenticated


def test_estimate_meal_time():
    """Test meal time estimation."""
    client = GarminConnectClient(email="test@example.com", password="testpass")
    
    assert client._estimate_meal_time("Śniadanie") == "07:00:00"
    assert client._estimate_meal_time("II Śniadanie") == "10:00:00"
    assert client._estimate_meal_time("Obiad") == "13:00:00"
    assert client._estimate_meal_time("Podwieczorek") == "16:00:00"
    assert client._estimate_meal_time("Kolacja") == "19:00:00"
    assert client._estimate_meal_time("Unknown") == "13:00:00"  # default
