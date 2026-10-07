# Must stay first: platform.machine() issues a WMI query that can time out under load and kill python.exe (0xC000070A) when
# pandas/scipy import; disabling it makes platform fall back to sys.getwindowsversion() and PROCESSOR_ARCHITECTURE.
import platform; platform._wmi_query = lambda *a, **k: (_ for _ in ()).throw(OSError("wmi disabled"))  # noqa: E702
"""Unit tests for the Phase 3 driver on synthetic fixtures (no Luna data, no personal data).

    python -m unittest tools.luna_district.test_luna_district -v
"""
import unittest

import numpy as np

from pathlib import Path

from . import copy_stage, export, models, partitions, select
from .common import (BASE_FIELDS, DISTRICTS, GROUP_CPI10, GROUP_MID14, HM_FIELDS, HM_FIT_FIELDS, R1_FIELDS, TARGETS,
                     expected_blanks)


class CommonTests(unittest.TestCase):
    def test_field_partition(self):
        self.assertEqual(len(TARGETS), 26)
        self.assertEqual(sorted(HM_FIELDS + BASE_FIELDS), sorted(TARGETS))
        self.assertEqual(len(HM_FIT_FIELDS), 19)
        self.assertTrue(set(R1_FIELDS) <= set(BASE_FIELDS))
        self.assertEqual(sorted(GROUP_CPI10 + GROUP_MID14 + ['sci_number_of_children_in_household', 'sii_dwelling_type']), sorted(TARGETS))

    def test_census_arithmetic_and_expected_blanks(self):
        for key, cfg in DISTRICTS.items():
            total = sum(expected_blanks(key, t) for t in TARGETS)
            self.assertEqual(total, cfg['completed_values'], key)
        self.assertEqual(DISTRICTS['or-05']['completed_values'], 1861842)
        self.assertEqual(DISTRICTS['ct-01']['completed_values'], 1340098)
        self.assertEqual(DISTRICTS['co-replay']['completed_values'], 1722948)


class PatchGateTests(unittest.TestCase):
    def setUp(self):
        self.saved = dict(copy_stage.PATCHES)
        copy_stage.PATCHES['x/y.py'] = [('ROWS = 491308', 'ROWS = 562563', 1, 'a')]

    def tearDown(self):
        copy_stage.PATCHES.clear()
        copy_stage.PATCHES.update(self.saved)

    def test_declared_patch_passes_the_gate(self):
        text = 'a = 1\nROWS = 491308\nb = 2\n'
        patched, lines = copy_stage.apply_patches('x/y.py', text)
        self.assertEqual(lines, {2})
        self.assertEqual(copy_stage.line_gate(text, patched, lines), [2])

    def test_undeclared_change_and_line_count_change_fail(self):
        text = 'a = 1\nROWS = 491308\nb = 2\n'
        patched, lines = copy_stage.apply_patches('x/y.py', text)
        with self.assertRaises(AssertionError):
            copy_stage.line_gate(text, patched.replace('b = 2', 'b = 3'), lines)         # an extra, undeclared edit
        with self.assertRaises(AssertionError):
            copy_stage.line_gate(text, patched + 'c = 3\n', lines)                         # an added line

    def test_missing_literal_is_refused(self):
        with self.assertRaises(AssertionError):
            copy_stage.apply_patches('x/y.py', 'ROWS = 1\n')

    def test_real_patch_table_has_no_newlines_and_single_occurrences(self):
        for rel, items in copy_stage.PATCHES.items():
            for old, new, count, kind in items:
                self.assertNotIn('\n', new)
                self.assertEqual(count, 1)
                self.assertIn(kind, ('a', 'c'))


