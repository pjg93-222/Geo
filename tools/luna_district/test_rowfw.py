# Must stay first: platform.machine() issues a WMI query that can time out under load and kill python.exe (0xC000070A) when
# pandas/pyarrow import; disabling it makes platform fall back to sys.getwindowsversion() and PROCESSOR_ARCHITECTURE.
import platform; platform._wmi_query = lambda *a, **k: (_ for _ in ()).throw(OSError("wmi disabled"))  # noqa: E702
"""Unit tests for the Phase 3b district row-framework tooling on synthetic data (no personal data).

    env -u PYTHONPATH PYTHONNOUSERSITE=1 <Python312>\\python.exe -m unittest tools.luna_district.test_rowfw -v

The copy test reads Colorado's real row-framework folder (read only) and copies it into a temporary folder.
"""
import ast
import json
import tempfile
import textwrap
import types
import unittest
from fractions import Fraction
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

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
            self.assertEqual(sum(len(v) for v in patched.values()), 43)               # 27 of Phase 3b, 16 of Phase 4B (kind e)
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
        self.assertEqual(len([s for s in STEPS if s.startswith('rowfw-')]), 16)


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


def _original_and_patched(rel):
    text = (rowfw.RF / rel).read_text(encoding='utf-8')
    return text, rowfw.apply_patches(rel, text)[0]


def _function(text, name, env):
    """The top-level function `name` of the source `text`, executed in a copy of `env`."""
    node = next(n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == name)
    ns = dict(env)
    exec(ast.get_source_segment(text, node), ns)
    return ns[name]


def _lines(text, start, count):
    """`count` source lines from the first line containing `start`, dedented."""
    lines = text.splitlines()
    i = next(k for k, line in enumerate(lines) if start in line)
    return textwrap.dedent('\n'.join(lines[i:i + count]))


def _overall_block(text):
    """The `default` line and the default block of the frozen config's `overall` in freeze_rowfw.py, as a dict display."""
    lines = text.splitlines()
    i = next(k for k, line in enumerate(lines) if line.strip() == "'overall': {" and lines[k + 1].strip().startswith("'default': "))
    return '{' + '\n'.join(lines[i + 1:i + 7]) + '}'


