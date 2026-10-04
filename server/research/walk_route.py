"""Walking order for a walk list (pure Python, no Django).

Stops become runs: one street side in one precinct, along spans that chain end to end, walkable in either direction.
Runs are ordered for the shortest total walk over the road tables of walk.sqlite3. Deadhead between runs is the
shortest path between the two stops (S1200 metres times ARTERIAL_FACTOR, 1.0) plus a crossing allowance when the next
run is the other side of the same street. The network is loaded around the stops and grown until every stop's block
is connected, so a step is never a straight line where the network has a path.

Runs whose ends lie within GROUP_GAP_M of each other form a group (a cluster of nearby streets). Each group is ordered
exactly as before on its own deadhead table: a few runs exactly; more by nearest-next from 20 starts, then 2-opt with
direction flips and or-opt, repeated from double-bridge kicks under a deterministic work cap. Groups are then ordered
on the straight-line gaps between their first and last stops, and each step between two groups is found by A* on the
network. Reported distances are true walking metres; the crossing allowance only steers the order (RoutePlan.cost).
Within a run the numbered doors are listed by house number in the walking direction.

The same input always gives the same plan: every search ends on a count of move evaluations (EVALS_PER_MS per budget
millisecond), never on the clock, so neither machine speed nor load changes the order. The one nondeterministic path
is the BACKSTOP, a wall-clock limit on the whole search far above what the work cap takes on real lists; it exists
only for pathological inputs or a machine stalled for seconds, and when it cuts a search short it logs a warning.
"""
import heapq
import json
import logging
import math
import time
from bisect import bisect_left, bisect_right
from collections import defaultdict, deque
from dataclasses import dataclass, field

INF = math.inf
ARTERIAL_FACTOR = 1.0  # cost per metre of S1200 deadhead; 1.0 is the plain shortest path
CROSSING_M = {'S1200': 60.0, 'S1400': 15.0}  # crossing effort, added to cost only
DETOUR = 1.5          # straight-line factor between stops the loaded network does not connect
EXACT_TASKS = 8       # up to this many runs the order is solved exactly
STARTS = 20           # nearest-next starts
NEIGHBOURS = 16       # candidate list length for 2-opt and or-opt
EVALS_PER_MS = 500    # move evaluations per budget millisecond: the deterministic cap that ends the search
BACKSTOP = 5.0        # seconds of wall clock for the whole search; a last resort, never reached on real lists
EPS = 1e-7
DEG_M = math.pi * 6371008.8 / 180
PAD_M = 400.0         # network loaded around the stops; grown PAD_GROWTH-fold while their blocks are not connected
PAD_GROWTH = 4
PAD_MAX_M = 102400.0
GROUP_GAP_M = 1500.0  # runs farther apart than this (straight line, end to end) are ordered as separate groups
NODE_SLACK_M = 2.0    # node positions are kept to the metre: the A* estimate stays this much under the straight line

log = logging.getLogger(__name__)


class RouteTooLarge(ValueError):
    """More runs in one group than the caller allows (plan_route max_runs): the order would take too long."""

    def __init__(self, runs):
        super().__init__('%d runs in one group' % runs)
        self.runs = runs


def edge_cost(len_m, kind):
    return float(len_m or 0) * (ARTERIAL_FACTOR if kind == 'S1200' else 1.0)


class _Deadline:
    """The BACKSTOP of one plan's search: a wall-clock instant shared by every group's search, logged once if passed."""
    __slots__ = ('at', 'tripped')

    def __init__(self, seconds):
        self.at, self.tripped = time.perf_counter() + seconds, False

    def passed(self, evals, max_evals, n):
        if time.perf_counter() <= self.at:
            return False
        if not self.tripped:
            self.tripped = True
            log.warning('walk route: the search passed its %.1f s backstop after %d of %d evaluations on %d items; '
                        'this plan may differ from a rebuild of the same list', BACKSTOP, evals, max_evals, n)
        return True


class Span:
    """A road_span row: junction-to-junction block, its walking cost and its centreline (parsed on first use)."""
    __slots__ = ('id', 'name_key', 'a', 'b', 'len_m', 'kind', 'cost', 'even_left', 'raw', '_pts', '_cum')

    def __init__(self, id, name_key, a, b, len_m, kind, cost, even_left, raw):
        self.id, self.name_key, self.a, self.b, self.kind = id, name_key, a, b, kind
        self.len_m, self.cost, self.even_left, self.raw = float(len_m or 0), cost, even_left, raw
        self._pts = self._cum = None

    @property
    def weight(self):
        return self.cost / self.len_m if self.len_m > 0 else 1.0

    def geometry(self):
        """Vertices and their offsets from `a`, scaled so the last offset is len_m."""
        if self._pts is None:
            pts = [(float(p[0]), float(p[1])) for p in json.loads(self.raw or '[]')]
            k, cum = (math.cos(math.radians(pts[0][1])) if pts else 1.0), [0.0]
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                cum.append(cum[-1] + math.hypot((x1 - x0) * k, y1 - y0))
            f = self.len_m / cum[-1] if cum[-1] > 0 else 0.0
            self._pts, self._cum = pts, [c * f for c in cum]
        return self._pts, self._cum

    def point(self, x):
        pts, cum = self.geometry()
        if not pts:
            return None
        if x <= 0 or len(pts) == 1:
            return pts[0]
        if x >= cum[-1]:
            return pts[-1]
        i = bisect_right(cum, x) - 1
        t = (x - cum[i]) / (cum[i + 1] - cum[i])
        (x0, y0), (x1, y1) = pts[i], pts[i + 1]
        return (x0 + t * (x1 - x0), y0 + t * (y1 - y0))

    def cut(self, x0, x1):
        """Centreline from offset x0 to offset x1 (towards `a` when x1 < x0)."""
        pts, cum = self.geometry()
        if not pts:
            return []
        lo, hi = (x0, x1) if x0 <= x1 else (x1, x0)
        out = [self.point(lo)] + pts[bisect_right(cum, lo):bisect_left(cum, hi)] + [self.point(hi)]
        return out if x0 <= x1 else out[::-1]


@dataclass
class Graph:
    adj: dict = field(default_factory=dict)    # node -> [(neighbour, cost, span id)]; links are whole spans
    spans: dict = field(default_factory=dict)  # span id -> Span
    _index: tuple = field(default=None, repr=False, compare=False)
    _links: tuple = field(default=None, repr=False, compare=False)
    _read: dict = field(default=None, repr=False, compare=False)   # span id -> (Span, edge ids) read from road_span

    def index(self):
        """({node: dense index}, [[(neighbour index, cost, metres)] per index]), built once and shared by the groups.
        Dense indices follow node ids, so a search over them breaks ties exactly as one over the ids would."""
        if self._index is None:
            nodes, spans = sorted(self.adj), self.spans
            at = {n: i for i, n in enumerate(nodes)}
            self._index = (at, [[(at[v], w, spans[sid].len_m) for v, w, sid in self.adj[n]] for n in nodes])
        return self._index

    def links(self):
        """([node id per dense index], [[span id] per index, in the order of index()'s links]), built on first use."""
        if self._links is None:
            nodes = sorted(self.adj)
            self._links = (nodes, [[sid for _, _, sid in self.adj[n]] for n in nodes])
        return self._links


