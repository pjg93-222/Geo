"""Untapped potential (Paul 2026-10-09): the formula, the per-person terms, the doors left out because a result is recorded, the
area sums of GET /api/v3/untapped and the State House and Senate polygons. The world is walk_testkit's invented neighbourhood
(38 people, 27 doors, three precincts) with invented support scores, turnout literals and sphere-of-influence standings.

Hand-checked values (wp = wi = 0.5 unless stated; s support score / 100, t chance of voting, i influence standing):
  s .5, t 1, i 0 -> 0.5            s 1, t 0, i 1 -> 0.75 (the most)         s 0 -> 0           s 1, t 1 -> 0
  s .75, t .8, i .4 -> (0.2 + 0.075) x 1.2 = 0.33
  wp .3, wi 1, s .25, t .5, i 1 -> (0.075 + 0.0875) x 2 = 0.325
The third precinct (code 5200021417, State House 021) holds four people, summed by hand:
  p35 s .8, t .9 (literals 90 80 100 90 90), i .2     undecided 0.4 x 0.9 = 0.36        supporter 0.8 x 0.1 = 0.08
  p36 s .5 (no support), t 1 (5 of 5 generals), i 1   undecided 1                       supporter 0
  p37 s .2, t 1/3 (2 of 6 generals), i .5             undecided 0.4/3 = 0.133333        supporter 0.2 x 2/3 = 0.133333
  p38 s .5, t 1 (2 of 2 generals), no standing        undecided 1                       supporter 0
  sums: undecided 2.493333, supporter 0.213333, undecided x i 1.138667 (0.072 + 1 + 0.066667), supporter x i 0.082667.
"""
import copy
import gzip
import itertools
import json
import re
import shutil
import sqlite3
import tempfile
import time
import zlib
from contextlib import closing, contextmanager
from datetime import date
from pathlib import Path
from unittest import mock

import numpy as np
from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase, override_settings

from tools import build_walk as B
from tools import co_baseline
from tools import districts as D
from tools import field_labels as FL
from tools import geography_levels as GL

from . import scoresdb
from . import untapped as U
from . import v3_cache
from . import walk_store as W
from . import walk_testkit as T
from .models import Disposition, Snapshot, WalkList
from .terrier_contacts import SourceStoreError
from .v3_http import BadRequest

LOOPBACK = dict(HTTP_HOST='localhost')
CO, OR = D.get('co-05'), D.get('or-05')
CANDIDATE = {**CO, 'candidate': 'Test Candidate', 'party': 'D', 'race_mode': 'general',     # the Colorado-shaped world, with a candidate
             'support': copy.deepcopy(OR['support']), 'roi': copy.deepcopy(OR['roi'])}
SUPPORT_DDL = ('CREATE TABLE support (pos INTEGER PRIMARY KEY, sup_score REAL NOT NULL, sup_pct REAL NOT NULL, '
               'sup_rank INTEGER NOT NULL, sup_tie_n INTEGER NOT NULL, in_universe INTEGER NOT NULL)')
GRID = [k / 20 for k in range(21)]
WEIGHTS = (0.0, 0.25, 0.5, 0.75, 1.0)
PRECINCT_F = '5200021417'

# person key -> sup (support score, support standing), sai (the five turnout literals), soi (influence standing), exposure.
# Everyone not listed has no support row (s .5), no turnout literal (t from the general record) and no standing (i 0).
INPUTS = {
    'p01': dict(sup=(62.5, 40.0), sai=[96, 80, 84, 98, 79], soi=80.0),      # s .625 (not .4), t .874, i .8
    'p02': dict(sup=(70.0, 40.0), sai=[90] * 5, exposure=0.2),               # s .7 (not .4), t .9 (not the exposure .2)
    'p03': dict(sup=(0.0, 1.0), sai=[100] * 5, soi=100.0),                   # a sure opponent: 0
    'p04': dict(sup=(100.0, 99.0), sai=[100] * 5, soi=100.0),                # a sure supporter who always votes: 0
    'p05': dict(sup=(100.0, 99.0), sai=[0] * 5, soi=100.0),                  # a supporter who never votes, most influence: 0.75
    'p06': dict(sup=(50.0, 50.0), sai=[100] * 5, soi=0.0),                   # 0.5
    'p07': dict(sup=(75.0, 75.0), sai=[80] * 5, soi=40.0),                   # (0.5 x 0.5 x 0.8 + 0.5 x 0.75 x 0.2) x 1.2 = 0.33
    'p10': dict(sai=[70, 'x', None, 90, 50]),                                # three usable literals: t .7
    'p12': dict(sai=['n/a', None, None, None, None]),                        # none usable: the general record, 6 of 6
    'p35': dict(sup=(80.0, 80.0), sai=[90, 80, 100, 90, 90], soi=20.0),
    'p36': dict(soi=100.0),
    'p37': dict(sup=(20.0, 20.0), soi=50.0),
}
SAI_PEOPLE = 9                                                               # people with at least one usable literal
BLOB_PEOPLE = 10                                                             # people with an evidence record (p12's literals are unusable)


def add_inputs(stores):
    """Give the testkit's score store the area ids, the support table and the potential inputs of INPUTS."""
    rows = {r['person_id']: r['csv'] for r in stores.case.rows}
    with closing(sqlite3.connect(stores.directory / 'scores.sqlite3')) as db, db:
        for column in ('reg_block', 'reg_tract', 'reg_zip'):
            db.execute('ALTER TABLE person ADD COLUMN %s TEXT' % column)
        db.execute(SUPPORT_DDL)
        for person, csv in rows.items():
            db.execute('UPDATE person SET reg_block = ?, reg_tract = ?, reg_zip = ? WHERE person_id = ?',
                       (csv['svi_registration_censusid'], csv['svi_registration_censusid'][:11], csv['svi_registration_zip'], person))
        for key, entry in INPUTS.items():
            if 'sup' in entry:
                person = stores.expected['keys'][key]
                db.execute('INSERT INTO support VALUES ((SELECT pos FROM person WHERE person_id = ?), ?, ?, 1, 1, 1)',
                           (person, *entry['sup']))
    T.add_potential_inputs(stores.directory / 'scores.sqlite3', {
        stores.expected['keys'][key]: {'sai': entry.get('sai'), 'soi_pct': entry.get('soi'),
                                       **({'exposure': entry['exposure']} if 'exposure' in entry else {})}
        for key, entry in INPUTS.items()})


