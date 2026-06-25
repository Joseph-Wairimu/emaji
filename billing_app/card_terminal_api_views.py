"""
DRF API views for card terminal management (used by the frontend dashboard).
All endpoints require JWT authentication.

Nuomiy cloud sync: card binding creation, top-ups, and deactivations are
mirrored to the Nuomiy platform as best-effort background calls. Failures
are logged but never surface as errors to the caller — E-Maji's local DB
is the source of truth.
"""
import uuid
import logging
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework import serializers as drf_serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.db import transaction as db_transaction

from .models import (
    CardBinding,
    CardTerminalDevice,
    CardTerminalTariff,
    CardTerminalTransaction,
    CardTerminalWhitelistEntry,
    Customer,
    PaymentLog,
    PrepaidWallet,
    Site,
)
from .permissions import IsAdmin, IsStaff
from .views_card_terminal import _upsert_whitelist

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Nuomiy cloud sync helpers — best-effort, never raise
# ---------------------------------------------------------------------------

def _nuomiy_first_id(rows: list, id_field: str) -> int | None:
    """Extract the first available ID from a Nuomiy /basesetting list response."""
    for row in rows:
        val = row.get(id_field) or row.get('id')
        if val is not None:
            try:
                return int(val)
            except (ValueError, TypeError):
                pass
    return None


def _nuomiy_register(binding: 'CardBinding') -> None:
    """Register the cardholder on the Nuomiy platform and store the returned ID."""
    if not settings.NUOMIY_APP_ID:
        return
    try:
        from .services.nuomiy_cloud import NuomiyCloudService
        svc = NuomiyCloudService()

        # Fetch card type and dept from the platform so no env config is needed
        card_type_rows = _nuomiy_rows(svc.get_card_types())
        dept_rows = _nuomiy_rows(svc.get_depts())
        card_type = _nuomiy_first_id(card_type_rows, 'cardTypeId')
        dept_id = _nuomiy_first_id(dept_rows, 'deptId')
        if not card_type or not dept_id:
            logger.warning('Nuomiy register skipped: no card types or depts found on platform')
            return

        customer = binding.customer
        indate = (date.today() + timedelta(days=5 * 365)).strftime('%Y-%m-%d')
        resp = svc.register_cardholder(
            customer_name=f'{customer.first_name} {customer.last_name}',
            customer_sex=0,
            card_type=card_type,
            dept_id=dept_id,
            indate_period=indate,
            card_number=binding.card_no,
            mobile_number=customer.phone or None,
        )
        nuomiy_id = str(resp.get('data') or resp.get('id') or '')
        if nuomiy_id:
            binding.nuomiy_customer_id = nuomiy_id
            binding.save(update_fields=['nuomiy_customer_id'])
        logger.info('Nuomiy register cardholder %s → %s', binding.card_no, resp.get('code'))
    except Exception:
        logger.exception('Nuomiy register failed for card %s', binding.card_no)


def _nuomiy_recharge(
    binding: 'CardBinding',
    amount_kes: Decimal,
    transaction_type: str | None = None,
    transaction_status: str = '1',
    wallet_type: str | None = None,
) -> None:
    """Mirror a top-up to the Nuomiy platform wallet."""
    if not settings.NUOMIY_APP_ID:
        return
    try:
        from .services.nuomiy_cloud import NuomiyCloudService
        svc = NuomiyCloudService()

        # Cash recharge = transactionCode 1, Cash wallet = walletType 1
        resolved_wallet_type = wallet_type or '1'
        transaction_type = transaction_type or '1'

        # Resolve card identifier: addrechage uses cardId (Nuomiy's internal card ID).
        # Prefer nuomiy_card_id; fall back to nuomiy_customer_id; last resort: physical cardNo.
        card_id = None
        card_no = None
        for id_field in (binding.nuomiy_card_id, binding.nuomiy_customer_id):
            if id_field:
                try:
                    card_id = int(id_field)
                    break
                except (ValueError, TypeError):
                    pass
        if card_id is None:
            try:
                card_no = int(binding.card_no)
            except (ValueError, TypeError):
                logger.warning(
                    'Nuomiy recharge skipped: card %s has no numeric identifier', binding.card_no
                )
                return

        resp = svc.recharge(
            amount=amount_kes,
            transaction_status=transaction_status,
            transaction_type=str(transaction_type),
            wallet_type=resolved_wallet_type,
            card_id=card_id,
            card_no=card_no,
        )
        logger.info('Nuomiy recharge card %s +%s → %s', binding.card_no, amount_kes, resp.get('code'))
    except Exception:
        logger.exception('Nuomiy recharge failed for card %s', binding.card_no)


