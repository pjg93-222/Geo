"""Synthetic checks for tools/districts.py (the district config), walk_geo's EPSG parameter and ingest.classify's
state/cd parameters. No real data, no Django, no server.

    <venv python> -m unittest tools.test_districts          (from the V3 folder)
"""
import json
import os
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import pandas as pd
from shapely.geometry import box

from server.research import walk_names as N
from tools import build_scores_v3 as BS
from tools import build_walk as B
from tools import districts as D
from tools import ingest
from tools import walk_geo as G

CO, OR, CT = (D.get(i) for i in ('co-05', 'or-05', 'ct-01'))


class ConfigTests(unittest.TestCase):
    def test_every_entry_has_the_same_shape(self):
        keys = set(CO)
        for key, d in D.DISTRICTS.items():
            self.assertEqual(set(d), keys, key)
            self.assertEqual(d['id'], key)
            self.assertRegex(d['sha256'], r'^[0-9a-f]{64}$')
            self.assertGreater(d['rows'], 0)
            self.assertGreater(d['households'], 0)
            self.assertTrue(d['snapshot_prefix'].endswith('-'))
            self.assertEqual(len(D.road_paths(d)), len(d['counties']), key)

    def test_get(self):
        self.assertIs(D.get(), CO)
        self.assertIs(D.get(None), CO)
        self.assertIs(D.get('or-05'), OR)
        copy = dict(CO)
        self.assertIs(D.get(copy), copy)
        with self.assertRaises(KeyError):
            D.get('xx-99')

    def test_co_equals_todays_literals(self):
        self.assertEqual(D.window(CO), N.WINDOW)
        self.assertEqual(list(D.window(CO).items()), list(N.WINDOW.items()))
        self.assertEqual(D.election_keys(CO), B.ELECTION_KEYS.tolist())
        self.assertEqual(D.data_dir(CO), D.LOCAL)
        self.assertEqual(D.street_salt(CO), D.SIBLING_LOCAL / '.street-salt')
        self.assertEqual(CO['bounds'], (-109.1, 36.9, -102.0, 41.1))
        self.assertEqual(D.data_dir(OR), D.LOCAL / 'or-05')
        self.assertEqual(D.street_salt(CT), D.LOCAL / 'ct-01' / '.street-salt')
        pattern = D.snapshot_pattern(CO)
        self.assertTrue(re.fullmatch(pattern, 'co-cd005-6cf898496ca2'))
        self.assertFalse(re.fullmatch(pattern, 'or-cd005-6cf898496ca2'))
        self.assertTrue(re.fullmatch(D.snapshot_pattern(CT), 'ct-cd001-6cf898496ca2'))

    def test_final_ports_and_abs_rules(self):
        self.assertEqual([d['port'] for d in (CO, OR, CT)], [8767, 8775, 8777])
        self.assertEqual(OR['abs_rule'], 'Abs = mail ballots (Oregon votes by mail; every recorded vote counts)')
        self.assertEqual(CT['abs_rule'], 'Abs = absentee or mail ballots (A or M)')
        self.assertIsNone(OR['race_mode'])
        self.assertIsNone(CT['race_mode'])


