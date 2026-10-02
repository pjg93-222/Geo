"""Synthetic checks for the walk-list street normaliser and road network (no real data).

    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 <venv python> -m unittest tools.test_walk_geo     (from the V3 folder)

The grid is invented: whole-metre offsets (EPSG:26913) around lon -104.80 lat 38.85, stored as GeoJSON lon/lat rows
like .local/roads.sqlite3 (x east, y north). Two parallel streets, y = 0 (N Cedar Heights Dr, two rows meeting at a
degree-2 node at x = 150) and y = 200 (Maple St to x = 200, then Saint Vrain St: a name change at a degree-2 node);
two cross streets, x = 100 (First Ave, S1200) and x = 300 (Mount View Ln), both y -100..300; a cul-de-sac with a bend
off Mount View at y = 100 (Aspen Ct); an unnamed connector joining the east ends at x = 400; an S1100 along y = 100
that must be dropped; Pine Pl isolated at y = 0, x 600..700. Hand counts: 18 nodes, 18 edges, 17 spans, 2 components.
"""
import json
import random
import sqlite3
import unittest

import numpy as np

from server.research import walk_names as N
from tools import walk_geo as G


ROADS = [  # id, name, kind, metre offsets
    (1, 'N Cedar Heights Dr', 'S1400', [(0, 0), (150, 0)]),
    (2, 'N Cedar Heights Dr', 'S1400', [(150, 0), (400, 0)]),        # meets road 1 at a degree-2 vertex
    (3, 'Maple St', 'S1400', [(0, 200), (200, 200)]),
    (4, 'Saint Vrain St', 'S1400', [(200, 200), (400, 200)]),         # name changes at a degree-2 vertex
    (5, 'First Ave', 'S1200', [(100, -100), (100, 300)]),
    (6, 'Mount View Ln', 'S1400', [(300, -100), (300, 300)]),
    (7, 'Aspen Ct', 'S1400', [(300, 100), (380, 100), (380, 140)]),   # cul-de-sac with a bend
    (8, '', 'S1400', [(400, 0), (400, 200)]),                         # unnamed connector
    (9, 'I- 25', 'S1100', [(-50, 100), (450, 100)]),                  # dropped; would cross C, D, F and touch E
    (10, 'Pine Pl', 'S1400', [(600, 0), (700, 0)]),                   # isolated
]
X0, Y0 = (round(float(v)) for v in G.to_metres(-104.80, 38.85))
CITY = 'Colorado Springs'


def geojson(offsets):
    lon, lat = G.to_lonlat(np.array([X0 + dx for dx, _ in offsets], float),
                           np.array([Y0 + dy for _, dy in offsets], float))
    return json.dumps({'type': 'LineString', 'coordinates': [[float(a), float(b)] for a, b in zip(lon, lat)]})


def road_rows(shuffle=None, roads=ROADS):
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE roads (id INTEGER PRIMARY KEY, name TEXT, kind TEXT, geometry TEXT)')
    db.executemany('INSERT INTO roads VALUES (?,?,?,?)', [(i, n, k, geojson(c)) for i, n, k, c in roads])
    rows = db.execute('SELECT id, name, kind, geometry FROM roads').fetchall()
    if shuffle is not None:
        random.Random(shuffle).shuffle(rows)
    return rows


def build(shuffle=None, roads=ROADS):
    walk = sqlite3.connect(':memory:')
    return walk, G.build_network(walk, road_rows(shuffle, roads))


def dump(conn):
    return {t: conn.execute('SELECT * FROM %s ORDER BY id' % t).fetchall()
            for t in ('road_node', 'road_edge', 'road_edge_box', 'road_span')}


def stop(ident, key, dx=None, dy=None, house=None, city=CITY):
    return {'id': ident, 'key': key, 'city': city, 'house_int': house,
            'parity': None if house is None else house % 2,
            'x': None if dx is None else X0 + dx, 'y': None if dy is None else Y0 + dy}


