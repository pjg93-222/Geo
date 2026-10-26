"""Synthetic checks for tools/build_walk.py (no real data): the walk store built from server/research/walk_testkit's
invented world against the testkit's hand-computed oracle, date-format detection, the quality gates and the
replace / backup rules.

    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 <venv python> -m unittest tools.test_walk_build     (from the V3 folder)

The synthetic world sits under one plan threshold on purpose (2022g mail share 0.70). Its TIGER roads are two components
(the isolated Pine St in Fountain: 0.899 of the length); the builder joins them with a virtual connector, so the store's
network is one. Tests that need a passing build relax the mail share with RELAXED (its component floor is the one tools/
make_walk_world.py uses and no longer matters); one test pins that the plan's own values fail this world and that GATES
holds them.
"""
import contextlib
import copy
import csv
import hashlib
import hmac
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pandas as pd

from server.research import walk_names as N
from server.research import walk_testkit as T
from tools import build_walk as B
from tools import walk_geo as G

RELAXED = {'largest_component': 0.85, 'abs_share': 0.65}
TABLES = {
    'meta': ['key', 'value'],
    'street': ['id', 'key', 'city', 'zip', 'display'],
    'stop': ['id', 'street_id', 'house', 'house_int', 'parity', 'span_id', 'pos_m', 'side_lr', 'placed_by', 'lon', 'lat'],
    'person': ['person_id', 'household_id', 'stop_id', 'street_id', 'unit', 'ss', 'sh', 'precinct', 'city', 'zip',
               'party', 'age', 'gender', 'reg_date', 'pri_voted', 'pri_elig', 'gen_voted', 'gen_elig', 'abs_voted',
               'abs_elig', 'voter_key'],
    'combo': ['ss', 'sh', 'precinct', 'street_id', 'people', 'doors'],
    'road_node': ['id', 'lon', 'lat', 'x', 'y', 'degree'],
    'road_edge': ['id', 'u', 'v', 'len_m', 'kind', 'name_key', 'span_id', 'coords', 'virtual'],
    'road_span': ['id', 'name_key', 'a', 'b', 'len_m', 'kind', 'edges', 'coords', 'even_left', 'virtual'],
}
META_KEYS = {'schema', 'source_sha256', 'roads_sha256', 'built_utc', 'n_people', 'n_stops', 'abs_rule', 'window', 'qa'}


def connect(path):
    connection = sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def build(stores, **kwargs):
    kwargs.setdefault('gates', RELAXED)
    return B.build(stores.source_csv, stores.directory, **kwargs)


def cli(stores, *extra, relaxed=True):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
            mock.patch.dict(B.GATES, RELAXED if relaxed else {}):
        code = B.main(['--source', str(stores.source_csv), '--data-dir', str(stores.directory), *extra])
    return code, out.getvalue(), err.getvalue()


def rewrite_source(stores, edit):
    """Edit the source CSV as edit(header, rows) -> (header, rows) and point the three stores at its new sha256."""
    path = Path(stores.source_csv)
    with path.open(newline='', encoding='cp1252') as stream:
        rows = list(csv.reader(stream))
    header, body = edit(rows[0], rows[1:])
    with path.open('w', newline='', encoding='cp1252') as stream:
        csv.writer(stream).writerows([header, *body])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    for name, key in (('contacts', 'source_sha256'), ('street', 'source_sha256'), ('scores', 'contacts_source_sha256')):
        with closing(sqlite3.connect(Path(stores.directory) / (name + '.sqlite3'))) as db, db:
            db.execute('UPDATE metadata SET value=? WHERE key=?', (json.dumps(digest), key))
    return digest


def span_of_block(db, block):
    """Id of the span whose two ends are the two ends of an expected block, whichever way it is drawn."""
    def near(p, q):
        return abs(p[0] - q[0]) < 5e-5 and abs(p[1] - q[1]) < 5e-5
    hits = []
    for sid, coords in db.execute('SELECT id, coords FROM road_span'):
        c = json.loads(coords)
        if (near(c[0], block['from']) and near(c[-1], block['to'])) or (near(c[0], block['to']) and near(c[-1], block['from'])):
            hits.append(sid)
    assert len(hits) == 1, block['id']
    return hits[0]


def add_person(rows, like, n, house, street=None, at=None):
    """Append a copy of the person row `like` as invented person n: own ids, household and voter id, the given house
    text, street (streetname, suffix) and registration coordinates (local metres; None: no geocode)."""
    row = copy.deepcopy(like)
    c = row['csv']
    c.update(sii_persist_id='777%06d' % n, sii_statara_id='666%06d' % n, sii_household_id='SYNHHX%03d' % n,
             svi_voter_id='93%07d' % n, svi_registration_addressrange=house)
    if street:
        c['svi_registration_streetname'], c['svi_registration_suffix'] = street
    c['svi_registration_address'] = c['sii_address'] = ' '.join(
        p for p in (house, c['svi_registration_streetname'], c['svi_registration_suffix']) if p)
    row.update(key='x%02d' % n, door=None, household=1000 + n, person_id=T.opaque_id(T.SALT, 'p', c['sii_persist_id']))
    row['lon'], row['lat'] = T.lonlat(*at) if at else (None, None)
    row['res_lon'], row['res_lat'] = row['lon'], row['lat']
    rows.append(row)
    return row


def dump(path):
    """Every table of a walk store as sorted tuples; meta reduced to what a rebuild must reproduce."""
    with closing(connect(path)) as db:
        out = {table: [tuple(r) for r in db.execute('SELECT * FROM %s ORDER BY %s' % (table, order))]
               for table, order in (('street', 'id'), ('stop', 'id'), ('person', 'person_id'), ('road_node', 'id'),
                                    ('road_edge', 'id'), ('road_span', 'id'),
                                    ('combo', 'ss, sh, precinct, street_id'))}
        meta = {r['key']: r['value'] for r in db.execute('SELECT key, value FROM meta')}
    qa = json.loads(meta.pop('qa'))
    qa.pop('timing')
    qa.pop('peak_memory_mb')
    meta.pop('built_utc')
    return out, meta, qa