class FormulaTests(SimpleTestCase):
    def test_hand_examples(self):
        for (s, t, i), weights, want in (
                ((.5, 1, 0), {}, 0.5), ((1, 0, 1), {}, 0.75), ((1, 1, 1), {}, 0), ((1, 1, 0), {}, 0), ((.75, .8, .4), {}, 0.33),
                ((.25, .5, 1), dict(wp=.3, wi=1), 0.325), ((.5, 1, 1), {}, 0.75)):
            self.assertAlmostEqual(FL.potential(s, t, i, **weights), want, places=12, msg=(s, t, i, weights))
        self.assertTrue(all(FL.potential(0, t, i) == 0 for t in GRID for i in GRID))             # a sure opponent
        undecided, supporter = FL.potential_parts(.75, .8, .4)
        self.assertAlmostEqual(undecided, 0.24, places=12)
        self.assertAlmostEqual(supporter, 0.09, places=12)

    def test_the_weights_pick_a_part(self):
        for s, t in itertools.product(GRID, GRID):
            self.assertEqual(FL.potential(s, t, .6, wp=1, wi=0), (1 - abs(2 * s - 1)) * t)       # wp 1, wi 0: exactly the undecided part
            self.assertEqual(FL.potential(s, t, .6, wp=0, wi=0), s * (1 - t))                    # wp 0: exactly the supporter part

    def test_range_and_the_two_parts_add_up_on_a_grid(self):
        for s, t, i, wp, wi in itertools.product(GRID, GRID, GRID, WEIGHTS, WEIGHTS):
            parts = FL.potential_parts(s, t, i, wp, wi)
            value = FL.potential(s, t, i, wp, wi)
            self.assertEqual(sum(parts), value)
            self.assertTrue(min(parts) >= 0 and 0 <= value <= max(wp, 1 - wp) * (1 + wi) + 1e-12, (s, t, i, wp, wi, value))

    def test_arrays_equal_single_values(self):
        s, t, i = (np.array(v) for v in ([.1, .5, .9, 1.0], [.2, 1.0, .4, 0.0], [0, .3, 1, .5]))
        got = FL.potential(s, t, i, .3, .8)
        self.assertEqual(got.tolist(), [FL.potential(*row, .3, .8) for row in zip(s, t, i)])

    def test_the_label_and_formula_of_the_preset_pass_the_advisory_gate(self):
        label, formula = FL.ROI_PRESETS['push']
        self.assertEqual(label, 'Needs a push')
        self.assertTrue(all(word in formula for word in ('wP', 'wR', 'wI', 'recorded result')), formula)
        self.assertTrue(FL.self_check() > 0)
        self.assertEqual(FL.TURNOUT_FIELDS, tuple(FL.FIELDS[k]['name'] for k in range(17, 22)))
        self.assertTrue(all(FL.BY_NAME[name]['group'] == 'part' for name in FL.TURNOUT_FIELDS))

    def test_the_districts_carry_the_defaults_and_colorado_none(self):
        for district in (OR, D.get('ct-01')):
            roi = district['roi']
            self.assertEqual((roi['p'], roi['potential'], roi['turf']), ('turnout', {'wp': .5, 'wi': .5}, {'doors': 60, 'jump_m': 400.0}))
        self.assertIsNone(CO['roi'])


class WeightTests(SimpleTestCase):
    def parse(self, q, district=OR):
        with override_settings(DISTRICT=district):
            return U.parse_weights(q)

    def test_absent_weights_are_the_districts_defaults(self):
        self.assertEqual(self.parse({}), (.5, .5))
        self.assertEqual(self.parse({}, {**OR, 'roi': {**OR['roi'], 'potential': {'wp': .7, 'wi': .2}}}), (.7, .2))
        self.assertEqual(self.parse({}, CO), (.5, .5))                          # a district with no street plan: the module defaults

    def test_each_weight_alone_and_both(self):
        self.assertEqual(self.parse({'wp': '0.3'}), (.3, .5))
        self.assertEqual(self.parse({'wi': '1'}), (.5, 1.0))
        self.assertEqual(self.parse({'wp': '0', 'wi': '1.0'}), (0.0, 1.0))
        self.assertEqual(self.parse({'wp': '0.1234', 'wi': '0.0001'}), (.1234, .0001))

    def test_anything_outside_zero_to_one_with_four_decimals_is_refused(self):
        for text in ('1.5', '2', '-1', '-0.1', '.5', '0.12345', '1.0001', '1.', '', 'x', '0,5', ' 0.5', '0.5 ', '01', '+0.5', 'nan'):
            for name in ('wp', 'wi'):
                with self.assertRaises(BadRequest, msg=repr(text)):
                    self.parse({name: text})


def chance(x, a, b):
    """A smooth rising chance of voting, 1 / (1 + exp(-(a + b x))): the truth that the invented records are drawn from."""
    return 1 / (1 + np.exp(-(a + b * np.asarray(x, dtype=float))))


def binomial_people(rng, count, a, b, trials=6, truth=None):
    """count people: raw t a whole number of per cent on 0..1, 1..trials eligible generals, and a vote record drawn from the
    chance `truth(raw t)` (default: the smooth chance of (a, b)). Returns (x, voted, elig)."""
    x = rng.integers(0, 101, count) / 100
    elig = rng.integers(1, trials + 1, count)
    return x, rng.binomial(elig, chance(x, a, b) if truth is None else truth(x)), elig


def groups(*rows):
    """People in groups of equal raw t: rows of (raw t, votes of each person, generals of each person, people).
    Returns (x, voted, elig) arrays."""
    return tuple(np.concatenate([np.full(row[3], row[k], dtype=float) for row in rows]) for k in (0, 1, 2))


