from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from .views import (
    UserViewSet, RoleViewSet, SiteViewSet, SiteAssignmentViewSet,
    CustomerViewSet, MeterViewSet, UnitPriceViewSet,
    BillingRecordViewSet, PaymentLogViewSet, ReadingLogViewSet, AnalyticsView, CustomTokenObtainPairView,
    BillingMpesaInitiateView, BillingMpesaStatusView,
)
from .smart_meter_views import (
    SmartMeterIngestView,
    SmartMeterStatusView,
    PrepaidWalletView,
    PrepaidTopupView,
    PrepaidMpesaInitiateView,
    ValveControlView,
    ReportIntervalView,
    SmartMeterCommandPendingView,
    SmartMeterCommandUpdateView,
    SmartMeterCommandAcknowledgeView,
)
from .card_terminal_api_views import (
    CardTerminalDeviceListView,
    CardBindingListCreateView,
    CardBindingDetailView,
    CardTerminalTransactionListView,
    CardTerminalTopupListView,
    CardTerminalWalletView,
    CardTerminalBasesettingsView,
    CardTerminalTopupView,
    CardTerminalStatsView,
    CardTerminalMpesaInitiateView,
    MpesaCallbackView,
    CardTerminalMpesaStatusView,
    MpesaC2BConfirmationView,
    MpesaC2BValidationView,
)
from .customer_portal_views import (
    CustomerMeView,
    CustomerBillingView,
    CustomerTransactionsView,
    CustomerTopupView,
    CustomerCreateLoginView,
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
    path('smart-meter/report-interval/', ReportIntervalView.as_view(), name='smart-meter-report-interval'),

    # Decoder-facing command queue endpoints (API key auth, no JWT)
    path('smart-meter/commands/pending/<str:meter_address>/', SmartMeterCommandPendingView.as_view(), name='smart-meter-commands-pending'),
    path('smart-meter/commands/<uuid:command_id>/update/', SmartMeterCommandUpdateView.as_view(), name='smart-meter-commands-update'),
    path('smart-meter/commands/acknowledge/<str:meter_address>/', SmartMeterCommandAcknowledgeView.as_view(), name='smart-meter-commands-acknowledge'),

    # Prepaid wallet endpoints (Fengbo / m³ based)
    path('prepaid/topup/', PrepaidTopupView.as_view(), name='prepaid-topup'),
    path('prepaid/wallet/<uuid:meter_id>/', PrepaidWalletView.as_view(), name='prepaid-wallet'),
    path('prepaid/mpesa/initiate/', PrepaidMpesaInitiateView.as_view(), name='prepaid-mpesa-initiate'),

    # Card terminal management API (JWT, frontend use)
    path('card-terminal/devices/', CardTerminalDeviceListView.as_view(), name='ct-devices'),
    path('card-terminal/bindings/', CardBindingListCreateView.as_view(), name='ct-bindings'),
    path('card-terminal/bindings/<uuid:binding_id>/', CardBindingDetailView.as_view(), name='ct-binding-detail'),
    path('card-terminal/transactions/', CardTerminalTransactionListView.as_view(), name='ct-transactions'),
    path('card-terminal/topups/', CardTerminalTopupListView.as_view(), name='ct-topups'),
    path('card-terminal/wallet/<uuid:customer_id>/', CardTerminalWalletView.as_view(), name='ct-wallet'),
    path('card-terminal/basesettings/', CardTerminalBasesettingsView.as_view(), name='ct-basesettings'),
    path('card-terminal/topup/', CardTerminalTopupView.as_view(), name='ct-topup'),
    path('card-terminal/stats/', CardTerminalStatsView.as_view(), name='ct-stats'),

    # M-Pesa STK push for billing record payments (postpaid / smart meter)
    path('billing/mpesa/initiate/', BillingMpesaInitiateView.as_view(), name='billing-mpesa-initiate'),
    path('billing/mpesa/status/<str:checkout_request_id>/', BillingMpesaStatusView.as_view(), name='billing-mpesa-status'),

    # M-Pesa STK push for card terminal top-ups
    path('card-terminal/mpesa/initiate/', CardTerminalMpesaInitiateView.as_view(), name='ct-mpesa-initiate'),
    path('card-terminal/mpesa/callback/', MpesaCallbackView.as_view(), name='ct-mpesa-callback'),
    path('card-terminal/mpesa/status/<str:checkout_request_id>/', CardTerminalMpesaStatusView.as_view(), name='ct-mpesa-status'),

    # M-Pesa C2B (Paybill) — customer-initiated, no STK
    path('card-terminal/mpesa/c2b/confirm/', MpesaC2BConfirmationView.as_view(), name='ct-mpesa-c2b-confirm'),
    path('card-terminal/mpesa/c2b/validate/', MpesaC2BValidationView.as_view(), name='ct-mpesa-c2b-validate'),

    # Customer portal (role=CUSTOMER JWT)
    path('customer/me/', CustomerMeView.as_view(), name='customer-me'),
    path('customer/billing/', CustomerBillingView.as_view(), name='customer-billing'),
    path('customer/transactions/', CustomerTransactionsView.as_view(), name='customer-transactions'),
    path('customer/topup/', CustomerTopupView.as_view(), name='customer-topup'),

    # Staff: create/reset customer portal login
    path('customers/<uuid:customer_id>/create-login/', CustomerCreateLoginView.as_view(), name='customer-create-login'),

    path('', include(router.urls)),
]