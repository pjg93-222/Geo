"""Precomputed, immutable payloads of a cu-summary-v4 snapshot (S1: precomputed per-level payloads, ETags, memo).

A snapshot is immutable, so everything derived from it is a pure function of its files. Each payload is built once,
written under `snapshots/<key>/derived/` (JSON and gzip, atomically) and kept in memory with its strong ETag. Requests
then cost a dictionary lookup. `prepare(key)` builds every payload; the importer and the server's warm thread call it.

Payloads (all aggregate; none contains a person):
  areas_<basis>_<level>   columnar values of one area level (the wire format of AreasPayload)
  geometry_<level>_<coarse|fine>   a GeoJSON FeatureCollection with label points
  meta                    the static part of /api/v3/meta
"""
import gzip
import hashlib
import json
import logging
import math
import re
import threading
from pathlib import Path

from django.conf import settings

from tools import area_layers as AL
from tools import districts as D
from tools.geography_levels import area_label

log = logging.getLogger('cu.v3')

DERIVED_VERSION = 'v2'
COARSE, FINE = 0.00055, 0.00006          # simplification tolerances in degrees (about 55 m and 6 m)
COORD_DECIMALS = 5
LEVELS = AL.LEVEL_ORDER
SNAPSHOT_RE = re.compile(D.snapshot_pattern(settings.DISTRICT))
VALUE_DECIMALS = 8                         # values are served at 8 decimals ...
SHARE_DECIMALS = 8
DISPLAY_SCALES = (1, 100, 1000)            # ... and at full precision where 8 decimals would change a displayed digit
OVERALL_NOTE = 'Interim overall: candidate support not yet included; final weights 30/25/25/20 arrive with candidate support'


class Blob:
    """One payload: its JSON bytes, gzip bytes and strong ETag."""
    __slots__ = ('body', 'gz', 'etag')

    def __init__(self, body, gz=None):
        self.body = body
        self.gz = gz if gz is not None else gzip.compress(body, compresslevel=6, mtime=0)
        self.etag = '"%s"' % hashlib.sha256(body).hexdigest()[:32]


_memory = {}
_locks = {}
_guard = threading.Lock()


def _lock(key, name):
    with _guard:
        return _locks.setdefault((key, name), threading.Lock())


def snapshot_dir(key):
    if not SNAPSHOT_RE.fullmatch(key):
        raise ValueError('Invalid snapshot key')
    return Path(settings.DATA_DIR) / 'snapshots' / key


def derived_path(key, name):
    return snapshot_dir(key) / 'derived' / ('%s_%s.json' % (DERIVED_VERSION, name))


def _write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.part')
    temporary.write_bytes(data)
    temporary.replace(path)


def _from_disk(key, name):
    path = derived_path(key, name)
    gz_path = path.with_name(path.name + '.gz')
    if path.exists() and gz_path.exists():
        return Blob(path.read_bytes(), gz_path.read_bytes())
    return None


def _store(key, name, value):
    made = Blob(json.dumps(value, separators=(',', ':'), allow_nan=False).encode('utf-8'))
    path = derived_path(key, name)
    _write_atomic(path, made.body)
    _write_atomic(path.with_name(path.name + '.gz'), made.gz)
    _memory[(key, name)] = made
    return made


def blob(key, name, builder):
    """The cached payload, from memory, then disk, else built (once, under a per-payload lock)."""
    memo = _memory.get((key, name))
    if memo is not None:
        return memo
    with _lock(key, name):
        memo = _memory.get((key, name))
        if memo is not None:
            return memo
        made = _from_disk(key, name)
        if made is None:
            return _store(key, name, builder())
        _memory[(key, name)] = made
        return made


def forget(key=None):
    """Drop in-memory payloads (tests, re-import)."""
    with _guard:
        for k in [k for k in _memory if key is None or k[0] == key]:
            del _memory[k]


# ---- readers -----------------------------------------------------------------------------------------------------

def read_json(key, filename):
    """A snapshot file parsed without keeping it in the shared lru cache (geometry is large)."""
    return json.loads((snapshot_dir(key) / filename).read_text(encoding='utf-8'))


def manifest_of(key):
    return read_json(key, 'manifest.json')


def area_rows(key):
    """The area-index rows of a snapshot (shared lru cache in bundles.load; about 10 MB)."""
    from .bundles import load
    return load(key, 'area_indices.json')['rows']


# ---- areas -------------------------------------------------------------------------------------------------------

def half_away(x):
    """sign(x) * floor(|x| + 0.5): the display rounding of the framework (half goes away from zero)."""
    return math.copysign(math.floor(abs(x) + 0.5), x) if x else 0.0


