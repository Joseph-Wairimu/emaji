"""
Merchant Transactions API — M-Pesa STK Push (C2B) collections.

Credentials are read from Django settings (see .env):
  MERCHANT_API_BASE_URL, MERCHANT_API_CLIENT_ID, MERCHANT_API_CLIENT_SECRET,
  MERCHANT_API_WEBHOOK_SECRET
"""
import base64
import hashlib
import hmac
import logging
import time
from decimal import Decimal

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Access tokens are valid for ~1 hour; cache and reuse across requests/instances.
_token_cache = {'access_token': None, 'expires_at': 0.0}


class MerchantApiService:
    def __init__(self):
        self.base_url = getattr(settings, 'MERCHANT_API_BASE_URL', '').rstrip('/')
        self.client_id = getattr(settings, 'MERCHANT_API_CLIENT_ID', '')
        self.client_secret = getattr(settings, 'MERCHANT_API_CLIENT_SECRET', '')

    def _access_token(self) -> str:
        now = time.time()
        if _token_cache['access_token'] and now < _token_cache['expires_at']:
            return _token_cache['access_token']

        creds = base64.b64encode(f'{self.client_id}:{self.client_secret}'.encode()).decode()
        resp = requests.post(
            f'{self.base_url}/auth/token',
            json={'grant_type': 'client_credentials'},
            headers={'Authorization': f'Basic {creds}', 'Content-Type': 'application/json'},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data['access_token']
        expires_in = int(data.get('expires_in', 3599))
        _token_cache['access_token'] = token
        _token_cache['expires_at'] = now + expires_in - 60  # refresh a minute early
        return token

    def _headers(self) -> dict:
        return {
            'Authorization': f'Bearer {self._access_token()}',
            'Content-Type': 'application/json',
        }

    def initiate_stk_push(
        self,
        phone: str,
        amount: Decimal,
        account_reference: str,
        transaction_desc: str = 'E-Maji Water Top-Up',
        external_id: str = '',
    ) -> dict:
        """
        Initiate an M-Pesa STK push (C2B) to `phone` for `amount` KES.
        Returns the `transaction` object from the response — result is delivered
        asynchronously via webhook; treat this as an acknowledgement only.
        Raises ValueError if credentials are missing, or on HTTP error.
        """
        if not self.base_url or not self.client_id or not self.client_secret:
            raise ValueError(
                'Merchant API credentials not configured '
                '(MERCHANT_API_BASE_URL / MERCHANT_API_CLIENT_ID / MERCHANT_API_CLIENT_SECRET)'
            )

        payload = {
            'phoneNumber': phone,
            'amount': int(amount),
            'accountReference': account_reference[:20],
            'transactionDesc': transaction_desc,
        }
        if external_id:
            payload['externalId'] = external_id[:20]

        resp = requests.post(
            f'{self.base_url}/transactions/m-pesa/c2b/initiate',
            json=payload,
            headers=self._headers(),
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()['transaction']

    def get_transaction(self, transaction_id: str) -> dict:
        """Fetch the current state of a transaction by id or externalId."""
        resp = requests.get(
            f'{self.base_url}/transactions/{transaction_id}',
            headers=self._headers(),
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()


def verify_webhook_signature(raw_body: bytes, signature_header: str) -> bool:
    """
    Verify the X-Webhook-Signature header (HMAC-SHA512 of the raw body, hex-encoded,
    prefixed with 'sha512=') using MERCHANT_API_WEBHOOK_SECRET. Constant-time compare.
    """
    secret = getattr(settings, 'MERCHANT_API_WEBHOOK_SECRET', '')
    if not secret or not signature_header:
        return False

    received_hex = signature_header.removeprefix('sha512=')
    expected_hex = hmac.new(secret.encode(), raw_body, hashlib.sha512).hexdigest()
    try:
        return hmac.compare_digest(bytes.fromhex(expected_hex), bytes.fromhex(received_hex))
    except ValueError:
        return False
