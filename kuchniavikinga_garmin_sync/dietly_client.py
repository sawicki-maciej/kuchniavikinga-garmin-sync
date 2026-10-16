"""Dietly API client for Kuchnia Vikinga meal data.

This client uses the reverse-engineered internal API from panel.dietly.pl.
It is UNOFFICIAL and may break if Dietly changes their API.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from curl_cffi.requests import AsyncSession

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
    """Client for Kuchnia Vikinga customer panel API.
    
    Based on JS bundle analysis of panel.kuchniavikinga.pl.
    The panel is Dietly-powered white-label platform hosted on Heroku behind Cloudflare.
    """

    # Base URL from JS bundle: baseUrl: '/api' relative to panel.kuchniavikinga.pl
    BASE_URL = "https://panel.kuchniavikinga.pl/api"
    COMPANY_ID = "kuchniavikinga"

    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self._session: AsyncSession | None = None
        self._authenticated = False

    async def __aenter__(self):
        """Async context manager entry."""
        await self.authenticate()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def authenticate(self) -> None:
        """Authenticate with Kuchnia Vikinga customer panel.
        
        Based on JS bundle analysis: POST /api/auth/login
        Uses curl_cffi with browser impersonation to handle Cloudflare protection.
        """
        try:
            if self._session is None:
                # Use curl_cffi with browser impersonation
                self._session = AsyncSession(impersonate="chrome120")

            # Visit login page first to establish session/cookies
            logger.info("Initializing session...")
            await self._session.get("https://panel.kuchniavikinga.pl/logowanie")

            # Login endpoint from JS: baseUrl + 'auth/login'
            login_url = f"{self.BASE_URL}/auth/login"
            payload = {
                "username": self.email,
                "password": self.password,
            }

            logger.info("Authenticating to Kuchnia Vikinga panel as %s", self.email)
            resp = await self._session.post(
                login_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Origin": "https://panel.kuchniavikinga.pl",
                    "Referer": "https://panel.kuchniavikinga.pl/logowanie",
                    "Accept": "application/json",
                },
            )

            if resp.status_code != 200:
                error_text = resp.text
                # Clean up session on auth failure
                await self.close()
                raise Exception(
                    f"Kuchnia Vikinga login failed: HTTP {resp.status_code} - {error_text}"
                )

            # Verify authentication by fetching profile
            # Endpoint from JS: 'profile'
            profile_url = f"{self.BASE_URL}/profile"
            resp = await self._session.get(profile_url)

            if resp.status_code != 200:
                # Clean up session on verification failure
                await self.close()
                raise Exception(
                    f"Profile fetch failed: HTTP {resp.status_code}"
                )

            profile = resp.json()
            logger.info("Authenticated as: %s", profile.get("email"))

            self._authenticated = True

        except Exception as e:
            # Ensure session is closed on any error
            await self.close()
            raise

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None
        self._authenticated = False

    def _get_headers(self) -> dict[str, str]:
        """Get headers for API calls.
        
        Based on panel behavior - minimal headers needed after authentication.
        """
        return {
            "Accept": "application/json",
        }

    async def get_active_orders(self) -> list[dict[str, Any]]:
        """Fetch all active orders.
        
        Endpoint from JS: 'company/customer/order/all'
        """
        if not self._authenticated or not self._session:
            raise Exception("Not authenticated - call authenticate() first")

        # Endpoint from JS bundle analysis
        url = f"{self.BASE_URL}/company/customer/order/all"
        logger.debug("Fetching orders from %s", url)

        resp = await self._session.get(url, headers=self._get_headers())

        if resp.status_code != 200:
            raise Exception(f"Failed to fetch orders: HTTP {resp.status_code}")

        data = resp.json()
        
        # Filter for active orders - structure needs verification with real data
        orders = []
        if isinstance(data, list):
            orders = [order for order in data if order.get("status") == "ACTIVE"]
        elif isinstance(data, dict) and "content" in data:
            orders = [
                order
                for order in data.get("content", [])
                if order.get("status") == "ACTIVE"
            ]
        else:
            # If it's a different structure, return all and let caller filter
            orders = [data] if isinstance(data, dict) else []
            
        logger.info("Found %d active orders", len(orders))
        return orders

    async def get_order_deliveries(self, order_id: int) -> list[dict[str, Any]]:
        """Fetch deliveries for an order.
        
        Endpoint from JS: 'company/customer/order/${orderId}'
        """
        if not self._authenticated or not self._session:
            raise Exception("Not authenticated")

        # Endpoint from JS bundle analysis
        url = f"{self.BASE_URL}/company/customer/order/{order_id}"
        logger.debug("Fetching deliveries for order %d", order_id)

        resp = await self._session.get(url, headers=self._get_headers())

        if resp.status_code != 200:
            raise Exception(
                f"Failed to fetch order {order_id}: HTTP {resp.status_code}"
            )

        data = resp.json()
        deliveries = data.get("deliveries", [])
        logger.debug("Order %d has %d deliveries", order_id, len(deliveries))
        return deliveries

    async def get_delivery_meals(
        self, delivery_id: int
    ) -> list[dict[str, Any]]:
        """Fetch meal details for a delivery.
        
        Endpoint from JS: 'company/general/menus/delivery/${deliveryId}/new'
        """
        if not self._authenticated or not self._session:
            raise Exception("Not authenticated")

        # Endpoint from JS bundle analysis
        url = f"{self.BASE_URL}/company/general/menus/delivery/{delivery_id}/new"
        logger.debug("Fetching meals for delivery %d", delivery_id)

        resp = await self._session.get(url, headers=self._get_headers())

        if resp.status_code != 200:
            raise Exception(
                f"Failed to fetch delivery {delivery_id} meals: HTTP {resp.status_code}"
            )

        data = resp.json()
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
