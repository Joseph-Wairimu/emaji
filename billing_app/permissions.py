from rest_framework import permissions
from .models import SiteAssignment, Role



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
        if not request.user.is_authenticated or not request.user.role.name == 'site_manager':
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
        if not request.user.is_authenticated or not request.user.role.name == 'meter_reader':
            return False
        return True

    def has_object_permission(self, request, view, obj):
        if hasattr(obj, 'site'):
            site = obj.site
        else:
            site = obj
        return SiteAssignment.objects.filter(user=request.user, site=site).exists()