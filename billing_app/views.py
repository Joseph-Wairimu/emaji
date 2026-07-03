import csv
from rest_framework import viewsets, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from django_filters.rest_framework import DjangoFilterBackend
from django.http import HttpResponse
from django.db import models as django_models
from .models import User, Role, Site, SiteAssignment, Customer, Meter, UnitPrice, BillingRecord, PaymentLog, ReadingLog, MpesaTopupRequest
from .serializers import (
    UserSerializer, RoleSerializer, SiteSerializer, SiteAssignmentSerializer,
    CustomerSerializer, MeterSerializer, UnitPriceSerializer,
    BillingRecordSerializer, PaymentLogSerializer, ReadingLogSerializer,CustomTokenObtainPairSerializer
)
from .permissions import IsAdmin, IsSiteManagerForSite, IsMeterReaderForSite
from rest_framework.views import APIView
from django.db.models import Sum, OuterRef, Subquery, Q, F, ExpressionWrapper
from decimal import Decimal
from rest_framework_simplejwt.views import TokenObtainPairView
from django.db.models import Sum, Value
from django.db.models.functions import Coalesce
from django.db.models.functions import Cast
from django.db.models import DecimalField
from django.utils import timezone

def get_queryset(self):
    user = self.request.user

    if user.role and user.role.name.upper() == "SUPER_ADMIN":
        return super().get_queryset()

    assigned_sites = SiteAssignment.objects.filter(user=user).values_list('site_id', flat=True)

    if user.role and user.role.name.upper() in ["site_manager", "meter_reader"]:
        return self.queryset.filter(site_id__in=assigned_sites)

    return self.queryset.none()


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer
    
    
class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsAdmin]

    @action(detail=True, methods=['post'], permission_classes=[IsAdmin])
    def assign_role(self, request, pk=None):
        user = self.get_object()
        role_id = request.data.get('role_id')
        role = Role.objects.get(id=role_id)
        user.role = role
        user.save()
        return Response({'status': 'role assigned'})

    @action(detail=True, methods=['post'], permission_classes=[IsAdmin])
    def assign_site(self, request, pk=None):
        user = self.get_object()
        site_id = request.data.get('site_id')
        site = Site.objects.get(id=site_id)
        SiteAssignment.objects.create(user=user, site=site)
        return Response({'status': 'site assigned'})


class RoleViewSet(viewsets.ModelViewSet):
    queryset = Role.objects.all()
    serializer_class = RoleSerializer
    #permission_classes = [IsAdmin]


class SiteViewSet(viewsets.ModelViewSet):
    queryset = Site.objects.all()
    serializer_class = SiteSerializer
    permission_classes = [IsAuthenticated, IsAdmin | IsSiteManagerForSite | IsMeterReaderForSite]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['name']


class SiteAssignmentViewSet(viewsets.ModelViewSet):
    queryset = SiteAssignment.objects.all()
    serializer_class = SiteAssignmentSerializer
    permission_classes = [IsAdmin]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['user', 'site']


class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer
    permission_classes = [IsAuthenticated, IsAdmin | IsSiteManagerForSite]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['site', 'account_status']
    search_fields = ['first_name', 'last_name', 'email']

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
        
    def get_queryset(self):
        user = self.request.user
        if user.role and user.role.name.upper() == "SUPER_ADMIN":
            return Customer.objects.all()
        assigned_sites = SiteAssignment.objects.filter(user=user).values_list('site_id', flat=True)
        return Customer.objects.filter(site_id__in=assigned_sites)
        


class MeterViewSet(viewsets.ModelViewSet):
    queryset = Meter.objects.all()
    serializer_class = MeterSerializer
    permission_classes = [IsAuthenticated, IsAdmin | IsSiteManagerForSite]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['site', 'status']
    
    def get_queryset(self):
        user = self.request.user
        if user.role and user.role.name.upper() == "SUPER_ADMIN":
            return Meter.objects.all()
        assigned_sites = SiteAssignment.objects.filter(user=user).values_list('site_id', flat=True)
        return Meter.objects.filter(site_id__in=assigned_sites)


class UnitPriceViewSet(viewsets.ModelViewSet):
    queryset = UnitPrice.objects.all()
    serializer_class = UnitPriceSerializer
    permission_classes = [IsAdmin]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['effective_date']