class NameTests(unittest.TestCase):
    def test_voter_parts_tiger_and_typed_text_share_one_key(self):
        pairs = [
            (N.name_key('N Cedar Heights Dr'), N.street_key('N', 'CEDAR HEIGHTS', 'DR', '')),
            (N.name_key('North Cedar Heights Drive'), N.name_key('N Cedar Heights Dr')),
            (N.name_key('Cedar Heights Drive'), N.street_key('', 'CEDAR HEIGHTS', 'DR', '')),
            (N.name_key('CEDAR HTS DR'), N.name_key('Cedar Heights Drive')),
            (N.name_key('  cedar   heights,  dr. '), N.name_key('Cedar Heights Dr')),
            (N.name_key('Saint Vrain St'), N.name_key('St. Vrain Street')),
            (N.street_key(None, 'SAINT VRAIN', 'STREET', None), N.name_key('Saint Vrain St')),
            (N.name_key('Mount View Ln'), N.name_key('Mt View Lane')),
            (N.street_key('', 'MOUNT VIEW', 'LN', ''), N.name_key('Mount View Ln')),
            (N.name_key('First Ave'), N.name_key('1st Avenue')),
            (N.street_key('', '1ST', 'AVE', ''), N.name_key('First Ave')),
            (N.name_key('US Hwy 24'), N.name_key('U.S. Highway 24')),
            (N.street_key('', 'US HIGHWAY 24', '', ''), N.name_key('US Hwy 24')),
            (N.name_key('I- 25'), N.name_key('Interstate 25')),
            (N.name_key('Twenty-First St'), N.name_key('21st Street')),
            (N.name_key('Cañon Ct'), N.name_key('Canon Court')),
            (N.name_key("O'Connell Blvd"), N.name_key('OCONNELL BOULEVARD')),
            (N.name_key('Academy Blvd N'), N.street_key('', 'ACADEMY', 'BLVD', 'NORTH')),
            (N.name_key('E Platte Ave'), N.street_key('EAST', 'PLATTE', 'AVENUE', float('nan'))),
            (N.name_key('Co Rd 21'), N.name_key('County Road 21')),
            (N.name_key('State Hwy 83'), N.street_key('', 'STATE HIGHWAY 83', '', '')),
            (N.name_key('Ft. Carson Pkwy'), N.name_key('Fort Carson Parkway')),
            (N.name_key('Cheyenne Mtn Blvd'), N.name_key('Cheyenne Mountain Boulevard')),
        ]
        self.assertGreaterEqual(len(pairs), 15)
        for left, right in pairs:
            with self.subTest(left=left):
                self.assertTrue(left)
                self.assertEqual(left, right)
        self.assertEqual(N.name_key('N Cedar Heights Dr'), 'N CEDAR HTS DR')
        self.assertEqual(N.name_key('Saint Vrain St'), 'ST VRAIN ST')
        self.assertEqual(N.name_key('Mount View Ln'), 'MT VW LN')
        self.assertEqual(N.name_key('First Ave'), '1ST AVE')
        self.assertEqual(N.name_key('US Hwy 24'), 'US HWY 24')
        self.assertEqual(N.name_key(''), '')
        self.assertEqual(N.name_key(None), '')

    def test_distinct_streets_stay_distinct(self):
        for a, b in [('N Academy Blvd', 'S Academy Blvd'), ('Cheyenne Blvd', 'Cheyenne Mountain Blvd'),
                     ('21st St', '26th St'), ('Cedar Heights Dr', 'Cedar Heights Ct'), ('US Hwy 24', 'US Hwy 85'),
                     ('Pikes Peak Ave', 'Pike Peak Ave')]:
            with self.subTest(a=a):
                self.assertNotEqual(N.name_key(a), N.name_key(b))

    def test_keys_are_idempotent_and_tables_consistent(self):
        groups = [g.split() for g in N._USPS.replace('\n', '').split('|')]
        words = [w for g in groups for w in g]
        self.assertEqual(len(words), len(set(words)), 'a spelling maps to two suffixes')
        for value in set(N.SUFFIXES.values()) | set(N.DIRECTIONS.values()):
            self.assertEqual(N.name_key(value), value)
        for text in ['N Cedar Heights Dr', 'Twenty-First St', 'U.S. Highway 24', 'Saint Vrain St', 'I25', 'U S 24']:
            key = N.name_key(text)
            self.assertEqual(N.name_key(key), key)
        self.assertEqual(N.name_key('U S 24'), 'US 24')
        self.assertEqual(N.name_key('I25'), 'I 25')

    def test_display(self):
        self.assertEqual(N.display_street('N', 'CEDAR HEIGHTS', 'DR', ''), 'N Cedar Heights Dr')
        self.assertEqual(N.display_street('NORTH', 'cedar heights', 'Drive', None), 'N Cedar Heights Dr')
        self.assertEqual(N.display_street('', '1ST', 'AVENUE', ''), '1st Ave')
        self.assertEqual(N.display_street('', "O'CONNELL", 'BLVD', 'NORTH'), "O'Connell Blvd N")
        self.assertEqual(N.display_street('SOUTH', 'MCARTHUR', 'AVE', ''), 'S McArthur Ave')
        self.assertEqual(N.display_street('', "PIKE'S PEAK", 'AVE', ''), "Pike's Peak Ave")
        self.assertEqual(N.display_street('', 'US HIGHWAY 24', '', ''), 'US Highway 24')
        self.assertEqual(N.display_street('', '', '', ''), '')

    def test_similarity(self):
        high = [('Cedar Heights Drive', 'N Cedar Heights Dr'), ('Academy Blvd', 'N Academy Blvd'),
                ('Magruder Dr', 'McGruder Dr'), ('Northgate Blvd', 'North Gate Blvd'), ('Highway 24', 'US Hwy 24'),
                ('Cheyenne Mtn', 'Cheyenne Mountain Blvd'), ('State Hwy 83', 'N State Highway 83')]
        low = [('Cheyenne Blvd', 'Cheyenne Mountain Blvd'), ('Main St', 'Maple St'), ('21st St', '26th St'),
               ('Cedar Heights Dr', 'Cedar Dr'), ('Elm St', 'Saint Vrain St'), ('Elm St', 'N Cedar Heights Dr')]
        for a, b in high:
            with self.subTest(a=a, b=b):
                self.assertGreaterEqual(N.similarity(a, b), 0.8)
                self.assertEqual(N.similarity(a, b), N.similarity(b, a))
        for a, b in low:
            with self.subTest(a=a, b=b):
                self.assertLess(N.similarity(a, b), 0.8)
        self.assertEqual(N.similarity('Saint Vrain St', 'ST VRAIN STREET'), 1.0)
        self.assertEqual(N.similarity('', 'Main St'), 0.0)
        self.assertGreater(N.similarity('Cheyenne', 'Cheyenne Blvd'), N.similarity('Cheyenne', 'Cheyenne Mountain Blvd'))

    def test_side_and_house_number(self):
        for text, side in [('Even', 'even'), ('E', 'even'), ('evens only', 'even'), ('odd #s', 'odd'), ('O', 'odd'),
                           ('', 'both'), (None, 'both'), ('Both', 'both'), ('even/odd', 'both'), ('left', 'both'),
                           ('eleven', 'both')]:
            with self.subTest(text=text):
                self.assertEqual(N.parse_side(text), side)
        for text, number in [('1234', 1234), ('1234 1/2', 1234), ('12A', 12), ('0012', 12), (1234, 1234),
                             (1234.0, 1234), ('', None), (None, None), ('ABC', None), (float('nan'), None),
                             ('12345678901', None)]:
            with self.subTest(text=text):
                self.assertEqual(N.house_int(text), number)

    def test_history_window(self):
        self.assertEqual(list(N.PRIMARIES), ['2014p', '2016p', '2018p', '2020p', '2022p', '2024p'])
        self.assertEqual(list(N.GENERALS), ['2014g', '2016g', '2018g', '2020g', '2022g', '2024g'])
        self.assertEqual(N.PRIMARIES['2020p'], '2020-06-30')
        self.assertEqual(N.GENERALS['2024g'], '2024-11-05')
        self.assertEqual(N.VH_FIELD.format('2022g'), 'svi_vh_2022g')
        self.assertEqual(N.WINDOW, {'primaries': list(N.PRIMARIES), 'generals': list(N.GENERALS),
                                    'abs_rule': 'Abs = mail ballots'})
        self.assertEqual(N.ABS_PREFIXES, ('M', 'A'))


class NetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.stats = build()
        cls.net = G.load_network(cls.db)

    def node(self, dx, dy):
        row = self.db.execute('SELECT id FROM road_node WHERE x=? AND y=?', (X0 + dx, Y0 + dy)).fetchone()
        self.assertIsNotNone(row, (dx, dy))
        return row[0]

    def span(self, p, q):
        """Span id of the edge between two grid points."""
        a, b = sorted((self.node(*p), self.node(*q)))
        return self.db.execute('SELECT span_id FROM road_edge WHERE u=? AND v=?', (a, b)).fetchone()[0]

    def span_row(self, sid):
        return self.db.execute('SELECT name_key, a, b, len_m, kind, edges, coords FROM road_span WHERE id=?',
                               (sid,)).fetchone()

    def test_counts_by_hand(self):
        self.assertEqual(self.stats['nodes'], 18)
        self.assertEqual(self.stats['edges'], 18)
        self.assertEqual(self.stats['spans'], 17)       # A 3, Maple 2, St Vrain 2, First 3, Mount View 4, 3 singles
        self.assertEqual(self.stats['components'], 2)
        self.assertAlmostEqual(self.stats['largest_component_share'], 1920 / 2020, places=3)
        self.assertAlmostEqual(self.stats['length_km'], 2.02, places=2)
        for table, count in [('road_node', 18), ('road_edge', 18), ('road_edge_box', 18), ('road_span', 17)]:
            self.assertEqual(self.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], count)
        degrees = {(0, 0): 1, (100, 0): 4, (150, 0): 2, (300, 0): 4, (400, 0): 2, (100, 200): 4, (200, 200): 2,
                   (300, 100): 3, (380, 140): 1, (600, 0): 1}
        for (dx, dy), degree in degrees.items():
            self.assertEqual(self.db.execute('SELECT degree FROM road_node WHERE id=?',
                                             (self.node(dx, dy),)).fetchone()[0], degree, (dx, dy))
        self.assertEqual(self.db.execute('SELECT SUM(degree) FROM road_node').fetchone()[0], 36)

    def test_s1100_dropped(self):
        self.assertEqual(self.stats['roads_in'], 10)
        self.assertEqual(self.stats['roads_kept'], 9)
        kinds = {r[0] for r in self.db.execute('SELECT DISTINCT kind FROM road_edge')}
        self.assertEqual(kinds, {'S1200', 'S1400'})
        self.assertNotIn('I 25', {r[0] for r in self.db.execute('SELECT name_key FROM road_edge')})
        for dx, dy in [(100, 100), (400, 100), (-50, 100), (450, 100)]:
            self.assertIsNone(self.db.execute('SELECT id FROM road_node WHERE x=? AND y=?',
                                              (X0 + dx, Y0 + dy)).fetchone())
        first_ave = {r[0] for r in self.db.execute("SELECT kind FROM road_edge WHERE name_key='1ST AVE'")}
        self.assertEqual(first_ave, {'S1200'})

    def test_span_chains_across_degree_two_vertex(self):
        sid = self.span((100, 0), (150, 0))
        self.assertEqual(sid, self.span((150, 0), (300, 0)))
        key, a, b, length, kind, edges, coords = self.span_row(sid)
        self.assertEqual((key, a, b, kind), ('N CEDAR HTS DR', self.node(100, 0), self.node(300, 0), 'S1400'))
        self.assertAlmostEqual(length, 200, delta=0.01)
        edges = json.loads(edges)
        self.assertEqual(len(edges), 2)
        self.assertTrue(all(e > 0 for e in edges))
        self.assertEqual(len(json.loads(coords)), 3)
        self.assertNotEqual(self.span((0, 0), (100, 0)), sid)          # degree-4 nodes still break
        self.assertNotEqual(self.span((300, 0), (400, 0)), sid)

    def test_name_change_and_unnamed_break_spans(self):
        maple, vrain = self.span((100, 200), (200, 200)), self.span((200, 200), (300, 200))
        self.assertNotEqual(maple, vrain)
        self.assertEqual(self.span_row(maple)[0], 'MAPLE ST')
        self.assertEqual(self.span_row(vrain)[0], 'ST VRAIN ST')
        unnamed = self.span((400, 0), (400, 200))
        key, a, b, length, _, edges, _ = self.span_row(unnamed)
        self.assertEqual((key, a, b, len(json.loads(edges))), ('', self.node(400, 0), self.node(400, 200), 1))
        self.assertAlmostEqual(length, 200, delta=0.01)
        self.assertNotEqual(self.span((300, 0), (400, 0)), unnamed)

    def test_cul_de_sac(self):
        sid = self.span((300, 100), (380, 140))
        key, a, b, length, kind, edges, coords = self.span_row(sid)
        self.assertEqual((key, a, b, kind), ('ASPEN CT', self.node(300, 100), self.node(380, 140), 'S1400'))
        self.assertAlmostEqual(length, 120, delta=0.01)
        self.assertEqual(len(json.loads(coords)), 3)                   # the bend survives as a vertex
        self.assertNotEqual(self.span((300, 0), (300, 100)), self.span((300, 100), (300, 200)))

    def test_rows_are_consistent(self):
        nodes = {r[0]: (r[1], r[2]) for r in self.db.execute('SELECT id, lon, lat FROM road_node')}
        boxes = {r[0]: r[1:] for r in self.db.execute('SELECT * FROM road_edge_box')}
        for eid, u, v, length, coords in self.db.execute('SELECT id, u, v, len_m, coords FROM road_edge'):
            coords = np.array(json.loads(coords))
            self.assertLessEqual(u, v)
            self.assertTrue(np.allclose(coords[0], nodes[u], atol=2e-6) and np.allclose(coords[-1], nodes[v], atol=2e-6))
            west, east, south, north = boxes[eid]
            self.assertTrue(west <= coords[:, 0].min() + 1e-6 and coords[:, 0].max() <= east + 1e-6)
            self.assertTrue(south <= coords[:, 1].min() + 1e-6 and coords[:, 1].max() <= north + 1e-6)
        lens = {r[0]: r[1] for r in self.db.execute('SELECT id, len_m FROM road_edge')}
        for sid, a, b, length, edges, coords in self.db.execute('SELECT id, a, b, len_m, edges, coords FROM road_span'):
            edges, coords = json.loads(edges), np.array(json.loads(coords))
            self.assertAlmostEqual(length, sum(lens[abs(e)] for e in edges), delta=0.05)
            self.assertTrue(np.allclose(coords[0], nodes[a], atol=2e-6) and np.allclose(coords[-1], nodes[b], atol=2e-6))
            for e in edges:
                self.assertEqual(self.db.execute('SELECT span_id FROM road_edge WHERE id=?', (abs(e),)).fetchone()[0], sid)
        lon, lat = self.db.execute('SELECT lon, lat FROM road_node WHERE id=?', (self.node(0, 0),)).fetchone()
        self.assertAlmostEqual(lon, -104.80, delta=1e-4)
        self.assertAlmostEqual(lat, 38.85, delta=1e-4)

    def test_build_is_deterministic_and_rerunnable(self):
        expected = dump(self.db)
        again, stats = build(shuffle=11)
        self.assertEqual(dump(again), expected)
        self.assertEqual(stats, self.stats)
        G.build_network(again, road_rows(shuffle=3))                   # replaces, never appends
        self.assertEqual(dump(again), expected)
        net = G.load_network(again)
        stops = self.tier_stops()
        self.assertEqual(G.snap_stops(net, stops), G.snap_stops(self.net, stops))

    def tier_stops(self):
        return [stop('a', 'N CEDAR HTS DR', 220, 10), stop('a_right', 'N CEDAR HTS DR', 220, -10),
                stop('a_named', 'N CEDAR HTS DR', 110, 40),            # First Ave is 10 m away, same name 40 m
                stop('b', N.name_key('Cedar Heights Drive'), 250, 30),  # no exact key: fuzzy to N Cedar Heights Dr
                stop('c', 'ELM ST', 305, 50), stop('c_left', 'ELM ST', 295, 50),
                stop('c_far_name', 'N CEDAR HTS DR', 220, 170),        # own street 170 m off beats St Vrain 30 m
                stop('blank', '', 395, 3),                             # unnamed stop never name-matches the connector
                stop('far', 'ELM ST', 500, 500), stop('noloc', 'N CEDAR HTS DR')]

    def test_snapping_tiers_positions_and_sides(self):
        stops = self.tier_stops()
        got = {s['id']: p for s, p in zip(stops, G.snap_stops(self.net, stops))}
        cedar, mount = self.span((150, 0), (300, 0)), self.span((300, 0), (300, 100))
        expect = {'a': ('geocode', cedar, 120, 1, 10), 'a_right': ('geocode', cedar, 120, -1, 10),
                  'a_named': ('geocode', cedar, 10, 1, 40), 'b': ('fuzzy', cedar, 150, 1, 30),
                  'c': ('nearest', mount, 50, -1, 5), 'c_left': ('nearest', mount, 50, 1, 5),
                  'c_far_name': ('geocode', cedar, 120, 1, 170),
                  'blank': ('nearest', self.span((300, 0), (400, 0)), 95, 1, 3)}
        for ident, (placed_by, span, pos, side, dist) in expect.items():
            with self.subTest(stop=ident):
                p = got[ident]
                self.assertEqual((p.placed_by, p.span_id, p.side_lr), (placed_by, span, side))
                self.assertAlmostEqual(p.pos_m, pos, delta=0.3)
                self.assertAlmostEqual(p.dist_m, dist, delta=0.3)
        self.assertEqual({k: got[k].tier for k in expect},
                         {'a': 'a', 'a_right': 'a', 'a_named': 'a', 'b': 'b', 'c': 'c', 'c_left': 'c',
                          'c_far_name': 'a', 'blank': 'c'})
        far = got['far']                                               # 283 m from every road: tier e, any distance
        self.assertEqual((far.placed_by, far.tier, far.span_id), ('nearest', 'e', self.span((300, 200), (300, 300))))
        self.assertAlmostEqual(far.dist_m, 200 * 2 ** 0.5, delta=0.3)
        self.assertIsNone(got['noloc'])
        lon, lat = G.to_lonlat(X0 + 220, Y0)
        self.assertAlmostEqual(got['a'].lon, lon, delta=2e-6)
        self.assertAlmostEqual(got['a'].lat, lat, delta=2e-6)
        self.assertEqual(G.span_points(self.net, [cedar], [120]), [[got['a'].lon, got['a'].lat]])

    def test_nearest_tier_reaches_150_m(self):
        # (520, 100): unnamed connector 120 m west, Pine Pl 128 m, Aspen Ct 140 m; (560, 260): every road > 150 m.
        self.assertEqual(G.NEAREST_M, 150.0)
        stops = [stop('setback', 'ELM ST', 520, 100), stop('setback_named', 'PNE PL', 520, 100),
                 stop('beyond', 'ELM ST', 560, 260)]
        got = {s['id']: p for s, p in zip(stops, G.snap_stops(self.net, stops))}
        connector, pine = self.span((400, 0), (400, 200)), self.span((600, 0), (700, 0))
        for ident, (placed_by, span, pos, side, dist) in {'setback': ('nearest', connector, 100, -1, 120),
                                                          'setback_named': ('geocode', pine, 0, 1, 128.06)}.items():
            with self.subTest(stop=ident):          # no name match -> tier c; a same-name road in range still wins
                p = got[ident]
                self.assertEqual((p.placed_by, p.span_id, p.side_lr), (placed_by, span, side))
                self.assertAlmostEqual(p.pos_m, pos, delta=0.3)
                self.assertAlmostEqual(p.dist_m, dist, delta=0.3)
        beyond = got['beyond']                                         # 171 m from (400, 200): no longer tier c, tier e
        self.assertEqual((beyond.placed_by, beyond.tier, beyond.span_id),
                         ('nearest', 'e', self.span((300, 200), (400, 200))))   # tie at the node: the lowest edge
        self.assertAlmostEqual(beyond.dist_m, (160 ** 2 + 60 ** 2) ** 0.5, delta=0.3)

    def test_even_left_majority_and_interpolated_sides(self):
        key = 'N CEDAR HTS DR'
        stops = [stop(h, key, x, y, h) for h, x, y in [(102, 120, 15), (104, 140, 15), (106, 160, 15),
                                                       (101, 120, -15), (103, 140, -15), (108, 180, -15)]]
        stops += [stop(110, key, house=110), stop(105, key, house=105),
                  stop('elm', 'ELM ST', 200, -20, 200), stop('elm2', 'ELM ST', 210, -20, 202)]  # other street's numbers
        placements = G.interpolate(self.net, stops, G.snap_stops(self.net, stops))
        even_left, agreement = G.assign_sides(self.net, placements)
        cedar = self.span((150, 0), (300, 0))
        self.assertEqual([placements[-1].placed_by, placements[-1].span_id], ['nearest', cedar])
        self.assertEqual(even_left, {cedar: 1})
        self.assertAlmostEqual(agreement, 5 / 6, places=3)                # 'nearest' stops do not vote
        self.assertEqual(self.net.spans[cedar].even_left, 1)
        got = {s['id']: p for s, p in zip(stops, placements)}
        self.assertEqual([got[h].side_lr for h in (102, 101, 108)], [1, -1, -1])   # observed sides stay as seen
        self.assertEqual((got[110].placed_by, got[110].span_id, got[110].side_lr), ('interpolated', cedar, 1))
        self.assertEqual((got[105].span_id, got[105].side_lr), (cedar, -1))
        slope = 300 / 34                       # least squares of (house, pos) for 101..108 on the block
        self.assertAlmostEqual(got[110].pos_m, 80 + 2 * slope, delta=0.5)  # past the last even (108 at 80 m)
        self.assertAlmostEqual(got[105].pos_m, 40 + 2 * slope, delta=0.5)  # past the last odd (103 at 40 m)
        self.assertGreater(got[110].pos_m, got[108].pos_m)

    def test_interpolation_follows_house_numbers(self):
        maple = self.span((100, 200), (200, 200))
        stops = [stop('100', 'MAPLE ST', 110, 190, 100), stop('198', 'MAPLE ST', 190, 190, 198)]
        stops += [stop(str(h), 'MAPLE ST', house=h) for h in (150, 120, 180)]
        placements = G.interpolate(self.net, stops, G.snap_stops(self.net, stops))
        got = {s['house_int']: p for s, p in zip(stops, placements)}
        for h in (120, 150, 180):
            with self.subTest(house=h):
                self.assertEqual((got[h].placed_by, got[h].span_id), ('interpolated', maple))
                self.assertAlmostEqual(got[h].pos_m, 10 + (h - 100) / 98 * 80, delta=0.3)
                self.assertIsNotNone(got[h].lon)
        order = sorted(got, key=lambda h: got[h].pos_m)
        self.assertEqual(order, [100, 120, 150, 180, 198])
        self.assertEqual(placements[0], G.snap_stops(self.net, stops[:1])[0])     # snapped stops untouched

    def test_interpolation_runs_along_the_span_chain(self):
        stops = [stop('100', 'MT VW LN', 290, -90, 100), stop('400', 'MT VW LN', 290, 290, 400),
                 stop('200', 'MT VW LN', house=200), stop('300', 'MT VW LN', house=300)]
        got = {s['house_int']: p for s, p in zip(stops, G.interpolate(self.net, stops, G.snap_stops(self.net, stops)))}
        self.assertEqual(got[200].span_id, self.span((300, 0), (300, 100)))
        self.assertAlmostEqual(got[200].pos_m, 10 + 380 / 3 - 100, delta=0.5)
        self.assertEqual(got[300].span_id, self.span((300, 100), (300, 200)))
        self.assertAlmostEqual(got[300].pos_m, 10 + 760 / 3 - 200, delta=0.5)
        lats = [got[h].lat for h in (100, 200, 300, 400)]
        self.assertTrue(all(a < b for a, b in zip(lats, lats[1:])), lats)  # northward with the house numbers

    def test_interpolation_fallbacks(self):
        stops = [stop('201', 'ST VRAIN ST', 210, 210, 201), stop('299', 'ST VRAIN ST', 290, 210, 299),
                 stop('250', 'ST VRAIN ST', house=250),                       # no evens placed: odd neighbours
                 stop('p10', 'PNE PL', house=10), stop('p30', 'PNE PL', house=30), stop('p20', 'PNE PL', house=20),
                 stop('nowhere', 'NOWHERE LN', house=5), stop('nohouse', 'N CEDAR HTS DR'),
                 stop('other', 'PNE PL', house=10, city='Manitou Springs')]
        got = {s['id']: p for s, p in zip(stops, G.interpolate(self.net, stops, G.snap_stops(self.net, stops)))}
        self.assertEqual(got['250'].span_id, self.span((200, 200), (300, 200)))
        self.assertAlmostEqual(got['250'].pos_m, 10 + 49 / 98 * 80, delta=0.5)
        pine = self.span((600, 0), (700, 0))
        self.assertEqual([got[k].span_id for k in ('p10', 'p20', 'p30')], [pine] * 3)
        self.assertEqual([round(got[k].pos_m) for k in ('p10', 'p20', 'p30')], [0, 50, 100])
        self.assertEqual(round(got['other'].pos_m), 50)                    # its own street: one house, mid-block
        for ident in ('nowhere', 'nohouse'):
            p = got[ident]
            self.assertEqual((p.placed_by, p.span_id, p.pos_m, p.lon, p.side_lr), ('interpolated', None, None, None, 0))
        self.assertTrue(all(p is not None for p in got.values()))


