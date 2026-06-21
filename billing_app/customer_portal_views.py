"""
Customer self-service portal API.

All endpoints require a JWT token whose associated User has:
  - role.name == 'CUSTOMER'
  - a Customer record linked via Customer.user (OneToOneField)

Staff endpoints (create-login, tariff read for site_manager) are in views.py / card_terminal_api_views.py.
"""
import uuid
import logging
from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    BillingRecord, CardBinding, CardTerminalTransaction,
    Customer, PaymentLog, PrepaidWallet, Role, UnitPrice, User,
)
from .permissions import IsCustomer, IsAdmin, IsStaff
from .views_card_terminal import _upsert_whitelist

logger = logging.getLogger(__name__)


def _get_customer(request) -> Customer:
    return request.user.customer_profile


# ---------------------------------------------------------------------------
# Customer self-service views
# ---------------------------------------------------------------------------

class CustomerMeView(APIView):
    """
    GET /api/customer/me/
    Full profile for the logged-in customer: account info, both wallet balances,
    linked card, latest billing record.
    """
    permission_classes = [IsCustomer]

    def get(self, request):
        customer = _get_customer(request)
        meter = customer.meter

        # Postpaid billing summary
        latest_bill = BillingRecord.objects.filter(customer=customer).order_by('-reading_date').first()
        billing_summary = None
        if latest_bill:
            billing_summary = {
                'reading_date': latest_bill.reading_date,
                'amount_due': str(latest_bill.amount_due),
                'amount_paid': str(latest_bill.amount_paid),
                'balance': str(latest_bill.balance),
                'payment_status': latest_bill.payment_status,
            }

        # Wallet
        wallet_data = None
        try:
            wallet = customer.wallet
            unit_price = UnitPrice.objects.order_by('-effective_date').first()
            balance_kes_fengbo = None
            if unit_price and unit_price.unit_price:
                balance_kes_fengbo = str((wallet.balance_m3 * unit_price.unit_price).quantize(Decimal('0.01')))
            wallet_data = {
                'balance_m3': str(wallet.balance_m3),
                'balance_kes_fengbo': balance_kes_fengbo,
                'balance_kes_card': str(wallet.balance_kes),
                'valve_status': wallet.valve_status,
            }
        except PrepaidWallet.DoesNotExist:
            pass

        # Card binding
        card_no = None
        try:
            card_no = customer.card_binding.card_no if customer.card_binding.is_active else None
        except CardBinding.DoesNotExist:
            pass

        return Response({
            'id': str(customer.id),
            'first_name': customer.first_name,
            'last_name': customer.last_name,
            'email': customer.email,
            'phone': customer.phone,
            'plot_no': customer.plot_no,
            'court_name': customer.court_name,
            'account_status': customer.account_status,
            'usage_status': customer.usage_status,
            'site': customer.site.name if customer.site else None,
            'meter_number': meter.meter_number if meter else None,
            'meter_type': meter.meter_type if meter else None,
            'card_no': card_no,
            'wallet': wallet_data,
            'latest_billing': billing_summary,
        })


class CustomerBillingView(APIView):
    """
    GET /api/customer/billing/
    Billing history for the logged-in customer (newest first, up to 24 records).
    """
    permission_classes = [IsCustomer]

    def get(self, request):
        customer = _get_customer(request)
        records = BillingRecord.objects.filter(customer=customer).order_by('-reading_date')[:24]
        data = []
        for r in records:
            data.append({
                'id': str(r.id),
                'reading_date': r.reading_date,
                'past_reading': str(r.past_reading),
                'current_reading': str(r.current_reading),
                'amount_due': str(r.amount_due),
                'amount_paid': str(r.amount_paid),
                'balance': str(r.balance),
                'payment_status': r.payment_status,
            })
        return Response(data)