class BillingRecordViewSet(viewsets.ModelViewSet):
    queryset = BillingRecord.objects.all()
    serializer_class = BillingRecordSerializer
    permission_classes = [IsAuthenticated, IsAdmin | IsSiteManagerForSite]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['customer', 'meter','reading_date','updated_at','payment_status']
    http_method_names = ['get', 'post']

    def get_queryset(self):
        user = self.request.user
        if user.role and user.role.name.upper() == "SUPER_ADMIN":
            return BillingRecord.objects.all()
        assigned_sites = SiteAssignment.objects.filter(user=user).values_list('site_id', flat=True)
        return BillingRecord.objects.filter(customer__site_id__in=assigned_sites)

    @action(detail=False, methods=['get'], url_path='download_statement',
            permission_classes=[IsAuthenticated, IsAdmin | IsSiteManagerForSite])
    def download_statement(self, request):
        accessible_billing = self.get_queryset().values_list('id', flat=True)

        payment_qs = PaymentLog.objects.filter(
            billing_record_id__in=accessible_billing
        ).select_related(
            'billing_record__customer', 'billing_record__meter'
        )

        customer_id = request.query_params.get('customer')
        customers_param = request.query_params.get('customers')

        if customer_id:
            payment_qs = payment_qs.filter(billing_record__customer_id=customer_id)
        elif customers_param:
            customer_ids = [cid.strip() for cid in customers_param.split(',') if cid.strip()]
            payment_qs = payment_qs.filter(billing_record__customer_id__in=customer_ids)

        payment_qs = payment_qs.order_by(
            'billing_record__customer__last_name',
            'billing_record__customer__first_name',
            'payment_date',
        )

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="payment_statement.csv"'

        writer = csv.writer(response)
        writer.writerow([
            'Customer Name', 'Meter Number', 'Transaction Reference',
            'Payment Date', 'Amount Paid', 'Payment Method',
            'Billing Period',
        ])

        for log in payment_qs:
            billing = log.billing_record
            customer = billing.customer
            writer.writerow([
                f"{customer.first_name} {customer.last_name}",
                billing.meter.meter_number,
                log.transaction_reference,
                log.payment_date.strftime('%Y-%m-%d %H:%M'),
                log.amount_paid,
                log.payment_method,
                billing.reading_date.strftime('%Y-%m-%d'),
            ])

        return response

class PaymentLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = PaymentLog.objects.all()
    serializer_class = PaymentLogSerializer
    permission_classes = [IsAuthenticated, IsAdmin | IsSiteManagerForSite]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['billing_record', 'payment_date', 'billing_type']

    def get_queryset(self):
        user = self.request.user
        if user.role and user.role.name.upper() == "SUPER_ADMIN":
            return PaymentLog.objects.all()
        assigned_sites = SiteAssignment.objects.filter(user=user).values_list('site_id', flat=True)
        return PaymentLog.objects.filter(
            Q(billing_record__customer__site_id__in=assigned_sites) |
            Q(billing_record__isnull=True, customer__site_id__in=assigned_sites)
        )


class ReadingLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ReadingLog.objects.all()
    serializer_class = ReadingLogSerializer
    permission_classes = [IsAuthenticated, IsAdmin | IsMeterReaderForSite]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['billing_record', 'recorded_at', 'billing_type']

    def get_queryset(self):
        user = self.request.user
        if user.role and user.role.name.upper() == "SUPER_ADMIN":
            return ReadingLog.objects.all()
        assigned_sites = SiteAssignment.objects.filter(user=user).values_list('site_id', flat=True)
        return ReadingLog.objects.filter(
            Q(billing_record__customer__site_id__in=assigned_sites) |
            Q(billing_record__isnull=True, customer__site_id__in=assigned_sites)
        )


