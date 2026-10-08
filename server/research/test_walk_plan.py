"""Street plan (GET /api/v3/walk/plan), the road-span pick of the walk list (spans=) and the printed scores (POST
/api/v3/walk/scores): hand-computed presets, the 5-door and 50 m rules, the weights, refusals, and the pick feeding a walk
list. The world is walk_testkit's invented neighbourhood with invented scores and a support table added to its score store.

Hand-checked scores of the three blocks that matter (s = support score / 100, p = participation / 100):

  Cedar Heights Dr, first block (chd1, span 1, 200 m, 7 doors, 12 people)
    p01-p04  s 0.5, p 0.8 0.6 0.4 0.2    mixed (1 - |2s - 1|) p = 0.8 + 0.6 + 0.4 + 0.2 = 2.0     turnout s (1 - p) = 0.5 x 2.0 = 1.0
    p05-p08  s 1.0, p 0.5                mixed 0                                                    turnout 4 x 0.5 = 2.0
    p09-p12  s 0.0, p 0.9                mixed 0                                                    turnout 0
    mixed 2.0   turnout 3.0   identify 4 (the four with s 0.5)   mean s 0.5   mean p 0.6333
  Cedar Heights Dr, second block (chd2, span 6, 200 m, 6 doors, 8 people)
    p13-p15  s 0.4, p 0.5   mixed 0.8 x 0.5 = 0.4 each (1.2)     turnout 0.4 x 0.5 = 0.2 each (0.6)
    p16-p17  s 0.2, p 1.0   mixed 0.4 x 1.0 = 0.4 each (0.8)     turnout 0
    p18      s 2/3, p 0.5   mixed 2/3 x 0.5 = 1/3                 turnout 2/3 x 0.5 = 1/3      (2/3 is inside the middle third)
    p19-p20  no support score, p 0.5   in nothing but identify
    mixed 2.3333   turnout 0.9333   identify 6 (three at 0.4, the 2/3, the two with none)   mean s 0.3778   mean p 0.625
  Ridgeview Ct (rdg, span 7, 100 m, 4 doors, 5 people): only ranked when the door floor is lowered to 4
    p27 s 1/3 (inside), p28 s 0.329 (outside), p29 s 0.67 (outside), p30 s 0.5 (inside), p31 none    identify 3

Everyone else has s 0.5, p 0.5. The blocks are 200 m, 200 m and 100 m long, so value per 100 m is half the value.

Needs a push (Paul 2026-10-08) per person = (1 - |2s - 1|) p + s (1 - p) with s = support STANDING / 100 (only p01 has a standing, 62.5,
that is not its score, 50) and p as above:
  chd1  p01 s 0.625 p 0.8: 0.75 x 0.8 + 0.625 x 0.2 = 0.725     p02 0.6 + 0.5 x 0.4 = 0.8     p03 0.4 + 0.5 x 0.6 = 0.7
        p04 0.2 + 0.5 x 0.8 = 0.6    p05-p08 (s 1, p 0.5) 0 + 0.5 = 0.5 each = 2.0    p09-p12 (s 0, a sure opponent) 0
        push 0.725 + 0.8 + 0.7 + 0.6 + 2.0 = 4.825, per 100 m 2.4125
  chd2  p13-p15 (s 0.4, p 0.5) 0.8 x 0.5 + 0.4 x 0.5 = 0.6 each = 1.8    p16-p17 (s 0.2, p 1) 0.4 each = 0.8    p18 (s 2/3, p 0.5) 1/3 + 1/3
        p19-p20 have no support: nothing.   push 1.8 + 0.8 + 2/3 = 3.2667, per 100 m 1.6333  (so push puts chd1 first; mixed puts chd2 first)
"""
import copy
import hashlib
import json
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

import numpy as np
from django.test import Client, SimpleTestCase, TestCase, override_settings

from tools import build_walk as B
from tools import districts as D
from tools import field_labels as FL

from . import walk_store as W
from . import walk_testkit as T
from . import walk_views as V
from .terrier_contacts import SourceStoreError

LOOPBACK = dict(HTTP_HOST='localhost')
JSON = 'application/json'
CO, OR = D.get('co-05'), D.get('or-05')
ROI_PUSH = copy.deepcopy(OR['roi'])                                                       # Oregon's street plan: Needs a push by default
CANDIDATE = {**CO, 'candidate': 'Test Candidate', 'party': 'D', 'race_mode': 'general',    # the Colorado-shaped world with a candidate
             'support': copy.deepcopy(OR['support']),
             'roi': {**ROI_PUSH, 'preset': 'mixed'}}      # most tests below name no preset and were written for the older default
