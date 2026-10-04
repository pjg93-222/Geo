"""Walk route ordering on tiny synthetic road tables (no Django, no real data)."""
import itertools
import json
import math
import random
import sqlite3
import time
import unittest
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import patch

from server.research import walk_route as W
from server.research.walk_route import baseline_length, dijkstra, load_graph, plan_route

LON0, LAT0 = -104.8, 38.85
SCHEMA = '''
CREATE TABLE road_node(id INTEGER PRIMARY KEY, lon REAL, lat REAL, x REAL, y REAL, degree INTEGER);
CREATE TABLE road_edge(id INTEGER PRIMARY KEY, u INTEGER, v INTEGER, len_m REAL, kind TEXT, name_key TEXT,
                       span_id INTEGER, coords TEXT);
CREATE VIRTUAL TABLE road_edge_box USING rtree(id, west, east, south, north);
CREATE TABLE road_span(id INTEGER PRIMARY KEY, name_key TEXT, a INTEGER, b INTEGER, len_m REAL, kind TEXT,
                       edges TEXT, coords TEXT, even_left INTEGER);
'''


def ll(x, y):
    return (LON0 + x / (W.DEG_M * math.cos(math.radians(LAT0))), LAT0 + y / W.DEG_M)


class Net:
    """road_* tables drawn in local metres; every polyline vertex is a node and every segment an edge."""

    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.db.executescript(SCHEMA)
        self.nodes, self.edges, self.spans, self.stops = {}, 0, 0, []

    def node(self, x, y):
        key = (round(x, 3), round(y, 3))
        if key not in self.nodes:
            self.nodes[key] = len(self.nodes) + 1
            self.db.execute('INSERT INTO road_node VALUES (?,?,?,?,?,0)', (self.nodes[key], *ll(x, y), x, y))
        return self.nodes[key]

    def edge(self, name, pts, kind='S1400', span=None):
        self.edges += 1
        coords = [ll(*p) for p in pts]
        length = sum(math.hypot(x1 - x0, y1 - y0) for (x0, y0), (x1, y1) in zip(pts, pts[1:]))
        self.db.execute('INSERT INTO road_edge VALUES (?,?,?,?,?,?,?,?)', (
            self.edges, self.node(*pts[0]), self.node(*pts[-1]), length, kind, name, span, json.dumps(coords)))
        lons, lats = zip(*coords)
        self.db.execute('INSERT INTO road_edge_box VALUES (?,?,?,?,?)',
                        (self.edges, min(lons), max(lons), min(lats), max(lats)))
        return self.edges, length

    def span(self, name, pts, kind='S1400', even_left=1, one_edge=False):
        self.spans += 1
        sid, ids, total = self.spans, [], 0.0
        for piece in [pts] if one_edge else zip(pts, pts[1:]):
            eid, length = self.edge(name, list(piece), kind, sid)
            ids.append(eid)
            total += length
        self.db.execute('INSERT INTO road_span VALUES (?,?,?,?,?,?,?,?,?)', (
            sid, name, self.node(*pts[0]), self.node(*pts[-1]), total, kind, json.dumps(ids),
            json.dumps([ll(*p) for p in pts]), even_left))
        return sid

    def stop(self, street_id, street, parity, span, pos, house, precinct='P1', side=None):
        self.stops.append({'id': len(self.stops) + 1, 'street_id': street_id, 'street': street, 'parity': parity,
                           'precinct': precinct, 'span_id': span, 'pos_m': pos, 'house_int': house,
                           'house': str(house), 'side_lr': (1 if parity == 0 else -1) if side is None else side,
                           'lon': None, 'lat': None})
        return self.stops[-1]['id']


def grid(net, n, block=100.0, s1200=()):
    """n vertical streets Vxx (south to north) and n horizontal Hxx (west to east), one span per block."""
    spans = {}
    for i in range(n):
        for k in range(n - 1):
            name = 'V%02d' % i
            spans[(name, k)] = net.span(name, [(i * block, k * block), (i * block, (k + 1) * block)],
                                        'S1200' if name in s1200 else 'S1400')
            name = 'H%02d' % i
            spans[(name, k)] = net.span(name, [(k * block, i * block), ((k + 1) * block, i * block)],
                                        'S1200' if name in s1200 else 'S1400')
    return spans