@unittest.skipUnless(rowfw.RFB.exists(), 'Colorado row-framework folder is not on this machine')
class FourTermTests(unittest.TestCase):
    """Phase 4B: the 16 kind-e patches. Colorado's branch must equal the original bit for bit; a candidate district adds support last."""
    CO_W = {'cua': 0.4, 'part': 0.35, 'soi': 0.25}
    ENGINE, DRAWS, FREEZE = (rowfw._B + p for p in ('rowfw/engine.py', 'rowfw/draws.py', 'freeze/freeze_rowfw.py'))
    ALT = {'ovr_alt_cu50_pct': {'weights': [{'value': 0.5}, {'value': 0.25}, {'value': 0.25}]}}

    def arrays(self, k=4, n=1000):
        rng = np.random.default_rng(20261007)
        return [rng.uniform(0.01, 100.0, n) for _ in range(k)]

    def district_jsons(self):
        with mock.patch.object(rowfw, 'read_json', return_value={'files': {'synthetic_completed.csv.gz': {'sha256': 'x'}}}):
            return rowfw.district_json('or-05'), rowfw.district_json('co-replay')

    def test_colorado_overall_scores_are_bitwise_the_original(self):
        old, new = (_function(t, 'overall_scores', {'np': np, 'Structure': object}) for t in _original_and_patched(self.ENGINE))
        cfg = {'overall': {'default': 'W40_35_25', 'alternatives': self.ALT}}
        a, b, c, _ = self.arrays()
        st = types.SimpleNamespace(ovr_w=dict(self.CO_W))
        x, y = old(a, b, c, st, cfg), new(a, b, c, st, cfg)
        self.assertEqual(list(x), list(y))                                           # no sup_pct key for Colorado
        for k in x:
            self.assertEqual(x[k].tobytes(), y[k].tobytes(), k)

    def test_colorado_draw_and_evidence_lines_are_bitwise_the_original(self):
        a, b, c, _ = self.arrays()
        w, pt = dict(self.CO_W), types.SimpleNamespace(cols={'part_pct': b})
        EN = types.SimpleNamespace(standing_pct_only=lambda x: x)
        old, new = _original_and_patched(self.DRAWS)
        for marker, name, env in (("ovr = w['cua'] * pct['cua']", 'ovr', {'w': w, 'pct': {'cua': a, 'soi': c}, 'pt': pt}),
                                  ("po = EN.standing_pct_only(w['cua'] * pc", 'po', {'w': w, 'pc': a, 'ps': c, 'pt': pt, 'EN': EN})):
            got = []
            for text in (old, new):
                ns = dict(env)
                exec(_lines(text, marker, 1), ns)
                got.append(ns[name].tobytes())
            self.assertEqual(got[0], got[1], marker)
        cfg = {'overall': {'default': 'W40_35_25', 'W40_35_25': {'weights': {g: {'value': v} for g, v in w.items()}}}}
        got = []
        for text in _original_and_patched(self.ENGINE):                              # the evidence block, engine.py:491-494
            ns = {'cfg': cfg, 'out': {'cua_measured_pct': a, 'part_measured_pct': np.full(len(a), 100.0), 'soi_measured_pct': c}}
            exec(_lines(text, "w = cfg['overall']", 4), ns)
            got.append(ns['out']['ovr_measured_pct'].tobytes())
        self.assertEqual(got[0], got[1])

    def test_freeze_lines_keep_colorado_s_values_and_give_a_candidate_the_four_term_overall(self):
        oregon, colorado = self.district_jsons()
        old, new = _original_and_patched(self.FREEZE)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            def values(text, district_cfg):
                (Path(tmp) / 'district.json').write_text(json.dumps(district_cfg), encoding='utf-8')
                W = lambda x: {'num': Fraction(x).numerator, 'den': Fraction(x).denominator, 'value': float(Fraction(x))}
                ns = {'F': Fraction, 'W': W, 'S_PAUL': 'Paul ruling', '__file__': str(Path(tmp) / 'build' / 'rowfw-1.0.0' / 'freeze' / 'freeze_rowfw.py')}
                exec(_lines(text, 'OVR_DEFAULT = ', 1), ns)
                d1 = eval('{' + _lines(text, "'D1': {'default'", 1).strip().removeprefix("'D1': {").rstrip(',') + '}', ns)['default']
                return {'default': ns['OVR_DEFAULT'], 'block': eval(_overall_block(text), ns), 'd1': d1}
            was, co, orr = values(old, colorado), values(new, colorado), values(new, oregon)
        self.assertEqual(json.dumps(co, default=str), json.dumps(was, default=str))                       # same values, same key order
        self.assertEqual(list(was['default'].values()), [Fraction(2, 5), Fraction(7, 20), Fraction(1, 4)])
        self.assertNotIn('support', co['block']['W40_35_25'])
        self.assertEqual(new.count('{g: float(v) for g, v in OVR_DEFAULT.items()}'), 2)               # the two float copies of the weights
        self.assertEqual(eval('{g: float(v) for g, v in OVR_DEFAULT.items()}', {'OVR_DEFAULT': co['default']}), {'cua': 0.40, 'part': 0.35, 'soi': 0.25})
        self.assertEqual(list(orr['default']), ['cua', 'sup', 'part', 'soi'])
        self.assertEqual(sum(orr['default'].values()), 1)
        self.assertEqual(list(orr['block']), ['default', 'W30_25_25_20'])
        self.assertEqual(orr['block']['default'], 'W30_25_25_20')
        block = orr['block']['W30_25_25_20']
        self.assertEqual(list(block['weights']), ['cua', 'sup', 'part', 'soi'])
        self.assertEqual(block['support'], oregon['support'])
        self.assertEqual((block['formula'], block['status']), (oregon['overall']['formula'], oregon['overall']['status']))
        self.assertTrue(orr['d1'].startswith('W30_25_25_20 overall (O-11'))

    def test_district_overall_scores_add_the_support_standing_last_and_return_it(self):
        new = _function(_original_and_patched(self.ENGINE)[1], 'overall_scores', {'np': np, 'Structure': object})
        a, b, c, d = self.arrays()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            np.savez(Path(tmp) / 'support.npz', sup_pct=d)
            cfg = {'overall': {'default': 'W30_15_35_20', 'alternatives': self.ALT, 'W30_15_35_20': {'support': {'pct_file': str(Path(tmp) / 'support.npz')}}}}
            got = new(a, b, c, types.SimpleNamespace(ovr_w={'cua': 0.3, 'sup': 0.15, 'part': 0.35, 'soi': 0.2}), cfg)
        self.assertEqual(got['ovr_score'].tobytes(), (0.3 * a + 0.35 * b + 0.2 * c + 0.15 * d).tobytes())      # ((cua + part) + soi) + sup
        self.assertEqual(got['sup_pct'].tobytes(), d.tobytes())
        self.assertEqual(got['ovr_alt_eq3_score'].tobytes(), ((a + b + c) / 3.0).tobytes())                    # the alternatives stay 3-term

    def test_district_json_has_the_four_term_overall_only_for_a_candidate_district(self):
        oregon, colorado = self.district_jsons()
        self.assertEqual(oregon['overall'], {'key': 'W30_25_25_20', 'weights': {'cua': [30, 100], 'sup': [25, 100], 'part': [25, 100], 'soi': [20, 100]},
                                             'formula': 'ovr_score = 0.30 * cua_pct + 0.25 * sup_pct + 0.25 * part_pct + 0.20 * soi_pct',
                                             'status': 'Paul ruling 2026-10-07 (O-11: candidate support counts in the overall, 30/25/25/20)'})
        s = oregon['support']
        self.assertEqual((Path(s['pct_file']).name, s['sha256'], s['party'], s['race_mode']), ('support.npz', None, 'D', 'general'))
        self.assertEqual(s['signals'], D.get('or-05')['support']['signals'])
        self.assertTrue('overall' not in colorado and 'support' not in colorado)
        docs = lambda tmp, name: (Path(tmp) / 'build' / 'rowfw-1.0.0' / name).read_text(encoding='utf-8')
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:                       # the copy's own notes say so, for a candidate only
            rowfw.build_copy(tmp, oregon, 'Oregon OR-05')
            self.assertIn('Phase 4B, O-11', docs(tmp, 'DEVIATIONS.md'))
            self.assertIn('W30_25_25_20', docs(tmp, 'RULINGS.md'))
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            rowfw.build_copy(tmp, colorado, 'Colorado replay')
            self.assertNotIn('O-11', docs(tmp, 'DEVIATIONS.md'))


