"""Synthetic checks for the district parameters of tools/build_walk.py (no real data): the CSV person-field path, the
vote-calendar and mail rules, the precinct key, the city column and the UTM zone, all on server/research/walk_testkit's
invented Colorado world with a modified copy of the co-05 entry standing in for OR / CT.

    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 <venv python> -m unittest tools.test_walk_district     (from the V3 folder)
"""
import contextlib
import io
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from server.research import walk_testkit as T
from tools import build_walk as B
from tools import districts as D
from tools import walk_geo as G
from tools.test_walk_build import RELAXED, connect, rewrite_source

CO = D.get('co-05')
OR, CT = D.get('or-05'), D.get('ct-01')
CSV_CO = {**CO, 'person_fields': 'csv'}                       # the Colorado world, people read from the CSV
OR_RULE = {**CSV_CO, 'primaries': OR['primaries'], 'abs_prefixes': OR['abs_prefixes'], 'abs_gate': OR['abs_gate'],
           'abs_rule': OR['abs_rule']}
CT_RULE = {**CSV_CO, 'primaries': CT['primaries'], 'abs_prefixes': CT['abs_prefixes'], 'abs_gate': CT['abs_gate'],
           'abs_rule': CT['abs_rule']}
PERSON_FIELDS = 'person_id, household_id, stop_id, street_id, unit, ss, sh, precinct, city, zip, party, age, gender, ' \
                'reg_date, pri_voted, pri_elig, gen_voted, gen_elig, abs_voted, abs_elig'      # all but voter_key (salted)


def table(path, sql):
    with closing(connect(path)) as db:
        return [tuple(r) for r in db.execute(sql)]


def meta(path):
    with closing(connect(path)) as db:
        return {k: v for k, v in db.execute('SELECT key, value FROM meta')}


class DistrictBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.scored = T.write_minimal_stores(root / 'scored')                    # scores.sqlite3 present
        cls.bare = T.write_minimal_stores(root / 'bare', scores=False)          # none: the CSV is the only source
        cls.walk_a = root / 'a.sqlite3'
        cls.result_a = B.build(cls.scored.source_csv, cls.scored.directory, cls.walk_a, gates=RELAXED)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.addCleanup(setattr, G, 'EPSG', G.EPSG)                              # build() sets the district's zone

    def build(self, district, name, stores=None, **kwargs):
        stores = stores or self.bare
        out = Path(self.tmp.name) / (name + '.sqlite3')
        result = B.build(stores.source_csv, stores.directory, out, gates=RELAXED, district=district, **kwargs)
        self.assertTrue(result['replaced'], result['failed'])
        return out, result

    def people(self, path):
        return {r[0]: r[1:] for r in table(path, 'SELECT person_id, party, age, precinct, abs_voted, pri_elig, gen_elig, '
                                                 'pri_voted, gen_voted FROM person')}

    # 1
    def test_the_csv_path_gives_the_scores_path_result(self):
        self.assertTrue(self.result_a['replaced'])
        self.assertFalse((self.bare.directory / 'scores.sqlite3').exists())
        out, _ = self.build(CSV_CO, 'b')
        self.assertEqual(self.people(out), self.people(self.walk_a))
        want = {p: (v['party'], v['age'], v['precinct']) for p, v in self.scored.expected['people'].items()}
        self.assertEqual({p: v[:3] for p, v in self.people(out).items()}, want)
        for sql in ('SELECT * FROM street ORDER BY id', 'SELECT * FROM stop ORDER BY id',
                    'SELECT * FROM combo ORDER BY ss, sh, precinct, street_id',
                    'SELECT %s FROM person ORDER BY person_id' % PERSON_FIELDS):
            self.assertEqual(table(out, sql), table(self.walk_a, sql), sql)
        self.assertEqual(meta(out)['person_fields'], 'csv')
        self.assertNotIn('person_fields', meta(self.walk_a))

    # 2
    def test_oregon_counts_every_recorded_vote_and_has_no_abs_gate(self):
        out, result = self.build(OR_RULE, 'or')
        self.assertEqual({k for k in result['qa']['gates'] if k.startswith('abs_share_')}, set())
        self.assertEqual(result['qa']['g_abs']['2020g']['share'], 1.0)
        self.assertEqual(result['qa']['g_abs']['2020g']['recorded'], result['qa']['g_abs']['2020g']['mail'])
        for voted_pri, voted_gen, abs_voted in table(out, 'SELECT pri_voted, gen_voted, abs_voted FROM person'):
            self.assertEqual(abs_voted, voted_pri + voted_gen)
        stored = meta(out)
        self.assertEqual(json.loads(stored['window']), D.window(OR_RULE))
        self.assertEqual(stored['abs_rule'], OR['abs_rule'])
        self.assertEqual(json.loads(stored['window'])['primaries'], list(OR['primaries']))

    # 3
    def test_connecticut_counts_a_and_m_only(self):
        voted, mail = B.vote_flags(pd.Series(['AC', 'MP', 'EP', 'PP', 'YP', 'QP', 'ac', ''], dtype=str), CT['abs_prefixes'])
        self.assertEqual(voted.tolist(), [True] * 7 + [False])
        self.assertEqual(mail.tolist(), [True, True, False, False, False, False, True, False])
        out, result = self.build(CT_RULE, 'ct')
        self.assertEqual({k for k in result['qa']['gates'] if k.startswith('abs_share_')}, set())
        expected = self.bare.expected['people']
        for person_id, abs_voted in table(out, 'SELECT person_id, abs_voted FROM person'):
            self.assertEqual(abs_voted, expected[person_id]['abs']['voted'], person_id)
        self.assertEqual(result['qa']['g_abs']['2022g']['share'], 0.7)             # not the Oregon 1.0
        self.assertEqual(json.loads(meta(out)['window'])['primaries'], list(CT['primaries']))

    # 4
    def test_the_calendar_decides_who_is_eligible(self):
        none = np.zeros((1, len(B.ELECTIONS)), dtype=bool)
        reg, dob = np.array([20240601], dtype=np.int32), np.array([19900101], dtype=np.int32)
        co = B.history(none, none, reg, dob, np.array(D.election_keys(CO), dtype=np.int64))
        oregon = B.history(none, none, reg, dob, np.array(D.election_keys(OR), dtype=np.int64))
        self.assertEqual((co['pri_elig'][0], oregon['pri_elig'][0]), (1, 0))       # 2024-06-25 vs 2024-05-21
        self.assertEqual((co['gen_elig'][0], oregon['gen_elig'][0]), (1, 1))
        self.assertEqual(B.history(none, none, reg, dob)['pri_elig'][0], 1)         # the default keys are Colorado's

    # 5
    def test_the_precinct_key_follows_the_district(self):
        county = {**CSV_CO, 'precinct_key': 'county-code', 'precinct_pattern': r'\d{5}-\d{10}'}
        out, _ = self.build(county, 'county')
        codes = {p: '08041-' + v['precinct'] for p, v in self.bare.expected['people'].items()}
        self.assertEqual(dict(table(out, 'SELECT person_id, precinct FROM person')), codes)
        self.assertEqual({r[0] for r in table(out, 'SELECT precinct FROM combo')}, set(codes.values()))
        named = {**CSV_CO, 'precinct_key': 'code-name', 'precinct_pattern': r'\d{10}-\d{10}'}
        out, _ = self.build(named, 'named')
        self.assertEqual({r[0] for r in table(out, 'SELECT precinct FROM person')},
                         {v['precinct'] + '-' + v['precinct'] for v in self.bare.expected['people'].values()})

    def test_a_precinct_key_outside_the_pattern_stops_the_build(self):
        wrong = {**CSV_CO, 'precinct_key': 'county-code'}                          # keeps CO's \d{10} pattern
        with self.assertRaisesRegex(B.BuildError, r'^\d+ precinct keys .* co-05 pattern$'):
            B.build(self.bare.source_csv, self.bare.directory, Path(self.tmp.name) / 'no.sqlite3', gates=RELAXED,
                    district=wrong)
        self.assertFalse((Path(self.tmp.name) / 'no.sqlite3').exists())

    # 6
    def test_the_city_comes_from_the_district_column(self):
        stores = T.write_minimal_stores(Path(self.tmp.name) / 'town')

        def edit(header, rows):
            at = header.index('svi_registration_city')
            return [*header, 'svi_registration_township'], [[*r[:at], 'NOT A TOWN', *r[at + 1:], 'TOWN OF ' + r[at]]
                                                             for r in rows]
        rewrite_source(stores, edit)
        town = {**CO, 'city_field': 'svi_registration_township', 'city_lead': CT['city_lead'],
                'city_words': CT['city_words']}
        out = Path(self.tmp.name) / 'town.sqlite3'
        self.assertTrue(B.build(stores.source_csv, stores.directory, out, gates=RELAXED, district=town)['replaced'])
        self.assertEqual({r[0] for r in table(out, 'SELECT city FROM street')}, {'COLORADO SPRINGS', 'FOUNTAIN'})
        self.assertEqual({r[0] for r in table(out, 'SELECT city FROM person')}, {'COLORADO SPRINGS', 'FOUNTAIN'})
        plain = Path(self.tmp.name) / 'plain.sqlite3'                              # the default column is still the city
        self.assertTrue(B.build(stores.source_csv, stores.directory, plain, gates=RELAXED)['replaced'])
        self.assertEqual({r[0] for r in table(plain, 'SELECT city FROM street')}, {'NOT A TOWN'})

    # 7
    def test_build_sets_the_district_utm_zone(self):
        self.assertEqual(G.EPSG, 26913)
        self.build({**CSV_CO, 'utm_epsg': 32613}, 'zone')                          # WGS 84 / UTM 13N: same ground as NAD83
        self.assertEqual(G.EPSG, 32613)
        self.build(CSV_CO, 'back')
        self.assertEqual(G.EPSG, 26913)

    def test_a_district_that_changes_the_vote_columns_is_refused(self):
        odd = {**CSV_CO, 'primaries': dict([('2012p', '2012-06-26'), *list(CO['primaries'].items())[1:]])}
        with self.assertRaisesRegex(B.BuildError, 'primaries must be keyed'):
            B.build(self.bare.source_csv, self.bare.directory, Path(self.tmp.name) / 'odd.sqlite3', district=odd)