def grid_stops(net, spans, n, blocks_of, block=100.0, both=True):
    """Stops at 25 and 75 m on each chosen block; precinct = quadrant; houses ascend with the span direction."""
    names = sorted({name for name, _ in spans})
    for sid, name in enumerate(names, 1):
        for k in blocks_of(sid, name):
            span = spans[(name, k)]
            for m, off in enumerate((25.0, 75.0)):
                along = k * block + off
                x, y = (int(name[1:]) * block, along) if name[0] == 'V' else (along, int(name[1:]) * block)
                precinct = 'P%d' % (2 * (x >= n * block / 2) + (y >= n * block / 2))
                for parity in ((0, 1) if both else (0,)):
                    net.stop(sid, name, parity, span, off, 100 * (k + 1) + 2 * m + parity, precinct)


class DijkstraTests(unittest.TestCase):
    def test_hand_computed_grid(self):
        # 1 -4- 2 -1- 3
        # 2     5     1
        # 4 -3- 5 -2- 6
        edges = [(1, 2, 4), (2, 3, 1), (1, 4, 2), (2, 5, 5), (3, 6, 1), (4, 5, 3), (5, 6, 2)]
        adj = {}
        for k, (u, v, w) in enumerate(edges):
            adj.setdefault(u, []).append((v, float(w), k))
            adj.setdefault(v, []).append((u, float(w), k))
        dist, prev = dijkstra(adj, 1)
        self.assertEqual(dist, {1: 0, 2: 4, 3: 5, 4: 2, 5: 5, 6: 6})
        path, node = [], 6
        while node != 1:
            node, link = prev[node]
            path.append((node, link))
        self.assertEqual(path, [(3, 4), (2, 1), (1, 0)])  # 1-2-3-6 = 6 beats 1-4-5-6 = 7
        dist, _ = dijkstra(adj, 1, targets={4})
        self.assertEqual(dist[4], 2)
        self.assertNotIn(6, dist)
        dist, _ = dijkstra(adj, 1, targets={99})  # unreachable target: full search, no error
        self.assertEqual(len(dist), 6)

    def test_network_costs(self):
        net = Net()
        spans = grid(net, 3, s1200={'H01'})
        net.span('Far', [(5000, 5000), (5100, 5000)])
        g = load_graph(net.db, (*ll(0, 0), *ll(200, 200)))
        self.assertEqual(len(g.spans), 12)  # the far span is outside the box and its 400 m pad
        node = lambda x, y: net.nodes[(x, y)]
        dist, _ = dijkstra(g.adj, node(0, 100))
        self.assertEqual(W.ARTERIAL_FACTOR, 1.0)  # plain shortest path: S1200 metres count as metres
        self.assertAlmostEqual(dist[node(100, 100)], 100.0)
        self.assertAlmostEqual(dist[node(200, 100)], 200.0)
        self.assertAlmostEqual(dist[node(200, 200)], 300.0)
        with patch.object(W, 'ARTERIAL_FACTOR', 1.3):  # the factor stays tunable
            dist, _ = dijkstra(load_graph(net.db, (*ll(0, 0), *ll(200, 200))).adj, node(0, 100))
        self.assertAlmostEqual(dist[node(100, 100)], 130.0)  # along H01: 100 x 1.3
        self.assertAlmostEqual(dist[node(200, 100)], 260.0)  # 260 < 400 around the block
        self.assertAlmostEqual(dist[node(200, 200)], 300.0)  # V00 then H02, never H01
        far = load_graph(net.db, (*ll(0, 0), *ll(200, 200)), span_ids=[13])
        self.assertIn(13, far.spans)
        self.assertAlmostEqual(far.spans[spans[('H01', 0)]].cost, 100.0)
        # Edges outside any span still route; a closed edge (u == v) stays a link of its node.
        cut, _ = net.edge('', [(0, 0), (200, 200)])
        net.db.execute('UPDATE road_edge SET len_m = 50 WHERE id = ?', (cut,))
        ring, _ = net.edge('', [(200, 0), (240, 0), (240, 40), (200, 0)])
        g = load_graph(net.db, (*ll(0, 0), *ll(200, 200)))
        self.assertAlmostEqual(dijkstra(g.adj, node(0, 100))[0][node(200, 200)], 150.0)
        self.assertEqual(g.spans[-ring].a, g.spans[-ring].b)
        self.assertIn((node(200, 0), g.spans[-ring].cost, -ring), g.adj[node(200, 0)])

    def test_multi_edge_span_is_one_link(self):
        net = Net()
        sid = net.span('Bend', [(0, 0), (60, 0), (60, 80)], 'S1200')
        g = load_graph(net.db, (*ll(0, 0), *ll(60, 80)))
        self.assertAlmostEqual(g.spans[sid].cost, 140.0)
        with patch.object(W, 'ARTERIAL_FACTOR', 1.3):
            self.assertAlmostEqual(load_graph(net.db, (*ll(0, 0), *ll(60, 80))).spans[sid].cost, 140 * 1.3)
        self.assertEqual(sorted(g.adj), [net.nodes[(0, 0)], net.nodes[(60, 80)]])

    def test_a_grown_network_reads_only_new_spans_and_is_the_same(self):
        net = Net()
        grid(net, 6, s1200={'H02'})
        long = net.span('Long', [(250, 50), (450, 50), (650, 50), (2650, 50)], 'S1200')   # ends far outside the pads
        net.edge('', [(0, 0), (-150, -150)])                                              # an edge without a span
        box = (*ll(0, 0), *ll(100, 100))
        small = load_graph(net.db, box, 200.0)
        fresh = load_graph(net.db, box, 800.0)
        real, reads = W._rows, []

        def spy(conn, sql, ids):
            reads.append((sql, sorted(ids)))
            return real(conn, sql, ids)
        with patch.object(W, '_rows', spy):
            grown = load_graph(net.db, box, 800.0, reuse=small)
        self.assertIn(long, small.spans)
        self.assertGreater(len(fresh.spans), len(small.spans))
        span_reads = [ids for sql, ids in reads if 'FROM road_span' in sql]
        self.assertEqual(span_reads, [sorted(set(fresh.spans) - set(small.spans))])     # only the new spans are read
        self.assertEqual(grown.adj, fresh.adj)
        self.assertEqual(list(grown.spans), list(fresh.spans))
        fields = ('id', 'name_key', 'a', 'b', 'len_m', 'kind', 'cost', 'even_left', 'raw')
        for sid, sp in fresh.spans.items():
            self.assertEqual([getattr(grown.spans[sid], k) for k in fields], [getattr(sp, k) for k in fields])
        self.assertAlmostEqual(grown.spans[long].cost, 2400.0)                           # all three edges, inside or out
        self.assertIs(grown.spans[long], small.spans[long])


