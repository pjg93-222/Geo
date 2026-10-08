"""Synthetic checks for tools/support.py (candidate support, general mode). An 8-row frame; no real data, no Django.

    <venv python> -m unittest tools.test_support          (from the V3 folder)
"""
import ast
import inspect
import textwrap
import unittest

import numpy as np

from tools import districts as D
from tools import support as S

OR, CT, CO = (D.get(i) for i in ('or-05', 'ct-01', 'co-05'))
# rows: 0 strong D with a 2024 D ballot, 1 D without a ballot, 2 R with a 2024 R ballot, 3 R, 4 U, 5 G, 6 blank registration,
# 7 W with a 2022 D ballot
COLS = {
    'sai_partisan_score':        [90, 80, 20, 10, 50, 60, 40, 70],
    'sai_harris_support_score':  [90, 85, 15, 10, 50, 55, 45, 60],
    'sai_trump_support_score':   [10, 15, 85, 90, 50, 45, 55, 40],
    'svi_party_registration':    ['D', 'D', 'R', 'R', 'U', 'G', '', 'W'],
    'svi_vh_2024p_party':        ['D', None, 'R', None, None, None, None, None],
    'svi_vh_2022p_party':        [None, None, None, None, None, None, None, 'D'],
}
EXPECT_D = [95.0, 100 * (0.8 + 0.85 + 1.0) / 3, 8.75, 100 * (0.1 + 0.1 + 0.0) / 3, 50.0,
            100 * (0.6 + 0.55 + 0.5) / 3, 42.5, 100 * (0.7 + 0.6 + 0.5 + 1.0) / 4]
ENGINE = D.rowfw_build(CO) / 'rowfw' / 'engine.py'


def entry(base=OR, **changes):
    """A copy of a district entry (a dict is accepted wherever an id is) with support settings changed."""
    return {**base, **changes, 'support': {**base['support'], **changes.get('support', {})}}


def body_of(source, name):
    """The statements of function `name` as source text, docstring left out."""
    function = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef) and n.name == name)
    return [ast.get_source_segment(source, n) for n in function.body
            if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]


class SignalTests(unittest.TestCase):
    def test_d_candidate_scores_are_the_mean_of_the_signals_present(self):
        result = S.compute(OR, COLS)
        np.testing.assert_allclose(result['sup_score'], EXPECT_D, rtol=0, atol=1e-9)
        self.assertEqual(result['coverage'], {'partisan': 8, 'presidential': 8, 'registration': 7, 'primary_ballot': 3})
        self.assertTrue(result['in_universe'].all() and len(result['in_universe']) == 8)

    def test_an_r_candidate_mirrors_a_d_candidate(self):
        as_r = S.compute(entry(party='R'), COLS)['sup_score']
        np.testing.assert_allclose(as_r, 100 - np.array(EXPECT_D), rtol=0, atol=1e-9)

    def test_minor_party_codes_count_halfway_unless_the_config_says_otherwise(self):
        base = S.signals(COLS, 'D', OR['support'])['registration']
        self.assertEqual(base.tolist()[:6], [1.0, 1.0, 0.0, 0.0, 0.5, 0.5])
        self.assertTrue(np.isnan(base[6]) and base[7] == 0.5)                  # blank: no signal; W: halfway
        changed = S.signals(COLS, 'D', {**OR['support'], 'party_values': {'W': 0.2, 'U': 0.4}})['registration']
        self.assertEqual((changed[4], changed[7]), (0.4, 0.2))
        self.assertAlmostEqual(S.compute(entry(support={'party_values': {'W': 0.2}}), COLS)['sup_score'][7],
                               100 * (0.7 + 0.6 + 0.2 + 1.0) / 4, places=9)

    def test_the_newest_d_or_r_ballot_wins_and_other_codes_are_skipped(self):
        cols = {**COLS, 'svi_vh_2024pp_party': ['U', 'R', None, None, None, None, None, None],
                'svi_vh_2024p_party': ['D', 'D', 'R', None, None, None, None, None],
                'svi_vh_2022p_party': ['R', 'R', 'D', 'D', None, None, None, 'D'],
                'svi_vh_2012p_party': ['R'] * 8}                                 # outside the window: never read
        ballot = S.signals(cols, 'D', OR['support'])['primary_ballot']
        # row 0: 2024pp is U (skipped) so 2024p D; row 1: 2024pp R beats 2024p D; row 2: 2024p R beats 2022p D; row 3: 2022p D
        self.assertEqual(ballot[:4].tolist(), [1.0, 0.0, 0.0, 1.0])
        self.assertTrue(np.isnan(ballot[4]) and np.isnan(ballot[5]) and ballot[7] == 1.0)
        self.assertEqual(S.ballot_columns(cols, (2014, 2025)),
                         ['svi_vh_2024pp_party', 'svi_vh_2024p_party', 'svi_vh_2022p_party'])
        self.assertEqual(S.ballot_columns(cols, (2023, 2025)), ['svi_vh_2024pp_party', 'svi_vh_2024p_party'])

    def test_a_missing_signal_renormalises_and_weights_apply(self):
        row1 = S.compute(OR, COLS)['sup_score'][1]
        self.assertAlmostEqual(row1, 100 * (0.8 + 0.85 + 1.0) / 3, places=9)          # no ballot: three signals, not four
        weighted = entry(support={'signals': {'partisan': 3, 'presidential': 1, 'registration': 1, 'primary_ballot': 1}})
        self.assertAlmostEqual(S.compute(weighted, COLS)['sup_score'][0], 100 * (3 * 0.9 + 0.9 + 1.0 + 1.0) / 6, places=9)

    def test_a_person_with_no_signal_at_all_stops_the_step(self):
        blank = {k: [None] * 2 for k in COLS}
        with self.assertRaisesRegex(ValueError, '2 people have no support signal'):
            S.compute(OR, blank)