class BuiltStoreTests(unittest.TestCase):
    """One build of the hand case (ISO dates), read back against the oracle."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.stores = T.write_minimal_stores(cls.tmp.name)
        cls.expected = cls.stores.expected
        cls.result = build(cls.stores)
        cls.path = cls.stores.directory / 'walk.sqlite3'
        cls.db = connect(cls.path)
        cls.persons = {r['person_id']: r for r in cls.db.execute('SELECT * FROM person')}
        cls.spans = cls.block_spans()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.tmp.cleanup()

    @classmethod
    def block_spans(cls):
        """{block id: (span id, coords, len_m, even_left)}: the span whose two ends are the block's two ends."""
        spans = [(r['id'], json.loads(r['coords']), r['len_m'], r['even_left'])
                 for r in cls.db.execute('SELECT id, coords, len_m, even_left FROM road_span')]

        def near(p, q):
            return abs(p[0] - q[0]) < 5e-5 and abs(p[1] - q[1]) < 5e-5
        out = {}
        for block in cls.expected['blocks']:
            a, b = block['from'], block['to']
            hits = [s for s in spans
                    if (near(s[1][0], a) and near(s[1][-1], b)) or (near(s[1][0], b) and near(s[1][-1], a))]
            assert len(hits) == 1, block['id']
            out[block['id']] = hits[0]
        return out

    def stop_row(self, door):
        sid = self.persons[self.expected['doors'][door]['people'][0]]['stop_id']
        return self.db.execute('SELECT * FROM stop WHERE id=?', (sid,)).fetchone()

    def block_of(self, door):
        return next(b for b in self.expected['blocks'] if door in b['doors'])

    # ---- shape ----------------------------------------------------------------------------------------------------
    def test_schema_matches_the_contract(self):
        names = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                               "AND name NOT LIKE 'road_edge_box_%'")}
        self.assertEqual(names, {*TABLES, 'road_edge_box'})
        for table, columns in TABLES.items():
            self.assertEqual([r[1] for r in self.db.execute('PRAGMA table_info(%s)' % table)], columns, table)
        self.assertIn('WITHOUT ROWID', self.db.execute("SELECT sql FROM sqlite_master WHERE name='person'").fetchone()[0])
        indexes = {r[0]: [i[2] for i in self.db.execute('PRAGMA index_info(%s)' % r[0])] for r in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name IN ('person', 'stop') "
            "AND name NOT LIKE 'sqlite_%'")}
        self.assertEqual(indexes, {'person_street': ['street_id'], 'person_stop': ['stop_id'],
                                   'person_area': ['ss', 'sh', 'precinct'], 'stop_street': ['street_id']})
        self.assertIn('UNIQUE', self.db.execute("SELECT sql FROM sqlite_master WHERE name='street'").fetchone()[0])

    def test_counts_match_the_oracle(self):
        want, one = self.expected['counts'], lambda sql: self.db.execute(sql).fetchone()[0]
        located = {p['street_household_id'] for p in self.expected['people'].values() if p['street_household_id']}
        self.assertEqual(one('SELECT count(*) FROM person'), want['people'])
        self.assertEqual(one('SELECT count(*) FROM stop'), want['stops'])
        self.assertEqual(one('SELECT count(*) FROM street'), want['streets'])
        self.assertEqual(one('SELECT count(*) FROM road_span WHERE virtual=0'), want['spans'])
        self.assertEqual(one('SELECT count(*) FROM road_span WHERE virtual=1'), 1)     # Fountain's Pine St joined
        self.assertEqual(one('SELECT count(DISTINCT household_id) FROM person'), len(located))
        self.assertEqual(one('SELECT count(*) FROM person WHERE household_id IS NULL'), want['ungeocoded_people'])
        self.assertEqual(one('SELECT count(*) FROM (SELECT DISTINCT stop_id, unit FROM person)'), want['doors'])
        self.assertEqual(one('SELECT count(DISTINCT precinct) FROM person'), want['precincts'])
        self.assertEqual(one('SELECT count(*) FROM person WHERE stop_id IS NULL OR street_id IS NULL'), 0)
        self.assertEqual(one('SELECT count(*) FROM road_node'), len(self.expected['topology']['nodes']))

    def test_every_person_matches_the_oracle(self):
        for pid, want in self.expected['people'].items():
            row, door = self.persons[pid], self.expected['doors'][want['door']]
            label = want['key']
            self.assertEqual((row['pri_voted'], row['pri_elig']), (want['pri']['voted'], want['pri']['eligible']), label)
            self.assertEqual((row['gen_voted'], row['gen_elig']), (want['gen']['voted'], want['gen']['eligible']), label)
            self.assertEqual((row['abs_voted'], row['abs_elig']), (want['abs']['voted'], want['abs']['eligible']), label)
            self.assertEqual([row[k] for k in ('age', 'gender', 'party', 'ss', 'sh', 'precinct', 'reg_date')],
                             [want[k] for k in ('age', 'gender', 'party', 'ss', 'sh', 'precinct', 'reg_date')], label)
            self.assertEqual((row['city'], row['zip']), (door['city'], door['zip']), label)
            self.assertEqual(row['unit'], 'Apt ' + door['unit'] if door['unit'] else '', label)
            self.assertEqual(row['household_id'], want['street_household_id'], label)

    def test_voter_keys_are_hmacs_under_the_walk_salt(self):
        salt = (self.stores.directory / '.walk-salt').read_bytes().strip()
        self.assertEqual(len(bytes.fromhex(salt.decode('ascii'))), 32)
        for pid, want in self.expected['people'].items():
            self.assertEqual(self.persons[pid]['voter_key'],
                             hmac.new(salt, want['voter_id'].encode(), hashlib.sha256).hexdigest(), want['key'])
        self.assertEqual(len({r['voter_key'] for r in self.persons.values()}), len(self.persons))

    def test_streets_are_unique_per_key_and_city(self):
        rows = {(r['key'], r['city']): r for r in self.db.execute('SELECT * FROM street')}
        self.assertEqual(len(rows), len(self.expected['streets']))
        self.assertEqual([r['id'] for r in sorted(rows.values(), key=lambda r: (r['key'], r['city']))], [1, 2, 3, 4, 5])
        for want in self.expected['streets']:
            row = rows[(N.name_key(want['street']), want['city'])]
            self.assertEqual((row['display'], row['zip']), (want['street'], want['zip']))
            people = self.db.execute('SELECT count(*) FROM person WHERE street_id=?', (row['id'],)).fetchone()[0]
            doors = {parity: self.db.execute(
                'SELECT count(*) FROM (SELECT DISTINCT p.stop_id, p.unit FROM person p JOIN stop s ON s.id = p.stop_id '
                'WHERE p.street_id=? AND s.parity=?)', (row['id'], parity)).fetchone()[0] for parity in (0, 1)}
            self.assertEqual((people, doors[0], doors[1]), (want['people'], want['doors_even'], want['doors_odd']))
        pines = [r for r in rows.values() if r['display'] == 'Pine St']
        self.assertEqual(sorted(r['city'] for r in pines), ['COLORADO SPRINGS', 'FOUNTAIN'])

    # ---- stops ----------------------------------------------------------------------------------------------------
    def test_stops_group_doors_by_street_and_house(self):
        stop_of = {k: self.stop_row(k)['id'] for k in self.expected['doors']}
        seen = {}
        for stop_key, doors in self.expected['stops'].items():
            ids = {stop_of[d] for d in doors}
            self.assertEqual(len(ids), 1, stop_key)
            seen[stop_key] = ids.pop()
        self.assertEqual(len(set(seen.values())), len(seen))
        for stop_key, sid in seen.items():
            door = self.expected['doors'][stop_key]
            row = self.db.execute('SELECT * FROM stop WHERE id=?', (sid,)).fetchone()
            self.assertEqual((row['house'], row['house_int'], row['parity']),
                             (door['house'], door['house_int'], 0 if door['parity'] == 'even' else 1))
            street = self.db.execute('SELECT * FROM street WHERE id=?', (row['street_id'],)).fetchone()
            self.assertEqual((street['display'], street['city']), (door['street'], door['city']))
        self.assertEqual(self.stop_row('d12')['id'], self.stop_row('d13')['id'])     # one apartment house, two doors
        units = {self.persons[p]['unit'] for k in ('d12', 'd13') for p in self.expected['doors'][k]['people']}
        self.assertEqual(units, {'Apt A', 'Apt B'})

    def test_geocoded_stops_sit_on_their_block(self):
        for block in self.expected['blocks']:
            span_id, _, length, _ = self.spans[block['id']]
            for key in block['doors']:
                door = self.expected['doors'][key]
                if not door['geocoded']:
                    continue
                row = self.stop_row(key)
                self.assertEqual((row['placed_by'], row['span_id']), ('geocode', span_id), key)
                self.assertTrue(0 <= row['pos_m'] <= length, key)
                self.assertAlmostEqual(row['lon'], door['lonlat'][0], places=6, msg=key)       # the geocode, not the snap
                self.assertAlmostEqual(row['lat'], door['lonlat'][1], places=6, msg=key)

    def test_the_door_without_coordinates_is_interpolated_between_its_neighbours(self):
        want = self.expected['interpolated']
        stop, before, after = (self.stop_row(want[k]) for k in ('door', 'before', 'after'))
        self.assertEqual(stop['placed_by'], 'interpolated')
        self.assertEqual(stop['span_id'], before['span_id'])
        self.assertEqual(stop['span_id'], after['span_id'])
        self.assertTrue(min(before['pos_m'], after['pos_m']) < stop['pos_m'] < max(before['pos_m'], after['pos_m']))
        fraction = (stop['pos_m'] - before['pos_m']) / (after['pos_m'] - before['pos_m'])
        self.assertAlmostEqual(fraction, want['fraction'], delta=0.001)
        metres = max(abs(stop['lon'] - want['centreline'][0]) * T.M_LON, abs(stop['lat'] - want['centreline'][1]) * T.M_LAT)
        self.assertLess(metres, 2.0)                                       # the centreline point, not a geocode
        by_position = sorted(want['order'], key=lambda k: self.stop_row(k)['pos_m'])
        self.assertIn(by_position, (want['order'], want['order'][::-1]))   # same-side doors in house-number order
        self.assertEqual(self.db.execute("SELECT count(*) FROM stop WHERE placed_by='interpolated'").fetchone()[0], 1)

    def test_house_numbers_run_monotonically_along_each_side_of_a_block(self):
        groups = {}
        for row in self.db.execute('SELECT * FROM stop WHERE span_id IS NOT NULL'):
            groups.setdefault((row['span_id'], row['parity']), []).append((row['house_int'], row['pos_m']))
        checked = 0
        for key, members in groups.items():
            positions = [pos for _, pos in sorted(members)]
            steps = np.diff(positions)
            self.assertTrue((steps > 0).all() or (steps < 0).all(), key)
            checked += len(members) > 1
        self.assertGreaterEqual(checked, 6)

    def test_even_numbers_are_on_the_expected_side_of_every_block(self):
        for block in self.expected['blocks']:
            span_id, coords, _, even_left = self.spans[block['id']]
            self.assertEqual(even_left, T.even_left_for(block, coords[0], coords[-1]), block['id'])
            if block['even_normal'] is None:
                self.assertIsNone(even_left, block['id'])
                continue
            for key in block['doors']:
                door, row = self.expected['doors'][key], self.stop_row(key)
                if door['geocoded']:
                    on_even_side = door['side'] == block['even_side']
                    self.assertEqual(row['side_lr'], 1 if on_even_side == bool(even_left) else -1, key)
                else:                                                      # interpolated: the side its parity belongs to
                    self.assertEqual(row['side_lr'], 1 if (row['parity'] == 0) == bool(even_left) else -1, key)
        self.assertIn('d07', [k for b in self.expected['blocks'] for k in b['mismatched']])    # a vote against the majority
        self.assertEqual(self.result['qa']['side_agreement'], 0.96)

    def test_combos_match_the_oracle(self):
        street_id = {(r['display'], r['city']): r['id'] for r in self.db.execute('SELECT * FROM street')}
        want = {(c['ss'], c['sh'], c['precinct'], street_id[(c['street'], c['city'])], c['people'], c['doors'])
                for c in self.expected['combos']}
        got = [tuple(r) for r in self.db.execute('SELECT ss, sh, precinct, street_id, people, doors FROM combo')]
        self.assertEqual(set(got), want)
        self.assertEqual(len(got), len(want))
        self.assertEqual(sum(r[4] for r in got), self.expected['counts']['people'])

    # ---- meta, network, privacy ------------------------------------------------------------------------------------
    def test_meta_keys_and_qa(self):
        meta = {r['key']: r['value'] for r in self.db.execute('SELECT key, value FROM meta')}
        self.assertEqual(set(meta), META_KEYS)
        self.assertEqual((meta['schema'], meta['n_people'], meta['n_stops']), ('walk-v1', '38', '26'))
        self.assertEqual(meta['source_sha256'], self.stores.source_sha256)
        self.assertEqual(meta['roads_sha256'], hashlib.sha256(self.stores.roads_path.read_bytes()).hexdigest())
        self.assertEqual(meta['abs_rule'], N.ABS_RULE)
        self.assertEqual(json.loads(meta['window']), N.WINDOW)
        self.assertRegex(meta['built_utc'], r'^\d{4}-\d\d-\d\dT')
        qa = json.loads(meta['qa'])
        self.assertEqual(qa['rows'], 38)
        self.assertEqual(qa['stop_counts']['by_placed_by'], {'geocode': 25, 'fuzzy': 0, 'nearest': 0, 'interpolated': 1})
        self.assertEqual((qa['stop_counts']['located'], qa['stop_counts']['snapped_share']), (25, 1.0))
        self.assertEqual(qa['stop_counts']['by_tier'], {'a': 25, 'b': 0, 'c': 0, 'd': 1, 'e': 0, 'g': 0})
        self.assertEqual((qa['stop_counts']['unplaced'], qa['stop_counts']['without_span']), (0, 0))
        self.assertEqual(sum(qa['stop_counts']['tier_e_road_m'].values()), 0)
        self.assertEqual(qa['stop_counts']['tier_g_area'], {'block': 0, 'precinct': 0, 'zip': 0, 'all': 0})
        self.assertEqual((qa['people_in_stops_without_span'], qa['people_without_stop']), (0, 0))
        self.assertEqual(qa['cities'], {'in_file': 2, 'canonical': 2})
        tiger = qa['network']['tiger']
        self.assertEqual((tiger['components'], tiger['largest_component_share']),
                         (self.expected['topology']['components'], 0.8993))
        self.assertEqual((qa['network']['components'], qa['network']['largest_component_share']), (1, 1.0))
        self.assertEqual([qa['network'][k] for k in ('virtual_streets', 'street_connectors', 'component_connectors')],
                         [0, 0, 1])
        self.assertEqual(set(qa['g_abs']), {'2020g', '2022g', '2024g'})
        self.assertEqual(qa['g_abs']['2022g'], {'recorded': 30, 'mail': 21, 'share': 0.7})
        self.assertEqual(set(qa['gates']), {'unplaced_stops', 'stops_without_span', 'largest_component',
                                           'side_agreement', 'abs_share_2020g', 'abs_share_2022g', 'abs_share_2024g'})
        self.assertEqual([qa['gates'][k]['value'] for k in ('unplaced_stops', 'stops_without_span', 'largest_component')],
                         [0, 0, 1.0])
        self.assertTrue(all(g['pass'] for g in qa['gates'].values()))
        self.assertEqual(set(qa['timing']), {'checksums', 'stores', 'read_csv', 'streets', 'network', 'write', 'total'})
        self.assertEqual(qa['dates']['sii_dob']['format'], '%Y-%m-%d')
        self.assertEqual(qa['dates']['svi_registration_date']['format'], '%Y-%m-%d')
        self.assertFalse(qa['forced'])

    def test_road_network_is_in_the_store(self):
        topology = self.expected['topology']
        self.assertEqual(self.db.execute('SELECT count(*) FROM road_edge WHERE virtual=0').fetchone()[0],
                         len(topology['kept_rows']))
        self.assertEqual(self.db.execute('SELECT count(*) FROM road_edge_box').fetchone()[0], len(topology['kept_rows']) + 1)
        bridge = self.db.execute('SELECT name_key, kind, len_m, u, v FROM road_edge WHERE virtual=1').fetchall()
        self.assertEqual([tuple(r)[:2] for r in bridge], [('', 'S1400')])
        self.assertAlmostEqual(bridge[0]['len_m'], 2752.6, delta=0.01 * 2752.6)   # Fountain's Pine St to Academy Blvd
        ends = self.db.execute('SELECT lon, lat FROM road_node WHERE id IN (?, ?) ORDER BY lat', tuple(bridge[0])[3:])
        for got, want in zip(ends, (T.lonlat(450, -3200), T.lonlat(330, -450))):      # Pine St's west end first
            self.assertAlmostEqual(got['lon'], want[0], delta=2e-5)
            self.assertAlmostEqual(got['lat'], want[1], delta=2e-5)
        other = self.db.execute("SELECT count(*) FROM road_edge WHERE kind NOT IN ('S1200', 'S1400')").fetchone()[0]
        self.assertEqual(other, 0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM road_span WHERE even_left IS NOT NULL').fetchone()[0],
                         sum(1 for b in self.expected['blocks'] if b['even_normal'] is not None))

    def test_no_name_address_or_contact_text_is_stored(self):
        text = ' '.join(str(v) for t in ('meta', 'street', 'stop', 'person', 'combo') for r in self.db.execute(
            'SELECT * FROM %s' % t) for v in tuple(r)).lower()
        for want in self.expected['people'].values():
            for token in re.findall(r"[^\W\d_]+", want['name'].lower()):
                if len(token) > 2:
                    self.assertIsNone(re.search(r'\b%s\b' % re.escape(token), text), want['key'])
            for value in (want['phone'], want['wireless'], want['voter_id'], want['dob']):
                if value:
                    self.assertNotIn(value.lower(), text)


