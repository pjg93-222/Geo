"""Map context around a walk list (WalkContext in frontend/src/v3/api/walkTypes.ts): the streets, precinct outlines and
homes near its runs. Computed for each response and never stored with a saved list.

Around the list's bbox grown by pad_m metres:
  roads      every block (road_span) with an edge in the box (R*Tree road_edge_box), one polyline each, simplified 4 m
             in district UTM metres with its topology kept (Douglas-Peucker; the topology-preserving simplifier for the few lines
             DP would make cross themselves or collapse). k is the kind, 'V' for a generated street or connector; n is
             the label the voter streets of that name carry, else the name key title-cased, '' when unnamed. At most
             MAX_ROADS: unnamed blocks are dropped first, shortest first.
  precincts  the polygons of the active snapshot's precincts that meet the box: the fine precinct layer the area map
             draws (v3_cache, simplified 0.00006 degrees, about 5-7 m), parsed once per snapshot version
  homes      the distinct stop positions in the box; at most MAX_HOMES, a fixed sample (the same points every time)
Coordinates are [lon, lat] at 6 decimals. The stop table has no spatial index, so the stop positions and street labels
are read once per version of walk.sqlite3 (Index, about 2 MB).
"""
import json
import logging
import math
import os
import threading
from collections import Counter, defaultdict
from pathlib import Path

from django.conf import settings

from . import v3_cache
from . import walk_names as N
from . import walk_store as W
from .terrier_contacts import SourceStoreError
from .v3_area_views import v4_snapshot
from .walk_route import DEG_M

log = logging.getLogger(__name__)

PAD_M = 300                 # metres around the list's bbox
MAX_ROADS = 6000
MAX_HOMES = 8000
ROAD_TOLERANCE_M = 4.0      # simplification of the road lines, district UTM metres
DECIMALS = 6
CHUNK = 900                 # ids per IN (...)
PRECINCTS = ('precinct', 'fine')
LON0, LAT0, LAT_BITS = 180_000_000, 90_000_000, 28     # a position key: (lon + LON0) << LAT_BITS | (lat + LAT0), microdegrees
RANKED = ('SELECT s.id FROM road_span s WHERE s.id IN (SELECT e.span_id FROM road_edge_box x JOIN road_edge e ON '
          'e.id = x.id WHERE x.west<=? AND x.east>=? AND x.south<=? AND x.north>=?) '
          "ORDER BY coalesce(s.name_key, '') = '', s.len_m DESC, s.id LIMIT ?")

_lock = threading.Lock()
_layer = None               # (stamp of the snapshot geometry, Layer)
_index = None               # (stamp of walk.sqlite3, Index)


def reset():
    """Forget the parsed precinct layer and the walk store index (tests, and after a store is replaced)."""
    global _layer, _index
    with _lock:
        _layer = _index = None


def _query(conn, sql, args=()):
    """All rows as plain tuples, whatever the connection's row factory."""
    cursor = conn.cursor()
    cursor.row_factory = None
    try:
        return cursor.execute(sql, args).fetchall()
    finally:
        cursor.close()


def _query_ids(conn, sql, ids):
    rows = []
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start:start + CHUNK]
        rows.extend(_query(conn, sql.format(','.join('?' * len(chunk))), chunk))
    return rows


def padded(bbox, pad_m=PAD_M):
    """[west, south, east, north] grown by pad_m metres (degrees of longitude at the box's mid latitude), rounded
    outwards to DECIMALS."""
    west, south, east, north = (float(v) for v in bbox)
    if not all(map(math.isfinite, (west, south, east, north, pad_m))) or west > east or south > north or pad_m < 0:
        raise ValueError('Invalid bbox')
    dlat = pad_m / DEG_M
    dlon = dlat / max(math.cos(math.radians((south + north) / 2)), 0.01)
    scale = 10 ** DECIMALS
    return [math.floor((west - dlon) * scale) / scale, math.floor((south - dlat) * scale) / scale,
            math.ceil((east + dlon) * scale) / scale, math.ceil((north + dlat) * scale) / scale]


def build_context(walk_conn, bbox, *, pad_m=PAD_M, data_dir=None):
    """WalkContext (a JSON-ready dict) around `bbox` grown by pad_m metres. walk_conn: a connection to walk.sqlite3;
    data_dir: the directory holding snapshots/ (default settings.DATA_DIR, through the v3_cache payload)."""
    box = padded(bbox, pad_m)
    walk = index(walk_conn)
    return {'bbox': box, 'roads': roads(walk_conn, box, walk), 'precincts': precincts(box, data_dir),
            'homes': homes(walk, box)}


def attach(payload, conn=None, **options):
    """`payload` with `context` around payload['bbox'] (set in place and returned), over `conn` or a read-only walk store
    connection of its own. When the walk store is unavailable the payload goes out without it (context is optional)."""
    if conn is not None:
        payload['context'] = build_context(conn, payload['bbox'], **options)
        return payload
    try:
        with W.walk_store() as store:
            payload['context'] = build_context(store.conn, payload['bbox'], **options)
    except SourceStoreError:
        log.info('walk context: the walk store is unavailable, the list goes out without context')
    return payload


