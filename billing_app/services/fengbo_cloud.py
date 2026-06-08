import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class FengboCloudService:
    """
    Client for the Fengbo Cloud water meter management API.
    Credentials are configured via FENGBO_API_URL, FENGBO_AREA_NAME,
    FENGBO_CHECK_CODE in settings (read from .env).
    """

    def __init__(self):
        self.base_url = settings.FENGBO_API_URL.rstrip("/")
        self.area_name = settings.FENGBO_AREA_NAME
        self.check_code = settings.FENGBO_CHECK_CODE

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def _get(self, path: str, params: dict) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def control_valve(self, meter_id: str, action: str) -> dict:
        """
        action: 'open' (valve=0) or 'close' (valve=1)
        Fengbo API: valve 0 = open, 1 = close
        """
        valve_code = 0 if action == "open" else 1
        result = self._post("valveControl", {
            "areaname": self.area_name,
            "data": [meter_id],
            "valve": valve_code,
            "checkCode": self.check_code,
        })
        logger.info(f"Valve {action} for {meter_id}: {result}")
        return result

    def get_readings(self, meter_ids: list) -> dict:
        """Fetch latest readings from Fengbo Cloud for given meter IDs."""
        return self._post("readData", {
            "areaname": self.area_name,
            "data": meter_ids,
            "checkCode": self.check_code,
        })

    def get_readings_by_region(self, page: int = 1, page_count: int = 100) -> dict:
        """Fetch all meter readings for the configured region (paginated)."""
        return self._get("readData", {
            "pageindex": page,
            "pagecount": page_count,
            "areaname": self.area_name,
            "checkCode": self.check_code,
        })

    def get_user_info(self, meter_ids: list) -> dict:
        """Get user/account info for given meter IDs."""
        return self._post("getUserInfo", {
            "areaname": self.area_name,
            "data": meter_ids,
            "checkCode": self.check_code,
        })

    def get_water_usage(self, meter_ids: list, start_date: str, end_date: str, month: str) -> dict:
        """Get consumption history for given meters in a date range."""
        return self._post("getUserWaterInfo", {
            "areaname": self.area_name,
            "data": meter_ids,
            "startDate": start_date,
            "endDate": end_date,
            "month": month,
            "checkCode": self.check_code,
        })

    def record_payment(self, meter_id: str, amount: float, method: str = "Cash recharge") -> dict:
        """Sync a payment to Fengbo's platform."""
        return self._post("pay", {
            "areaname": self.area_name,
            "checkCode": self.check_code,
            "meterId": meter_id,
            "rechargeMoney": round(amount, 2),
            "rechargeWay": method,
        })
