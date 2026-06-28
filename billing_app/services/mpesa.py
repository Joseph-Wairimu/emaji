"""
M-Pesa Daraja API service — Lipa na M-Pesa Online (STK Push).

Credentials are read from Django settings:
  MPESA_CONSUMER_KEY, MPESA_CONSUMER_SECRET, MPESA_SHORTCODE,
  MPESA_PASSKEY, MPESA_CALLBACK_URL, MPESA_ENV ('sandbox' | 'production')
"""
import base64
import logging
from datetime import datetime
from decimal import Decimal

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

class MpesaService:
    def __init__(self):
        self.base_url = getattr(settings, 'MPESA_BASE_URL', 'https://sandbox.safaricom.co.ke')
        self.consumer_key = getattr(settings, 'MPESA_CONSUMER_KEY', '')
        self.consumer_secret = getattr(settings, 'MPESA_CONSUMER_SECRET', '')
        self.shortcode = getattr(settings, 'MPESA_SHORTCODE', '')
        self.passkey = getattr(settings, 'MPESA_PASSKEY', '')
        self.callback_url = getattr(settings, 'MPESA_CALLBACK_URL', '')

    def _access_token(self) -> str:
        url = f'{self.base_url}/oauth/v1/generate?grant_type=client_credentials'
        creds = base64.b64encode(f'{self.consumer_key}:{self.consumer_secret}'.encode()).decode()
        resp = requests.get(url, headers={'Authorization': f'Basic {creds}'}, timeout=15)
        resp.raise_for_status()
        return resp.json()['access_token']

    def _timestamp(self) -> str:
        return datetime.now().strftime('%Y%m%d%H%M%S')

    def _password(self, timestamp: str) -> str:
        raw = f'{self.shortcode}{self.passkey}{timestamp}'
        return base64.b64encode(raw.encode()).decode()

    def initiate_stk_push(
        self,
        phone: str,
        amount: Decimal,
        account_ref: str,
        description: str = 'E-Maji Water Top-Up',
    ) -> dict:
        """
        Initiate an STK push to `phone` for `amount` KES.
        Returns the raw Daraja response dict.
        Raises on HTTP error or missing credentials.
        """
        if not self.consumer_key or not self.shortcode or not self.passkey:
            raise ValueError('M-Pesa credentials not configured (MPESA_CONSUMER_KEY / MPESA_SHORTCODE / MPESA_PASSKEY)')

        token = self._access_token()
        ts = self._timestamp()
        payload = {
            'BusinessShortCode': self.shortcode,
            'Password': self._password(ts),
            'Timestamp': ts,
            'TransactionType': 'CustomerPayBillOnline',
            'Amount': int(amount),
            'PartyA': phone,
            'PartyB': self.shortcode,
            'PhoneNumber': phone,
            'CallBackURL': self.callback_url,
            'AccountReference': account_ref[:12],
            'TransactionDesc': description[:13],
        }
        url = f'{self.base_url}/mpesa/stkpush/v1/processrequest'
        resp = requests.post(
            url, json=payload,
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def query_stk_push(self, checkout_request_id: str) -> dict:
        """Query the status of a pending STK push."""
        token = self._access_token()
        ts = self._timestamp()
        payload = {
            'BusinessShortCode': self.shortcode,
            'Password': self._password(ts),
            'Timestamp': ts,
            'CheckoutRequestID': checkout_request_id,
        }
        url = f'{self.base_url}/mpesa/stkpushquery/v1/query'
        resp = requests.post(
            url, json=payload,
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