@dataclass
class Run:
    street_id: int
    street: str
    side: str                 # 'even' | 'odd'
    precinct: str
    stop_ids: list            # walk order
    walk_m: float             # along the run, first stop to last stop
    lr: int                   # walked side relative to the path direction: 1 left, -1 right, 0 unknown
    path: list                # [[lon, lat], ...] centreline, first stop to last stop, in walk direction
    approach: list            # [[lon, lat], ...] from the previous run's last stop ([] for the first run)
    approach_m: float = 0.0   # walking metres along `approach`
    from_house: str = ''
    to_house: str = ''


@dataclass
class RoutePlan:
    runs: list = field(default_factory=list)
    total_m: float = 0.0        # walking metres: walk_m plus approach_m over the runs
    baseline_m: float = 0.0     # walking metres of the same runs in house-number order
    cost: float = 0.0           # what the order minimises: total_m plus crossing allowances (S1200 x ARTERIAL_FACTOR)
    baseline_cost: float = 0.0  # the same for house-number order; cost <= baseline_cost


def dijkstra(adj, source, targets=None):
    """Shortest paths from `source` over adj {node: [(neighbour, cost, link id)]}.

    Returns (dist, prev) for settled nodes, prev[node] = (previous node, link id). With `targets` the search stops as
    soon as every reachable target is settled."""
    dist, prev, best = {}, {}, {source: 0.0}
    heap, left = [(0.0, source)], None if targets is None else set(targets)
    while heap:
        d, u = heapq.heappop(heap)
        if u in dist:
            continue
        dist[u] = d
        if left is not None:
            left.discard(u)
            if not left:
                break
        for v, w, link in adj.get(u, ()):
            nd = d + w
            if nd < best.get(v, INF):
                best[v], prev[v] = nd, (u, link)
                heapq.heappush(heap, (nd, v))
    return dist, prev


def _astar(graph, xy, starts, goals):
    """Cheapest way over graph.adj from any of `starts` to any of `goals`, both {node: (cost, metres, ...)} of
    getting on at that node and off from it: (cost, metres, first node, last node, [(node, span id), ...] from the
    first node), or None when no goal is reachable. The estimate is the straight line (xy metres) to the nearest goal
    less NODE_SLACK_M, never more than the cost still to come, so the answer is the cheapest; without positions for
    every goal it is 0 (Dijkstra). `xy` holds (x, y) or None by graph.index() position.

    The search runs on graph.index()'s dense arrays, numbered in node id order, so the heap pops (estimate, cost,
    node) in the same order as it would over the ids and equal-cost ties resolve the same way. Each node's estimate
    is worked out once and reused (the same float every time)."""
    at, nb = graph.index()
    nodes, sids = graph.links()
    goal = {at[n]: goals[n][0] for n in goals}
    targets = [xy[i] for i in goal if xy[i] is not None]
    plain = len(targets) < len(goal)
    count = len(nb)
    best, size, prev, est = [INF] * count, [0.0] * count, [None] * count, [-1.0] * count
    hypot, push, pop = math.hypot, heapq.heappush, heapq.heappop
    (qx, qy), (rx, ry) = targets[0] if targets else (0.0, 0.0), targets[-1] if targets else (0.0, 0.0)

    def h(i):
        """max(0, min(straight line to each goal) - NODE_SLACK_M), as min() and max() would pick (a goal span has
        two ends at most)."""
        p = xy[i]
        if p is None or plain:
            return 0.0
        if len(targets) > 2:
            v = min(hypot(p[0] - q[0], p[1] - q[1]) for q in targets)
        else:
            v = hypot(p[0] - qx, p[1] - qy)
            w = hypot(p[0] - rx, p[1] - ry)
            if w < v:
                v = w
        v -= NODE_SLACK_M
        return v if v > 0.0 else 0.0
    heap = []
    for n in sorted(starts):
        i, c = at[n], starts[n][0]
        best[i], size[i] = c, starts[n][1]
        hv = est[i] = h(i)
        push(heap, (c + hv, c, i))
    found = None
    while heap:
        f, d, u = pop(heap)
        if d > best[u]:
            continue
        if found is not None and f >= found[0]:
            break
        g = goal.get(u)
        if g is not None and (found is None or d + g < found[0]):
            found = (d + g, u)
        su = size[u]
        for (v, w, metres), sid in zip(nb[u], sids[u]):
            nd = d + w
            if nd < best[v]:
                best[v], size[v], prev[v] = nd, su + metres, (u, sid)
                hv = est[v]
                if hv < 0.0:
                    hv = est[v] = h(v)
                push(heap, (nd + hv, nd, v))
    if found is None:
        return None
    links, node = [], found[1]
    while prev[node] is not None:
        u, sid = prev[node]
        links.append((nodes[u], sid))
        node = u
    last = nodes[found[1]]
    return found[0], size[found[1]] + goals[last][1], nodes[node], last, links[::-1]


def _plain(conn):
    """A cursor that returns plain tuples whatever the connection's row_factory (the same values, built faster)."""
    cursor = conn.cursor()
    cursor.row_factory = None
    return cursor


def _rows(conn, sql, ids):
    """The rows of `sql` (an IN ({}) query) for `ids` as tuples, by chunks of 900 ids in id order, in one list."""
    ids, out, cursor = sorted(ids), [], _plain(conn)
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        out.extend(cursor.execute(sql.format(','.join('?' * len(chunk))), chunk))
    return out


