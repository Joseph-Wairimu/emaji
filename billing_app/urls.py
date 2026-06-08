from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from .views import (
    UserViewSet, RoleViewSet, SiteViewSet, SiteAssignmentViewSet,
    CustomerViewSet, MeterViewSet, UnitPriceViewSet,
    BillingRecordViewSet, PaymentLogViewSet, ReadingLogViewSet, AnalyticsView, CustomTokenObtainPairView,
)
from .smart_meter_views import (
    SmartMeterIngestView,
    SmartMeterStatusView,
    PrepaidWalletView,
    PrepaidTopupView,
    ValveControlView,
)

router = DefaultRouter()
router.register(r'users', UserViewSet)
router.register(r'roles', RoleViewSet)
router.register(r'sites', SiteViewSet)
router.register(r'site-assignments', SiteAssignmentViewSet)
router.register(r'customers', CustomerViewSet)
router.register(r'meters', MeterViewSet)
router.register(r'unit-prices', UnitPriceViewSet)
router.register(r'billing', BillingRecordViewSet)
router.register(r'payments', PaymentLogViewSet)
router.register(r'readings', ReadingLogViewSet)

urlpatterns = [
    path('auth/login/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('analytics/', AnalyticsView.as_view(), name='analytics'),

    # Smart meter IoT endpoints
    path('smart-meter/ingest/', SmartMeterIngestView.as_view(), name='smart-meter-ingest'),
    path('smart-meter/status/<str:meter_address>/', SmartMeterStatusView.as_view(), name='smart-meter-status'),
    path('smart-meter/valve/', ValveControlView.as_view(), name='smart-meter-valve'),

    # Prepaid wallet endpoints
    path('prepaid/topup/', PrepaidTopupView.as_view(), name='prepaid-topup'),
    path('prepaid/wallet/<uuid:customer_id>/', PrepaidWalletView.as_view(), name='prepaid-wallet'),

    path('', include(router.urls)),
]