# ---- what is read once per version of walk.sqlite3 -----------------------------------------------------------------

def _keys(lonlat):
    import numpy as np
    whole = np.round(np.asarray(lonlat, dtype=float).reshape(-1, 2) * 10 ** DECIMALS).astype(np.int64)
    return (whole[:, 0] + LON0) << LAT_BITS | (whole[:, 1] + LAT0)


def _lonlat(keys):
    import numpy as np
    return np.column_stack([((keys >> LAT_BITS) - LON0) / 10 ** DECIMALS,
                            ((keys & ((1 << LAT_BITS) - 1)) - LAT0) / 10 ** DECIMALS])


class Index:
    """Of one version of walk.sqlite3: every distinct stop position as a key (sorted: by longitude, then latitude),
    the label of each voter street name key (the commonest display of the streets with that key) and whether road_span
    has the virtual column (stores built before it lack it)."""

    def __init__(self, conn):
        import numpy as np
        self.homes = np.unique(_keys(_query(conn, 'SELECT lon, lat FROM stop WHERE lon IS NOT NULL AND lat IS NOT NULL')))
        seen = defaultdict(Counter)
        for key, display in _query(conn, 'SELECT key, display FROM street'):
            if key and display:
                seen[key][display] += 1
        self.labels = {key: min(found.items(), key=lambda item: (-item[1], item[0]))[0] for key, found in seen.items()}
        self.virtual = any(row[1] == 'virtual' for row in _query(conn, 'PRAGMA table_info(road_span)'))

    def label(self, key):
        return self.labels.get(key) or N.display_street('', key, '', '') if key else ''


def _stamp(conn):
    """(path, mtime, size) of the connection's main database file; None for one in memory."""
    for _, name, path in _query(conn, 'PRAGMA database_list'):
        if name == 'main' and path:
            stat = os.stat(path)
            return path, stat.st_mtime_ns, stat.st_size
    return None


def index(conn):
    """The Index of the walk store behind `conn`, read once per file version (each time for a database in memory)."""
    global _index
    stamp = _stamp(conn)
    cached = _index
    if stamp is not None and cached is not None and cached[0] == stamp:
        return cached[1]
    with _lock:
        if stamp is not None and _index is not None and _index[0] == stamp:
            return _index[1]
        made = Index(conn)
        if stamp is not None:
            _index = (stamp, made)
        return made


# ---- roads ---------------------------------------------------------------------------------------------------------

def roads(conn, box, walk=None):
    west, south, east, north = box
    walk = walk or index(conn)
    ids = sorted(row[0] for row in _query(conn, RANKED, (east, west, north, south, MAX_ROADS)))
    spans = _query_ids(conn, 'SELECT id, name_key, kind, %s, coords FROM road_span WHERE id IN ({}) ORDER BY id'
                       % ('virtual' if walk.virtual else '0'), ids)
    lines = json.loads('[%s]' % ','.join(span[4] or '[]' for span in spans))
    keep = [i for i, line in enumerate(lines) if len(line) >= 2]
    spans, lines = [spans[i] for i in keep], simplified([lines[i] for i in keep], ROAD_TOLERANCE_M)
    return [{'k': 'V' if span[3] else span[2], 'n': walk.label(span[1]), 'c': line}
            for span, line in zip(spans, lines) if len(line) >= 2]


def simplified(lines, tolerance_m):
    """Each [[lon, lat], ...] line simplified by tolerance_m in district UTM metres, at DECIMALS, without repeated points (a line
    may end up shorter than two points). Douglas-Peucker; a line it would make cross itself, or a closed one it would
    collapse, goes through the topology-preserving simplifier instead (as shapely's preserve_topology, 10 times faster)."""
    import numpy as np
    import shapely

    from tools.walk_geo import to_lonlat, to_metres
    if not lines:
        return []
    counts = np.array([len(line) for line in lines])
    flat = np.array([point[:2] for line in lines for point in line], dtype=float)
    epsg = settings.DISTRICT['utm_epsg']
    x, y = to_metres(flat[:, 0], flat[:, 1], epsg)
    shapes = shapely.linestrings(np.column_stack([x, y]), indices=np.repeat(np.arange(len(lines)), counts))
    simple = shapely.simplify(shapes, tolerance_m, preserve_topology=False)
    redo = ~shapely.is_simple(simple) | (shapely.is_closed(shapes) & (shapely.get_num_coordinates(simple) < 4))
    if redo.any():
        simple[redo] = shapely.simplify(shapes[redo], tolerance_m, preserve_topology=True)
    points, at = shapely.get_coordinates(simple, return_index=True)
    lon, lat = to_lonlat(points[:, 0], points[:, 1], epsg)
    points = np.round(np.column_stack([lon, lat]), DECIMALS)
    keep = np.ones(len(points), dtype=bool)
    keep[1:] = (at[1:] != at[:-1]) | np.any(points[1:] != points[:-1], axis=1)
    ends = np.cumsum(np.bincount(at[keep], minlength=len(lines))).tolist()
    flat = points[keep].tolist()
    return [flat[start:end] for start, end in zip([0, *ends[:-1]], ends)]