class FitTurnoutTests(SimpleTestCase):
    """fit_turnout: the general-election record made non-decreasing in the raw chance of voting by weighted pooled adjacent
    violators, as the points (block centre, block rate) of a curve; t is that curve at the raw t."""

    def test_the_pooling_by_hand_weights_by_eligible_generals(self):
        # 400 people at each raw t; rates .2 .7 .5 .8 .7. Generals per person 10, 10, 30, 10, 10 (votes 2, 7, 15, 8, 7).
        # .7 then .5 is a violation: pooled (7 + 15) / (10 + 30) = .55 at the centre (.2 x 10 + .3 x 30) / 40 = .275.
        # .8 then .7: pooled 15 / 20 = .75 at (.4 + .5) / 2 = .45. The first point stands alone.
        x, voted, elig = groups((.1, 2, 10, 400), (.2, 7, 10, 400), (.3, 15, 30, 400), (.4, 8, 10, 400), (.5, 7, 10, 400))
        centres, values = U.fit_turnout(x, voted, elig)
        np.testing.assert_allclose(centres, [.1, .275, .45], rtol=0, atol=1e-12)
        np.testing.assert_allclose(values, [.2, .55, .75], rtol=0, atol=1e-12)
        np.testing.assert_allclose(np.interp([0, .1, .275, .45, 1], centres, values), [.2, .2, .55, .75, .75], atol=1e-12)
        np.testing.assert_allclose(np.interp(.19375, centres, values), .2 + (.55 - .2) * (.19375 - .1) / (.275 - .1), atol=1e-12)

    def test_a_record_that_already_rises_keeps_one_point_per_raw_t(self):
        x, voted, elig = groups(*[(k / 10, k, 10, 250) for k in range(1, 10)])
        centres, values = U.fit_turnout(x, voted, elig)
        np.testing.assert_allclose(centres, np.arange(1, 10) / 10, atol=1e-12)
        np.testing.assert_allclose(values, np.arange(1, 10) / 10, atol=1e-12)

    def test_it_recovers_a_known_step(self):
        x = np.repeat(np.arange(101) / 100, 20)                                  # 2020 people, raw t 0 to 1
        voted, elig = np.where(x <= .5, 2.0, 9.0), np.full(len(x), 10.0)         # .2 up to .5, .9 above: exact, so the blocks are exact
        centres, values = U.fit_turnout(x, voted, elig)
        np.testing.assert_allclose(values, [.2, .9], atol=1e-12)
        np.testing.assert_allclose(centres, [.25, .755], atol=1e-12)             # the mean of 0..50 and of 51..100, per cent
        truth = lambda t: np.where(np.asarray(t) <= .5, .2, .9)
        for t in (0, .1, .25, .755, .9, 1):                                      # at and beyond the block centres, within 0.02
            self.assertAlmostEqual(float(np.interp(t, centres, values)), float(truth(t)), delta=0.02, msg=t)
        rng = np.random.default_rng(31)                                          # drawn records: away from the jump the curve is within 0.02
        x, voted, elig = binomial_people(rng, 200_000, 0, 0, truth=truth)
        centres, values = U.fit_turnout(x, voted, elig)
        away = np.array([t for t in np.arange(101) / 100 if abs(t - .5) >= .2])
        self.assertLessEqual(np.abs(np.interp(away, centres, values) - truth(away)).max(), 0.02)

    def test_it_recovers_a_known_smooth_curve_within_0_02_everywhere(self):
        rng = np.random.default_rng(20261009)
        for a, b in ((-1.5, 7.0), (-1.0, 4.0), (0.5, 2.0)):
            x, voted, elig = binomial_people(rng, 500_000, a, b, trials=20)
            centres, values = U.fit_turnout(x, voted, elig)
            grid = np.arange(101) / 100
            self.assertLessEqual(np.abs(np.interp(grid, centres, values) - chance(grid, a, b)).max(), 0.02, (a, b))

    def test_the_curve_never_turns_the_order_round_and_rises_strictly_between_its_points(self):
        for seed, (a, b, count) in enumerate(((-1.0, 4.0, 5000), (0.0, 0.5, 3000), (1.0, 0.1, 40_000))):    # the last two are mostly noise
            x, voted, elig = binomial_people(np.random.default_rng(seed), count, a, b, trials=3)
            centres, values = U.fit_turnout(x, voted, elig)
            self.assertTrue(np.all(np.diff(centres) > 0) and np.all(np.diff(values) > 0), (a, b))
            self.assertTrue(0 <= values[0] and values[-1] <= 1)
            grid = np.linspace(0, 1, 2001)
            curve = np.interp(grid, centres, values)
            self.assertTrue(np.all(np.diff(curve) >= 0))                                          # non-decreasing everywhere
            inside = (grid >= centres[0]) & (grid <= centres[-1])
            self.assertTrue(np.all(np.diff(curve[inside]) > 0))                                   # strictly rising between the points
            self.assertTrue(np.all(curve[grid < centres[0]] == values[0]) and np.all(curve[grid > centres[-1]] == values[-1]))

    def test_the_curve_matches_the_record_taken_together(self):
        x, voted, elig = binomial_people(np.random.default_rng(11), 40_000, -0.8, 3.5)
        centres, values = U.fit_turnout(x, voted, elig)
        self.assertAlmostEqual((elig * np.interp(x, centres, values)).sum() / elig.sum(), voted.sum() / elig.sum(), delta=0.005)

    def test_the_fit_needs_one_thousand_people(self):
        x, voted, elig = binomial_people(np.random.default_rng(3), 1000, -1.0, 4.0)
        self.assertIsNotNone(U.fit_turnout(x, voted, elig))
        self.assertIsNone(U.fit_turnout(x[:999], voted[:999], elig[:999]))
        self.assertIn('fewer than 1000 people', U._fit(x[:999], voted[:999], elig[:999])[1])
        self.assertEqual(U.FIT_MIN_PEOPLE, 1000)

    def test_a_record_that_does_not_rise_with_the_scores_is_left_alone(self):
        x, _, elig = binomial_people(np.random.default_rng(5), 3000, 0, 1)
        falling = np.random.default_rng(6).binomial(elig, .9 - .7 * x)           # pooled into one block
        flat = elig / 2                                                          # every record half: one block
        for label, voted in (('falling', falling), ('flat', flat), ('nobody voted', np.zeros(len(x))), ('everybody voted', elig.astype(float))):
            curve, why = U._fit(x, voted, elig)
            self.assertIsNone(curve, label)
            self.assertRegex(why, r'^fewer than 2 blocks', label)
            self.assertIsNone(U.fit_turnout(x, voted, elig), label)
        curve, why = U._fit(np.full(len(x), .6), falling, elig)                  # one raw t: one block
        self.assertIsNone(curve)
        self.assertRegex(why, r'^fewer than 2 blocks')

    def test_a_value_that_is_not_finite_is_refused(self):
        x, voted, elig = binomial_people(np.random.default_rng(9), 2000, -1.0, 4.0)
        at = np.arange(len(x)) == 0
        for label, args in (('a NaN raw t', (np.where(at, np.nan, x), voted, elig)), ('an infinite raw t', (np.where(at, np.inf, x), voted, elig)),
                            ('a NaN record', (x, np.where(at, np.nan, voted), elig)), ('infinite generals', (x, voted, np.where(at, np.inf, elig)))):
            curve, why = U._fit(*args)
            self.assertIsNone(curve, label)
            self.assertEqual(why, 'a turnout score or a record is not finite', label)
            self.assertIsNone(U.fit_turnout(*args), label)

    def test_more_votes_than_eligible_generals_count_as_all_of_them(self):
        x, voted, elig = binomial_people(np.random.default_rng(17), 2000, -1.0, 4.0)
        odd = np.arange(len(x)) % 2 == 1
        over = U.fit_turnout(x, voted + 3 * odd * elig, elig)
        capped = U.fit_turnout(x, np.where(odd, elig, voted), elig)
        np.testing.assert_array_equal(over[0], capped[0])
        np.testing.assert_array_equal(over[1], capped[1])

    def test_people_with_no_eligible_general_carry_no_weight(self):
        x, voted, elig = binomial_people(np.random.default_rng(19), 2000, -1.0, 4.0)
        base = U.fit_turnout(x, voted, elig)
        more = U.fit_turnout(np.append(x, [.33, .66]), np.append(voted, [0, 0]), np.append(elig, [0, 0]))
        np.testing.assert_array_equal(base[0], more[0])
        np.testing.assert_array_equal(base[1], more[1])


