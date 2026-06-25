"""
Nuomiy / IDMP merchant API client.

Base URL: https://ivmer.nuomiy.com/meropen
Auth: AES-128-ECB signed requests using Java SHA1PRNG key derivation.

Sign algorithm (per-request, generated inside _get/_post right before the call):
  1. Merge business params + appId into one dict
  2. Build sorted param string: key=value&key=value
     (exclude 'sign', null values, empty strings; sort is case-sensitive ASCII)
  3. Derive 16-byte AES key from NUOMIY_PRIVATE_KEY via Java SHA1PRNG
  4. PKCS5-pad and AES-ECB encrypt → uppercase hex
  5. Add 'sign' to the params dict; send everything as query-string params

Note: ALL Nuomiy endpoints use query-string params (in: "query") even POSTs.
The 'requestValues' field in the Swagger is a Spring Boot HttpServletRequest
injection artifact — do NOT include it in requests or in the sign.
"""
import hashlib
import logging
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

import requests
from Crypto.Cipher import AES
from django.conf import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sign generation
# ---------------------------------------------------------------------------

def _java_sha1prng_key(seed: str, key_size: int = 16) -> bytes:
    """
    Reproduces Java SecureRandom.getInstance("SHA1PRNG") used with
    KeyGenerator AES-128 — the key derivation the Nuomiy platform expects.
    """
    seed_bytes = seed.encode("utf-8")
    state = hashlib.sha1(seed_bytes).digest()

    def _update_state(s: bytes, out: bytes) -> bytes:
        arr = bytearray(s)
        carry = 1
        for i in range(len(arr)):
            val = arr[i] + out[i] + carry
            arr[i] = val & 0xFF
            carry = val >> 8
            if arr[i] != out[i]:
                carry = 0
        return bytes(arr)

    result = bytearray()
    while len(result) < key_size:
        output = hashlib.sha1(state).digest()
        state = _update_state(state, output)
        result.extend(output)

    return bytes(result[:key_size])


def _pkcs5_pad(data: bytes) -> bytes:
    pad_len = 16 - (len(data) % 16)
    return data + bytes([pad_len] * pad_len)


def _fmt_amount(value) -> str:
    """
    Consistently format a monetary amount as a 2-decimal string.
    Avoids float imprecision (e.g. str(float(200)) = '200.0' not '200.00',
    and large floats can produce scientific notation).
    """
    return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def generate_sign(params: dict, private_key: str) -> str:
    """
    Build the AES-ECB signature for a Nuomiy API request.

    params      — all request parameters (sign key excluded automatically)
    private_key — NUOMIY_PRIVATE_KEY from .env

    The sign string is built from the already-formatted values in params so
    the string signed is identical to what is sent over the wire.
    """
    signing_params = {
        k: str(v)
        for k, v in params.items()
        if k != "sign" and v is not None and str(v) != ""
    }
    content = "&".join(f"{k}={v}" for k, v in sorted(signing_params.items()))
    aes_key = _java_sha1prng_key(private_key)
    cipher = AES.new(aes_key, AES.MODE_ECB)
    encrypted = cipher.encrypt(_pkcs5_pad(content.encode("utf-8")))
    return encrypted.hex().upper()


# ---------------------------------------------------------------------------
# Service client
# ---------------------------------------------------------------------------

