"""Street plan (GET /api/v3/walk/plan), the one-click turf (doors=), the road-span pick of the walk list (spans=, fresh=1, and doors=
to keep a door count) and the printed scores (POST /api/v3/walk/scores): hand-computed presets, the 5-door and 50 m rules, the
weights, doors with a recorded result, refusals, and the pick feeding a walk list. The world is walk_testkit's invented neighbourhood with invented scores and a
support table added to its score store. WalkPlanTests set the untapped terms (untapped.terms) and the doors with a result
(untapped.knocked) themselves, so its sums do not depend on how those are read; RealUntappedTests run the same tests with
them read as the app reads them: the five turnout literals and the sphere of influence standing in the score store, the
results posted to a saved list (test_untapped.py covers the reading itself).

Hand-checked terms of the three blocks that matter (s = support score / 100, 0.5 with none; t = the invented participation / 100,
the chance of voting; undecided (1 - |2s - 1|) t; supporter s (1 - t)):

  Cedar Heights Dr, first block (chd1, span 1, 200 m, 7 doors, 12 people)
    p01-p04  s 0.5, t 0.8 0.6 0.4 0.2    undecided 0.8 + 0.6 + 0.4 + 0.2 = 2.0     supporter 0.5 x (0.2 + 0.4 + 0.6 + 0.8) = 1.0
    p05-p08  s 1.0, t 0.5                undecided 0                                supporter 4 x 0.5 = 2.0
    p09-p12  s 0.0, t 0.9                undecided 0                                supporter 0
    undecided 2.0   supporter 3.0   identify 4 (the four with s 0.5)   mean s 0.5   mean t 0.6333
  Cedar Heights Dr, second block (chd2, span 6, 200 m, 6 doors, 8 people)
    p13-p15  s 0.4, t 0.5   undecided 0.8 x 0.5 = 0.4 each (1.2)     supporter 0.4 x 0.5 = 0.2 each (0.6)
    p16-p17  s 0.2, t 1.0   undecided 0.4 x 1.0 = 0.4 each (0.8)     supporter 0
    p18      s 2/3, t 0.5   undecided 2/3 x 0.5 = 1/3                 supporter 2/3 x 0.5 = 1/3      (2/3 is inside the middle third)
    p19-p20  no support score, so s 0.5, t 0.5   undecided 0.5 each (1.0)   supporter 0.25 each (0.5)
    undecided 3.3333   supporter 1.4333   identify 6 (three at 0.4, the 2/3, the two with none)   mean s 0.4083   mean t 0.625
  Ridgeview Ct (rdg, span 7, 100 m, 4 doors, 5 people): only ranked when the door floor is lowered to 4
    p27 s 1/3 (inside), p28 s 0.329 (outside), p29 s 0.67 (outside), p30 s 0.5 (inside), p31 none, so 0.5 (inside)   identify 3

Everyone else has s 0.5, t 0.5. The blocks are 200 m, 200 m and 100 m long, so value per 100 m is half the value.

Untapped potential ("Needs a push"), per person = (wP undecided + wR supporter) (1 + wI i), at the defaults wP = wR = wI = 0.5. The
sphere of influence standing i is 0 except p01 0.8, p05 0.4 and p17 0.6:
  chd1  p01 0.5 x (0.8 + 0.1) x 1.4 = 0.63      p02 0.5 x (0.6 + 0.2) = 0.4      p03 0.5 x (0.4 + 0.3) = 0.35      p04 0.5 x (0.2 + 0.4) = 0.3
        p05 0.5 x (0 + 0.5) x 1.2 = 0.3         p06-p08 0.5 x 0.5 = 0.25 each = 0.75       p09-p12 (s 0, a sure opponent) 0
        potential 2.73, per 100 m 1.365
  chd2  p13-p15 0.5 x (0.4 + 0.2) = 0.3 each = 0.9      p16 0.5 x 0.4 = 0.2      p17 0.2 x 1.3 = 0.26      p18 0.5 x 2/3 = 1/3
        p19-p20 0.5 x (0.5 + 0.25) = 0.375 each = 0.75       potential 2.4433, per 100 m 1.2217
        (so the potential puts chd1 first; undecided alone puts chd2 first)
"""
import copy
import hashlib
import json
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import date
from pathlib import Path
from types import SimpleNamespace
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
from .models import WalkList
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
SOI = {'p01': 80.0, 'p05': 40.0, 'p17': 60.0}         # sphere of influence standing (soi_pct); everyone else 0
OVR = {'p01': 71.5, 'p02': 40.4}          # overall standings that exercise the rounding (half goes up)
SUP_PCT = {'p01': 62.5}                   # a support standing that is not the support score
PRECINCT_W = '5200021301'
PRECINCT_F = '5200021417'                 # Fountain's Pine St alone, 3 km from everything else
GOLDEN = {'spans': '00b37cdfa741464ccca1a18a20468bc7639c7561671d2c927952db50940d9ae4',      # digests of lists without doors=, taken before it existed
          'fresh': '57f8b608de7f77051538b845ec759a8acda9f8483a02dbe8497469ca0f7f0d60',
          'streets': 'c9a3e7e3e702dfca1109a4f9bdcb37d4e57670b3fd9ecd293412def1cef11971',
          'area': '6f82e41b75bf77c53f33029dabe933c4ee81ef0053d993bc9c63e80c2156de30'}