class SupportStepTests(unittest.TestCase):
    def cache(self, tmp, msr=None, flip=False):
        n = lambda d, r, u: pa.array([d] * 4 + [r] * 4 + [u] * 4, pa.int8())
        path = Path(tmp) / 'inputs.parquet'
        pq.write_table(pa.table({
            'model_source_row': pa.array(msr or list(range(1, 13)), pa.int32()),
            'sai_partisan_score': n(20, 80, 50) if flip else n(80, 20, 50), 'sai_harris_support_score': n(80, 20, 50), 'sai_trump_support_score': n(20, 80, 50),
            'svi_party_registration': pa.array(['D'] * 4 + ['R'] * 4 + ['U'] * 4).dictionary_encode(),
            'svi_vh_2024p_party': pa.array(['D'] * 4 + ['R'] * 4 + [None] * 4, pa.string()).dictionary_encode(),     # as the real cache: nulls in a dictionary column
            'svi_vh_2010p_party': pa.array([None] * 8 + ['R'] * 4, pa.string())}), path)      # 2010 lies outside ballot_years (2014, 2025)
        return path

    def test_support_file_scores_toward_the_candidate_party_and_reports_aggregates(self):
        d = D.get('or-05')
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            out = Path(tmp) / 'support.npz'
            result = rowfw.write_support(self.cache(tmp), out, d)
            got = np.load(out)
            self.assertEqual(sorted(got.files), ['in_universe', 'model_source_row', 'sup_pct', 'sup_score'])
            self.assertEqual(got['model_source_row'].tolist(), list(range(1, 13)))
            np.testing.assert_allclose(got['sup_score'], [90.0] * 4 + [10.0] * 4 + [50.0] * 4)      # the 2010 'R' ballot is outside the window
            np.testing.assert_allclose(got['sup_pct'], [100 * 10 / 12] * 4 + [100 * 2 / 12] * 4 + [50.0] * 4)
            self.assertTrue(got['in_universe'].all())
            self.assertFalse((Path(tmp) / 'support.npz.partial').exists())
            got.close()
        self.assertEqual(result['rows'], 12)
        self.assertEqual(result['coverage'], {'partisan': 12, 'presidential': 12, 'registration': 12, 'primary_ballot': 8})
        self.assertEqual(result['orientation_means']['sai_partisan_score'], {'D': 80.0, 'R': 20.0})
        self.assertEqual(result['sup_score_quantiles']['0'], 10.0)

    def test_support_file_stops_on_a_flipped_score_a_misordered_cache_and_primary_mode(self):
        d = D.get('or-05')
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            out = Path(tmp) / 'support.npz'
            with self.assertRaises(ValueError):
                rowfw.write_support(self.cache(tmp, flip=True), out, d)
            with self.assertRaises(AssertionError):
                rowfw.write_support(self.cache(tmp, msr=[2, 1] + list(range(3, 13))), out, d)
            with self.assertRaises(SystemExit):
                rowfw.write_support(self.cache(tmp), out, {**d, 'race_mode': 'primary'})
            self.assertFalse(out.exists())