@override_settings(LOCAL_ADMIN_ACCESS=True, HOSTED=False, DEMO_MODE=False, DISTRICT=CANDIDATE)
class UntappedTests(TestCase):
    """The synthetic walk world with scores, support, turnout literals and standings, built once and read through the app."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.stores = T.write_minimal_stores(Path(cls.temporary.name) / 'world')
        if not B.build(cls.stores.source_csv, cls.stores.directory, force=True)['replaced']:
            raise AssertionError('The synthetic walk store was not written.')
        add_inputs(cls.stores)
        cls.expected = cls.stores.expected
        cls.override = override_settings(DATA_DIR=cls.stores.directory)
        cls.override.enable()

    @classmethod
    def tearDownClass(cls):
        U.reset()
        W.reset()
        cls.override.disable()
        cls.temporary.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.client = Client(**LOOPBACK)
        self.reset()
        self.addCleanup(self.reset)

    @staticmethod
    def reset():
        U.reset()
        W.reset()

    # ---- helpers ---------------------------------------------------------------------------------------------
    def person(self, key):
        return self.expected['keys'][key]

    def sqlite(self, name, sql, *args, folder=None):
        with closing(sqlite3.connect('file:%s?mode=ro' % ((folder or self.stores.directory) / name).as_posix(), uri=True)) as db:
            return db.execute(sql, args).fetchall()

    def pos(self, key, folder=None):
        return self.sqlite('scores.sqlite3', 'SELECT pos FROM person WHERE person_id = ?', self.person(key), folder=folder)[0][0]

    def voter_key(self, key):
        return self.sqlite('walk.sqlite3', 'SELECT voter_key FROM person WHERE person_id = ?', self.person(key))[0][0]

    def stop_of(self, key):
        return self.sqlite('walk.sqlite3', "SELECT stop_id, IFNULL(unit, '') FROM person WHERE person_id = ?", self.person(key))[0]

    def record(self, list_key, key, code, day=date(2026, 10, 1)):
        """A result of a visit: the append-only row the walk list API writes."""
        saved, _ = WalkList.objects.get_or_create(key=list_key, defaults=dict(title=list_key, payload={}, source_sha256='0' * 64))
        return Disposition.objects.create(walk_list=saved, person_id=self.person(key), voter_key=self.voter_key(key),
                                          code=code, contacted_on=day)

    def hand(self, key):
        """(s, t, i) of a person as the formula says, worked out from INPUTS and the hand tallies, not from the module."""
        entry = INPUTS.get(key, {})
        s = entry['sup'][0] / 100 if 'sup' in entry else 0.5
        numbers = [x / 100 for x in entry.get('sai') or [] if isinstance(x, int)]
        if numbers:
            t = sum(numbers) / len(numbers)
        else:
            record = self.expected['people'][self.person(key)]['gen']
            t = record['voted'] / record['eligible'] if record['eligible'] else 0.5
        return s, t, (entry.get('soi') or 0) / 100

    def sums(self, column, skip=()):
        """{area: [people, knocked, undecided, supporter, undecided_i, supporter_i]} over the 38 people by an expected column,
        leaving out the person keys in `skip` (counted as knocked)."""
        out = {}
        for pid, person in self.expected['people'].items():
            row = out.setdefault(person[column], [0, 0, 0.0, 0.0, 0.0, 0.0])
            if person['key'] in skip:
                row[1] += 1
                continue
            s, t, i = self.hand(person['key'])
            undecided, supporter = (1 - abs(2 * s - 1)) * t, s * (1 - t)
            row[0] += 1
            row[2:] = [row[2] + undecided, row[3] + supporter, row[4] + undecided * i, row[5] + supporter * i]
        return out

    def get(self, level, status=200, **headers):
        response = self.client.get('/api/v3/untapped?level=' + level, **headers)
        self.assertEqual(response.status_code, status, response.content[:200])
        return response

    def body(self, level):
        return json.loads(self.get(level).content)

    def assert_sums(self, body, want, ids=None):
        self.assertEqual(body['ids'], ids or sorted(want))
        for k, ident in enumerate(body['ids']):
            row = want[ident]
            self.assertEqual((body['people'][k], body['knocked'][k]), (row[0], row[1]), ident)
            for name, value in zip(('undecided', 'supporter', 'undecided_i', 'supporter_i'), row[2:]):
                self.assertAlmostEqual(body[name][k], value, places=5, msg=(ident, name))

    @contextmanager
    def world(self, scores=(), walk=(), drop=(), delete_walk=False):
        """A copy of the world with SQL run on its score and walk stores (and files dropped), served in place of it."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            folder = Path(temporary) / 'copy'
            shutil.copytree(self.stores.directory, folder)
            for name, statements in (('scores.sqlite3', scores), ('walk.sqlite3', walk)):
                with closing(sqlite3.connect(folder / name)) as db, db:
                    for sql in statements:
                        db.execute(sql)
            for name in drop:
                (folder / name).unlink()
            with override_settings(DATA_DIR=folder):
                self.reset()
                try:
                    yield folder
                finally:
                    self.reset()

    # ---- terms -----------------------------------------------------------------------------------------------
    def test_turnout_is_the_mean_of_the_five_literals_not_the_exposure_and_support_is_the_score(self):
        terms = U.terms()
        at = lambda key: self.pos(key) - 1
        self.assertAlmostEqual(terms.t[at('p01')], 0.874, places=12)                    # literals 96 80 84 98 79
        self.assertAlmostEqual(terms.t[at('p02')], 0.9, places=12)                      # literals 90, exposure 0.2: t is not the exposure
        self.assertEqual(self.sqlite('scores.sqlite3', 'SELECT sup_pct FROM support WHERE pos = ?', self.pos('p02'))[0][0], 40.0)
        self.assertAlmostEqual(terms.s[at('p02')], 0.7, places=12)                      # sup_score 70, sup_pct 40: s is the score
        self.assertAlmostEqual(terms.s[at('p01')], 0.625, places=12)
        self.assertAlmostEqual(terms.t[at('p10')], 0.7, places=12)                      # 70, 90 and 50 count; 'x' and a missing code do not
        self.assertAlmostEqual(terms.i[at('p01')], 0.8, places=12)                      # soi_pct 80
        self.assertEqual((terms.i[at('p02')], terms.i[at('p10')]), (0.0, 0.0))          # no standing: 0

    def test_without_a_support_row_s_is_even_odds_and_the_support_score_clamps_to_zero_one(self):
        terms = U.terms()
        self.assertEqual(terms.s[self.pos('p08') - 1], 0.5)
        self.assertEqual(terms.s[self.pos('p03') - 1], 0.0)
        self.assertEqual(terms.s[self.pos('p04') - 1], 1.0)

    def test_without_literals_t_is_the_general_record_and_even_odds_when_none_was_eligible(self):
        terms = U.terms()
        record = lambda key: self.expected['people'][self.person(key)]['gen']
        self.assertEqual(record('p25'), {'voted': 0, 'eligible': 0})
        self.assertEqual(terms.t[self.pos('p25') - 1], 0.5)                             # registered in 2025: no eligible general
        self.assertAlmostEqual(terms.t[self.pos('p23') - 1], 2 / 3, places=12)           # 2 of 3
        self.assertEqual(terms.t[self.pos('p12') - 1], 1.0)                              # an unusable literal: 6 of 6
        with self.world(walk=["UPDATE person SET gen_voted = 3, gen_elig = 4 WHERE person_id = '%s'" % self.person('p29')]):
            self.assertEqual(U.terms().t[self.pos('p29') - 1], 0.75)                    # 3 of 4

    def test_t_source_counts_where_each_t_came_from(self):
        terms = U.terms()
        self.assertEqual(terms.t_source, {'sai': SAI_PEOPLE, 'record': 28, 'even': 1})
        self.assertIsNone(terms.t_fit)                                                   # the small world keeps the raw t
        self.assertEqual(sum(terms.t_source.values()), terms.n)
        self.assertEqual(terms.n, len(self.expected['keys']))

    def test_every_person_matches_the_formula_and_the_arrays_are_read_only(self):
        terms = U.terms()
        for key in self.expected['keys']:
            s, t, i = self.hand(key)
            at = self.pos(key) - 1
            self.assertEqual((round(terms.s[at], 9), round(terms.t[at], 9), round(terms.i[at], 9)), (round(s, 9), round(t, 9), round(i, 9)), key)
            self.assertAlmostEqual(terms.undecided[at], (1 - abs(2 * s - 1)) * t, places=12)
            self.assertAlmostEqual(terms.supporter[at], s * (1 - t), places=12)
        for array in (terms.s, terms.t, terms.i, terms.undecided, terms.supporter):
            with self.assertRaises(ValueError):
                array[0] = 0.0
        self.assertIs(U.terms(), terms)                                                  # cached while the files stand

    def test_the_hand_examples_through_the_stored_terms(self):
        terms = U.terms()
        value = lambda key: float(FL.potential(*(terms.s[self.pos(key) - 1], terms.t[self.pos(key) - 1], terms.i[self.pos(key) - 1])))
        self.assertAlmostEqual(value('p06'), 0.5, places=12)
        self.assertAlmostEqual(value('p05'), 0.75, places=12)
        self.assertAlmostEqual(value('p07'), 0.33, places=12)
        self.assertEqual((value('p03'), value('p04')), (0.0, 0.0))

    def test_a_rebuilt_store_is_read_again_and_a_missing_one_is_a_503_error(self):
        first = U.terms()
        with self.world(scores=["UPDATE support SET sup_score = 10 WHERE pos = %d" % self.pos('p02')]):
            self.assertAlmostEqual(U.terms().s[self.pos('p02') - 1], 0.1)
            self.assertIsNot(U.terms(), first)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as empty, override_settings(DATA_DIR=Path(empty)):
            self.reset()
            with self.assertRaises(SourceStoreError) as caught:
                U.terms()
            self.assertEqual(caught.exception.status, 503)

    def test_a_store_with_no_walk_store_and_no_evidence_falls_to_even_odds(self):
        with self.world(scores=['DROP TABLE evidence', 'DROP TABLE value_dict'], drop=['walk.sqlite3']):
            terms = U.terms()
            self.assertEqual(terms.t_source, {'sai': 0, 'record': 0, 'even': terms.n})
            self.assertTrue(np.all(terms.t == 0.5))
            self.assertEqual(U.knocked().doors, frozenset())
            self.assertEqual(self.body('precinct')['t_source'], {'sai': 0, 'record': 0, 'even': 38})

    def test_the_testkit_helper_writes_440_byte_blobs_with_codes_that_are_not_the_values(self):
        pos = self.pos('p01')
        blob = self.sqlite('scores.sqlite3', 'SELECT blob FROM evidence WHERE pos = ?', pos)[0][0]
        evidence = scoresdb.decode_evidence(blob)
        fids = [FL.BY_NAME[name]['fid'] for name in FL.TURNOUT_FIELDS]
        literals = {fid: dict(self.sqlite('scores.sqlite3', 'SELECT code, literal FROM value_dict WHERE fid = ?', fid)) for fid in fids}
        self.assertEqual([literals[fid][int(evidence['values'][fid])] for fid in fids], ['96', '80', '84', '98', '79'])
        self.assertNotEqual([int(evidence['values'][fid]) for fid in fids], [96, 80, 84, 98, 79])
        self.assertTrue(all(float(evidence['exposure'][fid]) == np.float32(T.DECOY_EXPOSURE) for fid in fids))
        self.assertEqual(self.sqlite('scores.sqlite3', 'SELECT COUNT(*) FROM evidence')[0][0], BLOB_PEOPLE)
        self.assertEqual(len(zlib.decompress(blob)), 440)

    # ---- R2: doors with a recorded result ----------------------------------------------------------------------
    def test_nothing_is_knocked_before_any_result(self):
        left = U.knocked()
        self.assertEqual((U.results_version(), left.version, left.doors, left.people), ('0-0', '0-0', frozenset(), 0))
        self.assertFalse(left.pos.any())

    def test_a_result_leaves_out_the_voter_and_the_housemates_at_the_door_but_not_another_unit_of_the_stop(self):
        self.assertEqual(self.stop_of('p19')[0], self.stop_of('p20')[0])                 # 1285: one stop
        self.assertEqual((self.stop_of('p19')[1], self.stop_of('p20')[1]), ('Apt A', 'Apt B'))   # two doors
        self.record('a', 'p19', 'S')
        left = U.knocked()
        self.assertEqual(left.doors, frozenset({self.stop_of('p19')}))
        self.assertEqual([k for k in self.expected['keys'] if left.pos[self.pos(k) - 1]], ['p19'])
        self.assertEqual(self.stop_of('p03'), self.stop_of('p04'))                      # 1132: three people at one door
        self.assertEqual(self.stop_of('p03'), self.stop_of('p05'))
        self.record('a', 'p03', 'NH')
        left = U.knocked()
        self.assertEqual(sorted(k for k in self.expected['keys'] if left.pos[self.pos(k) - 1]), ['p03', 'p04', 'p05', 'p19'])
        self.assertEqual((left.people, len(left.doors), left.version), (4, 2, '2-2'))

    def test_a_result_cleared_on_the_same_list_is_not_knocked(self):
        self.record('a', 'p19', 'S')
        self.record('a', 'p19', None)
        left = U.knocked()
        self.assertEqual((left.people, left.doors, left.version), (0, frozenset(), '2-2'))

    def test_a_cleared_result_on_one_list_does_not_hide_a_standing_one_on_another(self):
        self.record('a', 'p19', 'S')
        self.record('a', 'p19', None)
        self.record('b', 'p19', 'NH')
        self.assertEqual(U.knocked().people, 1)
        self.record('b', 'p19', None)
        self.assertEqual(U.knocked().people, 0)
        self.record('a', 'p19', 'MV')                                                    # a later result on the first list stands again
        self.assertEqual(U.knocked().people, 1)

    def test_every_code_counts_and_an_unknown_voter_is_ignored(self):
        codes = (('p01', 'S'), ('p07', 'U'), ('p13', 'O'), ('p21', 'NH'), ('p24', 'MV'))
        for key, code in codes:
            self.record('a', key, code)
        saved = WalkList.objects.get(key='a')
        Disposition.objects.create(walk_list=saved, person_id='p_gone', voter_key='f' * 64, code='S', contacted_on=date(2026, 10, 1))
        left = U.knocked()
        self.assertEqual(left.version, '6-6')
        doors = {self.expected['people'][self.person(key)]['door'] for key, _ in codes}
        self.assertEqual(sorted(k for k in self.expected['keys'] if left.pos[self.pos(k) - 1]),
                         sorted(p['key'] for p in self.expected['people'].values() if p['door'] in doors))
        self.assertEqual(len(left.doors), 5)

    def test_a_knocked_voter_with_no_stop_is_left_out_alone(self):
        with self.world(walk=["UPDATE person SET stop_id = NULL WHERE person_id = '%s'" % self.person('p36')]):
            self.record('a', 'p36', 'S')
            left = U.knocked()
            self.assertEqual(left.doors, frozenset())
            self.assertEqual([k for k in self.expected['keys'] if left.pos[self.pos(k) - 1]], ['p36'])      # p37 shares the door, not the stop
            self.assertEqual(left.people, 1)

    def test_the_results_version_follows_saved_and_deleted_results(self):
        self.record('a', 'p19', 'S')
        self.record('b', 'p20', 'U')
        self.assertEqual(U.results_version(), '2-2')
        WalkList.objects.get(key='b').delete()                                           # the list's results go with it
        self.assertEqual(U.results_version(), '1-1')
        self.assertEqual(U.knocked().people, 1)

    # ---- the endpoint ------------------------------------------------------------------------------------------
    def test_the_precinct_sums_equal_the_hand_sums(self):
        body = self.body('precinct')
        self.assertEqual((body['schema'], body['level']), ('v3-untapped-1', 'precinct'))
        self.assert_sums(body, self.sums('precinct'))
        self.assertEqual(body['ids'], ['5200021301', '5200021302', PRECINCT_F])
        self.assertEqual(body['labels'], ['Precinct 301', 'Precinct 302', 'Precinct 417'])
        self.assertEqual(sum(body['people']), 38)
        self.assertEqual(body['defaults'], {'wp': 0.5, 'wi': 0.5})
        self.assertEqual(body['t_source'], {'sai': SAI_PEOPLE, 'record': 28, 'even': 1})
        self.assertEqual(body['results'], {'version': '0-0', 'people_knocked': 0})
        self.assertIsNone(body['t_calibration'])                                         # 38 people are too few to line t up with the record
        self.assertEqual(list(body), ['schema', 'level', 'ids', 'labels', 'people', 'knocked', 'undecided', 'supporter',
                                      'undecided_i', 'supporter_i', 'defaults', 't_source', 't_calibration', 'results'])

    def test_the_third_precinct_by_hand(self):
        body = self.body('precinct')
        k = body['ids'].index(PRECINCT_F)
        self.assertEqual((body['people'][k], body['knocked'][k]), (4, 0))
        for name, want in (('undecided', 2.493333), ('supporter', 0.213333), ('undecided_i', 1.138667), ('supporter_i', 0.082667)):
            self.assertEqual(body[name][k], want, name)                                  # rounded to six places

    def test_the_weights_are_applied_late_from_the_four_sums(self):
        body = self.body('precinct')
        k = body['ids'].index(PRECINCT_F)
        for wp, wi in ((0.5, 0.5), (1.0, 0.0), (0.0, 1.0), (0.3, 0.8)):
            value = wp * (body['undecided'][k] + wi * body['undecided_i'][k]) + (1 - wp) * (body['supporter'][k] + wi * body['supporter_i'][k])
            want = sum(float(FL.potential(*self.hand(key), wp, wi)) for key in ('p35', 'p36', 'p37', 'p38'))
            self.assertAlmostEqual(value, want, places=5, msg=(wp, wi))

    def test_state_house_and_senate_ids_are_the_fips_and_three_digits(self):
        house, senate = self.body('state_house'), self.body('state_senate')
        self.assertEqual(house['ids'], ['08018', '08019', '08021'])
        self.assertEqual(senate['ids'], ['08010', '08011'])
        self.assertEqual(house['labels'], ['Colorado House District 18', 'Colorado House District 19', 'Colorado House District 21'])
        self.assertEqual(senate['labels'], ['Colorado Senate District 10', 'Colorado Senate District 11'])
        self.assert_sums(house, {'08' + k.zfill(3): v for k, v in self.sums('sh').items()})
        self.assert_sums(senate, {'08' + k.zfill(3): v for k, v in self.sums('ss').items()})
        self.assertEqual(house['people'], [self.sums('precinct')[p][0] for p in ('5200021301', '5200021302', PRECINCT_F)])

    def test_block_tract_and_zip_levels_sum_the_registration_ids_of_the_score_store(self):
        rows = {r['person_id']: r['csv'] for r in self.stores.case.rows}
        for level, ident in (('block', lambda c: c['svi_registration_censusid']), ('tract', lambda c: c['svi_registration_censusid'][:11]),
                             ('zip', lambda c: c['svi_registration_zip'])):
            want = {}
            for pid, person in self.expected['people'].items():
                row = want.setdefault(ident(rows[pid]), [0, 0, 0.0, 0.0, 0.0, 0.0])
                s, t, i = self.hand(person['key'])
                undecided, supporter = (1 - abs(2 * s - 1)) * t, s * (1 - t)
                row[0] += 1
                row[2:] = [row[2] + undecided, row[3] + supporter, row[4] + undecided * i, row[5] + supporter * i]
            body = self.body(level)
            self.assert_sums(body, want)
            self.assertEqual(len(body['labels']), len(body['ids']))
        self.assertEqual(self.body('zip')['labels'], ['ZIP ' + z for z in self.body('zip')['ids']])

    def test_a_person_without_an_area_is_in_no_area(self):
        with self.world(scores=["UPDATE person SET reg_precinct = NULL WHERE person_id = '%s'" % self.person('p35')],
                        walk=["UPDATE person SET sh = '' WHERE person_id = '%s'" % self.person('p35')]):
            body = self.body('precinct')
            self.assertEqual(sum(body['people']), 37)
            self.assertEqual(sum(self.body('state_house')['people']), 37)
            self.assertNotIn('', body['ids'])

    def test_a_result_changes_the_version_the_sums_and_the_etag(self):
        before = self.get('precinct')
        self.assertEqual(json.loads(before.content)['results']['version'], '0-0')
        self.record('a', 'p35', 'S')                                                     # p35 and p38 share door d26
        after = self.get('precinct')
        self.assertNotEqual(before['ETag'], after['ETag'])
        body = json.loads(after.content)
        self.assertEqual(body['results'], {'version': '1-1', 'people_knocked': 2})
        k = body['ids'].index(PRECINCT_F)
        self.assertEqual((body['people'][k], body['knocked'][k]), (2, 2))
        for name, want in (('undecided', 1.133333), ('supporter', 0.133333), ('undecided_i', 1.066667), ('supporter_i', 0.066667)):
            self.assertEqual(body[name][k], want, name)
        self.assert_sums(body, self.sums('precinct', skip=('p35', 'p38')))
        self.assertEqual(sum(body['knocked']), 2)
        self.assert_sums(self.body('state_house'), {'08' + k.zfill(3): v for k, v in self.sums('sh', skip=('p35', 'p38')).items()})
        self.assertEqual(self.get('precinct')['ETag'], after['ETag'])                    # stable while nothing changes

    def test_the_response_revalidates_and_is_conditional_and_gzipped(self):
        first = self.get('precinct')
        self.assertEqual((first['Cache-Control'], first['Content-Type']), ('private, no-cache', 'application/json'))
        self.assertTrue(first['ETag'].startswith('"') and not first['ETag'].startswith('W/'))
        again = self.get('precinct', status=304, HTTP_IF_NONE_MATCH=first['ETag'])
        self.assertEqual(again.content, b'')
        zipped = self.get('precinct', HTTP_ACCEPT_ENCODING='gzip')
        self.assertEqual(zipped['Content-Encoding'], 'gzip')
        self.assertEqual(json.loads(gzip.decompress(zipped.content)), json.loads(first.content))
        self.get('precinct', status=304, HTTP_ACCEPT_ENCODING='gzip', HTTP_IF_NONE_MATCH=zipped['ETag'])
        self.assertNotEqual(zipped['ETag'], first['ETag'])

    def test_refusals(self):
        for url in ('/api/v3/untapped', '/api/v3/untapped?level=county', '/api/v3/untapped?level=', '/api/v3/untapped?level=block&level=zip',
                    '/api/v3/untapped?level=block&wp=0.5', '/api/v3/untapped?level=Precinct'):
            self.assertEqual(self.client.get(url).status_code, 400, url)
        self.assertEqual(self.client.post('/api/v3/untapped?level=precinct').status_code, 405)
        self.assertEqual(Client(HTTP_HOST='localhost', REMOTE_ADDR='192.0.2.10').get('/api/v3/untapped?level=precinct').status_code, 401)

    def test_a_district_with_no_candidate_gets_404_whatever_the_level(self):
        with override_settings(DISTRICT=CO):
            for level in ('precinct', 'nonsense'):
                response = self.client.get('/api/v3/untapped?level=' + level)
                self.assertEqual((response.status_code, response.json()), (404, {'error': 'No candidate is set for this district.'}))

    def test_missing_stores_are_a_503(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as empty, override_settings(DATA_DIR=Path(empty)):
            self.reset()
            response = self.client.get('/api/v3/untapped?level=precinct')
            self.assertEqual((response.status_code, response.json()), (503, {'error': 'The local score store has not been built.'}))

    def test_state_levels_need_the_walk_store_and_the_others_do_not(self):
        with self.world(drop=['walk.sqlite3']):
            self.assertEqual(self.get('precinct').status_code, 200)
            response = self.client.get('/api/v3/untapped?level=state_house')
            self.assertEqual(response.status_code, 503)

    def test_the_warm_step_builds_the_terms_and_says_where_t_came_from(self):
        from .management.commands import serve
        info = mock.Mock()
        with self.assertLogs('cu.warm', level='INFO') as logs:
            serve.warm_untapped(info, time.time())
        info.push.assert_called_once_with()
        self.assertIn('warm: untapped terms ready (38 people; t from the turnout scores %d, from the general record 28, even odds 1; '
                      '0 people at doors with a result)' % SAI_PEOPLE, logs.output[0])
        with mock.patch.object(U, 'terms', side_effect=SourceStoreError('The local score store has not been built.')),                 self.assertLogs('cu.warm', level='INFO') as logs:
            serve.warm_untapped(info, time.time())                                 # a failure is logged, never raised
        self.assertIn('warm: untapped not ready (The local score store has not been built.)', logs.output[0])

    def test_the_new_route_stays_out_of_the_colorado_gate_and_before_the_catch_all(self):
        from django.urls import resolve
        from . import v3_area_views as v3a
        self.assertIs(resolve('/api/v3/untapped').func, v3a.untapped)
        self.assertFalse([t for _, t in co_baseline.ENDPOINTS if 'untapped' in t or 'state_house' in t or 'state_senate' in t])

    def test_untapped_is_a_features_layer_for_a_candidate_store_with_support_only(self):
        info = mock.patch.object(scoresdb, 'info', return_value=mock.Mock(meta={'support': {}}))
        with info, tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as empty, override_settings(DATA_DIR=Path(empty)):
            self.assertEqual(self.client.get('/api/v3/features').json()['overlay']['layers'], ['ovr', 'cua', 'part', 'soi', 'lean', 'sup', 'untapped'])
        with mock.patch.object(scoresdb, 'info', return_value=mock.Mock(meta={})), override_settings(DATA_DIR=self.stores.directory):
            self.assertEqual(self.client.get('/api/v3/features').json()['overlay']['layers'], ['ovr', 'cua', 'part', 'soi', 'lean'])
        with override_settings(DISTRICT=CO), mock.patch.object(scoresdb, 'info', return_value=mock.Mock(meta={'support': {}})):
            self.assertNotIn('untapped', self.client.get('/api/v3/features').json()['overlay']['layers'])


PRECINCTS = ('5200021301', '5200021302')
CALIBRATED = re.compile(r'chance of voting calibrated against the general-election record: n=(\d+), (\d+) points, '
                        r'mean (\d\.\d{4}) -> (\d\.\d{4}) \(record (\d\.\d{4})\)')


def write_fit_world(directory, literals, voted, elig):
    """A score store (person, evidence, value_dict) and a walk store (gen_voted, gen_elig) of len(literals) invented people.
    literals[k] is the whole number held by all five turnout literals of person k, so raw t = literals[k] / 100; None gives the
    person no evidence record (no raw t)."""
    directory.mkdir(parents=True, exist_ok=True)
    ids = ['f%05d' % k for k in range(len(literals))]
    with closing(sqlite3.connect(directory / 'scores.sqlite3')) as db, db:
        db.execute('CREATE TABLE person (pos INTEGER PRIMARY KEY, person_id TEXT UNIQUE NOT NULL, reg_precinct TEXT)')
        db.executemany('INSERT INTO person VALUES (?,?,?)', [(k + 1, pid, PRECINCTS[k % 2]) for k, pid in enumerate(ids)])
    T.add_potential_inputs(directory / 'scores.sqlite3', {pid: {'sai': None if lit is None else [int(lit)] * 5}
                                                          for pid, lit in zip(ids, literals)})
    with closing(sqlite3.connect(directory / 'walk.sqlite3')) as db, db:
        db.execute('CREATE TABLE person (person_id TEXT PRIMARY KEY, gen_voted INTEGER, gen_elig INTEGER)')
        db.executemany('INSERT INTO person VALUES (?,?,?)', zip(ids, map(int, voted), map(int, elig)))


@override_settings(LOCAL_ADMIN_ACCESS=True, HOSTED=False, DEMO_MODE=False, DISTRICT=CANDIDATE)
class CalibratedTermsTests(TestCase):
    """terms() lines t up against the general-election record when there are 1000 people to do it with, and not otherwise."""
    A, B = -1.0, 4.0

    def setUp(self):
        self.client = Client(**LOOPBACK)
        self.addCleanup(U.reset)
        U.reset()

    def world(self, fit_people=1500, no_record=40, no_score=30, no_score_no_record=7, falling=False, seed=2026):
        """Write a world: `fit_people` people with a raw t and an eligible general (record drawn from a chance that rises with
        raw t, or from one that falls), `no_record` with a raw t and no eligible general, `no_score` with an eligible general
        and no raw t, `no_score_no_record` with neither. Returns the arrays of everyone, in score-store order."""
        rng = np.random.default_rng(seed)
        x, voted, elig = binomial_people(rng, fit_people, self.A, self.B, truth=(lambda t: .9 - .7 * t) if falling else None)
        extra_x = rng.integers(0, 101, no_record) / 100
        other_x, other_voted, other_elig = binomial_people(rng, no_score, 0.0, 1.0)
        literals = np.concatenate([np.rint(x * 100), np.rint(extra_x * 100), np.full(no_score + no_score_no_record, np.nan)])
        voted = np.concatenate([voted, np.zeros(no_record, int), other_voted, np.zeros(no_score_no_record, int)])
        elig = np.concatenate([elig, np.zeros(no_record, int), other_elig, np.zeros(no_score_no_record, int)])
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, True)
        write_fit_world(directory, [None if np.isnan(v) else int(v) for v in literals], voted, elig)
        override = override_settings(DATA_DIR=directory)
        override.enable()
        self.addCleanup(override.disable)
        raw = np.where(np.isnan(literals), np.nan, literals / 100)
        return raw, voted, elig

    def test_the_chance_of_voting_is_the_curve_of_the_raw_one_for_everyone_with_a_raw_one(self):
        raw, voted, elig = self.world()
        terms = U.terms()
        has = ~np.isnan(raw)
        fit = has & (elig >= 1)
        curve = U.fit_turnout(raw[fit], voted[fit], elig[fit])
        self.assertIsNotNone(curve)
        self.assertEqual(terms.t_fit, {'n': int(fit.sum()), 'points': len(curve[0]), 'method': 'monotone'})
        self.assertGreater(terms.t_fit['points'], 2)
        np.testing.assert_array_equal(terms.t[has], np.interp(raw[has], *curve))                      # the people without a record too
        self.assertAlmostEqual((elig[fit] * terms.t[fit]).sum() / elig[fit].sum(), voted[fit].sum() / elig[fit].sum(), delta=0.01)
        self.assertGreater(terms.t[fit].mean(), raw[fit].mean())                                      # raw t is the lower of the two here
        self.assertEqual(terms.t_source, {'sai': int(has.sum()), 'record': 30, 'even': 7})

    def test_people_without_a_raw_chance_keep_their_own_record_and_even_odds(self):
        raw, voted, elig = self.world()
        terms = U.terms()
        record = np.isnan(raw) & (elig > 0)
        self.assertEqual((int(record.sum()), int((np.isnan(raw) & (elig == 0)).sum())), (30, 7))
        self.assertEqual(terms.t[record].tolist(), (voted[record] / elig[record]).tolist())
        self.assertTrue(np.all(terms.t[np.isnan(raw) & (elig == 0)] == 0.5))

    def test_the_order_of_people_does_not_change(self):
        raw, voted, elig = self.world()
        terms = U.terms()
        has = ~np.isnan(raw)
        centres, values = U.fit_turnout(raw[has & (elig >= 1)], voted[has & (elig >= 1)], elig[has & (elig >= 1)])
        distinct = np.unique(raw[has])
        calibrated = np.array([terms.t[has][raw[has] == value][0] for value in distinct])
        self.assertTrue(np.all(np.diff(calibrated) >= 0))                                              # never turned round
        inside = (distinct >= centres[0]) & (distinct <= centres[-1])
        self.assertGreater(inside.sum(), 50)
        self.assertTrue(np.all(np.diff(calibrated[inside]) > 0))                                       # a bigger raw t is a bigger t
        self.assertTrue(np.all(calibrated[distinct < centres[0]] == values[0]))                        # flat only outside the points
        self.assertTrue(np.all(calibrated[distinct > centres[-1]] == values[-1]))
        self.assertTrue(np.all((terms.undecided >= 0) & (terms.supporter >= 0)))

    def test_the_arrays_are_still_read_only_and_the_potential_uses_the_calibrated_chance(self):
        self.world()
        terms = U.terms()
        with self.assertRaises(ValueError):
            terms.t[0] = 0.0
        self.assertEqual(terms.undecided.tolist(), ((1 - np.abs(2 * terms.s - 1)) * terms.t).tolist())
        self.assertEqual(terms.supporter.tolist(), (terms.s * (1 - terms.t)).tolist())

    def test_the_log_says_what_was_fitted_and_what_it_did(self):
        raw, voted, elig = self.world()
        with self.assertLogs('cu.untapped', level='INFO') as logs:
            terms = U.terms()
        found = CALIBRATED.fullmatch(logs.records[0].getMessage())
        self.assertIsNotNone(found, logs.output)
        fit = ~np.isnan(raw) & (elig >= 1)
        self.assertEqual(found.groups(), (str(int(fit.sum())), str(terms.t_fit['points']), '%.4f' % raw[fit].mean(),
                                          '%.4f' % terms.t[fit].mean(), '%.4f' % (voted[fit].sum() / elig[fit].sum())))
        self.assertEqual(len(logs.records), 1)
        U.terms()                                                                    # cached: the line is said once per build
        self.assertEqual(len(logs.records), 1)

    def test_the_payload_carries_the_fit_and_null_when_there_is_none(self):
        self.world()
        body = json.loads(self.client.get('/api/v3/untapped?level=precinct').content)
        terms = U.terms()
        self.assertEqual(body['t_calibration'], terms.t_fit)
        self.assertEqual(set(body['t_calibration']), {'n', 'points', 'method'})
        self.assertIsInstance(body['t_calibration']['n'], int)
        self.assertIsInstance(body['t_calibration']['points'], int)
        self.assertEqual(body['t_calibration']['method'], 'monotone')
        self.assertEqual(U.area_payload('precinct')['t_calibration'], terms.t_fit)
        self.assertEqual(body['ids'], list(PRECINCTS))
        self.assertEqual(sum(body['people']), terms.n)

    def test_the_sums_use_the_calibrated_chance(self):
        self.world()
        terms = U.terms()
        body = U.area_payload('precinct')
        for k, ident in enumerate(body['ids']):
            mine = np.arange(terms.n) % 2 == k
            self.assertAlmostEqual(body['undecided'][k], terms.undecided[mine].sum(), places=5)
            self.assertAlmostEqual(body['supporter'][k], terms.supporter[mine].sum(), places=5)

    def test_999_people_to_fit_with_are_too_few_and_a_thousand_are_enough(self):
        for fit_people, calibrated in ((999, False), (1000, True)):
            with self.subTest(fit_people=fit_people):
                U.reset()
                raw, voted, elig = self.world(fit_people=fit_people)                  # plus 40 with a raw t and no eligible general
                self.assertGreater((~np.isnan(raw)).sum(), 1000)                      # the people with a raw t alone number over 1000
                with self.assertLogs('cu.untapped', level='INFO') as logs:
                    terms = U.terms()
                has = ~np.isnan(raw)
                if calibrated:
                    self.assertEqual(terms.t_fit['n'], 1000)
                    self.assertIsNotNone(CALIBRATED.fullmatch(logs.records[0].getMessage()))
                else:
                    self.assertIsNone(terms.t_fit)
                    self.assertEqual(terms.t[has].tolist(), raw[has].tolist())        # the raw t stands
                    self.assertEqual(logs.records[0].getMessage(), 'chance of voting not calibrated (fewer than 1000 people with a '
                                     'turnout score and an eligible general: 999)')
                    self.assertIsNone(U.area_payload('precinct')['t_calibration'])

    def test_a_record_that_falls_with_the_raw_chance_leaves_the_raw_chance_as_it_is(self):
        raw, _, _ = self.world(falling=True)
        with self.assertLogs('cu.untapped', level='INFO') as logs:
            terms = U.terms()
        has = ~np.isnan(raw)
        self.assertIsNone(terms.t_fit)
        self.assertEqual(terms.t[has].tolist(), raw[has].tolist())
        self.assertRegex(logs.records[0].getMessage(), r'^chance of voting not calibrated \(fewer than 2 blocks: .*\)$')

    def test_without_a_walk_store_there_is_no_record_to_line_up_with(self):
        raw, _, _ = self.world()
        (Path(settings.DATA_DIR) / 'walk.sqlite3').unlink()
        U.reset()
        with self.assertLogs('cu.untapped', level='INFO') as logs:
            terms = U.terms()
        has = ~np.isnan(raw)
        self.assertIsNone(terms.t_fit)
        self.assertEqual(terms.t[has].tolist(), raw[has].tolist())
        self.assertEqual(terms.t_source, {'sai': int(has.sum()), 'record': 0, 'even': int((~has).sum())})
        self.assertIn('fewer than 1000 people', logs.records[0].getMessage())


