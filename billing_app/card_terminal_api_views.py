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
from .permissions import IsAdmin, IsStaff
from .views_card_terminal import _upsert_whitelist

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Nuomiy cloud sync helpers — best-effort, never raise
# ---------------------------------------------------------------------------

def _nuomiy_register(binding: 'CardBinding') -> None:
    """Register the cardholder on the Nuomiy platform and store the returned ID."""
    if not settings.NUOMIY_APP_ID:
        return
    try:
        from .services.nuomiy_cloud import NuomiyCloudService
        customer = binding.customer
        indate = (date.today() + timedelta(days=5 * 365)).strftime('%Y-%m-%d')
        svc = NuomiyCloudService()
        resp = svc.register_cardholder(
            customer_name=f'{customer.first_name} {customer.last_name}',
            customer_sex=0,
            card_type=int(settings.NUOMIY_DEFAULT_CARD_TYPE),
            dept_id=int(settings.NUOMIY_DEFAULT_DEPT_ID),
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


def _nuomiy_recharge(binding: 'CardBinding', amount_kes: Decimal) -> None:
    """Mirror a top-up to the Nuomiy platform wallet."""
    if not settings.NUOMIY_APP_ID:
        return
    try:
        from .services.nuomiy_cloud import NuomiyCloudService
        svc = NuomiyCloudService()

        # Resolve card identifier — prefer nuomiy_customer_id as cardId;
        # fall back to the physical card number as cardNo (must be numeric).
        card_id = None
        card_no = None
        if binding.nuomiy_customer_id:
            try:
                card_id = int(binding.nuomiy_customer_id)
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
            amount=amount_kes,  # Decimal passed directly; service formats to 2dp
            transaction_status='1',
            transaction_type=settings.NUOMIY_DEFAULT_TRANSACTION_TYPE,
            wallet_type=settings.NUOMIY_DEFAULT_WALLET_TYPE,
            card_id=card_id,
            card_no=card_no,
        )
        logger.info('Nuomiy recharge card %s +%s → %s', binding.card_no, amount_kes, resp.get('code'))
    except Exception:
        logger.exception('Nuomiy recharge failed for card %s', binding.card_no)


def _nuomiy_set_card_status(binding: 'CardBinding', card_status: str) -> None:
    """
    Mirror a card status change to Nuomiy.
    card_status: '1'=issued, '3'=lost, '4'=cancelled
    Uses nuomiy_customer_id if stored; falls back to card_no lookup.
    """
    if not settings.NUOMIY_APP_ID:
        return
    try:
        from .services.nuomiy_cloud import NuomiyCloudService
        if not binding.nuomiy_customer_id:
            logger.warning('No nuomiy_customer_id for card %s — skipping status sync', binding.card_no)
            return
        svc = NuomiyCloudService()
        resp = svc.unsubscribe_card(
            card_ids=binding.nuomiy_customer_id,
            card_status=card_status,
        )
        logger.info('Nuomiy set card %s status %s → %s', binding.card_no, card_status, resp.get('code'))
    except Exception:
        logger.exception('Nuomiy status sync failed for card %s', binding.card_no)


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


def _first(*values):
    """Return the first non-None, non-empty value from the candidates."""
    for v in values:
        if v is not None and v != '':
            return v
    return None


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
    Map a Nuomiy cardholder record to the shape CardBindingsTable expects.
    local_map: { card_no -> CardBinding } for enriching customer_id and local id.
    """
    card_no = str(_first(
        c.get('cardNumber'), c.get('cardNo'), c.get('physicalCardNo'), ''
    ))
    local = local_map.get(card_no)
    card_status = str(_first(c.get('cardStatus'), c.get('status'), '1'))
    return {
        'id': str(local.id) if local else str(_first(c.get('id'), c.get('customerId'), '')),
        'card_no': card_no,
        'customer_name': _first(
            c.get('customerName'), c.get('name'),
            f'{local.customer.first_name} {local.customer.last_name}' if local else None,
            '—',
        ),
        'customer_id': str(local.customer.id) if local else None,
        'is_active': card_status == '1',
        'nuomiy_customer_id': str(_first(c.get('id'), c.get('customerId'), '')),
        'created_at': _first(
            c.get('createTime'), c.get('createDate'), c.get('createdAt'),
            local.created_at.isoformat() if local else '',
        ),
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
    PATCH /api/card-terminal/bindings/<id>/ — update (e.g. deactivate a lost card)
    DELETE /api/card-terminal/bindings/<id>/ — remove binding
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def patch(self, request, binding_id):
        binding = get_object_or_404(CardBinding, id=binding_id)
        is_active = request.data.get('is_active')
        if is_active is not None:
            binding.is_active = bool(is_active)
            binding.save(update_fields=['is_active'])
            op = 1 if binding.is_active else 0
            _upsert_whitelist(binding.card_no, op)
            # Mirror status to Nuomiy: re-issued (1) or lost (3)
            _nuomiy_set_card_status(binding, '1' if binding.is_active else '3')
        return Response(CardBindingSerializer(binding).data)

    def delete(self, request, binding_id):
        binding = get_object_or_404(CardBinding, id=binding_id)
        _upsert_whitelist(binding.card_no, 0)
        # Mark as cancelled on Nuomiy before removing locally
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


class CardTerminalTopupView(APIView):
    """
    POST /api/card-terminal/topup/
    Body: { "customer_id": "<uuid>", "amount_kes": <number> }
    Credits balance_kes, adds card to whitelist, records PaymentLog.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        customer_id = request.data.get('customer_id')
        amount_raw = request.data.get('amount_kes')

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
            _nuomiy_recharge(binding, amount_kes)

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
