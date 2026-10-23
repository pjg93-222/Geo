"""Audience filter (POST /api/v3/walk/audience/streets, GET /api/v3/walk/audience/options) on walk_testkit's invented world,
with an invented support table and invented canvass results. Nothing here was read from a real record.

The area A below is x -320..0 m, y -50..50 m of the testkit origin: Cedar Heights Dr only, its first block whole and the
first part of its second block (the rest of the street lies east of x = 0). Doors in A and their people (party, support):
  d01 p01 R 10, p02 R 20          d02 p03 D 50, p04 D none, p05 U 60 (two households, one door)
  d04 p07 D 100/3                 d05 p08 U 200/3            d06 p09 D 90, p10 D 80, p11 U none
  d07 p12 R 0                     d08 p13 D 100, p14 D 70    d09 p15 D 66                d10 p16 L 34
d03 (p06 U, no support row) has its map position removed here, so it is the one unplaced door on the street.
"""
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import resolve

from tools import build_walk as B
from tools import districts as D
from tools import field_labels as FL

from . import audience_views as AV
from . import walk_store as W
from . import walk_testkit as T
from .models import Disposition, WalkList

OPTIONS, STREETS = '/api/v3/walk/audience/options', '/api/v3/walk/audience/streets'
CANDIDATE = {**D.get('co-05'), 'candidate': 'Test Candidate', 'party': 'D', 'race_mode': 'general'}
SUPPORT_DDL = ('CREATE TABLE support (pos INTEGER PRIMARY KEY, sup_score REAL NOT NULL, sup_pct REAL NOT NULL, '
               'sup_rank INTEGER NOT NULL, sup_tie_n INTEGER NOT NULL, in_universe INTEGER NOT NULL)')
SUPPORT = {'p01': 10, 'p02': 20, 'p03': 50, 'p05': 60, 'p07': 100 / 3, 'p08': 200 / 3, 'p09': 90, 'p10': 80, 'p12': 0,
           'p13': 100, 'p14': 70, 'p15': 66, 'p16': 34}
THIRD, TWO_THIRDS = 100 / 3, 200 / 3
EVERYONE = {'low': 0, 'high': 100, 'include_no_score': True}
NONE = {'party': [], 'canvass': []}
STREET_KEYS = {'id', 'label', 'center', 'matching_doors', 'voter_doors', 'dropped_by_exclude'}


def area(x0, y0, x1, y1):
    return [list(T.lonlat(x0, y0)), list(T.lonlat(x1, y0)), list(T.lonlat(x1, y1)), list(T.lonlat(x0, y1))]


AREA_A = area(-320, -50, 0, 50)
FIRST_BLOCK = area(-320, -50, -110, 50)