class PlanTests(unittest.TestCase):
    def check_cover(self, net, plan):
        ids = [i for run in plan.runs for i in run.stop_ids]
        self.assertEqual(sorted(ids), sorted(s['id'] for s in net.stops))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(plan.total_m, round(sum(r.walk_m + r.approach_m for r in plan.runs), 1))
        self.assertGreaterEqual(plan.cost, plan.total_m)  # weights and allowances never shorten a walk
        self.assertLessEqual(plan.cost, plan.baseline_cost)

    def test_direction_flip(self):
        net = Net()
        a = [net.span('A', [(0, y), (0, y + 100)]) for y in (0, 100, 200)]
        b = [net.span('B', [(100, y), (100, y + 100)]) for y in (0, 100, 200)]
        net.span('C', [(0, 300), (100, 300)])
        net.span('D', [(0, 0), (100, 0)])
        for street, spans in ((1, a), (2, b)):
            for k, span in enumerate(spans):
                for m, off in enumerate((25, 75)):
                    net.stop(street, 'AB'[street - 1], 0, span, off, 100 + 4 * k + 2 * m)
        plan = plan_route(net.db, net.stops)
        self.check_cover(net, plan)
        # Baseline walks both streets north: 250 + (25 + 300 + 75) + 250. Flipping one run: 250 + 150 + 250.
        self.assertEqual((plan.total_m, plan.baseline_m), (650.0, 900.0))
        self.assertEqual((plan.cost, plan.baseline_cost), (650.0, 900.0))  # all S1400, no crossing
        self.assertEqual(baseline_length(net.db, net.stops), 900.0)
        rising = sorted(int(r.from_house) < int(r.to_house) for r in plan.runs)
        self.assertEqual(rising, [False, True])
        first, second = plan.runs  # houses rise northward: a first run walked north crosses on C, else on D
        x0, x1 = (0, 100) if first.street == 'A' else (100, 0)
        y = 300 if int(first.from_house) < int(first.to_house) else 0
        approach = second.approach
        self.assertEqual(len(approach), 4)
        for got, want in zip(approach[1:3], (ll(x0, y), ll(x1, y))):
            self.assertAlmostEqual(got[0], want[0], delta=1e-6)
            self.assertAlmostEqual(got[1], want[1], delta=1e-6)

    def test_crossing_penalty(self):
        for kind, cross in (('S1400', 15.0), ('S1200', 60.0)):
            net = Net()
            span = net.span('M', [(0, 0), (100, 0)], kind)
            for parity in (0, 1):
                for k, off in enumerate((20, 50, 80)):
                    net.stop(1, 'M', parity, span, off, 2 * k + 2 + parity)
            plan = plan_route(net.db, net.stops)
            self.check_cover(net, plan)
            # Crossing straight over is no distance but costs the allowance; house order walks back 60 m first.
            self.assertEqual((plan.runs[1].approach_m, plan.total_m), (0.0, 120.0))
            self.assertEqual(plan.cost, plan.total_m + cross)
            self.assertEqual((plan.baseline_m, plan.baseline_cost), (180.0, 180.0 + cross))
            at = {'2': 20, '4': 50, '6': 80, '3': 20, '5': 50, '7': 80}
            for run in plan.runs:  # even = north = left of west->east; odd = south
                east = at[run.to_house] > at[run.from_house]
                self.assertEqual(run.lr, (1 if east else -1) * (1 if run.side == 'even' else -1))
                self.assertAlmostEqual(run.path[0][0], ll(at[run.from_house], 0)[0], delta=1e-6)
                self.assertAlmostEqual(run.path[-1][0], ll(at[run.to_house], 0)[0], delta=1e-6)
            self.assertEqual(at[plan.runs[0].to_house], at[plan.runs[1].from_house])  # cross over, walk back
            self.assertEqual(plan.runs[1].approach[0], plan.runs[0].path[-1])
            self.assertEqual(plan.runs[1].approach[-1], plan.runs[1].path[0])

    def test_crossing_at_a_shared_node_only_for_the_same_street(self):
        for same in (True, False):
            net = Net()
            s1 = net.span('M', [(0, 0), (100, 0)])
            s2 = net.span('M' if same else 'N', [(100, 0), (200, 0)])
            net.stop(1, 'M', 0, s1, 10, 2)
            net.stop(1, 'M', 0, s1, 90, 4)
            net.stop(1 if same else 2, 'M' if same else 'N', 1, s2, 10, 101)
            net.stop(1 if same else 2, 'M' if same else 'N', 1, s2, 90, 103)
            plan = plan_route(net.db, net.stops)
            self.check_cover(net, plan)
            self.assertEqual(plan.total_m, 180.0)
            self.assertEqual(plan.cost, plan.total_m + (15 if same else 0))  # one crossing of the same street

    def test_dead_end_crosses_at_the_last_house(self):
        net = Net()
        net.span('Main', [(-100, 0), (0, 0)])
        court = net.span('Ct', [(0, 0), (0, 100)])
        for off, house in ((20, 2), (60, 4)):
            net.stop(1, 'Ct', 0, court, off, house)
        for off, house in ((40, 1), (80, 3)):
            net.stop(1, 'Ct', 1, court, off, house)
        plan = plan_route(net.db, net.stops)
        self.check_cover(net, plan)
        self.assertEqual(plan.total_m, 40 + 20 + 40)
        self.assertEqual(plan.cost, plan.total_m + 15)  # one crossing of the court

    def test_runs_split_by_precinct_and_gap(self):
        net = Net()
        spans = [net.span('P', [(x, 0), (x + 100, 0)]) for x in (0, 100, 200, 300)]
        for k, precinct in enumerate('AAB'):
            for m, off in enumerate((25, 75)):
                net.stop(1, 'P', 0, spans[k], off, 100 * k + 2 * m + 2, precinct)
        net.stop(1, 'P', 0, spans[3], 50, 400, 'A')  # back in A after B: a third even run
        net.stop(1, 'P', 1, spans[1], 50, 151, 'A')  # no odd stop on the third block: two odd runs
        net.stop(1, 'P', 1, spans[3], 50, 351, 'A')
        plan = plan_route(net.db, net.stops)
        self.check_cover(net, plan)
        by = {}
        for run in plan.runs:
            by.setdefault(run.side, []).append((run.precinct, sorted(int(h) for h in (run.from_house, run.to_house))))
        self.assertEqual(sorted(by['even']), [('A', [2, 104]), ('A', [400, 400]), ('B', [202, 204])])
        self.assertEqual(sorted(by['odd']), [('A', [151, 151]), ('A', [351, 351])])

    def test_interleaved_precincts_split(self):
        net = Net()
        span = net.span('Q', [(0, 0), (100, 0)])
        for k, precinct in enumerate('AABAA'):
            net.stop(1, 'Q', 0, span, 10 + 20 * k, 2 + 2 * k, precinct)
        plan = plan_route(net.db, net.stops)
        self.check_cover(net, plan)
        self.assertEqual(sorted(r.precinct for r in plan.runs), ['A', 'A', 'B'])
        self.assertEqual(plan.total_m, 80.0)

    def test_chain_over_spans_and_loop(self):
        net = Net()
        net.span('Access', [(-100, 0), (0, 0)])
        sides = [net.span('Loop', p) for p in ([(0, 0), (100, 0)], [(100, 0), (100, 100)],
                                               [(100, 100), (0, 100)], [(0, 100), (0, 0)])]
        for k, offs in enumerate(((10, 50, 90), (10, 90), (10, 20), (80, 90))):
            for off in offs:
                net.stop(1, 'Loop', 0, sides[k], off, 100 * k + off)
        plan = plan_route(net.db, net.stops)
        self.assertEqual(len(plan.runs), 1)
        self.assertEqual(plan.runs[0].walk_m, 240.0)  # opens at the 160 m stretch without doors
        houses = [net.stops[i - 1]['house_int'] for i in plan.runs[0].stop_ids]
        self.assertIn(houses, ([380, 390, 10, 50, 90, 110, 190, 210, 220],
                               [220, 210, 190, 110, 90, 50, 10, 390, 380]))

    def test_cul_de_sac_ring(self):
        net = Net()
        stem = net.span('Ring Ct', [(0, 0), (0, 100)])
        bulb = net.span('Ring Ct', [(0, 100), (0, 150), (-50, 200), (-100, 150), (0, 100)])  # a == b
        g = load_graph(net.db, (*ll(-100, 0), *ll(0, 200)))
        p = net.nodes[(0, 100)]
        self.assertEqual((g.spans[bulb].a, g.spans[bulb].b), (p, p))
        self.assertIn((p, g.spans[bulb].cost, bulb), g.adj[p])
        for span, off, house in ((stem, 30, 2), (stem, 70, 4), (bulb, 40, 6), (bulb, 140, 8), (bulb, 240, 10)):
            net.stop(1, 'Ring Ct', 0, span, off, house)
        for span, off, house in ((stem, 50, 1), (bulb, 60, 3), (bulb, 200, 5)):
            net.stop(1, 'Ring Ct', 1, span, off, house)
        plan = plan_route(net.db, net.stops)
        self.check_cover(net, plan)
        # Each side is one run up the stem and round the bulb (310 and 250 m); crossing at the stem's foot walks
        # |30 - 50| (+15 cost). House order crosses from the bulb back down the stem: 63.2 + 50 (+15 cost).
        self.assertEqual(sorted((r.side, r.walk_m) for r in plan.runs), [('even', 310.0), ('odd', 250.0)])
        self.assertEqual((plan.total_m, plan.baseline_m), (580.0, 673.2))
        self.assertEqual((plan.cost, plan.baseline_cost), (plan.total_m + 15, plan.baseline_m + 15))

    def test_closed_ring_opens_at_its_widest_gap(self):
        net = Net()
        ring = net.span('O Ring', [(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)], one_edge=True)
        edge = net.db.execute('SELECT u, v FROM road_edge').fetchone()
        self.assertEqual(edge[0], edge[1])
        for off in (10, 20, 380, 390):
            net.stop(1, 'O Ring', 0, ring, off, off)
        plan = plan_route(net.db, net.stops)
        self.assertEqual(len(plan.runs), 1)
        self.assertEqual(plan.runs[0].walk_m, 40.0)  # through the node, not the 360 m round the far side
        houses = [net.stops[i - 1]['house_int'] for i in plan.runs[0].stop_ids]
        self.assertIn(houses, ([380, 390, 10, 20], [20, 10, 390, 380]))
        path = plan.runs[0].path  # the stops at 380 m and 20 m with the ring's node (0, 0) between them
        self.assertEqual(len(path), 3)
        self.assertAlmostEqual(path[1][0], ll(0, 0)[0], delta=1e-6)
        self.assertAlmostEqual(path[1][1], ll(0, 0)[1], delta=1e-6)

    def test_spanless_stops_last(self):
        net = Net()
        span = net.span('M', [(0, 0), (100, 0)])
        net.stop(1, 'M', 0, span, 50, 2)
        net.stop(2, 'Z St', 0, None, None, 4)
        net.stop(2, 'Z St', 0, None, None, 2)
        net.stop(3, 'B St', 1, None, None, 3)
        net.stop(3, 'B St', 0, None, None, 6)
        net.stop(3, 'B St', 1, None, None, 1)
        net.stop(4, 'Gone', 0, 999, 10.0, 8)  # span missing from the store
        plan = plan_route(net.db, net.stops)
        self.check_cover(net, plan)
        self.assertEqual([(r.street, r.side, r.from_house, r.to_house) for r in plan.runs], [
            ('M', 'even', '2', '2'), ('B St', 'even', '6', '6'), ('B St', 'odd', '1', '3'),
            ('Gone', 'even', '8', '8'), ('Z St', 'even', '2', '4')])
        for run in plan.runs[1:]:
            self.assertEqual((run.path, run.approach, run.lr, run.walk_m), ([], [], 0, 0.0))

    def test_disconnected_streets(self):
        net = Net()
        s1 = net.span('A', [(0, 0), (100, 0)])
        s2 = net.span('B', [(0, 300), (100, 300)])
        net.stop(1, 'A', 0, s1, 50, 2)
        net.stop(2, 'B', 0, s2, 50, 2)
        plan = plan_route(net.db, net.stops)
        self.check_cover(net, plan)
        self.assertEqual(len(plan.runs[1].approach), 2)  # a straight line: its length, at 1.5x in the cost
        self.assertAlmostEqual(plan.runs[1].approach_m, 300.0, delta=0.1)
        self.assertAlmostEqual(plan.cost, 450.0, delta=0.2)

    def test_shortest_deadhead_and_arterial_factor(self):
        # A and B are linked by C (S1200, 100 m) and, in some cases, D (S1400 via (50, 130), 116.6 m).
        # Walk: 60 + (20 + link + 20) + 60 metres; no crossing, so cost = total_m + arterial surcharge only.
        for factor, detour, want, points in ((1.0, True, (260.0, 260.0), 4),     # shortest path: along C
                                             (1.3, True, (276.6, 276.6), 5),     # tuned: D avoids C
                                             (1.3, False, (260.0, 290.0), 4)):   # tuned, no D: C costs 130
            net = Net()
            a = net.span('A', [(0, 0), (0, 100)])
            b = net.span('B', [(100, 0), (100, 100)])
            net.span('C', [(0, 100), (100, 100)], 'S1200')
            if detour:
                net.span('D', [(0, 100), (50, 130), (100, 100)])
            for street, span in ((1, a), (2, b)):
                net.stop(street, 'AB'[street - 1], 0, span, 20, 2)
                net.stop(street, 'AB'[street - 1], 0, span, 80, 4)
            with patch.object(W, 'ARTERIAL_FACTOR', factor):
                plan = plan_route(net.db, net.stops)
            self.check_cover(net, plan)
            self.assertEqual((plan.total_m, plan.cost), want)
            self.assertEqual(len(plan.runs[1].approach), points)

    def test_virtual_links_walk_like_local_streets(self):
        net = Net()
        s1 = net.span('A', [(0, 0), (100, 0)])
        s2 = net.span('B', [(0, 300), (100, 300)])
        link = net.span('', [(100, 0), (100, 300)], even_left=None)   # a connector joining two components
        for table in ('road_edge', 'road_span'):                # the builder's additive column
            net.db.execute('ALTER TABLE %s ADD COLUMN virtual INTEGER DEFAULT 0' % table)
        net.db.execute('UPDATE road_span SET virtual=1 WHERE id=?', (link,))
        net.db.execute('UPDATE road_edge SET virtual=1 WHERE span_id=?', (link,))
        net.stop(1, 'A', 0, s1, 50, 2)
        net.stop(2, 'B', 0, s2, 50, 2)
        with patch.object(W, 'ARTERIAL_FACTOR', 1.3):            # S1400 semantics: no arterial surcharge on it
            plan = plan_route(net.db, net.stops)
        self.check_cover(net, plan)
        self.assertEqual((plan.total_m, plan.cost), (400.0, 400.0))   # along the link, not 1.5 x the 300 m gap
        self.assertEqual(len(plan.runs[1].approach), 4)

    def test_blank_house_stops_walk_in_the_even_run(self):
        net = Net()
        span = net.span('M', [(0, 0), (100, 0)])
        for off, house in ((10, 8), (30, 6), (50, 4)):             # numbers fall along the span
            net.stop(1, 'M', 0, span, off, house)
        net.stop(1, 'M', 1, span, 40, 5)
        net.stops.append({'id': 9, 'street_id': 1, 'street': 'M', 'parity': None, 'precinct': 'P1', 'span_id': span,
                          'pos_m': 90.0, 'house_int': None, 'house': '', 'side_lr': 1})
        plan = plan_route(net.db, net.stops)
        self.check_cover(net, plan)
        even = next(r for r in plan.runs if r.side == 'even')
        self.assertIn(9, even.stop_ids)                          # no parity: with the evens
        houses = [next(s['house'] for s in net.stops if s['id'] == i) for i in even.stop_ids]
        self.assertEqual([h for h in houses if h], ['4', '6', '8'])   # house order judged on the numbered doors
        self.assertEqual(houses[0], '')                          # the blank house sits at the 4 end, shown blank

    def test_side_from_even_left_when_unknown(self):
        net = Net()
        span = net.span('M', [(0, 0), (100, 0)], even_left=0)
        net.stop(1, 'M', 0, span, 20, 2, side=0)
        net.stop(1, 'M', 0, span, 80, 4, side=0)
        run = plan_route(net.db, net.stops).runs[0]
        self.assertEqual(run.lr, -1 if run.from_house == '2' else 1)
        self.assertEqual(plan_route(net.db, []).runs, [])

    def test_never_longer_than_house_order(self):
        for seed in range(1, 6):
            net = Net()
            spans = grid(net, 8, s1200={'H03'})
            rng = random.Random(seed)
            grid_stops(net, spans, 8, lambda sid, name: sorted(rng.sample(range(7), rng.randint(1, 4))),
                       both=seed % 2 == 0)
            plan = plan_route(net.db, net.stops)
            self.check_cover(net, plan)  # cost <= baseline_cost holds by construction
            self.assertLess(plan.total_m, 0.9 * plan.baseline_m)
            for run in plan.runs:
                self.assertTrue(run.path)
                precincts = {net.stops[i - 1]['precinct'] for i in run.stop_ids}
                self.assertEqual(precincts, {run.precinct})

    def test_deterministic(self):
        def build(order):
            net = Net()
            spans = grid(net, 10)
            rng = random.Random(7)
            grid_stops(net, spans, 10, lambda sid, name: sorted(rng.sample(range(9), 3)))
            stops = list(net.stops)
            if order:
                random.Random(order).shuffle(stops)
            return asdict(plan_route(net.db, stops))
        first = build(0)
        self.assertEqual(build(0), first)
        self.assertEqual(build(3), first)

    def test_a_busy_machine_does_not_change_the_plan(self):
        """The search ends on its count of move evaluations, not on the clock: a clock racing to 90% of the backstop
        (five times the 0.9 s that used to cut the search) gives the very same plan; past the backstop the plan is
        still whole, and the one warning says so."""
        net = Net()
        spans = grid(net, 10)
        rng = random.Random(7)
        grid_stops(net, spans, 10, lambda sid, name: sorted(rng.sample(range(9), 3)))
        calm = asdict(plan_route(net.db, net.stops))
        reads = []
        with patch.object(W, 'time', SimpleNamespace(perf_counter=lambda: reads.append(1) or time.perf_counter())):
            self.assertEqual(asdict(plan_route(net.db, net.stops)), calm)
        self.assertGreater(len(reads), 20)                       # the search does look at the clock
        step, ticks = 0.9 * W.BACKSTOP / len(reads), itertools.count()
        with patch.object(W, 'time', SimpleNamespace(perf_counter=lambda: next(ticks) * step)):
            rushed = asdict(plan_route(net.db, net.stops))
        self.assertGreater(next(ticks) * step, 4.0)             # the old limit was 3 x 300 ms from the start
        self.assertEqual(rushed, calm)
        ticks = itertools.count()
        with patch.object(W, 'time', SimpleNamespace(perf_counter=lambda: next(ticks) * W.BACKSTOP)), \
                self.assertLogs(W.log, 'WARNING') as logged:
            cut = plan_route(net.db, net.stops)
        self.assertEqual(len(logged.records), 1)
        self.assertIn('backstop', logged.output[0])
        self.check_cover(net, cut)
        self.assertLessEqual(cut.cost, cut.baseline_cost)

    def test_forty_street_grid_speed(self):
        net = Net()
        spans = grid(net, 20, s1200={'H10', 'V10'})

        def blocks(sid, name):  # two separate two-block stretches per street
            a = sid * 7 % 15
            b = (a + 4 + sid % 3) % 17
            return sorted({a, a + 1, b, b + 1})
        grid_stops(net, spans, 20, blocks)
        self.assertEqual(len(net.stops), 640)
        started = time.process_time()   # CPU time of this process: other load on the machine does not count
        plan = plan_route(net.db, net.stops)
        elapsed = time.process_time() - started
        self.check_cover(net, plan)
        self.assertLess(plan.total_m, plan.baseline_m)
        self.assertLess(elapsed, 1.0, 'plan took %.3f s' % elapsed)


