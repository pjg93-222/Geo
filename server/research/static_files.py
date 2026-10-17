"""Serving the built frontend quickly and safely (S1: immutable assets, ETag/304, pre-compressed bodies).

  * `/assets/*` files carry a content hash in their name, so they are `public, max-age=31536000, immutable`.
  * `index.html` (and any other non-hashed file) is `no-cache` with a validator, so a rebuild shows up at once.
  * A `.br` or `.gz` sibling written by frontend/scripts/precompress.mjs is served when the client accepts it and the
    sibling is not older than its source. Without a sibling, text files over 1 KB are gzipped once in memory.
  * ETag is derived from the file's size and modification time (plus the encoding); If-None-Match and If-Modified-Since
    answer 304 without reading the file.
The content-security policy is the one the app has always sent.
"""
import gzip
import mimetypes
import os
import re
import threading
from email.utils import formatdate
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, HttpResponse

CSP = ("default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
       "form-action 'self'")
IMMUTABLE = 'public, max-age=31536000, immutable'
REVALIDATE = 'no-cache'
mimetypes.add_type('font/woff2', '.woff2')
mimetypes.add_type('text/javascript', '.js')
mimetypes.add_type('application/wasm', '.wasm')
COMPRESSIBLE = ('text/', 'application/javascript', 'application/json', 'image/svg+xml', 'application/xml')
MIN_COMPRESS = 1024
_gzip_cache = {}
_gzip_lock = threading.Lock()


def accepted(request):
    """Encodings the client accepts with q > 0."""
    names = set()
    for token in request.META.get('HTTP_ACCEPT_ENCODING', '').split(','):
        name, _, quality = token.strip().partition(';')
        quality = quality.strip()
        if quality.startswith('q=') and quality[2:].strip() in ('0', '0.0', '0.00', '0.000'):
            continue
        names.add(name.strip().lower())
    return names


def _fresh(sibling, source):
    try:
        return sibling.stat().st_mtime_ns >= source.stat().st_mtime_ns
    except OSError:
        return False


def _not_modified(request, etag, mtime):
    match = request.META.get('HTTP_IF_NONE_MATCH')
    if match:
        # weak comparison (RFC 9110 for If-None-Match): GZipMiddleware turns a strong ETag into W/"..." on the way out
        candidates = {part.strip()[2:] if part.strip().startswith('W/') else part.strip() for part in match.split(',')}
        return etag in candidates or match.strip() == '*'
    since = request.META.get('HTTP_IF_MODIFIED_SINCE')
    if since:
        from django.utils.http import parse_http_date_safe
        parsed = parse_http_date_safe(since)
        return parsed is not None and int(mtime) <= parsed
    return False


def _headers(response, etag, mtime, cache_control, vary):
    response['ETag'] = etag
    response['Last-Modified'] = formatdate(mtime, usegmt=True)
    response['Cache-Control'] = cache_control
    if vary:
        response['Vary'] = 'Accept-Encoding'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = CSP


def serve(request, path=''):
    root = (Path(settings.BASE_DIR) / 'frontend' / 'dist').resolve()
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root):
        return HttpResponse(status=404)
    is_asset = bool(path) and candidate.is_file() and path.startswith('assets/')
    if not path or not candidate.is_file():
        candidate = root / 'index.html'
        is_asset = False
    if not candidate.exists():
        return HttpResponse('Frontend not built. Run npm install and npm run build in frontend.', status=503)
    stat = candidate.stat()
    content_type = mimetypes.guess_type(str(candidate))[0] or 'application/octet-stream'
    compressible = content_type.startswith(COMPRESSIBLE)
    wants = accepted(request)
    encoding, source = None, candidate
    if compressible:
        for name, suffix in (('br', '.br'), ('gzip', '.gz')):
            sibling = candidate.with_name(candidate.name + suffix)
            if name in wants and sibling.exists() and _fresh(sibling, candidate):
                encoding, source = name, sibling
                break
        else:
            if 'gzip' in wants and stat.st_size >= MIN_COMPRESS:
                encoding = 'gzip-memory'
    tag = '"%x-%x%s"' % (stat.st_mtime_ns, stat.st_size, '-' + encoding if encoding else '')
    cache_control = IMMUTABLE if is_asset else REVALIDATE
    # The approved loading clip must retain seeking and native resolution when served by Django.
    if content_type == 'video/mp4' and request.META.get('HTTP_RANGE'):
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', request.META['HTTP_RANGE'])
        start, end = 0, stat.st_size - 1
        valid = bool(match and (match[1] or match[2]))
        if valid:
            if match[1]:
                start = int(match[1])
                if match[2]: end = min(int(match[2]), end)
            else:
                start = max(0, stat.st_size - int(match[2]))
            valid = 0 <= start <= end < stat.st_size
        if not valid:
            response = HttpResponse(status=416)
            response['Content-Range'] = 'bytes */%d' % stat.st_size
        else:
            with candidate.open('rb') as stream:
                stream.seek(start)
                response = HttpResponse(b'' if request.method == 'HEAD' else stream.read(end - start + 1), status=206, content_type=content_type)
            response['Content-Length'] = str(end - start + 1)
            response['Content-Range'] = 'bytes %d-%d/%d' % (start, end, stat.st_size)
        response['Accept-Ranges'] = 'bytes'
        response['Content-Encoding'] = 'identity'
        _headers(response, tag, stat.st_mtime, cache_control, False)
        return response
    if _not_modified(request, tag, stat.st_mtime):
        response = HttpResponse(status=304)
        _headers(response, tag, stat.st_mtime, cache_control, compressible)
        return response
    if encoding == 'gzip-memory':
        cache_key = (str(candidate), stat.st_mtime_ns, stat.st_size)
        with _gzip_lock:
            body = _gzip_cache.get(cache_key)
            if body is None:
                body = gzip.compress(candidate.read_bytes(), compresslevel=9, mtime=0)
                _gzip_cache.clear() if len(_gzip_cache) > 64 else None
                _gzip_cache[cache_key] = body
        response = HttpResponse(body, content_type=content_type)
        response['Content-Encoding'] = 'gzip'
    elif encoding:
        response = FileResponse(source.open('rb'), content_type=content_type)
        response['Content-Encoding'] = encoding
    else:
        response = FileResponse(candidate.open('rb'), content_type=content_type)
        if not compressible:
            response['Content-Encoding'] = 'identity'     # already compact (fonts, images): keep GZipMiddleware away
        if content_type == 'video/mp4':
            response['Accept-Ranges'] = 'bytes'
    _headers(response, tag, stat.st_mtime, cache_control, compressible)
    return response
