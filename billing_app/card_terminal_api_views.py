"""
DRF API views for card terminal management (used by the frontend dashboard).
All endpoints require JWT authentication.
"""
import uuid
import logging
from decimal import Decimal

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
    customer_id = drf_serializers.UUIDField(write_only=True)

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
# Views
# ---------------------------------------------------------------------------

class CardTerminalDeviceListView(APIView):
    """GET /api/card-terminal/devices/ — list all registered terminal devices."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        devices = CardTerminalDevice.objects.select_related('site').order_by('-last_seen_at')
        return Response(CardTerminalDeviceSerializer(devices, many=True).data)


class CardBindingListCreateView(APIView):
    """
    GET  /api/card-terminal/bindings/ — list all card→customer bindings
    POST /api/card-terminal/bindings/ — create a new binding
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        bindings = CardBinding.objects.select_related('customer').order_by('-created_at')
        return Response(CardBindingSerializer(bindings, many=True).data)

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
            # Sync whitelist
            op = 1 if binding.is_active else 0
            _upsert_whitelist(binding.card_no, op)
        return Response(CardBindingSerializer(binding).data)

    def delete(self, request, binding_id):
        binding = get_object_or_404(CardBinding, id=binding_id)
        _upsert_whitelist(binding.card_no, 0)
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

        # Enable offline access
        try:
            card_no = customer.card_binding.card_no
            _upsert_whitelist(card_no, 1)
        except CardBinding.DoesNotExist:
            card_no = None

        ref = f'CT-TOPUP-{uuid.uuid4().hex[:10].upper()}'
        PaymentLog.objects.create(
            customer=customer,
            billing_type='PREPAID',
            amount_paid=amount_kes,
            payment_method='Card Terminal Top-Up',
            transaction_reference=ref,
            created_by=request.user,
        )

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
