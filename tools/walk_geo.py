"""Walk-list road network: build the noded street graph into walk.sqlite3 (road_* tables), add virtual streets for the
voter streets TIGER lacks and connectors that make it one component, load it, snap stops to it, place the rest by house
number or by area, and settle which side of each block carries the even numbers.

Build-time library for the walk builder (vectorised shapely 2 + pyproj, no Django). Geometry is EPSG:26913 metres;
stored coordinates are EPSG:4326 [lon, lat]. Typical use:

    build_network(out, roads.execute('SELECT id, name, kind, geometry FROM roads'))
    stats = augment_network(out, virtual_streets(load_network(out), stops))
    net = load_network(out)                     # stops: id, key, city, zip, house_int, parity, x, y (None: no geocode)
    placements = place_near(net, stops, interpolate(net, stops, snap_stops(net, stops)), targets)  # {i: (x, y)}
    even_left, agreement = assign_sides(net, placements)            # UPDATE road_span SET even_left=? WHERE id=?

Placement tiers: a own street within 500 m, b similar name within 150 m, c any road within 150 m, e the nearest road at
any distance (located stops); d house-number interpolation along the own street, g the nearest road to an area centre
(stops without a geocode).
"""
import json
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import shapely
from pyproj import Transformer

from server.research.walk_names import name_key, similarity

KINDS = ('S1200', 'S1400')                   # S1100 (limited-access highways) is dropped
GRID_M = 1.0                                 # noding precision, as tools/prepare_geography.py
SAME_NAME_M, FUZZY_M, NEAREST_M, FUZZY_MIN = 500.0, 150.0, 150.0, 0.8    # snap radii of tiers a, b, c (metres)
VIRTUAL_KIND = 'S1400'                       # virtual streets and connectors walk like local streets
VIRTUAL_GAP_M = 400.0                        # a virtual street breaks where house-order neighbours lie further apart
TABLES = ('road_node', 'road_edge', 'road_edge_box', 'road_span')
SCHEMA = (
    'CREATE TABLE road_node(id INTEGER PRIMARY KEY, lon REAL, lat REAL, x REAL, y REAL, degree INTEGER)',
    'CREATE TABLE road_edge(id INTEGER PRIMARY KEY, u INTEGER, v INTEGER, len_m REAL, kind TEXT, name_key TEXT, '
    'span_id INTEGER, coords TEXT, virtual INTEGER DEFAULT 0)',
    'CREATE VIRTUAL TABLE road_edge_box USING rtree(id, west, east, south, north)',
    'CREATE TABLE road_span(id INTEGER PRIMARY KEY, name_key TEXT, a INTEGER, b INTEGER, len_m REAL, kind TEXT, '
    'edges TEXT, coords TEXT, even_left INTEGER, virtual INTEGER DEFAULT 0)',
    'CREATE INDEX road_edge_span ON road_edge(span_id)',
    'CREATE INDEX road_span_key ON road_span(name_key)',
)


@lru_cache(maxsize=None)
def _transformer(source, target):
    return Transformer.from_crs(source, target, always_xy=True)


def to_metres(lon, lat):
    """EPSG:4326 lon/lat -> EPSG:26913 x/y metres (scalars or arrays)."""
    return _transformer(4326, 26913).transform(lon, lat)


def to_lonlat(x, y):
    """EPSG:26913 x/y metres -> EPSG:4326 lon/lat (scalars or arrays)."""
    return _transformer(26913, 4326).transform(x, y)


# ---- build -----------------------------------------------------------------------------------------------------
def build_network(conn, roads_rows):
    """Write road_node / road_edge / road_edge_box / road_span from (id, name, kind, GeoJSON LineString EPSG:4326)
    rows. Keeps S1200 + S1400, nodes all lines on a 1 m grid in EPSG:26913 (snap-rounded union), re-attaches each
    noded piece to the TIGER line under it (nearest; ties prefer a named line, then the lowest road id) and chains
    same-name edges through degree-2 nodes into spans. Ids are canonical (nodes by x, y; edges by u, v, geometry), so
    the output does not depend on row order. Replaces existing road_* rows and commits. Returns build stats."""
    road_ids, keys, kinds, parts = [], [], [], []
    seen = 0
    for road_id, name, kind, geometry in roads_rows:
        seen += 1
        if kind not in KINDS:
            continue
        geo = json.loads(geometry) if isinstance(geometry, (str, bytes)) else geometry
        if not geo:
            continue
        for line in {'LineString': [geo['coordinates']], 'MultiLineString': geo['coordinates']}.get(geo['type'], []):
            if len(line) > 1:
                road_ids.append(int(road_id))
                keys.append(name_key(name))
                kinds.append(kind)
                parts.append(np.asarray(line, float)[:, :2])
    if not parts:
        raise ValueError('No S1200/S1400 road lines to build a network from')
    kept = len(set(road_ids))
    lonlat = np.concatenate(parts)
    x, y = to_metres(lonlat[:, 0], lonlat[:, 1])
    lines = shapely.linestrings(np.column_stack([x, y]),
                                indices=np.repeat(np.arange(len(parts)), [len(p) for p in parts]))
    lines = shapely.set_precision(lines, GRID_M)
    ok = ~shapely.is_empty(lines)
    lines, road_ids = lines[ok], np.array(road_ids)[ok]
    keys, kinds = np.array(keys, dtype=object)[ok], np.array(kinds, dtype=object)[ok]
    pieces = shapely.get_parts(shapely.union_all(lines, grid_size=GRID_M))
    pieces = pieces[(shapely.get_type_id(pieces) == 1) & (shapely.length(pieces) > 0)]
    source = _source_lines(pieces, lines, road_ids, keys)
    xy = np.split(np.round(shapely.get_coordinates(pieces)), np.cumsum(shapely.get_num_coordinates(pieces))[:-1])
    return {'roads_in': seen, 'roads_kept': kept, **_write(conn, [(c, keys[s], kinds[s], 0) for c, s in zip(xy, source)])}