def _nuomiy_set_card_status(binding: 'CardBinding', card_status: str) -> None:
    """
    Mirror a card status change to Nuomiy via unsubscribeCard.
    card_status: '1'=issued, '3'=lost, '4'=cancelled
    unsubscribeCard.cardIds expects the Nuomiy cardId (not customerId).
    """
    if not settings.NUOMIY_APP_ID:
        return
    # Prefer nuomiy_card_id (the cardId field); fall back to nuomiy_customer_id
    nuomiy_id = binding.nuomiy_card_id or binding.nuomiy_customer_id
    if not nuomiy_id:
        logger.warning('No Nuomiy card/customer ID for card %s — skipping status sync', binding.card_no)
        return
    try:
        from .services.nuomiy_cloud import NuomiyCloudService
        svc = NuomiyCloudService()
        resp = svc.unsubscribe_card(card_ids=nuomiy_id, card_status=card_status)
        logger.info('Nuomiy set card %s status %s → %s', binding.card_no, card_status, resp.get('code'))
    except Exception:
        logger.exception('Nuomiy status sync failed for card %s', binding.card_no)


def _nuomiy_reissue(binding: 'CardBinding', new_card_no: str) -> None:
    """Mirror a card reissue to Nuomiy (replace physical card number)."""
    if not settings.NUOMIY_APP_ID:
        return
    # reissueCard requires cardId (not customerId)
    nuomiy_card_id = binding.nuomiy_card_id or binding.nuomiy_customer_id
    if not nuomiy_card_id:
        logger.warning('Nuomiy reissue skipped: no card ID stored for card %s', binding.card_no)
        return
    try:
        from .services.nuomiy_cloud import NuomiyCloudService
        svc = NuomiyCloudService()
        merchant_id = int(settings.NUOMIY_MERCHANT_ID) if settings.NUOMIY_MERCHANT_ID else 0
        resp = svc.reissue_card(
            card_id=int(nuomiy_card_id),
            card_number=new_card_no,
            card_amount=Decimal('0.00'),
            merchant_id=merchant_id,
        )
        logger.info('Nuomiy reissue card %s → %s  code=%s', binding.card_no, new_card_no, resp.get('code'))
    except Exception:
        logger.exception('Nuomiy reissue failed for card %s', binding.card_no)


def _nuomiy_modify(binding: 'CardBinding', name: str | None, mobile: str | None) -> None:
    """Mirror cardholder detail changes to Nuomiy."""
    if not settings.NUOMIY_APP_ID or not binding.nuomiy_customer_id:
        return
    try:
        from .services.nuomiy_cloud import NuomiyCloudService
        svc = NuomiyCloudService()
        resp = svc.modify_cardholder(
            customer_id=binding.nuomiy_customer_id,
            customer_name=name,
            mobile_number=mobile,
        )
        logger.info('Nuomiy modify card %s → %s', binding.card_no, resp.get('code'))
    except Exception:
        logger.exception('Nuomiy modify failed for card %s', binding.card_no)


# ---------------------------------------------------------------------------
# Serializers (inline — small enough not to warrant a separate file)
# ---------------------------------------------------------------------------

class CardTerminalDeviceSerializer(drf_serializers.ModelSerializer):
    site_name = drf_serializers.SerializerMethodField()

    def get_site_name(self, obj):
        return obj.site.name if obj.site else None

    class Meta:
        model = CardTerminalDevice
        fields = ['id', 'device_number', 'iccid', 'name', 'site_name',
                  'is_active', 'last_seen_at', 'created_at']


class CardBindingSerializer(drf_serializers.ModelSerializer):
    customer_name = drf_serializers.SerializerMethodField()
    customer_id = drf_serializers.UUIDField()

    def get_customer_name(self, obj):
        return f'{obj.customer.first_name} {obj.customer.last_name}'

    class Meta:
        model = CardBinding
        fields = ['id', 'card_no', 'customer_id', 'customer_name', 'is_active', 'created_at']
        read_only_fields = ['id', 'customer_name', 'created_at']

    def create(self, validated_data):
        customer_id = validated_data.pop('customer_id')
        customer = get_object_or_404(Customer, id=customer_id)
        binding = CardBinding.objects.create(customer=customer, **validated_data)
        # Add to whitelist for offline access if wallet has balance
        wallet = PrepaidWallet.objects.filter(customer=customer).first()
        if wallet and wallet.balance_kes > Decimal('0'):
            _upsert_whitelist(binding.card_no, 1)
        _nuomiy_register(binding)
        return binding