class OrientationTests(unittest.TestCase):
    def test_the_right_way_round_returns_the_six_means(self):
        means = S.orientation(COLS)
        self.assertEqual(set(means), {'sai_partisan_score', 'sai_harris_support_score', 'sai_trump_support_score'})
        self.assertEqual(means['sai_partisan_score'], {'D': 85.0, 'R': 15.0})
        self.assertEqual(means['sai_trump_support_score'], {'D': 12.5, 'R': 87.5})

    def test_a_flipped_column_raises_for_each_of_the_three(self):
        for name in ('sai_partisan_score', 'sai_harris_support_score', 'sai_trump_support_score'):
            flipped = {**COLS, name: [100 - v for v in COLS[name]]}
            with self.assertRaisesRegex(ValueError, name):
                S.orientation(flipped)


class RefusalTests(unittest.TestCase):
    def test_primary_mode_is_refused_with_the_plain_message(self):
        with self.assertRaises(SystemExit) as stop:
            S.compute(entry(race_mode='primary'), COLS)
        self.assertEqual(stop.exception.code, S.PRIMARY_NOT_APPROVED)
        self.assertIn('PRIMARY_MODE_SPEC.md', S.PRIMARY_NOT_APPROVED)

    def test_the_mode_comes_from_the_config_not_the_code(self):
        self.assertEqual((OR['race_mode'], CT['race_mode']), ('general', 'general'))
        for district in (OR, CT):
            self.assertEqual(len(S.compute(district, COLS)['sup_score']), 8, district['id'])

    def test_no_candidate_and_unknown_modes_cannot_be_scored(self):
        for bad in (entry(race_mode=None), entry(race_mode='both'), entry(party='U'), entry(party=None)):
            with self.assertRaises(ValueError):
                S.compute(bad, COLS)
        with self.assertRaises(ValueError):
            S.compute(CO, COLS)


class StandingTests(unittest.TestCase):
    def test_midrank_standing_by_hand(self):
        np.testing.assert_array_equal(S.standing_pct(np.array([10.0, 20.0, 20.0, 30.0])), [12.5, 50.0, 50.0, 87.5])

    def test_the_source_equals_the_rowfw_function(self):
        if not ENGINE.is_file():
            self.skipTest('the Colorado row-framework build is not on this machine')
        engine = ENGINE.read_text(encoding='utf-8')
        ours = textwrap.dedent(inspect.getsource(S.standing_pct))
        self.assertEqual(body_of(ours, 'standing_pct'), body_of(engine, 'standing_pct_only'))
        self.assertEqual(list(inspect.signature(S.standing_pct).parameters), ['score'])


if __name__ == '__main__':
    unittest.main()
