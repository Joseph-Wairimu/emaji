"""
HTTP/JSON card-swipe water terminal protocol implementation.

The terminal initiates all requests; this server is the HTTP server.
All four endpoints are under /hxz/v1/ and use plain Django HttpResponse
(not DRF) because the protocol is strict about output format:
- No trailing newlines after the closing brace
- Content-Type: application/json; charset=gb2312
- Responses must be valid JSON only — no extra characters

Heartbeat cadence: device calls ServerTime every 30 seconds.
Transaction cadence (two calls per tap):
  1. Mode=1 (balance inquiry) — server returns balance + tariff config
  2. Mode=0 (deduction report) — terminal reports consumed pulses; server records deduction
     Water is already dispensed by the time Mode=0 arrives.
"""
import json
import logging
import uuid
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import (
    CardBinding,
    CardTerminalDevice,
    CardTerminalTariff,
    CardTerminalTransaction,
    CardTerminalWhitelistEntry,
    Customer,
    PaymentLog,
    PrepaidWallet,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _protocol_response(data: dict, http_status: int = 200) -> HttpResponse:
    """
    Serialize to JSON with no trailing whitespace — the terminal rejects
    responses with characters after the closing brace.
    """
    body = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    return HttpResponse(body, status=http_status, content_type='application/json; charset=gb2312')


def _parse_body(request) -> dict:
    if not request.body:
        return {}
    try:
        raw = request.body.decode('gb2312')
    except UnicodeDecodeError:
        raw = request.body.decode('utf-8', errors='ignore')
    return json.loads(raw)


def _current_terminal_time() -> str:
    """Return yyyyMMddHHmmssd where d = day of week (Mon=1, Sun=0)."""
    now = timezone.localtime()
    weekday = now.weekday() + 1   # Python Mon=0 → Mon=1
    if weekday == 7:
        weekday = 0               # Python Sun=6 → Sun=0
    return now.strftime('%Y%m%d%H%M%S') + str(weekday)


def _upsert_device(request) -> CardTerminalDevice | None:
    device_number = (
        request.headers.get('Device-ID')
        or request.headers.get('Device_ID')
        or request.META.get('HTTP_DEVICE_ID', '')
    ).strip()
    if not device_number:
        return None
    iccid = request.headers.get('ICCID', '')
    device, _ = CardTerminalDevice.objects.get_or_create(
        device_number=device_number,
        defaults={'name': f'Device {device_number}', 'iccid': iccid},
    )
    device.last_seen_at = timezone.now()
    if iccid:
        device.iccid = iccid
    device.save(update_fields=['last_seen_at', 'iccid'])
    return device


def _get_active_tariff() -> CardTerminalTariff | None:
    return CardTerminalTariff.objects.filter(is_active=True).first()


def _upsert_whitelist(card_no: str, operation: int) -> None:
    CardTerminalWhitelistEntry.objects.update_or_create(
        card_no=card_no,
        defaults={'operation': operation},
    )


def _build_success_response(wallet: PrepaidWallet, tariff: CardTerminalTariff,
                             customer: Customer, amount_kes: Decimal) -> dict:
    return {
        'Status': 1,
        'Msg': '',
        'Name': f'{customer.first_name} {customer.last_name}'[:20],
        'CardNo': wallet.customer.card_binding.card_no,
        'Money': f'{wallet.balance_kes:.2f}',
        'Subsidy': '0.00',
        'ConMode': 0,
        'ChargeMode': tariff.charge_mode,
        'Pulses': tariff.pulses_per_m3,
        'Rate': float(tariff.rate_per_m3),
        'Timeflow': 1,
        'Amount': f'{amount_kes:.2f}',
        'Text': f'Used\r\n{amount_kes:.2f}\r\nBal\r\n{wallet.balance_kes:.2f}',
        'ThermalControl': 0,
    }


# ---------------------------------------------------------------------------
# Protocol views
# ---------------------------------------------------------------------------

@csrf_exempt
def server_time(request):
    """
    POST /hxz/v1/ServerTime
    Called every 30 seconds as a heartbeat. Must respond within 3 seconds.
    Returns server time, offline spending limit, and whitelist update flag.
    """
    if request.method != 'POST':
        return _protocol_response({'Status': 0, 'Msg': 'Bad Method'}, 405)

    _upsert_device(request)

    try:
        body = _parse_body(request)
    except Exception:
        body = {}

    wl_sum = int(body.get('WLSum', 0))
    current_wl_count = CardTerminalWhitelistEntry.objects.count()

    tariff = _get_active_tariff()
    off_amount = float(tariff.offline_limit_kes) if tariff else 10.0
    wl_update = 1 if current_wl_count != wl_sum else 0
    wl_page = 1 if wl_update else 0

    return _protocol_response({
        'Status': 1,
        'Msg': '',
        'Time': _current_terminal_time(),
        'OffAmount': off_amount,
        'WLUptate': wl_update,
        'WLPage': wl_page,
    })


@csrf_exempt
def consum_transactions(request):
    """
    POST /hxz/v1/Water/ConsumTransactions

    TWO-CALL sequence per customer tap:
      Call 1 — Mode=1 (balance inquiry): return balance + tariff config, no deduction.
               If balance=0 return Status=0 so device does not open valve.
      Call 2 — Mode=0 (deduction report): water already dispensed. Record consumption.
               Always return Status=1; allow balance to go negative.

    Must respond within 3 seconds or terminal falls back to offline mode.
    """
    if request.method != 'POST':
        return _protocol_response({'Status': 0, 'Msg': 'Bad Method'}, 405)

    device = _upsert_device(request)

    try:
        body = _parse_body(request)
    except Exception:
        return _protocol_response({'Status': 0, 'Msg': 'Bad JSON'})

    order_no = str(body.get('Order', '')).strip()
    card_no = str(body.get('CardNo', '')).strip()
    try:
        mode = int(body.get('Mode', 0))
    except (ValueError, TypeError):
        mode = 0
    try:
        amount_sent = Decimal(str(body.get('Amount', '0.00')).strip() or '0.00')
    except InvalidOperation:
        amount_sent = Decimal('0.00')
    try:
        cons_water_volf = int(body.get('ConsWaterVolf', 0))
    except (ValueError, TypeError):
        cons_water_volf = 0

    if not order_no or not card_no:
        return _protocol_response({'Status': 0, 'Msg': 'Missing'})

    tariff = _get_active_tariff()
    if not tariff:
        return _protocol_response({'Status': 0, 'Msg': 'No Tariff'})

    try:
        binding = CardBinding.objects.select_related('customer').get(card_no=card_no, is_active=True)
    except CardBinding.DoesNotExist:
        return _protocol_response({'Status': 0, 'Msg': 'No Card'})

    customer = binding.customer
    wallet, _ = PrepaidWallet.objects.get_or_create(
        customer=customer,
        defaults={'balance_m3': Decimal('0'), 'last_known_flow_m3': Decimal('0'), 'valve_status': 'unknown'},
    )

    # ── Mode=1: Balance inquiry ──────────────────────────────────────────────
    if mode == 1:
        if wallet.balance_kes <= Decimal('0'):
            return _protocol_response({'Status': 0, 'Msg': 'No Money'})

        CardTerminalTransaction.objects.get_or_create(
            order_no=order_no,
            defaults={
                'device': device,
                'card_no': card_no,
                'customer': customer,
                'mode': 1,
                'source': 'online',
                'amount_deducted_kes': Decimal('0.00'),
                'volume_consumed_units': cons_water_volf,
                'balance_after_kes': wallet.balance_kes,
                'is_successful': True,
                'failure_reason': '',
            },
        )

        return _protocol_response({
            'Status': 1,
            'Msg': '',
            'Name': f'{customer.first_name} {customer.last_name}'[:20],
            'CardNo': card_no,
            'Money': f'{wallet.balance_kes:.2f}',
            'Subsidy': '0.00',
            'ConMode': 0,
            'ChargeMode': tariff.charge_mode,
            'Pulses': tariff.pulses_per_m3,
            'Rate': float(tariff.rate_per_m3),
            'Timeflow': 1,
            'Amount': '0.00',
            'Text': f'Balance\r\n{wallet.balance_kes:.2f}',
            'ThermalControl': 0,
        })

    # ── Mode=0: Deduction report — water already dispensed ───────────────────
    if mode == 0:
        with transaction.atomic():
            # Idempotency: if this order was already processed, return cached result
            existing = CardTerminalTransaction.objects.filter(order_no=order_no).first()
            if existing:
                wallet_now = PrepaidWallet.objects.get(customer=customer)
                return _protocol_response({
                    'Status': 1,
                    'Msg': '',
                    'Name': f'{customer.first_name} {customer.last_name}'[:20],
                    'CardNo': card_no,
                    'Money': f'{wallet_now.balance_kes:.2f}',
                    'Subsidy': '0.00',
                    'ConMode': 0,
                    'ChargeMode': tariff.charge_mode,
                    'Pulses': tariff.pulses_per_m3,
                    'Rate': float(tariff.rate_per_m3),
                    'Timeflow': 1,
                    'Amount': f'{existing.amount_deducted_kes:.2f}',
                    'Text': f'Used\r\n{existing.amount_deducted_kes:.2f}\r\nBal\r\n{wallet_now.balance_kes:.2f}',
                    'ThermalControl': 0,
                })

            wallet = PrepaidWallet.objects.select_for_update().get(customer=customer)

            if cons_water_volf > 0:
                amount_kes = (Decimal(cons_water_volf) / Decimal(tariff.pulses_per_m3)) * tariff.rate_per_m3
            elif amount_sent > Decimal('0'):
                amount_kes = amount_sent
            else:
                amount_kes = Decimal('0.00')

            # Water is already dispensed — deduct regardless of balance (allow negative)
            wallet.balance_kes -= amount_kes
            wallet.save(update_fields=['balance_kes', 'updated_at'])

            CardTerminalTransaction.objects.create(
                order_no=order_no,
                device=device,
                card_no=card_no,
                customer=customer,
                mode=0,
                source='online',
                amount_deducted_kes=amount_kes,
                volume_consumed_units=cons_water_volf,
                balance_after_kes=wallet.balance_kes,
                is_successful=True,
            )

            PaymentLog.objects.create(
                customer=customer,
                billing_type='PREPAID',
                amount_paid=amount_kes,
                payment_method='Card Terminal',
                transaction_reference=f'CT-{order_no[:40]}',
            )

            # Remove from offline whitelist if balance exhausted
            if wallet.balance_kes <= Decimal('0'):
                _upsert_whitelist(card_no, 0)

        return _protocol_response({
            'Status': 1,
            'Msg': '',
            'Name': f'{customer.first_name} {customer.last_name}'[:20],
            'CardNo': card_no,
            'Money': f'{wallet.balance_kes:.2f}',
            'Subsidy': '0.00',
            'ConMode': 0,
            'ChargeMode': tariff.charge_mode,
            'Pulses': tariff.pulses_per_m3,
            'Rate': float(tariff.rate_per_m3),
            'Timeflow': 1,
            'Amount': f'{amount_kes:.2f}',
            'Text': f'Used\r\n{amount_kes:.2f}\r\nBal\r\n{wallet.balance_kes:.2f}',
            'ThermalControl': 0,
        })

    return _protocol_response({'Status': 0, 'Msg': 'Bad Mode'})


@csrf_exempt
def offline_transactions(request):
    """
    POST /hxz/v1/Water/OffLines
    Terminal uploads stored-offline transactions after reconnecting.
    Idempotent on Order field — duplicate uploads are silently accepted.
    """
    if request.method != 'POST':
        return _protocol_response({'Status': 0, 'Msg': 'Bad Method'}, 405)

    device = _upsert_device(request)

    try:
        body = _parse_body(request)
    except Exception:
        return _protocol_response({'Status': 0, 'Msg': 'Bad JSON'})

    order_no = str(body.get('Order', '')).strip()
    card_no = str(body.get('CardNo', '')).strip()
    try:
        money = Decimal(str(body.get('Money', '0.00')).strip() or '0.00')
    except InvalidOperation:
        money = Decimal('0.00')
    try:
        cons_water_volf = int(body.get('ConsWaterVolf', 0))
    except (ValueError, TypeError):
        cons_water_volf = 0

    if not order_no or not card_no:
        return _protocol_response({'Status': 0, 'Msg': 'Missing'})

    with transaction.atomic():
        # Idempotency: already processed offline order
        if CardTerminalTransaction.objects.filter(order_no=order_no).exists():
            return _protocol_response({'Status': 1, 'Msg': '', 'Order': order_no})

        try:
            binding = CardBinding.objects.select_related('customer').get(card_no=card_no)
        except CardBinding.DoesNotExist:
            return _protocol_response({'Status': 0, 'Msg': 'No Card'})

        customer = binding.customer
        wallet = PrepaidWallet.objects.select_for_update().get_or_create(
            customer=customer,
            defaults={'balance_m3': Decimal('0'), 'last_known_flow_m3': Decimal('0'), 'valve_status': 'unknown'},
        )[0]

        # Terminal pre-authorized this offline spend — deduct; allow going negative
        wallet.balance_kes -= money
        wallet.save(update_fields=['balance_kes', 'updated_at'])

        CardTerminalTransaction.objects.create(
            order_no=order_no,
            device=device,
            card_no=card_no,
            customer=customer,
            mode=0,
            source='offline',
            amount_deducted_kes=money,
            volume_consumed_units=cons_water_volf,
            balance_after_kes=wallet.balance_kes,
            is_successful=True,
        )

        PaymentLog.objects.create(
            customer=customer,
            billing_type='PREPAID',
            amount_paid=money,
            payment_method='Card Terminal (Offline)',
            transaction_reference=f'CT-OFF-{order_no[:36]}',
        )

        if wallet.balance_kes <= Decimal('0'):
            _upsert_whitelist(card_no, 0)

    return _protocol_response({'Status': 1, 'Msg': '', 'Order': order_no})


@csrf_exempt
def whitelist(request):
    """
    POST /hxz/v1/Water/WhiteList
    Terminal downloads its offline-allowed card list paginated.
    WLData format: "seq|card_no|operation,seq|card_no|operation,..."
    operation 1=allow offline, 0=remove from offline list.
    """
    if request.method != 'POST':
        return _protocol_response({'Status': 0, 'Msg': 'Bad Method'}, 405)

    _upsert_device(request)

    try:
        body = _parse_body(request)
    except Exception:
        return _protocol_response({'Status': 0, 'Msg': 'Bad JSON'})

    comm_id = int(body.get('CommID', 0))
    page = max(1, int(body.get('Page', 1)))
    page_number = max(1, int(body.get('PageNumber', 10)))

    start = (page - 1) * page_number
    end = start + page_number

    records = list(CardTerminalWhitelistEntry.objects.order_by('id')[start:end])
    total = CardTerminalWhitelistEntry.objects.count()
    has_more = end < total

    wl_items = [
        f'{start + i + 1}|{rec.card_no}|{rec.operation}'
        for i, rec in enumerate(records)
    ]

    return _protocol_response({
        'Status': 1,
        'Msg': '',
        'CommID': comm_id,
        'Page': page,
        'PageLen': len(wl_items),
        'Uptate': 1 if has_more else 0,
        'WLData': ','.join(wl_items),
    })