# ---- precincts -----------------------------------------------------------------------------------------------------

def _rings(geometry):
    """Outer rings and holes of a GeoJSON Polygon or MultiPolygon."""
    if geometry['type'] == 'Polygon':
        return geometry['coordinates']
    if geometry['type'] == 'MultiPolygon':
        return [ring for polygon in geometry['coordinates'] for ring in polygon]
    return []


class Layer:
    """The polygons of one FeatureCollection by id: rings at DECIMALS, and bounds and shapes for the box test."""

    def __init__(self, collection):
        import numpy as np
        import shapely
        from shapely.geometry import shape
        features = sorted(collection['features'], key=lambda feature: feature['properties']['id'])
        self.ids = [feature['properties']['id'] for feature in features]
        self.shapes = np.empty(len(features), dtype=object)
        self.shapes[:] = [shape(feature['geometry']) for feature in features]
        self.bounds = shapely.bounds(self.shapes).reshape(-1, 4)
        self.rings = [[np.round(np.asarray(ring, dtype=float)[:, :2], DECIMALS) for ring in _rings(feature['geometry'])]
                      for feature in features]

    def within(self, box):
        """[{id, label, rings}] of the polygons that meet `box`, by id."""
        import numpy as np
        import shapely
        west, south, east, north = box
        b = self.bounds
        near = np.flatnonzero((b[:, 0] <= east) & (b[:, 2] >= west) & (b[:, 1] <= north) & (b[:, 3] >= south))
        if len(near):
            near = near[shapely.intersects(self.shapes[near], shapely.box(west, south, east, north))]
        return [{'id': self.ids[i], 'label': self.ids[i], 'rings': [ring.tolist() for ring in self.rings[i]]}
                for i in near.tolist()]


def precinct_layer(data_dir=None):
    """The precinct Layer of the active cu-summary-v4 snapshot, None without one (or when its geometry is unreadable).
    data_dir None reads the v3_cache payload (memory, disk, else built); a data_dir its derived file."""
    global _layer
    found = v4_snapshot()
    if found is None:
        return None
    key, (level, variant) = found[0], PRECINCTS
    try:
        if data_dir is None:
            blob = v3_cache.geometry_blob(key, level, variant)
            stamp, read = (None, key, blob.etag), (lambda: blob.body)
        else:
            path = Path(data_dir) / 'snapshots' / key / 'derived' / (
                '%s_geometry_%s_%s.json' % (v3_cache.DERIVED_VERSION, level, variant))
            stat = path.stat()
            stamp, read = (str(path.resolve()), stat.st_mtime_ns, stat.st_size), path.read_bytes
        cached = _layer
        if cached is not None and cached[0] == stamp:
            return cached[1]
        with _lock:
            if _layer is None or _layer[0] != stamp:
                _layer = (stamp, Layer(json.loads(read())))
            return _layer[1]
    except (OSError, ValueError, KeyError, TypeError) as problem:
        log.warning('walk context: the precinct outlines of the active snapshot are unreadable (%s)', type(problem).__name__)
        return None


def precincts(box, data_dir=None):
    layer = precinct_layer(data_dir)
    return layer.within(box) if layer is not None else []


# ---- homes ---------------------------------------------------------------------------------------------------------

def homes(walk, box):
    """The distinct stop positions in `box` (walk: an Index) by longitude, then latitude; at most MAX_HOMES (thin)."""
    import numpy as np
    low, high = _keys([[box[0], box[1]], [box[2], box[3]]])          # south-west and north-east corners
    lat_bits = (1 << LAT_BITS) - 1
    keys = walk.homes[np.searchsorted(walk.homes, low & ~lat_bits):np.searchsorted(walk.homes, high | lat_bits, side='right')]
    lat = keys & lat_bits
    keys = keys[(lat >= (low & lat_bits)) & (lat <= (high & lat_bits))]
    if len(keys) > MAX_HOMES:
        keys = keys[thin(keys, MAX_HOMES)]
    return np.round(_lonlat(keys), DECIMALS).tolist()


def thin(keys, cap):
    """Ascending indices of `cap` of the position keys: those that hash lowest (splitmix64). The sample is spread like the
    points themselves and the same for the same points."""
    import numpy as np
    z = np.asarray(keys, dtype=np.int64).view(np.uint64)
    z = (z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    z = (z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    z = z ^ (z >> np.uint64(31))
    return np.sort(np.argsort(z, kind='stable')[:cap])