def load_graph(conn, bbox, pad_m=PAD_M, *, span_ids=(), reuse=None):
    """Spans with an edge inside bbox (west, south, east, north; degrees) grown by pad_m, plus `span_ids`.

    A span costs the sum of its edges' costs (len_m, x ARTERIAL_FACTOR on S1200). Edges without a span stand alone.
    `reuse`, a graph loaded earlier from the same connection, lends the spans it read: a span is the same whatever box
    loaded it (its row, and its cost over all of its edges), so only spans new to this box are read and built."""
    west, south, east, north = bbox
    dlat = pad_m / DEG_M
    dlon = dlat / max(math.cos(math.radians((south + north) / 2)), 0.01)
    edges = {r[0]: r for r in _plain(conn).execute(
        'SELECT e.id,e.u,e.v,e.len_m,e.kind,e.name_key,e.span_id FROM road_edge_box x JOIN road_edge e ON e.id=x.id '
        'WHERE x.west<=? AND x.east>=? AND x.south<=? AND x.north>=?',
        (east + dlon, west - dlon, north + dlat, south - dlat))}
    wanted = {r[6] for r in edges.values() if r[6] is not None} | {s for s in span_ids if s is not None}
    known = reuse._read if reuse is not None and reuse._read is not None else {}
    read = {sid: known[sid] for sid in wanted if sid in known}
    rows = _rows(conn, 'SELECT id,name_key,a,b,len_m,kind,edges,coords,even_left FROM road_span WHERE id IN ({})',
                 [sid for sid in wanted if sid not in read])
    lists = [[abs(int(e)) for e in ids] for ids in json.loads('[%s]' % ','.join(r[6] or '[]' for r in rows))]
    missing = ({e for ids in lists for e in ids} | {e for _, ids in read.values() for e in ids}) - edges.keys()
    edges.update((r[0], r) for r in _rows(
        conn, 'SELECT id,u,v,len_m,kind,name_key,span_id FROM road_edge WHERE id IN ({})', missing))
    each = {e: edge_cost(r[3], r[4]) for e, r in edges.items()}
    for r, ids in zip(rows, lists):    # a span costs sum() of its edges' costs in its own order (one summation)
        costs = [each.get(e) for e in ids]
        cost = sum(costs) if ids and None not in costs else edge_cost(r[4], r[5])
        read[r[0]] = (Span(r[0], r[1], r[2], r[3], r[4], r[5], cost, r[8], r[7]), ids)
    spans = {sid: read[sid][0] for sid in sorted(read)}   # by id, the order the spans are built and linked in
    alone = sorted(e for e, r in edges.items() if r[6] not in spans)
    for eid, coords in _rows(conn, 'SELECT id,coords FROM road_edge WHERE id IN ({})', alone):
        r = edges[eid]
        spans[-eid] = Span(-eid, r[5], r[1], r[2], r[3], r[4], edge_cost(r[3], r[4]), None, coords)
    adj = defaultdict(list)
    for sid in sorted(spans):  # a closed loop (a == b) stays a link of its node; it never shortens a path
        sp = spans[sid]
        adj[sp.a].append((sp.b, sp.cost, sid))
        if sp.b != sp.a:
            adj[sp.b].append((sp.a, sp.cost, sid))
    return Graph(dict(adj), spans, _read=read)


def _connected(adj, nodes):
    """True when every node of `nodes` is reachable from the others over adj."""
    nodes = sorted(set(nodes))
    if len(nodes) < 2:
        return True
    left, seen, todo = set(nodes[1:]), {nodes[0]}, [nodes[0]]
    while todo and left:
        for v, _, _ in adj.get(todo.pop(), ()):
            if v not in seen:
                seen.add(v)
                left.discard(v)
                todo.append(v)
    return not left


def _node_matrix(graph, terms):
    """Shortest-path costs between the nodes `terms` and the true lengths of those paths, both symmetric (INF where
    the loaded network does not connect). Each search stops once the terms it still needs are settled, so the terms
    of a compact group only look at their own part of the network."""
    at, nb = graph.index()
    tix, k = [at.get(n, -1) for n in terms], len(terms)
    D, L = [[INF] * k for _ in range(k)], [[INF] * k for _ in range(k)]
    for r in range(k):
        D[r][r] = L[r][r] = 0.0
    dist, size = [INF] * len(nb), [0.0] * len(nb)
    push, pop = heapq.heappush, heapq.heappop
    for r in range(k - 1):
        want = {tix[c]: c for c in range(r + 1, k) if tix[c] >= 0}
        if tix[r] < 0 or not want:
            continue
        dist[tix[r]], heap, left, touched = 0.0, [(0.0, tix[r])], len(want), [tix[r]]
        while heap:
            d, u = pop(heap)
            if d > dist[u]:
                continue
            if u in want:
                c = want[u]
                D[r][c] = D[c][r] = d
                L[r][c] = L[c][r] = size[u]
                left -= 1
                if not left:
                    break
            su = size[u]
            for v, w, metres in nb[u]:
                nd = d + w
                dv = dist[v]
                if nd < dv:
                    if dv == INF:
                        touched.append(v)
                    dist[v], size[v] = nd, su + metres
                    push(heap, (nd, v))
        for v in touched:
            dist[v], size[v] = INF, 0.0
    return D, L


def _metres(p, q):
    k = math.cos(math.radians((p[1] + q[1]) / 2))
    return math.hypot((q[0] - p[0]) * k, q[1] - p[1]) * DEG_M


def _extend(out, pts):
    for p in pts:
        if not out or p != out[-1]:
            out.append(p)
    return out


def _coords(pts):
    out = _extend([], [[round(p[0], 6), round(p[1], 6)] for p in pts if p is not None])
    return out + out[-1:] if len(out) == 1 else out


class _Stop:
    __slots__ = ('id', 'street_id', 'street', 'parity', 'precinct', 'span', 'x', 'house_int', 'house', 'side', 'pos')


def _field(s, key):
    try:
        return s[key]
    except (KeyError, IndexError):
        return None


def _stop(s):
    st = _Stop()
    st.id, st.street_id, st.street = _field(s, 'id'), _field(s, 'street_id'), _field(s, 'street') or ''
    st.parity, st.precinct = int(_field(s, 'parity') or 0) & 1, _field(s, 'precinct') or ''
    st.span, st.x, st.house_int = _field(s, 'span_id'), _field(s, 'pos_m'), _field(s, 'house_int')
    st.house, st.side, st.pos = _field(s, 'house') or '', int(_field(s, 'side_lr') or 0), 0.0
    return st


def _house_key(s):
    return (s.house_int is None, s.house_int or 0, s.house, s.id)


def _rising(pairs):
    """True when house numbers grow along the chain: the sign of their least-squares slope over (position, number)
    pairs in chain order, so one door geocoded a few metres off does not turn the run round; a flat or single
    number reads by its first and last door."""
    if len(pairs) < 2:
        return True
    mp, mh = sum(p for p, _ in pairs) / len(pairs), sum(h for _, h in pairs) / len(pairs)
    slope = sum((p - mp) * (h - mh) for p, h in pairs)
    return slope > 0 if abs(slope) > EPS else pairs[0][1] <= pairs[-1][1]


class _Task:
    __slots__ = ('street_id', 'street', 'parity', 'precinct', 'stops', 'chain', 'walk_m', 'ascending', 'cycle')

    def __init__(self, stops, chain, cycle=False):
        first, last = stops[0], stops[-1]
        self.street_id, self.street, self.parity = first.street_id, first.street, first.parity
        self.precinct, self.stops, self.chain, self.walk_m = first.precinct, stops, chain, last.pos - first.pos
        self.ascending = _rising([(s.pos, s.house_int) for s in stops if s.house_int is not None])  # a blank has no say
        self.cycle = cycle


