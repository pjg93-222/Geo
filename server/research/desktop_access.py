"""The native launcher boundary authorizes the exact approved administrator.

Ordinary launches require sign-in. An explicit local owner preference permits
request-only access without changing the account, password, or stored session.
The marker is an in-process object issued only by the token gate.
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
    return bool(principal and principal.mode == 'existing-owner' and not settings.HOSTED
                and getattr(settings, 'DESKTOP_BOUNDARY', False)
                and getattr(settings, 'DESKTOP_PASSWORD_LOCK_DISABLED', False))


def account_authorized(request, user):
    principal = request_principal(request)
    binding = binding_for(principal) if principal else None
    return bool(binding and binding.user_id is not None and user and user.is_authenticated
                and user.pk == binding.user_id and user.username == binding.local_username
                and user.is_active and user.is_staff
                and (user.has_usable_password() or password_lock_disabled(principal)))


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
        if not principal or not binding_for(principal):
            return JsonResponse({'error': 'Desktop approval is required.'}, status=403)
        if password_lock_disabled(principal):
            binding = binding_for(principal)
            owner = binding.user
            if (owner and str(owner.pk) == principal.local_user_id
                    and owner.username == principal.local_username and owner.is_active and owner.is_staff):
                # Request-only access for the exact approved owner; retain account/password/session state.
                request.user = owner
        if request.path == '/api/health' and request.method in ('GET', 'HEAD'):
            return JsonResponse({'status': 'ok'})
        auth = {'/api/auth/session': {'GET', 'HEAD'}, '/api/auth/login': {'POST'},
                '/api/auth/logout': {'POST'}, '/api/auth/accept': {'POST'}}
        if request.method in auth.get(request.path, set()) or _login_asset(request):
            return self.get_response(request)
        if not administrator_authorized(request):
            return JsonResponse({'error': 'Sign in with your approved account to continue.'}, status=401)
        return self.get_response(request)
