"""End-to-end test of the offline loader on the synthetic fixture (system Python with pyarrow, no Django).

    env -u PYTHONPATH PYTHONNOUSERSITE=1 /c/Users/pgoff/AppData/Local/Programs/Python/Python312/python.exe \\
        -m unittest tools.test_build_scores -v          (from the V3 folder)

The fixture is invented: its contact layer carries SENTINEL_* names, addresses, emails and phones so the test can prove
that none of them reaches scores.sqlite3. The loader runs in full (manifest hashes, W1 census with the shifted control,
contribution sums, band rule, street mapping) and must fail closed on every tampered input.
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import unittest
import zlib
from pathlib import Path

import numpy as np

from tools import districts as D
from tools import field_labels as FL
from tools import glassbox
from tools import make_scores_fixture as MF
from tools import scores_schema as S
from tools import build_scores_v3 as B


class LoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.root = Path(cls.temporary.name)
        cls._events = (glassbox.EVENT_DIR, glassbox.MIRROR)
        glassbox.EVENT_DIR, glassbox.MIRROR = cls.root / 'events', cls.root / 'events.jsonl'
        cls.fx = MF.make_store(cls.root / 'fx', parquet=True)
        cls.out, cls.receipt_path = cls.root / 'loaded.sqlite3', cls.root / 'receipt.json'
        cls.code = cls.run_loader(cls.out, cls.receipt_path)
        cls.db = sqlite3.connect('file:%s?mode=ro' % cls.out.as_posix(), uri=True)
        cls.db.row_factory = sqlite3.Row

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        glassbox.EVENT_DIR, glassbox.MIRROR = cls._events
        cls.temporary.cleanup()

    @classmethod
    def arguments(cls, out, receipt, rowfw=None, contacts=None, w1='verify'):
        fx = cls.fx
        return ['--rowfw', str(rowfw or fx.rowfw_dir), '--cache', str(fx.cache_path),
                '--contacts', str(contacts or fx.contacts_path), '--street', str(fx.street_path),
                '--source-csv', str(fx.source_csv), '--salt-file', str(fx.directory / '.street-salt'),
                '--w1', w1, '--out', str(out), '--receipt', str(receipt), '--low-memory']

    @classmethod
    def run_loader(cls, out, receipt, **kwargs):
        return B.main(cls.arguments(out, receipt, **kwargs))

    def scalar(self, sql, *args):
        return self.db.execute(sql, args).fetchone()[0]

    # ---- the build and its receipt ------------------------------------------------------------------------------
    def test_build_succeeds_and_counts_match_the_fixture(self):
        self.assertEqual(self.code, 0)
        receipt = json.loads(self.receipt_path.read_text(encoding='utf-8'))
        self.assertEqual((receipt['rows'], receipt['households']), (self.fx.n, len(self.fx.data['household']['hh'])))
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM person'), self.fx.n)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM evidence'), self.fx.n)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM household'), receipt['households'])
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM demo_name'), self.fx.n)
        self.assertEqual(self.scalar('PRAGMA integrity_check'), 'ok')
        self.assertFalse((self.out.parent / (self.out.name + '.building')).exists())

    def test_w1_census_matches_every_row_and_the_shifted_control_matches_none(self):
        w1 = json.loads(self.receipt_path.read_text(encoding='utf-8'))['w1']
        self.assertEqual((w1['status'], w1['rows_checked'], w1['matches'], w1['shifted_control_matches']),
                         ('verified', self.fx.n, self.fx.n, 0))
        stored = json.loads(self.scalar("SELECT value FROM metadata WHERE key='w1'"))
        self.assertEqual(stored['matches'], self.fx.n)

    def test_the_same_scores_as_the_direct_fixture_store(self):
        direct = sqlite3.connect('file:%s?mode=ro' % self.fx.scores_path.as_posix(), uri=True)
        try:
            for column in ('person_id', 'ovr_grade', 'reg_group', 'reg_block', 'hh', 'age'):
                got = [r[0] for r in self.db.execute('SELECT %s FROM person ORDER BY pos' % column)]
                want = [r[0] for r in direct.execute('SELECT %s FROM person ORDER BY pos' % column)]
                self.assertEqual(got, want, column)
            for column in ('ovr_pct', 'cua_pct', 'lean', 'ovr_pct_within_reg'):
                got = np.array([r[0] for r in self.db.execute('SELECT %s FROM person ORDER BY pos' % column)])
                want = np.array([r[0] for r in direct.execute('SELECT %s FROM person ORDER BY pos' % column)])
                self.assertTrue(np.allclose(got, want, atol=1e-4), column)
        finally:
            direct.close()

    # ---- privacy of the file --------------------------------------------------------------------------------------
    def test_no_name_address_email_phone_coordinate_or_source_row_is_stored(self):
        tables = [r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE "
                                                "'demo_search%'")]
        forbidden_columns = re.compile(r'name$|address|email|phone|latitude|longitude|model_source_row|persist', re.I)
        for table in tables:
            for row in self.db.execute('PRAGMA table_info(%s)' % table):
                if table in ('demo_name', 'list_column', 'list_dict') and row['name'] == 'name':
                    continue                                    # synthetic display names / names of list columns
                self.assertIsNone(forbidden_columns.search(row['name']), (table, row['name']))
        sentinel = re.compile(rb'SENTINEL|@example\.invalid|555-\d{4}')
        raw = self.out.read_bytes()
        self.assertIsNone(sentinel.search(raw))
        for ident, in self.db.execute('SELECT person_id FROM person'):
            self.assertRegex(ident, r'^p_[0-9a-f]{64}$')
        for ident, in self.db.execute('SELECT h_id FROM street_household'):
            self.assertRegex(ident, r'^h_[0-9a-f]{64}$')
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM pragma_table_info('person') WHERE name='model_source_row'"), 0)
        # contacts text never appears in the metadata either
        for key, value in self.db.execute('SELECT key, value FROM metadata'):
            self.assertNotIn('SENTINEL', value, key)

    # ---- indexes ---------------------------------------------------------------------------------------------------
    def plan(self, sql, *args):
        return ' | '.join(r[3] for r in self.db.execute('EXPLAIN QUERY PLAN ' + sql, args))

    def test_each_list_shape_uses_an_index(self):
        first = self.db.execute('SELECT * FROM person WHERE pos=1').fetchone()
        shapes = {
            'registration': ('SELECT pos FROM person WHERE reg_group=? ORDER BY pos LIMIT 51', (1,), 'person_reg'),
            'grade': ("SELECT pos FROM person WHERE ovr_grade IN ('A','B') ORDER BY pos LIMIT 51", (), 'person_grade'),
            'lean': ('SELECT pos FROM person WHERE lean_band=? AND lean_undet=0 ORDER BY pos LIMIT 51', (1,),
                     'person_lean'),
            'block': ('SELECT pos FROM person WHERE reg_block=? ORDER BY pos LIMIT 51', (first['reg_block'],),
                      'person_reg_block'),
            'precinct': ('SELECT pos FROM person WHERE reg_precinct=? ORDER BY pos LIMIT 51', (first['reg_precinct'],),
                         'person_reg_precinct'),
            'tract': ('SELECT pos FROM person WHERE reg_tract=? ORDER BY pos LIMIT 51', (first['reg_tract'],),
                      'person_reg_tract'),
            'zip': ('SELECT pos FROM person WHERE reg_zip=? ORDER BY pos LIMIT 51', (first['reg_zip'],), 'person_reg_zip'),
            'household members': ('SELECT pos, person_id, age, ovr_pct, ovr_pct_lo90, ovr_pct_hi90, ovr_grade, '
                                  'lean_band, lean_undet FROM person WHERE hh=? ORDER BY pos', (first['hh'],),
                                  'person_hh'),
            'person by id': ('SELECT * FROM person WHERE person_id=?', (first['person_id'],), 'person_id'),
            'within registration': ('SELECT pos FROM person WHERE reg_group=? AND reg_rank<=? ORDER BY pos', (1, 50),
                                    'person_reg'),
        }
        for name, (sql, args, index) in shapes.items():
            plan = self.plan(sql, *args)
            self.assertIn(index, plan, (name, plan))
            self.assertNotRegex(plan, r'SCAN person(?! USING)', (name, plan))
        for name, sql in (('evidence', 'SELECT blob FROM evidence WHERE pos=1'),
                          ('street household', "SELECT hh FROM street_household WHERE h_id='h_x'"),
                          ('household', 'SELECT * FROM household WHERE hh=1')):
            self.assertNotRegex(self.plan(sql), r'SCAN (evidence|street_household|household)(?! USING)', name)
        # the household and registration lists read only the index (no table lookup)
        self.assertIn('COVERING INDEX person_hh', self.plan(shapes['household members'][0], first['hh']))
        self.assertIn('COVERING INDEX person_reg', self.plan(shapes['registration'][0], 1))

    # ---- the numbers ---------------------------------------------------------------------------------------------------
    def evidence(self, pos):
        raw = zlib.decompress(self.db.execute('SELECT blob FROM evidence WHERE pos=?', (pos,)).fetchone()[0])
        return np.frombuffer(raw, '<f4', 40, 280).astype(np.float64)

    def test_field_contributions_add_up_to_the_scores(self):
        points = np.array([f['points'] for f in FL.FIELDS])
        points50 = np.array([f['points_code50'] for f in FL.FIELDS])
        groups = {g: [i for i, f in enumerate(FL.FIELDS) if f['group'] == g] for g in FL.GROUPS}
        for pos in range(1, self.fx.n + 1, 7):
            r = self.db.execute('SELECT * FROM person WHERE pos=?', (pos,)).fetchone()
            exposure = np.nan_to_num(self.evidence(pos), nan=0.0)
            code50 = bool(r['flags'] & (1 << S.FLAG_BITS.index('flag_digital_code50')))
            contribution = exposure * (points50 if code50 else points)
            for group, column in (('cua', 'cua_score'), ('part', 'part_score'), ('soi', 'soi_score'), ('lean', 'lean')):
                self.assertAlmostEqual(contribution[groups[group]].sum(), r[column], delta=1e-3, msg=(pos, group))

    def test_lean_band_rule_and_registration_groups(self):
        for r in self.db.execute('SELECT lean, lean_band, party, reg_group FROM person'):
            near_cut = min(abs(abs(r['lean']) - 25.0), abs(abs(r['lean']) - 75.0)) < 1e-4
            if not near_cut:
                self.assertEqual(FL.lean_band_of(r['lean']), r['lean_band'])
            self.assertEqual(FL.REG_GROUPS[r['reg_group']], FL.reg_group_of(r['party']))
        counts = json.loads(self.scalar("SELECT value FROM metadata WHERE key='reg_group_counts'"))
        for i, code in enumerate(FL.REG_GROUPS):
            self.assertEqual(counts[code], self.scalar('SELECT COUNT(*) FROM person WHERE reg_group=?', i))

    def test_balanced_allocations_are_recorded_for_the_three_frozen_k_values(self):
        balanced = json.loads(self.scalar("SELECT value FROM metadata WHERE key='balanced'"))
        self.assertEqual(sorted(balanced['k']), ['100', '4913', '49131'])
        k100 = balanced['k']['100']
        self.assertEqual(sum(k100['K_g'].values()), 100)
        self.assertEqual(k100['K_actual'], k100['K_g'])       # no tie crosses the cut in the fixture
        for k, entry in balanced['k'].items():
            for code in FL.REG_GROUPS:
                self.assertGreaterEqual(entry['K_actual'][code], min(entry['K_g'][code], balanced['counts'][code]))

    def test_balanced_allocations_follow_the_k_values_of_the_frozen_config(self):
        # a district's config names its own K (OR-05: 100, 1% and 10% of its rows); Colorado's three stay the default above
        folder = self.root / 'rowfw_k'
        shutil.copytree(self.fx.rowfw_dir, folder)
        path = folder / 'framework_config.json'
        config = json.loads(path.read_text(encoding='utf-8'))
        config['top_sets'] = {'balanced_by_registration_view': {'K': [100, 7, 70]}}
        path.write_text(json.dumps(config), encoding='utf-8')
        for stem in B.STEMS:
            manifest_path = folder / ('%s_rowfw-1.0.0.manifest.json' % stem)
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            manifest['config_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        out = self.root / 'k.sqlite3'
        self.assertEqual(self.run_loader(out, self.root / 'k.json', rowfw=folder), 0)
        db = sqlite3.connect('file:%s?mode=ro' % out.as_posix(), uri=True)
        try:
            balanced = json.loads(db.execute("SELECT value FROM metadata WHERE key='balanced'").fetchone()[0])
        finally:
            db.close()
        self.assertEqual(sorted(balanced['k']), ['100', '7', '70'])
        self.assertEqual(sum(balanced['k']['70']['K_g'].values()), 70)

    def test_a_group_with_no_seats_is_checked_against_zero_not_the_builds_wrapped_k_actual(self):
        computed = {'K_g': {'O': 0, 'D': 3}, 'K_actual': {'O': 0, 'D': 4}}
        self.assertTrue(B.balanced_row_matches(computed, {'K_g': '0', 'K_actual': '851'}, 'O'))      # CT other at K=100: rowfw wrapped to the whole group
        self.assertFalse(B.balanced_row_matches({'K_g': {'O': 0}, 'K_actual': {'O': 2}}, {'K_g': '0', 'K_actual': '851'}, 'O'))
        self.assertTrue(B.balanced_row_matches(computed, {'K_g': '3', 'K_actual': '4'}, 'D'))
        self.assertFalse(B.balanced_row_matches(computed, {'K_g': '3', 'K_actual': '5'}, 'D'))        # a group with seats still must agree

    def test_households_map_to_one_household_code_and_street_rows_have_a_bin(self):
        rows = self.db.execute('SELECT h_id, hh, n_people, ovr_mean_pct, bin, reg_block FROM street_household').fetchall()
        self.assertGreater(len(rows), 100)
        for r in rows:
            members = self.db.execute('SELECT COUNT(*), AVG(ovr_pct) FROM person WHERE hh=?', (r['hh'],)).fetchone()
            self.assertLessEqual(r['n_people'], members[0])
            self.assertIn(r['bin'], (1, 2, 3, 4, 5))
            self.assertEqual(r['bin'], 1 + sum(1 for cut in (20, 40, 60, 80) if cut <= r['ovr_mean_pct']))
            self.assertTrue(re.fullmatch(r'\d{15}', r['reg_block']))
        self.assertEqual(self.scalar('SELECT COUNT(DISTINCT hh) FROM street_household'), len(rows))

    def test_store_size_is_small_per_person(self):
        self.assertLess(self.out.stat().st_size / self.fx.n, 5000)         # the real store is about 1.2 KB per person

    # ---- fail closed ---------------------------------------------------------------------------------------------------
    def tampered(self, name, edit):
        """Run the loader on a copy of the P1 folder that `edit(folder)` has damaged; the previous store must survive."""
        folder = self.root / ('rowfw_' + name)
        shutil.copytree(self.fx.rowfw_dir, folder)
        edit(folder)
        out, receipt = self.root / (name + '.sqlite3'), self.root / (name + '.json')
        out.write_bytes(self.out.read_bytes())
        before = out.read_bytes()
        code = self.run_loader(out, receipt, rowfw=folder)
        self.assertEqual(code, 2, name)
        self.assertEqual(out.read_bytes(), before, name + ': the previous store was touched')
        self.assertFalse(receipt.exists(), name)

    def test_parquet_sha_mismatch_stops_the_build(self):
        def edit(folder):
            path = folder / 'row_scores_rowfw-1.0.0.parquet'
            data = bytearray(path.read_bytes())
            data[len(data) // 2] ^= 0x55
            path.write_bytes(bytes(data))
        self.tampered('flipped_parquet', edit)

    def test_manifest_changes_stop_the_build(self):
        def edit(folder):
            path = folder / 'profiles_rowfw-1.0.0.manifest.json'
            manifest = json.loads(path.read_text(encoding='utf-8'))
            manifest['rows'] += 1
            path.write_text(json.dumps(manifest), encoding='utf-8')
        self.tampered('manifest_rows', edit)

    def test_area_table_with_a_residence_block_or_the_hatch_column_stops_the_build(self):
        def residence_block(folder):
            path = folder / 'area_scores_rowfw-1.0.0.csv'
            lines = path.read_text(encoding='utf-8').splitlines()
            header = lines[0].split(',')
            row = next(l for l in lines[1:] if l.startswith('registration,block'))
            path.write_text('\n'.join(lines + [row.replace('registration,block', 'residence,block', 1)]) + '\n',
                            encoding='utf-8')
        self.tampered('residence_block', residence_block)

        def hatch(folder):
            path = folder / 'area_scores_rowfw-1.0.0.csv'
            lines = path.read_text(encoding='utf-8').splitlines()
            lines[0] += ',cua_hatch_cpi_blank'
            path.write_text('\n'.join([lines[0]] + [l + ',' for l in lines[1:]]) + '\n', encoding='utf-8')
        self.tampered('hatch_column', hatch)

    def test_crosswalk_that_is_off_by_one_or_wrong_stops_the_build(self):
        for name, edit in (('rotated', lambda ids: ids[1:] + ids[:1]), ('swapped', lambda ids: [ids[1], ids[0]] + ids[2:])):
            contacts = self.root / (name + '_contacts.sqlite3')
            shutil.copyfile(self.fx.contacts_path, contacts)
            connection = sqlite3.connect(contacts)
            ids = [r[0] for r in connection.execute('SELECT id FROM contacts ORDER BY rowid')]
            new = edit(ids)
            connection.execute('UPDATE contacts SET id = id || "_"')            # free the unique keys first
            for rowid, ident in enumerate(new, start=1):
                connection.execute('UPDATE contacts SET id=? WHERE rowid=?', (ident, rowid))
            connection.commit()
            connection.close()
            out = self.root / (name + '.sqlite3')
            code = self.run_loader(out, self.root / (name + '.json'), contacts=contacts)
            self.assertEqual(code, 2, name)
            self.assertFalse(out.exists(), name)

    def test_street_and_contacts_from_different_sources_stop_the_build(self):
        contacts = self.root / 'other_source_contacts.sqlite3'
        shutil.copyfile(self.fx.contacts_path, contacts)
        connection = sqlite3.connect(contacts)
        connection.execute("UPDATE metadata SET value=? WHERE key='source_sha256'", (json.dumps('0' * 64),))
        connection.commit()
        connection.close()
        out = self.root / 'other_source.sqlite3'
        self.assertEqual(self.run_loader(out, self.root / 'other_source.json', contacts=contacts), 2)
        self.assertFalse(out.exists())


class DistrictInputTests(unittest.TestCase):
    def test_a_null_geography_value_becomes_an_empty_string_in_both_column_layouts(self):
        import pyarrow as pa
        table = pa.table({'d': pa.array(['b', None, 'a', 'b']).dictionary_encode(), 'p': pa.array(['x', None, 'y', 'z'])})
        self.assertEqual(B.strings(table, 'd').tolist(), ['b', '', 'a', 'b'])
        self.assertEqual(B.strings(table, 'p').tolist(), ['x', '', 'y', 'z'])
        clean = pa.table({'d': pa.array(['b', 'a']).dictionary_encode()})
        self.assertEqual(B.strings(clean, 'd').tolist(), ['b', 'a'])

    def test_minor_party_group_may_gain_a_code_but_never_hold_d_r_or_u(self):
        co = {'D': ['D'], 'R': ['R'], 'U': ['U'], 'other': ['G', 'L', 'O', 'T']}
        self.assertTrue(B.registration_groups_ok(co))
        self.assertTrue(B.registration_groups_ok({**co, 'other': ['G', 'L', 'O', 'T', 'W']}))             # OR-05 and CT-01 add W
        self.assertFalse(B.registration_groups_ok({**co, 'other': ['G', 'D']}))
        self.assertFalse(B.registration_groups_ok({**co, 'U': ['U', 'X']}))
        self.assertFalse(B.registration_groups_ok({'D': ['D'], 'R': ['R'], 'other': ['G']}))

    def test_k_values_come_from_the_config_with_colorados_as_the_fallback(self):
        self.assertEqual(B.k_values({'top_sets': {'balanced_by_registration_view': {'K': [100, 5626, 56256]}}}), (100, 5626, 56256))
        self.assertEqual(B.k_values({}), B.W.K_VALUES)
        self.assertEqual(B.W.K_VALUES, (100, 4913, 49131))


class RollupCheckTests(unittest.TestCase):
    def test_co_needs_the_rollup_to_equal_the_party_mapping_and_or_skips_it(self):
        mapped = np.array(['D', 'R', 'U', 'O'])
        flipped = mapped.copy()
        flipped[2] = 'O'
        B.check_rollup(D.get('co-05'), mapped.copy(), mapped)
        with self.assertRaises(B.BuildError):
            B.check_rollup(D.get('co-05'), flipped, mapped)
        B.check_rollup(D.get('or-05'), flipped, mapped)


if __name__ == '__main__':
    unittest.main()