class CommandLineTests(unittest.TestCase):
    """--district, the default source and data directory, and the sha256 check of the district's own file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stores = T.write_minimal_stores(self.tmp.name, scores=False)
        self.addCleanup(setattr, G, 'EPSG', G.EPSG)
        self.entry = {**CSV_CO, 'source_csv': self.stores.source_csv, 'sha256': self.stores.source_sha256}

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.dict(B.GATES, RELAXED), mock.patch.dict(D.DISTRICTS, {'co-05': self.entry}):
            code = B.main(['--district', 'co-05', '--data-dir', str(self.stores.directory), *args])
        return code, out.getvalue(), err.getvalue()

    def test_the_district_source_is_the_default_and_its_sha256_is_checked(self):
        code, out, err = self.run_cli()
        self.assertEqual(code, 0, err)
        self.assertIn('walk store written', out)
        self.assertEqual(meta(self.stores.directory / 'walk.sqlite3')['person_fields'], 'csv')
        (self.stores.directory / 'walk.sqlite3').unlink()
        self.entry['sha256'] = '0' * 64
        code, out, err = self.run_cli()
        self.assertEqual((code, out), (1, ''))
        self.assertIn('different sha256', err)
        self.assertFalse((self.stores.directory / 'walk.sqlite3').exists())

    def test_an_explicit_source_skips_the_district_sha256_check(self):
        self.entry['sha256'] = '0' * 64
        code, out, err = self.run_cli('--source', str(self.stores.source_csv))
        self.assertEqual(code, 0, err)

    def test_other_districts_default_to_their_own_folder_and_ignore_cu_data_dir(self):
        with mock.patch.dict('os.environ', {'CU_DATA_DIR': 'elsewhere'}):
            self.assertEqual(B.default_data_dir(CO), Path('elsewhere'))
            self.assertEqual(B.default_data_dir(OR), D.data_dir(OR))
            self.assertEqual(B.default_data_dir(CT), D.LOCAL / 'ct-01')


if __name__ == '__main__':
    unittest.main()