def _auto_import_nuomiy_cardholder(c: dict) -> 'CardBinding | None':
    """
    Auto-create a local Customer + CardBinding + PrepaidWallet for a Nuomiy
    cardholder that has no matching local record (e.g. registered via Nuomiy portal).
    Idempotent: checks by nuomiy_card_id first to avoid duplicates on repeated calls.
    """
    card_no = str(c.get('cardNumber') or '')
    nuomiy_card_id = str(c.get('cardId', ''))
    nuomiy_customer_id = str(c.get('customerId', ''))
    if not card_no:
        return None

    # Already imported under a different card_no? (e.g. after a reissue)
    if nuomiy_card_id:
        existing = CardBinding.objects.filter(nuomiy_card_id=nuomiy_card_id).first()
        if existing:
            if existing.card_no != card_no:
                existing.card_no = card_no
                existing.save(update_fields=['card_no'])
            return existing

    site = Site.objects.order_by('id').first()
    if not site:
        logger.warning('Auto-import skipped for card %s: no Site exists in E-Maji', card_no)
        return None

    customer_name = (c.get('customerName') or 'Unknown').strip()
    parts = customer_name.split(' ', 1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else ''

    try:
        with db_transaction.atomic():
            customer = Customer.objects.create(
                first_name=first_name,
                last_name=last_name,
                phone=c.get('mobileNumber') or '',
                email=f'nuomiy_{nuomiy_card_id or card_no}@nuomiy.local',
                plot_no=c.get('customerIdentity') or card_no,
                court_name=c.get('merchantName') or '',
                usage_status='ACTIVE',
                account_status='ACTIVE',
                site=site,
                created_by=None,
            )
            cash_balance = Decimal(str(c.get('cashBalance') or '0'))
            PrepaidWallet.objects.create(
                customer=customer,
                balance_kes=cash_balance,
                balance_m3=Decimal('0'),
                last_known_flow_m3=Decimal('0'),
                valve_status='unknown',
            )
            binding = CardBinding.objects.create(
                card_no=card_no,
                customer=customer,
                is_active=(int(c.get('cardStatus', 1)) == 1),
                nuomiy_card_id=nuomiy_card_id,
                nuomiy_customer_id=nuomiy_customer_id,
            )
            # If card has balance, add to whitelist for offline access
            if cash_balance > Decimal('0'):
                _upsert_whitelist(card_no, 1)
        logger.info('Auto-imported Nuomiy card %s → local customer %s', card_no, customer.id)
        return binding
    except Exception:
        logger.exception('Failed to auto-import Nuomiy card %s', card_no)
        return None


class CardTerminalTariffSerializer(drf_serializers.ModelSerializer):
    class Meta:
        model = CardTerminalTariff
        fields = ['id', 'name', 'rate_per_m3', 'pulses_per_m3',
                  'offline_limit_kes', 'charge_mode', 'is_active']


class CardTerminalTransactionSerializer(drf_serializers.ModelSerializer):
    device_number = drf_serializers.SerializerMethodField()
    customer_name = drf_serializers.SerializerMethodField()
    mode_display = drf_serializers.SerializerMethodField()

    def get_device_number(self, obj):
        return obj.device.device_number if obj.device else None

    def get_customer_name(self, obj):
        if obj.customer:
            return f'{obj.customer.first_name} {obj.customer.last_name}'
        return None

    def get_mode_display(self, obj):
        return dict(CardTerminalTransaction.MODE_CHOICES).get(obj.mode, str(obj.mode))

    class Meta:
        model = CardTerminalTransaction
        fields = [
            'id', 'order_no', 'device_number', 'card_no', 'customer_name',
            'mode', 'mode_display', 'source', 'amount_deducted_kes',
            'volume_consumed_units', 'balance_after_kes',
            'is_successful', 'failure_reason', 'created_at',
        ]


class CardTerminalWalletSerializer(drf_serializers.ModelSerializer):
    customer_name = drf_serializers.SerializerMethodField()
    card_no = drf_serializers.SerializerMethodField()
    whitelist_status = drf_serializers.SerializerMethodField()

    def get_customer_name(self, obj):
        return f'{obj.customer.first_name} {obj.customer.last_name}'

    def get_card_no(self, obj):
        try:
            return obj.customer.card_binding.card_no
        except CardBinding.DoesNotExist:
            return None

    def get_whitelist_status(self, obj):
        try:
            card_no = obj.customer.card_binding.card_no
            entry = CardTerminalWhitelistEntry.objects.filter(card_no=card_no).first()
            if entry:
                return 'allowed' if entry.operation == 1 else 'denied'
        except CardBinding.DoesNotExist:
            pass
        return 'not_listed'

    class Meta:
        model = PrepaidWallet
        fields = ['customer_name', 'card_no', 'balance_kes', 'whitelist_status', 'updated_at']


# ---------------------------------------------------------------------------
# Nuomiy response helpers
# ---------------------------------------------------------------------------

def _nuomiy_rows(resp: dict) -> list:
    """
    Extract the list of records from a Nuomiy paginated response.
    Handles the three common Spring Boot response shapes:
      { "rows": [...] }
      { "data": { "list": [...] } }
      { "data": [...] }
    """
    if isinstance(resp.get('rows'), list):
        return resp['rows']
    data = resp.get('data')
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ('list', 'rows', 'records'):
            if isinstance(data.get(key), list):
                return data[key]
    return []



def _normalize_nuomiy_device(d: dict, local_map: dict) -> dict:
    """
    Map a Nuomiy /device/devices record to the shape CardTerminalDevicesTable expects.
    Confirmed response fields: deviceMac, deviceId, merchantName, onlineStatus, aliveTime.
    local_map: { device_number -> CardTerminalDevice } for enriching site_name / last_seen_at.
    """
    # deviceMac is the physical device identifier (e.g. "I02MPLZF07")
    device_number = str(d.get('deviceMac') or d.get('devCode') or d.get('deviceCode') or '')
    local = local_map.get(device_number)
    # onlineStatus: 1=online, 0=offline
    is_active = int(d.get('onlineStatus', 0)) == 1
    # aliveTime from Nuomiy is authoritative for last heartbeat; fall back to local
    alive_time = d.get('aliveTime') or (local.last_seen_at.isoformat() if (local and local.last_seen_at) else None)
    return {
        'id': str(d.get('deviceId') or device_number),
        'device_number': device_number,
        'name': d.get('merchantName') or device_number,
        'iccid': d.get('iccid') or d.get('simCardNo') or '',
        'site_name': local.site.name if (local and local.site) else None,
        'is_active': is_active,
        'last_seen_at': alive_time,
        'created_at': d.get('createTime') or d.get('createDate') or '',
    }


def _normalize_nuomiy_cardholder(c: dict, local_map: dict) -> dict:
    """
    Map a Nuomiy /customer/getinfo record to the shape CardBindingsTable expects.
    Confirmed response fields: cardNumber, virtualCardNo, customerName, customerId,
    cardId, cardStatus, cashBalance, mobileNumber, indatePeriod, createTime.
    local_map: { card_no -> CardBinding } for enriching local customer_id (UUID).
    """
    card_no = str(c.get('cardNumber') or c.get('cardNo') or '')
    local = local_map.get(card_no)
    nuomiy_card_id = str(c.get('cardId', ''))
    nuomiy_customer_id = str(c.get('customerId', ''))

    # Auto-import: create local Customer + CardBinding for Nuomiy-only cards
    if not local:
        local = _auto_import_nuomiy_cardholder(c)

    # Backfill cardId and customerId on the local binding so card operations use correct IDs
    if local:
        update_fields = []
        if nuomiy_card_id and local.nuomiy_card_id != nuomiy_card_id:
            local.nuomiy_card_id = nuomiy_card_id
            update_fields.append('nuomiy_card_id')
        if nuomiy_customer_id and local.nuomiy_customer_id != nuomiy_customer_id:
            local.nuomiy_customer_id = nuomiy_customer_id
            update_fields.append('nuomiy_customer_id')
        if update_fields:
            local.save(update_fields=update_fields)

    return {
        # local UUID — required by top-up and deactivate actions
        'id': str(local.id) if local else str(c.get('customerId', '')),
        'card_no': card_no,
        'virtual_card_no': c.get('virtualCardNo') or '',
        'customer_name': c.get('customerName') or '—',
        # local UUID for /card-terminal/topup/ call
        'customer_id': str(local.customer.id) if local else None,
        # Nuomiy IDs for sync operations
        'nuomiy_customer_id': nuomiy_customer_id,
        'nuomiy_card_id': nuomiy_card_id,
        # card state: 1=Issued (active), 2=Not Issued, 3=Lost, 4=Cancelled
        'is_active': int(c.get('cardStatus', 0)) == 1,
        'card_status': c.get('cardStatus'),
        # wallet
        'cash_balance': c.get('cashBalance'),
        # cardholder info
        'mobile_number': c.get('mobileNumber') or '',
        'indate_period': c.get('indatePeriod') or '',
        'merchant_name': c.get('merchantName') or '',
        'created_at': c.get('createTime') or (local.created_at.isoformat() if local else ''),
        'updated_at': c.get('updateTime') or '',
    }


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

class CardTerminalDeviceListView(APIView):
    """
    GET /api/card-terminal/devices/
    Fetches device list from the Nuomiy cloud platform and enriches each
    record with local heartbeat data (last_seen_at, site). Falls back to
    local DB only if Nuomiy is unreachable or not configured.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        local_qs = CardTerminalDevice.objects.select_related('site').order_by('-last_seen_at')
        local_map = {d.device_number: d for d in local_qs}

        if settings.NUOMIY_APP_ID:
            try:
                from .services.nuomiy_cloud import NuomiyCloudService
                resp = NuomiyCloudService().get_devices(page_size=100)
                rows = _nuomiy_rows(resp)
                if rows:
                    return Response([_normalize_nuomiy_device(d, local_map) for d in rows])
                logger.warning('Nuomiy get_devices returned empty rows — falling back to local DB')
            except Exception as e:
                logger.warning('Nuomiy get_devices unavailable (%s) — falling back to local DB', e)

        return Response(CardTerminalDeviceSerializer(local_qs, many=True).data)


class CardBindingListCreateView(APIView):
    """
    GET  /api/card-terminal/bindings/ — list card→customer bindings from Nuomiy
    POST /api/card-terminal/bindings/ — create a new binding (local + Nuomiy sync)
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        local_qs = CardBinding.objects.select_related('customer').order_by('-created_at')
        local_map = {b.card_no: b for b in local_qs}

        if settings.NUOMIY_APP_ID:
            try:
                from .services.nuomiy_cloud import NuomiyCloudService
                resp = NuomiyCloudService().get_cardholders(page_size=100)
                rows = _nuomiy_rows(resp)
                if rows:
                    return Response([_normalize_nuomiy_cardholder(c, local_map) for c in rows])
                logger.warning('Nuomiy get_cardholders returned empty rows — falling back to local DB')
            except Exception as e:
                logger.warning('Nuomiy get_cardholders unavailable (%s) — falling back to local DB', e)

        return Response(CardBindingSerializer(local_qs, many=True).data)

    def post(self, request):
        serializer = CardBindingSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CardBindingDetailView(APIView):
    """
    PATCH /api/card-terminal/bindings/<id>/
      action=activate   — unreport / re-activate card (Nuomiy status 1)
      action=deactivate — report card as lost (Nuomiy status 3)
      action=cancel     — cancel card, keep local record (Nuomiy status 4)
      action=reissue    — replace physical card; requires card_no in body
      action=modify     — edit cardholder name/mobile; optional fields in body
    DELETE /api/card-terminal/bindings/<id>/ — remove binding + cancel on Nuomiy
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def patch(self, request, binding_id):
        binding = get_object_or_404(CardBinding, id=binding_id)
        action = request.data.get('action')

        if action == 'activate':
            binding.is_active = True
            binding.save(update_fields=['is_active'])
            _upsert_whitelist(binding.card_no, 1)
            _nuomiy_set_card_status(binding, '1')

        elif action == 'deactivate':
            binding.is_active = False
            binding.save(update_fields=['is_active'])
            _upsert_whitelist(binding.card_no, 0)
            _nuomiy_set_card_status(binding, '3')

        elif action == 'cancel':
            binding.is_active = False
            binding.save(update_fields=['is_active'])
            _upsert_whitelist(binding.card_no, 0)
            _nuomiy_set_card_status(binding, '4')

        elif action == 'reissue':
            new_card_no = request.data.get('card_no', '').strip()
            if not new_card_no:
                return Response({'error': 'card_no is required for reissue'}, status=status.HTTP_400_BAD_REQUEST)
            _nuomiy_reissue(binding, new_card_no)
            old_card_no = binding.card_no
            binding.card_no = new_card_no
            binding.is_active = True
            binding.save(update_fields=['card_no', 'is_active'])
            _upsert_whitelist(old_card_no, 0)
            _upsert_whitelist(new_card_no, 1)

        elif action == 'modify':
            name = request.data.get('customer_name', '').strip() or None
            mobile = request.data.get('mobile_number', '').strip() or None
            if name:
                parts = name.split(' ', 1)
                binding.customer.first_name = parts[0]
                binding.customer.last_name = parts[1] if len(parts) > 1 else ''
                binding.customer.save(update_fields=['first_name', 'last_name'])
            if mobile:
                binding.customer.phone = mobile
                binding.customer.save(update_fields=['phone'])
            _nuomiy_modify(binding, name, mobile)

        else:
            return Response({'error': f'Unknown action: {action}'}, status=status.HTTP_400_BAD_REQUEST)

        return Response(CardBindingSerializer(binding).data)

    def delete(self, request, binding_id):
        binding = get_object_or_404(CardBinding, id=binding_id)
        _upsert_whitelist(binding.card_no, 0)
        _nuomiy_set_card_status(binding, '4')
        binding.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CardTerminalTariffListCreateView(APIView):
    """
    GET  /api/card-terminal/tariffs/ — list tariffs (any staff)
    POST /api/card-terminal/tariffs/ — create tariff (admin only)
    """
    def get_permissions(self):
        if self.request.method == 'GET':
            return [IsStaff()]
        return [IsAdmin()]

    def get(self, request):
        tariffs = CardTerminalTariff.objects.order_by('-is_active', 'name')
        return Response(CardTerminalTariffSerializer(tariffs, many=True).data)

    def post(self, request):
        serializer = CardTerminalTariffSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CardTerminalTariffDetailView(APIView):
    """PATCH /api/card-terminal/tariffs/<id>/ — update tariff."""
    permission_classes = [IsAuthenticated, IsAdmin]

    def patch(self, request, tariff_id):
        tariff = get_object_or_404(CardTerminalTariff, id=tariff_id)
        serializer = CardTerminalTariffSerializer(tariff, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CardTerminalTransactionListView(APIView):
    """
    GET /api/card-terminal/transactions/
    Supports ?card_no=, ?source=online|offline, ?limit=50
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = CardTerminalTransaction.objects.select_related('device', 'customer').order_by('-created_at')

        card_no = request.query_params.get('card_no')
        if card_no:
            qs = qs.filter(card_no=card_no)

        source = request.query_params.get('source')
        if source in ('online', 'offline'):
            qs = qs.filter(source=source)

        try:
            limit = min(int(request.query_params.get('limit', 100)), 500)
        except (ValueError, TypeError):
            limit = 100

        return Response(CardTerminalTransactionSerializer(qs[:limit], many=True).data)


class CardTerminalWalletView(APIView):
    """GET /api/card-terminal/wallet/<customer_id>/ — card terminal balance for a customer."""
    permission_classes = [IsAuthenticated]

    def get(self, request, customer_id):
        customer = get_object_or_404(Customer, id=customer_id)
        wallet = PrepaidWallet.objects.filter(customer=customer).first()
        if not wallet:
            return Response({'balance_kes': '0.00', 'card_no': None, 'whitelist_status': 'not_listed'})
        return Response(CardTerminalWalletSerializer(wallet).data)


class CardTerminalBasesettingsView(APIView):
    """
    GET /api/card-terminal/basesettings/
    Returns transaction types and wallet types from the Nuomiy platform,
    so the frontend can populate dropdowns in the top-up sheet.
    Falls back to empty lists if Nuomiy is unavailable.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not settings.NUOMIY_APP_ID:
            return Response({'transaction_types': [], 'wallet_types': []})
        try:
            from .services.nuomiy_cloud import NuomiyCloudService
            svc = NuomiyCloudService()
            tx_resp = svc.get_transaction_types()
            tx_rows = _nuomiy_rows(tx_resp)
            if tx_rows:
                logger.info('Nuomiy transaction_types first row keys: %s', list(tx_rows[0].keys()))
            # id=transactionCode because addrechage expects transactionCode (1,2,3…) not transactionId
            transaction_types = [
                {
                    'id': str(r['transactionCode']),
                    'name': r.get('transactionModeName') or str(r['transactionCode']),
                    'direction': r.get('transactionStatus'),
                    'wallet_type': str(r.get('transactionWalletType') or '1'),
                }
                for r in tx_rows
                if r.get('transactionCode')
            ]
        except Exception as e:
            logger.warning('Nuomiy get_transaction_types failed: %s', e)
            transaction_types = []

        # Nuomiy does not expose a dedicated walletType endpoint;
        # wallet type 1 = cash wallet (standard for all card terminals).
        wallet_types = [{'id': '1', 'name': 'Cash Wallet'}]

        return Response({'transaction_types': transaction_types, 'wallet_types': wallet_types})


class CardTerminalTopupView(APIView):
    """
    POST /api/card-terminal/topup/
    Body: { "customer_id": "<uuid>", "amount_kes": <number>,
            "transaction_type": "<id>", "transaction_status": "1"|"2" }
    Credits balance_kes, adds card to whitelist, records PaymentLog.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        customer_id = request.data.get('customer_id')
        amount_raw = request.data.get('amount_kes')
        transaction_type = str(request.data.get('transaction_type', '')).strip() or None
        transaction_status = str(request.data.get('transaction_status', '1')).strip()
        wallet_type = str(request.data.get('wallet_type', '')).strip() or None

        if not customer_id or amount_raw is None:
            return Response(
                {'error': 'customer_id and amount_kes are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            amount_kes = Decimal(str(amount_raw))
            if amount_kes <= 0:
                raise ValueError
        except (ValueError, Exception):
            return Response({'error': 'amount_kes must be a positive number'}, status=status.HTTP_400_BAD_REQUEST)

        customer = get_object_or_404(Customer, id=customer_id)

        wallet, _ = PrepaidWallet.objects.get_or_create(
            customer=customer,
            defaults={'balance_m3': Decimal('0'), 'last_known_flow_m3': Decimal('0'), 'valve_status': 'unknown'},
        )
        wallet.balance_kes += amount_kes
        wallet.save(update_fields=['balance_kes', 'updated_at'])

        # Enable offline access and mirror recharge to Nuomiy
        binding = None
        card_no = None
        try:
            binding = customer.card_binding
            card_no = binding.card_no
            _upsert_whitelist(card_no, 1)
        except CardBinding.DoesNotExist:
            pass

        ref = f'CT-TOPUP-{uuid.uuid4().hex[:10].upper()}'
        PaymentLog.objects.create(
            customer=customer,
            billing_type='PREPAID',
            amount_paid=amount_kes,
            payment_method='Card Terminal Top-Up',
            transaction_reference=ref,
            created_by=request.user,
        )

        if binding:
            _nuomiy_recharge(binding, amount_kes, transaction_type=transaction_type, transaction_status=transaction_status, wallet_type=wallet_type)

        return Response({
            'status': 'ok',
            'new_balance_kes': str(wallet.balance_kes),
            'amount_added': str(amount_kes),
            'card_no': card_no,
            'whitelist': 'allowed' if card_no else 'no_card_bound',
            'reference': ref,
        })


class CardTerminalStatsView(APIView):
    """GET /api/card-terminal/stats/ — summary counts for the dashboard."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.db.models import Sum, Count
        from django.utils import timezone
        from datetime import timedelta

        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        total_devices = CardTerminalDevice.objects.filter(is_active=True).count()
        online_devices = CardTerminalDevice.objects.filter(
            is_active=True,
            last_seen_at__gte=now - timedelta(minutes=2),
        ).count()

        txn_qs = CardTerminalTransaction.objects.filter(is_successful=True, mode=0)
        today_collected = txn_qs.filter(
            created_at__gte=today_start
        ).aggregate(total=Sum('amount_deducted_kes'))['total'] or Decimal('0')

        month_collected = txn_qs.filter(
            created_at__gte=month_start
        ).aggregate(total=Sum('amount_deducted_kes'))['total'] or Decimal('0')

        offline_pending = CardTerminalTransaction.objects.filter(source='offline').count()
        total_bindings = CardBinding.objects.filter(is_active=True).count()

        return Response({
            'total_devices': total_devices,
            'online_devices': online_devices,
            'today_collected_kes': str(round(today_collected, 2)),
            'month_collected_kes': str(round(month_collected, 2)),
            'offline_transactions': offline_pending,
            'active_card_bindings': total_bindings,
        })