def _heading(sp, end):
    pts = sp.geometry()[0]
    if len(pts) < 2:
        return 0.0, 0.0
    (x0, y0), (x1, y1) = (pts[0], pts[1]) if end == 0 else (pts[-1], pts[-2])
    dx, dy = (x1 - x0) * math.cos(math.radians(y0)), y1 - y0
    n = math.hypot(dx, dy) or 1.0
    return dx / n, dy / n


def _chains(graph, span_ids):
    """Split spans into chains [(span id, traversed a->b)] joined at shared nodes with the same name_key.

    Where more than two such span ends meet, the straightest pairs continue. Returns [(chain, is_cycle)]."""
    at = defaultdict(list)
    for sid in sorted(span_ids):
        sp = graph.spans[sid]
        at[(sp.a, sp.name_key or '')].append((sid, 0))
        at[(sp.b, sp.name_key or '')].append((sid, 1))
    mate = {}
    for key in sorted(at):
        ends = at[key]
        if len(ends) == 2:
            mate[ends[0]], mate[ends[1]] = ends[1], ends[0]
        elif len(ends) > 2:
            h = {e: _heading(graph.spans[e[0]], e[1]) for e in ends}
            pairs = sorted((h[p][0] * h[q][0] + h[p][1] * h[q][1], p, q)
                           for i, p in enumerate(ends) for q in ends[i + 1:] if p[0] != q[0])
            for _, p, q in pairs:
                if p not in mate and q not in mate:
                    mate[p], mate[q] = q, p
    seen, out = set(), []

    def follow(sid, end):
        chain = []
        while sid not in seen:
            seen.add(sid)
            chain.append((sid, end == 0))
            nxt = mate.get((sid, 1 - end))
            if nxt is None:
                break
            sid, end = nxt
        return chain
    for sid in sorted(span_ids):
        for end in (0, 1):
            if sid not in seen and (sid, end) not in mate:
                out.append((follow(sid, end), False))
    for sid in sorted(span_ids):
        if sid not in seen:
            out.append((follow(sid, 0), True))
    return out


def _lay(graph, chain, on, cycle):
    """Chain offsets [(Span, forward, offset)] with each stop's chain position set. A cycle (a ring of spans or one
    closed span) is laid out twice and its stops start after the longest stretch without stops, so that stretch,
    wherever it lies, is the part left unwalked."""
    laid, off = [], 0.0
    for sid, fwd in chain * (2 if cycle else 1):
        sp = graph.spans[sid]
        laid.append((sp, fwd, off))
        off += sp.len_m
    for sp, fwd, o in laid[:len(chain)]:
        for s in on[sp.id]:
            s.pos = o + (s.x if fwd else sp.len_m - s.x)
    if cycle:
        ring = sorted((s for sid, _ in chain for s in on[sid]), key=lambda s: (s.pos,) + _house_key(s))
        gaps = [b.pos - a.pos for a, b in zip(ring, ring[1:])] + [ring[0].pos + off / 2 - ring[-1].pos]
        k = max(range(len(ring)), key=lambda k: (gaps[k], -k))
        for s in ring[:k + 1] if k < len(ring) - 1 else ():
            s.pos += off / 2
    return laid


def _tasks(graph, stops):
    groups = defaultdict(list)
    for s in stops:
        groups[(s.street_id, s.parity)].append(s)
    tasks = []
    for key in sorted(groups, key=lambda k: (k[0] is None, k[0] or 0, k[1])):
        on = defaultdict(list)
        for s in groups[key]:
            on[s.span].append(s)
        for chain, cycle in _chains(graph, on):
            laid = _lay(graph, chain, on, cycle)
            run = []
            for s in sorted((s for sid, _ in chain for s in on[sid]), key=lambda s: (s.pos,) + _house_key(s)):
                if run and s.precinct != run[-1].precinct:
                    tasks.append(_Task(run, laid, cycle))
                    run = []
                run.append(s)
            if run:
                tasks.append(_Task(run, laid, cycle))
    return tasks


def _baseline(tasks):
    """House-number order: street label, even before odd, house ascending, each run walked up its numbers."""
    key = lambda i: (tasks[i].street, tasks[i].street_id or 0, tasks[i].parity,
                     min(_house_key(s) for s in tasks[i].stops), i)
    return [2 * i + (not tasks[i].ascending) for i in sorted(range(len(tasks)), key=key)]


class _Order:
    """Items with two ends each (item i has ends 2i and 2i+1) and a symmetric cost G between ends of different items.

    State 2i+o takes item i forwards (o=0) or backwards (o=1): it is entered at end 2i+o and left at the other end,
    so a step s -> t costs G[s ^ 1][t]. `points` ([lon, lat] per end, or a point(e) method) place the starts."""

    def __init__(self, G, points=None):
        self.G, self.points, self.n = G, points, len(G) // 2
        self.order = [[f for f in sorted(range(len(G)), key=row.__getitem__) if f >> 1 != e >> 1] for e, row in enumerate(G)]
        self.near = [o[:NEIGHBOURS] for o in self.order]

    def point(self, e):
        return self.points[e]

    def cost(self, tour):
        G = self.G
        return sum(G[s ^ 1][t] for s, t in zip(tour, tour[1:]))

    def starts(self):
        """Entry states at the periphery: items whose far end lies farthest from the centroid of all ends."""
        pts = [self.point(e) for e in range(2 * self.n)]
        c = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        far = []
        for i in range(self.n):
            d0, d1 = _metres(pts[2 * i], c), _metres(pts[2 * i + 1], c)
            far.append((-max(d0, d1), i, d0 < d1))
        return [2 * i + o for _, i, o in sorted(far)[:STARTS]]

    def nearest(self, start):
        G, T = self.G, self.n
        lists, done, ptr = self.order, bytearray(T), [0] * len(G)
        done[start >> 1], tour, total, cur = 1, [start], 0.0, start
        for _ in range(T - 1):
            e = cur ^ 1
            lst, p = lists[e], ptr[e]
            while done[lst[p] >> 1]:
                p += 1
            ptr[e], cur = p, lst[p]
            done[cur >> 1] = 1
            total += G[e][cur]
            tour.append(cur)
        return total, tour

    def exact(self):
        """Shortest tour by dynamic programming over (items visited, last state); for a handful of items."""
        G, T = self.G, self.n
        best = [{} for _ in range(1 << T)]
        for s in range(2 * T):
            best[1 << (s >> 1)][s] = (0.0, -1)
        for mask in range(1, 1 << T):
            for s, (c, _) in best[mask].items():
                row = G[s ^ 1]
                for t in range(2 * T):
                    if not mask >> (t >> 1) & 1:
                        here = best[mask | 1 << (t >> 1)]
                        if t not in here or c + row[t] < here[t][0] - EPS:
                            here[t] = (c + row[t], s)
        mask = (1 << T) - 1
        s = min(best[mask], key=lambda s: (best[mask][s][0], s))
        tour = []
        while s >= 0:
            tour.append(s)
            s, mask = best[mask][s][1], mask ^ 1 << (s >> 1)
        return tour[::-1]

    def solve(self, max_evals, deadline):
        """A short tour of all items: exact for a few; else the best nearest-next tour from the starts, improved by
        _Search within max_evals move evaluations (`deadline`, a _Deadline, is only the backstop)."""
        if self.n > EXACT_TASKS:
            best = None
            for s in self.starts():
                c, t = self.nearest(s)
                if best is None or c < best[0] - EPS:
                    best = (c, t)
            search = _Search(self, best[1], max_evals, deadline)
            return search.iterate(self.cost, max(50, 2 * self.n)) if search.run() else search.tour
        return self.exact() if self.n > 1 else [0]