@override_settings(LOCAL_ADMIN_ACCESS=True, HOSTED=False, DEMO_MODE=False, DISTRICT=CANDIDATE)
class AudienceTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.stores = T.write_minimal_stores(Path(cls.temporary.name) / 'world')
        if not B.build(cls.stores.source_csv, cls.stores.directory, force=True)['replaced']:
            raise AssertionError('The synthetic walk store was not written.')
        cls.keys = cls.stores.expected['keys']
        with closing(sqlite3.connect(cls.stores.directory / 'scores.sqlite3')) as db, db:
            db.execute(SUPPORT_DDL)
            for key, score in SUPPORT.items():
                pos = db.execute('SELECT pos FROM person WHERE person_id = ?', (cls.keys[key],)).fetchone()[0]
                db.execute('INSERT INTO support VALUES (?, ?, ?, 1, 1, 1)', (pos, score, score))
        with closing(sqlite3.connect(cls.stores.directory / 'walk.sqlite3')) as db, db:
            db.execute('UPDATE stop SET lon = NULL, lat = NULL WHERE id = (SELECT stop_id FROM person WHERE person_id = ?)',
                       (cls.keys['p06'],))
            cls.voter_key = {k: db.execute('SELECT voter_key FROM person WHERE person_id = ?', (p,)).fetchone()[0]
                             for k, p in cls.keys.items()}
            cls.party = {k: db.execute("SELECT IFNULL(NULLIF(party, ''), 'none') FROM person WHERE person_id = ?", (p,)).fetchone()[0]
                         for k, p in cls.keys.items()}
        cls.override = override_settings(DATA_DIR=cls.stores.directory)
        cls.override.enable()
        W.reset()

    @classmethod
    def tearDownClass(cls):
        W.reset()
        cls.override.disable()
        cls.temporary.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.client = Client(HTTP_HOST='localhost')
        self.lists = {}

    # ---- helpers ---------------------------------------------------------------------------------------------
    def post(self, body, status=200):
        response = self.client.post(STREETS, data=json.dumps(body), content_type='application/json')
        self.assertEqual(response.status_code, status, response.content[:300])
        return json.loads(response.content)

    def streets(self, polygon=AREA_A, include=NONE, exclude=NONE, support=EVERYONE):
        return self.post({'polygon': polygon, 'include': include, 'exclude': exclude, 'support': support})

    def cedar(self, payload):
        (street,) = [s for s in payload['streets'] if s['label'] == 'Cedar Heights Dr']
        return street

    def result(self, list_name, key, code):
        if list_name not in self.lists:
            self.lists[list_name] = WalkList.objects.create(key=list_name, title=list_name, payload={}, source_sha256='0' * 64)
        Disposition.objects.create(walk_list=self.lists[list_name], person_id=self.keys[key], voter_key=self.voter_key[key],
                                   code=code, contacted_on='2026-10-01')

    def canvass(self):
        """p01 Supporter; p09 Not home on one list, then Supporter on another; p12 Opposed on list a, Undecided on list
        b, then list a cleared: Undecided stands; p02 and p16 (alone at d10) Moved."""
        self.result('a', 'p01', 'S')
        self.result('a', 'p09', 'NH')
        self.result('b', 'p09', 'S')
        self.result('a', 'p12', 'O')
        self.result('b', 'p12', 'U')
        self.result('a', 'p12', None)
        self.result('a', 'p02', 'MV')
        self.result('b', 'p16', 'MV')

    # ---- doors ---------------------------------------------------------------------------------------------------
    def test_routes(self):
        self.assertEqual(resolve(OPTIONS).func, AV.options)
        self.assertEqual(resolve(STREETS).func, AV.streets)

    def test_everyone_counts_each_voter_door_in_the_area_once(self):
        payload = self.streets()
        self.assertEqual([s['label'] for s in payload['streets']], ['Cedar Heights Dr'])
        street = self.cedar(payload)
        self.assertEqual((street['matching_doors'], street['voter_doors'], street['dropped_by_exclude']), (9, 9, 0))
        self.assertEqual(payload['totals'], {'matching_doors': 9, 'voter_doors': 9, 'dropped_by_exclude': 0, 'unplaced': 1})
        self.assertEqual(payload['totals']['unplaced'], 1)          # d03, whose stop has no map position
        self.assertEqual((payload['basis'], payload['support_coverage']), ('voter-file', True))

    def test_the_polygon_clips_at_the_stop(self):
        self.assertEqual(self.cedar(self.streets(FIRST_BLOCK))['voter_doors'], 6)       # d01 d02 d04-d07
        self.assertEqual(self.cedar(self.streets(area(-320, -50, 400, 50)))['voter_doors'], 15)
        lon, lat = self.cedar(self.streets(FIRST_BLOCK))['center']
        self.assertTrue(T.lonlat(-300, 0)[0] < lon < T.lonlat(-100, 0)[0] and abs(lat - T.ORIGIN[1]) < 0.001)
        self.assertEqual(self.streets(area(-1000, 1000, -900, 1100))['streets'], [])

    def test_include_a_party(self):
        street = self.cedar(self.streets(include={'party': ['D'], 'canvass': []}))
        self.assertEqual((street['matching_doors'], street['dropped_by_exclude']), (5, 0))     # d02 d04 d06 d08 d09
        self.assertEqual(self.streets(include={'party': ['D'], 'canvass': []})['totals']['unplaced'], 0)
        both = self.cedar(self.streets(include={'party': ['L', 'R'], 'canvass': []}))
        self.assertEqual(both['matching_doors'], 3)                                            # d01 d07 d10

    def test_exclude_wins_at_a_mixed_door(self):
        street = self.cedar(self.streets(include={'party': ['D'], 'canvass': []}, exclude={'party': ['U'], 'canvass': []}))
        # d02 (p05 U) and d06 (p11 U) have a Democrat but an Unaffiliated resident too: both are dropped
        self.assertEqual((street['matching_doors'], street['dropped_by_exclude'], street['voter_doors']), (3, 2, 9))
        only = self.cedar(self.streets(exclude={'party': ['U'], 'canvass': []}))
        self.assertEqual((only['matching_doors'], only['dropped_by_exclude']), (6, 2))         # d02 d05 d06 gone; d05 is not mixed

    def test_support_range_edges_are_inclusive(self):
        persuadable = {'low': THIRD, 'high': TWO_THIRDS, 'include_no_score': False}
        self.assertEqual(self.cedar(self.streets(support=persuadable))['matching_doors'], 5)   # d02 d04 d05 d09 d10
        for low, high, doors in ((THIRD, THIRD, 1), (TWO_THIRDS, TWO_THIRDS, 1), (0, 0, 1), (100, 100, 1), (34, 34, 1),
                                 (33, 33, 0), (0, THIRD, 3)):
            with self.subTest(low=low, high=high):
                payload = self.streets(support={'low': low, 'high': high, 'include_no_score': False})
                self.assertEqual(sum(s['matching_doors'] for s in payload['streets']), doors)

    def test_the_no_score_checkbox(self):
        on = {'low': THIRD, 'high': TWO_THIRDS, 'include_no_score': True}
        payload = self.streets(support=on)
        self.assertEqual(self.cedar(payload)['matching_doors'], 6)          # p11 adds d06; p04 is at d02 already
        self.assertEqual(payload['totals']['unplaced'], 1)             # p06 has no score
        none_only = {'low': 50, 'high': 50, 'include_no_score': True}
        self.assertEqual(self.cedar(self.streets(support=none_only))['matching_doors'], 2)   # d02 (p03 50, p04) and d06 (p11)

    def test_a_district_without_support_rows_has_no_scores(self):
        with mock.patch.object(W.Store, 'attach_scores', lambda store: False):
            payload = self.streets(support={'low': 0, 'high': 100, 'include_no_score': False})
            self.assertEqual((payload['totals']['matching_doors'], payload['support_coverage']), (0, False))
            self.assertEqual(self.streets()['totals']['matching_doors'], 9)
            self.assertFalse(json.loads(self.client.get(OPTIONS).content)['support_coverage'])

    def test_canvass_newest_standing_result_and_moved(self):
        self.canvass()
        street = self.cedar(self.streets())
        self.assertEqual(street['voter_doors'], 8)               # d10 held only p16, who moved
        supporters = self.cedar(self.streets(include={'party': [], 'canvass': ['S']}))
        self.assertEqual(supporters['matching_doors'], 2)                   # p01 at d01, p09 at d06
        undecided = self.cedar(self.streets(include={'party': [], 'canvass': ['U']}))
        self.assertEqual(undecided['matching_doors'], 1)                    # p12: list a was cleared, list b stands
        self.assertEqual(self.cedar(self.streets(include={'party': [], 'canvass': ['O', 'NH']}))['matching_doors'], 0)
        fresh = self.cedar(self.streets(include={'party': [], 'canvass': ['none']}))
        self.assertEqual(fresh['matching_doors'], 6)                        # d02 d04 d05 d06 d08 d09
        alone = self.cedar(self.streets(include={'party': ['R'], 'canvass': []}, exclude={'party': [], 'canvass': ['S']}))
        self.assertEqual((alone['matching_doors'], alone['dropped_by_exclude']), (1, 0))      # d07; d01 has p01 alone (p02 moved)
        mixed = self.cedar(self.streets(include={'party': ['D'], 'canvass': []}, exclude={'party': [], 'canvass': ['S']}))
        self.assertEqual((mixed['matching_doors'], mixed['dropped_by_exclude']), (4, 1))      # d06: p09 Supporter, p10 still in

    def test_options_count_the_district_without_movers(self):
        self.canvass()
        payload = json.loads(self.client.get(OPTIONS).content)
        moved = {'p02', 'p16'}
        parties = {}
        for key, code in self.party.items():
            if key not in moved:
                parties[code] = parties.get(code, 0) + 1
        self.assertEqual({row['code']: row['count'] for row in payload['party']},
                         {code: parties.get(code, 0) for code in FL.AUDIENCE_PARTY_LABELS})
        self.assertEqual({row['code']: row['count'] for row in payload['canvass']},
                         {'S': 2, 'U': 1, 'O': 0, 'NH': 0, 'none': len(self.keys) - 2 - 3})
        self.assertEqual(set(payload), {'district', 'candidate_party', 'support_coverage', 'party', 'canvass'})
        self.assertEqual((payload['district'], sum(r['count'] for r in payload['party'])), ('co-05', len(self.keys) - 2))
        self.assertEqual((payload['support_coverage'], payload['candidate_party']), (True, 'D'))
        self.assertEqual([r['label'] for r in payload['party'] if r['code'] in ('G', 'T', 'none')],
                         ['Registered Green', 'Party code T', 'No party code'])
        with override_settings(DISTRICT={**CANDIDATE, 'candidate': None}):
            self.assertIsNone(json.loads(self.client.get(OPTIONS).content)['candidate_party'])
        self.assertEqual(self.client.get(OPTIONS + '?x=1').status_code, 400)

    def test_no_person_data_leaves_the_server(self):
        self.canvass()
        text = self.client.post(STREETS, data=json.dumps({'polygon': area(-400, -300, 400, 300), 'include': NONE,
                                                          'exclude': NONE, 'support': EVERYONE}),
                                content_type='application/json').content.decode('utf-8')
        payload = json.loads(text)
        self.assertEqual(set(payload), {'basis', 'streets', 'totals', 'support_coverage'})
        self.assertTrue(payload['streets'] and all(set(s) == STREET_KEYS for s in payload['streets']))
        for key in self.keys:
            self.assertNotIn(self.keys[key], text)
            self.assertNotIn(self.voter_key[key], text)
        options = self.client.get(OPTIONS).content.decode('utf-8')
        self.assertTrue(all(self.voter_key[k] not in options and self.keys[k] not in options for k in self.keys))

    # ---- refusals ----------------------------------------------------------------------------------------------
    def test_only_whitelisted_values_are_taken(self):
        good = {'polygon': AREA_A, 'include': NONE, 'exclude': NONE, 'support': EVERYONE}
        bad = [
            {**good, 'extra': 1},
            {k: v for k, v in good.items() if k != 'support'},
            {**good, 'include': {'party': ['X'], 'canvass': []}},
            {**good, 'include': {'party': ['D', 'D'], 'canvass': []}},
            {**good, 'include': {'party': 'D', 'canvass': []}},
            {**good, 'include': {'party': [{'D': 1}], 'canvass': []}},
            {**good, 'exclude': {'party': [], 'canvass': ['MV']}},
            {**good, 'exclude': {'party': [], 'canvass': [], 'support': []}},
            {**good, 'exclude': {'party': ["D' OR 1=1 --"], 'canvass': []}},
            {**good, 'support': {'low': 60, 'high': 40, 'include_no_score': True}},
            {**good, 'support': {'low': -1, 'high': 40, 'include_no_score': True}},
            {**good, 'support': {'low': 0, 'high': 101, 'include_no_score': True}},
            {**good, 'support': {'low': True, 'high': 40, 'include_no_score': True}},
            {**good, 'support': {'low': '0', 'high': 40, 'include_no_score': True}},
            {**good, 'support': {'low': 0, 'high': 40, 'include_no_score': 1}},
            {**good, 'support': {'low': 0, 'high': 40}},
            {**good, 'polygon': [[0, 0], [1, 1]]},
            {**good, 'polygon': [[-104.8, 38.85], [-104.7, 38.95], [-104.8, 38.95], [-104.7, 38.85]]},
            [],
        ]
        for body in bad:
            with self.subTest(body=body):
                self.post(body, 400)
        for raw in (b'{"polygon": NaN}', b'{"a": 1, "a": 2}', b'not json'):
            with self.subTest(raw=raw):
                self.assertEqual(self.client.post(STREETS, data=raw, content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post(STREETS + '?x=1', data=json.dumps(good), content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post(STREETS, data=json.dumps(good), content_type='text/plain').status_code, 400)
        self.assertEqual(self.client.get(STREETS).status_code, 405)

    def test_local_administrator_only_and_not_in_demo_mode(self):
        factory, body = RequestFactory(), json.dumps({'polygon': AREA_A, 'include': NONE, 'exclude': NONE, 'support': EVERYONE})
        for change in ('remote', 'anonymous', 'proxy', 'not_owner', 'hosted', 'demo'):
            for view, request in ((AV.streets, factory.post(STREETS, data=body, content_type='application/json', REMOTE_ADDR='127.0.0.1')),
                                  (AV.options, factory.get(OPTIONS, REMOTE_ADDR='127.0.0.1'))):
                request.user = SimpleNamespace(is_authenticated=True)
                request.local_admin_access = True
                if change == 'remote':
                    request.META['REMOTE_ADDR'] = '203.0.113.1'
                elif change == 'anonymous':
                    request.user.is_authenticated = False
                elif change == 'proxy':
                    request.META['HTTP_X_FORWARDED_FOR'] = '127.0.0.1'
                elif change == 'not_owner':
                    request.local_admin_access = False
                with self.subTest(change=change, view=view.__name__), \
                        override_settings(HOSTED=change == 'hosted', DEMO_MODE=change == 'demo', DESKTOP_BOUNDARY=False), \
                        mock.patch.object(W, 'walk_store', side_effect=AssertionError('must not open')):
                    self.assertEqual(view(request).status_code, 403)
        self.assertEqual(self.client.get(OPTIONS + '?demo=1').status_code, 403)