class NuomiyCloudService:
    """
    Client for the Nuomiy merchant API (ivmer.nuomiy.com/meropen).

    All methods raise requests.HTTPError on non-2xx responses.
    Callers that want best-effort behaviour should catch Exception.
    """

    def __init__(self):
        self.base_url = settings.NUOMIY_BASE_URL.rstrip("/")
        self.app_id = settings.NUOMIY_APP_ID
        self.private_key = settings.NUOMIY_PRIVATE_KEY

    # ── Internal helpers ────────────────────────────────────────────────────

    def _signed(self, params: dict) -> dict:
        """
        Merge appId into params, generate the sign over all params,
        and return the complete dict ready to send as query-string params.
        Sign is generated here — immediately before the HTTP call — so it
        covers exactly what will be transmitted.
        """
        full = {"appId": self.app_id, **params}
        full["sign"] = generate_sign(full, self.private_key)
        return full

    def _parse(self, resp) -> dict:
        """Parse response body; raise with a clear message if not JSON."""
        if resp.status_code == 404:
            raise requests.HTTPError(
                f"404 Not Found — check endpoint path is correct: {resp.url}",
                response=resp,
            )
        try:
            return resp.json()
        except Exception:
            raise requests.HTTPError(
                f"Non-JSON response (status={resp.status_code}): {resp.text[:200]}",
                response=resp,
            )

    def _get(self, path: str, params: dict) -> dict:
        signed = self._signed(params)
        url = f"{self.base_url}/{path.lstrip('/')}"
        logger.info("Nuomiy → GET %s  params=%s", url, [k for k in signed if k != 'sign'])
        resp = requests.get(url, params=signed, timeout=15)
        data = self._parse(resp)
        logger.info("Nuomiy ← GET %s  status=%s  code=%s", path, resp.status_code, data.get("code", "?"))
        resp.raise_for_status()
        return data

    def _post(self, path: str, params: dict) -> dict:
        # Nuomiy POST endpoints use query-string params, not a JSON body
        signed = self._signed(params)
        url = f"{self.base_url}/{path.lstrip('/')}"
        logger.info("Nuomiy → POST %s  params=%s", url, [k for k in signed if k != 'sign'])
        resp = requests.post(url, params=signed, timeout=15)
        data = self._parse(resp)
        logger.info("Nuomiy ← POST %s  status=%s  code=%s", path, resp.status_code, data.get("code", "?"))
        resp.raise_for_status()
        return data

    @staticmethod
    def _ts() -> str:
        """Timestamp in yyyyMMddHHmmss format as required by dateTime/timestamp fields."""
        return datetime.now().strftime("%Y%m%d%H%M%S")

    # ── Cardholder ──────────────────────────────────────────────────────────

    def get_cardholders(
        self,
        page_index: int = 1,
        page_size: int = 20,
        begin_time: str | None = None,
        end_time: str | None = None,
        card_status: str | None = None,
    ) -> dict:
        """
        GET /customer/getinfo — paginated cardholder list.

        begin_time / end_time: yyyy-MM-dd HH:mm
        card_status: "1"=Issued, "2"=Not Issued, "3"=Lost
        """
        params: dict = {"pageIndex": str(page_index), "pageSize": str(page_size)}
        if begin_time:
            params["beginTime"] = begin_time
        if end_time:
            params["endTime"] = end_time
        if card_status:
            params["cardStatus"] = card_status
        return self._get("customer/getinfo", params)

    def get_card_balance(
        self,
        phone_number: str | None = None,
        card_number: str | None = None,
        virtual_card_no: str | None = None,
        user_name: str | None = None,
    ) -> dict:
        """
        POST /customer/getCardBalance — fetch wallet balance.

        At least one of phone_number, card_number, or virtual_card_no is required.
        timestamp (yyyyMMddHHmmss) is generated automatically.
        """
        if not any([phone_number, card_number, virtual_card_no]):
            raise ValueError("At least one of phone_number, card_number, or virtual_card_no is required")
        params: dict = {"timestamp": self._ts()}
        if phone_number:
            params["phoneNumber"] = phone_number
        if card_number:
            params["cardNumber"] = card_number
        if virtual_card_no:
            params["virtualCardNo"] = virtual_card_no
        if user_name:
            params["userName"] = user_name
        return self._post("customer/getCardBalance", params)

    def check_card(
        self,
        phone_number: str,
        password: str,
        card_no: str | None = None,
        virtual_card_no: str | None = None,
    ) -> dict:
        """
        POST /customer/checkCard — verify cardholder payment password.

        At least one of card_no or virtual_card_no is required.
        dateTime (yyyyMMddHHmmss) is generated automatically.
        """
        if not any([card_no, virtual_card_no]):
            raise ValueError("At least one of card_no or virtual_card_no is required")
        params: dict = {
            "phoneNumber": phone_number,
            "password": password,
            "dateTime": self._ts(),
        }
        if card_no:
            params["cardNo"] = card_no
        if virtual_card_no:
            params["virtualCardNo"] = virtual_card_no
        return self._post("customer/checkCard", params)

    def register_cardholder(
        self,
        customer_name: str,
        customer_sex: int,
        card_type: int,
        dept_id: int,
        indate_period: str,
        card_number: str | None = None,
        mobile_number: str | None = None,
        cash_balance: Decimal | float | None = None,
        pay_password: str | None = None,
        **extra,
    ) -> dict:
        """
        POST /customer/regist — register a new cardholder on Nuomiy.

        Required:
          customer_name   — full name
          customer_sex    — 0=Male, 1=Female
          card_type       — Nuomiy personnel category ID (from get_card_types())
          dept_id         — Nuomiy organisation ID (from get_depts())
          indate_period   — card validity date yyyy-MM-dd

        Optional but recommended:
          card_number     — physical NFC card number
          mobile_number   — cardholder phone
          cash_balance    — prepaid opening balance (formatted as 2dp decimal string)
        """
        params: dict = {
            "customerName": customer_name,
            "customerSex": str(customer_sex),
            "cardType": str(card_type),
            "deptId": str(dept_id),
            "indatePeriod": indate_period,
        }
        if card_number:
            params["cardNumber"] = card_number
        if mobile_number:
            params["mobileNumber"] = mobile_number
        if cash_balance is not None:
            params["cashBalance"] = _fmt_amount(cash_balance)
        if pay_password:
            params["payPassword"] = pay_password
        # Any additional optional fields (customerAddress, customerBirthday, etc.)
        params.update({k: str(v) for k, v in extra.items() if v is not None and str(v) != ""})
        return self._post("customer/regist", params)

    def recharge(
        self,
        amount: Decimal | float,
        transaction_status: str,
        transaction_type: str,
        wallet_type: str,
        card_id: int | None = None,
        card_no: int | None = None,
        bank_order_no: str | None = None,
    ) -> dict:
        """
        GET /customer/addrechage — top up or deduct from a card wallet on Nuomiy.

        amount            — monetary amount, formatted as 2dp string before signing
        transaction_status — "1"=increase (top-up), "2"=decrease (deduction)
        transaction_type  — Nuomiy transaction category ID (from get_transaction_types())
        wallet_type       — Nuomiy wallet type ID
        card_id / card_no — pass at least one; card_id is the Nuomiy internal ID,
                            card_no is the physical NFC card number (int64)
        """
        if card_id is None and card_no is None:
            raise ValueError("At least one of card_id or card_no is required for recharge")
        params: dict = {
            "amount": _fmt_amount(amount),
            "transactionStatus": transaction_status,
            "transactionType": str(transaction_type),
            "walletType": str(wallet_type),
        }
        if card_id is not None:
            params["cardId"] = str(card_id)
        if card_no is not None:
            params["cardNo"] = str(card_no)
        if bank_order_no:
            params["bankOrderNo"] = bank_order_no
        return self._get("customer/addrechage", params)

    def unsubscribe_card(self, card_ids: str, card_status: str) -> dict:
        """
        POST /customer/unsubscribeCard — loss report, unreport, or cancellation.

        card_ids    — comma-separated Nuomiy card IDs (up to 100)
        card_status — "1"=Issued (re-activate), "3"=Lost, "4"=Cancelled
        dateTime (yyyyMMddHHmmss) generated automatically.
        """
        params: dict = {
            "cardIds": card_ids,
            "cardStatus": card_status,
            "dateTime": self._ts(),
        }
        return self._post("customer/unsubscribeCard", params)

    def remove_card(self, customer_ids: str, password: str) -> dict:
        """
        POST /customer/removeCard — permanently delete cardholder records.

        password must be the merchant password (encrypted per Nuomiy spec).
        """
        params: dict = {
            "customerIds": customer_ids,
            "password": password,
            "dateTime": self._ts(),
        }
        return self._post("customer/removeCard", params)

    def reissue_card(
        self,
        card_id: int,
        card_number: str,
        card_amount: Decimal | float,
        merchant_id: int,
    ) -> dict:
        """POST /customer/reissueCard — issue a replacement physical card."""
        params: dict = {
            "cardId": str(card_id),
            "cardNumber": card_number,
            "cardAmount": _fmt_amount(card_amount),
            "merchantId": str(merchant_id),
            "dateTime": self._ts(),
        }
        return self._post("customer/reissueCard", params)

    def modify_cardholder(
        self,
        customer_id: str,
        customer_name: str | None = None,
        mobile_number: str | None = None,
        customer_sex: int | None = None,
        customer_birthday: str | None = None,
        customer_address: str | None = None,
        **extra,
    ) -> dict:
        """POST /customer/updateInfo — update cardholder details on Nuomiy."""
        params: dict = {"customerId": str(customer_id)}
        if customer_name:
            params["customerName"] = customer_name
        if mobile_number:
            params["mobileNumber"] = mobile_number
        if customer_sex is not None:
            params["customerSex"] = str(customer_sex)
        if customer_birthday:
            params["customerBirthday"] = customer_birthday
        if customer_address:
            params["customerAddress"] = customer_address
        params.update({k: str(v) for k, v in extra.items() if v is not None and str(v) != ""})
        return self._post("customer/update", params)

    # ── Device management ───────────────────────────────────────────────────

    def get_devices(
        self,
        page_index: int = 1,
        page_size: int = 100,
        begin_time: str | None = None,
        end_time: str | None = None,
    ) -> dict:
        """
        GET /device/devices — paginated device list.
        beginTime / endTime are required by the API; format: yyyy-MM-dd HH:mm.
        Defaults to a 2-year window ending now when not supplied.
        """
        _fmt = "%Y-%m-%d %H:%M"
        now = datetime.now()
        params: dict = {
            "pageIndex": str(page_index),
            "pageSize": str(page_size),
            "beginTime": begin_time or (now.replace(year=now.year - 2)).strftime(_fmt),
            "endTime": end_time or now.strftime(_fmt),
        }
        return self._get("device/devices", params)

    # ── Basic settings ──────────────────────────────────────────────────────

    def get_card_types(self, page_index: int = 1, page_size: int = 100) -> dict:
        """GET /basesetting/cardtypes — personnel category list."""
        return self._get(
            "basesetting/cardtypes",
            {"pageIndex": str(page_index), "pageSize": str(page_size)},
        )

    def get_depts(self, page_index: int = 1, page_size: int = 100) -> dict:
        """GET /basesetting/depts — organisation list."""
        return self._get(
            "basesetting/depts",
            {"pageIndex": str(page_index), "pageSize": str(page_size)},
        )

    def get_transaction_types(self, page_index: int = 1, page_size: int = 100) -> dict:
        """GET /basesetting/transactions — increase/decrease category list."""
        return self._get(
            "basesetting/transactions",
            {"pageIndex": str(page_index), "pageSize": str(page_size)},
        )

    def get_periods(
        self, device_type: str, page_index: int = 1, page_size: int = 100
    ) -> dict:
        """GET /basesetting/periods — time period amounts for a device type."""
        return self._get(
            "basesetting/periods",
            {
                "deviceType": device_type,
                "pageIndex": str(page_index),
                "pageSize": str(page_size),
            },
        )
