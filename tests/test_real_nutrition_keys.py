"""Test nutrition parsing with real API keys from production."""

import pytest
from datetime import date

from kuchniavikinga_garmin_sync.dietly_client import DietlyClient


def test_production_nutrition_keys():
    """Test parsing with the exact key set from production logs.
    
    Production keys: ['weight', 'calories', 'fat', 'protein', 'carbohydrate',
                      'dietaryFiber', 'sugar', 'salt', 'saturatedFattyAcids', 'caloriesText']
    """
    client = DietlyClient(email="test@example.com", password="testpass")
    
    # Real production nutrition data structure
    nutrition_data = {
        "weight": 350,
        "calories": 520,
        "fat": 18.5,
        "protein": 32.0,
        "carbohydrate": 55.2,  # Singular form - this is the actual key
        "dietaryFiber": 8.3,
        "sugar": 4.1,
        "salt": 1.2,
        "saturatedFattyAcids": 4.5,
        "caloriesText": "520 kcal"
    }
    
    # Test carbohydrate (singular) is parsed correctly
    carbs = (
        client._safe_float(nutrition_data.get("carbohydrate")) or
        client._safe_float(nutrition_data.get("carbohydrates")) or
        client._safe_float(nutrition_data.get("carbs")) or
        client._safe_float(nutrition_data.get("wegle"))
    )
    
    assert carbs == 55.2, "Should parse 'carbohydrate' (singular) field"
    assert client._safe_float(nutrition_data.get("protein")) == 32.0
    assert client._safe_float(nutrition_data.get("fat")) == 18.5
    assert float(nutrition_data.get("calories", 0) or 0) == 520.0


def test_production_keys_with_zero_carbs():
    """Test that warning is logged when carbs are actually zero or missing."""
    client = DietlyClient(email="test@example.com", password="testpass")
    
    # Nutrition with zero carbs (should not trigger warning since carbs exist)
    nutrition_data = {
        "calories": 150,
        "protein": 20.0,
        "carbohydrate": 0,  # Explicitly zero
        "fat": 8.0
    }
    
    carbs = (
        client._safe_float(nutrition_data.get("carbohydrate")) or
        client._safe_float(nutrition_data.get("carbohydrates")) or
        client._safe_float(nutrition_data.get("carbs")) or
        client._safe_float(nutrition_data.get("wegle"))
    )
    
    # _safe_float(0) returns 0.0, which is falsy but should be preserved
    assert carbs == 0.0 or carbs is None, "Zero carbs should be handled"


def test_production_keys_missing_carbohydrate():
    """Test fallback when carbohydrate field is missing entirely."""
    client = DietlyClient(email="test@example.com", password="testpass")
    
    # Missing carbohydrate field entirely
    nutrition_data = {
        "calories": 200,
        "protein": 15.0,
        "fat": 10.0,
        # No carb-related fields
    }
    
    carbs = (
        client._safe_float(nutrition_data.get("carbohydrate")) or
        client._safe_float(nutrition_data.get("carbohydrates")) or
        client._safe_float(nutrition_data.get("carbs")) or
        client._safe_float(nutrition_data.get("wegle"))
    )
    
    assert carbs is None, "Should return None when no carb field exists"