class ReviewTests(unittest.TestCase):
    def test_doors_follow_house_numbers_in_the_walking_direction(self):
        net = Net()
        span = net.span('M', [(0, 0), (100, 0)])
        for off, house in ((10, 2), (30, 6), (40, 4), (70, 8)):      # 6 geocoded a little past 4
            net.stop(1, 'M', 0, span, off, house)
        run = plan_route(net.db, net.stops).runs[0]
        houses = [net.stops[i - 1]['house_int'] for i in run.stop_ids]
        self.assertIn(houses, ([2, 4, 6, 8], [8, 6, 4, 2]))
        self.assertEqual((run.from_house, run.to_house), tuple(str(h) for h in (houses[0], houses[-1])))
        self.assertEqual(run.walk_m, 60.0)                          # the walked stretch is unchanged

    def test_a_path_outside_the_first_pad_is_walked_not_drawn_straight(self):
        net = Net()
        a = net.span('A', [(0, 0), (100, 0)])
        b = net.span('B', [(0, 300), (100, 300)])
        for pts in ([(100, 0), (100, -1000)], [(100, -1000), (-200, -1000)], [(-200, -1000), (-200, 300)],
                    [(-200, 300), (0, 300)]):                          # the only link dips 1 km south of the stops
            net.span('Link', pts)
        net.stop(1, 'A', 0, a, 50, 2)
        net.stop(2, 'B', 0, b, 50, 2)
        plan = plan_route(net.db, net.stops)
        self.assertAlmostEqual(plan.runs[1].approach_m, 50 + 1000 + 300 + 1300 + 200 + 50, delta=0.2)
        self.assertGreater(len(plan.runs[1].approach), 4)
        self.assertLessEqual(plan.cost, plan.baseline_cost)

    def test_far_apart_groups_are_ordered_and_stepped_exactly(self):
        net = Net()
        for x in (0, 3000):                                          # blocks end at the junctions
            net.span('Art', [(x, 0), (x + 3000, 0)], 'S1200')
        spans, n = {}, 0
        for base in (0, 3000, 6000):                                 # three neighbourhoods 3 km apart on one arterial
            for k, name in enumerate(('N%d' % base, 'S%d' % base)):
                y0 = 0 if k == 0 else -200
                spans[name] = net.span(name, [(base, y0), (base, y0 + 200)])
                n += 1
                for off, house in ((50, 2), (150, 4)):
                    net.stop(n, name, 0, spans[name], off, house)
        graph, tasks, _ = W._prepare(net.db, net.stops)
        route = W._Route(net.db, graph, tasks)
        self.assertEqual(len(route.groups), 3)
        plan = plan_route(net.db, net.stops)
        PlanTests.check_cover(self, net, plan)
        self.assertEqual(asdict(plan), asdict(plan_route(net.db, list(reversed(net.stops)))))
        self.assertEqual(plan.cost, plan.total_m)        # no crossing here, and no 1.5x straight-line fallback step
        self.assertEqual(plan.total_m, 7500.0)   # optimal: start at a far end, the arterial once, finish at a far end
        with self.assertRaises(W.RouteTooLarge):
            plan_route(net.db, net.stops, max_runs=1)

    def test_many_dispersed_groups_plan_quickly(self):
        net = Net()
        xs = [2000 * g + 100 * k for g in range(30) for k in range(4)]
        for a, b in zip(xs, xs[1:]):
            net.span('Art', [(a, 0), (b, 0)], 'S1200')
        n = 0
        for g in range(30):                                          # 30 neighbourhoods 2 km apart, 4 runs each
            base = 2000 * g
            for k in range(4):
                sid = net.span('G%02d-%d' % (g, k), [(base + 100 * k, 0), (base + 100 * k, 300)])
                n += 1
                for off, house in ((50, 2), (150, 4), (250, 6)):
                    net.stop(n, 'G%02d-%d' % (g, k), 0, sid, off, house)
        started = time.process_time()
        plan = plan_route(net.db, net.stops)
        elapsed = time.process_time() - started
        PlanTests.check_cover(self, net, plan)
        self.assertEqual(plan.cost, plan.total_m)        # every step on the network
        self.assertLess(elapsed, 3.0, 'plan took %.2f s of CPU' % elapsed)


if __name__ == '__main__':
    unittest.main()