class RunTests(unittest.TestCase):
    """Replace, backup, gate and failure rules of the CLI, one fresh directory per test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stores = T.write_minimal_stores(self.tmp.name)
        self.dir = self.stores.directory
        self.out = self.dir / 'walk.sqlite3'

    def only_expected_files(self):
        names = {p.name for p in self.dir.iterdir()}
        self.assertFalse([n for n in names if n.startswith('.walk-build-') or n.endswith('.build.lock')], names)

    def bad_world(self, directory):
        """The same people, but nobody voted by mail in 2020g."""
        rows = copy.deepcopy(self.stores.case.rows)
        for row in rows:
            if row['csv']['svi_vh_2020g']:
                row['csv']['svi_vh_2020g'] = 'PC'
        return T.write_minimal_stores(directory, T.Case(rows, self.stores.case.expected))

    def test_plan_thresholds(self):
        self.assertEqual(B.GATES, {'unplaced_stops': 0, 'stops_without_span': 0, 'largest_component': 1.0,
                                   'side_agreement': 0.85, 'abs_share': 0.85})
        self.assertEqual(B.CEILINGS, ('unplaced_stops', 'stops_without_span'))
        self.assertEqual(N.ABS_GATE['elections'], ('2020g', '2022g', '2024g'))
        raw = {'unplaced_stops': 1, 'stops_without_span': 0, 'largest_component': 0.9999, 'side_agreement': 0.85,
               'abs_share': {'2020g': 0.9}}
        judged = B.judge(raw, B.GATES)
        self.assertEqual({k: g['pass'] for k, g in judged.items()}, {'unplaced_stops': False, 'stops_without_span': True,
                                                                    'largest_component': False, 'side_agreement': True,
                                                                    'abs_share_2020g': True})
        self.assertEqual((judged['unplaced_stops']['max'], judged['largest_component']['min']), (0, 1.0))

    def test_the_synthetic_world_fails_the_plan_thresholds_and_nothing_is_written(self):
        code, out, err = cli(self.stores, relaxed=False)
        self.assertEqual(code, 2, err)
        self.assertFalse(self.out.exists())
        self.assertIn('NOT replaced', out)
        self.assertIn('abs_share_2022g', out)
        qa = json.loads(out[out.index('{'):out.rindex('}') + 1])
        failed = sorted(k for k, g in qa['gates'].items() if not g['pass'])
        self.assertEqual(failed, ['abs_share_2022g'])                    # the connected network passes at 100 %
        self.assertEqual(qa['gates']['largest_component'], {'value': 1.0, 'min': 1.0, 'pass': True})
        self.only_expected_files()
        self.assertFalse((self.dir / 'walk-backups').exists())

    def test_a_bad_g_abs_exits_2_and_leaves_the_old_file_alone(self):
        self.assertEqual(cli(self.stores)[0], 0)
        before = self.out.read_bytes()
        stores = self.bad_world(self.tmp.name)
        code, out, err = cli(stores)
        self.assertEqual(code, 2, err)
        self.assertEqual(self.out.read_bytes(), before)
        self.assertFalse((self.dir / 'walk-backups').exists())
        self.only_expected_files()
        qa = json.loads(out[out.index('{'):out.rindex('}') + 1])
        self.assertEqual({k for k, g in qa['gates'].items() if not g['pass']}, {'abs_share_2020g'})
        self.assertEqual(qa['g_abs']['2020g'], {'recorded': 35, 'mail': 0, 'share': 0.0})
        self.assertIn('failed gates: abs_share_2020g', out)

    def test_located_stops_far_from_every_road_go_to_the_nearest_road(self):
        rows = copy.deepcopy(self.stores.case.rows)
        for row in rows:
            if row['door'] in ('d01', 'd04'):                           # geocoded about a kilometre north of any road
                row['lat'] += 0.01
        stores = T.write_minimal_stores(self.tmp.name, T.Case(rows, self.stores.case.expected))
        code, out, err = cli(stores)
        self.assertEqual(code, 0, err)                                  # tier e: placed at any distance, gates hold
        qa = json.loads(out[out.index('{'):out.rindex('}') + 1])
        counts = qa['stop_counts']
        self.assertEqual((counts['located'], counts['snapped'], counts['snapped_share']), (25, 23, 0.92))
        self.assertEqual(counts['by_tier'], {'a': 23, 'b': 0, 'c': 0, 'd': 1, 'e': 2, 'g': 0})
        self.assertEqual(counts['tier_e_road_m'], {'150-250': 0, '250-500': 0, '500-1000': 2, '1000-2000': 0, '2000+': 0})
        self.assertEqual(counts['by_placed_by'], {'geocode': 23, 'fuzzy': 0, 'nearest': 2, 'interpolated': 1})
        academy = next(b for b in stores.expected['blocks'] if b['id'] == 'acad_n')
        with closing(connect(self.out)) as db:
            for key, door in (('p01', 'd01'), ('p07', 'd04')):
                moved = db.execute('SELECT s.* FROM stop s JOIN person p ON p.stop_id = s.id WHERE p.person_id = ?',
                                   (stores.expected['keys'][key],)).fetchone()
                self.assertEqual((moved['placed_by'], moved['span_id']), ('nearest', span_of_block(db, academy)), key)
                geocode = next(r for r in rows if r['door'] == door)
                self.assertEqual((moved['lon'], moved['lat']), (round(geocode['lon'], 6), round(geocode['lat'], 6)))

    def everyone_world(self, directory, blocks=False):
        """The hand case plus six invented people: four geocoded on Aster Way (no road of that name: a virtual street),
        one without a geocode on Larkspur Ct (no road either) and one with no house number on Cedar Heights Dr, both
        for tier g. With blocks, scores.person gains a reg_block column that puts those two in the census block of the
        Pine St households (doors d24, d25); everyone else has none."""
        rows = copy.deepcopy(self.stores.case.rows)
        like = next(r for r in rows if r['key'] == 'p05')
        for n, (house, x, y) in enumerate((('101', 500, 95), ('102', 520, 125), ('103', 540, 95), ('104', 560, 125)), 1):
            add_person(rows, like, n, house, ('ASTER', 'WAY'), (x, y))
        add_person(rows, like, 5, '7', ('LARKSPUR', 'CT'))
        add_person(rows, like, 6, '')
        stores = T.write_minimal_stores(directory, T.Case(rows, self.stores.case.expected))
        if blocks:
            pine = [p for d in ('d24', 'd25') for p in self.stores.expected['doors'][d]['people']]
            with closing(sqlite3.connect(stores.scores_path)) as db, db:
                db.execute('ALTER TABLE person ADD COLUMN reg_block TEXT')
                db.executemany('UPDATE person SET reg_block=? WHERE person_id=?',
                               [('080410001001000', p) for p in pine + [r['person_id'] for r in rows[-2:]]])
        return stores, rows[-6:]

    def stop_of(self, db, row):
        return db.execute('SELECT s.*, r.virtual AS on_virtual, r.name_key AS span_key FROM stop s JOIN person p ON '
                          'p.stop_id = s.id JOIN road_span r ON r.id = s.span_id WHERE p.person_id = ?',
                          (row['person_id'],)).fetchone()

    def test_every_person_gets_a_stop_and_every_stop_a_place(self):
        stores, extra = self.everyone_world(Path(self.tmp.name) / 'everyone')
        code, out, err = cli(stores)
        self.assertEqual(code, 0, err)
        qa = json.loads(out[out.index('{'):out.rindex('}') + 1])
        counts = qa['stop_counts']
        self.assertEqual((qa['people'], qa['stops'], qa['people_without_stop']), (44, 32, 0))
        self.assertEqual(counts['by_tier'], {'a': 29, 'b': 0, 'c': 0, 'd': 1, 'e': 0, 'g': 2})
        self.assertEqual((counts['unplaced'], counts['without_span'], counts['without_house_number']), (0, 0, 1))
        self.assertEqual(counts['tier_g_area'], {'block': 0, 'precinct': 2, 'zip': 0, 'all': 0})   # no reg_block column
        self.assertEqual([qa['network'][k] for k in ('virtual_streets', 'virtual_pieces', 'street_connectors',
                                                     'component_connectors', 'components')], [1, 1, 2, 1, 1])
        self.assertEqual([qa['network'][k]['count'] for k in ('street_connector_m', 'component_connector_m')], [2, 1])
        self.assertTrue(all(g['pass'] for g in qa['gates'].values()))
        with closing(connect(stores.directory / 'walk.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM person WHERE stop_id IS NULL').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM stop WHERE span_id IS NULL OR lon IS NULL').fetchone()[0], 0)
            for row in extra[:4]:                                       # on the virtual street through their geocodes
                s = self.stop_of(db, row)
                self.assertEqual((s['placed_by'], s['on_virtual'], s['span_key']), ('geocode', 1, 'ASTER WAY'))
                self.assertEqual((s['lon'], s['lat']), (round(row['lon'], 6), round(row['lat'], 6)))
            for row in extra[4:]:
                self.assertEqual(self.stop_of(db, row)['placed_by'], 'interpolated')
            blank = self.stop_of(db, extra[5])
            self.assertEqual((blank['house'], blank['house_int'], blank['parity']), ('', None, None))

    def test_tier_g_uses_the_census_block_centre_when_the_store_has_blocks(self):
        stores, extra = self.everyone_world(Path(self.tmp.name) / 'blocks', blocks=True)
        result = build(stores)
        self.assertTrue(result['passed'], result['failed'])
        self.assertEqual(result['qa']['stop_counts']['tier_g_area'], {'block': 2, 'precinct': 0, 'zip': 0, 'all': 0})
        pine = next(b for b in stores.expected['blocks'] if b['id'] == 'pine_n')
        with closing(connect(stores.directory / 'walk.sqlite3')) as db:
            for row in extra[4:]:                     # the two Pine St households' centre lies on Pine St's north block
                self.assertEqual(self.stop_of(db, row)['span_id'], span_of_block(db, pine))

    def test_city_spellings_in_the_file_are_one_city(self):
        base = Path(self.tmp.name) / 'base'
        reference = build(T.write_minimal_stores(base))
        spellings = ('COLO SPGS', 'CO SPGS', 'COLORADO SPRINGS')

        def edit(header, body):
            city = header.index('svi_registration_city')
            for i, row in enumerate(body):
                if row[city] == 'COLORADO SPRINGS':
                    row[city] = spellings[i % 3]
            return header, body
        rewrite_source(self.stores, edit)
        result = build(self.stores)
        self.assertEqual(result['qa']['cities'], {'in_file': 4, 'canonical': 2})
        self.assertEqual(reference['qa']['cities'], {'in_file': 2, 'canonical': 2})
        one, two = dump(base / 'walk.sqlite3')[0], dump(self.out)[0]
        for table in one:
            if table == 'person':                                       # voter_key follows each directory's own salt
                self.assertEqual([r[:-1] for r in one[table]], [r[:-1] for r in two[table]], table)
            else:
                self.assertEqual(one[table], two[table], table)
        self.assertEqual({r[2] for r in two['street']}, {'COLORADO SPRINGS', 'FOUNTAIN'})

    def test_force_replaces_past_a_failed_gate_backs_up_and_records_it(self):
        self.assertEqual(cli(self.stores)[0], 0)
        old = dump(self.out)
        stores = self.bad_world(self.tmp.name)
        code, out, err = cli(stores, '--force')
        self.assertEqual(code, 0, err)
        self.assertIn('forced past: abs_share_2020g', out)
        backups = list((self.dir / 'walk-backups').glob('walk-*.sqlite3'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(dump(backups[0])[0], old[0])                   # the backup holds the file that was replaced
        with closing(connect(self.out)) as db:
            qa = json.loads(db.execute("SELECT value FROM meta WHERE key='qa'").fetchone()[0])
            self.assertEqual(db.execute('SELECT count(*) FROM person').fetchone()[0], 38)
        self.assertTrue(qa['forced'])
        self.assertFalse(qa['gates']['abs_share_2020g']['pass'])
        self.only_expected_files()

    def test_a_rebuild_is_identical_and_backs_up_the_previous_file(self):
        first = build(self.stores)
        self.assertTrue(first['replaced'] and first['backup'] is None)
        one = dump(self.out)
        second = build(self.stores)
        self.assertTrue(second['replaced'])
        self.assertEqual(dump(self.out), one)                             # same rows, same ids, same keys (salt reused)
        backups = list((self.dir / 'walk-backups').glob('walk-*.sqlite3'))
        self.assertEqual([second['backup']], backups)
        self.assertEqual(dump(backups[0]), one)
        third = build(self.stores)
        self.assertEqual(len(list((self.dir / 'walk-backups').glob('walk-*.sqlite3'))), 2)    # never overwrites one
        self.assertNotEqual(third['backup'], second['backup'])
        self.only_expected_files()

    def test_a_restored_walk_salt_gives_the_same_keys_and_another_salt_gives_other_keys(self):
        salt = self.dir / '.walk-salt'

        def keys():
            with closing(connect(self.out)) as db:
                return dict(db.execute('SELECT person_id, voter_key FROM person').fetchall())
        build(self.stores)
        before = keys()
        backup = self.dir.parent / 'salt-backup'                          # outside the data directory, as a laptop backup is
        shutil.copyfile(salt, backup)
        salt.unlink()
        shutil.copyfile(backup, salt)                                     # restored
        build(self.stores)
        self.assertEqual(keys(), before)
        salt.write_text('ab' * 32, encoding='ascii')                      # any other salt re-keys everyone
        build(self.stores)
        other = keys()
        self.assertEqual(other.keys(), before.keys())
        self.assertTrue(set(other.values()).isdisjoint(before.values()))

    def test_row_count_must_match_the_contacts_store(self):
        for name, edit, message in (
                ('fewer', lambda h, rows: (h, rows[:-1]), 'Source has 37 rows, the contacts store 38'),
                ('more', lambda h, rows: (h, rows + [rows[0]]), 'more rows than the contacts store')):
            with self.subTest(name):
                stores = T.write_minimal_stores(self.tmp.name)
                rewrite_source(stores, edit)
                code, out, err = cli(stores, '--force')                  # --force does not override an integrity check
                self.assertEqual(code, 1)
                self.assertIn(message, err)
                self.assertFalse(self.out.exists())
                self.only_expected_files()

    def test_a_source_that_is_not_the_stores_source_is_refused(self):
        with self.stores.source_csv.open('a', newline='', encoding='cp1252') as stream:
            stream.write('\r\n')
        code, out, err = cli(self.stores)
        self.assertEqual(code, 1)
        self.assertIn('different source file', err)
        self.assertFalse(self.out.exists())

    def test_a_missing_column_is_named(self):
        rewrite_source(self.stores, lambda h, rows: (['gender_renamed' if c == 'sii_gender' else c for c in h], rows))
        code, out, err = cli(self.stores)
        self.assertEqual(code, 1)
        self.assertIn('Source columns missing: sii_gender', err)

    def test_a_store_held_open_for_a_moment_is_still_replaced(self):
        real, calls = os.replace, []

        def busy(src, dst, times):
            calls.append(dst)
            if len(calls) <= times:
                raise PermissionError(13, 'in use')
            return real(src, dst)
        with mock.patch.object(B, 'REPLACE_WAIT_S', 0), mock.patch.object(B.os, 'replace', lambda s, d: busy(s, d, 2)):
            self.assertTrue(build(self.stores)['replaced'])
        self.assertEqual(len(calls), 3)
        self.assertTrue(self.out.exists())
        one = dump(self.out)
        calls.clear()
        with mock.patch.object(B, 'REPLACE_WAIT_S', 0), mock.patch.object(B.os, 'replace', lambda s, d: busy(s, d, 99)):
            with self.assertRaisesRegex(B.BuildError, 'in use'):
                build(self.stores)
        self.assertEqual(len(calls), B.REPLACE_TRIES)
        self.assertEqual(dump(self.out), one)                             # the old file stays
        self.assertFalse(list(self.dir.glob('.walk-build-*')))            # the new one is not left behind

    def test_a_held_lock_stops_the_build_and_stays(self):
        lock = self.dir / 'walk.build.lock'
        lock.write_bytes(b'')
        code, out, err = cli(self.stores)
        self.assertEqual(code, 1)
        self.assertIn('walk.build.lock', err)
        self.assertTrue(lock.exists())
        self.assertFalse(self.out.exists())

    def test_unexpected_errors_print_call_sites_not_messages(self):
        broken = mock.patch.object(B, 'read_source', side_effect=ValueError('MESSAGE-WITH-A-VALUE'))
        with mock.patch.dict(os.environ), broken:
            os.environ.pop('WALK_BUILD_TRACEBACK', None)
            code, out, err = cli(self.stores)
        self.assertEqual(code, 1)
        self.assertIn('ValueError', err)
        self.assertNotIn('MESSAGE-WITH-A-VALUE', err + out)
        self.only_expected_files()

    def test_output_is_aggregates_only(self):
        code, out, err = cli(self.stores)
        self.assertEqual(code, 0, err)
        shown = (out + err).lower()
        self.assertIn('walk store written', shown)
        salt = (self.dir / '.walk-salt').read_text().strip()
        secrets = [salt]
        for want in self.stores.expected['people'].values():
            secrets += [want['name'], want['voter_id'], want['phone'], want['wireless'], want['dob'],
                        want['street_household_id'] or '']
            secrets += re.findall(r"[^\W\d_]{4,}", want['name'])
        secrets += list(self.stores.person_ids)
        secrets += [s['streetname'] for s in self.stores.expected['streets']] + ['colorado springs', 'fountain']
        for value in secrets:
            if value:
                self.assertNotIn(value.lower(), shown)


class DateFormatTests(unittest.TestCase):
    """The real file's date format is not documented: it is detected, unparseable values are counted, nothing crashes."""

    FORMATS = ('%m/%d/%Y', '%Y%m%d', '%d/%m/%Y', '%m/%d/%y', '%Y/%m/%d', '%m-%d-%Y', '%d-%b-%Y')

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.iso = T.write_minimal_stores(Path(cls.tmp.name) / 'iso')
        build(cls.iso)
        cls.reference = dump(cls.iso.directory / 'walk.sqlite3')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_other_formats_build_the_same_store(self):
        for fmt in self.FORMATS:
            with self.subTest(fmt):
                stores = T.write_minimal_stores(Path(self.tmp.name) / re.sub(r'\W', '_', fmt), date_format=fmt)
                result = build(stores)
                tables, meta, qa = dump(stores.directory / 'walk.sqlite3')
                for table in ('person', 'stop', 'street', 'combo', 'road_span'):
                    got = tables[table]
                    want = self.reference[0][table]
                    if table == 'person':                                 # voter_key follows each directory's own salt
                        got = [r[:-1] for r in got]
                        want = [r[:-1] for r in want]
                    self.assertEqual(got, want, table)
                for column, seen in result['qa']['dates'].items():
                    self.assertEqual(seen['format'], fmt, column)
                    self.assertEqual((seen['unparseable'], seen['blank']), (0, 0), column)
                self.assertEqual(set(result['qa']['dates']), {'sii_dob', 'svi_registration_date'})
                self.assertEqual({k: v for k, v in qa.items() if k != 'dates'},
                                 {k: v for k, v in self.reference[2].items() if k != 'dates'})

    def test_unparseable_and_blank_dates_are_counted_and_the_build_goes_on(self):
        stores = T.write_minimal_stores(Path(self.tmp.name) / 'junk')
        keys = stores.expected['keys']
        rows = {pid: i for i, pid in enumerate(stores.person_ids)}

        def edit(header, body):
            dob, reg = header.index('sii_dob'), header.index('svi_registration_date')
            body[rows[keys['p03']]][dob] = 'not a date'
            body[rows[keys['p08']]][dob] = '13/45/1999'
            body[rows[keys['p05']]][reg] = ''
            body[rows[keys['p07']]][reg] = '0000-00-00'
            body[rows[keys['p09']]][reg] = '1700-01-01'            # a date, but not a plausible one
            return header, body
        rewrite_source(stores, edit)
        result = build(stores)
        dates = result['qa']['dates']
        self.assertEqual((dates['sii_dob']['unparseable'], dates['sii_dob']['blank']), (2, 0))
        self.assertEqual((dates['svi_registration_date']['unparseable'], dates['svi_registration_date']['blank']), (2, 1))
        self.assertEqual(dates['sii_dob']['format'], '%Y-%m-%d')
        with closing(connect(stores.directory / 'walk.sqlite3')) as db:
            for key in ('p03', 'p08', 'p05', 'p07', 'p09'):
                row = db.execute('SELECT * FROM person WHERE person_id=?', (keys[key],)).fetchone()
                # eligible only by having voted, whichever date is missing
                self.assertEqual((row['pri_elig'], row['gen_elig']), (row['pri_voted'], row['gen_voted']), key)
                self.assertEqual(row['abs_elig'], row['pri_voted'] + row['gen_voted'], key)
            self.assertIsNone(db.execute('SELECT reg_date FROM person WHERE person_id=?', (keys['p05'],)).fetchone()[0])
            self.assertIsNone(db.execute('SELECT reg_date FROM person WHERE person_id=?', (keys['p07'],)).fetchone()[0])
            self.assertEqual(db.execute('SELECT reg_date FROM person WHERE person_id=?', (keys['p01'],)).fetchone()[0],
                             stores.expected['people'][keys['p01']]['reg_date'])

    def parse(self, *chunks):
        column = B.DateColumn('x')
        return column, [column.parse(pd.Series(chunk, dtype=str)).tolist() for chunk in chunks]

    def test_detection_on_small_columns(self):
        cases = [
            (['2020-11-03', '1999-01-31', ''], '%Y-%m-%d', [20201103, 19990131, 0]),
            (['11/03/2020', '1/5/1999'], '%m/%d/%Y', [20201103, 19990105]),
            (['25/12/1980', '03/04/1980'], '%d/%m/%Y', [19801225, 19800403]),     # a day above 12 settles day-first
            (['03/04/1980'], '%m/%d/%Y', [19800304]),                             # nothing settles it: month-first
            (['19801225', '20000101'], '%Y%m%d', [19801225, 20000101]),
            (['11/03/44', '01/02/05', '12/31/69'], '%m/%d/%y', [19441103, 20050102, 19691231]),
            (['2020-11-03 00:00:00', '2020-11-04T13:45:00', '2020-11-05'], '%Y-%m-%d', [20201103, 20201104, 20201105]),
            (['11/03/2020 3:45 PM'], '%m/%d/%Y', [20201103]),
            (['Nov 3, 2020', 'January 5, 1999'], '%b %d, %Y', [20201103, 19990105]),   # '%B' rescues the long month name
            (['3-Nov-2020'], '%d-%b-%Y', [20201103]),
            (['  2020-11-03  '], '%Y-%m-%d', [20201103]),
        ]
        for values, fmt, keys in cases:
            with self.subTest(values):
                column, [got] = self.parse(values)
                self.assertEqual(column.format, fmt)
                self.assertEqual(got, keys)

    def test_failures_are_counted_not_raised(self):
        column, [got] = self.parse(['2020-11-03', 'garbage', '0000-00-00', '2020-13-45', '1700-01-01', '2101-01-01', ''])
        self.assertEqual(got, [20201103, 0, 0, 0, 0, 0, 0])
        self.assertEqual(column.report()['unparseable'], 5)
        self.assertEqual(column.report()['blank'], 1)
        self.assertEqual(column.report()['rows'], 7)
        empty, [none] = self.parse(['', ''])
        self.assertEqual((none, empty.format, empty.report()['unparseable']), ([0, 0], None, 0))
        silly, [junk] = self.parse(['abc', 'def'])
        self.assertEqual((junk, silly.report()['unparseable']), ([0, 0], 2))
        self.assertEqual(column.report()['parsed_by_other_formats'], {})      # only formats that rescued something

    def test_a_stray_format_is_rescued_and_later_chunks_keep_the_main_format(self):
        column, (first, second) = self.parse(['2020-11-03', '11/04/2020', '1999-12-31'], ['2001-02-03', '04/05/2006'])
        self.assertEqual((first, second), ([20201103, 20201104, 19991231], [20010203, 20060405]))
        self.assertEqual(column.format, '%Y-%m-%d')
        self.assertEqual(column.report()['parsed_by_other_formats'], {'%m/%d/%Y': 2})
        self.assertEqual(column.report()['unparseable'], 0)

    def test_iso_dates_round_trip(self):
        self.assertEqual(B.iso_dates(np.array([20201103, 0, 19000101], dtype=np.int32)), ['2020-11-03', None, '1900-01-01'])


