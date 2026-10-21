"""Field staff: the one way a hosted deployment opens person-level routes (ruling 8: walk lists and door-knock results).

Every person-level route is local_only (street_views.py): the passwordless owner on this machine, nobody else. Hosted there
is no such owner, so those routes answer 403 to everyone, except the four the walk-list flow needs, which pass
`field=GROUP` and are then open to an account that is all of:
  * signed in, active, and in the Django group "field-staff" (granted when the invitation is made: manage.py invite
    --field-staff). The accounts database is this district's alone, so the group is district staff by construction;
  * the person the verified Cloudflare Access token names (the middleware in access.py, which must have run);
  * under the per-user rate limit.
Opened: GET /api/v3/walk/lists (titles and counts), GET /api/v3/walk/lists/<key> (a saved list), POST /api/v3/walk/dispositions,
and POST /api/v3/walk/opt-ins (explicitly recorded donor/volunteer status, independent of visit results).
Each opened read or write leaves an AuditEvent (who, route, list key, voter keys written: never a name, address or phone).
Everything else, including scoring lists, contacts, search and the workspace, stays closed.

Field managers: the group "field-manager" (manage.py invite --field-manager; nobody by default, and not implied by field-staff)
opens the builder routes and nothing else: walk/{options,streets,match,list,overview,plan} and POST walk/lists, so a list can be
made on the server, where the saved lists live. Same sign-in, same Access identity, same rate limit, and each call leaves an
AuditEvent (route only: never a parameter, a name, an address or a phone).
"""
import threading
import time
from collections import defaultdict, deque

from django.conf import settings
from django.contrib.auth.models import Group

from .models import AuditEvent

GROUP = 'field-staff'
MANAGER = 'field-manager'
WINDOW = 60.0       # seconds the rate limit looks back

_lock = threading.Lock()
# ponytail: counted per process. The district runs one waitress process, so this is the user's ceiling; a second worker
# or replica would multiply it. Move the counters into the database (or Redis) if the app is ever scaled out.
_recent = defaultdict(deque)


def reset_rate_limits():
    with _lock:
        _recent.clear()


def grant(user, group=GROUP):
    user.groups.add(Group.objects.get_or_create(name=group)[0])


def in_group(user, group):
    return user.is_authenticated and user.is_active and user.groups.filter(name=group).exists()


def is_field_staff(user):
    return in_group(user, GROUP)


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


def denied(request, group=GROUP):
    """The JSON error response unless this hosted request may use a route of `group` (field-staff or field-manager), else None."""
    from .street_views import response     # street_views imports this module lazily, so import it the same way
    who = getattr(request, 'access_email', None)
    if not settings.HOSTED or not who or who.startswith('service:'):
        return response({'error': 'Field access needs a Cloudflare Access sign-in.'}, 403)
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return response({'error': 'Sign in to continue'}, 401)
    if not in_group(user, group):
        return response({'error': 'This account is not %s for this district.' % group.replace('-', ' ')}, 403)
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
