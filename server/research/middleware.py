"""Passwordless owner access for direct loopback requests on a local installation."""
from ipaddress import ip_address
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.utils import timezone

from .models import AuditEvent, Invitation


class LocalAdministratorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.local_admin_access = False
        from .desktop_access import administrator_authorized
        if administrator_authorized(request):
            # AuthenticationMiddleware already resolved the user's real session.
            # No account creation, role upgrade or passwordless login occurs here.
            request.local_admin_access = True
            return self.get_response(request)
        enabled = settings.LOCAL_ADMIN_ACCESS and not settings.HOSTED
        direct = not any(request.META.get(key) for key in (
            'HTTP_FORWARDED', 'HTTP_X_FORWARDED_FOR', 'HTTP_X_FORWARDED_HOST',
            'HTTP_X_FORWARDED_PROTO',
        ))
        host = urlsplit('//'+request.get_host()).hostname
        try:
            loopback = ip_address(request.META.get('REMOTE_ADDR', '')).is_loopback
        except ValueError:
            loopback = False
        if (enabled and direct and loopback and host in {'127.0.0.1', 'localhost', '::1'}
                and request.path.startswith('/api/') and request.path != '/api/health'):
            owner, created = User.objects.get_or_create(username='admin', defaults={
                'password': make_password(None), 'is_active': True,
                'is_staff': True, 'is_superuser': True,
            })
            if not (owner.is_active and owner.is_staff and owner.is_superuser):
                owner.is_active = owner.is_staff = owner.is_superuser = True
                owner.save(update_fields=['is_active', 'is_staff', 'is_superuser'])
            if created:
                Invitation.objects.filter(username='admin', used_at=None).update(expires_at=timezone.now())
                AuditEvent.objects.create(actor='admin', action='account.local_owner_created')
            # Grant access to this request only; never mint a reusable administrator session.
            request.user = owner
            request.local_admin_access = True
        return self.get_response(request)