def _r(value, decimals):
    """Round for the wire, but never in a way that changes a displayed number: a value so close to a rounding boundary
    that `decimals` would move it across (whole points, whole percent of a share, tenths of a percent of a share) is
    sent at full precision."""
    if value is None:
        return None
    value = float(value)
    short = round(value, decimals)
    if any(half_away(short * scale) != half_away(value * scale) for scale in DISPLAY_SCALES):
        return value
    return short


def build_areas(key, basis, level):
    from .bundles import load
    rows = [r for r in area_rows(key) if r['basis'] == basis and r['level'] == level]
    by_id = {r['id']: r for r in rows}
    ids = set(by_id)
    adjacency = {}
    if basis == 'registration':
        geometry = read_json(key, 'geometry.json')
        key_geo = {'block': 'block', 'precinct': 'precinct', 'tract': 'tract', 'zip': 'zcta'}[level]
        ids |= set(geometry[key_geo])
        del geometry
        if level in AL.ADJACENCY_LEVELS:
            adjacency = load(key, 'adjacency.json').get(level, {})
    ids = sorted(ids)
    columns = {g: {name: k for k, name in enumerate(AL.COLUMNS[g])} for g in AL.GROUPS}
    out = {
        'schema': 'v3-areas-1', 'snapshot': key, 'basis': basis, 'level': level, 'ids': ids,
        'labels': [area_label(level, i, settings.DISTRICT) for i in ids],
        'status': [by_id[i]['status'] if i in by_id else 'withheld' for i in ids],
        'n': [], 'households': [], 'parent': [], 'precinct': [], 'tract': [], 'blocks': [],
    }
    if adjacency or level in AL.ADJACENCY_LEVELS:
        out['neighbours'] = [adjacency.get(i, []) for i in ids]
    cells = {g: {name: [] for name in ('mean', 'se', 'measured', 'pct', 'top10', 'top10_se', 'top10_pct')}
             for g in ('ovr', 'cua', 'part', 'soi')}
    lean = {name: [] for name in ('mean', 'se', 'measured', 'share_d', 'share_r', 'share_u')}
    for ident in ids:
        row = by_id.get(ident)
        ok = row is not None and row['status'] == 'ok'
        out['n'].append(row['n_rows'] if ok else None)
        out['households'].append(row['n_households'] if ok else None)
        out['parent'].append(ident[:11] if level == 'block' and basis == 'registration' else None)
        out['precinct'].append(row.get('precinct') if ok else None)
        out['tract'].append(row.get('tract') if ok else None)
        out['blocks'].append(row.get('n_blocks') if ok else None)
        for g, target in cells.items():
            if ok:
                c = columns[g]
                v = row['groups'][g]
                target['mean'].append(_r(v[c['eb_mean']], VALUE_DECIMALS))
                target['se'].append(_r(v[c['eb_se']], VALUE_DECIMALS))
                target['measured'].append(_r(v[c['measured_pct_mean']], VALUE_DECIMALS))
                target['pct'].append(_r(v[c['area_pct']], VALUE_DECIMALS))
                target['top10'].append(_r(v[c['top10_share_eb']], SHARE_DECIMALS))
                target['top10_se'].append(_r(v[c['top10_eb_se']], SHARE_DECIMALS))
                target['top10_pct'].append(_r(v[c['top10_area_pct']], VALUE_DECIMALS))
            else:
                for name in target:
                    target[name].append(None)
        if ok:
            c = columns['lean']
            v = row['groups']['lean']
            lean['mean'].append(_r(v[c['eb_mean']], VALUE_DECIMALS))
            lean['se'].append(_r(v[c['eb_se']], VALUE_DECIMALS))
            lean['measured'].append(_r(v[c['measured_pct_mean']], VALUE_DECIMALS))
            for name, column in (('share_d', 'share_reg_D'), ('share_r', 'share_reg_R'), ('share_u', 'share_reg_U')):
                lean[name].append(_r(v[c[column]], SHARE_DECIMALS))
        else:
            for name in lean:
                lean[name].append(None)
    out['groups'] = {**cells, 'lean': lean}
    return out


# ---- geometry ----------------------------------------------------------------------------------------------------

def _round_coords(coords):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], COORD_DECIMALS), round(coords[1], COORD_DECIMALS)]
    return [_round_coords(c) for c in coords]