class CustomerTransactionsView(APIView):
    """
    GET /api/customer/transactions/
    Card terminal transaction history for the logged-in customer (newest 50).
    """
    permission_classes = [IsCustomer]

    def get(self, request):
        customer = _get_customer(request)
        try:
            card_no = customer.card_binding.card_no
        except CardBinding.DoesNotExist:
            return Response([])

        txns = CardTerminalTransaction.objects.filter(
            card_no=card_no, is_successful=True, mode=0
        ).order_by('-created_at')[:50]

        data = [{
            'order_no': t.order_no,
            'source': t.source,
            'amount_kes': str(t.amount_deducted_kes),
            'balance_after': str(t.balance_after_kes),
            'created_at': t.created_at,
        } for t in txns]
        return Response(data)


class CustomerTopupView(APIView):
    """
    POST /api/customer/topup/
    Customer requests a manual card-terminal wallet topup.
    Body: { "amount_kes": <number>, "payment_method": "M-PESA" | "Cash" | ... }
    In production this would be wired to an M-PESA STK push; for now it records
    the payment and credits the wallet immediately (staff-confirmed flow).
    """
    permission_classes = [IsCustomer]

    def post(self, request):
        customer = _get_customer(request)
        amount_raw = request.data.get('amount_kes')
        payment_method = str(request.data.get('payment_method', 'Self-Service')).strip() or 'Self-Service'

        if amount_raw is None:
            return Response({'error': 'amount_kes is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            amount_kes = Decimal(str(amount_raw))
            if amount_kes <= 0:
                raise ValueError
        except (ValueError, Exception):
            return Response({'error': 'amount_kes must be a positive number'}, status=status.HTTP_400_BAD_REQUEST)

        wallet, _ = PrepaidWallet.objects.get_or_create(
            customer=customer,
            defaults={'balance_m3': Decimal('0'), 'last_known_flow_m3': Decimal('0'), 'valve_status': 'unknown'},
        )
        wallet.balance_kes += amount_kes
        wallet.save(update_fields=['balance_kes', 'updated_at'])

        # Re-enable offline whitelist access
        try:
            card_no = customer.card_binding.card_no
            _upsert_whitelist(card_no, 1)
        except CardBinding.DoesNotExist:
            pass

        ref = f'CUST-TOPUP-{uuid.uuid4().hex[:10].upper()}'
        PaymentLog.objects.create(
            customer=customer,
            billing_type='PREPAID',
            amount_paid=amount_kes,
            payment_method=payment_method,
            transaction_reference=ref,
        )

        return Response({
            'status': 'ok',
            'new_balance_kes': str(wallet.balance_kes),
            'amount_added': str(amount_kes),
            'reference': ref,
        })


# ---------------------------------------------------------------------------
# Staff endpoint: create a customer portal login
# ---------------------------------------------------------------------------

class CustomerCreateLoginView(APIView):
    """
    POST /api/customers/<customer_id>/create-login/
    Staff-only. Creates (or resets) a User account with role=CUSTOMER and
    links it to the Customer record so the customer can log in to the portal.

    Body: { "email": "...", "password": "..." }
    If the Customer already has a linked user, the password is reset.
    """
    permission_classes = [IsStaff]

    def post(self, request, customer_id):
        customer = get_object_or_404(Customer, id=customer_id)

        email = str(request.data.get('email', '')).strip()
        password = str(request.data.get('password', '')).strip()

        if not email or not password:
            return Response(
                {'error': 'email and password are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if len(password) < 6:
            return Response(
                {'error': 'password must be at least 6 characters'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        customer_role, _ = Role.objects.get_or_create(
            name='CUSTOMER',
            defaults={'description': 'Self-service customer portal access'},
        )

        if customer.user:
            # Reset existing account
            user = customer.user
            user.email = email
            user.username = email
            user.set_password(password)
            user.role = customer_role
            user.save()
            created = False
        else:
            if User.objects.filter(email=email).exists():
                return Response(
                    {'error': f'A user with email {email} already exists'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            user = User.objects.create(
                email=email,
                username=email,
                first_name=customer.first_name,
                last_name=customer.last_name,
                role=customer_role,
            )
            user.set_password(password)
            user.save()
            customer.user = user
            customer.save(update_fields=['user'])
            created = True

        return Response({
            'status': 'created' if created else 'updated',
            'user_id': str(user.id),
            'email': user.email,
            'customer_id': str(customer.id),
            'customer_name': f'{customer.first_name} {customer.last_name}',
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