class _Model(_Order):
    """Task ends and the symmetric deadhead matrix G between them.

    End 2i is task i's first stop in chain order, end 2i+1 its last. State 2i+o walks task i forwards (o=0) or
    backwards (o=1): it is entered at end 2i+o and left at the other end, so a step s -> t costs G[s ^ 1][t]."""

    def __init__(self, graph, tasks):
        self.graph, self.tasks = graph, tasks
        self.ends = [(graph.spans[s.span], s.x) for t in tasks for s in (t.stops[0], t.stops[-1])]
        terms = sorted({n for sp, _ in self.ends for n in (sp.a, sp.b)})
        self.at = at = {n: i for i, n in enumerate(terms)}
        self.D, self.L = _node_matrix(graph, terms)
        D = self.D
        m = len(self.ends)
        A, B = [at[sp.a] for sp, _ in self.ends], [at[sp.b] for sp, _ in self.ends]
        self.CA = CA = [x * sp.weight for sp, x in self.ends]
        self.CB = CB = [(sp.len_m - x) * sp.weight for sp, x in self.ends]
        SP, X = [sp for sp, _ in self.ends], [x for _, x in self.ends]
        street = [t.street_id for t in tasks for _ in (0, 1)]
        parity = [t.parity for t in tasks for _ in (0, 1)]
        self.points = [None] * m
        G = [[INF] * m for _ in range(m)]
        for e in range(m):
            ca, cb, sp_e, x_e, st_e, pa_e, row = CA[e], CB[e], SP[e], X[e], street[e], parity[e], G[e]
            R = [p + ca if p + ca <= q + cb else q + cb for p, q in zip(D[A[e]], D[B[e]])]
            for f in range(e + 1 + (not e & 1), m):
                v, w = R[A[f]] + CA[f], R[B[f]] + CB[f]
                if w < v:
                    v = w
                if SP[f] is sp_e and abs(x_e - X[f]) * sp_e.weight < v:
                    v = abs(x_e - X[f]) * sp_e.weight
                if v == INF:
                    v = DETOUR * _metres(self.point(e), self.point(f))
                if street[f] == st_e and parity[f] != pa_e:
                    v += CROSSING_M['S1200' if 'S1200' in (sp_e.kind, SP[f].kind) else 'S1400']
                row[f] = G[f][e] = v
        _Order.__init__(self, G, self.points)

    def point(self, e):
        if self.points[e] is None:
            sp, x = self.ends[e]
            self.points[e] = sp.point(x) or (0.0, 0.0)
        return self.points[e]

    def baseline(self):
        return _baseline(self.tasks)

    def way(self, e, f):
        """Cheapest way from end e (a run's last stop) to end f (the next run's first stop): None when the network
        does not connect them, 'direct' along their shared span, or (i, j): leave e's span by its a (0) or b (1) end,
        enter f's span by its a or b end."""
        (sp_e, x_e), (sp_f, x_f) = self.ends[e], self.ends[f]
        D, at = self.D, self.at
        best = (abs(x_e - x_f) * sp_e.weight, 'direct') if sp_e is sp_f else (INF, None)
        for i, (ne, ce) in enumerate(((sp_e.a, self.CA[e]), (sp_e.b, self.CB[e]))):
            for j, (nf, cf) in enumerate(((sp_f.a, self.CA[f]), (sp_f.b, self.CB[f]))):
                c = ce + D[at[ne]][at[nf]] + cf
                if c < best[0]:
                    best = (c, (i, j))
        return best[1]

    def metres(self, e, f):
        """True walking length of way(e, f); a crossing allowance is cost, not distance."""
        (sp_e, x_e), (sp_f, x_f), way = self.ends[e], self.ends[f], self.way(e, f)
        if way is None:
            return _metres(self.point(e), self.point(f))
        if way == 'direct':
            return abs(x_e - x_f)
        i, j = way
        return ((sp_e.len_m - x_e) if i else x_e) + self.L[self.at[sp_e.b if i else sp_e.a]][
            self.at[sp_f.b if j else sp_f.a]] + ((sp_f.len_m - x_f) if j else x_f)

    def approach(self, e, f):
        """Coordinates of way(e, f) and their true length in metres."""
        (sp_e, x_e), (sp_f, x_f), way = self.ends[e], self.ends[f], self.way(e, f)
        if way is None:
            return _coords([self.point(e), self.point(f)]), _metres(self.point(e), self.point(f))
        if way == 'direct':
            return _coords(sp_e.cut(x_e, x_f)), abs(x_e - x_f)
        i, j = way
        pts, metres = sp_e.cut(x_e, sp_e.len_m if i else 0.0), (sp_e.len_m - x_e) if i else x_e
        ne, nf = (sp_e.b if i else sp_e.a), (sp_f.b if j else sp_f.a)
        if ne != nf:
            _, prev = dijkstra(self.graph.adj, ne, (nf,))
            links, v = [], nf
            while v != ne:
                u, sid = prev[v]
                links.append((u, sid))
                v = u
            for u, sid in reversed(links):
                sp = self.graph.spans[sid]
                p = sp.geometry()[0]
                _extend(pts, p if sp.a == u else p[::-1])
                metres += sp.len_m
        metres += (sp_f.len_m - x_f) if j else x_f
        return _coords(_extend(pts, sp_f.cut(sp_f.len_m if j else 0.0, x_f))), metres

    def totals(self, tour):
        """(walking metres, cost) of a tour with every run and step rounded to 0.1 m, as the runs report them."""
        walk = sum(round(self.tasks[s >> 1].walk_m, 1) for s in tour)
        steps = [(s ^ 1, t) for s, t in zip(tour, tour[1:])]
        return (round(walk + sum(round(min(self.metres(e, f), self.G[e][f]), 1) for e, f in steps), 1),
                round(walk + sum(round(self.G[e][f], 1) for e, f in steps), 1))