class PartitionTests(unittest.TestCase):
    def fixture(self):
        rng = np.random.default_rng(3)
        n = 6000
        groups = np.sort(rng.integers(0, 1500, n))
        order = rng.permutation(1500)
        gsplit = np.where(order < 900, 0, np.where(order < 1200, 1, 2))
        split = gsplit[groups]
        ghalf = rng.integers(0, 2, 1500)
        half = np.where(split == 1, ghalf[groups], -1)
        observed = rng.random(n) > 0.2
        return groups, split, half, observed

    def test_holdout_is_all_observed_split2(self):
        _, split, _, observed = self.fixture()
        h = partitions.holdout_rows(split, observed)
        self.assertTrue((split[h] == 2).all() and observed[h].all())
        self.assertEqual(len(h), int(((split == 2) & observed).sum()))
        self.assertEqual(h.dtype, np.int64)

    def test_content_hash_depends_on_every_field(self):
        frozen = {t: np.arange(5, dtype=np.int64) for t in TARGETS}
        base = partitions.content_hash(frozen)
        frozen2 = dict(frozen)
        frozen2[TARGETS[-1]] = np.arange(6, dtype=np.int64)
        self.assertNotEqual(base, partitions.content_hash(frozen2))

    def test_a_halves_are_disjoint_and_exhaustive(self):
        groups, split, half, observed = self.fixture()
        iscal, issearch = partitions.a_halves(groups, split, half, 20260941, 20260946)
        rows_a = np.flatnonzero((split == 1) & (half == 0) & observed)
        cal, a1, a2 = partitions.split_a(rows_a, iscal, issearch)
        self.assertEqual(sorted(np.concatenate([cal, a1, a2])), sorted(rows_a))
        comps = [set(groups[x].tolist()) for x in (cal, a1, a2)]
        self.assertFalse(comps[0] & comps[1] or comps[0] & comps[2] or comps[1] & comps[2])

    def test_leakage_check_passes_clean_and_fails_on_planted_overlap(self):
        groups, split, half, observed = self.fixture()
        iscal, issearch = partitions.a_halves(groups, split, half, 1, 2)
        rows_a = np.flatnonzero((split == 1) & (half == 0) & observed)
        cal, a1, a2 = partitions.split_a(rows_a, iscal, issearch)
        train = np.flatnonzero((split == 0) & observed)[:500]
        b = np.flatnonzero((split == 1) & (half == 1) & observed)[:300]
        h = partitions.holdout_rows(split, observed)
        counts = partitions.leakage_check(groups, {'train': train, 'cal': cal, 'A1': a1, 'A2': a2, 'B': b}, h)
        self.assertEqual(counts['train'], 500)
        with self.assertRaises(AssertionError):                       # a holdout row planted in training
            partitions.leakage_check(groups, {'train': np.concatenate([train, h[:1]])}, h)
        with self.assertRaises(AssertionError):                       # a component shared by two development sets
            partitions.leakage_check(groups, {'cal': cal, 'A1': np.concatenate([a1, cal[:1]])}, h)


class SelectionTests(unittest.TestCase):
    promote = staticmethod(lambda a2, a1: bool(a2['gain'] >= .001 and a2['paired_component_80_interval'][0] > 0 and a1['gain'] >= .001))

    def test_challenger_needs_gate_and_positive_b(self):
        good = {'gain': .002, 'paired_component_80_interval': [.0005, .004]}
        self.assertTrue(select.passes(good, good, .001, self.promote))
        self.assertFalse(select.passes(good, good, -.001, self.promote))
        self.assertFalse(select.passes({'gain': .0005, 'paired_component_80_interval': [.0001, .001]}, good, .01, self.promote))

    def test_pick_prefers_larger_gain_and_h_on_ties(self):
        self.assertEqual(select.pick([], {}), 'R')
        self.assertEqual(select.pick(['H'], {'H': .01}), 'H')

    def test_majority_rules(self):
        self.assertTrue(select.lift_rule({'A1': .1, 'A2': -.2, 'B': .49}))
        self.assertFalse(select.lift_rule({'A1': .1, 'A2': .5, 'B': .1}))          # the bar is strict: 0.5 is a lift
        self.assertFalse(select.lift_rule({'A1': .1, 'A2': .3, 'B': 2.}))          # one check above the bar is enough
        self.assertTrue(select.worse_rule({'A1': -.1, 'A2': -.2, 'B': -.01}))       # below the majority code on every check
        self.assertFalse(select.worse_rule({'A1': -.1, 'A2': 0., 'B': -.5}))        # a tie on one check is not "worse"

    def test_fill_replaces_the_model_only_when_ruled(self):
        p = np.array([[.2, .8], [.9, .1]])
        field = {'fill': 'model', 'codes': ['a', 'b'], 'majority_code': 'a'}
        self.assertEqual(select.fill_of(p, field).tolist(), [1, 0])
        self.assertEqual(select.fill_of(p, {**field, 'fill': 'majority'}).tolist(), [0, 0])


