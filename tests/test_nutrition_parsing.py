"""Test nutrition data parsing from Dietly API."""

import pytest
from datetime import date

from kuchniavikinga_garmin_sync.dietly_client import DietlyClient, NutritionInfo


def test_carbs_field_parsing():
    """Test that carbs are parsed from multiple possible field names."""
    client = DietlyClient(email="test@example.com", password="testpass")
    
    # Test "carbohydrates" field (most likely correct one)
    nutrition_data = {
        "calories": 500,
        "protein": 30,
        "carbohydrates": 50,  # Full field name
        "fat": 20
    }
    carbs = (
        client._safe_float(nutrition_data.get("carbohydrates")) or
        client._safe_float(nutrition_data.get("carbs")) or
        client._safe_float(nutrition_data.get("wegle"))
    )
    assert carbs == 50.0, "Should parse carbohydrates field"
    
    # Test "carbs" field (shorthand)
    nutrition_data = {
        "calories": 500,
        "protein": 30,
        "carbs": 45,
        "fat": 20
    }
    carbs = (
        client._safe_float(nutrition_data.get("carbohydrates")) or
        client._safe_float(nutrition_data.get("carbs")) or
        client._safe_float(nutrition_data.get("wegle"))
    )
    assert carbs == 45.0, "Should parse carbs field"
    
    # Test "wegle" field (Polish)
    nutrition_data = {
        "calories": 500,
        "protein": 30,
        "wegle": 55,  # Polish for carbohydrates
        "fat": 20
    }
    carbs = (
        client._safe_float(nutrition_data.get("carbohydrates")) or
        client._safe_float(nutrition_data.get("carbs")) or
        client._safe_float(nutrition_data.get("wegle"))
    )
    assert carbs == 55.0, "Should parse wegle field (Polish)"
    
    # Test missing carbs
    nutrition_data = {
        "calories": 500,
        "protein": 30,
        "fat": 20
    }
    carbs = (
        client._safe_float(nutrition_data.get("carbohydrates")) or
        client._safe_float(nutrition_data.get("carbs")) or
        client._safe_float(nutrition_data.get("wegle"))
    )
    assert carbs is None, "Should return None when no carbs field present"


def test_safe_float_edge_cases():
    """Test _safe_float helper handles edge cases."""
    client = DietlyClient(email="test@example.com", password="testpass")
    
    assert client._safe_float(None) is None
    assert client._safe_float(0) == 0.0
    assert client._safe_float("") is None
    assert client._safe_float(42) == 42.0
    assert client._safe_float(42.5) == 42.5
    assert client._safe_float("42.5") == 42.5