class ShapeTests(unittest.TestCase):
    LOOPS = [(1, 'Loop Rd', 'S1400', [(0, 0), (0, 50)]),                                   # stem
             (2, 'Loop Rd', 'S1400', [(0, 50), (30, 80), (0, 110), (-30, 80), (0, 50)]),   # lollipop bulb
             (3, 'Circle Dr', 'S1400', [(100, 0), (150, 0), (150, 50)]),                    # isolated ring, two halves
             (4, 'Circle Dr', 'S1400', [(150, 50), (100, 50), (100, 0)])]
    TOWNS = [(1, 'Main St', 'S1400', [(0, 0), (100, 0)]), (2, 'Side St', 'S1400', [(0, 100), (100, 100)]),
             (3, 'Main St', 'S1400', [(5000, 0), (5300, 0)])]                              # same name, far and longer

    def test_loops_and_rings(self):
        db, stats = build(roads=self.LOOPS)
        self.assertEqual((stats['nodes'], stats['edges'], stats['spans']), (4, 4, 3))
        node = {(dx, dy): db.execute('SELECT id FROM road_node WHERE x=? AND y=?', (X0 + dx, Y0 + dy)).fetchone()[0]
                for dx, dy in [(0, 0), (0, 50), (100, 0), (150, 50)]}
        self.assertEqual(db.execute('SELECT degree FROM road_node WHERE id=?', (node[(0, 50)],)).fetchone()[0], 3)
        spans = {r[0]: r[1:] for r in db.execute("SELECT name_key, a, b, len_m, edges FROM road_span WHERE a=b")}
        self.assertEqual(set(spans), {'LOOP RD', 'CIR DR'})
        self.assertEqual(spans['LOOP RD'][:2], (node[(0, 50)], node[(0, 50)]))
        self.assertAlmostEqual(spans['LOOP RD'][2], 4 * 1800 ** 0.5, delta=0.05)
        self.assertEqual(spans['CIR DR'][:2], (node[(100, 0)], node[(100, 0)]))
        self.assertEqual((spans['CIR DR'][2], len(json.loads(spans['CIR DR'][3]))), (200, 2))
        self.assertEqual(build(shuffle=5, roads=self.LOOPS)[0].execute('SELECT * FROM road_span').fetchall(),
                         db.execute('SELECT * FROM road_span').fetchall())
        net = G.load_network(db)
        stops = [stop('c1', 'CIR DR', 95, 10, 1), stop('c9', 'CIR DR', 155, 40, 9),
                 stop('c5', 'CIR DR', house=5), stop('l1', 'LOOP RD', house=1), stop('l9', 'LOOP RD', house=9)]
        got = {s['id']: p for s, p in zip(stops, G.interpolate(net, stops, G.snap_stops(net, stops)))}
        self.assertAlmostEqual(got['c1'].pos_m, 10, delta=0.3)
        self.assertAlmostEqual(got['c9'].pos_m, 110, delta=0.3)          # second half is walked v->u
        self.assertAlmostEqual(got['c5'].pos_m, 60, delta=0.3)
        loop_spans = {sid for sid, s in net.spans.items() if s.name_key == 'LOOP RD'}
        self.assertEqual({got['l1'].span_id, got['l9'].span_id}, loop_spans)   # stem then bulb, by house number

    def test_own_street_wins_within_500_m(self):
        db, _ = build(roads=[(1, 'Oak St', 'S1400', [(0, 0), (200, 0)]),
                             (2, 'Elm St', 'S1400', [(0, 200), (200, 200)])])
        net = G.load_network(db)
        oak, elm = (db.execute('SELECT id FROM road_span WHERE name_key=?', (k,)).fetchone()[0]
                    for k in ('OAK ST', 'ELM ST'))
        stops = [stop('own', 'OAK ST', 100, 300), stop('no_name', 'ASH ST', 100, 300),
                 stop('beyond', 'OAK ST', 100, 520)]
        got = {s['id']: p for s, p in zip(stops, G.snap_stops(net, stops))}
        self.assertEqual(G.SAME_NAME_M, 500.0)
        p = got['own']                                                 # Oak St 300 m off, Elm St 100 m: its own street
        self.assertEqual((p.placed_by, p.span_id, p.side_lr), ('geocode', oak, 1))
        self.assertAlmostEqual(p.pos_m, 100, delta=0.3)
        self.assertAlmostEqual(p.dist_m, 300, delta=0.3)
        q = got['no_name']                                             # the same spot, no street of its own name
        self.assertEqual((q.placed_by, q.span_id), ('nearest', elm))
        far = got['beyond']                                            # Oak St 520 m: not tier a; Elm St 320 m, tier e
        self.assertEqual((far.placed_by, far.tier, far.span_id), ('nearest', 'e', elm))
        self.assertAlmostEqual(far.dist_m, 320, delta=0.3)

    def test_unlocated_street_goes_to_the_chain_near_its_town(self):
        db, _ = build(roads=self.TOWNS)
        net = G.load_network(db)
        stops = [dict(stop('s1', 'SIDE ST', 10, 110, 1), zip='80901'), dict(stop('s2', 'SIDE ST', 90, 110, 3), zip='80901'),
                 dict(stop('m1', 'MAIN ST', house=1), zip='80901'), dict(stop('b1', 'MAIN ST', house=1, city='Elsewhere'))]
        got = {s['id']: p for s, p in zip(stops, G.interpolate(net, stops, G.snap_stops(net, stops)))}
        near = db.execute('SELECT span_id FROM road_edge WHERE name_key=? ORDER BY len_m', ('MAIN ST',)).fetchall()
        self.assertEqual(got['m1'].span_id, near[0][0])                  # nearest to the town's snapped stops
        self.assertEqual(got['b1'].span_id, near[1][0])                  # no hint for that town: the longest chain