SUPPORT_DDL = ('CREATE TABLE support (pos INTEGER PRIMARY KEY, sup_score REAL NOT NULL, sup_pct REAL NOT NULL, '
               'sup_rank INTEGER NOT NULL, sup_tie_n INTEGER NOT NULL, in_universe INTEGER NOT NULL)')
PLAIN = (50.0, 50.0)                      # (support score, participation) of everyone not listed
SCORES = {                                # person key -> (support score or None, participation)
    **{k: (50.0, p) for k, p in zip(('p01', 'p02', 'p03', 'p04'), (80.0, 60.0, 40.0, 20.0))},
    **dict.fromkeys(('p05', 'p06', 'p07', 'p08'), (100.0, 50.0)),
    **dict.fromkeys(('p09', 'p10', 'p11', 'p12'), (0.0, 90.0)),
    **dict.fromkeys(('p13', 'p14', 'p15'), (40.0, 50.0)),
    **dict.fromkeys(('p16', 'p17'), (20.0, 100.0)),
    'p18': (200 / 3.0, 50.0), 'p19': (None, 50.0), 'p20': (None, 50.0),
    'p27': (100 / 3.0, 50.0), 'p28': (32.9, 50.0), 'p29': (67.0, 50.0), 'p30': (50.0, 50.0), 'p31': (None, 50.0),
}
OVR = {'p01': 71.5, 'p02': 40.4}          # overall standings that exercise the rounding (half goes up)
SUP_PCT = {'p01': 62.5}                   # a support standing that is not the support score
PRECINCT_W = '5200021301'


def lowered(**roi):
    """The candidate district with some street-plan settings changed."""
    return {**CANDIDATE, 'roi': {**CANDIDATE['roi'], **roi}}


def add_scores(stores):
    """Give the testkit's score store the columns and the support table the street plan reads."""
    census = {r['person_id']: r['csv']['svi_registration_censusid'] for r in stores.case.rows}
    with closing(sqlite3.connect(stores.directory / 'scores.sqlite3')) as db, db:
        for column in ('ovr_pct REAL', 'part_pct REAL', 'reg_block TEXT'):
            db.execute('ALTER TABLE person ADD COLUMN ' + column)
        db.execute(SUPPORT_DDL)
        for key, person in stores.expected['keys'].items():
            pos = db.execute('SELECT pos FROM person WHERE person_id = ?', (person,)).fetchone()[0]
            sup, part = SCORES.get(key, PLAIN)
            db.execute('UPDATE person SET ovr_pct = ?, part_pct = ?, reg_block = ? WHERE pos = ?',
                       (OVR.get(key, 50.0), part, census[person], pos))
            if sup is not None:
                db.execute('INSERT INTO support VALUES (?, ?, ?, 1, 1, 1)', (pos, sup, SUP_PCT.get(key, sup)))