def build_geometry_pair(key, level):
    """Both variants of one level from the snapshot geometry: {'coarse': FeatureCollection, 'fine': ...}."""
    from shapely.geometry import mapping, shape
    source = read_json(key, 'geometry.json')
    layer = source[{'block': 'block', 'precinct': 'precinct', 'tract': 'tract', 'zip': 'zcta'}[level]]
    result = {'coarse': [], 'fine': []}
    for ident in sorted(layer):
        full = shape(layer[ident]['geometry'])
        point = full.representative_point()
        for variant, tolerance in (('coarse', COARSE), ('fine', FINE)):
            geom = full.simplify(tolerance, preserve_topology=True)
            if geom.is_empty:
                geom = full
            mapped = mapping(geom)
            result[variant].append({
                'type': 'Feature', 'properties': {'id': ident, 'lx': round(point.x, COORD_DECIMALS),
                                                  'ly': round(point.y, COORD_DECIMALS)},
                'geometry': {'type': mapped['type'], 'coordinates': _round_coords(mapped['coordinates'])}})
    return {v: {'type': 'FeatureCollection', 'features': f} for v, f in result.items()}


def geometry_blob(key, level, variant):
    """Coarse and fine variants of a level are built together (one pass over the polygons) under one lock."""
    name = 'geometry_%s_%s' % (level, variant)
    memo = _memory.get((key, name))
    if memo is not None:
        return memo
    with _lock(key, 'geometry_' + level):
        made = _memory.get((key, name)) or _from_disk(key, name)
        if made is None:
            pair = build_geometry_pair(key, level)
            for each, collection in pair.items():
                _store(key, 'geometry_%s_%s' % (level, each), collection)
            made = _memory[(key, name)]
        _memory[(key, name)] = made
        return made


def areas_blob(key, basis, level):
    return blob(key, 'areas_%s_%s' % (basis, level), lambda: build_areas(key, basis, level))


def meta_blob(key):
    return blob(key, 'meta', lambda: build_meta(key))


def build_meta(key):
    manifest = manifest_of(key)
    district = read_json(key, 'district.geojson')
    from shapely.geometry import shape
    bounds = shape(district['features'][0]['geometry']).bounds
    rf = manifest['row_framework']
    return {
        'schema': 'v3-meta-1', 'snapshot': key, 'district_label': settings.DISTRICT['short_label'],
        'share_breaks': list(AL.SHARE_BREAKS), 'lean_cuts': list(AL.LEAN_CUTS),
        'measured_breaks': list(AL.MEASURED_BREAKS), 'household_breaks': list(AL.BREAKS),
        'weights': settings.DISTRICT['interim_overall'] or settings.DISTRICT['score_weights'],
        'levels': list(LEVELS), 'bbox': [round(v, 5) for v in bounds],
        'floors': {'min_n': AL.MIN_N, 'min_households': AL.MIN_HOUSEHOLDS},
        'bases': {basis: list(levels) for basis, levels in AL.BASIS_LEVELS.items()},
        'index_catalog': AL.INDEX_CATALOG, 'layers': AL.LAYERS,
        'row_framework': {'version': rf['version'], 'area_table_sha256': rf['area_table_sha256'],
                          'suppressed_ids_sha256': rf['suppressed_ids_sha256'],
                          'framework_config_sha256': rf['framework_config_sha256']},
        'geometry_method': manifest['geometry_method'],
        'support_likelihood': manifest['support_likelihood'], 'persistent_voices': manifest['persistent_voices'],
        'people': manifest['bases']['registration']['imported'],
        'registered_people': manifest['bases']['registration']['mapped'],
        **({'overall_note': OVERALL_NOTE} if settings.DISTRICT['interim_overall'] else {}),   # absent for Colorado
    }


def prepare(key, progress=None):
    """Build every derived payload of a v4 snapshot (idempotent). Returns {name: bytes}."""
    sizes = {}
    note = progress or (lambda *_: None)
    stale = snapshot_dir(key) / 'derived'
    if stale.is_dir():
        for path in stale.iterdir():
            if not path.name.startswith(DERIVED_VERSION + '_'):
                path.unlink(missing_ok=True)          # payloads of an older builder version
    for basis, levels in AL.BASIS_LEVELS.items():
        for level in levels:
            b = areas_blob(key, basis, level)
            sizes['areas_%s_%s' % (basis, level)] = len(b.body)
            note('areas', basis, level, len(b.body))
    for level in LEVELS:
        for variant in ('coarse', 'fine'):
            b = geometry_blob(key, level, variant)
            sizes['geometry_%s_%s' % (level, variant)] = len(b.body)
            note('geometry', level, variant, len(b.body))
    sizes['meta'] = len(meta_blob(key).body)
    return sizes