SQUARE = lambda x: {'type': 'Polygon', 'coordinates': [[[x, 0], [x, 1], [x + 1, 1], [x + 1, 0], [x, 0]]]}


@override_settings(LOCAL_ADMIN_ACCESS=True, HOSTED=False, DEMO_MODE=False, DISTRICT=CANDIDATE)
class StateGeometryTests(TestCase):
    """State House and Senate polygons are served from the snapshot's geometry.json like the four area levels."""

    KEY = 'co-cd005-aaaaaaaaaaaa'

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temporary.cleanup)
        self.client = Client(**LOOPBACK)
        Snapshot.objects.create(key=self.KEY, manifest={'schema': 'cu-summary-v4'}, active=True)
        geometry = {'state_house': {i: {'id': i, 'geometry': SQUARE(k), 'bbox': []} for k, i in enumerate(('08018', '08019'))},
                    'state_senate': {'08010': {'id': '08010', 'geometry': SQUARE(5), 'bbox': []}},
                    'precinct': {'p1': {'id': 'p1', 'geometry': SQUARE(9), 'bbox': []}}}
        patch = mock.patch.object(v3_cache, 'read_json', lambda key, name: geometry)
        patch.start()
        self.addCleanup(patch.stop)
        override = override_settings(DATA_DIR=Path(self.temporary.name))
        override.enable()
        self.addCleanup(override.disable)
        v3_cache.forget()
        self.addCleanup(v3_cache.forget)

    def url(self, level, variant='coarse'):
        return '/api/v3/geometry/%s/%s/%s.json' % (self.KEY, level, variant)

    def test_state_house_and_senate_come_as_feature_collections_with_label_points(self):
        for level, ids in (('state_house', ['08018', '08019']), ('state_senate', ['08010'])):
            for variant in ('coarse', 'fine'):
                response = self.client.get(self.url(level, variant))
                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertEqual([f['properties']['id'] for f in body['features']], ids)
                self.assertTrue(all(set(f['properties']) == {'id', 'lx', 'ly'} for f in body['features']))
                self.assertEqual(response['Cache-Control'], 'private, max-age=31536000, immutable')

    def test_the_four_area_levels_still_work_and_a_level_the_snapshot_lacks_is_a_404(self):
        self.assertEqual(self.client.get(self.url('precinct')).status_code, 200)
        for level in ('block', 'tract', 'zip'):                                 # the fixture has no such polygons
            response = self.client.get(self.url(level))
            self.assertEqual((response.status_code, response.json()), (404, {'error': 'Unknown geometry.'}))
        for url in (self.url('county'), self.url('state_house', 'huge'), '/api/v3/geometry/co-cd005-bbbbbbbbbbbb/state_house/coarse.json'):
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_the_area_index_levels_of_the_snapshot_are_unchanged(self):
        self.assertEqual(v3_cache.LEVELS, ('block', 'precinct', 'tract', 'zip'))
        self.assertEqual(v3_cache.GEOMETRY_LEVELS, ('block', 'precinct', 'tract', 'zip', 'state_house', 'state_senate'))
        self.assertEqual(GL.label('state_house', '08018', CANDIDATE), 'Colorado House District 18')
