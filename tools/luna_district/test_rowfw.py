# Must stay first: platform.machine() issues a WMI query that can time out under load and kill python.exe (0xC000070A) when
# pandas/pyarrow import; disabling it makes platform fall back to sys.getwindowsversion() and PROCESSOR_ARCHITECTURE.
import platform; platform._wmi_query = lambda *a, **k: (_ for _ in ()).throw(OSError("wmi disabled"))  # noqa: E702
"""Unit tests for the Phase 3b district row-framework tooling on synthetic data (no personal data).

    env -u PYTHONPATH PYTHONNOUSERSITE=1 <Python312>\\python.exe -m unittest tools.luna_district.test_rowfw -v

The copy test reads Colorado's real row-framework folder (read only) and copies it into a temporary folder.
"""
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
import pyarrow as pa

from tools import districts as D
from . import export, originals, rowfw
from .common import TARGETS, read_json, write_json
from .run_phase3 import STEPS


class CopyTests(unittest.TestCase):
    @unittest.skipUnless(rowfw.RFB.exists(), 'Colorado row-framework folder is not on this machine')
    def test_dry_run_copy_finds_every_anchor_once_and_leaves_colorado_untouched(self):
        before = originals.listing(False, roots=[rowfw.RF])
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            manifest = rowfw.build_copy(tmp, {'id': 'or-05', 'terrier_district': 'or-05', 'is_co': False, 'rows': 1}, 'Oregon OR-05')
            patched = {k: v['patched_lines'] for k, v in manifest['files'].items() if v['patched_lines']}
            self.assertEqual(set(patched), set(rowfw.PATCHES))                       # every patched file, nothing else
            self.assertEqual(sum(len(v) for v in patched.values()), 27)
            for rel, info in manifest['files'].items():                              # an unpatched file is the original, byte for byte
                if not info['patched_lines'] and 'appended_sha256' not in info:
                    self.assertEqual(info['original_sha256'], info['copy_sha256'], rel)
            self.assertTrue((Path(tmp) / 'district.json').exists() and (Path(tmp) / 'copy-diff.patch').stat().st_size > 0)
            docs = Path(tmp) / 'build' / 'rowfw-1.0.0'
            self.assertIn('"Go, populate"', (docs / 'RULINGS.md').read_text(encoding='utf-8'))
            self.assertIn('Phase 3b, Oregon OR-05', (docs / 'RULINGS.md').read_text(encoding='utf-8'))
            for forbidden in ('framework_config.json', 'PLAN.receipt.json', 'area_scores_rowfw-1.0.0.csv'):
                self.assertFalse((docs / forbidden).exists(), forbidden)             # outputs and frozen files are not copied
        self.assertEqual(before, originals.listing(False, roots=[rowfw.RF]))

    @unittest.skipUnless(rowfw.RFB.exists(), 'Colorado row-framework folder is not on this machine')
    def test_a_blank_area_id_is_the_text_nan_and_does_not_break_unique(self):
        text = (rowfw.RFB / 'rowfw' / 'areas.py').read_text(encoding='utf-8')
        line = next(l for l in rowfw.apply_patches(rowfw._B + 'rowfw/areas.py', text)[0].splitlines() if l.strip().startswith('vals = df[col]'))
        env = {'df': pd.DataFrame({'c': pd.Categorical(['b', None, 'a', 'b'])}), 'col': 'c', 'canon': np.array([0, 1, 2, 3])}
        exec(line.strip(), env)                                                     # pandas 3 left NaN here and np.unique raised TypeError
        self.assertEqual(list(np.unique(env['vals'])), ['a', 'b', 'nan'])

    def test_a_patch_that_adds_a_line_or_misses_its_anchor_is_refused(self):
        with self.assertRaises(AssertionError):
            rowfw.apply_patches('x.py', 'a = 1\n', {'x.py': [('b = 1', 'b = 2', 1, 'a')]})
        with self.assertRaises(AssertionError):
            rowfw.apply_patches('x.py', 'a = 1\n', {'x.py': [('a = 1', 'a = 1\nb = 2', 1, 'a')]})
        self.assertEqual(rowfw.apply_patches('x.py', 'a = 1\nb = 1\n', {'x.py': [('b = 1', 'b = 2', 1, 'a')]}), ('a = 1\nb = 2\n', {2}))

    def test_every_new_step_is_registered_and_defined(self):
        for step, (module, function) in STEPS.items():
            if step.startswith('rowfw-'):
                self.assertEqual(module, 'rowfw')
                self.assertTrue(callable(getattr(rowfw, function)), step)
        self.assertEqual(len([s for s in STEPS if s.startswith('rowfw-')]), 14)