class RowfwConfigTests(unittest.TestCase):
    """Phase 3b Lane 0: the row-framework folder, county names, interim overall, current() and block_aliases()."""

    def test_co_paths_equal_build_scores_defaults(self):
        self.assertEqual(D.rowfw_build(CO), BS.RFB)
        self.assertEqual(D.rowfw_cache(CO), BS.RF / 'cache' / 'inputs.parquet')
        self.assertEqual(D.rowfw_build(OR), D.LOCAL / 'or-05' / 'rowfw' / 'build' / 'rowfw-1.0.0')
        self.assertEqual(D.rowfw_cache(CT), D.LOCAL / 'ct-01' / 'rowfw' / 'cache' / 'inputs.parquet')

    def test_county_names(self):
        self.assertEqual(CO['county_names'], {'08041': 'El Paso County'})
        for d in (CO, OR, CT):
            self.assertTrue(set(d['counties']) <= set(d['county_names']), d['id'])     # every road county has a name
            self.assertTrue(all(k.startswith(d['state_fips']) for k in d['county_names']), d['id'])
        self.assertEqual(OR['county_names']['41005'], 'Clackamas County')
        self.assertEqual(CT['county_names']['09110'], 'Capitol Planning Region')

    def test_interim_overall(self):
        self.assertIsNone(CO['interim_overall'])
        for d in (OR, CT):
            self.assertEqual(d['interim_overall'], CO['score_weights'], d['id'])      # Colorado's 40/35/25

    def test_current_honours_cu_district(self):
        with mock.patch.dict(os.environ):
            os.environ.pop('CU_DISTRICT', None)
            self.assertIs(D.current(), CO)
            os.environ['CU_DISTRICT'] = ''
            self.assertIs(D.current(), CO)
            os.environ['CU_DISTRICT'] = 'ct-01'
            self.assertIs(D.current(), CT)
            os.environ['CU_DISTRICT'] = 'xx-99'
            with self.assertRaises(KeyError):
                D.current()

    def test_block_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = {**CT, 'geography': Path(tmp)}
            self.assertEqual(D.block_aliases(d), {})                                   # file missing
            aliases = {'090010101001001': '091100101001001'}
            (Path(tmp) / 'block_aliases.json').write_text(json.dumps(aliases), encoding='utf-8')
            self.assertEqual(D.block_aliases(d), aliases)


class CityTests(unittest.TestCase):
    def test_co_cases_of_the_walk_build_tests_unchanged(self):
        same = {'COLORADO SPRINGS': ['CO SPGS', 'COLO SPGS', 'COLO SPRINGS', 'COLORADO SPGS', 'COLORADO SPRINGS',
                                     'colorado springs ', 'Colorado Spgs.'],
                'MANITOU SPRINGS': ['MANITOU SPGS', 'MANITOU SPRINGS'],
                'GREEN MOUNTAIN FALLS': ['GRN MTN FLS', 'GREEN MOUNTAIN FALLS'],
                'FORT CARSON': ['FT CARSON', 'Fort Carson']}
        for want, variants in same.items():
            for text in variants:
                self.assertEqual(D.canonical_city(text), want, text)
                self.assertEqual(D.canonical_city(text, 'co-05'), want, text)
        for text in ('CALHAN', 'CASCADE', 'ELBERT', 'FALCON', 'FOUNTAIN', 'LARKSPUR', 'MONUMENT', 'PALMER LAKE', 'PEYTON',
                     'PUEBLO', 'USAF ACADEMY', 'WOODLAND PARK'):
            self.assertEqual(D.canonical_city(text), text)
        self.assertEqual(D.canonical_city(''), '')
        self.assertEqual(D.canonical_city("O'Fallon"), 'OFALLON')

    def test_or_and_ct_spellings(self):
        self.assertEqual(D.canonical_city('MT ANGEL', OR), 'MOUNT ANGEL')
        self.assertEqual(D.canonical_city('ST BENEDICT', OR), 'SAINT BENEDICT')
        self.assertEqual(D.canonical_city('TOWN OF BERLIN', CT), 'BERLIN')
        self.assertEqual(D.canonical_city('CT MIDDLETOWN', CT), 'MIDDLETOWN')
        self.assertEqual(D.canonical_city('WINCHESTER CNTR', CT), 'WINCHESTER CENTER')
        self.assertEqual(D.canonical_city('CT', CT), 'CT')


