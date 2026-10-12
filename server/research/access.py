"""Cloudflare Access in front of a hosted deployment: no request reaches Django without a verified Access identity.

AccessJwtMiddleware is installed first when CU_DEPLOYMENT=hosted (server/settings.py). It hands every request to the shared
access_gate (RS256 token, exact aud and iss, allowed Host) and answers 403 to anything the gate refuses. Once the session
layer has run (process_view), a signed-in Django account must be the person the token names: the JWT email must equal the
account's email (its username when it has none), ignoring case, else the session is ended and the answer is 403. So a
session cookie stolen or carried to another Access identity is useless, and an Access identity with no account has no data.

Environment (read once, at startup; a missing or malformed value stops the server):
  ACCESS_AUD, ACCESS_ISSUER, ACCESS_JWKS_FILE, ACCESS_SERVICE_CNS   as access_gate.py describes them
  ALLOWED_HOSTS                                                     the gate's exact Host names; defaults to CU_ALLOWED_HOSTS
The gate is always enforced here, whatever CU_BIND is. /api/health and /version (the container's own probes) are the routes a
loopback peer may reach without a token, by the gate's rule for /healthz and /version; a request that carries proxy headers must
still have one. /version answers with version info only (access_gate.version_payload: git sha, build time, snapshot id).

One audit line per request goes to the cu.access logger: when, app, who, method, route without its query, status (and a
fixed denial code). Never the query string, the client address, a header or a body.
"""
import logging
import os

from django.conf import settings
from django.contrib.auth import logout
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse

from . import access_gate as gate

log = logging.getLogger('cu.access')
HEALTH = '/api/health'      # Terrier's health route; the gate knows the same probe as /healthz


def load_config(environ=None):
    """The gate's AccessConfig for a hosted deployment: enforced, with ALLOWED_HOSTS defaulting to CU_ALLOWED_HOSTS."""
    env = dict(os.environ if environ is None else environ)
    env['ACCESS_ENFORCE'] = '1'
    if not (env.get('ALLOWED_HOSTS') or '').strip():
        env['ALLOWED_HOSTS'] = env.get('CU_ALLOWED_HOSTS', '')
    try:
        return gate.AccessConfig.from_env(env)
    except gate.AccessConfigError as problem:
        raise ImproperlyConfigured('Cloudflare Access: %s' % problem) from None


def account_email(user):
    return (user.email or user.username).strip()


class AccessJwtMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.cfg = load_config()
        self.app = 'terrier-' + settings.DISTRICT['id']

    def __call__(self, request):
        request.access_email = None         # the verified Access identity; None until the gate has passed the request
        request.access_reason = None
        # The gate's loopback exemption is for /healthz and /version. Here /api/health (mapped onto /healthz) and /version (served by
        # views.version) may use it; the gate's own /healthz, which this app does not have, is changed to a path exempt from nothing.
        # The gate wants real header names and path plus query: request.headers and request.get_full_path(). Never request.META
        # as the headers: the gate denies HTTP_* names. (META is read for the peer address only.)
        path, mark, query = request.get_full_path().partition('?')     # a "?" inside the path is percent-encoded by then
        path = '/healthz' if path == HEALTH else '/' if path == '/healthz' else path
        decision = gate.check_request(self.cfg, request.method, path + mark + query, request.headers,
                                      request.META.get('REMOTE_ADDR', ''))
        if not decision.allowed:
            request.access_reason = decision.reason
            response = self.refuse()
        else:
            request.access_email = decision.user
            response = self.get_response(request)
        log.info(gate.audit_line(self.app, request.access_email, request.method, request.path, response.status_code,
                                 request.access_reason))
        return response

    def process_view(self, request, view_func, view_args, view_kwargs):
        """Runs after the session and authentication middleware: a signed-in account must be the Access identity."""
        user = getattr(request, 'user', None)
        who = getattr(request, 'access_email', None)
        if who is None or user is None or not user.is_authenticated:
            return None
        if who.casefold() != account_email(user).casefold():
            logout(request)
            request.access_reason = 'session user'
            return self.refuse()
        return None

    def refuse(self):
        response = HttpResponse('forbidden\n', status=403, content_type='text/plain; charset=utf-8')
        for name, value in gate.security_headers(self.cfg):
            response[name] = value
        return response