class BatchTests(unittest.TestCase):
    COLUMNS = ['model_source_row', 'svi_registration_censusid', 'sii_censusid', 'svi_registration_precinct_code',
               'svi_registration_county', 'svi_registration_precinct_name', 'model_audited_tract', 'other']

    def batch(self, rows):
        return pa.RecordBatch.from_arrays([pa.array([r[i] for r in rows], pa.string()) for i in range(len(self.COLUMNS))],
                                          names=self.COLUMNS)

    def root(self, tmp, **district):
        root = Path(tmp)
        (root / 'cache').mkdir()
        write_json(root / 'district.json', {'id': 'x', 'is_co': False, **district})
        return root

    def test_oregon_precinct_key_residence_block_and_audited_sample(self):
        rows = [('1', '411234567890123', '419999999999999', '123', '41005', 'P 123', None, 'a'),
                ('2', None, '418888888888888', '45', '41051', 'P 45', None, 'b'),
                ('3', 'x', None, None, '41051', None, None, 'c')]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = self.root(tmp, terrier_district='or-05')
            np.save(root / 'cache' / 'model_audited_tract.npy', np.array(['41123456789', '', ''], dtype='<U11'))
            out = list(rowfw.district_batches([self.batch(rows)], root))
            self.assertEqual(len(out), 1)
            got = out[0].to_pydict()
            self.assertEqual(got['svi_registration_precinct_code'], ['41005-123', '41051-45', None])
            self.assertEqual(got['svi_registration_censusid'], ['411234567890123', '418888888888888', 'x'])   # row 2 borrows the residence block
            self.assertEqual(got['model_audited_tract'], ['41123456789', None, None])
            self.assertEqual(got['other'], ['a', 'b', 'c'])
            self.assertEqual(np.load(root / 'cache' / 'registration_geo_estimated.npy').tolist(), [2])    # row 3 has no residence block either

    def test_connecticut_old_county_block_ids_are_recoded_and_precinct_key_has_the_name(self):
        rows = [('1', '090010101000001', '090010101000002', '005-00', '', 'WEST HARTFORD (DISTRICT 005)', None, 'a')]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = self.root(tmp, terrier_district='ct-01')
            with mock.patch.object(D, 'block_aliases', return_value={'090010101000001': '091110101000001'}):
                got = list(rowfw.district_batches([self.batch(rows)], root))[0].to_pydict()
            self.assertEqual(got['svi_registration_censusid'], ['091110101000001'])
            self.assertEqual(got['sii_censusid'], ['090010101000002'])                 # not an alias: unchanged
            self.assertEqual(got['svi_registration_precinct_code'], ['005-00-WEST-HARTFORD-DISTRICT-005'])
            with mock.patch.object(D, 'block_aliases', return_value={}), self.assertRaises(SystemExit):
                list(rowfw.district_batches([self.batch(rows)], root))                 # Connecticut without its alias file: stop

    def test_colorado_batches_pass_through_unchanged(self):
        batch = self.batch([('1', 'a', 'b', 'c', 'd', 'e', None, 'f')])
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            write_json(root / 'district.json', {'id': 'co-replay', 'is_co': True})
            self.assertTrue(all(a is b for a, b in zip(rowfw.district_batches([batch], root), [batch])))


