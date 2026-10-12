"""Field staff: the one way a hosted deployment opens person-level routes (ruling 8: walk lists and door-knock results).

Every person-level route is local_only (street_views.py): the passwordless owner on this machine, nobody else. Hosted there
is no such owner, so those routes answer 403 to everyone, except the three the walk-list flow needs, which pass
`field=True` and are then open to an account that is all of:
  * signed in, active, and in the Django group "field-staff" (granted when the invitation is made: manage.py invite
    --field-staff). The accounts database is this district's alone, so the group is district staff by construction;
  * the person the verified Cloudflare Access token names (the middleware in access.py, which must have run);
  * under the per-user rate limit.
Opened: GET /api/v3/walk/lists (titles and counts), GET /api/v3/walk/lists/<key> (a saved list), POST /api/v3/walk/dispositions.
Each opened read or write leaves an AuditEvent (who, route, list key, voter keys written: never a name, address or phone).
Everything else, including building, exporting and scoring lists, contacts, search and the workspace, stays closed.
"""
import threading
import time
from collections import defaultdict, deque

from django.conf import settings
from django.contrib.auth.models import Group

from .models import AuditEvent

GROUP = 'field-staff'
WINDOW = 60.0       # seconds the rate limit looks back

_lock = threading.Lock()
# ponytail: counted per process. The district runs one waitress process, so this is the user's ceiling; a second worker
# or replica would multiply it. Move the counters into the database (or Redis) if the app is ever scaled out.
_recent = defaultdict(deque)


def reset_rate_limits():
    with _lock:
        _recent.clear()


def grant(user):
    group, _ = Group.objects.get_or_create(name=GROUP)
    user.groups.add(group)


def is_field_staff(user):
    return user.is_authenticated and user.is_active and user.groups.filter(name=GROUP).exists()


def limited(who, now=None):
    """True when `who` has used up this minute's allowance; otherwise counts the request."""
    now = time.monotonic() if now is None else now
    with _lock:
        seen = _recent[who.casefold()]
        while seen and now - seen[0] >= WINDOW:
            seen.popleft()
        if len(seen) >= settings.FIELD_RATE_PER_MINUTE:
            return True
        seen.append(now)
        return False


def denied(request):
    """The JSON error response unless this hosted request may use a field route, else None."""
    from .street_views import response     # street_views imports this module lazily, so import it the same way
    who = getattr(request, 'access_email', None)
    if not settings.HOSTED or not who or who.startswith('service:'):
        return response({'error': 'Field access needs a Cloudflare Access sign-in.'}, 403)
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return response({'error': 'Sign in to continue'}, 401)
    if not is_field_staff(user):
        return response({'error': 'This account is not field staff for this district.'}, 403)
    if limited(who):
        result = response({'error': 'Too many requests. Wait a minute and try again.'}, 429)
        result['Retry-After'] = str(int(WINDOW))
        return result
    return None


def audit(request, action, **detail):
    """One AuditEvent for an opened read or write when hosted; nothing locally. Keys and counts only, never a name."""
    if settings.HOSTED:
        AuditEvent.objects.create(actor=request.access_email or request.user.username, action=action,
                                  detail={'route': request.path, **detail})
