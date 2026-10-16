"""Dietly API client for Kuchnia Vikinga meal data.

This client uses the reverse-engineered internal API from panel.dietly.pl.
It is UNOFFICIAL and may break if Dietly changes their API.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class NutritionInfo:
    """Nutrition information for a meal."""

    calories: float
    protein: float | None
    carbs: float | None
    fat: float | None


@dataclass
class DeliveredMeal:
    """A delivered meal from Kuchnia Vikinga."""

    delivery_date: date
    meal_name: str  # e.g. "Śniadanie", "Obiad", "Kolacja"
    dish_name: str  # e.g. "Kurczak z warzywami"
    nutrition: NutritionInfo
    delivery_id: int
    meal_id: int


class DietlyClient:
    """Client for Dietly API (Kuchnia Vikinga backend)."""

    BASE_URL = "https://dietly.pl/api"
    PANEL_BASE_URL = "https://panel.dietly.pl/api"
    COMPANY_ID = "kuchniavikinga"

    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self._session: aiohttp.ClientSession | None = None
        self._authenticated = False

    async def __aenter__(self):
        """Async context manager entry."""
        await self.authenticate()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def authenticate(self) -> None:
        """Authenticate with Dietly and establish session."""
        if self._session is None:
            self._session = aiohttp.ClientSession()

        # Login via panel.dietly.pl
        login_url = f"{self.PANEL_BASE_URL}/auth/login"
        data = {
            "username": self.email,
            "password": self.password,
        }

        logger.info("Authenticating to Dietly as %s", self.email)
        async with self._session.post(
            login_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise Exception(
                    f"Dietly login failed: HTTP {resp.status} - {error_text}"
                )

        # Verify authentication
        profile_url = f"{self.BASE_URL}/profile"
        async with self._session.get(
            profile_url,
            headers=self._get_headers(),
        ) as resp:
            if resp.status != 200:
                raise Exception(f"Dietly authentication verification failed: HTTP {resp.status}")

            profile = await resp.json()
            logger.info("Authenticated as: %s", profile.get("email"))

        self._authenticated = True

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None

    def _get_headers(self) -> dict[str, str]:
        """Get headers required for Dietly API calls."""
        return {
            "accept": "application/json",
            "company-id": self.COMPANY_ID,
            "x-launcher-type": "BROWSER_DIETLY",
        }

    async def get_active_orders(self) -> list[dict[str, Any]]:
        """Fetch all active orders."""
        if not self._authenticated or not self._session:
            raise Exception("Not authenticated - call authenticate() first")

        url = f"{self.BASE_URL}/profile/profile-order/all?page=0"
        logger.debug("Fetching active orders from %s", url)

        async with self._session.get(url, headers=self._get_headers()) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to fetch orders: HTTP {resp.status}")

            data = await resp.json()
            orders = [
                order
                for order in data.get("content", [])
                if order.get("status") == "ACTIVE"
            ]
            logger.info("Found %d active orders", len(orders))
            return orders

    async def get_order_deliveries(self, order_id: int) -> list[dict[str, Any]]:
        """Fetch deliveries for an order."""
        if not self._authenticated or not self._session:
            raise Exception("Not authenticated")

        url = f"{self.BASE_URL}/company/customer/order/{order_id}"
        logger.debug("Fetching deliveries for order %d", order_id)

        async with self._session.get(url, headers=self._get_headers()) as resp:
            if resp.status != 200:
                raise Exception(
                    f"Failed to fetch order {order_id}: HTTP {resp.status}"
                )

            data = await resp.json()
            deliveries = data.get("deliveries", [])
            logger.debug("Order %d has %d deliveries", order_id, len(deliveries))
            return deliveries

    async def get_delivery_meals(
        self, delivery_id: int
    ) -> list[dict[str, Any]]:
        """Fetch meal details for a delivery."""
        if not self._authenticated or not self._session:
            raise Exception("Not authenticated")

        url = f"{self.BASE_URL}/company/general/menus/delivery/{delivery_id}/new"
        logger.debug("Fetching meals for delivery %d", delivery_id)

        async with self._session.get(url, headers=self._get_headers()) as resp:
            if resp.status != 200:
                raise Exception(
                    f"Failed to fetch delivery {delivery_id} meals: HTTP {resp.status}"
                )

            data = await resp.json()
            meals = data.get("deliveryMenuMeal", [])
            logger.debug("Delivery %d has %d meals", delivery_id, len(meals))
            return meals

    async def get_delivered_meals(
        self, start_date: date, end_date: date
    ) -> list[DeliveredMeal]:
        """Fetch all delivered meals in the date range."""
        orders = await self.get_active_orders()
        delivered_meals: list[DeliveredMeal] = []

        for order in orders:
            order_id = order["orderId"]
            logger.debug("Processing order %d", order_id)

            deliveries = await self.get_order_deliveries(order_id)

            for delivery in deliveries:
                delivery_date_str = delivery.get("date")
                if not delivery_date_str:
                    continue

                delivery_date = datetime.strptime(delivery_date_str, "%Y-%m-%d").date()

                # Filter by date range
                if not (start_date <= delivery_date <= end_date):
                    continue

                # Check if delivered (status should be past delivery)
                # In Dietly, delivered meals have certain status flags
                # For safety, we sync meals from yesterday or earlier
                if delivery_date >= date.today():
                    logger.debug(
                        "Skipping future delivery %s (not yet delivered)",
                        delivery_date,
                    )
                    continue

                delivery_id = delivery["deliveryId"]
                meals = await self.get_delivery_meals(delivery_id)

                for meal in meals:
                    # Extract nutrition info
                    nutrition_data = meal.get("nutrition", {})
                    
                    delivered_meal = DeliveredMeal(
                        delivery_date=delivery_date,
                        meal_name=meal.get("mealName", "Unknown"),
                        dish_name=meal.get("menuMealName", "Unknown dish"),
                        nutrition=NutritionInfo(
                            calories=float(nutrition_data.get("calories", 0) or 0),
                            protein=self._safe_float(nutrition_data.get("protein")),
                            carbs=self._safe_float(nutrition_data.get("carbs")),
                            fat=self._safe_float(nutrition_data.get("fat")),
                        ),
                        delivery_id=delivery_id,
                        meal_id=meal.get("deliveryMealId", 0),
                    )
                    delivered_meals.append(delivered_meal)

                logger.info(
                    "Delivery %s: found %d meals", delivery_date, len(meals)
                )

        logger.info(
            "Total delivered meals in range %s to %s: %d",
            start_date,
            end_date,
            len(delivered_meals),
        )
        return delivered_meals

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        """Safely convert a value to float, returning None if not possible."""
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
