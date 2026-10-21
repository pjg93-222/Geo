"""The native launcher boundary authorizes an existing signed-in administrator.

This never creates an account or changes roles. The marker is an in-process
object, not an HTTP header, and is issued only by the token gate. The approved
existing owner is never asked for a password: the native launch itself (this
launch's secret token, a direct loopback request, the exact bound account) is its
verification. A recipient signs in with its district account's own password.
Hosted deployments never use this boundary.
"""
from ipaddress import ip_address
from django.conf import settings
from django.http import JsonResponse
from pathlib import Path
from .desktop_principal import Principal, binding_for

MARKER_KEY = 'terrier.desktop.boundary'
_ISSUER = object()
_FORWARDED = ('HTTP_FORWARDED', 'HTTP_X_FORWARDED_FOR', 'HTTP_X_FORWARDED_HOST', 'HTTP_X_FORWARDED_PROTO')


def _direct(environment, origin):
    try:
        if not ip_address(environment.get('REMOTE_ADDR', '')).is_loopback:
            return False
    except ValueError:
        return False
    return (environment.get('HTTP_HOST') == origin.removeprefix('http://')
            and not any(environment.get(key) for key in _FORWARDED)
            and environment.get('HTTP_ORIGIN', '') in ('', origin)
            and environment.get('HTTP_SEC_FETCH_SITE', '') in ('', 'same-origin', 'none'))


class DesktopBoundary:
    """Called by GuardedWSGI only after validating this launch's secret token."""
    def __init__(self, district, port, principal=None):
        if district not in {'co-05', 'or-05', 'ct-01'} or type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError('Invalid native desktop boundary')
        self.district = district
        self.origin = 'http://127.0.0.1:' + str(port)
        self.principal = principal

    def authorize_environment(self, environment):
        environment.pop(MARKER_KEY, None)
        if (not isinstance(self.principal, Principal) or self.principal.district_id != self.district
                or settings.HOSTED or not getattr(settings, 'DESKTOP_BOUNDARY', False)
                or self.district != settings.DISTRICT['id'] or not _direct(environment, self.origin)):
            return False
        environment[MARKER_KEY] = (_ISSUER, self.district, self.origin, self.principal)
        return True


def request_principal(request):
    """Only the private object inserted by this launch's token gate is trusted."""
    if settings.HOSTED or not getattr(settings, 'DESKTOP_BOUNDARY', False):
        return None
    marker = request.META.get(MARKER_KEY)
    if (not isinstance(marker, tuple) or len(marker) != 4 or marker[0] is not _ISSUER
            or marker[1] != settings.DISTRICT['id'] or not _direct(request.META, marker[2])
            or not isinstance(marker[3], Principal) or marker[3].district_id != marker[1]):
        return None
    return marker[3]


def password_lock_disabled(principal):
    """True only for the approved existing owner behind the native boundary. The desktop client reads it from
    /api/auth/session so a Windows lock does not ask the owner for a password either."""
    return bool(isinstance(principal, Principal) and principal.mode == 'existing-owner' and not settings.HOSTED
                and getattr(settings, 'DESKTOP_BOUNDARY', False))


def account_authorized(request, user):
    """The bound account: the existing owner by the native launch alone, a recipient signed in by its password."""
    principal = request_principal(request)
    binding = binding_for(principal) if principal else None
    if not (binding and binding.user_id is not None and user and user.is_authenticated
            and user.pk == binding.user_id and user.username == binding.local_username
            and user.is_active and user.is_staff):
        return False
    if principal.mode == 'existing-owner':
        return password_lock_disabled(principal) and str(user.pk) == principal.local_user_id
    return user.has_usable_password()


def administrator_authorized(request):
    """Existing app administrator role AND the approved account's exact local PK."""
    return account_authorized(request, getattr(request, 'user', None))


def _login_asset(request):
    if request.method not in ('GET', 'HEAD'):
        return False
    if request.path in {'/', '/desktop-signin', '/people', '/method', '/contacts', '/criteria', '/sources', '/workspace', '/walk'}:
        return True
    # Serve only existing immutable frontend files needed by the login interface.
    if not request.path.startswith(('/assets/', '/brand/')) and request.path != '/favicon.ico':
        return False
    root = (Path(settings.BASE_DIR) / 'frontend' / 'dist').resolve()
    candidate = (root / request.path.lstrip('/')).resolve()
    return (candidate.is_relative_to(root) and candidate.is_file()
            and candidate.suffix.lower() in {'.js', '.css', '.png', '.svg', '.ico', '.webp', '.woff', '.woff2', '.mp4'})


class DesktopPrincipalMiddleware:
    """After Django authentication: approved launch alone never exposes app data."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.HOSTED or not getattr(settings, 'DESKTOP_BOUNDARY', False):
            return self.get_response(request)
        principal = request_principal(request)
        binding = binding_for(principal) if principal else None
        if not binding:
            return JsonResponse({'error': 'Desktop approval is required.'}, status=403)
        if principal.mode == 'existing-owner':
            # The owner is the bound account itself, never a password sign-in or a district session cookie.
            from django.contrib.auth.models import AnonymousUser
            request.user = binding.user if binding.user else AnonymousUser()
        if request.path == '/api/health' and request.method in ('GET', 'HEAD'):
            return JsonResponse({'status': 'ok'})
        auth = {'/api/auth/session': {'GET', 'HEAD'}, '/api/auth/login': {'POST'},
                '/api/auth/logout': {'POST'}, '/api/auth/accept': {'POST'}}
        if request.method in auth.get(request.path, set()) or _login_asset(request):
            return self.get_response(request)
        if not administrator_authorized(request):
            return JsonResponse({'error': 'Sign in with your approved account to continue.'}, status=401)
        return self.get_response(request)
