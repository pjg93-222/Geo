"""Aggregate v3 endpoints: the area index layers of a cu-summary-v4 snapshot (no person-level data).

  GET /api/v3/meta                                         static facts, the score-store status, the demo flag
  GET /api/v3/areas/<snapshot>/<basis>/<level>.json        columnar values of one level (registration or residence)
  GET /api/v3/geometry/<snapshot>/<level>/<coarse|fine>.json  polygons with label points

Areas and geometry are immutable per snapshot (the key is in the URL): `private, max-age=31536000, immutable`, a strong
ETag and a precomputed gzip body. They are available to any signed-in user like the other aggregate endpoints. Nothing
here reads the person table; the only score-store fields used are the totals and the district rate.
"""
import json
import logging
from functools import lru_cache

from django.conf import settings

from tools import area_layers as AL

from . import v3_cache
from .models import Snapshot
from .v3_http import (BadRequest, IMMUTABLE, REVALIDATE, blob_response, error, get_only, json_etag_response, params,
                      signed_in)

log = logging.getLogger('cu.v3')

SCHEMA = 'cu-summary-v4'


def v4_snapshot(key=None):
    """(key, manifest) of the requested or active snapshot when it is cu-summary-v4, else None."""
    query = Snapshot.objects.filter(key=key) if key else Snapshot.objects.filter(active=True)
    snap = query.first()
    if snap is None or snap.manifest.get('schema') != SCHEMA:
        return None
    return snap.key, snap.manifest


@lru_cache(maxsize=4)
def _static_meta(key):
    return json.loads(v3_cache.meta_blob(key).body)


def store_status(manifest):
    """How the score store relates to the active bundle: aggregate totals only, never a person."""
    from . import scoresdb
    try:
        info = scoresdb.info()
    except scoresdb.StoreError as problem:
        return {'status': 'missing', 'note': str(problem)}, None
    recorded = info.meta.get('area_table_sha256')
    expected = manifest['row_framework']['area_table_sha256']
    if recorded != expected:
        return {'status': 'mismatch', 'note': 'The score store and the area layers use different area tables.'}, None
    return {'status': 'ok', 'note': ''}, info


@get_only
@signed_in
def meta(request):
    parameters = params(request, {'demo'})
    if parameters.get('demo', '1') != '1':
        raise BadRequest('Invalid demo flag')
    found = v4_snapshot()
    if found is None:
        return error('No row-framework snapshot is active.', 404)
    key, manifest = found
    payload = dict(_static_meta(key))
    status, info = store_status(manifest)
    payload['store'] = status
    payload['demo'] = bool(settings.DEMO_MODE or parameters.get('demo') == '1')
    if info is not None:
        payload['people'] = int(info.meta['n_people'])
        payload['households'] = int(info.meta['n_households'])
        payload['district_rate'] = {g: info.meta['district']['top10_rate'][g] for g in ('ovr', 'cua', 'part', 'soi')}
        payload['district_mean'] = {'score': info.meta['district']['score_mean'],
                                    'measured': info.meta['district']['measured_mean'],
                                    'lean': info.meta['district']['lean_mean']}
    else:
        payload['households'] = None
        payload['district_rate'] = {g: 0.1 for g in ('ovr', 'cua', 'part', 'soi')}
    return json_etag_response(request, payload, REVALIDATE)


def _snapshot_or_404(key):
    found = v4_snapshot(key)
    if found is None:
        raise LookupError('Unknown snapshot')
    return found[0]


@get_only
@signed_in
def areas(request, snap, basis, level):
    params(request, set())
    if level not in AL.BASIS_LEVELS.get(basis, ()):
        return error('Unknown area level.', 404)
    try:
        key = _snapshot_or_404(snap)
    except LookupError:
        return error('Unknown snapshot.', 404)
    return blob_response(request, v3_cache.areas_blob(key, basis, level), IMMUTABLE)


@get_only
@signed_in
def geometry(request, snap, level, variant):
    params(request, set())
    if level not in AL.LEVEL_ORDER or variant not in ('coarse', 'fine'):
        return error('Unknown geometry.', 404)
    try:
        key = _snapshot_or_404(snap)
    except LookupError:
        return error('Unknown snapshot.', 404)
    return blob_response(request, v3_cache.geometry_blob(key, level, variant), IMMUTABLE)