class SampleAndDictionaryTests(unittest.TestCase):
    def test_audited_tracts_household_group_and_threshold_rules(self):
        tract = ['A', 'A', 'A', 'A', 'A', 'A', 'B', 'B', 'B', 'B', 'C', '']
        mapped = [True] * 5 + [False] + [True] * 6
        household = ['h0', 'h0', 'h1', 'h2', 'h3', 'h4', 'h1', 'h5', 'h6', 'h7', 'h8', 'h9']
        groups = [0, 0, 1, 2, 3, 4, 5, 2, 6, 7, 8, 9]
        got = rowfw.audited_tracts(tract, mapped, household, groups, minimum=2)
        # row 5 unmapped; rows 2 and 6 share household h1 across A and B; rows 3 and 7 share group 2 across A and B; row 11 has no tract;
        # tract C is left with 1 row, under the minimum of 2
        self.assertEqual(got.tolist(), ['A', 'A', '', '', 'A', '', '', '', 'B', 'B', '', ''])
        self.assertEqual(rowfw.audited_tracts(tract, mapped, household, groups, minimum=3).tolist(), ['A', 'A', '', '', 'A'] + [''] * 7)

    def dictionary(self):
        return {'fields': {'a': {'completion_probability_columns': [], 'reference_probability_columns': ['a__p_nonzero']},
                           'b': {'completion_probability_columns': ['b__completion_p_nonzero'], 'reference_probability_columns': ['b__p_nonzero']}}}

    def test_fix_dictionary_changes_exactly_the_legacy_fields_and_is_idempotent(self):
        d = self.dictionary()
        self.assertEqual(rowfw.fix_dictionary(d, expected={'a'}, header=['a__p_nonzero', 'x']), ['a'])
        self.assertEqual(d['fields']['a']['completion_probability_columns'], ['a__p_nonzero'])
        self.assertEqual(d['fields']['b']['completion_probability_columns'], ['b__completion_p_nonzero'])
        self.assertEqual(rowfw.fix_dictionary(d, expected={'a'}), [])
        with self.assertRaises(AssertionError):                                       # another field without a column is not ours to fix
            rowfw.fix_dictionary(self.dictionary(), expected={'z'})
        with self.assertRaises(AssertionError):                                       # a column the CSV does not hold
            rowfw.fix_dictionary(self.dictionary(), expected={'a'}, header=['x'])

    def test_export_dictionary_falls_back_to_the_legacy_probability_column(self):
        names, registry = [], {}
        results = {}
        for t in TARGETS:
            names += [t + '__completed_nonzero', t + '__p_nonzero'] + ([] if t in rowfw.LEGACY5 else [t + '__completion_p_nonzero'])
            results[t] = {'field': {'task': 'binary_nonzero', 'evaluation_codes': [0, 1]}}
            registry[t] = {'majority_code': 0, 'method': 'm', 'model_id': 'id'}
        parsed = [(n, export.COL.match(n)) for n in names]
        fields = export.build_dictionary(types.SimpleNamespace(results=results), [], parsed, {}, registry)['fields']
        for t in TARGETS:
            want = [t + '__p_nonzero'] if t in rowfw.LEGACY5 else [t + '__completion_p_nonzero']
            self.assertEqual(fields[t]['completion_probability_columns'], want, t)
            self.assertEqual(fields[t]['reference_probability_columns'], [t + '__p_nonzero'], t)


class GateTests(unittest.TestCase):
    def test_json_diff_and_path_normalisation(self):
        subs = [(r'C:\copy\scratch', '<S>'), (r'C:\copy', '<RF>'), (r'C:\co\row', '<RF>')]
        a = {'p': r'C:\copy\cache\x', 'q': [1, {'r': r'C:\copy\scratch'}], 'k': 1}
        b = {'p': r'C:\co\row\cache\x', 'q': [1, {'r': '<S>'}], 'k': 2, 'extra': 0}
        diffs = rowfw.json_diff(rowfw._normal(a, subs), rowfw._normal(b, subs))
        self.assertEqual(sorted(map(tuple, diffs)), [('extra',), ('k',)])

    def test_table_equal_finds_a_changed_value_and_a_changed_layout(self):
        import pyarrow.parquet as pq
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = pa.table({'a': pa.array([1, 2, 3, 4]), 'b': pa.array(['x', 'y', 'z', 'w']).dictionary_encode()})
            other = pa.table({'a': pa.array([1, 2, 3, 5]), 'b': base.column('b')})
            for name, table, groups in (('one', base, 2), ('same', base, 2), ('value', other, 2), ('layout', base, 4)):
                pq.write_table(table, Path(tmp) / (name + '.parquet'), row_group_size=groups)
            check = lambda name: rowfw.table_equal(Path(tmp) / 'one.parquet', Path(tmp) / (name + '.parquet'))
            self.assertTrue(check('same')['tables_equal'])
            self.assertFalse(check('value')['tables_equal'])
            self.assertTrue(check('layout')['tables_equal'])                          # another row-group layout, same values

    def test_original_hash_guard_sees_a_change_in_a_given_root(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp) / 'rf'
            (root / 'sub').mkdir(parents=True)
            (root / 'sub' / 'f.txt').write_text('abc')
            before = Path(tmp) / 'before.json'
            originals.snapshot(before, roots=[root])
            self.assertEqual(originals.check_stat(strict=True, before_path=before, roots=[root]), [])
            (root / 'sub' / 'f.txt').write_text('abcd')
            with self.assertRaises(RuntimeError):
                originals.check_stat(strict=True, before_path=before, roots=[root])
            self.assertEqual(read_json(before)['lock_files'], {})


if __name__ == '__main__':
    unittest.main()