def _write(conn, lines):
    """Replace the road_* tables with the network of `lines` [(xy metres on the grid, name_key, kind, virtual)]:
    canonical ids, node degrees, spans (edges of one name and one virtual flag chained through degree-2 nodes).
    Commits. Returns {nodes, edges, spans, components, largest_component_share, length_km}."""
    nodes, edges = _edges(lines)
    u = [e[0] for e in edges]
    v = [e[1] for e in edges]
    lengths = [e[2] for e in edges]
    degree = np.bincount(np.r_[u, v], minlength=len(nodes) + 1)
    node_lon, node_lat = to_lonlat(nodes[:, 0], nodes[:, 1])
    allxy = np.concatenate([e[6] for e in edges])
    lon, lat = to_lonlat(allxy[:, 0], allxy[:, 1])
    ll = np.round(np.column_stack([lon, lat]), 6)
    edge_ll = np.split(ll, np.cumsum([len(e[6]) for e in edges])[:-1])
    spans = _spans(u, v, [(e[4], e[7]) for e in edges])
    span_of = {}
    for sid, (chain, _, _) in enumerate(spans, 1):
        for signed in chain:
            span_of[abs(signed)] = sid

    for table in TABLES:
        conn.execute('DROP TABLE IF EXISTS ' + table)
    for sql in SCHEMA:
        conn.execute(sql)
    conn.executemany('INSERT INTO road_node VALUES (?,?,?,?,?,?)',
                     [(i + 1, round(float(node_lon[i]), 6), round(float(node_lat[i]), 6), float(nodes[i, 0]),
                       float(nodes[i, 1]), int(degree[i + 1])) for i in range(len(nodes))])
    conn.executemany('INSERT INTO road_edge VALUES (?,?,?,?,?,?,?,?,?)',
                     [(i, e[0], e[1], round(e[2], 2), e[5], e[4], span_of[i], json.dumps(edge_ll[i - 1].tolist(),
                       separators=(',', ':')), e[7]) for i, e in enumerate(edges, 1)])
    conn.executemany('INSERT INTO road_edge_box VALUES (?,?,?,?,?)',
                     [(i, float(c[:, 0].min()), float(c[:, 0].max()), float(c[:, 1].min()), float(c[:, 1].max()))
                      for i, c in enumerate(edge_ll, 1)])
    span_rows = []
    for sid, (chain, a, b) in enumerate(spans, 1):
        by_kind, path = defaultdict(float), []
        for signed in chain:
            e = abs(signed) - 1
            by_kind[edges[e][5]] += lengths[e]
            c = edge_ll[e] if signed > 0 else edge_ll[e][::-1]
            path.extend(c.tolist() if not path else c[1:].tolist())
        first = edges[abs(chain[0]) - 1]
        span_rows.append((sid, first[4], a, b, round(sum(lengths[abs(s) - 1] for s in chain), 2),
                          max(by_kind.items(), key=lambda kv: (kv[1], kv[0]))[0], json.dumps(chain),
                          json.dumps(path, separators=(',', ':')), None, first[7]))
    conn.executemany('INSERT INTO road_span VALUES (?,?,?,?,?,?,?,?,?,?)', span_rows)
    conn.commit()
    components, largest, total = _components(u, v, lengths, len(nodes))
    return {'nodes': len(nodes), 'edges': len(edges), 'spans': len(spans), 'components': components,
            'largest_component_share': round(largest / total, 4) if total else 0.0, 'length_km': round(total / 1000, 3)}


def augment_network(conn, streets):
    """Add virtual streets to the network in conn and make it one component; rewrites the road_* tables (new ids).
    streets: [(name_key, [(x, y), ...], city)] polylines in EPSG:26913 metres (virtual_streets; city optional). Each
    becomes one edge (virtual = 1, kind S1400) whose ends are joined to the nearest real node by straight virtual
    connectors (name_key ''); then every component but the longest gets one connector from its node nearest to the
    longest one. Connectors are walkable, never snapped to. Returns the _write stats plus virtual_streets (names),
    virtual_pieces (edges), street_connectors, component_connectors and the length spread of each kind of connector
    (street_connector_m, component_connector_m: count, p50, p95, max, km)."""
    old = conn.execute('SELECT coords, name_key, kind, virtual FROM road_edge ORDER BY id').fetchall()
    parts = [np.asarray(json.loads(r[0]), float) for r in old]
    lines = []
    if parts:
        ll = np.concatenate(parts)
        x, y = to_metres(ll[:, 0], ll[:, 1])
        xy = np.round(np.column_stack([x, y]) / GRID_M) * GRID_M         # back on the grid the edges were built on
        for (_, key, kind, virtual), c in zip(old, np.split(xy, np.cumsum([len(p) for p in parts])[:-1])):
            lines.append((c, key or '', kind, virtual or 0))
    real = np.unique(np.vstack([c[[0, -1]] for c, _, _, virtual in lines if not virtual] or [np.empty((0, 2))]), axis=0)
    added, names, ends = 0, set(), []
    for key, points, *city in streets:
        c = np.round(np.asarray(points, float).reshape(-1, 2) / GRID_M) * GRID_M
        c = c[np.r_[True, np.any(c[1:] != c[:-1], axis=1)]] if len(c) else c
        if len(c) < 2:
            continue
        lines.append((c, key, VIRTUAL_KIND, 1))
        added += 1
        names.add((key, *city))
        ends += [c[0], c[-1]]
    joins = set()
    if ends and len(real):
        ends = np.array(ends)
        joins = {(tuple(end), tuple(node)) for end, node in zip(ends, _closest(real, ends)) if tuple(end) != tuple(node)}
    lines += [(np.array(pair, float), '', VIRTUAL_KIND, 1) for pair in sorted(joins)]
    bridges = _bridges(lines)
    lines += [(np.array(pair, float), '', VIRTUAL_KIND, 1) for pair in bridges]
    return {**_write(conn, lines), 'virtual_streets': len(names), 'virtual_pieces': added,
            'street_connectors': len(joins), 'component_connectors': len(bridges),
            'street_connector_m': _spread(sorted(joins)), 'component_connector_m': _spread(bridges)}