class AnalyticsView(APIView):
    permission_classes = [IsAuthenticated]

    @staticmethod
    def _month_boundaries(year, month):
        """Return (start, end) datetime for a given year/month."""
        now = timezone.now()
        start = now.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
        if month == 12:
            end = start.replace(year=year + 1, month=1)
        else:
            end = start.replace(month=month + 1)
        return start, end

    def get(self, request):
        now = timezone.now()  # fresh per-request timestamp — never use module-level now
        user = request.user

        if user.role and user.role.name.upper() != "SUPER_ADMIN":
            assigned_sites = SiteAssignment.objects.filter(user=user).values_list("site_id", flat=True)
            billing_records = BillingRecord.objects.filter(customer__site_id__in=assigned_sites)
            customers = Customer.objects.filter(site_id__in=assigned_sites)
            payment_logs = PaymentLog.objects.filter(
                Q(billing_record__customer__site_id__in=assigned_sites) |
                Q(billing_record__isnull=True, customer__site_id__in=assigned_sites)
            )
        else:
            billing_records = BillingRecord.objects.all()
            customers = Customer.objects.all()
            payment_logs = PaymentLog.objects.all()

        # ── Current month boundaries ────────────────────────────────────
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if now.month == 12:
            end_of_month = start_of_month.replace(year=now.year + 1, month=1)
        else:
            end_of_month = start_of_month.replace(month=now.month + 1)

        expected_amount = billing_records.filter(balance__gt=0).aggregate(
            total_due=Sum("balance")
        )["total_due"] or Decimal("0.00")

        total_paid_raw = payment_logs.filter(
            payment_date__gte=start_of_month,
            payment_date__lt=end_of_month,
        ).aggregate(total=Coalesce(Sum("amount_paid"), Value(Decimal("0"))))["total"]

        current_month_records = billing_records.filter(
            reading_date__gte=start_of_month,
            reading_date__lt=end_of_month,
        )
        consumption_agg = current_month_records.aggregate(
            current_sum=Coalesce(Sum("current_reading"), Value(Decimal("0"))),
            past_sum=Coalesce(Sum("past_reading"), Value(Decimal("0"))),
        )
        total_consumption_current_month_units = (
            consumption_agg["current_sum"] - consumption_agg["past_sum"]
        )

        neg_balances = billing_records.annotate(
            balance_dec=Cast("balance", DecimalField(max_digits=12, decimal_places=2))
        ).filter(balance_dec__lt=0).aggregate(
            total=Coalesce(Sum("balance_dec"), Value(Decimal("0.00")))
        )["total"]

        applied_paid = total_paid_raw
        unpaid_amount = expected_amount

        total_bills = billing_records.count()
        total_customers = customers.count()

        latest_billing = BillingRecord.objects.filter(customer=OuterRef("pk")).order_by("-reading_date")
        customers_with_debt = customers.annotate(
            latest_balance=Subquery(latest_billing.values("balance")[:1])
        ).filter(latest_balance__gt=0).count()

        customers_paid = customers.annotate(
            latest_status=Subquery(latest_billing.values("payment_status")[:1])
        ).filter(latest_status="PAID").count()

        payment_completion_rate = (
            (applied_paid / expected_amount * 100) if expected_amount > 0 else Decimal("0.00")
        )

        def _billed_for_qs(qs):
            return qs.annotate(
                cycle_billed=ExpressionWrapper(
                    (F("current_reading") - F("past_reading")) * F("unit_price_used"),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                )
            ).aggregate(
                total=Coalesce(Sum("cycle_billed"), Value(Decimal("0")))
            )["total"]

        monthly_breakdown = []
        year, month = now.year, now.month
        for _ in range(7):
            m_start, m_end = self._month_boundaries(year, month)
            billed = _billed_for_qs(
                billing_records.filter(reading_date__gte=m_start, reading_date__lt=m_end)
            )
            collected = payment_logs.filter(
                payment_date__gte=m_start, payment_date__lt=m_end,
            ).aggregate(
                total=Coalesce(Sum("amount_paid"), Value(Decimal("0")))
            )["total"]

            monthly_breakdown.insert(0, {
                "month": m_start.strftime("%b"),
                "year": year,
                "billed": float(round(billed, 2)),
                "collected": float(round(collected, 2)),
            })
            month -= 1
            if month == 0:
                month = 12
                year -= 1

        current_month_billed = _billed_for_qs(current_month_records)

        return Response({
            "expected_amount": str(round(expected_amount, 2)),
            "total_amount_paid_raw": str(round(total_paid_raw, 2)),
            "total_amount_to_be_paid": str(round(expected_amount, 2)),
            "total_consumption_current_month_units": str(round(total_consumption_current_month_units, 2)),
            "unpaid_amount": str(round(unpaid_amount, 2)),
            "overpayment": abs(neg_balances),
            "total_bills": total_bills,
            "total_customers": total_customers,
            "customers_with_debt": customers_with_debt,
            "total_paid_customers": customers_paid,
            "payment_completion_rate": f"{round(payment_completion_rate, 2)}%",
            "monthly_breakdown": monthly_breakdown,
            "current_month_billed": str(round(current_month_billed, 2)),
            "current_month_collected": str(round(total_paid_raw, 2)),
        })