class ExportTests(unittest.TestCase):
    def test_method_ids(self):
        self.assertEqual(export.method_of('sci_charitable_donor', 'R'), 'hm_refit')
        self.assertEqual(export.method_of('sci_charitable_donor', 'R', 'majority'), 'majority')
        self.assertEqual(export.method_of('sci_cpi_cc_index', 'R'), 'legacy_refit')
        self.assertEqual(export.method_of('sii_dwelling_type', 'R'), 'legacy_refit_prior_corrected')
        self.assertEqual(export.method_of('sci_ct_political_partyaff_democrat', 'H'), 'hm_refit')
        for t in TARGETS:
            for kind in ('R', 'H'):
                for fill in ('model', 'majority'):
                    self.assertIn(export.method_of(t, kind, fill), export.METHODS)

    def test_status_codes(self):
        fill = np.array([0, 1, 0, 1])
        # household-model and base fields alike: only fills equal to the majority index are relabelled when the rule applies
        self.assertEqual(export.status_code('sci_charitable_donor', 'R', True, fill, 0).tolist(), [2, 0, 2, 0])
        self.assertEqual(export.status_code('sci_cpi_auto_index', 'R', True, fill, 0).tolist(), [2, 1, 2, 1])
        self.assertEqual(export.status_code('sci_cpi_auto_index', 'R', False, fill, 0).tolist(), [1, 1, 1, 1])
        self.assertEqual(export.status_code('sci_ct_political_partyaff_democrat', 'H', False, fill, 0).tolist(), [0, 0, 0, 0])
        # a model below the majority code on every check: the whole field is majority default
        self.assertEqual(export.status_code('sci_cpi_auto_index', 'R', True, np.zeros(4, int), 0, True).tolist(), [2, 2, 2, 2])

    def test_seven_decimal_format(self):
        out = export.fmt7(np.array([0.5, 0.123456789, 1.0], dtype=np.float32))
        self.assertEqual(out.tolist(), ['0.5000000', '0.1234568', '1.0000000'])

    def test_column_names_parse(self):
        for name, kind in [('sci_cpi_auto_index__completed_nonzero', 'completed_nonzero'), ('sii_dwelling_type__completed', 'completed'),
                           ('sii_dwelling_type__p_G', 'p_G'), ('sii_dwelling_type__completion_p_S', 'completion_p_S'),
                           ('sci_cpi_cc_index__flag_p95', 'flag_p95'), ('sci_cpi_cc_index__estimate_probability', 'estimate_probability')]:
            m = export.COL.match(name)
            self.assertIsNotNone(m, name)
            self.assertEqual(m['k'], kind)
        self.assertIsNone(export.COL.match('model_source_row'))

    def test_completed_values_cpi_maps_to_zero_one(self):
        try:
            import pandas as pd
        except ImportError:
            self.skipTest('pandas not installed in this interpreter')
        col = pd.Series([' 3', '0', '', '1', '9'])
        self.assertEqual(export.completed_values(col, True).tolist(), ['1', '0', '', '1', '1'])
        self.assertEqual(export.completed_values(col, False).tolist(), ['3', '0', '', '1', '9'])


class DistrictOnlyTests(unittest.TestCase):
    def test_no_colorado_trained_model_can_fill(self):
        D = Path('d')
        self.assertEqual(models.bundle_path(D, 'sci_cpi_cc_index', 'R').name, 'legacy-sci_cpi_cc_index.joblib')
        self.assertEqual(models.bundle_path(D, 'sci_charitable_donor', 'R').name, 'hm-T-sci_charitable_donor.joblib')
        with self.assertRaises(AssertionError):                        # the retired Colorado-transfer candidate
            models.bundle_path(D, 'sci_cpi_cc_index', 'T')
        here = Path(__file__).parent
        for name in ('models', 'select', 'score', 'export', 'verify', 'report'):
            self.assertNotIn('co-transfer', (here / f'{name}.py').read_text(encoding='utf-8'), name)

    def test_forest_threads_are_one_config_value(self):
        from .common import N_JOBS
        self.assertEqual(N_JOBS, 4)
        for name in ('models', 'export'):
            self.assertNotIn('n_jobs=2', (Path(__file__).parent / f'{name}.py').read_text(encoding='utf-8'), name)


class WatchdogTests(unittest.TestCase):
    def tripped(self, clock):
        """Run Step._watch on a fake clock (one monotonic reading per 20 s tick); True when it would os._exit(4)."""
        from unittest import mock
        from . import common
        st = common.Step('or-05', 'watchdog-test')
        st._stop = mock.Mock()
        st._stop.wait.side_effect = [False] * (len(clock) - 1) + [True]
        ticks = iter(clock)
        with mock.patch.object(common.time, 'monotonic', lambda: next(ticks)), mock.patch.object(common, 'available_ram_gb', return_value=9.0), \
                mock.patch.object(st, '_finish'), mock.patch.object(common.os, '_exit') as ex:
            st._watch()
        return ex.called

    def test_a_suspend_is_not_step_time_but_a_real_overrun_is(self):
        self.assertFalse(self.tripped([0, 20, 40, 40 + 5 * 3600, 60 + 5 * 3600]))    # lid closed for 5 h in the middle of a 2-minute step
        self.assertTrue(self.tripped(list(range(0, 2 * 3600 + 200, 20))))            # 2 h of steady ticks


if __name__ == '__main__':
    unittest.main()
