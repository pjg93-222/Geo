"""Small HTTP helpers shared by the v3 endpoints: exact query whitelists, ETag/304, gzip and error shapes."""
import json
import re
from functools import wraps

from django.http import HttpResponse, JsonResponse

IMMUTABLE = 'private, max-age=31536000, immutable'
REVALIDATE = 'private, no-cache'
NO_STORE = 'no-store, private'


class BadRequest(ValueError):
    """An unknown, missing or duplicate parameter, or a value outside its allowed set."""


def params(request, allowed, required=()):
    """The query as {name: single value}. Unknown, duplicate and missing-required names are rejected."""
    names = set(request.GET)
    if not names <= set(allowed) or not set(required) <= names:
        raise BadRequest('Unexpected or missing query parameter')
    for name in names:
        if len(request.GET.getlist(name)) != 1:
            raise BadRequest('Duplicate query parameter')
    return {name: request.GET[name] for name in names}


def error(message, status):
    response = JsonResponse({'error': message}, status=status)
    response['Cache-Control'] = NO_STORE
    return response


def _etag_matches(header, etags):
    if not header:
        return False
    if header.strip() == '*':
        return True
    candidates = {part.strip() for part in header.split(',')}
    candidates |= {c[2:] if c.startswith('W/') else c for c in candidates}
    return bool(candidates & set(etags))


def _accepts_gzip(request):
    value = request.META.get('HTTP_ACCEPT_ENCODING', '')
    for token in value.split(','):
        name, _, quality = token.strip().partition(';')
        if name.strip().lower() in ('gzip', 'x-gzip', '*'):
            if quality.strip().startswith('q=') and quality.strip()[2:].strip() in ('0', '0.0', '0.00', '0.000'):
                continue
            return True
    return False


def blob_response(request, made, cache_control):
    """Serve a v3_cache.Blob: strong ETag per encoding, 304 on If-None-Match, gzip when accepted."""
    gzip_etag = made.etag[:-1] + '-gzip"'
    if _etag_matches(request.META.get('HTTP_IF_NONE_MATCH'), (made.etag, gzip_etag)):
        response = HttpResponse(status=304)
        response['ETag'] = gzip_etag if _accepts_gzip(request) else made.etag
    else:
        if _accepts_gzip(request):
            response = HttpResponse(made.gz, content_type='application/json')
            response['Content-Encoding'] = 'gzip'
            response['ETag'] = gzip_etag
        else:
            response = HttpResponse(made.body, content_type='application/json')
            response['ETag'] = made.etag
    response['Cache-Control'] = cache_control
    response['Vary'] = 'Accept-Encoding'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def json_etag_response(request, payload, cache_control=REVALIDATE):
    """A small per-request JSON body (for example /api/v3/meta) with an ETag over its bytes."""
    from .v3_cache import Blob
    return blob_response(request, Blob(json.dumps(payload, separators=(',', ':'), allow_nan=False).encode('utf-8')),
                         cache_control)


def get_only(function):
    """GET and HEAD only; everything else is 405."""
    @wraps(function)
    def wrapped(request, *args, **kwargs):
        if request.method not in ('GET', 'HEAD'):
            response = error('GET is required.', 405)
            response['Allow'] = 'GET, HEAD'
            return response
        return function(request, *args, **kwargs)
    return wrapped


def signed_in(function):
    """Signed-in users only (loopback is the local owner); a BadRequest becomes a 400 with a neutral message."""
    @wraps(function)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return error('Sign in to continue', 401)
        try:
            return function(request, *args, **kwargs)
        except BadRequest:
            return error('Invalid request.', 400)
    return wrapped


def clean(value, pattern):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise BadRequest('Invalid value')
    return value