class PrecinctAndVoteTests(unittest.TestCase):
    def test_precinct_key(self):
        self.assertEqual(D.precinct_key(CO, '0805000123'), '0805000123')
        self.assertEqual(D.precinct_key(OR, '123', '41005'), '41005-123')
        self.assertEqual(D.precinct_key(CT, '005-00', '', 'WEST HARTFORD (DISTRICT 005)'),
                         '005-00-WEST-HARTFORD-DISTRICT-005')
        self.assertEqual(D.precinct_key(OR, '123'), '123')
        self.assertEqual(D.precinct_key(CT, '005-00'), '005-00')
        self.assertEqual(D.precinct_key(CT, '005-00', name=' - '), '005-00')
        for d, args in ((CO, ('0805000123',)), (OR, ('123', '41005')), (OR, ('123',)),
                        (CT, ('005-00', '', 'WEST HARTFORD (DISTRICT 005)')), (CT, ('005-00',))):
            self.assertRegex(D.precinct_key(d, *args), '^(?:%s)$' % d['precinct_pattern'])

    def test_is_mail(self):
        for code, want in (('M1', True), ('A1', True), ('Y1', False), ('P1', False)):
            self.assertIs(D.is_mail(CO, code), want, code)
        for code, want in (('Y1', True), ('P1', True), ('E1', True), ('', False), (None, False)):
            self.assertIs(D.is_mail(OR, code), want, code)
        for code, want in (('A1', True), ('M1', True), ('E1', False), ('P1', False), ('Y1', False)):
            self.assertIs(D.is_mail(CT, code), want, code)

    def test_calendars(self):
        self.assertEqual(D.election_keys(OR)[3], 20200519)
        self.assertEqual(D.election_keys(CT)[5], 20240813)
        self.assertEqual(D.election_keys(OR)[6:], D.election_keys(CO)[6:])
        self.assertEqual(D.election_keys(CT)[6:], D.election_keys(CO)[6:])
        for d in (CO, OR, CT):
            self.assertEqual(list(d['primaries']), list(N.PRIMARIES))


class EpsgAndClassifyTests(unittest.TestCase):
    def test_walk_geo_epsg_parameter(self):
        x, y = G.to_metres(-122.68, 45.52, 26910)
        lon, lat = G.to_lonlat(x, y, 26910)
        self.assertAlmostEqual(lon, -122.68, delta=1e-7)
        self.assertAlmostEqual(lat, 45.52, delta=1e-7)
        self.assertEqual(G.EPSG, 26913)
        self.assertEqual(G.to_metres(-104.82, 38.86), G.to_metres(-104.82, 38.86, 26913))
        self.assertNotEqual(G.to_metres(-122.68, 45.52), (x, y))

    def test_classify_state_and_cd(self):
        prefix = 'svi_registration'
        geoid = '410050001001001'
        frame = pd.DataFrame({prefix + '_censusid': [geoid] * 3, prefix + '_state': ['OR', 'CO', 'OR'],
                              prefix + '_latitude': ['0.5'] * 3, prefix + '_longitude': ['0.5'] * 3,
                              prefix + '_geolevel': ['ROOFTOP'] * 3,
                              'svi_registration_congressional_district': ['005', '005', '001']})
        lookup, district = {geoid: box(0, 0, 1, 1)}, box(0, 0, 1, 1)
        status = ingest.classify(frame, prefix, lookup, district, state='OR', cd='005')[0].tolist()
        self.assertEqual(status, ['mapped', 'out_of_scope', 'out_of_scope'])
        status = ingest.classify(frame, prefix, lookup, district)[0].tolist()
        self.assertEqual(status, ['out_of_scope', 'mapped', 'out_of_scope'])


class DataDirGuardTests(unittest.TestCase):
    def test_check_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            D.check_data_dir(OR, tmp)                                    # no street store: nothing to refuse
            with closing(sqlite3.connect(Path(tmp) / 'street.sqlite3')) as connection:
                connection.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                connection.execute('INSERT INTO metadata VALUES (?, ?)', ('source_sha256', json.dumps(OR['sha256'])))
                connection.commit()
            D.check_data_dir(OR, tmp)
            with self.assertRaises(ValueError):
                D.check_data_dir(CT, tmp)
            with closing(sqlite3.connect(Path(tmp) / 'street.sqlite3')) as connection:
                connection.execute("DELETE FROM metadata WHERE key='source_sha256'")
                connection.commit()
            with self.assertRaises(ValueError):
                D.check_data_dir(OR, tmp)


if __name__ == '__main__':
    unittest.main()