class AreaTests(unittest.TestCase):
    def test_area_points_fall_back_from_block_to_precinct_zip_and_all(self):
        # person: block, precinct, zip, household, geocode, stop (0: none). Homes A-E; stops 3-5 have no geocode.
        people = [('B1', 'P1', 'Z1', 0, 'A', 1), ('B1', 'P1', 'Z1', 1, 'B', 2), ('B3', 'P1', 'Z1', 2, 'C', 6),
                  ('B4', 'P4', 'Z1', 3, 'D', 0), ('B5', 'P5', 'Z3', 4, 'E', 0),
                  ('B2', 'P1', 'Z1', -1, None, 3),                # block without a home: its precinct
                  ('', 'P2', 'Z1', -1, None, 4),                  # no block, precinct without a home: its ZIP
                  ('', 'P3', 'Z2', -1, None, 5),                  # nothing local: every home
                  ('B1', 'P1', 'Z1', -1, None, 6), ('B1', 'P1', 'Z1', -1, None, 6)]   # stop 6: B1 twice beats B3
        homes = {'A': (-104.80, 38.85), 'B': (-104.79, 38.85), 'C': (-104.80, 38.86), 'D': (-104.78, 38.84),
                 'E': (-104.70, 38.90)}
        vocab = {k: B.Vocab() for k in ('block', 'precinct', 'zip')}
        codes = {k: vocab[k].encode(pd.Series([p[i] for p in people])) for i, k in enumerate(('block', 'precinct', 'zip'))}
        lon = np.array([homes[p[4]][0] if p[4] else np.nan for p in people])
        lat = np.array([homes[p[4]][1] if p[4] else np.nan for p in people])
        crowd = SimpleNamespace(lon=lon, lat=lat, household_code=np.array([p[3] for p in people]),
                                block=vocab['block'], block_code=codes['block'], precinct=vocab['precinct'],
                                precinct_code=codes['precinct'])
        src = SimpleNamespace(codes={'zip': codes['zip']}, vocab={'zip': vocab['zip']})
        xy, basis = B.area_points(src, crowd, SimpleNamespace(row=np.array([p[5] for p in people]), count=6))

        def centre(*names):
            x, y = G.to_metres(np.array([homes[n][0] for n in names]), np.array([homes[n][1] for n in names]))
            return [x.mean(), y.mean()]
        want = [centre('A', 'B'), centre('A', 'B'), centre('A', 'B', 'C'), centre('A', 'B', 'C', 'D'),
                centre('A', 'B', 'C', 'D', 'E'), centre('A', 'B')]
        self.assertEqual(basis, ['block', 'block', 'precinct', 'zip', 'all', 'block'])
        np.testing.assert_allclose(xy, want, atol=1e-6)