def _groups(points):
    """Task indices in groups: tasks i (ends 2i, 2i+1 at `points`) whose ends share a GROUP_GAP_M grid cell or sit in
    neighbouring cells belong together (single linkage). Each group lists its tasks in order; groups by first task."""
    n = len(points) // 2
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        i, j = find(i), find(j)
        if i != j:
            parent[max(i, j)] = min(i, j)
    if n:
        k = math.cos(math.radians(sum(p[1] for p in points) / len(points))) * DEG_M / GROUP_GAP_M
        cells = {}
        for e, (lon, lat) in enumerate(points):
            cell = (math.floor(lon * k), math.floor(lat * DEG_M / GROUP_GAP_M))
            if cell in cells:
                union(e >> 1, cells[cell])
            else:
                cells[cell] = e >> 1
        for (cx, cy), i in sorted(cells.items()):
            for dx, dy in ((1, -1), (1, 0), (1, 1), (0, 1)):
                j = cells.get((cx + dx, cy + dy))
                if j is not None:
                    union(i, j)
    out = defaultdict(list)
    for i in range(n):
        out[find(i)].append(i)
    return [out[r] for r in sorted(out)]


class _Route:
    """All runs of a plan. Runs whose ends lie near each other form a group (_groups), ordered exactly as a plan of
    that group alone would be (_Model: every deadhead between its runs by Dijkstra). Groups are then ordered on the
    straight-line gaps between their first and last stops; each step between two groups is found by A* on the same
    network, so every reported metre is a walked metre. With one group this is the plain _Model plan."""

    def __init__(self, conn, graph, tasks, max_runs=None):
        self.conn, self.graph, self.tasks = conn, graph, tasks
        self.ends = [(graph.spans[s.span], s.x) for t in tasks for s in (t.stops[0], t.stops[-1])]
        self.points = [sp.point(x) or (0.0, 0.0) for sp, x in self.ends]
        self.groups = _groups(self.points)
        largest = max(len(g) for g in self.groups)
        if max_runs is not None and largest > max_runs:
            raise RouteTooLarge(largest)
        self.models, self.home, self.xy, self.steps = [], {}, None, {}
        for g, members in enumerate(self.groups):
            self.models.append(_Model(graph, [tasks[i] for i in members]))
            for j, i in enumerate(members):
                self.home[i] = (g, j)

    def baseline(self):
        return _baseline(self.tasks)

    def order(self, budget_ms):
        """The shortest tour found: each group's own (never worse than its house-number order), groups in order. The
        work of every search is capped by budget_ms * EVALS_PER_MS move evaluations, shared by the groups by their
        runs; BACKSTOP seconds from here is the wall-clock last resort."""
        deadline, tours = _Deadline(BACKSTOP), []
        for members, model in zip(self.groups, self.models):
            base = tour = model.baseline()
            if len(members) > 1:
                tour = model.solve(max(1, round(budget_ms * EVALS_PER_MS * len(members) / len(self.tasks))), deadline)
                if model.totals(base)[1] < model.totals(tour)[1]:
                    tour = base
            tours.append([2 * members[s >> 1] + (s & 1) for s in tour])
        if len(tours) == 1:
            return tours[0]
        ends = [e for t in tours for e in (t[0], t[-1] ^ 1)]   # a group is entered at its first end, left at its last
        pts = [self.points[e] for e in ends]
        G = [[INF] * len(ends) for _ in ends]
        for x in range(len(ends)):
            for y in range(x + 1 + (not x & 1), len(ends)):
                G[x][y] = G[y][x] = _metres(pts[x], pts[y])
        out = []
        for s in _Order(G, pts).solve(max(1, budget_ms * EVALS_PER_MS // 10), deadline):
            out.extend(tours[s >> 1] if not s & 1 else [x ^ 1 for x in reversed(tours[s >> 1])])
        return out

    def _local(self, e, f):
        (ge, je), (gf, jf) = self.home[e >> 1], self.home[f >> 1]
        return (self.models[ge], 2 * je + (e & 1), 2 * jf + (f & 1)) if ge == gf else (None, e, f)

    def _cross(self, e, f):
        """(cost, metres, coords) of the cheapest way from end e (a run's last stop) to end f (the next run's first
        stop) of another group: A* over the network, the crossing allowance as in _Model."""
        if (e, f) in self.steps:
            return self.steps[(e, f)]
        if self.xy is None:                    # positions of the loaded network's nodes, by graph.index() position
            at, _ = self.graph.index()
            self.xy = [None] * len(at)
            for n, x, y in _rows(self.conn, 'SELECT id, x, y FROM road_node WHERE id IN ({})', at):
                if x is not None and y is not None:
                    self.xy[at[n]] = (x, y)
        (sp_e, x_e), (sp_f, x_f) = self.ends[e], self.ends[f]
        starts, goals = {}, {}
        for out, sp, x, towards in ((starts, sp_e, x_e, True), (goals, sp_f, x_f, False)):
            for node, along in ((sp.a, x), (sp.b, sp.len_m - x)):
                if node not in out or along * sp.weight < out[node][0]:
                    out[node] = (along * sp.weight, along, 0.0 if node == sp.a and along == x else sp.len_m)
        found = _astar(self.graph, self.xy, starts, goals)
        direct = abs(x_e - x_f) * sp_e.weight if sp_e is sp_f else INF
        if direct < INF and (found is None or direct <= found[0]):
            cost, metres, coords = direct, abs(x_e - x_f), _coords(sp_e.cut(x_e, x_f))
        elif found is None:
            p, q = self.points[e], self.points[f]
            cost, metres, coords = DETOUR * _metres(p, q), _metres(p, q), _coords([p, q])
        else:
            cost, metres, first, last, links = found
            pts = sp_e.cut(x_e, starts[first][2])
            for u, sid in links:
                sp = self.graph.spans[sid]
                p = sp.geometry()[0]
                _extend(pts, p if sp.a == u else p[::-1])
            coords = _coords(_extend(pts, sp_f.cut(goals[last][2], x_f)))
        a, b = self.tasks[e >> 1], self.tasks[f >> 1]
        if a.street_id == b.street_id and a.parity != b.parity:
            cost += CROSSING_M['S1200' if 'S1200' in (sp_e.kind, sp_f.kind) else 'S1400']
        self.steps[(e, f)] = (cost, metres, coords)
        return self.steps[(e, f)]

    def g(self, e, f):
        model, a, b = self._local(e, f)
        return model.G[a][b] if model else self._cross(e, f)[0]

    def metres(self, e, f):
        model, a, b = self._local(e, f)
        return model.metres(a, b) if model else self._cross(e, f)[1]

    def approach(self, e, f):
        model, a, b = self._local(e, f)
        return model.approach(a, b) if model else (self._cross(e, f)[2], self._cross(e, f)[1])

    def totals(self, tour):
        """(walking metres, cost) of a tour with every run and step rounded to 0.1 m, as the runs report them."""
        walk = sum(round(self.tasks[s >> 1].walk_m, 1) for s in tour)
        steps = [(s ^ 1, t) for s, t in zip(tour, tour[1:])]
        return (round(walk + sum(round(min(self.metres(e, f), self.g(e, f)), 1) for e, f in steps), 1),
                round(walk + sum(round(self.g(e, f), 1) for e, f in steps), 1))


class _Search:
    """Local search on a tour of states: 2-opt with direction flips (segment reversal) and or-opt of 1-3 tasks in
    either direction, on candidate lists, re-examining only tasks next to changed edges; then double-bridge kicks
    from a fixed-seed generator, kept when they shorten the tour. Work is capped by a count of move evaluations
    (deterministic); the deadline is only the BACKSTOP, the one path on which the clock can change the result."""

    def __init__(self, model, tour, max_evals, deadline):
        self.G, self.near, self.tour, self.n = model.G, model.near, list(tour), len(tour)
        self.pos, self.queued, self.queue = [0] * self.n, bytearray(self.n), deque()
        self.evals, self.max_evals, self.deadline, self.seed = 0, max_evals, deadline, 0x5DEECE66D
        self.index()
        self.push(*self.tour)

    def index(self):
        for k, s in enumerate(self.tour):
            self.pos[s >> 1] = k

    def at(self, k):
        return self.tour[k] if 0 <= k < self.n else -1

    def push(self, *states):
        for s in states:
            if s >= 0 and not self.queued[s >> 1]:
                self.queued[s >> 1] = 1
                self.queue.append(s >> 1)

    def spent(self):
        return self.evals >= self.max_evals or self.deadline.passed(self.evals, self.max_evals, self.n)

    def flip(self, i, j):
        t = self.tour
        t[i:j + 1] = [s ^ 1 for s in reversed(t[i:j + 1])]
        for k in range(i, j + 1):
            self.pos[t[k] >> 1] = k
        self.push(self.at(i - 1), t[i], t[j], self.at(j + 1))

    def two_opt(self, k):
        """Reversals replacing the edge tour[k-1] -> tour[k]."""
        G, t, pos, n = self.G, self.tour, self.pos, self.n
        e, f = t[k - 1] ^ 1, t[k]
        row, col, old = G[e], G[f], G[e][f]
        for g in self.near[e]:  # tour[k-1] leads into tour[j] flipped: reverse k..j
            if row[g] >= old - EPS:
                break
            self.evals += 1
            j = pos[g >> 1]
            if j >= k and t[j] == g ^ 1:
                delta = row[g] - old
                if j + 1 < n:
                    delta += G[f][t[j + 1]] - G[t[j] ^ 1][t[j + 1]]
                if delta < -EPS:
                    self.flip(k, j)
                    return True
        for g in self.near[f]:  # tour[i] flipped leads into tour[k]: reverse i..k-1
            if col[g] >= old - EPS:
                break
            self.evals += 1
            i = pos[g >> 1]
            if i < k and t[i] == g:
                delta = col[g] - old
                if i:
                    p = t[i - 1] ^ 1
                    delta += G[p][e] - G[p][t[i]]
                if delta < -EPS:
                    self.flip(i, k - 1)
                    return True
        return False

    def or_opt(self, i):
        """Move tour[i:i+size] (size 1-3, either direction) beside a candidate neighbour."""
        G, near, t, pos, n = self.G, self.near, self.tour, self.pos, self.n
        for size in (1, 2, 3):
            if i + size > n or size >= n:
                return False
            seg = t[i:i + size]
            prev, nxt = self.at(i - 1), self.at(i + size)
            gain = (G[prev ^ 1][seg[0]] if prev >= 0 else 0.0) + (G[seg[-1] ^ 1][nxt] if nxt >= 0 else 0.0)
            if prev >= 0 and nxt >= 0:
                gain -= G[prev ^ 1][nxt]
            if gain <= EPS:
                continue
            best = None
            for rev in (False, True):
                entry, leave = (seg[-1] ^ 1, seg[0]) if rev else (seg[0], seg[-1] ^ 1)
                slots = [(-1, t[0] if i else nxt), (t[-1] if nxt >= 0 else prev, -1)]
                for g in near[entry]:
                    j = pos[g >> 1]
                    if not i <= j < i + size and t[j] == g ^ 1:
                        slots.append((t[j], nxt if j + 1 == i else self.at(j + 1)))
                for g in near[leave]:
                    j = pos[g >> 1]
                    if not i <= j < i + size and t[j] == g:
                        slots.append((prev if j == i + size else self.at(j - 1), t[j]))
                for p, q in slots:
                    self.evals += 1
                    add = (G[p ^ 1][entry] if p >= 0 else 0.0) + (G[leave][q] if q >= 0 else 0.0)
                    if p >= 0 and q >= 0:
                        add -= G[p ^ 1][q]
                    if add - gain < -EPS and (best is None or add - gain < best[0]):
                        best = (add - gain, rev, p)
            if best:
                _, rev, p = best
                self.push(prev, nxt)
                rest = t[:i] + t[i + size:]
                k = rest.index(p) + 1 if p >= 0 else 0
                t[:] = rest[:k] + ([s ^ 1 for s in reversed(seg)] if rev else seg) + rest[k:]
                self.index()
                self.push(*seg, self.at(k - 1), self.at(k + size))
                return True
        return False

    def run(self):
        """Improve until no queued task yields a move; False when the work cap (or the backstop) cut it short."""
        while self.queue:
            if self.spent():
                return False
            task = self.queue.popleft()
            self.queued[task] = 0
            k = self.pos[task]
            (k and self.two_opt(k)) or (k + 1 < self.n and self.two_opt(k + 1)) or self.or_opt(k)
        return True

    def rand(self, k):
        self.seed = (self.seed * 6364136223846793005 + 1442695040888963407) & 0xFFFFFFFFFFFFFFFF
        return (self.seed >> 33) % k

    def kick(self):
        """Double bridge on nearby segments: P X Y S becomes P Y X S."""
        t, n = self.tour, self.n
        w = min(n // 3, 50)
        a = 1 + self.rand(n - 3)
        b = min(n - 2, a + 1 + self.rand(w))
        c = min(n - 1, b + 1 + self.rand(w))
        t[:] = t[:a] + t[b:c] + t[a:b] + t[c:]
        self.index()
        self.evals += n  # the kick's own list work
        m = a + c - b
        self.push(t[a - 1], t[a], t[m - 1], t[m], t[c - 1], t[c])

    def iterate(self, cost, patience):
        """Kick and re-improve, keeping the shortest tour, until `patience` kicks in a row fail or work runs out."""
        best, best_cost, fails = list(self.tour), cost(self.tour), 0
        while self.n >= 8 and fails < patience and not self.spent():
            self.kick()
            done = self.run()
            c = cost(self.tour)
            if c < best_cost - EPS:
                best, best_cost, fails = list(self.tour), c, 0
            else:
                fails += 1
                self.tour[:] = best
                self.index()
            if not done:
                break
        return best


def _path(t, rev):
    lo, hi, out = t.stops[0].pos, t.stops[-1].pos, []
    for sp, fwd, off in t.chain:
        a, b = max(lo, off) - off, min(hi, off + sp.len_m) - off
        if a <= b:
            _extend(out, sp.cut(a, b) if fwd else sp.cut(sp.len_m - a, sp.len_m - b))
    return _coords(out[::-1] if rev else out)


def _lr(t, rev):
    laid, total = {sp.id: (sp, fwd) for sp, fwd, _ in t.chain}, 0
    for s in t.stops:
        sp, fwd = laid[s.span]
        side = s.side
        if not side and sp.even_left in (0, 1):
            side = 1 if (s.parity == 0) == (sp.even_left == 1) else -1
        total += side if fwd else -side
    total = -total if rev else total
    return (total > 0) - (total < 0)


def _side(parity):
    return 'odd' if parity else 'even'


def _walk_order(t, rev):
    """The stops of task t as the walker meets them: along the chain (backwards when rev), with the numbered doors
    then dealt out in house-number order, rising or falling with the walk, over the places numbered doors hold, so a
    list reads 4102, 4106, 4110 though a geocode put 4106 a few metres past 4110. A door without a number keeps its
    place; a ring keeps its chain order, since its numbers wrap where the ring was opened."""
    seq = t.stops[::-1] if rev else list(t.stops)
    if t.cycle:
        return seq
    slots = [k for k, s in enumerate(seq) if s.house_int is not None]
    for k, s in zip(slots, sorted((seq[k] for k in slots), key=_house_key, reverse=t.ascending == bool(rev))):
        seq[k] = s
    return seq


def _loose_runs(stops):
    """Stops without a span: one run per street side and precinct, in house order, without geometry."""
    groups = defaultdict(list)
    for s in stops:
        groups[(s.street, s.street_id or 0, s.parity)].append(s)
    runs = []
    for key in sorted(groups):
        run = []
        for s in sorted(groups[key], key=_house_key) + [None]:
            if run and (s is None or s.precinct != run[-1].precinct):
                runs.append(Run(run[0].street_id, run[0].street, _side(run[0].parity), run[0].precinct,
                                [r.id for r in run], 0.0, 0, [], [], 0.0, run[0].house, run[-1].house))
                run = []
            if s is not None:
                run.append(s)
    return runs


def _prepare(conn, stops):
    """(graph, tasks, stops without a usable span). The network is loaded around the stops' blocks with PAD_M of
    margin, grown PAD_GROWTH-fold until every block of a stop is connected to the others (or the whole network is
    in), so no step falls back to a straight line where the network has a path."""
    stops = [_stop(s) for s in stops]
    placed = [s for s in stops if s.span is not None and s.x is not None]
    graph = Graph()
    if placed:
        ids = {s.span for s in placed}
        xs, ys = [], []
        for (raw,) in _rows(conn, 'SELECT coords FROM road_span WHERE id IN ({})', ids):
            for p in json.loads(raw or '[]'):
                xs.append(p[0])
                ys.append(p[1])
        if xs:
            box, pad = (min(xs), min(ys), max(xs), max(ys)), PAD_M
            graph = load_graph(conn, box, pad, span_ids=ids)
            while pad < PAD_MAX_M and not _connected(graph.adj, {n for sid in ids if sid in graph.spans
                                                                for n in (graph.spans[sid].a, graph.spans[sid].b)}):
                pad *= PAD_GROWTH
                wider = load_graph(conn, box, pad, span_ids=ids, reuse=graph)
                if len(wider.spans) == len(graph.spans):
                    break                     # the whole network is in: what is not connected never will be
                graph = wider
    placed = [s for s in placed if s.span in graph.spans]
    for s in placed:
        s.x = min(max(float(s.x), 0.0), graph.spans[s.span].len_m)
    loose = [s for s in stops if s.span not in graph.spans or s.x is None]
    return graph, _tasks(graph, placed), loose


def plan_route(conn, stops, *, budget_ms=300, max_runs=None):
    """Order `stops` (mappings: id, street_id, street, parity, precinct, span_id, pos_m, house_int, house, side_lr,
    lon, lat) into runs with the shortest walk. Stops without a span follow as runs in house order. With max_runs,
    a group of nearby runs larger than that raises RouteTooLarge before any ordering work."""
    graph, tasks, loose = _prepare(conn, stops)
    plan = RoutePlan()
    if tasks:
        route = _Route(conn, graph, tasks, max_runs)
        tour = base = route.baseline()
        if len(tasks) > 1:
            tour = route.order(budget_ms)
            if route.totals(base)[1] < route.totals(tour)[1]:
                tour = base
            back = [s ^ 1 for s in reversed(tour)]  # same length; keep the direction with more runs in house order
            if (-sum(tasks[s >> 1].ascending != (s & 1) for s in back), back) < \
                    (-sum(tasks[s >> 1].ascending != (s & 1) for s in tour), tour):
                tour = back
        prev = None
        for s in tour:
            t, rev = tasks[s >> 1], s & 1
            seq = _walk_order(t, rev)
            numbered = [x for x in seq if x.house_int is not None] or seq
            approach, metres = route.approach(prev ^ 1, s) if prev is not None else ([], 0.0)
            metres = min(metres, route.g(prev ^ 1, s)) if prev is not None else 0.0  # equal sums, other order
            plan.runs.append(Run(t.street_id, t.street, _side(t.parity), t.precinct, [x.id for x in seq],
                                 round(t.walk_m, 1), _lr(t, rev), _path(t, rev), approach, round(metres, 1),
                                 numbered[0].house, numbered[-1].house))
            prev = s
        plan.cost = route.totals(tour)[1]
        plan.baseline_m, plan.baseline_cost = route.totals(base)
    plan.runs.extend(_loose_runs(loose))
    plan.total_m = round(sum((r.walk_m + r.approach_m for r in plan.runs), 0.0), 1)
    return plan


def baseline_length(conn, stops):
    """Walking metres of the same runs in house-number order (street label, even before odd, house ascending)."""
    graph, tasks, _ = _prepare(conn, stops)
    if not tasks:
        return 0.0
    route = _Route(conn, graph, tasks)
    return route.totals(route.baseline())[0]