import logging as _logging
_billing_mpesa_logger = _logging.getLogger(__name__)


class BillingMpesaInitiateView(APIView):
    """
    POST /api/billing/mpesa/initiate/
    Body: { "billing_record_id": "<uuid>", "amount_kes": <number>, "phone_number": "07XXXXXXXX" }
    Initiates an M-Pesa STK push for a billing record payment.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from django.shortcuts import get_object_or_404
        billing_record_id = request.data.get('billing_record_id')
        amount_raw = request.data.get('amount_kes')
        phone = str(request.data.get('phone_number', '')).strip()

        if not billing_record_id or amount_raw is None or not phone:
            return Response(
                {'error': 'billing_record_id, amount_kes, and phone_number are required'},
                status=400,
            )

        try:
            amount_kes = Decimal(str(amount_raw))
            if amount_kes <= 0:
                raise ValueError
        except (ValueError, Exception):
            return Response({'error': 'amount_kes must be a positive number'}, status=400)

        phone = phone.replace('+', '').replace(' ', '').replace('-', '')
        if phone.startswith('0'):
            phone = '254' + phone[1:]
        if not phone.startswith('254') or len(phone) != 12 or not phone.isdigit():
            return Response(
                {'error': 'Invalid phone number. Use 07XXXXXXXX or 254XXXXXXXXX format.'},
                status=400,
            )

        billing = get_object_or_404(BillingRecord, id=billing_record_id)

        external_id = f'BILL-{str(billing_record_id)[:8]}'

        try:
            from .services.merchant_api import MerchantApiService
            svc = MerchantApiService()
            transaction = svc.initiate_stk_push(
                phone=phone,
                amount=amount_kes,
                account_reference=f'EMAJI-{str(billing_record_id)[:7].upper()}',
                transaction_desc='Water Bill Payment',
                external_id=external_id,
            )
        except ValueError as e:
            return Response({'error': str(e)}, status=503)
        except Exception as e:
            _billing_mpesa_logger.exception('M-Pesa STK initiation failed for billing %s', billing_record_id)
            return Response({'error': f'M-Pesa service error: {str(e)}'}, status=502)

        checkout_request_id = transaction.get('id', '')

        MpesaTopupRequest.objects.create(
            customer=billing.customer,
            billing_record=billing,
            amount_kes=amount_kes,
            phone_number=phone,
            checkout_request_id=checkout_request_id,
            merchant_request_id=external_id,
            created_by=request.user,
        )

        return Response({
            'status': 'pending',
            'checkout_request_id': checkout_request_id,
            'customer_message': 'Check your phone and enter your M-Pesa PIN.',
        })


class BillingMpesaStatusView(APIView):
    """
    GET /api/billing/mpesa/status/<checkout_request_id>/
    Polls the status of a billing M-Pesa STK push.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, checkout_request_id):
        from django.shortcuts import get_object_or_404
        topup = get_object_or_404(MpesaTopupRequest, checkout_request_id=checkout_request_id)

        if topup.status == 'pending':
            try:
                from .services.merchant_api import MerchantApiService
                transaction = MerchantApiService().get_transaction(checkout_request_id)
                if transaction.get('status') == 'FAILED':
                    topup.status = 'failed'
                    topup.result_code = str(transaction.get('resultCode', ''))
                    topup.result_desc = transaction.get('errorMessage') or transaction.get('resultDesc', '')
                    topup.save()
            except Exception:
                pass

        return Response({
            'status': topup.status,
            'amount_kes': str(topup.amount_kes),
            'phone_number': topup.phone_number,
            'mpesa_receipt_number': topup.mpesa_receipt_number,
            'result_desc': topup.result_desc,
            'created_at': topup.created_at.isoformat(),
        })