@override_settings(LOCAL_ADMIN_ACCESS=True, HOSTED=False, DEMO_MODE=False, DISTRICT=CANDIDATE)
class WalkPlanTests(TestCase):
    """The synthetic walk world with scores and support, built once; every test reads it through the API."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.stores = T.write_minimal_stores(Path(cls.temporary.name) / 'world')
        if not B.build(cls.stores.source_csv, cls.stores.directory, force=True)['replaced']:
            raise AssertionError('The synthetic walk store was not written.')
        add_scores(cls.stores)                  # after the build, so the walk store is the testkit's own
        cls.expected = cls.stores.expected
        census = {r['person_id']: r['csv']['svi_registration_censusid'] for r in cls.stores.case.rows}
        cls.block_id = {b['id']: census[cls.expected['doors'][b['doors'][0]]['people'][0]] for b in cls.expected['blocks'] if b['doors']}
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
        self.client = Client(**LOOPBACK)

    # ---- helpers ---------------------------------------------------------------------------------------------
    def get(self, url, status=200):
        response = self.client.get(url)
        self.assertEqual(response.status_code, status, response.content[:300])
        return json.loads(response.content)

    def plan(self, status=200, **query):
        query = {'ss': '010', **query}
        return self.get('/api/v3/walk/plan?' + urlencode({k: v for k, v in query.items() if v is not None}), status)

    def list(self, status=200, **query):
        return self.get('/api/v3/walk/list?' + urlencode(query), status)

    def post(self, url, data, status=200):
        response = self.client.post(url, data=json.dumps(data), content_type=JSON)
        self.assertEqual(response.status_code, status, response.content[:300])
        return json.loads(response.content)

    def by_span(self, plan):
        return {row['span']: row for row in plan['spans']}

    def people_in(self, *blocks, precinct=None):
        doors = [d for b in self.expected['blocks'] if b['id'] in blocks for d in b['doors']]
        return {p for d in doors for p in self.expected['doors'][d]['people']
                if precinct in (None, self.expected['doors'][d]['precinct'])}

    def voters(self, payload):
        return {v['person_id'] for run in payload['runs'] for door in run['doors'] for v in door['voters']}

    def ids(self, *keys):
        return [self.expected['keys'][k] for k in keys]

    def cedar(self):
        return self.get('/api/v3/walk/streets?q=cedar')['streets'][0]['id']

    def world_without_support(self, name):
        copy_dir = Path(self.temporary.name) / name
        shutil.copytree(self.stores.directory, copy_dir)
        with closing(sqlite3.connect(copy_dir / 'scores.sqlite3')) as db, db:
            db.execute('DROP TABLE support')
        return copy_dir

    def write_density(self, **pop_density):
        """density.json with the block level for the named blocks (chd1=900.0, ...); removed after the test."""
        block = {'ids': [self.block_id[name] for name in pop_density], 'pop_density': list(pop_density.values())}
        path = self.stores.directory / 'density.json'
        path.write_text(json.dumps({'schema': 'density-1', 'levels': {'block': block}}), encoding='utf-8')
        self.addCleanup(path.unlink)

    # ---- the world -------------------------------------------------------------------------------------------
    def test_the_world_has_the_spans_the_hand_checks_name(self):
        with closing(sqlite3.connect(self.stores.directory / 'walk.sqlite3')) as db:
            for name, span in {'chd1': 1, 'chd2': 6, 'rdg': 7}.items():
                spans = {db.execute('SELECT s.span_id FROM person p JOIN stop s ON s.id = p.stop_id WHERE p.person_id = ?',
                                    (p,)).fetchone()[0] for p in self.people_in(name)}
                self.assertEqual(spans, {span}, name)
            self.assertEqual(db.execute('SELECT len_m FROM road_span WHERE id IN (1, 6, 7) ORDER BY id').fetchall(),
                             [(200.0,), (200.0,), (100.0,)])

    # ---- the street plan -------------------------------------------------------------------------------------
    def test_mixed_support_streets_by_hand(self):
        plan = self.plan()
        self.assertEqual({k: plan[k] for k in ('schema', 'preset', 'p', 'race_mode')},
                         {'schema': 'walk-plan-1', 'preset': 'mixed', 'p': 'participation', 'race_mode': 'general'})
        self.assertEqual(plan['weights'], {'value': 1.0, 'pop_density': 0.0, 'voter_density': 0.0})
        self.assertEqual(plan['spans'], [
            {'span': 6, 'street': 'Cedar Heights Dr', 'doors': 6, 'people': 8, 'len_m': 200.0, 's_mean': 0.3778, 'p_mean': 0.625,
             'pop_density': None, 'voter_density': 4.0, 'value': 2.3333, 'value_per_100m': 1.1667, 'score': 0.75, 'rank': 1},
            {'span': 1, 'street': 'Cedar Heights Dr', 'doors': 7, 'people': 12, 'len_m': 200.0, 's_mean': 0.5, 'p_mean': 0.6333,
             'pop_density': None, 'voter_density': 6.0, 'value': 2.0, 'value_per_100m': 1.0, 'score': 0.25, 'rank': 2}])

    def test_needs_a_push_by_hand_and_is_the_default_of_a_real_district(self):
        with override_settings(DISTRICT={**CANDIDATE, 'roi': ROI_PUSH}):
            plan = self.plan()
        self.assertEqual({k: plan[k] for k in ('schema', 'preset')}, {'schema': 'walk-plan-1', 'preset': 'push'})
        self.assertEqual(plan['spans'], [
            {'span': 1, 'street': 'Cedar Heights Dr', 'doors': 7, 'people': 12, 'len_m': 200.0, 's_mean': 0.5, 'p_mean': 0.6333,
             'pop_density': None, 'voter_density': 6.0, 'value': 4.825, 'value_per_100m': 2.4125, 'score': 0.75, 'rank': 1},
            {'span': 6, 'street': 'Cedar Heights Dr', 'doors': 6, 'people': 8, 'len_m': 200.0, 's_mean': 0.3778, 'p_mean': 0.625,
             'pop_density': None, 'voter_density': 4.0, 'value': 3.2667, 'value_per_100m': 1.6333, 'score': 0.25, 'rank': 2}])
        self.assertEqual(plan, self.plan(preset='push'))

    def test_push_ranks_the_streets_differently_from_the_alternatives_it_leaves_in_place(self):
        self.assertEqual([r['span'] for r in self.plan(preset='push')['spans']], [1, 6])
        self.assertEqual([r['span'] for r in self.plan(preset='mixed')['spans']], [6, 1])
        self.assertEqual([r['span'] for r in self.plan(preset='turnout')['spans']], [1, 6])
        self.assertEqual([r['span'] for r in self.plan(preset='identify')['spans']], [6, 1])
        self.assertEqual(ROI_PUSH['presets'], ('push', 'mixed', 'turnout', 'identify'))

    def test_push_follows_the_same_span_rules_and_weights(self):
        with override_settings(DISTRICT=lowered(min_doors=4)):
            self.assertEqual(sorted(self.by_span(self.plan(preset='push'))), [1, 6, 7])
        with override_settings(DISTRICT=lowered(min_length_m=250)):
            rows = self.by_span(self.plan(preset='push'))
        self.assertEqual((rows[1]['len_m'], rows[1]['value_per_100m'], rows[6]['value_per_100m']), (250.0, 1.93, 1.3067))
        self.write_density(chd1=900.0, chd2=100.0)
        self.assertEqual([(r['span'], r['score']) for r in self.plan(preset='push', w_value='0', w_pop='1', w_voter='0')['spans']],
                         [(1, 0.75), (6, 0.25)])
        self.assertEqual(self.plan(preset='push', w_value='1', w_pop='1')['weights']['pop_density'], 0.5)

    def test_supporter_turnout_by_hand(self):
        plan = self.plan(preset='turnout')
        rows = self.by_span(plan)
        self.assertEqual([r['span'] for r in plan['spans']], [1, 6])
        self.assertEqual((rows[1]['value'], rows[1]['value_per_100m'], rows[1]['rank']), (3.0, 1.5, 1))
        self.assertEqual((rows[6]['value'], rows[6]['value_per_100m'], rows[6]['rank']), (0.9333, 0.4667, 2))

    def test_voter_identification_counts_the_middle_third_and_the_missing(self):
        plan = self.plan(preset='identify')
        rows = self.by_span(plan)
        self.assertEqual([r['span'] for r in plan['spans']], [6, 1])
        self.assertEqual((rows[1]['value'], rows[1]['value_per_100m']), (4, 2.0))      # the four at 0.5; 0 and 1 are outside
        self.assertEqual((rows[6]['value'], rows[6]['value_per_100m']), (6, 3.0))      # 0.4 x 3, 2/3 (inclusive), two with none

    def test_the_lower_edge_of_the_middle_third_is_inside_and_a_hair_either_side_is_not(self):
        with override_settings(DISTRICT=lowered(min_doors=4)):
            rdg = self.by_span(self.plan(preset='identify'))[7]
        self.assertEqual((rdg['doors'], rdg['people'], rdg['value']), (4, 5, 3))       # 1/3 in, 0.329 out, 0.67 out, 0.5 in, none in

    def test_a_block_with_fewer_than_five_doors_is_not_ranked(self):
        plan = self.plan()
        self.assertEqual(sorted(self.by_span(plan)), [1, 6])
        # not ranked: chd3 3 doors / 3 people, Juniper Ln 2/2 and 1/1, Ridgeview Ct 4/5, Pine St 2/3
        self.assertEqual(plan['not_ranked'], {'spans': 5, 'people': 14})
        self.assertEqual(plan['matched'], {'people': 34, 'scored': 34})
        with override_settings(DISTRICT=lowered(min_doors=4)):
            lower = self.plan()
        self.assertEqual((sorted(self.by_span(lower)), lower['not_ranked']), ([1, 6, 7], {'spans': 4, 'people': 9}))

    def test_an_area_counts_only_its_own_doors(self):
        plan = self.plan(ss=None, precinct=PRECINCT_W)           # the second Cedar block splits over two precincts: 3 doors here
        self.assertEqual([(r['span'], r['doors'], r['people']) for r in plan['spans']], [(1, 7, 12)])

    def test_a_short_block_counts_as_fifty_metres_and_the_floor_can_be_raised(self):
        with override_settings(DISTRICT=lowered(min_length_m=250)):
            rows = self.by_span(self.plan())
        self.assertEqual((rows[6]['len_m'], rows[6]['value_per_100m'], rows[6]['voter_density']), (250.0, 0.9333, 3.2))
        self.assertEqual((rows[1]['len_m'], rows[1]['value_per_100m']), (250.0, 0.8))

    def test_the_weights_blend_the_standings_of_value_population_and_voters(self):
        self.write_density(chd1=900.0, chd2=100.0)
        order = lambda plan: [(r['span'], r['score']) for r in plan['spans']]
        # two ranked blocks stand at 0.25 and 0.75 on each measure
        self.assertEqual(order(self.plan(w_value='1')), [(6, 0.75), (1, 0.25)])
        self.assertEqual(order(self.plan(w_value='0', w_pop='1', w_voter='0')), [(1, 0.75), (6, 0.25)])
        self.assertEqual(self.by_span(self.plan(w_pop='1'))[1]['pop_density'], 900.0)
        self.assertEqual(order(self.plan(w_voter='3')), [(1, 0.75), (6, 0.25)])          # people per 100 m: 6 and 4
        self.assertEqual(self.plan(w_pop='3', w_voter='1')['weights'], {'value': 0.0, 'pop_density': 0.75, 'voter_density': 0.25})
        # an equal blend of value and population density leaves both at 0.5: the higher value per 100 m comes first
        self.assertEqual(order(self.plan(w_value='2', w_pop='2')), [(6, 0.5), (1, 0.5)])
        self.assertEqual(self.plan(w_value='5', w_pop='5')['weights']['value'], 0.5)

    def test_a_block_with_no_density_stands_lowest_and_a_density_weight_needs_the_file(self):
        self.assertEqual(self.plan(w_pop='1', status=503)['error'], 'Density is not built yet.')
        self.assertEqual(self.plan()['spans'][0]['pop_density'], None)                 # no weight on it: the plan does not need the file
        self.write_density(chd2=100.0)
        self.assertEqual([r['span'] for r in self.plan(w_pop='1')['spans']], [6, 1])

    def test_refusals(self):
        self.assertEqual(self.plan(ss=None, status=400)['error'], 'Choose a district or precinct first.')
        self.assertIn('not offered', self.plan(preset='priority', status=400)['error'])
        self.assertEqual(self.plan(w_value='0', status=400)['error'], 'Give at least one weight above zero.')
        for bad in ('-1', 'x', '1e3', ''):
            self.plan(w_value=bad, status=400)
        self.get('/api/v3/walk/plan?ss=010&extra=1', 400)
        self.get('/api/v3/walk/plan?ss=010&ss=011', 400)
        with mock.patch.object(V, 'MAX_SPANS', 3):
            self.assertIn('blocks', self.plan(status=400)['error'])

    def test_a_district_without_a_candidate_has_no_street_plan(self):
        with override_settings(DISTRICT=CO):
            self.assertEqual(self.plan(status=404)['error'], 'No candidate is set for this district.')
            self.assertEqual(self.get('/api/v3/walk/plan', 404)['error'], 'No candidate is set for this district.')

    def test_without_support_there_is_no_plan(self):
        with override_settings(DATA_DIR=self.world_without_support('nosupport')):
            self.assertEqual(self.plan(status=503)['error'], 'Support is not built yet.')
        with mock.patch.object(W.Store, 'attach_scores', return_value=False):
            self.assertEqual(self.plan(status=503)['error'], 'Support is not built yet.')

    def test_the_plan_reads_the_score_store_and_never_writes_it(self):
        before = hashlib.sha256((self.stores.directory / 'scores.sqlite3').read_bytes()).hexdigest()
        self.plan()
        self.assertEqual(hashlib.sha256((self.stores.directory / 'scores.sqlite3').read_bytes()).hexdigest(), before)

    def test_a_score_store_from_another_source_file_is_refused(self):
        with W.walk_store() as store:
            store.info = copy.copy(store.info)
            store.info.source_sha256 = '0' * 64
            with self.assertRaises(SourceStoreError) as caught:
                store.attach_scores()
        self.assertEqual(caught.exception.status, 409)

    # ---- the road-span pick of the walk list -----------------------------------------------------------------
    def test_spans_alone_list_exactly_those_blocks(self):
        payload = self.list(spans='1,6')
        self.assertEqual(self.voters(payload), self.people_in('chd1', 'chd2'))
        self.assertEqual(payload['query'], {'spans': '1,6'})
        self.assertEqual(payload['totals']['voters'], 20)

    def test_an_area_narrows_the_spans_and_the_ids_are_normalised(self):
        payload = self.list(spans='6,1,6', precinct=PRECINCT_W)
        self.assertEqual(self.voters(payload), self.people_in('chd1', 'chd2', precinct=PRECINCT_W))
        self.assertEqual(payload['query'], {'spans': '1,6', 'precinct': PRECINCT_W})

    def test_spans_add_to_street_picks_without_listing_anyone_twice(self):
        cedar = self.cedar()
        payload = self.list(streets='%d:both' % cedar, spans='1,7')
        self.assertEqual(self.voters(payload), self.people_in('chd1', 'chd2', 'chd3', 'rdg'))
        self.assertEqual(payload['totals']['voters'], 28)
        self.assertEqual(payload['query'], {'streets': '%d:both' % cedar, 'spans': '1,7'})
        even = self.list(streets='%d:even' % cedar, spans='1')                      # the odd side of block 1 comes from the span
        self.assertTrue(self.people_in('chd1') <= self.voters(even))

    def test_the_cap_counts_a_person_once(self):
        with mock.patch.object(V, 'MAX_VOTERS', 10):
            message = self.list(400, streets='%d:both' % self.cedar(), spans='1,7')['error']
            self.assertIn('That is 28 voters, and a walk list holds up to 10.', message)
            self.assertIn('That is 12 voters', self.list(400, spans='1')['error'])

    def test_bad_picks_are_refused(self):
        for bad in ('', 'x', '1;2', '1,,2', '-1', '1234567890'):
            self.list(400, spans=bad)
        self.assertIn('up to 200 planned streets', self.list(400, spans=','.join(map(str, range(1, 202))))['error'])
        self.assertEqual(len(self.list(spans=','.join(map(str, range(1, 201))))['query']['spans'].split(',')), 200)
        self.assertEqual(self.list(400)['error'], 'Pick an area or add a street first.')

    def test_a_list_of_planned_streets_saves_and_reloads_with_its_spans(self):
        shown = self.list(spans='1,6')
        saved = self.post('/api/v3/walk/lists', {'title': 'Planned', 'query': {'spans': '6,1'}, 'fingerprint': shown['fingerprint']}, 201)
        self.assertEqual((saved['query'], saved['changed']), ({'spans': '1,6'}, False))
        again = self.get('/api/v3/walk/lists/' + saved['list']['key'])
        self.assertEqual(again['query'], {'spans': '1,6'})
        self.assertEqual(self.voters(again), self.people_in('chd1', 'chd2'))

    def test_the_ranked_blocks_feed_a_walk_list(self):
        ranked = self.plan()['spans']
        payload = self.list(spans=str(ranked[0]['span']))
        self.assertEqual((payload['totals']['voters'], payload['totals']['doors']), (ranked[0]['people'], ranked[0]['doors']))
        both = self.list(spans=','.join(str(r['span']) for r in ranked))
        self.assertEqual(self.voters(both), self.people_in('chd1', 'chd2'))

    def test_the_old_query_shapes_are_unchanged(self):
        cedar = self.cedar()
        self.assertEqual(self.list(streets='%d:both' % cedar)['query'], {'streets': '%d:both' % cedar})
        self.assertEqual(self.list(ss='010')['query'], {'ss': '010'})
        self.assertEqual(self.list(streets='%d:odd' % cedar, precinct=PRECINCT_W)['query'],
                         {'streets': '%d:odd' % cedar, 'precinct': PRECINCT_W})

    # ---- the printed scores ----------------------------------------------------------------------------------
    def test_a_candidate_district_gets_overall_participation_and_support_as_whole_numbers(self):
        p01, p02, p05 = self.ids('p01', 'p02', 'p05')
        self.assertEqual(self.post('/api/v3/walk/scores', {'ids': [p01, p02, p05]}),
                         {'scores': {p01: {'ovr': 72, 'part': 80, 'sup': 63, 'push': 73},   # 71.5, 62.5 and 72.5 round up
                                     p02: {'ovr': 40, 'part': 60, 'sup': 50, 'push': 80},   # 0.6 + 0.5 x 0.4
                                     p05: {'ovr': 50, 'part': 50, 'sup': 100, 'push': 50}}})   # a supporter who may not vote: 1 x 0.5

    def test_a_district_without_a_candidate_has_no_support_number(self):
        (p01,) = self.ids('p01')
        with override_settings(DISTRICT=CO):
            self.assertEqual(self.post('/api/v3/walk/scores', {'ids': [p01]}), {'scores': {p01: {'ovr': 72, 'part': 80}}})

    def test_only_the_people_asked_for_and_known_come_back(self):
        (p19,) = self.ids('p19')
        body = self.post('/api/v3/walk/scores', {'ids': [p19, 'p_unknown', p19]})
        self.assertEqual(body, {'scores': {p19: {'ovr': 50, 'part': 50}}})            # no support score: no sup

    def test_the_scores_request_is_checked(self):
        for bad in ({}, {'ids': []}, {'ids': 'p_1'}, {'ids': [1]}, {'ids': ['a b']}, {'ids': [None]}, {'ids': ['x' * 81]},
                    {'ids': ['p_1'], 'extra': 1}):
            self.post('/api/v3/walk/scores', bad, 400)
        self.post('/api/v3/walk/scores', {'ids': ['p_%d' % i for i in range(V.MAX_VOTERS)]})
        self.post('/api/v3/walk/scores', {'ids': ['p_%d' % i for i in range(V.MAX_VOTERS + 1)]}, 400)
        self.assertEqual(self.client.get('/api/v3/walk/scores').status_code, 405)

    def test_without_the_score_store_there_are_no_scores(self):
        with mock.patch.object(W.Store, 'attach_scores', return_value=False):
            self.assertEqual(self.post('/api/v3/walk/scores', {'ids': self.ids('p01')}, 503)['error'], 'Scores are not built yet.')

    def test_a_store_without_the_support_table_still_gives_overall_and_participation(self):
        (p01,) = self.ids('p01')
        with override_settings(DATA_DIR=self.world_without_support('nosupport-scores')):
            self.assertEqual(self.post('/api/v3/walk/scores', {'ids': [p01]}), {'scores': {p01: {'ovr': 72, 'part': 80}}})


class RankTests(SimpleTestCase):
    ROI = {'min_doors': 5, 'min_length_m': 50}
    VALUE = {'value': 1.0, 'pop_density': 0.0, 'voter_density': 0.0}

    def span(self, doors=5, people=5, mixed=1.0, len_m=200.0, block=None, push=0):
        return {'doors': doors, 'people': people, 'scored': people, 'push': push, 'mixed': mixed, 'turnout': 0, 'identify': 0, 's_sum': 0,
                's_n': 0, 'p_sum': 0, 'p_n': 0, 'len_m': len_m, 'street': 'Main St', 'block': block}

    def test_a_short_block_is_measured_over_fifty_metres(self):
        spans = {1: self.span(len_m=30.0), 2: self.span(len_m=0.0), 3: self.span(len_m=None), 4: self.span(len_m=50.0)}
        rows, _ = V.rank_plan(spans, 'mixed', self.VALUE, self.ROI, None)
        self.assertEqual({r['len_m'] for r in rows}, {50.0})
        self.assertEqual({r['value_per_100m'] for r in rows}, {2.0})                   # 1.0 over 50 m
        self.assertEqual({r['voter_density'] for r in rows}, {10.0})                   # 5 people over 50 m
        rows, _ = V.rank_plan({1: self.span(len_m=50.1)}, 'mixed', self.VALUE, self.ROI, None)
        self.assertEqual(rows[0]['len_m'], 50.1)

    def test_the_push_preset_ranks_by_the_push_sum_and_not_by_mixed(self):
        spans = {1: self.span(mixed=9.0, push=1.0), 2: self.span(mixed=0.0, push=3.0, len_m=300.0), 3: self.span(mixed=5.0, push=2.0)}
        rows, _ = V.rank_plan(spans, 'push', self.VALUE, self.ROI, None)
        self.assertEqual([(r['span'], r['value'], r['value_per_100m']) for r in rows], [(2, 3.0, 1.0), (3, 2.0, 1.0), (1, 1.0, 0.5)])
        rows, _ = V.rank_plan(spans, 'mixed', self.VALUE, self.ROI, None)
        self.assertEqual([r['span'] for r in rows], [1, 3, 2])

    def test_the_door_floor_is_inclusive_at_five(self):
        rows, skipped = V.rank_plan({1: self.span(doors=4, people=6), 2: self.span(doors=5)}, 'mixed', self.VALUE, self.ROI, None)
        self.assertEqual(([r['span'] for r in rows], skipped), ([2], {'spans': 1, 'people': 6}))

    def test_ties_go_to_the_higher_value_then_the_lower_span(self):
        spans = {1: self.span(mixed=1.0, block='a'), 2: self.span(mixed=2.0, block='b'), 3: self.span(mixed=2.0, block='c')}
        density = {'a': 30.0, 'b': 10.0, 'c': 10.0}
        rows, _ = V.rank_plan(spans, 'mixed', {'value': 0.0, 'pop_density': 1.0, 'voter_density': 0.0}, self.ROI, density)
        self.assertEqual([r['span'] for r in rows], [1, 2, 3])                          # 2 and 3 tie on density and on value
        rows, _ = V.rank_plan(spans, 'mixed', {'value': 0.5, 'pop_density': 0.5, 'voter_density': 0.0}, self.ROI, density)
        self.assertEqual([r['span'] for r in rows], [2, 3, 1])

    def test_a_block_with_no_value_on_a_weighted_measure_stands_lowest(self):
        spans = {1: self.span(block='a'), 2: self.span(block='missing')}
        rows, _ = V.rank_plan(spans, 'mixed', {'value': 0.0, 'pop_density': 1.0, 'voter_density': 0.0}, self.ROI, {'a': 5.0})
        self.assertEqual([(r['span'], r['score'], r['pop_density']) for r in rows], [(1, 0.5, 5.0), (2, 0.0, None)])

    def test_percentile_is_the_midrank_and_skips_missing(self):
        self.assertEqual(V.percentile({'a': 1, 'b': 2, 'c': 2, 'd': 3, 'e': None}), {'a': 0.125, 'b': 0.5, 'c': 0.5, 'd': 0.875})
        self.assertEqual(V.percentile({'a': None}), {})
        self.assertEqual(V.percentile({}), {})


class PushFormulaTests(SimpleTestCase):
    """Needs a push (Paul 2026-10-08): push = (1 - |2s - 1|) p + s (1 - p), shown 0..100, s = support standing / 100, p = participation / 100."""

    def test_a_hand_checked_example_in_its_two_parts(self):
        # s 0.625, p 0.8: undecided and likely to vote (1 - |1.25 - 1|) x 0.8 = 0.6; supporter who may not vote 0.625 x 0.2 = 0.125
        undecided, supporter = FL.push_parts(62.5, 80.0)
        self.assertAlmostEqual(undecided, 60.0, places=9)
        self.assertAlmostEqual(supporter, 12.5, places=9)
        self.assertAlmostEqual(FL.push(62.5, 80.0), 72.5, places=9)
        # a split voter who rarely votes: s 0.5, p 0.2 -> 0.2 + 0.5 x 0.8 = 0.6
        self.assertAlmostEqual(FL.push(50.0, 20.0), 60.0, places=9)

    def test_sure_supporters_who_always_vote_and_sure_opponents_score_nothing(self):
        self.assertEqual(FL.push(100.0, 100.0), 0.0)
        self.assertEqual([FL.push(0.0, p) for p in (0.0, 25.0, 50.0, 100.0)], [0.0] * 4)         # a sure opponent, whatever the turnout
        self.assertAlmostEqual(FL.push(99.0, 100.0), 2.0, places=9)                              # nearly sure: nearly nothing
        self.assertAlmostEqual(FL.push(1.0, 100.0), 2.0, places=9)

    def test_the_most_a_person_can_score_is_100_and_it_is_reached_two_ways(self):
        self.assertAlmostEqual(FL.push(50.0, 100.0), 100.0, places=9)        # undecided and certain to vote
        self.assertAlmostEqual(FL.push(100.0, 0.0), 100.0, places=9)         # a sure supporter who will not vote
        self.assertAlmostEqual(FL.push(50.0, 0.0), 50.0, places=9)
        self.assertAlmostEqual(FL.push(0.0, 0.0), 0.0, places=9)

    def test_it_stays_inside_0_to_100_and_the_parts_add_up(self):
        grid = [(s / 2.0, p / 2.0) for s in range(0, 201) for p in range(0, 201)]               # steps of half a point
        for s, p in grid:
            undecided, supporter = FL.push_parts(s, p)
            self.assertGreaterEqual(undecided, 0.0)
            self.assertGreaterEqual(supporter, 0.0)
            self.assertLessEqual(FL.push(s, p), 100.0 + 1e-9, (s, p))
            self.assertAlmostEqual(undecided + supporter, FL.push(s, p), places=9)

    def test_arrays_give_the_same_numbers_as_single_values(self):
        s, p = np.array([0.0, 50.0, 62.5, 100.0, 33.3]), np.array([100.0, 100.0, 80.0, 0.0, 47.0])
        np.testing.assert_allclose(FL.push(s, p), [FL.push(a, b) for a, b in zip(s, p)], rtol=0, atol=1e-12)

    def test_the_sql_of_the_street_plan_is_the_same_formula(self):
        with closing(sqlite3.connect(':memory:')) as db:
            for s, p in [(s, p) for s in (0, 1, 12.5, 33.3, 50, 62.5, 66.7, 99, 100) for p in (0, 7, 20, 50, 80, 100)]:
                (sql,) = db.execute('SELECT ' + W.PUSH_SQL + ' FROM (SELECT ? AS sup_pct) sp, (SELECT ? AS part_pct) sc', (s, p)).fetchone()
                self.assertAlmostEqual(100.0 * sql, FL.push(s, p), places=9, msg=(s, p))

    def test_the_wording_passes_the_advisory_gate_and_the_preset_is_listed_first(self):
        self.assertGreater(FL.self_check(), 0)
        self.assertEqual(list(FL.ROI_PRESETS)[:2], ['push', 'mixed'])
        self.assertEqual(FL.ROI_PRESETS['push'][0], 'Needs a push')