def digest(payload):
    """The sha256 of a walk list as sent, all but the day it was built and its fingerprint (which holds the voter keys, salted per build)."""
    shown = {key: value for key, value in payload.items() if key not in ('generated_on', 'fingerprint')}
    return hashlib.sha256(json.dumps(shown, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def lowered(**roi):
    """The candidate district with some street-plan settings changed."""
    return {**CANDIDATE, 'roi': {**CANDIDATE['roi'], **roi}}


def add_scores(stores):
    """Give the testkit's score store the columns and the support table the street plan reads; {person key: pos}."""
    census = {r['person_id']: r['csv']['svi_registration_censusid'] for r in stores.case.rows}
    positions = {}
    with closing(sqlite3.connect(stores.directory / 'scores.sqlite3')) as db, db:
        for column in ('ovr_pct REAL', 'part_pct REAL', 'reg_block TEXT'):
            db.execute('ALTER TABLE person ADD COLUMN ' + column)
        db.execute(SUPPORT_DDL)
        for key, person in stores.expected['keys'].items():
            pos = positions[key] = db.execute('SELECT pos FROM person WHERE person_id = ?', (person,)).fetchone()[0]
            sup, part = SCORES.get(key, PLAIN)
            db.execute('UPDATE person SET ovr_pct = ?, part_pct = ?, reg_block = ? WHERE pos = ?',
                       (OVR.get(key, 50.0), part, census[person], pos))
            if sup is not None:
                db.execute('INSERT INTO support VALUES (?, ?, ?, 1, 1, 1)', (pos, sup, SUP_PCT.get(key, sup)))
    T.add_potential_inputs(stores.directory / 'scores.sqlite3', {      # five equal turnout scores: t is the participation / 100
        person: {'sai': [int(SCORES.get(key, PLAIN)[1])] * 5, 'soi_pct': SOI.get(key, 0.0)} for key, person in stores.expected['keys'].items()})
    return positions


def fake_terms(positions):
    """What untapped.terms() gives for the world: s = support score / 100 (0.5 with none), t = participation / 100, i = soi_pct / 100."""
    n = len(positions)
    s, t, i = np.full(n, 0.5), np.full(n, 0.5), np.zeros(n)
    for key, pos in positions.items():
        sup, part = SCORES.get(key, PLAIN)
        if sup is not None:
            s[pos - 1] = sup / 100.0
        t[pos - 1] = part / 100.0
        i[pos - 1] = SOI.get(key, 0.0) / 100.0
    return SimpleNamespace(n=n, s=s, t=t, i=i, undecided=(1 - np.abs(2 * s - 1)) * t, supporter=s * (1 - t),
                           t_source={'sai': n, 'record': 0, 'even': 0}, stamp=None)


@override_settings(LOCAL_ADMIN_ACCESS=True, HOSTED=False, DEMO_MODE=False, DISTRICT=CANDIDATE)
class WalkPlanTests(TestCase):
    """The synthetic walk world with scores and support, built once; every test reads it through the API."""
    real = False                    # True: untapped.terms() and untapped.knocked() are not replaced (RealUntappedTests)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.stores = T.write_minimal_stores(Path(cls.temporary.name) / 'world')
        if not B.build(cls.stores.source_csv, cls.stores.directory, force=True)['replaced']:
            raise AssertionError('The synthetic walk store was not written.')
        cls.positions = add_scores(cls.stores)                  # after the build, so the walk store is the testkit's own
        cls.terms = fake_terms(cls.positions)
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
        self.doors = set()                          # doors with a recorded result: (stop id, unit)
        self.lists = {}                             # real results: the saved list of each senate district
        if self.real:
            V.U.reset()                             # cached on the results version, which a rolled-back test can repeat
            self.addCleanup(V.U.reset)
            return
        for name, value in (('terms', lambda: self.terms),
                            ('knocked', lambda: SimpleNamespace(version='0-0', doors=frozenset(self.doors), pos=None, people=0))):
            patch = mock.patch.object(V.U, name, side_effect=value)
            patch.start()
            self.addCleanup(patch.stop)

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

    def span_of(self, block):
        with closing(sqlite3.connect(self.stores.directory / 'walk.sqlite3')) as db:
            (span,) = {db.execute('SELECT s.span_id FROM person p JOIN stop s ON s.id = p.stop_id WHERE p.person_id = ?',
                                  (p,)).fetchone()[0] for p in self.people_in(block)}
        return span

    def knock(self, *keys):
        """A result is recorded for these people: everyone at their doors is left out."""
        if not self.real:
            with closing(sqlite3.connect(self.stores.directory / 'walk.sqlite3')) as db:
                for person in self.ids(*keys):
                    self.doors.add(db.execute("SELECT stop_id, IFNULL(unit, '') FROM person WHERE person_id = ?", (person,)).fetchone())
            return
        for ss in sorted({self.expected['people'][person]['ss'] for person in self.ids(*keys)}):      # a result goes on a saved list
            if ss not in self.lists:
                shown = self.list(ss=ss)
                self.lists[ss] = self.post('/api/v3/walk/lists', {'title': 'Worked ' + ss, 'query': {'ss': ss},
                                                                  'fingerprint': shown['fingerprint']}, 201)['list']['key']
            entries = [{'person_id': person, 'code': 'S'} for person in self.ids(*keys) if self.expected['people'][person]['ss'] == ss]
            self.post('/api/v3/walk/dispositions', {'list': self.lists[ss], 'on': date.today().isoformat(), 'entries': entries})

    def door_values(self, block, precinct=PRECINCT_W, wp=0.5, wi=0.5):
        """{(stop id, unit): potential} of the doors of `block` in `precinct` with no recorded result, summed person by person from the
        invented terms (FL.potential), apart from the code under test."""
        at = {self.expected['keys'][key]: pos - 1 for key, pos in self.positions.items()}
        values, left_out = {}, V.U.knocked().doors
        with closing(sqlite3.connect(self.stores.directory / 'walk.sqlite3')) as db:
            for person in self.people_in(block, precinct=precinct):
                door = tuple(db.execute("SELECT stop_id, IFNULL(unit, '') FROM person WHERE person_id = ?", (person,)).fetchone())
                if door not in left_out:
                    k = at[person]
                    values[door] = values.get(door, 0.0) + float(FL.potential(self.terms.s[k], self.terms.t[k], self.terms.i[k], wp, wi))
        return values

    def best_doors(self, block, keep, **weights):
        """The `keep` doors of `block` of most potential, ties to the lower door key."""
        values = self.door_values(block, **weights)
        return [door for door, _ in sorted(values.items(), key=lambda item: (-item[1], item[0]))[:keep]]

    def people_at(self, doors, precinct=PRECINCT_W):
        with closing(sqlite3.connect(self.stores.directory / 'walk.sqlite3')) as db:
            return {person for stop, unit in doors for (person,) in db.execute(
                "SELECT person_id FROM person WHERE stop_id = ? AND IFNULL(unit, '') = ? AND precinct = ?", (stop, unit, precinct))}

    def door_of(self, key):
        with closing(sqlite3.connect(self.stores.directory / 'walk.sqlite3')) as db:
            return tuple(db.execute("SELECT stop_id, IFNULL(unit, '') FROM person WHERE person_id = ?", self.ids(key)).fetchone())

    def blocks_of_w(self):
        """{span id: the testkit's name of the block} for the five blocks of precinct W."""
        return {1: 'chd1', 6: 'chd2', 7: 'rdg', self.span_of('jun_s'): 'jun_s', self.span_of('jun_n1'): 'jun_n1'}

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
    def test_undecided_voters_who_usually_vote_by_hand(self):
        plan = self.plan()
        self.assertEqual({k: plan[k] for k in ('schema', 'preset', 'p', 'race_mode', 'potential', 'knocked')},
                         {'schema': 'walk-plan-2', 'preset': 'mixed', 'p': 'turnout', 'race_mode': 'general',
                          'potential': {'wp': 0.5, 'wi': 0.5}, 'knocked': {'people': 0, 'doors': 0}})
        self.assertEqual(plan['weights'], {'value': 1.0, 'pop_density': 0.0, 'voter_density': 0.0})
        self.assertEqual(plan['spans'], [
            {'span': 6, 'street': 'Cedar Heights Dr', 'doors': 6, 'people': 8, 'len_m': 200.0, 's_mean': 0.4083, 't_mean': 0.625,
             'pop_density': None, 'voter_density': 4.0, 'value': 3.3333, 'value_per_100m': 1.6667, 'score': 0.75, 'rank': 1},
            {'span': 1, 'street': 'Cedar Heights Dr', 'doors': 7, 'people': 12, 'len_m': 200.0, 's_mean': 0.5, 't_mean': 0.6333,
             'pop_density': None, 'voter_density': 6.0, 'value': 2.0, 'value_per_100m': 1.0, 'score': 0.25, 'rank': 2}])
        self.assertNotIn('turf', plan)

    def test_untapped_potential_by_hand_and_is_the_default_of_a_real_district(self):
        with override_settings(DISTRICT={**CANDIDATE, 'roi': ROI_PUSH}):
            plan = self.plan()
        self.assertEqual({k: plan[k] for k in ('schema', 'preset')}, {'schema': 'walk-plan-2', 'preset': 'push'})
        self.assertEqual(plan['spans'], [
            {'span': 1, 'street': 'Cedar Heights Dr', 'doors': 7, 'people': 12, 'len_m': 200.0, 's_mean': 0.5, 't_mean': 0.6333,
             'pop_density': None, 'voter_density': 6.0, 'value': 2.73, 'value_per_100m': 1.365, 'score': 0.75, 'rank': 1},
            {'span': 6, 'street': 'Cedar Heights Dr', 'doors': 6, 'people': 8, 'len_m': 200.0, 's_mean': 0.4083, 't_mean': 0.625,
             'pop_density': None, 'voter_density': 4.0, 'value': 2.4433, 'value_per_100m': 1.2217, 'score': 0.25, 'rank': 2}])
        self.assertEqual(plan, self.plan(preset='push'))

    def test_the_balance_and_influence_weights_move_the_potential(self):
        def values(**query):
            plan = self.plan(preset='push', **query)
            return plan['potential'], {r['span']: r['value'] for r in plan['spans']}
        self.assertEqual(values(wp='1', wi='0'), ({'wp': 1.0, 'wi': 0.0}, {1: 2.0, 6: 3.3333}))        # undecided alone: the older 'mixed'
        self.assertEqual(values(wp='0', wi='0'), ({'wp': 0.0, 'wi': 0.0}, {1: 3.0, 6: 1.4333}))        # supporters alone: the older 'turnout'
        self.assertEqual(values(wp='0.5', wi='0')[1], {1: 2.5, 6: 2.3833})                             # no influence: half of undecided + supporter
        # undecided alone with influence 1: p01 0.8 x 1.8, p02-p04 1.2; p13-p15 1.2, p16 0.4, p17 0.4 x 1.6, p18 1/3, p19-p20 1.0
        self.assertEqual(values(wp='1', wi='1'), ({'wp': 1.0, 'wi': 1.0}, {1: 2.64, 6: 3.5733}))
        self.assertEqual(values(wp='0.3', wi='0.0001')[0], {'wp': 0.3, 'wi': 0.0001})
        self.assertEqual(self.plan(preset='push', wp='1.0')['potential'], {'wp': 1.0, 'wi': 0.5})        # one weight sent, the other default

    def test_the_weights_leave_the_other_presets_alone(self):
        self.assertEqual(self.plan(preset='mixed', wp='0', wi='1')['spans'], self.plan(preset='mixed')['spans'])
        self.assertEqual(self.plan(preset='identify', wp='0', wi='1')['spans'], self.plan(preset='identify')['spans'])

    def test_potential_ranks_the_streets_differently_from_the_alternatives_it_leaves_in_place(self):
        self.assertEqual([r['span'] for r in self.plan(preset='push')['spans']], [1, 6])
        self.assertEqual([r['span'] for r in self.plan(preset='mixed')['spans']], [6, 1])
        self.assertEqual([r['span'] for r in self.plan(preset='turnout')['spans']], [1, 6])
        self.assertEqual([r['span'] for r in self.plan(preset='identify')['spans']], [6, 1])
        self.assertEqual(ROI_PUSH['presets'], ('push', 'mixed', 'turnout', 'identify'))

    def test_potential_follows_the_same_span_rules_and_weights(self):
        with override_settings(DISTRICT=lowered(min_doors=4)):
            self.assertEqual(sorted(self.by_span(self.plan(preset='push'))), [1, 6, 7])
        with override_settings(DISTRICT=lowered(min_length_m=250)):
            rows = self.by_span(self.plan(preset='push'))
        self.assertEqual((rows[1]['len_m'], rows[1]['value_per_100m'], rows[6]['value_per_100m']), (250.0, 1.092, 0.9773))
        self.write_density(chd1=900.0, chd2=100.0)
        self.assertEqual([(r['span'], r['score']) for r in self.plan(preset='push', w_value='0', w_pop='1', w_voter='0')['spans']],
                         [(1, 0.75), (6, 0.25)])
        self.assertEqual(self.plan(preset='push', w_value='1', w_pop='1')['weights']['pop_density'], 0.5)

    def test_supporter_turnout_by_hand(self):
        plan = self.plan(preset='turnout')
        rows = self.by_span(plan)
        self.assertEqual([r['span'] for r in plan['spans']], [1, 6])
        self.assertEqual((rows[1]['value'], rows[1]['value_per_100m'], rows[1]['rank']), (3.0, 1.5, 1))
        self.assertEqual((rows[6]['value'], rows[6]['value_per_100m'], rows[6]['rank']), (1.4333, 0.7167, 2))

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
        self.assertEqual((rows[6]['len_m'], rows[6]['value_per_100m'], rows[6]['voter_density']), (250.0, 1.3333, 3.2))
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

    def test_the_potential_weights_and_the_doors_are_checked(self):
        for name, bad in (('wp', '1.5'), ('wi', '-1'), ('wp', ''), ('wi', 'x'), ('wp', '0.12345'), ('wi', '1e0'), ('wp', '2')):
            self.plan(status=400, **{name: bad})
        for good in ('0', '1', '0.5', '0.0001', '1.0'):
            self.plan(wp=good, wi=good)
        self.assertEqual(self.plan(ss=None, precinct=PRECINCT_W, doors='20', status=200)['turf']['asked'], 20)
        for good in ('1', '5', '501', '1000'):                                      # a door ask is capped at MAX_DOORS (1,000)
            self.assertEqual(self.plan(ss=None, precinct=PRECINCT_W, doors=good)['turf']['asked'], int(good))
        for bad in ('0', '000'):
            self.assertEqual(self.plan(ss=None, precinct=PRECINCT_W, doors=bad, status=400)['error'], 'Ask for at least 1 door.')
        for bad in ('1001', '5000', '10000', '999999'):
            self.assertEqual(self.plan(ss=None, precinct=PRECINCT_W, doors=bad, status=400)['error'], 'Ask for 1 to 1,000 doors.')
        for bad in ('x', '1000000', '', '-20', '2.5'):
            self.plan(ss=None, precinct=PRECINCT_W, doors=bad, status=400)
        self.assertEqual(self.plan(doors='20', status=400)['error'], 'Plan a walk list from one precinct.')      # a district is not a precinct
        self.assertEqual(self.plan(ss=None, sh='018', doors='20', status=400)['error'], 'Plan a walk list from one precinct.')
        self.assertEqual(self.plan(ss='010', precinct=PRECINCT_W, doors='10')['turf']['asked'], 10)           # a precinct inside a district is

    def test_a_district_without_a_candidate_has_no_street_plan(self):
        with override_settings(DISTRICT=CO):
            self.assertEqual(self.plan(status=404)['error'], 'No candidate is set for this district.')
            self.assertEqual(self.plan(ss=None, precinct=PRECINCT_W, doors='20', status=404)['error'], 'No candidate is set for this district.')
            self.assertEqual(self.get('/api/v3/walk/plan', 404)['error'], 'No candidate is set for this district.')

    def test_without_support_there_is_no_plan(self):
        with override_settings(DATA_DIR=self.world_without_support('nosupport')):
            self.assertEqual(self.plan(status=503)['error'], 'Support is not built yet.')
        with mock.patch.object(W.Store, 'attach_scores', return_value=False):
            self.assertEqual(self.plan(status=503)['error'], 'Support is not built yet.')

    def test_the_plan_reads_the_score_store_and_never_writes_it(self):
        before = hashlib.sha256((self.stores.directory / 'scores.sqlite3').read_bytes()).hexdigest()
        self.plan()
        self.plan(ss=None, precinct=PRECINCT_W, doors='10')
        self.assertEqual(hashlib.sha256((self.stores.directory / 'scores.sqlite3').read_bytes()).hexdigest(), before)

    def test_a_score_store_from_another_source_file_is_refused(self):
        with W.walk_store() as store:
            store.info = copy.copy(store.info)
            store.info.source_sha256 = '0' * 64
            with self.assertRaises(SourceStoreError) as caught:
                store.attach_scores()
        self.assertEqual(caught.exception.status, 409)

    def test_a_block_carries_its_end_nodes_and_blocks_that_meet_share_one(self):
        with W.walk_store() as store:
            store.attach_scores()
            rows, blocks = store.span_plan({'ss': '010'})
            spans, gone = V.fold_plan(store, rows, blocks, V.U.terms(), SimpleNamespace(doors=frozenset()), 0.5, 0.5)
            with closing(sqlite3.connect(self.stores.directory / 'walk.sqlite3')) as db:
                ends = dict((i, (a, b)) for i, a, b in db.execute('SELECT id, a, b FROM road_span'))
            xy = store.node_xy([n for span in spans.values() for n in (span['a'], span['b'])])
        self.assertEqual(gone, {'people': 0, 'doors': 0})
        self.assertEqual({k: (v['a'], v['b']) for k, v in spans.items()}, {k: ends[k] for k in spans})
        chd1, chd2 = spans[1], spans[6]
        self.assertEqual(len({chd1['a'], chd1['b']} & {chd2['a'], chd2['b']}), 1)            # Cedar Heights Dr meets itself at Juniper Ln
        self.assertTrue(all(isinstance(x, float) and isinstance(y, float) for x, y in xy.values()))
        self.assertEqual(set(xy), {n for span in spans.values() for n in (span['a'], span['b'])})
        node = chd1['a'] if chd1['a'] in (chd2['a'], chd2['b']) else chd1['b']
        far = chd1['b'] if node == chd1['a'] else chd1['a']
        self.assertAlmostEqual(((xy[node][0] - xy[far][0]) ** 2 + (xy[node][1] - xy[far][1]) ** 2) ** 0.5, 200.0, delta=0.5)

    def test_a_precinct_alone_searches_the_area_index_and_reads_the_same_rows(self):
        area = {'precinct': PRECINCT_W}
        clause, args = W._area(area, 'p')
        plain = (' AND p.precinct=?', [PRECINCT_W])          # the clause before the combo seek: a scan of person_area
        sql = W.VOTER_SQL + ' WHERE 1=1%s ORDER BY p.person_id'
        with W.walk_store() as store:
            plan = ' '.join(row[-1] for row in store.conn.execute('EXPLAIN QUERY PLAN ' + sql % clause, args))
            self.assertIn('SEARCH p USING INDEX person_area (ss=? AND sh=? AND precinct=?)', plan)
            rows = [tuple(r) for r in store.conn.execute(sql % clause, args)]
            self.assertTrue(rows)
            self.assertEqual(rows, [tuple(r) for r in store.conn.execute(sql % plain[0], plain[1])])
            self.assertEqual([tuple(r) for r in store.voters([], area, 1000)],
                             [tuple(r) for r in store.conn.execute(W.VOTER_SQL + ' WHERE 1=1' + plain[0], plain[1])])

    def test_the_fold_keeps_the_undecided_and_supporter_parts_of_each_door_when_asked(self):
        with W.walk_store() as store:
            store.attach_scores()
            rows, blocks = store.span_plan({'precinct': PRECINCT_W})
            plain, _ = V.fold_plan(store, rows, blocks, V.U.terms(), V.U.knocked(), 0.5, 0.5)
            split, _ = V.fold_plan(store, rows, blocks, V.U.terms(), V.U.knocked(), 0.5, 0.5, by_door=True)
        self.assertEqual(plain[1]['parts'], {})                                       # not asked for: nothing kept
        parts = split[1]['parts']
        self.assertEqual((len(parts), sum(part[0] for part in parts.values())), (7, 12))      # chd1: seven doors, twelve people
        # the door of p03, p04 and p05: undecided 0.2 + 0.1 + 0 and supporter 0.15 + 0.2 + 0.3 (the module's hand check)
        door = parts[self.door_of('p03')]
        self.assertEqual(door[0], 3)
        self.assertAlmostEqual(door[1], 0.3)
        self.assertAlmostEqual(door[2], 0.65)
        self.assertAlmostEqual(sum(part[1] + part[2] for part in parts.values()), 2.73)
        self.assertAlmostEqual(split[1]['push'], 2.73)
        self.assertEqual({k: (v['doors'], v['people'], v['push']) for k, v in split.items()},
                         {k: (v['doors'], v['people'], v['push']) for k, v in plain.items()})      # the sums are the same either way

    # ---- doors with a recorded result ------------------------------------------------------------------------
    def test_a_door_with_a_recorded_result_leaves_the_sums_and_is_counted(self):
        self.knock('p03')                                    # the door at 1132 holds p03, p04 and p05
        mixed = self.plan()
        self.assertEqual(mixed['knocked'], {'people': 3, 'doors': 1})
        self.assertEqual(mixed['matched'], {'people': 31, 'scored': 31})
        row = self.by_span(mixed)[1]
        self.assertEqual((row['doors'], row['people'], row['value'], row['value_per_100m'], row['voter_density']), (6, 9, 1.4, 0.7, 4.5))
        self.assertEqual((row['s_mean'], row['t_mean']), (0.4444, 0.7222))
        self.assertEqual(self.by_span(mixed)[6]['value'], 3.3333)                            # the other block is as it was
        rows = self.by_span(self.plan(preset='push'))
        self.assertEqual((rows[1]['value'], rows[1]['doors'], rows[1]['people']), (1.78, 6, 9))     # 2.73 less 0.35 + 0.3 + 0.3
        self.assertEqual(self.by_span(self.plan(preset='turnout'))[1]['value'], 1.8)
        self.assertEqual(self.by_span(self.plan(preset='identify'))[1]['value'], 2)

    def test_only_the_unit_with_a_result_leaves_when_a_house_has_two(self):
        self.knock('p19')                                    # 1285 unit A; p20 at unit B of the same stop stays
        plan = self.plan()
        self.assertEqual(plan['knocked'], {'people': 1, 'doors': 1})
        row = self.by_span(plan)[6]
        self.assertEqual((row['doors'], row['people'], row['value']), (5, 7, 2.8333))

    def test_two_people_at_one_door_leave_together(self):
        self.knock('p13')                                    # p14 lives at the same door
        self.assertEqual(self.plan()['knocked'], {'people': 2, 'doors': 1})

    def test_a_block_with_every_door_done_is_not_ranked_and_the_others_are_unchanged(self):
        self.knock(*(f'p{n:02d}' for n in range(1, 13)))
        plan = self.plan()
        self.assertEqual(plan['knocked'], {'people': 12, 'doors': 7})
        self.assertEqual([r['span'] for r in plan['spans']], [6])
        self.assertEqual(plan['not_ranked'], {'spans': 6, 'people': 14})
        self.assertEqual(plan['matched'], {'people': 22, 'scored': 22})
        self.assertEqual(self.by_span(self.plan(preset='push'))[6]['value'], 2.4433)

    def test_doors_with_a_result_elsewhere_are_not_counted_in_this_area(self):
        self.knock('p35')                                    # Fountain's Pine St, outside this senate district
        self.assertEqual(self.plan()['knocked'], {'people': 0, 'doors': 0})
        self.assertEqual(self.plan(ss='011')['knocked'], {'people': 2, 'doors': 1})

    # ---- the one-click turf ----------------------------------------------------------------------------------
    def turf(self, doors, precinct=PRECINCT_W, **query):
        return self.plan(ss=None, precinct=precinct, doors=str(doors), **query)['turf']

    def test_the_turf_starts_at_the_most_untapped_block_and_grows_along_connected_streets(self):
        # blocks of precinct W by potential: chd1 2.73 (7 doors), rdg 1.5793, chd2 1.1 (3 doors), jun_s 0.75, jun_n1 0.375
        jun_s, jun_n1 = self.span_of('jun_s'), self.span_of('jun_n1')
        self.assertEqual(self.turf(10), {'asked': 10, 'spans': [1, 6], 'doors': 10, 'people': 16, 'value': 3.83,
                                         'undecided': 1.96, 'supporter': 1.87, 'jumps': [], 'stopped': 'full', 'trim': None})
        # rdg, the second best block of all, waits: it meets none of the blocks taken until Juniper Ln is
        self.assertEqual(self.turf(11)['spans'], [1, 6, jun_s])
        self.assertEqual(self.turf(13)['spans'], [1, 6, jun_s, jun_n1])
        full = self.turf(14)
        self.assertEqual((full['spans'], full['doors'], full['stopped']), ([1, 6, jun_s, jun_n1, 7], 14, 'full'))      # rdg cut to 1 of its 4 doors
        everything = self.turf(18)
        self.assertEqual((everything['spans'], everything['doors'], everything['people'], everything['stopped'], everything['trim']),
                         ([1, 6, jun_s, jun_n1, 7], 17, 24, 'exhausted', None))
        self.assertEqual(everything['jumps'], [])

    def test_the_turf_is_worth_the_potential_at_the_weights_whatever_the_preset(self):
        self.assertEqual(self.turf(10, preset='identify'), self.turf(10))
        self.assertEqual(self.turf(10, preset='mixed'), self.turf(10, preset='push'))
        turf = self.turf(10, wp='1', wi='0')                       # undecided alone: chd1 2.0 + chd2's four 1.6
        self.assertEqual((turf['spans'], turf['value'], turf['undecided'], turf['supporter']), ([1, 6], 3.6, 3.6, 0))
        turf = self.turf(10, wp='0', wi='0')                       # supporters alone: chd1 3.0 + chd2's four 0.6
        self.assertEqual((turf['spans'], turf['value'], turf['undecided'], turf['supporter']), ([1, 6], 3.6, 0, 3.6))

    def doors_of(self, span, precinct=PRECINCT_W):
        with closing(sqlite3.connect(self.stores.directory / 'walk.sqlite3')) as db:
            return db.execute("SELECT COUNT(DISTINCT p.stop_id || ':' || IFNULL(p.unit, '')) FROM person p JOIN stop s ON s.id = p.stop_id "
                              "WHERE s.span_id = ? AND p.precinct = ?", (span, precinct)).fetchone()[0]

    def test_the_turf_holds_exactly_the_asked_doors_with_the_last_block_cut_to_fit(self):
        for asked in range(10, 18):
            turf = self.turf(asked)
            before = sum(self.doors_of(span) for span in turf['spans'][:-1])
            last = turf['spans'][-1]
            self.assertEqual((turf['stopped'], turf['doors']), ('full', asked), asked)
            self.assertLess(before, asked)
            if before + self.doors_of(last) == asked:
                self.assertIsNone(turf['trim'], asked)
            else:
                self.assertEqual(turf['trim'], {'span': last, 'kept': asked - before, 'of': self.doors_of(last)}, asked)

    def test_the_cut_block_keeps_the_people_of_its_doors_and_the_sums_of_them(self):
        keep = self.best_doors('rdg', 1)                         # 14 doors: rdg, the last block, keeps the one door worth most
        turf = self.turf(14)
        names = self.blocks_of_w()
        whole = [names[span] for span in turf['spans'][:-1]]
        people = sum(len(self.people_in(name, precinct=PRECINCT_W)) for name in whole) + len(self.people_at(keep))
        self.assertEqual((turf['spans'][-1], turf['trim'], turf['doors'], turf['people']), (7, {'span': 7, 'kept': 1, 'of': 4}, 14, people))
        value = sum(sum(self.door_values(name).values()) for name in whole) + self.door_values('rdg')[keep[0]]
        self.assertAlmostEqual(turf['value'], value, places=3)
        self.assertAlmostEqual(turf['undecided'] + turf['supporter'], turf['value'], places=3)
        self.assertLess(turf['value'], 3.83 + 0.75 + 0.375 + 1.5793)           # less than the whole of rdg would have added

    def test_the_cut_follows_the_weights(self):
        names = self.blocks_of_w()
        for weights in ({}, {'wp': '1', 'wi': '0'}, {'wp': '0', 'wi': '0'}, {'wp': '0.3', 'wi': '1'}):
            numbers = {name: float(text) for name, text in weights.items()}
            turf = self.turf(14, **weights)
            *whole, last = turf['spans']
            listed = self.trimmed(spans=','.join(map(str, turf['spans'])), doors='14', **weights)
            kept = turf['doors'] - sum(self.doors_of(span) for span in whole)
            expected = set().union(*(self.people_in(names[span], precinct=PRECINCT_W) for span in whole))
            expected |= self.people_at(self.best_doors(names[last], kept, **numbers))
            self.assertEqual(self.voters(listed), expected, weights)

    def test_a_block_whose_doors_are_all_done_is_not_in_the_turf(self):
        self.knock(*(f'p{n:02d}' for n in range(1, 13)))          # all of chd1, the best block of the precinct
        turf = self.turf(10)
        # Ridgeview Ct is best now (1.5793); Juniper Ln north meets it, then Cedar Heights Dr (1.1), then Juniper Ln south
        self.assertEqual((turf['spans'], turf['doors'], turf['stopped']),
                         ([7, self.span_of('jun_n1'), 6, self.span_of('jun_s')], 10, 'full'))

    def test_a_door_with_a_result_changes_the_turf_and_the_list_built_from_it_is_the_same_size(self):
        self.knock('p03')
        turf = self.turf(10)
        jun_s = self.span_of('jun_s')
        # chd1 6 doors, chd2 3, and Juniper Ln south would make it 11: it keeps its one door of more potential
        self.assertEqual((turf['spans'], turf['doors'], turf['people'], turf['trim']),
                         ([1, 6, jun_s], 10, 9 + 4 + 1, {'span': jun_s, 'kept': 1, 'of': 2}))
        picked = ','.join(map(str, turf['spans']))
        listed = self.list(spans=picked, precinct=PRECINCT_W, fresh='1', doors='10')
        self.assertEqual((listed['totals']['doors'], listed['totals']['voters']), (turf['doors'], turf['people']))
        self.assertGreater(listed['totals']['walk_m'], 0)
        self.assertEqual(listed['query'], {'spans': picked, 'precinct': PRECINCT_W, 'fresh': '1', 'doors': '10'})
        whole = self.list(spans=picked, precinct=PRECINCT_W, fresh='1')                  # without doors the blocks come whole
        self.assertEqual((whole['totals']['doors'], whole['totals']['voters']), (11, 15))
        stale = self.list(spans=picked, precinct=PRECINCT_W)                             # without fresh the done doors come back
        self.assertEqual((stale['totals']['doors'], stale['totals']['voters']), (12, 18))

    def test_the_list_built_from_a_turf_matches_its_doors_and_people(self):
        for asked in (10, 12, 14, 17):
            turf = self.turf(asked)
            listed = self.list(spans=','.join(map(str, turf['spans'])), precinct=PRECINCT_W, fresh='1', doors=str(asked))
            self.assertEqual((listed['totals']['doors'], listed['totals']['voters']), (turf['doors'], turf['people']), asked)
            self.assertGreater(listed['totals']['walk_m'], 0)

    def test_a_precinct_with_less_than_the_asked_doors_gives_all_it_has(self):
        spans = {self.span_of('pine_f')}
        turf = self.turf(10, precinct=PRECINCT_F)
        self.assertEqual((set(turf['spans']), turf['doors'], turf['people'], turf['stopped']), (spans, 2, 4, 'exhausted'))
        self.assertEqual(turf['jumps'], [])

    def test_the_turf_is_stopped_by_the_list_limits(self):
        with mock.patch.object(V, 'MAX_VOTERS', 14):                               # 14 doors asked: no more than the people ceiling
            turf = self.turf(14)
        self.assertEqual((turf['spans'], turf['people'], turf['stopped']), ([1], 12, 'cap'))              # chd2's four would make 16
        with mock.patch.object(V, 'MAX_PICK_SPANS', 3):
            turf = self.turf(17)
        self.assertEqual((len(turf['spans']), turf['stopped']), (3, 'cap'))

    def test_the_reach_of_a_jump_is_the_districts_setting(self):
        with override_settings(DISTRICT=lowered(turf={'doors': 60, 'jump_m': 123.0})), \
                mock.patch.object(V.walk_turf, 'turf', wraps=V.walk_turf.turf) as spy:
            self.turf(10)
        self.assertEqual(spy.call_args.args[3:], (123.0, V.MAX_VOTERS, V.MAX_PICK_SPANS))

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
            self.assertIn('That is 28 voters, too large to build in one list (at most 10).', message)
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
        self.knock('p03')                                    # doors with a result change nothing in a list that does not ask for fresh
        self.assertEqual(self.voters(self.list(spans='1,6', precinct=PRECINCT_W)), self.people_in('chd1', 'chd2', precinct=PRECINCT_W))

    # ---- fresh lists -----------------------------------------------------------------------------------------
    def test_a_fresh_list_leaves_out_the_people_at_doors_with_a_result(self):
        self.knock('p03')                                    # p03, p04 and p05 live at that door
        payload = self.list(spans='1,6', precinct=PRECINCT_W, fresh='1')
        self.assertEqual(self.voters(payload), self.people_in('chd1', 'chd2', precinct=PRECINCT_W) - set(self.ids('p03', 'p04', 'p05')))
        self.assertEqual(payload['query'], {'spans': '1,6', 'precinct': PRECINCT_W, 'fresh': '1'})
        self.assertEqual((payload['totals']['voters'], payload['totals']['doors']), (13, 9))
        plain = self.list(spans='1,6', precinct=PRECINCT_W)
        self.assertEqual((plain['totals']['voters'], plain['totals']['doors']), (16, 10))
        self.assertNotEqual(payload['fingerprint'], plain['fingerprint'])

    def test_a_fresh_list_with_nothing_to_leave_out_is_the_list_with_fresh_in_its_query(self):
        plain = self.list(spans='1,6')
        fresh = self.list(spans='1,6', fresh='1')
        self.assertEqual(fresh['query'], {'spans': '1,6', 'fresh': '1'})
        self.assertEqual(self.voters(fresh), self.voters(plain))
        self.assertEqual((fresh['totals'], fresh['runs']), (plain['totals'], plain['runs']))

    def test_fresh_works_for_streets_and_areas_too(self):
        self.knock('p01')                                    # the door at 1112: p01 and p02
        cedar = self.cedar()
        payload = self.list(streets='%d:both' % cedar, fresh='1')
        self.assertEqual(self.voters(payload), self.people_in('chd1', 'chd2', 'chd3') - set(self.ids('p01', 'p02')))
        self.assertEqual(payload['query'], {'streets': '%d:both' % cedar, 'fresh': '1'})
        area = self.list(ss='010', fresh='1')
        self.assertEqual(area['totals']['voters'], self.list(ss='010')['totals']['voters'] - 2)
        self.assertEqual(area['query'], {'ss': '010', 'fresh': '1'})

    def test_everyone_having_a_result_is_said_in_words(self):
        self.knock(*(f'p{n:02d}' for n in range(1, 13)))
        self.assertEqual(self.list(400, spans='1', fresh='1')['error'], 'Everyone on those blocks already has a recorded result.')
        self.assertEqual(self.list(400, spans='1', precinct=PRECINCT_W, fresh='1')['error'],
                         'Everyone on those blocks already has a recorded result.')
        self.assertEqual(self.list(spans='1')['totals']['voters'], 12)                     # without fresh they are all there
        self.assertEqual(self.list(400, ss='011', streets='%d:both' % self.cedar(), fresh='1')['error'],
                         'No voters live on those streets in this area.')                  # no one there at all: the older words

    def test_the_cap_counts_the_people_a_fresh_list_keeps(self):
        self.knock('p03')                                    # 16 people in precinct W on blocks 1 and 6, 13 once p03, p04, p05 are left out
        with mock.patch.object(V, 'MAX_VOTERS', 13):
            self.assertEqual(self.list(spans='1,6', precinct=PRECINCT_W, fresh='1')['totals']['voters'], 13)
            self.assertIn('That is 16 voters, too large to build in one list (at most 13).', self.list(400, spans='1,6', precinct=PRECINCT_W)['error'])
        with mock.patch.object(V, 'MAX_VOTERS', 10):
            self.assertIn('That is 13 voters, too large to build in one list (at most 10).',
                          self.list(400, spans='1,6', precinct=PRECINCT_W, fresh='1')['error'])

    def test_fresh_takes_the_value_1_and_nothing_else(self):
        for bad in ('0', '2', '', 'true', '1 ', '11'):
            self.list(400, spans='1', fresh=bad)
        self.get('/api/v3/walk/list?spans=1&fresh=1&fresh=1', 400)

    def test_a_fresh_list_saves_with_its_query_and_reloads_as_it_was_frozen(self):
        self.knock('p03')
        shown = self.list(spans='6,1', precinct=PRECINCT_W, fresh='1')
        saved = self.post('/api/v3/walk/lists', {'title': 'Fresh', 'query': {'spans': '6,1', 'precinct': PRECINCT_W, 'fresh': '1'},
                                                 'fingerprint': shown['fingerprint']}, 201)
        self.assertEqual((saved['query'], saved['changed']), ({'spans': '1,6', 'precinct': PRECINCT_W, 'fresh': '1'}, False))
        self.knock('p01')                                    # another door is done after the list was frozen: the saved list is as it was
        again = self.get('/api/v3/walk/lists/' + saved['list']['key'])
        self.assertEqual(self.voters(again), self.voters(shown))
        rebuilt = self.post('/api/v3/walk/lists', {'title': 'Again', 'query': saved['query'], 'fingerprint': shown['fingerprint']}, 201)
        self.assertTrue(rebuilt['changed'])                    # built again today without p01 and p02: not the list that was shown
        self.assertEqual(self.voters(rebuilt), self.voters(shown) - set(self.ids('p01', 'p02')))

    def test_a_query_to_save_may_carry_only_known_keys_and_fresh_is_one(self):
        shown = self.list(spans='1')
        for query in ({'spans': '1', 'fresh': '0'}, {'spans': '1', 'extra': '1'}, {'fresh': '1'}):
            self.post('/api/v3/walk/lists', {'title': 'x', 'query': query, 'fingerprint': shown['fingerprint']}, 400)

    # ---- a list cut to a door count --------------------------------------------------------------------------
    def trimmed(self, status=200, spans='1,6', **query):
        picked = {} if spans is None else {'spans': spans}
        return self.list(status, **{**picked, 'precinct': PRECINCT_W, 'fresh': '1', **query})

    def test_the_plan_and_the_list_agree_on_the_doors_and_people_whatever_the_count_the_weights_and_the_results(self):
        for knocked in ((), ('p03',), ('p20', 'p27')):
            self.knock(*knocked)                                       # each round adds to the doors with a result of the one before
            for asked in (10, 11, 13, 14, 17):
                for weights in ({}, {'wp': '1', 'wi': '0'}, {'wp': '0.3', 'wi': '1'}):
                    turf = self.turf(asked, **weights)
                    listed = self.trimmed(spans=','.join(map(str, turf['spans'])), doors=str(asked), **weights)
                    context = (knocked, asked, weights)
                    self.assertEqual((listed['totals']['doors'], listed['totals']['voters']), (turf['doors'], turf['people']), context)
                    self.assertLessEqual(turf['doors'], asked, context)
                    if turf['stopped'] == 'full':
                        self.assertEqual(turf['doors'], asked, context)

    def test_the_other_precinct_agrees_too(self):
        turf = self.turf(10, precinct=PRECINCT_F)
        listed = self.trimmed(spans=','.join(map(str, turf['spans'])), precinct=PRECINCT_F, doors='10')
        self.assertEqual((listed['totals']['doors'], listed['totals']['voters']), (2, 4))
        self.assertEqual((turf['doors'], turf['people'], turf['trim']), (2, 4, None))

    def test_a_single_block_is_cut_to_the_doors_of_most_potential(self):
        listed = self.trimmed(spans='1', doors='10')                               # seven doors, fewer than the count: the whole block
        self.assertEqual((listed['totals']['doors'], listed['totals']['voters']), (7, 12))
        self.assertEqual(self.voters(self.trimmed(spans='1', doors='10', wp='0.5', wi='0.5')), self.voters(listed))
        for keep in (6, 3, 1):
            turf, listed = self.turf(keep), self.trimmed(spans='1', doors=str(keep))
            kept = self.best_doors('chd1', keep)
            self.assertEqual((turf['spans'], turf['doors'], turf['trim']), ([1], keep, {'span': 1, 'kept': keep, 'of': 7}))
            self.assertEqual(self.voters(listed), self.people_at(kept), keep)
            self.assertEqual((listed['totals']['doors'], listed['totals']['voters']), (keep, len(self.people_at(kept))), keep)
            self.assertEqual((listed['totals']['doors'], listed['totals']['voters']), (turf['doors'], turf['people']), keep)

    def test_the_spans_are_read_in_the_order_given_and_the_one_that_passes_the_count_is_cut(self):
        chd1, chd2, rdg = (self.people_in(name, precinct=PRECINCT_W) for name in ('chd1', 'chd2', 'rdg'))
        listed = self.trimmed(spans='7,1,6', doors='10')                           # rdg 4 doors whole, chd1 would make 11: it keeps 6 of 7, chd2 left out
        self.assertEqual(listed['query'], {'spans': '7,1,6', 'precinct': PRECINCT_W, 'fresh': '1', 'doors': '10'})
        self.assertEqual(listed['totals']['doors'], 10)
        self.assertEqual(self.voters(listed), rdg | self.people_at(self.best_doors('chd1', 6)))
        other = self.trimmed(spans='1,6,7', doors='10')                            # chd1 7 + chd2 3 = 10: both whole, rdg left out
        self.assertEqual(other['query']['spans'], '1,6,7')
        self.assertEqual((other['totals']['doors'], self.voters(other)), (10, chd1 | chd2))
        self.assertEqual(self.voters(self.trimmed(spans='6,1,7', doors='10')), chd1 | chd2)
        middle = self.trimmed(spans='6,7,1', doors='10')                           # chd2 3, rdg 4, then chd1 keeps 3 of 7
        self.assertEqual(self.voters(middle), chd2 | rdg | self.people_at(self.best_doors('chd1', 3)))

    def test_street_picks_are_added_whole_on_top_of_the_doors_kept_from_the_blocks(self):
        juniper = self.get('/api/v3/walk/streets?q=juniper')['streets'][0]['id']
        for name, street in (('cedar', self.cedar()), ('juniper', juniper)):       # cedar holds doors the cut drops, juniper does not
            on_top = self.trimmed(spans='7,1,6', doors='10', streets='%d:both' % street)
            only_street = self.trimmed(streets='%d:both' % street, spans=None)
            blocks = self.trimmed(spans='7,1,6', doors='10')
            self.assertEqual(on_top['query'], {'streets': '%d:both' % street, 'spans': '7,1,6', 'precinct': PRECINCT_W, 'fresh': '1', 'doors': '10'}, name)
            self.assertEqual(self.voters(on_top), self.voters(only_street) | self.voters(blocks), name)
            self.assertEqual(on_top['totals']['voters'], len(self.voters(on_top)), name)
        cedar_people = self.people_in('chd1', 'chd2', 'chd3', precinct=PRECINCT_W)
        self.assertTrue(cedar_people <= self.voters(self.trimmed(spans='7,1,6', doors='10', streets='%d:both' % self.cedar())))   # the dropped door is back
        self.knock('p01')                                                           # fresh still applies to the streets
        on_top = self.trimmed(spans='7,1,6', doors='10', streets='%d:both' % self.cedar())
        self.assertEqual(self.voters(on_top) & set(self.ids('p01', 'p02')), set())
        shown = self.trimmed(spans='7,1,6', doors='10', streets='%d:both' % juniper)
        saved = self.post('/api/v3/walk/lists', {'title': 'On top', 'query': shown['query'], 'fingerprint': shown['fingerprint']}, 201)
        self.assertEqual((saved['query'], saved['changed']), (shown['query'], False))

    def test_a_span_named_twice_counts_once_and_the_blocks_after_the_count_is_met_are_left_out(self):
        listed = self.trimmed(spans='1,6,1,7,6', doors='10')
        self.assertEqual(listed['query']['spans'], '1,6,7')                        # each once, at its first place
        self.assertEqual(self.voters(listed), self.people_in('chd1', 'chd2', precinct=PRECINCT_W))

    def test_the_doors_are_the_potential_at_the_weights_sent(self):
        # rdg 4 doors and chd2 3 are whole, chd1 keeps 3 of its 7: its door 1 (undecided) leads at the defaults and wp 1, door 3 (supporter) at wp 0
        around = self.people_in('rdg', 'chd2', precinct=PRECINCT_W)
        sets = set()
        for weights in ({}, {'wp': '1', 'wi': '0'}, {'wp': '0', 'wi': '0'}, {'wp': '0.3', 'wi': '1'}):
            numbers = {name: float(text) for name, text in weights.items()}
            listed = self.trimmed(spans='7,6,1', doors='10', **weights)
            self.assertEqual(self.voters(listed), around | self.people_at(self.best_doors('chd1', 3, **numbers)), weights)
            sets.add(frozenset(self.voters(listed)))
        self.assertEqual(len(sets), 2)                                              # wp 0 keeps other doors of chd1 than the rest do

    def test_doors_with_a_recorded_result_are_not_among_the_doors_kept(self):
        self.knock('p03')                                    # the door of p03, p04, p05, the most valuable of chd1
        listed = self.trimmed(spans='1', doors='10')
        self.assertEqual((listed['totals']['doors'], listed['totals']['voters']), (6, 9))
        listed = self.trimmed(spans='7,6,1', doors='10')    # rdg 4 doors, chd2 3, then chd1 (6 doors left) keeps 3
        self.assertEqual(listed['totals']['doors'], 10)
        self.assertEqual(self.voters(listed) & set(self.ids('p03', 'p04', 'p05')), set())
        self.assertEqual(self.voters(listed), self.people_in('rdg', 'chd2', precinct=PRECINCT_W) | self.people_at(self.best_doors('chd1', 3)))

    def test_everyone_having_a_result_is_still_said_with_a_door_count(self):
        self.knock(*(f'p{n:02d}' for n in range(1, 13)))
        self.assertEqual(self.trimmed(400, spans='1', doors='10')['error'], 'Everyone on those blocks already has a recorded result.')

    def test_a_list_cut_to_a_door_count_saves_and_reloads_with_its_count_and_order(self):
        shown = self.trimmed(spans='7,1,6', doors='010', wp='1.0', wi='0.50')
        query = {'spans': '7,1,6', 'precinct': PRECINCT_W, 'fresh': '1', 'doors': '10', 'wp': '1', 'wi': '0.5'}
        self.assertEqual(shown['query'], query)                                      # the count and weights are written plainly
        saved = self.post('/api/v3/walk/lists', {'title': 'Cut', 'query': query, 'fingerprint': shown['fingerprint']}, 201)
        self.assertEqual((saved['query'], saved['changed']), (query, False))
        self.assertEqual(saved['fingerprint'], shown['fingerprint'])
        again = self.get('/api/v3/walk/lists/' + saved['list']['key'])
        self.assertEqual((again['query'], self.voters(again)), (query, self.voters(shown)))
        self.assertEqual(self.list(**query)['fingerprint'], shown['fingerprint'])    # the saved query rebuilds the same list
        self.knock('p01')                                                            # a result after the save: built again today it is another list
        rebuilt = self.post('/api/v3/walk/lists', {'title': 'Cut again', 'query': query, 'fingerprint': shown['fingerprint']}, 201)
        self.assertTrue(rebuilt['changed'])
        self.assertEqual(rebuilt['query'], query)

    def test_a_list_cut_to_a_door_count_needs_planned_blocks_in_one_precinct_with_fresh_and_a_district_with_a_candidate(self):
        needs = 'Trim needs planned blocks.'
        self.assertEqual(self.list(400, precinct=PRECINCT_W, fresh='1', doors='20')['error'], needs)                       # no spans
        self.assertEqual(self.list(400, ss='010', fresh='1', doors='20')['error'], needs)
        self.assertEqual(self.list(400, spans='1,6', precinct=PRECINCT_W, doors='20')['error'], needs)                     # no fresh
        self.assertEqual(self.list(400, spans='1,6', precinct=PRECINCT_W, doors='20', wp='0.5')['error'], needs)
        self.assertEqual(self.list(400, spans='1,6', precinct=PRECINCT_W, fresh='1', wp='0.5')['error'], needs)           # weights alone
        self.assertEqual(self.list(400, spans='1,6', precinct=PRECINCT_W, fresh='1', wi='0.5', wp='0')['error'], needs)
        self.assertEqual(self.list(400, streets='%d:both' % self.cedar(), precinct=PRECINCT_W, fresh='1', doors='20')['error'], needs)   # streets alone
        self.assertEqual(self.list(400, spans='1,6', fresh='1', doors='20')['error'], 'Plan a walk list from one precinct.')
        self.assertEqual(self.list(400, spans='1,6', ss='010', fresh='1', doors='20')['error'], 'Plan a walk list from one precinct.')
        self.assertEqual(self.trimmed(400, doors='0')['error'], 'Ask for at least 1 door.')
        for bad in ('1001', '5000'):
            self.assertEqual(self.trimmed(400, doors=bad)['error'], 'Ask for 1 to 1,000 doors.')
        for good in ('1', '9', '501', '1000'):
            self.assertEqual(self.trimmed(doors=good)['query']['doors'], good)
        for bad in ('x', '1000000', '', '-20', '2.5', ' 20', '20 '):
            self.assertEqual(self.trimmed(400, doors=bad)['error'], 'Invalid request.')
        for name, bad in (('wp', '1.5'), ('wi', '-1'), ('wp', ''), ('wi', 'x'), ('wp', '0.12345'), ('wi', '1e0'), ('wp', '2')):
            self.assertEqual(self.trimmed(400, doors='20', **{name: bad})['error'], 'Invalid request.')
        self.get('/api/v3/walk/list?spans=1&precinct=%s&fresh=1&doors=20&doors=30' % PRECINCT_W, 400)
        self.get('/api/v3/walk/list?spans=1&precinct=%s&fresh=1&doors=20&wp=0.5&wp=0.4' % PRECINCT_W, 400)
        with override_settings(DISTRICT=CO):
            self.assertEqual(self.trimmed(404, doors='20')['error'], 'No candidate is set for this district.')
            self.assertEqual(self.list(404, ss='010', doors='20')['error'], 'No candidate is set for this district.')
            self.assertEqual(self.list(404, spans='1', wp='0.5')['error'], 'No candidate is set for this district.')
            self.assertEqual(self.list(spans='1,6', precinct=PRECINCT_W, fresh='1')['query'], {'spans': '1,6', 'precinct': PRECINCT_W, 'fresh': '1'})

    def test_a_save_may_carry_a_door_count_only_where_a_list_may(self):
        shown = self.trimmed(spans='1,6')
        base = {'spans': '1,6', 'precinct': PRECINCT_W, 'fresh': '1'}
        for query in ({'spans': '1,6', 'precinct': PRECINCT_W, 'doors': '20'}, {**base, 'doors': 20}, {**base, 'doors': None},
                      {**base, 'doors': '0'}, {**base, 'doors': '10001'}, {**base, 'doors': '20', 'extra': '1'}):
            self.post('/api/v3/walk/lists', {'title': 'x', 'query': query, 'fingerprint': shown['fingerprint']}, 400)

    def test_a_list_cut_to_a_door_count_needs_the_support_scores(self):
        with override_settings(DATA_DIR=self.world_without_support('nosupport-trim')):
            self.assertEqual(self.trimmed(503, doors='20')['error'], 'Support is not built yet.')
        with mock.patch.object(W.Store, 'attach_scores', return_value=False):
            self.assertEqual(self.trimmed(503, doors='20')['error'], 'Support is not built yet.')

    def test_a_list_with_no_door_count_never_reads_the_potential(self):
        with mock.patch.object(V, 'left_out_to_fit', side_effect=AssertionError('trim path read')), \
                mock.patch.object(V.walk_turf, 'best_doors', side_effect=AssertionError('trim path read')):
            self.assertEqual(self.list(spans='1,6')['totals']['voters'], 20)
            self.assertEqual(self.trimmed()['totals']['voters'], 16)
            self.assertEqual(self.list(ss='010', fresh='1')['query'], {'ss': '010', 'fresh': '1'})

    def test_the_lists_without_a_door_count_are_what_they_were(self):
        # the digest covers the whole answer (query, totals, runs, doors, paths, voters and the map context) but the day it was built
        cedar = self.cedar()
        for query, golden in ((dict(spans='1,6', precinct=PRECINCT_W), GOLDEN['spans']),
                              (dict(spans='6,1', precinct=PRECINCT_W, fresh='1'), GOLDEN['fresh']),
                              (dict(streets='%d:both' % cedar), GOLDEN['streets']),
                              (dict(ss='010'), GOLDEN['area'])):
            self.assertEqual(digest(self.list(**query)), golden, query)

    # ---- voter profiles (tools/profiles.py) ------------------------------------------------------------------
    # The district's candidate is a Democrat. On spans 1 and 6 (Cedar Heights Dr, p01-p20; precinct W holds p01-p16) the coalition is
    # the unaffiliated and minor-party people (p05 p06 p08 p11 p16 p19), the Democrats with s of 2/3 or more (p07, s 1.0) and the
    # Republicans with s in the middle third (p01 p02, s 0.5; p18, s exactly 2/3). Left out: Democrats below 2/3 or with no s
    # (p03 p04 p09 p10 p13-p15 p20) and Republicans outside the middle third (p12 s 0; p17 s 0.2).
    COALITION = ('p01', 'p02', 'p05', 'p06', 'p07', 'p08', 'p11', 'p16', 'p18', 'p19')
    COALITION_W = ('p01', 'p02', 'p05', 'p06', 'p07', 'p08', 'p11', 'p16')            # those in precinct W

    def test_a_profile_of_all_is_no_profile_at_all(self):
        query = dict(spans='1,6', precinct=PRECINCT_W)
        plain, named = self.list(**query), self.list(profile='all', **query)
        self.assertEqual(json.dumps(named, sort_keys=True), json.dumps(plain, sort_keys=True))
        self.assertEqual(named['query'], {'spans': '1,6', 'precinct': PRECINCT_W})
        self.assertEqual(json.dumps(self.plan(profile='all'), sort_keys=True), json.dumps(self.plan(), sort_keys=True))
        self.assertNotIn('profile', self.plan(profile='all'))
        self.assertEqual(json.dumps(self.trimmed(doors='4', profile='all'), sort_keys=True),       # the door-count trim too
                         json.dumps(self.trimmed(doors='4'), sort_keys=True))

    def test_the_coalition_list_holds_the_supporters_and_persuadables(self):
        shown = self.list(spans='1,6', profile='coalition')
        self.assertEqual(self.voters(shown), set(self.ids(*self.COALITION)))
        self.assertEqual(shown['totals']['voters'], 10)
        self.assertEqual(shown['query'], {'spans': '1,6', 'profile': 'coalition'})
        self.assertEqual(self.voters(self.list(spans='1,6', precinct=PRECINCT_W, fresh='1', profile='coalition')),
                         set(self.ids(*self.COALITION_W)))
        whole = self.voters(self.list(ss='010', profile='coalition'))                 # the whole senate district: more people, same rule
        self.assertTrue(set(self.ids(*self.COALITION)) <= whole and not whole & set(self.ids('p03', 'p12', 'p13', 'p17', 'p20')))

    def test_the_coalition_list_by_street_pick_and_with_a_result_left_out(self):
        picked = self.voters(self.list(streets='%d:both' % self.cedar(), profile='coalition'))
        self.assertEqual(picked & set(self.ids(*self.COALITION)), set(self.ids(*self.COALITION)))
        self.assertFalse(picked & set(self.ids('p03', 'p12', 'p13', 'p17', 'p20')))
        self.knock('p01')                                                      # the door of p01 and p02
        self.assertEqual(self.voters(self.trimmed(profile='coalition')), set(self.ids(*self.COALITION_W)) - set(self.ids('p01', 'p02')))

    def test_the_plan_counts_only_the_people_of_the_profile(self):
        # chd1: p01 p02 p05 p06 p07 p08 p11 on 6 doors (p01 and p02 share one). undecided 0.8 + 0.6 = 1.4; supporter
        # 0.5 x (0.2 + 0.4) + 4 x 0.5 + 0 = 2.3; identify 2 (p01, p02). chd2: p16 p18 p19 on 3 doors, under the 5 a block needs.
        for preset, value in (('mixed', 1.4), ('turnout', 2.3), ('identify', 2.0)):
            row = self.by_span(self.plan(preset=preset, profile='coalition'))[1]
            self.assertEqual((row['people'], row['doors'], row['value']), (7, 6, value), preset)
        everyone = self.plan(preset='identify')
        self.assertEqual((self.by_span(everyone)[1]['people'], self.by_span(everyone)[1]['doors']), (12, 7))
        profiled = self.plan(preset='identify', profile='coalition')
        self.assertEqual(profiled['profile'], 'coalition')
        self.assertNotIn(6, self.by_span(profiled))
        self.assertIn(6, self.by_span(everyone))
        self.assertLess(profiled['matched']['people'], everyone['matched']['people'])

    def test_a_list_cut_to_a_door_count_cuts_the_blocks_of_the_profile(self):
        shown = self.trimmed(profile='coalition', doors='4')
        self.assertEqual(shown['totals']['doors'], 4)
        voters = self.voters(shown)
        self.assertLessEqual(voters, set(self.ids(*self.COALITION_W)))
        self.assertGreaterEqual(voters, set(self.ids('p01', 'p02', 'p05')))      # the doors of most potential

    def test_a_republican_candidate_gets_supportive_republicans_and_persuadable_democrats(self):
        # supporters p18 (s 2/3); persuadable Democrats p03 p04 (0.5) p13 p14 p15 (0.4) p20 (none, so 0.5); everyone unaffiliated
        want = set(self.ids('p18', 'p03', 'p04', 'p13', 'p14', 'p15', 'p20', 'p05', 'p06', 'p08', 'p11', 'p16', 'p19'))
        with override_settings(DISTRICT={**CANDIDATE, 'party': 'R'}):
            self.assertEqual(self.voters(self.list(spans='1,6', profile='coalition')), want)

    def test_a_bad_profile_is_refused(self):
        for value in ('nope', 'COALITION', 'all,coalition', '', 'coalition '):
            self.list(400, ss='010', profile=value)
            self.plan(400, profile=value)
        self.get('/api/v3/walk/list?ss=010&profile=all&profile=coalition', 400)
        for value in ('nope', 'COALITION', 'coalition '):                      # every unknown key, not only a lower-case one
            self.assertEqual(self.list(400, ss='010', profile=value)['error'], V.PROFILE_NOT_OFFERED, value)
            self.assertEqual(self.plan(400, profile=value)['error'], V.PROFILE_NOT_OFFERED, value)
        self.post('/api/v3/walk/lists', {'title': 'x', 'query': {'ss': '010', 'profile': 'nope'}, 'fingerprint': '0' * 64}, 400)

    def test_a_district_without_a_candidate_offers_only_all(self):
        with override_settings(DISTRICT=CO):
            self.assertEqual(self.list(400, ss='010', profile='coalition')['error'], V.PROFILE_NOT_OFFERED)
            self.assertEqual(self.list(ss='010', profile='all')['query'], {'ss': '010'})

    def test_a_profile_needs_the_support_scores_and_somebody_in_it(self):
        with override_settings(DATA_DIR=self.world_without_support('nosupport-profile')):
            self.assertEqual(self.list(503, ss='010', profile='coalition')['error'], 'Support is not built yet.')
            self.assertEqual(self.list(ss='010', profile='all')['totals']['voters'], self.list(ss='010')['totals']['voters'])
        with mock.patch.object(V.PR, 'member_fn', return_value=lambda code, s: False):
            self.assertEqual(self.list(400, spans='1,6', precinct=PRECINCT_W, profile='coalition')['error'], V.PROFILE_EMPTY)

    def test_a_block_with_nobody_in_the_profile_leaves_the_others_listed(self):
        # only p07 (a Democrat with s 1.0, on span 1) is kept: span 6 has nobody in the profile, and the list is still built
        with mock.patch.object(V.PR, 'member_fn', return_value=lambda code, s: code == 'D' and s is not None and s > 0.9):
            self.assertEqual(self.voters(self.list(spans='1,6', profile='coalition')), set(self.ids('p07')))
            self.assertEqual(self.voters(self.trimmed(doors='4', profile='coalition')), set(self.ids('p07')))
            self.assertEqual(self.plan(preset='identify', profile='coalition')['matched']['people'], 1)   # one door: not ranked

    def test_a_saved_list_keeps_its_profile(self):
        query = {'spans': '1,6', 'precinct': PRECINCT_W, 'profile': 'coalition'}
        shown = self.list(**query)
        saved = self.post('/api/v3/walk/lists', {'title': 'Coalition', 'query': query, 'fingerprint': shown['fingerprint']}, 201)
        self.assertEqual((saved['query'], saved['changed']), (query, False))
        key = saved['list']['key']
        self.assertEqual(WalkList.objects.get(key=key).query, query)
        self.assertEqual(self.voters(self.get('/api/v3/walk/lists/' + key)), set(self.ids(*self.COALITION_W)))
        plain = self.post('/api/v3/walk/lists', {'title': 'All', 'query': {**query, 'profile': 'all'},
                                                 'fingerprint': self.list(spans='1,6', precinct=PRECINCT_W)['fingerprint']}, 201)
        self.assertNotIn('profile', plain['query'])
        self.assertEqual(plain['totals']['voters'], 16)

    # ---- the printed scores ----------------------------------------------------------------------------------
    def test_a_candidate_district_gets_overall_participation_support_and_potential_as_whole_numbers(self):
        p01, p02, p05 = self.ids('p01', 'p02', 'p05')
        self.assertEqual(self.post('/api/v3/walk/scores', {'ids': [p01, p02, p05]}),
                         {'scores': {p01: {'ovr': 72, 'part': 80, 'sup': 63, 'push': 63},   # 71.5, 62.5 round up; push 0.5 x (0.8 + 0.1) x 1.4
                                     p02: {'ovr': 40, 'part': 60, 'sup': 50, 'push': 40},   # 0.5 x (0.6 + 0.2)
                                     p05: {'ovr': 50, 'part': 50, 'sup': 100, 'push': 30}}})   # a supporter who may not vote: 0.5 x 0.5 x 1.2

    def test_the_printed_potential_is_at_the_districts_default_weights_and_leaves_out_nobody(self):
        (p01,) = self.ids('p01')
        self.knock('p01')                                    # a person's own value is a property of the person
        self.assertEqual(self.post('/api/v3/walk/scores', {'ids': [p01]})['scores'][p01]['push'], 63)
        with override_settings(DISTRICT=lowered(potential={'wp': 1.0, 'wi': 0.0})):
            self.assertEqual(self.post('/api/v3/walk/scores', {'ids': [p01]})['scores'][p01]['push'], 80)   # undecided alone: 0.8

    def test_a_district_without_a_candidate_has_no_support_number(self):
        (p01,) = self.ids('p01')
        with override_settings(DISTRICT=CO), mock.patch.object(V.U, 'terms', side_effect=AssertionError('not read for Colorado')):
            self.assertEqual(self.post('/api/v3/walk/scores', {'ids': [p01]}), {'scores': {p01: {'ovr': 72, 'part': 80}}})

    def test_only_the_people_asked_for_and_known_come_back(self):
        (p19,) = self.ids('p19')
        body = self.post('/api/v3/walk/scores', {'ids': [p19, 'p_unknown', p19]})
        self.assertEqual(body, {'scores': {p19: {'ovr': 50, 'part': 50}}})            # no support score: no sup

    def test_the_scores_request_is_checked(self):
        for bad in ({}, {'ids': []}, {'ids': 'p_1'}, {'ids': [1]}, {'ids': ['a b']}, {'ids': [None]}, {'ids': ['x' * 81]},
                    {'ids': ['p_1'], 'extra': 1}):
            self.post('/api/v3/walk/scores', bad, 400)
        self.assertEqual(V.MAX_SCORE_IDS, 1500)                                       # a batch size, apart from the list's ceiling
        self.post('/api/v3/walk/scores', {'ids': ['p_%d' % i for i in range(V.MAX_SCORE_IDS)]})
        self.post('/api/v3/walk/scores', {'ids': ['p_%d' % i for i in range(V.MAX_SCORE_IDS + 1)]}, 400)
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
        return {'doors': doors, 'people': people, 'scored': people, 'push': push, 'mixed': mixed, 'turnout': 0, 'identify': 0, 'undecided': 0,
                'supporter': 0, 's_sum': 0, 's_n': 0, 't_sum': 0, 't_n': 0, 'len_m': len_m, 'street': 'Main St', 'block': block}

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

    def test_the_means_are_of_the_people_with_a_score_and_empty_without_any(self):
        spans = {1: {**self.span(), 's_sum': 1.5, 's_n': 3, 't_sum': 2.25, 't_n': 3}, 2: self.span()}
        rows, _ = V.rank_plan(spans, 'mixed', self.VALUE, self.ROI, None)
        by_span = {r['span']: r for r in rows}
        self.assertEqual((by_span[1]['s_mean'], by_span[1]['t_mean']), (0.5, 0.75))
        self.assertEqual((by_span[2]['s_mean'], by_span[2]['t_mean']), (None, None))
        self.assertNotIn('p_mean', by_span[1])

    def test_percentile_is_the_midrank_and_skips_missing(self):
        self.assertEqual(V.percentile({'a': 1, 'b': 2, 'c': 2, 'd': 3, 'e': None}), {'a': 0.125, 'b': 0.5, 'c': 0.5, 'd': 0.875})
        self.assertEqual(V.percentile({'a': None}), {})
        self.assertEqual(V.percentile({}), {})


class WordingTests(SimpleTestCase):
    def test_the_wording_passes_the_advisory_gate_and_the_preset_is_listed_first(self):
        self.assertGreater(FL.self_check(), 0)
        self.assertEqual(list(FL.ROI_PRESETS)[:2], ['push', 'mixed'])
        self.assertEqual(FL.ROI_PRESETS['push'][0], 'Needs a push')


class RealUntappedTests(WalkPlanTests):
    """The same world and the same hand-checked numbers, with the terms and the doors with a result read by untapped.py: the five
    turnout scores of a person are all their participation, so t is what the tests above set, and a result is posted to a saved list."""
    real = True

    def test_the_terms_are_read_from_the_score_store(self):
        terms = V.U.terms()
        np.testing.assert_array_equal(terms.s, self.terms.s)
        np.testing.assert_array_equal(terms.t, self.terms.t)
        np.testing.assert_array_equal(terms.i, self.terms.i)
        self.assertEqual(terms.t_source, {'sai': 38, 'record': 0, 'even': 0})

