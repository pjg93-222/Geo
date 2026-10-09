"""Aggregate v3 endpoints: the area index layers of a cu-summary-v4 snapshot (no person-level data).

  GET /api/v3/meta                                         static facts, the score-store status, the demo flag
  GET /api/v3/districts                                    the districts of the header selector, each with its instance port
  GET /api/v3/features                                     this district's settings: industries, candidate, overlay layers, street plan
  GET /api/v3/density                                      DATA_DIR/density.json (population and voter density per area), 404 until built
  GET /api/v3/areas/<snapshot>/<basis>/<level>.json        columnar values of one level (registration or residence)
  GET /api/v3/parties/<snapshot>/<basis>/<level>.json      people per party code in each area of one level (O-10)
  GET /api/v3/geometry/<snapshot>/<level>/<coarse|fine>.json  polygons with label points

Areas and geometry are immutable per snapshot (the key is in the URL): `private, max-age=31536000, immutable`, a strong
ETag and a precomputed gzip body. They are available to any signed-in user like the other aggregate endpoints. Nothing
here reads the person table; the only score-store fields used are the totals and the district rate.
"""
import json
import logging
from functools import lru_cache
from pathlib import Path

from django.conf import settings

from tools import area_layers as AL
from tools import districts as D
from tools import field_labels as FL
from tools import scores_schema as S

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
    payload['source'] = 'live'
    payload['data_origin'] = 'local-source-and-model-output'
    payload['demo'] = bool(settings.DEMO_MODE or parameters.get('demo') == '1')
    if info is not None:
        payload['people'] = int(info.meta['n_people'])
        payload['households'] = int(info.meta['n_households'])
        payload['district_parties'] = info.party_counts()
        payload['district_rate'] = {g: info.meta['district']['top10_rate'][g] for g in ('ovr', 'cua', 'part', 'soi')}
        payload['district_mean'] = {'score': info.meta['district']['score_mean'],
                                    'measured': info.meta['district']['measured_mean'],
                                    'lean': info.meta['district']['lean_mean']}
    else:
        payload['households'] = None
        payload['district_rate'] = {g: 0.1 for g in ('ovr', 'cua', 'part', 'soi')}
    return json_etag_response(request, payload, REVALIDATE)


@get_only
@signed_in
def districts(request):
    """The districts of the header selector. One server process serves one district (settings.DISTRICT); each other
    district has its own instance at its port. `ready`: the district's data directory (this process's DATA_DIR for the
    current district) holds scores.sqlite3, walk.sqlite3 and a snapshot, so choosing it opens a working app."""
    params(request, set())
    current = settings.DISTRICT['id']
    listed = []
    for d in D.DISTRICTS.values():
        directory = Path(settings.DATA_DIR) if d['id'] == current else D.data_dir(d, settings.LOCAL_ROOT)
        ready = (all((directory / name).is_file() for name in ('scores.sqlite3', 'walk.sqlite3'))
                 and any((directory / 'snapshots').glob(d['snapshot_prefix'] + '*')))
        listed.append({'id': d['id'], 'label': d['label'], 'short_label': d['short_label'], 'state': d['state'],
                       'port': d['port'], 'ready': ready, 'center': list(d['center']), 'zoom': d['zoom']})
    return json_etag_response(request, {'schema': 'v3-districts-1', 'current': current, 'districts': listed}, REVALIDATE)


def _store_has_support():
    """True when the score store was built with candidate support (scores_writer records `support` in its metadata)."""
    from . import scoresdb
    try:
        return 'support' in scoresdb.info().meta
    except scoresdb.StoreError:
        return False


@get_only
@signed_in
def features(request):
    """The settings of the district this process serves (tools/districts.py): configuration only, never a person. A candidate
    district also names its candidate, race mode and street-plan presets; `sup` is an overlay layer only once the score store
    holds the support table, and the density layers only once density.json is built."""
    params(request, set())
    d = settings.DISTRICT
    built = (Path(settings.DATA_DIR) / 'density.json').is_file()
    layers = ['ovr', 'cua', 'part', 'soi', 'lean']
    if d['candidate'] and _store_has_support():
        layers.append('sup')
    if built:
        layers += ['pop_density', 'voter_density']
    candidate = d['candidate'] and {
        'name': d['candidate'], 'party': d['party'], 'party_label': FL.PARTY_CODES[d['party']], 'race_mode': d['race_mode'],
        'primary_universe': d['primary_universe'], 'signals': d['support']['signals']}
    roi = d['roi'] and {**d['roi'], 'presets': [{'id': i, 'label': FL.ROI_PRESETS[i][0], 'formula': FL.ROI_PRESETS[i][1]}
                                               for i in d['roi']['presets']]}
    industries = [{'id': i, 'label': FL.INDUSTRIES[i]['label'],
                   'fields': [{'name': n, 'label': t, 'weight': w} for n, t, w in FL.INDUSTRIES[i]['fields']]}
                  for i in d['industries']]
    return json_etag_response(request, {
        'schema': 'v3-features-1', 'district': d['id'], 'industries': industries, 'industry': d['industries'][0],
        'candidate': candidate, 'party_codes': FL.PARTY_CODES,
        'overlay': {'layers': layers, 'density': built}, 'roi': roi}, REVALIDATE)


@lru_cache(maxsize=2)
def _density_blob(path, mtime_ns, size):
    return v3_cache.Blob(Path(path).read_bytes())


@get_only
@signed_in
def density(request):
    """density.json of this district's data directory (tools/build_density.py), served as it is."""
    params(request, set())
    path = Path(settings.DATA_DIR) / 'density.json'
    try:
        stat = path.stat()
    except OSError:
        return error('Density is not built yet.', 404)
    return blob_response(request, _density_blob(str(path), stat.st_mtime_ns, stat.st_size), REVALIDATE)


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


@lru_cache(maxsize=12)
def _parties_blob(path, stamp, key, basis, level):
    """One level's party counts, worked out from the score store on first use (display only): the store's path and stamp
    are in the key, so a rebuilt store is read again."""
    from . import scoresdb
    by_area = scoresdb.party_counts_by_area(S.AREA_LEVEL_OF[(basis, level)])
    ids = sorted(by_area)
    return v3_cache.Blob(json.dumps({'schema': 'v3-parties-1', 'snapshot': key, 'basis': basis, 'level': level,
                                     'ids': ids, 'parties': [by_area[i] for i in ids]},
                                    separators=(',', ':'), allow_nan=False).encode('utf-8'))


@get_only
@signed_in
def parties(request, snap, basis, level):
    """O-10: how many people hold each party code in each area of a level ('' = no code). An area with no people is absent.
    Every area is counted whatever its size; the sums are the area's people. Revalidated, not immutable: the counts come
    from the score store, which can be rebuilt under the same snapshot."""
    params(request, set())
    if level not in AL.BASIS_LEVELS.get(basis, ()):
        return error('Unknown area level.', 404)
    found = v4_snapshot(snap)
    if found is None:
        return error('Unknown snapshot.', 404)
    status, info = store_status(found[1])
    if info is None or status['status'] != 'ok':
        return error('The score store is unavailable.', 404)
    return blob_response(request, _parties_blob(str(info.path), info.stamp, found[0], basis, level), REVALIDATE)


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
