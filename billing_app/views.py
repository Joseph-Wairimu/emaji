import csv
from rest_framework import viewsets, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from django_filters.rest_framework import DjangoFilterBackend
from django.http import HttpResponse
from .models import User, Role, Site, SiteAssignment, Customer, Meter, UnitPrice, BillingRecord, PaymentLog, ReadingLog
from .serializers import (
    UserSerializer, RoleSerializer, SiteSerializer, SiteAssignmentSerializer,
    CustomerSerializer, MeterSerializer, UnitPriceSerializer,
    BillingRecordSerializer, PaymentLogSerializer, ReadingLogSerializer,CustomTokenObtainPairSerializer
)
from .permissions import IsAdmin, IsSiteManagerForSite, IsMeterReaderForSite
from rest_framework.views import APIView
from django.db.models import Sum, OuterRef, Subquery
from decimal import Decimal
from rest_framework_simplejwt.views import TokenObtainPairView
from django.db.models import Sum, Value
from django.db.models.functions import Coalesce
from django.db.models.functions import Cast
from django.db.models import DecimalField
from django.utils import timezone
now = timezone.now()

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
        queryset = self.get_queryset().select_related('customer', 'meter')

        customer_id = request.query_params.get('customer')
        customers_param = request.query_params.get('customers')

        if customer_id:
            queryset = queryset.filter(customer_id=customer_id)
        elif customers_param:
            customer_ids = [cid.strip() for cid in customers_param.split(',') if cid.strip()]
            queryset = queryset.filter(customer_id__in=customer_ids)

        queryset = queryset.order_by(
            'customer__last_name', 'customer__first_name', 'reading_date'
        )

        response = HttpResponse(content_type='text/csv')
        filename = 'payment_statement.csv'
        response['Content-Disposition'] = f'attachment; filename="{filename}"'

        writer = csv.writer(response)
        writer.writerow([
            'Customer Name', 'Meter Number', 'Reading Date',
            'Past Reading (m³)', 'Current Reading (m³)', 'Consumption (m³)',
            'Unit Price', 'Amount Due', 'Amount Paid', 'Balance',
            'Payment Status', 'Last Updated',
        ])

        for record in queryset:
            consumption = record.current_reading - record.past_reading
            writer.writerow([
                f"{record.customer.first_name} {record.customer.last_name}",
                record.meter.meter_number,
                record.reading_date.strftime('%Y-%m-%d'),
                record.past_reading,
                record.current_reading,
                consumption,
                record.unit_price_used,
                record.amount_due,
                record.amount_paid,
                record.balance,
                record.payment_status,
                record.updated_at.strftime('%Y-%m-%d %H:%M'),
            ])

        return response

class PaymentLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = PaymentLog.objects.all()
    serializer_class = PaymentLogSerializer
    permission_classes = [IsAuthenticated, IsAdmin | IsSiteManagerForSite]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['billing_record', 'payment_date']
    
    def get_queryset(self):
        user = self.request.user
        if user.role and user.role.name.upper() == "SUPER_ADMIN":
            return PaymentLog.objects.all()
        assigned_sites = SiteAssignment.objects.filter(user=user).values_list('site_id', flat=True)
        return PaymentLog.objects.filter(billing_record__customer__site_id__in=assigned_sites)



class ReadingLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ReadingLog.objects.all()
    serializer_class = ReadingLogSerializer
    permission_classes = [IsAuthenticated, IsAdmin | IsMeterReaderForSite]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['billing_record', 'recorded_at']
    
    def get_queryset(self):
        user = self.request.user
        if user.role and user.role.name.upper() == "SUPER_ADMIN":
            return ReadingLog.objects.all()
        assigned_sites = SiteAssignment.objects.filter(user=user).values_list('site_id', flat=True)
        return ReadingLog.objects.filter(billing_record__customer__site_id__in=assigned_sites)


class AnalyticsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        if user.role and user.role.name.upper() != "SUPER_ADMIN":
            assigned_sites = SiteAssignment.objects.filter(user=user).values_list("site_id", flat=True)
            billing_records = BillingRecord.objects.filter(customer__site_id__in=assigned_sites)
            customers = Customer.objects.filter(site_id__in=assigned_sites)
        else:
            billing_records = BillingRecord.objects.all()
            customers = Customer.objects.all()

        expected_amount = billing_records.filter(balance__gt=0).aggregate(
                total_due=Sum("balance")
            )["total_due"] or Decimal("0.00")

        # payment_qs = PaymentLog.objects.filter(billing_record__in=billing_records)
        # payments_agg = payment_qs.aggregate(total_paid_raw=Sum("current_amount_paid"))
   
        start_of_month = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )

        if now.month == 12:
            end_of_month = start_of_month.replace(year=now.year + 1, month=1)
        else:
            end_of_month = start_of_month.replace(month=now.month + 1)

        total_paid_raw = billing_records.filter(
            updated_at__gte=start_of_month,
            updated_at__lt=end_of_month
        ).aggregate(
            total_due=Sum("current_amount_paid")
        )["total_due"] or Decimal("0.00")
        
        total_consumption_current_month = billing_records.filter(
            updated_at__gte=start_of_month,
            updated_at__lt=end_of_month
        ).aggregate(
            total_due=Sum("current_reading")
        )["total_due"] or Decimal("0.00")
        
        total_consumption_past_month =billing_records.filter(
            updated_at__gte=start_of_month,
            updated_at__lt=end_of_month
        ).aggregate(
            total_due=Sum("past_reading")
        )["total_due"] or Decimal("0.00")
        total_consumption_current_month_units = total_consumption_current_month - total_consumption_past_month
        
        if total_paid_raw <= expected_amount:
            applied_paid = total_paid_raw
            unpaid_amount = expected_amount - applied_paid
            neg_balances = billing_records.annotate(
                    balance_dec=Cast("balance", DecimalField(max_digits=12, decimal_places=2))
                ).filter(balance_dec__lt=0).aggregate(
                    total=Coalesce(Sum("balance_dec"), Value(Decimal("0.00")))
                )["total"]


        else:
            applied_paid = expected_amount
            unpaid_amount = Decimal("0.00")
            neg_balances = billing_records.annotate(
                    balance_dec=Cast("balance", DecimalField(max_digits=12, decimal_places=2))
                ).filter(balance_dec__lt=0).aggregate(
                    total=Coalesce(Sum("balance_dec"), Value(Decimal("0.00")))
                )["total"]
        total_bills = billing_records.count()
        total_customers = customers.count()

        latest_billing = BillingRecord.objects.filter(customer=OuterRef("pk")).order_by("-reading_date")
        customers_with_debt = customers.annotate(
            latest_balance=Subquery(latest_billing.values("balance")[:1])
        ).filter(latest_balance__gt=0).count()

        customers_paid = customers.annotate(
            latest_status=Subquery(latest_billing.values("payment_status")[:1])
        ).filter(latest_status="PAID").count()

        payment_completion_rate = (applied_paid / expected_amount * 100) if expected_amount > 0 else Decimal("0.00")

        return Response({
            "expected_amount": str(round(expected_amount, 2)),
            "total_amount_paid_raw": str(round(total_paid_raw, 2)),  
            "total_amount_to_be_paid": str(round(expected_amount , 2)),
            "total_consumption_current_month_units": str(round(total_consumption_current_month_units , 2)),
            # "total_amount_paid": str(round(applied_paid, 2)),        
            "unpaid_amount": str(round(unpaid_amount, 2)),
            "overpayment": abs(neg_balances),
            "total_bills": total_bills,
            "total_customers": total_customers,
            "customers_with_debt": customers_with_debt,
            "total_paid_customers": customers_paid,
            "payment_completion_rate": f"{round(payment_completion_rate, 2)}%"
        })