class ResetTests(unittest.TestCase):
    def test_reset_moves_the_run_outputs_and_nothing_else(self):
        moved_top = ['framework_config.json', 'criteria_rowfw-1.0.0.json', 'PLAN_rowfw-1.0.0.md', 'PLAN.receipt.json', 'area_scores_rowfw-1.0.0.csv',
                     'row_scores_rowfw-1.0.0.parquet', 'row_scores_rowfw-1.0.0.manifest.json', 'profiles_rowfw-1.0.0.parquet',
                     'household_scores_rowfw-1.0.0.manifest.json', 'summaries', 'release', 'tests/T00.json', 'tests/T03.json', 'tests/predicate_io',
                     'tests/superseded']
        files = ['summaries/a.csv', 'release/stage_receipts.json', 'tests/predicate_io/release/x.json', 'tests/superseded/2026_P1_pre/SNAPSHOT_MANIFEST.json']
        kept = ['rowfw_build.py', 'rowfw/engine.py', 'RULINGS.md', 'DEVIATIONS.md', 'PREREG_2026_GENERAL.md', 'freeze/freeze_rowfw.py', 'tools/t.py',
                'docs/data_dictionary.json', 'tests/fixtures/f.json', 'tests/predicates/p.py', 'cache/inputs.parquet']
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            B, dest = Path(tmp) / 'build', Path(tmp) / 'superseded' / '3b-x'
            written = [r for r in moved_top if r not in ('summaries', 'release', 'tests/predicate_io', 'tests/superseded')] + files + kept
            for rel in written:
                (B / rel).parent.mkdir(parents=True, exist_ok=True)
                (B / rel).write_text(rel, encoding='utf-8')
            self.assertEqual(sorted(rowfw.move_outputs(B, dest)), sorted(moved_top))
            for rel in written:
                self.assertEqual((B / rel).exists(), rel in kept, rel)
                self.assertEqual((dest / rel).exists(), rel not in kept, rel)
            self.assertEqual(rowfw.move_outputs(B, Path(tmp) / 'again'), [])                       # nothing left: a second reset moves nothing


if __name__ == '__main__':
    unittest.main()
