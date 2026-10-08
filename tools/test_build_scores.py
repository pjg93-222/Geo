"""End-to-end test of the offline loader on the synthetic fixture (system Python with pyarrow, no Django).

    env -u PYTHONPATH PYTHONNOUSERSITE=1 /c/Users/pgoff/AppData/Local/Programs/Python/Python312/python.exe \\
        -m unittest tools.test_build_scores -v          (from the V3 folder)

The fixture is invented: its contact layer carries SENTINEL_* names, addresses, emails and phones so the test can prove
that none of them reaches scores.sqlite3. The loader runs in full (manifest hashes, W1 census with the shifted control,
contribution sums, band rule, street mapping) and must fail closed on every tampered input.
"""
import contextlib
import hashlib
import io
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
from tools import scores_writer as W
from tools import support as SUP


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
    def arguments(cls, out, receipt, rowfw=None, contacts=None, w1='verify', area_table=None):
        fx = cls.fx
        return ['--rowfw', str(rowfw or fx.rowfw_dir), '--cache', str(fx.cache_path), '--area-table', str(area_table or fx.area_csv),
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
    def tampered(self, name, edit=None, area_table=None):
        """Run the loader on a copy of the P1 folder that `edit(folder)` has damaged, or on `area_table`; the previous store must survive."""
        folder = self.root / ('rowfw_' + name)
        shutil.copytree(self.fx.rowfw_dir, folder)
        if edit:
            edit(folder)
        out, receipt = self.root / (name + '.sqlite3'), self.root / (name + '.json')
        out.write_bytes(self.out.read_bytes())
        before = out.read_bytes()
        code = self.run_loader(out, receipt, rowfw=folder, area_table=area_table)
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

    def table_copy(self, name, edit):
        """A copy of the shown-areas table whose lines `edit(lines)` has changed, with a manifest that follows the new bytes."""
        path = self.root / (name + '_area.csv')
        path.write_text('\n'.join(edit(self.fx.area_csv.read_text(encoding='utf-8').splitlines())) + '\n', encoding='utf-8')
        path.with_suffix('.manifest.json').write_text(json.dumps({'sha256': W.sha256_file(path)}), encoding='utf-8')
        return path

    def test_area_table_with_a_residence_block_a_suppressed_row_or_the_hatch_column_stops_the_build(self):
        def residence_block(lines):
            row = next(l for l in lines[1:] if l.startswith('registration,block'))
            return lines + [row.replace('registration,block', 'residence,block', 1)]
        self.tampered('residence_block', area_table=self.table_copy('residence_block', residence_block))

        def suppressed(lines):                      # the table shows every area: a row marked suppressed is not its
            cells = lines[1].split(',')
            cells[lines[0].split(',').index('suppressed')] = '1'
            return [lines[0], ','.join(cells)] + lines[2:]
        self.tampered('suppressed_row', area_table=self.table_copy('suppressed_row', suppressed))

        def hatch(lines):
            return [lines[0] + ',cua_hatch_cpi_blank'] + [l + ',' for l in lines[1:]]
        self.tampered('hatch_column', area_table=self.table_copy('hatch_column', hatch))

    def test_a_missing_or_altered_shown_areas_table_stops_the_build(self):
        self.tampered('no_table', area_table=self.root / 'there_is_no_such_table.csv')
        altered = self.root / 'altered_area.csv'                                    # its manifest still holds the old sha256
        altered.write_text(self.fx.area_csv.read_text(encoding='utf-8') + '\n', encoding='utf-8')
        altered.with_suffix('.manifest.json').write_text(self.fx.area_csv.with_suffix('.manifest.json').read_text(encoding='utf-8'))
        self.tampered('altered_table', area_table=altered)

    def test_the_store_records_the_shown_areas_table_and_no_suppressed_list(self):
        meta = {k: json.loads(v) for k, v in self.db.execute('SELECT key, value FROM metadata')}
        self.assertEqual(meta['area_table_sha256'], self.fx.area_sha)
        self.assertNotIn('suppressed_ids_sha256', meta)
        self.assertEqual(set(meta['area_table']), {'area_table_sha256', 'levels', 'manifest_sha256'})
        self.assertTrue(all(set(v) == {'areas'} for v in meta['area_table']['levels'].values()))

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


class SupportLoaderTests(unittest.TestCase):
    """Phase 4B: a candidate district's store gets the `support` table, the `ord_sup` list and the `support` metadata key, after the
    loader has checked the frozen 4-term overall, the pin on cache/support.npz, the file against tools/support.py and every row's
    overall. The invented district is Oregon's entry with the weights 40/0/35/25, because the fixture's overall is the 3-term one:
    the sup term is read from the file like the real one, and the real 30/25/25/20 sum is checked on hand numbers below."""

    DISTRICT = {**D.get('or-05'), 'score_weights': {'cua': 40, 'sup': 0, 'part': 35, 'soi': 25}}

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.root = Path(cls.temporary.name)
        cls._events = (glassbox.EVENT_DIR, glassbox.MIRROR)
        glassbox.EVENT_DIR, glassbox.MIRROR = cls.root / 'events', cls.root / 'events.jsonl'
        cls.fx = MF.make_store(cls.root / 'fx', parquet=True)

    @classmethod
    def tearDownClass(cls):
        glassbox.EVENT_DIR, glassbox.MIRROR = cls._events
        cls.temporary.cleanup()

    def plain(self, name):
        """The fixture's P1 folder and cache as they are (no support columns, no overall block)."""
        folder = self.root / name
        shutil.copytree(self.fx.rowfw_dir, folder / 'rowfw')
        shutil.copyfile(self.fx.cache_path, folder / 'inputs.parquet')
        return folder

    def world(self, name, flip=False, tweak=None, pin=True):
        """The plain folder with the support inputs added to the cache, support.npz beside it, and a config that freezes the 4-term
        overall and pins the file. `flip` turns the partisan score the wrong way; `tweak` edits the arrays before they are saved."""
        import pyarrow as pa
        import pyarrow.parquet as pq
        fx, district, folder = self.fx, self.DISTRICT, self.plain(name)
        rng = np.random.default_rng(11)
        party = np.asarray(fx.data['party'])
        lean = np.clip(50 + 30 * (party == 'D') - 30 * (party == 'R') + rng.normal(0, 8, fx.n), 1, 99)
        ballot = [('D' if rng.random() < .6 else 'R') if p in ('D', 'R') and rng.random() < .3 else None for p in party.tolist()]
        cache = pq.read_table(fx.cache_path)
        for column, values in (('sai_partisan_score', 100 - lean if flip else lean), ('sai_harris_support_score', lean),
                               ('sai_trump_support_score', 100 - lean)):
            cache = cache.append_column(column, pa.array(values))
        cache = cache.append_column('svi_vh_2024p_party', pa.array(ballot, type=pa.string()).dictionary_encode())
        cache_path = folder / 'inputs.parquet'
        pq.write_table(cache, cache_path, compression='zstd')
        result = SUP.compute(district, B.support_columns(pq, cache_path, district))
        arrays = {'model_source_row': np.arange(1, fx.n + 1), 'sup_score': result['sup_score'], 'sup_pct': result['sup_pct'],
                  'in_universe': result['in_universe']}
        if tweak:
            tweak(arrays)
        np.savez(folder / 'support.npz', **arrays)
        sha = W.sha256_file(folder / 'support.npz')
        key = 'W' + '_'.join(str(v) for v in district['score_weights'].values())
        path = folder / 'rowfw' / 'framework_config.json'
        config = json.loads(path.read_text(encoding='utf-8'))
        config['overall'] = {'default': key, key: {
            'weights': {g: {'value': v / 100.0} for g, v in district['score_weights'].items()},
            'support': {'sha256': sha if pin else '0' * 64, 'candidate': district['candidate'], 'party': district['party'],
                        'race_mode': district['race_mode'], 'signals': district['support']['signals']}}}
        path.write_text(json.dumps(config), encoding='utf-8')
        for stem in B.STEMS:
            manifest_path = folder / 'rowfw' / ('%s_rowfw-1.0.0.manifest.json' % stem)
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            manifest['config_sha256'] = W.sha256_file(path)
            manifest['inputs']['cache_sha256'] = W.sha256_file(cache_path)
            manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        return folder

    def run_loader(self, folder, name, district):
        fx, out = self.fx, self.root / (name + '.sqlite3')
        argv = ['--rowfw', str(folder / 'rowfw'), '--cache', str(folder / 'inputs.parquet'), '--area-table', str(fx.area_csv),
                '--contacts', str(fx.contacts_path),
                '--street', str(fx.street_path), '--source-csv', str(fx.source_csv), '--salt-file', str(fx.directory / '.street-salt'),
                '--w1', 'verify', '--out', str(out), '--receipt', str(self.root / (name + '.json')), '--low-memory']
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            code = B.main(argv, district=district)
        return code, out, error.getvalue()

    def test_a_candidate_store_holds_the_support_table_ord_sup_and_the_metadata_key(self):
        folder = self.world('ok')
        code, out, error = self.run_loader(folder, 'ok', self.DISTRICT)
        self.assertEqual((code, error), (0, ''))
        file = np.load(folder / 'support.npz')
        db = sqlite3.connect('file:%s?mode=ro' % out.as_posix(), uri=True)
        try:
            self.assertEqual([r[1] for r in db.execute('PRAGMA table_info(support)')],
                             ['pos', 'sup_score', 'sup_pct', 'sup_rank', 'sup_tie_n', 'in_universe'])
            by_id = dict(zip(np.asarray(self.fx.data['person_id']).tolist(), file['sup_score'].tolist()))
            rows = db.execute('SELECT p.person_id, s.sup_score, s.sup_rank, s.in_universe FROM person p JOIN support s ON s.pos = p.pos').fetchall()
            self.assertEqual(len(rows), self.fx.n)
            self.assertTrue(all(by_id[person] == score and universe == 1 for person, score, _, universe in rows))
            self.assertEqual({rank for _, score, rank, _ in rows if score == max(by_id.values())}, {1})
            meta = {k: json.loads(v) for k, v in db.execute('SELECT key, value FROM metadata')}
            support = meta['support']
            self.assertEqual((support['candidate'], support['party'], support['race_mode']), ('Janelle Bynum', 'D', 'general'))
            self.assertEqual(support['sha256'], W.sha256_file(folder / 'support.npz'))
            self.assertEqual(sorted(support['coverage']), sorted(SUP.SIGNALS))
            self.assertEqual(support['coverage']['partisan'], self.fx.n)
            self.assertTrue(0 < support['coverage']['primary_ballot'] < self.fx.n)
            self.assertLessEqual(support['overall_max_abs_diff'], 1e-4)
            self.assertEqual(meta['list_columns']['ord_sup']['n'], self.fx.n)
            order = np.frombuffer(db.execute("SELECT data FROM list_column WHERE name='ord_sup'").fetchone()[0], dtype='<i4')
            ranks = [db.execute('SELECT sup_rank FROM support WHERE pos = ?', (int(i) + 1,)).fetchone()[0] for i in order[:60]]
            self.assertEqual((ranks[0], ranks), (1, sorted(ranks)))
        finally:
            db.close()

    def test_a_district_without_a_candidate_writes_no_support_table_key_or_list(self):
        code, out, error = self.run_loader(self.plain('plain'), 'plain', D.get('co-05'))
        self.assertEqual((code, error), (0, ''))
        db = sqlite3.connect('file:%s?mode=ro' % out.as_posix(), uri=True)
        try:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'support'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM metadata WHERE key = 'support'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM list_column WHERE name = 'ord_sup'").fetchone()[0], 0)
        finally:
            db.close()

    def stops(self, name, message, folder=None, district=None, **world):
        code, out, error = self.run_loader(folder or self.world(name, **world), name, district or self.DISTRICT)
        self.assertEqual(code, 2, (name, error))
        self.assertIn(message, error, name)
        self.assertFalse(out.exists(), name)

    def test_the_loader_stops_on_every_broken_link_between_the_file_the_config_and_the_cache(self):
        self.stops('unpinned', 'is not the file the frozen config pins', pin=False)
        self.stops('shifted_rows', 'rows are not keys 1..N', tweak=lambda a: a.update(model_source_row=a['model_source_row'] + 1))
        self.stops('other_score', 'differs from tools/support.py', tweak=lambda a: a['sup_score'].__setitem__(0, a['sup_score'][0] + 1))
        self.stops('flipped', 'orientation', flip=True)
        self.stops('primary', 'general mode only', folder=self.world('primary_w'), district={**self.DISTRICT, 'race_mode': 'primary'})
        self.stops('other_weights', 'The frozen overall is W40_0_35_25, not W40_0_30_30', folder=self.world('weights_w'),
                   district={**self.DISTRICT, 'score_weights': {'cua': 40, 'sup': 0, 'part': 30, 'soi': 30}})
        self.stops('no_overall', 'rerun the row framework with candidate support', folder=self.plain('no_overall'))

    def test_the_overall_must_recompute_from_the_standings_and_the_support_file(self):
        w = {'cua': .30, 'sup': .25, 'part': .25, 'soi': .20}
        # .30 x 80 + .25 x 60 + .20 x 40 + .25 x 50 = 24 + 15 + 8 + 12.5 = 59.5
        self.assertLess(B.check_overall(w, [80, 20], [60, 20], [40, 20], [50, 20], [59.5, 20.0]), 1e-12)
        self.assertLess(B.check_overall(w, [80], [60], [40], [50], [59.50005]), 1e-4)            # float32 noise passes
        with self.assertRaises(B.BuildError):
            B.check_overall(w, [80, 20], [60, 20], [40, 20], [50, 20], [59.5, 20.1])           # one row off by 0.1
        with self.assertRaises(B.BuildError):
            B.check_overall(w, [80], [60], [40], [50], [63.0])                                 # the 3-term .40/.35/.25 overall, 63.0

    def test_support_rank_is_the_competition_rank_highest_first_with_the_tie_size(self):
        rank, tie = W.support_rank([90.0, 70.0, 90.0000000001, 50.0, 70.0])
        self.assertEqual((rank.tolist(), tie.tolist()), ([1, 3, 1, 5, 3], [2, 2, 2, 1, 2]))


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