def _spread(pairs):
    """count, p50, p95, max (metres) and total km of straight links [(a, b)]."""
    d = np.array([np.hypot(a[0] - b[0], a[1] - b[1]) for a, b in pairs])
    if not len(d):
        return {'count': 0, 'p50': None, 'p95': None, 'max': None, 'km': 0.0}
    p50, p95 = np.percentile(d, [50, 95])
    return {'count': len(d), 'p50': round(float(p50), 1), 'p95': round(float(p95), 1), 'max': round(float(d.max()), 1),
            'km': round(float(d.sum()) / 1000, 3)}


def _closest(targets, points):
    """For each point, the nearest of `targets` (n x 2 metres); ties go to the lowest (x, y)."""
    tree = shapely.STRtree(shapely.points(targets))
    qi, ti = tree.query_nearest(shapely.points(points), all_matches=True)
    order = np.lexsort((targets[ti, 1], targets[ti, 0], qi))
    qi, ti = qi[order], ti[order]
    first = np.r_[True, qi[1:] != qi[:-1]]
    out = np.empty((len(points), 2))
    out[qi[first]] = targets[ti[first]]
    return out


def _bridges(lines):
    """[(node, node)] straight links joining every component of `lines` but the longest to the longest one: from the
    component's node nearest to it (ties: lowest coordinates). Components are taken in order of their lowest node."""
    ends = np.vstack([np.array([c[0] for c, *_ in lines]), np.array([c[-1] for c, *_ in lines])])
    nodes, inverse = np.unique(ends, axis=0, return_inverse=True)
    inverse = inverse.reshape(-1)
    u, v = inverse[:len(lines)].tolist(), inverse[len(lines):].tolist()
    lengths = shapely.length(shapely.linestrings(np.concatenate([c for c, *_ in lines]),
                                                 indices=np.repeat(np.arange(len(lines)), [len(c) for c, *_ in lines])))
    parent = list(range(len(nodes)))

    def find(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n
    for a, b in zip(u, v):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    root = np.array([find(n) for n in range(len(nodes))])
    size = defaultdict(float)
    for a, length in zip(u, lengths.tolist()):
        size[root[a]] += length
    if len(size) < 2:
        return []
    main = max(size, key=lambda r: (size[r], -r))
    inside = root == main
    tree = shapely.STRtree(shapely.points(nodes[inside]))
    targets, others = nodes[inside], np.flatnonzero(~inside)
    (qi, ti), d = tree.query_nearest(shapely.points(nodes[others]), all_matches=True, return_distance=True)
    group = root[others[qi]]
    order = np.lexsort((targets[ti, 1], targets[ti, 0], nodes[others[qi], 1], nodes[others[qi], 0], d, group))
    group, qi, ti = group[order], qi[order], ti[order]
    first = np.r_[True, group[1:] != group[:-1]]
    return [(tuple(nodes[others[q]]), tuple(targets[t])) for q, t in zip(qi[first], ti[first])]


def _source_lines(pieces, lines, road_ids, keys):
    """Index of the source line under each noded piece: smallest summed distance at 3 sample points."""
    tree = shapely.STRtree(lines)
    pi, li = tree.query(shapely.line_interpolate_point(pieces, 0.5, normalized=True), predicate='dwithin',
                        distance=2 * GRID_M)
    score = sum(shapely.distance(shapely.line_interpolate_point(pieces[pi], f, normalized=True), lines[li])
                for f in (0.25, 0.5, 0.75))
    order = np.lexsort((road_ids[li], keys[li] == '', np.round(score, 1), pi))
    pi, li = pi[order], li[order]
    first = np.r_[True, pi[1:] != pi[:-1]]
    source = np.full(len(pieces), -1)
    source[pi[first]] = li[first]
    lost = np.flatnonzero(source < 0)
    if len(lost):
        qi, ti = tree.query_nearest(shapely.line_interpolate_point(pieces[lost], 0.5, normalized=True),
                                    all_matches=False)
        source[lost[qi]] = ti
    return source


def _edges(lines):
    """Grid nodes sorted by (x, y) and canonical edges (u <= v) sorted by (u, v, length, coordinates, virtual, name)
    from [(xy, name_key, kind, virtual)]: [(u, v, len_m, flat coords, name_key, kind, xy array u->v, virtual)]."""
    xy = [np.asarray(line[0], float) for line in lines]
    ends = np.vstack([np.array([c[0] for c in xy]), np.array([c[-1] for c in xy])])
    nodes, inverse = np.unique(ends, axis=0, return_inverse=True)
    inverse = inverse.reshape(-1) + 1
    lengths = shapely.length(shapely.linestrings(np.concatenate(xy), indices=np.repeat(np.arange(len(xy)),
                                                                                        [len(c) for c in xy])))
    edges = []
    for i, (line, (_, key, kind, virtual)) in enumerate(zip(xy, lines)):
        u, v = int(inverse[i]), int(inverse[i + len(xy)])
        if u > v or (u == v and tuple(line[-2]) < tuple(line[1])):
            line, u, v = line[::-1], v, u
        edges.append((u, v, float(lengths[i]), tuple(line.ravel().tolist()), key, kind, line, int(virtual)))
    edges.sort(key=lambda e: (e[:4], e[7], e[4]))
    return nodes, edges


def _spans(u, v, keys):
    """Maximal same-name chains broken at nodes of degree != 2 or where the name changes (unnamed edges chain only
    with unnamed ones; keys may be (name, virtual) pairs, so real and virtual edges never share a span). u, v, keys are
    per edge (edge id = index + 1). Returns [(signed edge ids a->b, a, b)] ordered by each span's lowest edge id; spans
    run from the lower node id (closed loops: lower first edge id first)."""
    ends = defaultdict(list)                            # node -> [(edge index, 0 at u | 1 at v)]
    for e in range(len(u)):
        ends[u[e]].append((e, 0))
        ends[v[e]].append((e, 1))

    def through(node):
        here = ends[node]
        return len(here) == 2 and keys[here[0][0]] == keys[here[1][0]]

    def walk(node, came, forward):
        steps = []
        while through(node):
            here = ends[node]
            nxt = here[1] if here[0] == came else here[0]
            if seen[nxt[0]]:
                break
            seen[nxt[0]] = True
            steps.append((nxt[0] + 1) * (1 if (nxt[1] == 0) == forward else -1))
            far = 1 - nxt[1]
            node, came = (v if far else u)[nxt[0]], (nxt[0], far)
        return steps, node

    seen = [False] * len(u)
    spans = []
    for e in range(len(u)):
        if seen[e]:
            continue
        seen[e] = True
        tail, b = walk(v[e], (e, 1), True)
        head, a = walk(u[e], (e, 0), False)
        chain = head[::-1] + [e + 1] + tail
        if a > b or (a == b and abs(chain[-1]) < abs(chain[0])):
            chain, a, b = [-s for s in reversed(chain)], b, a
        spans.append((chain, a, b))
    return spans


def _components(u, v, lengths, n_nodes):
    parent = list(range(n_nodes + 1))

    def find(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    for a, b in zip(u, v):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    size = defaultdict(float)
    for a, length in zip(u, lengths):
        size[find(a)] += length
    return len(size), max(size.values(), default=0.0), sum(size.values())


# ---- load ------------------------------------------------------------------------------------------------------
@dataclass(slots=True)
class Span:
    id: int
    name_key: str
    a: int
    b: int
    len_m: float
    kind: str
    edges: list                 # signed edge ids a->b
    offsets: list               # distance from a to the start of each edge
    even_left: int | None


class _Chain:
    """Spans of one name joined end to end: items [(span_id, +1 walked a->b | -1, offset, len_m)]."""
    __slots__ = ('items', 'offsets', 'length', 'where')

    def __init__(self, items):
        self.items, self.offsets = items, [item[2] for item in items]
        self.length = items[-1][2] + items[-1][3]
        self.where = {item[0]: k for k, item in enumerate(items)}

    def coord(self, span_id, pos):
        _, direction, offset, length = self.items[self.where[span_id]]
        return offset + (pos if direction > 0 else length - pos)

    def locate(self, c):
        k = min(max(bisect_right(self.offsets, c) - 1, 0), len(self.items) - 1)
        span_id, direction, offset, length = self.items[k]
        s = min(max(c - offset, 0.0), length)
        return span_id, (s if direction > 0 else length - s)


class Network:
    """road_* tables in memory: per-edge numpy arrays (geometry in metres, u->v) with an STRtree, spans by id.
    snap_tree holds the edges stops may sit on (snap_rows): every edge but the virtual connectors."""

    def __init__(self, nodes, edges, spans):
        self.nodes = nodes                                       # id -> (x, y, degree)
        self.spans = spans                                       # id -> Span
        self.edge_id = np.array([e[0] for e in edges], dtype=np.int64)
        self.edge_u = np.array([e[1] for e in edges], dtype=np.int64)
        self.edge_v = np.array([e[2] for e in edges], dtype=np.int64)
        self.edge_len = np.array([e[3] for e in edges], dtype=float)
        self.edge_kind = np.array([e[4] for e in edges], dtype=object)
        self.edge_key = np.array([e[5] or '' for e in edges], dtype=object)
        self.edge_span = np.array([e[6] for e in edges], dtype=np.int64)
        self.edge_virtual = np.array([e[8] or 0 if len(e) > 8 else 0 for e in edges], dtype=np.int64)
        self.row = {int(e): i for i, e in enumerate(self.edge_id)}
        parts = [np.asarray(json.loads(e[7]), float) for e in edges]
        if parts:
            ll = np.concatenate(parts)
            x, y = to_metres(ll[:, 0], ll[:, 1])
            self.edge_geom = shapely.linestrings(np.column_stack([x, y]),
                                                 indices=np.repeat(np.arange(len(parts)), [len(p) for p in parts]))
        else:
            self.edge_geom = np.array([], dtype=object)
        self.tree = shapely.STRtree(self.edge_geom)
        self.snap_rows = np.flatnonzero(~((self.edge_virtual > 0) & (self.edge_key == '')))
        self.snap_tree = shapely.STRtree(self.edge_geom[self.snap_rows])
        self.edge_dir = np.ones(len(edges), dtype=np.int64)     # +1 when the span walks the edge u->v
        self.edge_off = np.zeros(len(edges))                    # span distance from a to the edge's first point
        self.edge_span_len = np.zeros(len(edges))
        self.key_spans = defaultdict(list)
        for span in spans.values():
            self.key_spans[span.name_key].append(span.id)
            for signed, offset in zip(span.edges, span.offsets):
                r = self.row[abs(signed)]
                self.edge_dir[r], self.edge_off[r], self.edge_span_len[r] = (1 if signed > 0 else -1), offset, span.len_m
        self._chains = {}

    def chains(self, key):
        """Spans named `key` linked end to end (from dead ends first, lowest ids first); a branch starts a new chain."""
        if key not in self._chains:
            adjacent = defaultdict(list)
            for sid in self.key_spans.get(key, []):
                adjacent[self.spans[sid].a].append((sid, 0))
                adjacent[self.spans[sid].b].append((sid, 1))
            used, chains = set(), []
            starts = sorted(n for n, here in adjacent.items() if len(here) == 1)
            for node in starts + [self.spans[sid].a for sid in self.key_spans.get(key, [])]:
                items, offset = [], 0.0
                while True:
                    step = min(((sid, end) for sid, end in adjacent[node] if sid not in used), default=None)
                    if step is None:
                        break
                    span = self.spans[step[0]]
                    used.add(span.id)
                    items.append((span.id, 1 if step[1] == 0 else -1, offset, span.len_m))
                    offset += span.len_m
                    node = span.b if step[1] == 0 else span.a
                if items:
                    chains.append(_Chain(items))
            self._chains[key] = chains
        return self._chains[key]


def load_network(conn):
    """Read road_node / road_edge / road_span from a walk.sqlite3 connection into a Network (stores without the
    virtual column read as all real)."""
    nodes = {r[0]: (r[1], r[2], r[3]) for r in conn.execute('SELECT id, x, y, degree FROM road_node')}
    virtual = 'virtual' if any(r[1] == 'virtual' for r in conn.execute('PRAGMA table_info(road_edge)')) else '0'
    edges = conn.execute('SELECT id, u, v, len_m, kind, name_key, span_id, coords, %s FROM road_edge ORDER BY id'
                         % virtual).fetchall()
    lengths = {e[0]: e[3] for e in edges}
    spans = {}
    for sid, key, a, b, length, kind, chain, even_left in conn.execute(
            'SELECT id, name_key, a, b, len_m, kind, edges, even_left FROM road_span ORDER BY id'):
        chain = json.loads(chain)
        offsets = np.r_[0.0, np.cumsum([lengths[abs(s)] for s in chain])[:-1]].tolist()
        spans[sid] = Span(sid, key or '', a, b, length, kind, chain, offsets, even_left)
    return Network(nodes, edges, spans)


def span_points(net, span_ids, positions):
    """[lon, lat] on the centreline at each (span_id, pos_m from span.a)."""
    rows, fractions = [], []
    for sid, pos in zip(span_ids, positions):
        span = net.spans[sid]
        k = max(bisect_right(span.offsets, pos) - 1, 0)
        r = net.row[abs(span.edges[k])]
        along = min(max(pos - span.offsets[k], 0.0), net.edge_len[r])
        along = along if span.edges[k] > 0 else net.edge_len[r] - along
        rows.append(r)
        fractions.append(along / net.edge_len[r] if net.edge_len[r] > 0 else 0.0)
    if not rows:
        return []
    xy = shapely.get_coordinates(shapely.line_interpolate_point(net.edge_geom[rows], fractions, normalized=True))
    lon, lat = to_lonlat(xy[:, 0], xy[:, 1])
    return np.round(np.column_stack([lon, lat]), 6).tolist()


# ---- snapping --------------------------------------------------------------------------------------------------
@dataclass(slots=True)
class Placement:
    """Where a stop sits: pos_m from span.a along the span; side_lr +1 left / -1 right of a->b, 0 unknown;
    lon/lat on the centreline; dist_m from the geocode to the centreline (snapped tiers only)."""
    id: object
    span_id: int | None
    pos_m: float | None
    side_lr: int
    placed_by: str                  # geocode | fuzzy | nearest | interpolated
    lon: float | None
    lat: float | None
    dist_m: float | None = None
    parity: int | None = None       # 0 even, 1 odd (from the stop; used by assign_sides)
    house_int: int | None = None
    tier: str | None = None         # a b c e (snapped), d g (interpolated); None: not placed


def _stop_house(stop):
    house = stop.get('house_int')
    return int(house) if house is not None else None


def _stop_parity(stop):
    parity = stop.get('parity')
    if parity is None and _stop_house(stop) is not None:
        parity = _stop_house(stop) % 2
    return None if parity is None else int(parity)


def _finite(value):
    try:
        return value is not None and np.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _first_per(p, *keys):
    """Positions (into p) of the best row per p value; keys sort ascending, first key most significant."""
    order = np.lexsort(tuple(reversed(keys)) + (p,))
    p_sorted = p[order]
    return order[np.r_[True, p_sorted[1:] != p_sorted[:-1]]] if len(order) else order


def _nearest_edges(net, points):
    """Row of the nearest snappable edge to each point, at any distance (ties: the lowest row)."""
    qi, ti = net.snap_tree.query_nearest(points, all_matches=True)
    e = net.snap_rows[ti]
    best = _first_per(qi, shapely.distance(points[qi], net.edge_geom[e]), e)
    rows = np.empty(len(points), dtype=np.int64)
    rows[qi[best]] = e[best]
    return rows


def _project(net, e, xy):
    """Points xy (metres) dropped onto edge rows e: (pos_m from the span's a, side_lr +1 left / -1 right of a->b,
    0 on the line, lon, lat of the centreline point, distance in metres)."""
    geom, point = net.edge_geom[e], shapely.points(xy)
    length = shapely.length(geom)
    t = shapely.line_locate_point(geom, point)
    snapped = shapely.get_coordinates(shapely.line_interpolate_point(geom, t))
    eps = np.minimum(0.5, length / 4)
    c0 = shapely.get_coordinates(shapely.line_interpolate_point(geom, np.clip(t - eps, 0, length)))
    c1 = shapely.get_coordinates(shapely.line_interpolate_point(geom, np.clip(t + eps, 0, length)))
    d = c1 - c0
    w = xy - snapped
    offset = (d[:, 0] * w[:, 1] - d[:, 1] * w[:, 0]) / np.maximum(np.hypot(d[:, 0], d[:, 1]), 1e-9)
    side = np.where(np.abs(offset) < 0.05, 0, np.sign(offset)).astype(np.int64) * net.edge_dir[e]
    along = np.clip(t / np.maximum(length, 1e-9), 0, 1) * net.edge_len[e]
    pos = net.edge_off[e] + np.where(net.edge_dir[e] > 0, along, net.edge_len[e] - along)
    pos = np.clip(pos, 0, net.edge_span_len[e])
    lon, lat = to_lonlat(snapped[:, 0], snapped[:, 1])
    return pos, side, lon, lat, np.hypot(w[:, 0], w[:, 1])


def snap_stops(net, stops, chunk=50000):
    """Place stops with a location on the nearest suitable edge. Tiers: a) same name_key within 500 m -> 'geocode'
    (a home's own street beats a nearer foreign road), b) similarity >= 0.8 within 150 m (most similar, then nearest)
    -> 'fuzzy', c) any edge within 150 m -> 'nearest' (rural setbacks, streets TIGER lacks), e) the nearest edge at any
    distance -> 'nearest'; a lower tier only places stops the higher ones left, so every located stop is placed.
    Virtual connectors are never snapped to. Unnamed edges and unnamed stops never match by name. stops: dicts with
    id, key, x, y (EPSG:26913 metres or None); house_int / parity are carried into the Placement. Returns a list
    aligned with stops: Placement, or None for a stop without a location."""
    out = [None] * len(stops)
    rows = [i for i, s in enumerate(stops) if _finite(s.get('x')) and _finite(s.get('y'))]
    if not rows or not len(net.snap_rows):
        return out
    xy = np.array([(float(stops[i]['x']), float(stops[i]['y'])) for i in rows])
    skeys = [stops[i].get('key') or '' for i in rows]
    vocab = sorted(set(net.edge_key.tolist()) | set(skeys))
    code = {k: c for c, k in enumerate(vocab)}
    ecode = np.array([code[k] if k else -1 for k in net.edge_key], dtype=np.int64)
    scode = np.array([code[k] if k else -2 for k in skeys], dtype=np.int64)
    points = shapely.points(xy)
    pick, tier, sims = np.full(len(rows), -1), np.zeros(len(rows), dtype=np.int8), {}

    def take(level, p, e, *keys):
        best = _first_per(p, *keys, e)
        pick[p[best]], tier[p[best]] = e[best], level

    for lo in range(0, len(rows), chunk):
        pi, ei = net.snap_tree.query(points[lo:lo + chunk], predicate='dwithin',
                                     distance=max(SAME_NAME_M, FUZZY_M, NEAREST_M))
        pi, ei = pi + lo, net.snap_rows[ei]
        same = ecode[ei] == scode[pi]
        if same.any():
            p, e = pi[same], ei[same]
            d = shapely.distance(points[p], net.edge_geom[e])
            within = d <= SAME_NAME_M
            take(1, p[within], e[within], d[within])
        left = (pick[pi] < 0) & (scode[pi] >= 0) & (ecode[ei] >= 0)
        if left.any():
            p, e = pi[left], ei[left]
            pair = scode[p] * len(vocab) + ecode[e]
            unique, inverse = np.unique(pair, return_inverse=True)
            for value in unique.tolist():
                if value not in sims:
                    sims[value] = similarity(vocab[value // len(vocab)], vocab[value % len(vocab)])
            score = np.array([sims[value] for value in unique.tolist()])[inverse]
            near = score >= FUZZY_MIN
            if near.any():
                p, e, score = p[near], e[near], score[near]
                d = shapely.distance(points[p], net.edge_geom[e])
                within = d <= FUZZY_M
                take(2, p[within], e[within], -score[within], d[within])
        left = pick[pi] < 0
        if left.any():
            p, e = pi[left], ei[left]
            d = shapely.distance(points[p], net.edge_geom[e])
            within = d <= NEAREST_M
            take(3, p[within], e[within], d[within])
    left = np.flatnonzero(pick < 0)
    if len(left):                                                       # tier e: whatever the distance
        pick[left], tier[left] = _nearest_edges(net, points[left]), 4

    e = pick
    pos, side, lon, lat, dist = _project(net, e, xy)
    labels = {1: ('geocode', 'a'), 2: ('fuzzy', 'b'), 3: ('nearest', 'c'), 4: ('nearest', 'e')}
    for k, i in enumerate(rows):
        stop = stops[i]
        out[i] = Placement(stop.get('id'), int(net.edge_span[e[k]]), round(float(pos[k]), 2), int(side[k]),
                           labels[int(tier[k])][0], round(float(lon[k]), 6), round(float(lat[k]), 6),
                           round(float(dist[k]), 2), _stop_parity(stop), _stop_house(stop), labels[int(tier[k])][1])
    return out


# ---- interpolation (tier d) ------------------------------------------------------------------------------------
def interpolate(net, stops, placements):
    """Tier d for every stop whose placement is None (no location, or nothing in range). Per street (key + city):
    interpolate pos by house number between the placed neighbours of the same parity (the other parity when a side has
    none), on their span or along their name's span chain; beyond the ends extend along the neighbour's span at the
    street's house-number slope. A street with no placed stops is spread by house number over a chain of spans whose
    name_key equals the street key: the chain nearest the snapped stops of the same city + zip (else city), else the
    longest. No such span (or no house number) -> span_id None, left to tier g (place_near).
    stops: dicts with id, key, city, house_int, parity, optional zip, x, y. Returns a new list aligned with stops."""
    out = list(placements)
    streets, areas = defaultdict(list), defaultdict(lambda: [0.0, 0.0, 0])
    for i, stop in enumerate(stops):
        city, zip_code = str(stop.get('city') or ''), str(stop.get('zip') or '')
        streets[(stop.get('key') or '', city)].append(i)
        if out[i] is not None and out[i].placed_by != 'interpolated' and _finite(stop.get('x')) \
                and _finite(stop.get('y')):
            for area in ((city, zip_code), (city, None)):            # centre of the snapped stops per area
                acc = areas[area]
                acc[0], acc[1], acc[2] = acc[0] + float(stop['x']), acc[1] + float(stop['y']), acc[2] + 1
    spots = {}
    for street in sorted(streets):
        members = streets[street]
        todo = [i for i in members if out[i] is None]
        if todo:
            zip_code = max(Counter(str(stops[i].get('zip') or '') for i in members).items(),
                           key=lambda kv: (kv[1], kv[0]))[0]
            hint = areas.get((street[1], zip_code)) or areas.get((street[1], None))
            near = (hint[0] / hint[2], hint[1] / hint[2]) if hint else None
            spots.update(_street_spots(net, stops, out, members, todo, street[0], near))
    found = [i for i in sorted(spots) if spots[i] is not None]
    lonlat = span_points(net, [spots[i][0] for i in found], [spots[i][1] for i in found])
    where = dict(zip(found, lonlat))
    for i, spot in spots.items():
        stop = stops[i]
        lon, lat = where.get(i, (None, None))
        out[i] = Placement(stop.get('id'), spot[0] if spot else None, round(float(spot[1]), 2) if spot else None, 0,
                           'interpolated', lon, lat, None, _stop_parity(stop), _stop_house(stop), 'd' if spot else None)
    return out


def place_near(net, stops, placements, targets):
    """Tier g: every stop index in `targets` ({index: (x, y) EPSG:26913 metres}, e.g. the centre of the geocoded
    households of its census block) goes onto the nearest walkable road at any distance as 'interpolated', lon/lat
    the centreline point, side_lr 0 (assign_sides fills it in). Returns a new list aligned with stops."""
    out, order = list(placements), sorted(targets)
    if not order or not len(net.snap_rows):
        return out
    xy = np.array([targets[i] for i in order], float).reshape(-1, 2)
    e = _nearest_edges(net, shapely.points(xy))
    pos, _, lon, lat, _ = _project(net, e, xy)
    for k, i in enumerate(order):
        stop = stops[i]
        out[i] = Placement(stop.get('id'), int(net.edge_span[e[k]]), round(float(pos[k]), 2), 0, 'interpolated',
                           round(float(lon[k]), 6), round(float(lat[k]), 6), None, _stop_parity(stop),
                           _stop_house(stop), 'g')
    return out


def virtual_streets(net, stops, minimum=2):
    """Polylines for the streets (key + city) TIGER lacks: none of their located stops finds a real road of its own or
    a similar name (tiers a and b fail for all of them; real roads win), yet at least `minimum` carry a house number.
    The geocodes in house-number order, cut into pieces wherever neighbours lie more than VIRTUAL_GAP_M apart; in every
    piece of two or more each inner vertex is averaged with its two neighbours, twice (a zigzag between the two sides
    of the street shrinks to a ninth). Returns [(key, [(x, y), ...], city)] ordered by key, city, house; feed it to
    augment_network."""
    real, members = set(), defaultdict(list)
    for stop, p in zip(stops, snap_stops(net, stops)):
        key = stop.get('key') or ''
        if not key or p is None:
            continue
        street = (key, str(stop.get('city') or ''))
        if p.tier in ('a', 'b'):
            real.add(street)
        elif _stop_house(stop) is not None:
            members[street].append((_stop_house(stop), float(stop['x']), float(stop['y'])))
    out = []
    for street in sorted(members):
        if street in real or len(members[street]) < minimum:
            continue
        pts = np.array([(x, y) for _, x, y in sorted(members[street])])
        for piece in np.split(pts, np.flatnonzero(np.hypot(*np.diff(pts, axis=0).T) > VIRTUAL_GAP_M) + 1):
            for _ in range(2 if len(piece) > 2 else 0):
                piece[1:-1] = (piece[:-2] + piece[1:-1] + piece[2:]) / 3
            if len(piece) > 1:
                out.append((street[0], [tuple(p) for p in piece.tolist()], street[1]))
    return out


def _street_spots(net, stops, out, members, todo, key, near=None):
    """{stop index: (span_id, pos_m) | None} for the unplaced stops of one street."""
    house = {i: _stop_house(stops[i]) for i in members}
    placed = sorted((house[i], out[i].span_id, out[i].pos_m, i) for i in members
                    if out[i] is not None and out[i].span_id is not None and house[i] is not None)
    if not placed:
        chains = net.chains(key) if key else []
        numbers = [house[i] for i in members if house[i] is not None]
        if not chains or not numbers:
            return {i: None for i in todo}
        if near is not None and len(chains) > 1:
            point = shapely.Point(near)

            def gap(c):
                rows = [net.row[abs(e)] for item in c.items for e in net.spans[item[0]].edges]
                return round(float(shapely.distance(point, net.edge_geom[rows]).min()), 1)
            chain = min(chains, key=lambda c: (gap(c), -c.length, c.items[0][0]))
        else:
            chain = max(chains, key=lambda c: (c.length, -c.items[0][0]))
        low, high = min(numbers), max(numbers)
        return {i: None if house[i] is None else chain.locate(
            chain.length * ((house[i] - low) / (high - low) if high > low else 0.5)) for i in todo}
    sides = {0: [], 1: []}
    for row in placed:
        parity = _stop_parity(stops[row[3]])
        if parity in sides:
            sides[parity].append(row)
    spots, slopes = {}, {}
    for i in todo:
        if house[i] is None:
            spots[i] = None
            continue
        candidates = sides.get(_stop_parity(stops[i])) or placed
        spots[i] = _between(net, candidates, placed, house[i], slopes)
    return spots


def _between(net, candidates, placed, h, slopes):
    numbers = [row[0] for row in candidates]
    lo = candidates[bisect_right(numbers, h) - 1] if bisect_right(numbers, h) else None
    hi = candidates[bisect_left(numbers, h)] if bisect_left(numbers, h) < len(candidates) else None
    if lo and hi:
        if lo[0] == hi[0]:
            return lo[1], lo[2]
        f = (h - lo[0]) / (hi[0] - lo[0])
        if lo[1] == hi[1]:
            return lo[1], lo[2] + f * (hi[2] - lo[2])
        key = net.spans[lo[1]].name_key
        if key and key == net.spans[hi[1]].name_key:
            for chain in net.chains(key):
                if lo[1] in chain.where and hi[1] in chain.where:
                    a, b = chain.coord(lo[1], lo[2]), chain.coord(hi[1], hi[2])
                    return chain.locate(a + f * (b - a))
        same_lo, same_hi = lo[0] // 100 == h // 100, hi[0] // 100 == h // 100   # same hundred block wins
        near = (lo if same_lo else hi) if same_lo != same_hi else (lo if h - lo[0] <= hi[0] - h else hi)
    else:
        near = lo or hi
    if near[1] not in slopes:                       # metres per house number of this street on near's span
        pts = [(row[0], row[2]) for row in placed if row[1] == near[1]]
        mean_h = sum(p[0] for p in pts) / len(pts)
        spread = sum((p[0] - mean_h) ** 2 for p in pts)
        slopes[near[1]] = max(-10.0, min(10.0, sum((p[0] - mean_h) * p[1] for p in pts) / spread)) if spread else 0.0
    return near[1], min(max(near[2] + slopes[near[1]] * (h - near[0]), 0.0), net.spans[near[1]].len_m)


# ---- sides -----------------------------------------------------------------------------------------------------
def assign_sides(net, placements):
    """even_left per span by majority of the stops snapped to their own street (tiers geocode + fuzzy: parity vs
    side_lr; a 'nearest' stop's number belongs to another street). Ties and spans without votes stay unset.
    Fills side_lr of interpolated stops from it (0 when the span has no majority) and sets Span.even_left.
    Returns ({span_id: 1 even numbers on the left of a->b | 0}, share of votes agreeing with their span's majority,
    None without votes)."""
    votes = defaultdict(lambda: [0, 0])
    for p in placements:
        if p is not None and p.span_id is not None and p.placed_by in ('geocode', 'fuzzy') and p.side_lr \
                and p.parity is not None:
            votes[p.span_id][0 if (p.parity == 0) == (p.side_lr > 0) else 1] += 1
    even_left = {sid: int(a > b) for sid, (a, b) in sorted(votes.items()) if a != b}
    total = sum(a + b for a, b in votes.values())
    agreement = round(sum(max(a, b) for a, b in votes.values()) / total, 4) if total else None
    for sid, value in even_left.items():
        net.spans[sid].even_left = value
    for p in placements:
        if p is not None and p.placed_by == 'interpolated' and p.span_id is not None and p.parity is not None:
            value = even_left.get(p.span_id)
            p.side_lr = 0 if value is None else (1 if (p.parity == 0) == (value == 1) else -1)
    return even_left, agreement