def span_between(db, p, q):
    """Span id of the edge between two grid points of a built network."""
    node = lambda dx, dy: db.execute('SELECT id FROM road_node WHERE x=? AND y=?', (X0 + dx, Y0 + dy)).fetchone()[0]
    a, b = sorted((node(*p), node(*q)))
    return db.execute('SELECT span_id FROM road_edge WHERE u=? AND v=?', (a, b)).fetchone()[0]


class VirtualTests(unittest.TestCase):
    """Virtual streets for voter streets TIGER lacks, connectors that make the network one component, tier g."""
    OAK = [(1, 520, 285), (2, 530, 315), (3, 560, 285), (4, 570, 315), (5, 600, 285), (6, 610, 315)]   # odd S, even N

    def oak(self):
        return [stop('oak%d' % h, 'OAK CT', x, y, h) for h, x, y in self.OAK]

    def test_virtual_street_is_built_snapped_to_and_bridged(self):
        db, tiger = build()
        stops = self.oak() + [stop('lone', 'BIRCH LN', 650, 400, 1),                       # one geocode: no street
                              stop('own', 'MAPLE ST', 150, 260, 120), stop('own2', 'MAPLE ST', 120, 260, 102)]
        streets = G.virtual_streets(G.load_network(db), stops)
        self.assertEqual([(key, city) for key, _, city in streets], [('OAK CT', CITY)])   # Maple St: own span 60 m off
        line = np.array(streets[0][1])
        self.assertEqual(line[[0, -1]].tolist(), [[X0 + 520, Y0 + 285], [X0 + 610, Y0 + 315]])
        self.assertTrue(np.all(np.diff(line[:, 1]) > 0), line)       # the +-15 m zigzag across the street is gone
        stats = G.augment_network(db, streets)
        self.assertEqual((tiger['components'], stats['components'], stats['largest_component_share']), (2, 1, 1.0))
        self.assertEqual([stats[k] for k in ('virtual_streets', 'virtual_pieces', 'street_connectors',
                                             'component_connectors')], [1, 1, 2, 1])
        self.assertEqual(stats['street_connector_m'], {'count': 2, 'p50': 193.2, 'p95': 234.8, 'max': 239.4, 'km': 0.386})
        self.assertEqual(stats['component_connector_m'], {'count': 1, 'p50': 200.0, 'p95': 200.0, 'max': 200.0, 'km': 0.2})
        self.assertEqual((stats['nodes'], stats['edges'], stats['spans']), (20, 22, 21))
        virtual = db.execute('SELECT name_key, kind, round(len_m) FROM road_edge WHERE virtual=1 ORDER BY len_m').fetchall()
        self.assertEqual(virtual, [('OAK CT', 'S1400', 96), ('', 'S1400', 147),      # street, west end to (400, 200)
                                   ('', 'S1400', 200), ('', 'S1400', 239)])          # Pine Pl bridge, east end
        self.assertEqual(span_between(db, (600, 0), (400, 0)),            # Pine Pl joined at Cedar's east end
                         db.execute("SELECT span_id FROM road_edge WHERE virtual=1 AND round(len_m)=200").fetchone()[0])
        for sid, flag, edges in db.execute('SELECT id, virtual, edges FROM road_span'):
            flags = {db.execute('SELECT virtual FROM road_edge WHERE id=?', (abs(e),)).fetchone()[0] for e in json.loads(edges)}
            self.assertEqual(flags, {flag}, sid)
        self.assertEqual(db.execute('SELECT count(*) FROM road_edge WHERE virtual=0').fetchone()[0], 18)

        net = G.load_network(db)
        placed = G.snap_stops(net, stops)
        oak = db.execute("SELECT span_id FROM road_edge WHERE name_key='OAK CT'").fetchone()[0]
        got = {s['id']: p for s, p in zip(stops, placed)}
        self.assertEqual({(got['oak%d' % h].tier, got['oak%d' % h].span_id) for h in range(1, 7)}, {('a', oak)})
        for side in ((1, 3, 5), (2, 4, 6)):                          # house order along each side of the street
            positions = [got['oak%d' % h].pos_m for h in side]
            self.assertEqual(positions, sorted(positions), side)
        self.assertEqual([got['oak%d' % h].side_lr for h in (2, 3, 4, 5)], [1, -1, 1, -1])   # evens north = left
        even_left, _ = G.assign_sides(net, placed)
        self.assertEqual(even_left[oak], 1)
        on_bridge = G.snap_stops(net, [stop('bridge', '', 500, 0)])[0]  # on the connector: snapped to a real road
        self.assertEqual(db.execute('SELECT virtual FROM road_span WHERE id=?', (on_bridge.span_id,)).fetchone()[0], 0)
        self.assertAlmostEqual(on_bridge.dist_m, 100, delta=0.3)
        again, _ = build(shuffle=4)
        G.augment_network(again, G.virtual_streets(G.load_network(again), stops[::-1]))
        self.assertEqual(dump(again), dump(db))                       # same input, same tables

    def test_a_real_road_of_a_similar_name_vetoes_the_virtual_street(self):
        db, _ = build()
        net = G.load_network(db)
        near = [stop('b%d' % h, 'CEDAR HTS DR', x, 30, h) for h, x in ((2, 230), (4, 260))]      # fuzzy, tier b
        far = [stop('f%d' % h, 'CEDAR HTS DR', x, y, h) for h, x, y in ((6, 520, 285), (8, 610, 315))]  # tiers c, e
        placed = {s['id']: p.tier for s, p in zip(near + far, G.snap_stops(net, near + far))}
        self.assertEqual(placed, {'b2': 'b', 'b4': 'b', 'f6': 'c', 'f8': 'e'})
        self.assertEqual(G.virtual_streets(net, near + far + self.oak()), G.virtual_streets(net, self.oak()))
        self.assertEqual([k for k, *_ in G.virtual_streets(net, far + self.oak())], ['CEDAR HTS DR', 'OAK CT'])

    def test_virtual_streets_break_at_gaps_over_400_m(self):
        db, _ = build()
        net = G.load_network(db)
        stops = self.oak() + [stop('oak20', 'OAK CT', 1100, 300, 20), stop('oak22', 'OAK CT', 1150, 300, 22),  # 490 m on
                              stop('oak40', 'OAK CT', 2000, 2000, 40),                                       # alone
                              stop('fig1', 'FIG LN', 2000, 1000, 1), stop('fig3', 'FIG LN', 2000, 1400, 3),  # 400 m: kept
                              stop('fig5', 'FIG LN', 2000, 1800.5, 5)]                                       # 400.5 m: cut
        self.assertEqual(G.VIRTUAL_GAP_M, 400.0)
        pieces = G.virtual_streets(net, stops)
        self.assertEqual([(k, len(line)) for k, line, _ in pieces], [('FIG LN', 2), ('OAK CT', 6), ('OAK CT', 2)])
        self.assertEqual(pieces[2][1], [(X0 + 1100, Y0 + 300), (X0 + 1150, Y0 + 300)])
        stats = G.augment_network(db, pieces)
        self.assertEqual([stats[k] for k in ('virtual_streets', 'virtual_pieces', 'street_connectors')], [2, 3, 6])
        ends = db.execute("SELECT round(len_m) FROM road_edge WHERE virtual=1 AND name_key='' ORDER BY len_m").fetchall()
        self.assertIn((500.0,), ends)                                 # the far Oak Ct piece has connectors of its own
        self.assertIn((541.0,), ends)
        self.assertEqual(stats['components'], 1)

    def test_real_and_virtual_edges_never_share_a_span(self):
        db, _ = build(roads=[(1, '', 'S1400', [(0, 0), (100, 0)]), (2, 'Far Rd', 'S1400', [(300, 0), (400, 0)])])
        stats = G.augment_network(db, [])
        self.assertEqual((stats['components'], stats['component_connectors'], stats['spans']), (1, 1, 3))
        degree = db.execute('SELECT degree FROM road_node WHERE x=? AND y=?', (X0 + 100, Y0)).fetchone()[0]
        self.assertEqual(degree, 2)                                   # unnamed dead end + unnamed connector meet here
        self.assertEqual(db.execute('SELECT virtual, round(len_m) FROM road_span WHERE virtual=1').fetchall(), [(1, 200)])

    def test_tier_g_goes_to_the_nearest_road_of_an_area_centre(self):
        db, _ = build()
        net = G.load_network(db)
        stops = [stop('g', 'NOWHERE LN', house=7), stop('kept', 'MAPLE ST', 150, 190, 120)]
        placed = G.interpolate(net, stops, G.snap_stops(net, stops))
        self.assertEqual((placed[0].span_id, placed[0].tier), (None, None))   # tier d has nothing to go on
        got = G.place_near(net, stops, placed, {0: (X0 + 500, Y0 + 500)})
        self.assertEqual(got[1], placed[1])
        g = got[0]
        self.assertEqual((g.placed_by, g.tier, g.span_id, g.side_lr), ('interpolated', 'g', span_between(db, (300, 200),
                                                                                                         (300, 300)), 0))
        self.assertAlmostEqual(g.pos_m, 100, delta=0.3)
        lon, lat = G.to_lonlat(X0 + 300, Y0 + 300)
        self.assertAlmostEqual(g.lon, lon, delta=2e-6)
        self.assertAlmostEqual(g.lat, lat, delta=2e-6)


if __name__ == '__main__':
    unittest.main()
