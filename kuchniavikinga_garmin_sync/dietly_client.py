"""Kuchnia Vikinga customer panel API client.

This client uses the internal API from panel.kuchniavikinga.pl.
It is UNOFFICIAL and may break if the panel changes.

Authentication flow (from real browser traffic):
1. GET /api/profile to obtain SESSION and XSRF-TOKEN cookies
2. POST /api/auth/login with form-encoded credentials
3. Re-read XSRF-TOKEN after login (it changes)
4. All subsequent requests need:
   - SESSION and XSRF-TOKEN cookies
   - x-xsrf-token header matching XSRF-TOKEN cookie value
   - company-id and x-launcher-type headers
"""

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

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
    
    Implements the actual authentication flow from browser traffic capture.
    """

    BASE_URL = "https://panel.kuchniavikinga.pl/api"
    PANEL_ORIGIN = "https://panel.kuchniavikinga.pl"
    COMPANY_ID = "kuchniavikinga"

    def __init__(self, email: str, password: str, dump_raw: bool = False):
        self.email = email
        self.password = password
        self.dump_raw = dump_raw
        self._session: AsyncSession | None = None
        self._authenticated = False
    
    def _masked_email(self) -> str:
        """Return masked email for logging."""
        if "@" in self.email:
            local, domain = self.email.split("@", 1)
            if len(local) <= 2:
                masked_local = "*" * len(local)
            else:
                masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
            return f"{masked_local}@{domain}"
        return "***"

    async def __aenter__(self):
        """Async context manager entry."""
        await self.authenticate()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def authenticate(self) -> None:
        """Authenticate with Kuchnia Vikinga customer panel.
        
        Flow from real browser traffic:
        1. GET /api/profile to obtain SESSION and XSRF-TOKEN cookies
        2. POST /api/auth/login with form-encoded credentials
        3. XSRF-TOKEN changes after login, re-read from cookies
        """
        try:
            if self._session is None:
                # Use curl_cffi with browser impersonation for Cloudflare
                self._session = AsyncSession(impersonate="chrome120")

            # Step 1: Get SESSION and XSRF-TOKEN cookies
            logger.info("Obtaining CSRF tokens...")
            profile_url = f"{self.BASE_URL}/profile"
            resp = await self._session.get(
                profile_url,
                headers={
                    "Accept": "application/json",
                    "Origin": self.PANEL_ORIGIN,
                    "Referer": f"{self.PANEL_ORIGIN}/",
                }
            )
            
            # We expect 401 Unauthorized here (not logged in yet)
            if resp.status_code not in [200, 401]:
                logger.warning("Unexpected status getting tokens: %d", resp.status_code)
            
            xsrf_token = self._session.cookies.get("XSRF-TOKEN")
            if not xsrf_token:
                raise Exception("Failed to obtain XSRF-TOKEN cookie")
            
            logger.debug("Obtained XSRF-TOKEN: %s", xsrf_token[:20] + "...")

            # Step 2: Login with form-encoded credentials
            login_url = f"{self.BASE_URL}/auth/login"
            login_data = urlencode({
                "username": self.email,
                "password": self.password,
            })

            logger.info("Authenticating to Kuchnia Vikinga panel as %s", self._masked_email())
            resp = await self._session.post(
                login_url,
                data=login_data,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                    "Origin": self.PANEL_ORIGIN,
                    "Referer": f"{self.PANEL_ORIGIN}/logowanie",
                    "x-xsrf-token": xsrf_token,
                    "company-id": self.COMPANY_ID,
                    "x-launcher-type": "BROWSER_PANEL",
                },
            )

            if resp.status_code != 200:
                error_text = resp.text
                # Clean up session on auth failure
                await self.close()
                raise Exception(
                    f"Login failed: HTTP {resp.status_code} - {error_text}"
                )

            logger.info("Login successful")

            # Step 3: XSRF-TOKEN changes after login, re-read it
            new_xsrf_token = self._session.cookies.get("XSRF-TOKEN")
            if new_xsrf_token:
                logger.debug("XSRF-TOKEN updated after login")
            
            # Verify authentication by fetching profile
            resp = await self._session.get(
                profile_url,
                headers=self._get_headers(),
            )

            if resp.status_code != 200:
                await self.close()
                raise Exception(
                    f"Profile verification failed: HTTP {resp.status_code}"
                )

            profile = resp.json()
            logger.info("Authenticated as: %s", profile.get("email"))
            
            if self.dump_raw:
                self._dump_json("profile", profile)

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
        
        From browser traffic: requires x-xsrf-token, company-id, x-launcher-type.
        The XSRF-TOKEN cookie changes after login, so we read it fresh each time.
        """
        if not self._session:
            raise Exception("No session available")
        
        xsrf_token = self._session.cookies.get("XSRF-TOKEN")
        if not xsrf_token:
            raise Exception("XSRF-TOKEN cookie not found")
        
        return {
            "Accept": "application/json",
            "Origin": self.PANEL_ORIGIN,
            "Referer": f"{self.PANEL_ORIGIN}/",
            "x-xsrf-token": xsrf_token,
            "company-id": self.COMPANY_ID,
            "x-launcher-type": "BROWSER_PANEL",
        }
    
    def _dump_json(self, name: str, data: Any) -> None:
        """Dump JSON structure with redacted values for debugging."""
        if not self.dump_raw:
            return
        
        def redact(obj: Any, path: str = "") -> Any:
            """Recursively redact sensitive values while preserving structure."""
            if isinstance(obj, dict):
                return {k: redact(v, f"{path}.{k}") for k, v in obj.items()}
            elif isinstance(obj, list):
                return [redact(item, f"{path}[{i}]") for i, item in enumerate(obj)]
            elif isinstance(obj, str):
                return f"<str len={len(obj)}>"
            elif isinstance(obj, (int, float)):
                return f"<{type(obj).__name__}>"
            elif isinstance(obj, bool):
                return f"<bool>"
            elif obj is None:
                return None
            else:
                return f"<{type(obj).__name__}>"
        
        redacted = redact(data)
        logger.info("RAW JSON [%s]:\n%s", name, json.dumps(redacted, indent=2))

    async def get_active_order_ids(self) -> list[int]:
        """Fetch active order IDs.
        
        From browser traffic: GET /api/company/customer/order/active-ids
        """
        if not self._authenticated or not self._session:
            raise Exception("Not authenticated - call authenticate() first")

        url = f"{self.BASE_URL}/company/customer/order/active-ids"
        logger.debug("Fetching active order IDs from %s", url)

        resp = await self._session.get(url, headers=self._get_headers())

        if resp.status_code != 200:
            raise Exception(f"Failed to fetch active order IDs: HTTP {resp.status_code}")

        data = resp.json()
        
        if self.dump_raw:
            self._dump_json("active_order_ids", data)
        
        # Parse defensively - could be a list of IDs or objects
        if isinstance(data, list):
            if data and isinstance(data[0], int):
                order_ids = data
            elif data and isinstance(data[0], dict):
                order_ids = [item.get("id") or item.get("orderId") for item in data]
            else:
                order_ids = []
        else:
            order_ids = []
            
        logger.info("Found %d active order IDs", len(order_ids))
        return order_ids

    async def get_order_deliveries(self, order_id: int) -> list[dict[str, Any]]:
        """Fetch order with its deliveries.
        
        From browser traffic: GET /api/company/customer/order/{orderId}
        """
        if not self._authenticated or not self._session:
            raise Exception("Not authenticated")

        url = f"{self.BASE_URL}/company/customer/order/{order_id}"
        logger.debug("Fetching order %d with deliveries", order_id)

        resp = await self._session.get(url, headers=self._get_headers())

        if resp.status_code != 200:
            raise Exception(
                f"Failed to fetch order {order_id}: HTTP {resp.status_code}"
            )

        data = resp.json()
        
        if self.dump_raw:
            self._dump_json(f"order_{order_id}", data)
        
        # Parse defensively - deliveries might be nested
        deliveries = data.get("deliveries", [])
        logger.debug("Order %d has %d deliveries", order_id, len(deliveries))
        return deliveries

    async def get_delivery_meals(
        self, delivery_id: int
    ) -> list[dict[str, Any]]:
        """Fetch meal details with nutrition for a delivery.
        
        From browser traffic: GET /api/company/general/menus/delivery/{deliveryId}/new
        """
        if not self._authenticated or not self._session:
            raise Exception("Not authenticated")

        url = f"{self.BASE_URL}/company/general/menus/delivery/{delivery_id}/new"
        logger.debug("Fetching meals for delivery %d", delivery_id)

        resp = await self._session.get(url, headers=self._get_headers())

        if resp.status_code != 200:
            raise Exception(
                f"Failed to fetch delivery {delivery_id} meals: HTTP {resp.status_code}"
            )

        data = resp.json()
        
        if self.dump_raw:
            self._dump_json(f"delivery_{delivery_id}_meals", data)
        
        # Parse defensively - check multiple possible keys
        meals = (
            data.get("deliveryMenuMeal") or
            data.get("meals") or
            data.get("deliveryMeals") or
            []
        )
        
        if not meals and isinstance(data, list):
            meals = data
            
        logger.debug("Delivery %d has %d meals", delivery_id, len(meals))
        return meals

    async def get_delivered_meals(
        self, start_date: date, end_date: date
    ) -> list[DeliveredMeal]:
        """Fetch all delivered meals in the date range.
        
        Uses Europe/Warsaw timezone to determine "today" for delivery filtering.
        Skips future deliveries and deleted items.
        """
        # Get active order IDs first
        order_ids = await self.get_active_order_ids()
        delivered_meals: list[DeliveredMeal] = []
        
        # Get "today" in Europe/Warsaw timezone
        from datetime import datetime as dt
        from zoneinfo import ZoneInfo
        warsaw_tz = ZoneInfo("Europe/Warsaw")
        today_warsaw = dt.now(warsaw_tz).date()

        for order_id in order_ids:
            logger.debug("Processing order %d", order_id)

            deliveries = await self.get_order_deliveries(order_id)

            for delivery in deliveries:
                # Skip deleted deliveries
                if delivery.get("deleted"):
                    logger.debug("Skipping deleted delivery %s", delivery.get("deliveryId"))
                    continue
                
                delivery_date_str = delivery.get("date")
                if not delivery_date_str:
                    continue

                delivery_date = datetime.strptime(delivery_date_str, "%Y-%m-%d").date()

                # Filter by date range
                if not (start_date <= delivery_date <= end_date):
                    continue

                # Skip future deliveries only (today is OK)
                if delivery_date > today_warsaw:
                    logger.debug(
                        "Skipping future delivery %s (today is %s in Europe/Warsaw)",
                        delivery_date,
                        today_warsaw,
                    )
                    continue

                delivery_id = delivery["deliveryId"]
                meals = await self.get_delivery_meals(delivery_id)

                meal_count = 0
                for meal in meals:
                    # Skip deleted meals
                    if meal.get("deleted"):
                        logger.debug("Skipping deleted meal %s", meal.get("deliveryMealId"))
                        continue
                    
                    # Extract nutrition info
                    nutrition_data = meal.get("nutrition", {})
                    
                    # Try multiple field names for carbohydrates (API may use different names)
                    carbs = (
                        self._safe_float(nutrition_data.get("carbohydrates")) or
                        self._safe_float(nutrition_data.get("carbs")) or
                        self._safe_float(nutrition_data.get("wegle"))  # Polish for carbs
                    )
                    
                    delivered_meal = DeliveredMeal(
                        delivery_date=delivery_date,
                        meal_name=meal.get("mealName", "Unknown"),
                        dish_name=meal.get("menuMealName", "Unknown dish"),
                        nutrition=NutritionInfo(
                            calories=float(nutrition_data.get("calories", 0) or 0),
                            protein=self._safe_float(nutrition_data.get("protein")),
                            carbs=carbs,
                            fat=self._safe_float(nutrition_data.get("fat")),
                        ),
                        delivery_id=delivery_id,
                        meal_id=meal.get("deliveryMealId", 0),
                    )
                    delivered_meals.append(delivered_meal)
                    meal_count += 1

                logger.info(
                    "Delivery %s: found %d meals", delivery_date, meal_count
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
