from rest_framework import permissions
from .models import SiteAssignment, Role


class IsCustomer(permissions.BasePermission):
    """JWT-authenticated user with role CUSTOMER and a linked Customer profile."""
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        role = getattr(request.user, 'role', None)
        if role is None or role.name != 'CUSTOMER':
            return False
        return hasattr(request.user, 'customer_profile')


class IsStaff(permissions.BasePermission):
    """Any authenticated user with a staff role (SUPER_ADMIN, site_manager, meter_reader)."""
    STAFF_ROLES = {'SUPER_ADMIN', 'site_manager', 'meter_reader'}

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        role = getattr(request.user, 'role', None)
        return role is not None and role.name in self.STAFF_ROLES



class IsAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        role = getattr(user, "role", None)
        if role is None:
            return False

        return role.name == "SUPER_ADMIN"

class IsSiteManagerForSite(permissions.BasePermission):
    def has_permission(self, request, view):
        role = getattr(request.user, 'role', None)
        if not request.user.is_authenticated or role is None or role.name != 'site_manager':
            return False
        return True

    def has_object_permission(self, request, view, obj):
        if hasattr(obj, 'site'):
            site = obj.site
        else:
            site = obj
        return SiteAssignment.objects.filter(user=request.user, site=site).exists()


class IsMeterReaderForSite(permissions.BasePermission):
    def has_permission(self, request, view):
        role = getattr(request.user, 'role', None)
        if not request.user.is_authenticated or role is None or role.name != 'meter_reader':
            return False
        return True

    def has_object_permission(self, request, view, obj):
        if hasattr(obj, 'site'):
            site = obj.site
        else:
            site = obj
        return SiteAssignment.objects.filter(user=request.user, site=site).exists()