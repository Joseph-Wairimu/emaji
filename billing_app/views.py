from rest_framework import viewsets, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from django_filters.rest_framework import DjangoFilterBackend
from .models import User, Role, Site, SiteAssignment, Customer, Meter, UnitPrice, BillingRecord, PaymentLog, ReadingLog
from .serializers import (
    UserSerializer, RoleSerializer, SiteSerializer, SiteAssignmentSerializer,
    CustomerSerializer, MeterSerializer, UnitPriceSerializer,
    BillingRecordSerializer, PaymentLogSerializer, ReadingLogSerializer
)
from .permissions import IsAdmin, IsSiteManagerForSite, IsMeterReaderForSite


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


class MeterViewSet(viewsets.ModelViewSet):
    queryset = Meter.objects.all()
    serializer_class = MeterSerializer
    permission_classes = [IsAuthenticated, IsAdmin | IsSiteManagerForSite]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['site', 'status']


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
    filterset_fields = ['customer', 'meter', 'reading_date', 'payment_status']


class PaymentLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = PaymentLog.objects.all()
    serializer_class = PaymentLogSerializer
    permission_classes = [IsAuthenticated, IsAdmin | IsSiteManagerForSite]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['billing_record', 'payment_date']


class ReadingLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ReadingLog.objects.all()
    serializer_class = ReadingLogSerializer
    permission_classes = [IsAuthenticated, IsAdmin | IsMeterReaderForSite]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['billing_record', 'recorded_at']
