from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from .views import (
    UserViewSet, RoleViewSet, SiteViewSet, SiteAssignmentViewSet,
    CustomerViewSet, MeterViewSet, UnitPriceViewSet,
    BillingRecordViewSet, PaymentLogViewSet, ReadingLogViewSet
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
    path('auth/login/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('', include(router.urls)),
]