class NormaliserTests(unittest.TestCase):
    def test_city_variants_of_the_real_file_collapse(self):
        same = {'COLORADO SPRINGS': ['CO SPGS', 'COLO SPGS', 'COLO SPRINGS', 'COLORADO SPGS', 'COLORADO SPRINGS',
                                     'colorado springs ', 'Colorado Spgs.'],
                'MANITOU SPRINGS': ['MANITOU SPGS', 'MANITOU SPRINGS'],
                'GREEN MOUNTAIN FALLS': ['GRN MTN FLS', 'GREEN MOUNTAIN FALLS'],
                'FORT CARSON': ['FT CARSON', 'Fort Carson']}
        for want, variants in same.items():
            for text in variants:
                self.assertEqual(B.canonical_city(text), want, text)
        for text in ('CALHAN', 'CASCADE', 'ELBERT', 'FALCON', 'FOUNTAIN', 'LARKSPUR', 'MONUMENT', 'PALMER LAKE', 'PEYTON',
                     'PUEBLO', 'USAF ACADEMY', 'WOODLAND PARK'):
            self.assertEqual(B.canonical_city(text), text)
        self.assertEqual(B.canonical_city(''), '')
        self.assertEqual(B.canonical_city("O'Fallon"), 'OFALLON')

    def test_unit_and_house_labels(self):
        self.assertEqual([B.unit_label(t) for t in ('APT 4', '# 12B', '  ', 'BLDG 3 APT A', 'apt a-2', 'Unit', 'REAR')],
                         ['Apt 4', '# 12B', '', 'Bldg 3 Apt A', 'Apt A-2', 'Unit', 'Rear'])
        self.assertEqual([B.house_label(t) for t in (' 12a ', '1234  1/2', '')], ['12A', '1234 1/2', ''])

    def test_vote_flags(self):
        voted, mail = B.vote_flags(pd.Series(['MC', 'pc', '', ' AP ', 'zz', 'ap'], dtype=str))
        self.assertEqual(voted.tolist(), [True, True, False, True, True, True])
        self.assertEqual(mail.tolist(), [True, False, False, True, False, True])

    def test_history_edges(self):
        keys = {e: i for i, e in enumerate(B.ELECTIONS)}
        none = np.zeros((4, len(B.ELECTIONS)), dtype=bool)
        voted = none.copy()
        voted[3, keys['2014g']] = True                         # registered in 2019 but voted in 2014: eligible by voting
        mail = voted.copy()
        reg = np.array([20201103, 20201104, 0, 20190101], dtype=np.int32)
        dob = np.array([20021103, 20021103, 19800101, 19800101], dtype=np.int32)
        tally = B.history(voted, mail, reg, dob)
        # 0: registered and 18 on the 2020 general day -> that one and every later one; 1: registered a day too late
        # 2: no registration date -> nothing; 3: from 2019 on (2020p, 2020g, 2022, 2024), plus the 2014 general by voting
        self.assertEqual(tally['gen_elig'].tolist(), [3, 2, 0, 4])
        self.assertEqual(tally['pri_elig'].tolist(), [2, 2, 0, 3])
        self.assertEqual(tally['gen_voted'].tolist(), [0, 0, 0, 1])
        self.assertEqual(tally['abs_voted'].tolist(), [0, 0, 0, 1])
        self.assertEqual(tally['abs_elig'].tolist(), (tally['pri_elig'] + tally['gen_elig']).tolist())
        late = B.history(none[:1], none[:1], np.array([20100101], dtype=np.int32), np.array([20021104], dtype=np.int32))
        self.assertEqual((late['gen_elig'][0], late['pri_elig'][0]), (2, 2))    # turns 18 the day after the 2020 general


if __name__ == '__main__':
    unittest.main()
