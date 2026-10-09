"""Synthetic checks for tools/build_density.py: 4 blocks (one with no land), 7 people, 2 precincts. No real data, no Django.

    <venv python> -m unittest tools.test_density          (from the V3 folder)
"""
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

from tools import build_density as BD
from tools import districts as D
from tools import glassbox

# B1 1 km2, B2 0.5 km2 (shared by P1 and P2), B3 no land, B4 2 km2
BLOCKS = pd.DataFrame({'GEOID20': ['B1', 'B2', 'B3', 'B4'], 'POP20': [100, 50, 30, 400],
                       'ALAND20': [1_000_000, 500_000, 0, 2_000_000]})
# (block, precinct, tract, zip): the last person has no block id
PEOPLE = pd.DataFrame([('B1', 'P1', 'T1', 'Z1'), ('B1', 'P1', 'T1', 'Z1'), ('B2', 'P1', 'T1', 'Z1'), ('B2', 'P2', 'T1', 'Z2'),
                       ('B4', 'P2', 'T2', 'Z2'), ('B3', 'P2', 'T2', 'Z2'), ('', 'P2', 'T2', 'Z2')],
                      columns=['reg_block', 'reg_precinct', 'reg_tract', 'reg_zip'])


class ComputeTests(unittest.TestCase):
    def setUp(self):
        self.levels, self.counts, self.join = BD.compute(PEOPLE, BLOCKS, {})

    def test_population_density_skips_blocks_without_land(self):
        block = self.levels['block']
        self.assertEqual(block['ids'], ['B1', 'B2', 'B3', 'B4'])
        self.assertEqual(block['pop_density'], [100.0, 100.0, None, 200.0])
        self.assertEqual(block['land_km2'], [1.0, 0.5, None, 2.0])

    def test_an_area_counts_a_shared_block_in_each_and_leaves_out_land_free_blocks(self):
        precinct, tract, zipcode = (self.levels[k] for k in ('precinct', 'tract', 'zip'))
        self.assertEqual(precinct['pop_density'], [100.0, 180.0])           # P1: B1+B2 150 / 1.5; P2: B2+B4 450 / 2.5 (B3 out)
        self.assertEqual(precinct['land_km2'], [1.5, 2.5])
        self.assertEqual(tract['pop_density'], [100.0, 200.0])
        self.assertEqual(zipcode['pop_density'], [100.0, 180.0])

    def test_voter_density_counts_people_in_the_land_blocks_of_every_area(self):
        self.assertEqual(self.levels['block']['voter_density'], [2.0, 4.0, None, 0.5])
        self.assertEqual(self.levels['precinct']['voter_density'], [2.0, 0.8])         # P2: B2 + B4 people, 2 / 2.5 km2 (nothing is withheld)
        self.assertEqual(self.levels['tract']['voter_density'], [round(4 / 1.5, 3), 0.5])
        self.assertEqual(self.levels['zip']['voter_density'], [2.0, 0.8])
        self.assertTrue(all(v is not None for v in self.levels['precinct']['voter_density'] + self.levels['zip']['voter_density']))

    def test_counts_and_join_receipt(self):
        self.assertEqual(self.join, {'people': 7, 'with_block': 6, 'joined': 6, 'rate': 1.0, 'blocks_in_file': 4,
                                     'land_zero_blocks': 1, 'land_zero_people': 1})
        self.assertEqual(self.counts['precinct'], {'areas': 2, 'pop_density': 2, 'voter_density': 2})
        self.assertEqual(self.counts['block']['pop_density'], 3)

    def test_a_connecticut_alias_joins_the_old_county_id(self):
        old = BLOCKS.assign(GEOID20=['090010000001', 'B2', 'B3', 'B4'])
        levels, counts, join = BD.compute(PEOPLE, old, {'090010000001': 'B1'})
        self.assertEqual((levels, counts, join), (self.levels, self.counts, self.join))
        self.assertEqual(BD.compute(PEOPLE, old, {})[2]['joined'], 4)                  # without it the two B1 people fall out


    def test_a_person_still_carrying_the_old_county_id_joins_the_same_block(self):
        old = BLOCKS.assign(GEOID20=['090010000001', 'B2', 'B3', 'B4'])
        people = PEOPLE.assign(reg_block=['090010000001'] + list(PEOPLE.reg_block[1:]))
        self.assertEqual(BD.compute(people, old, {'090010000001': 'B1'})[2]['joined'], 6)      # the new id and the old both join


class MainTests(unittest.TestCase):
    """main() on small real files: a person table and a dummy blocks.zip (the block read is patched)."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = self.root = Path(self.temporary.name)
        (root / 'geo').mkdir()
        (root / 'geo' / 'blocks.zip').write_bytes(b'not a real zip')
        (root / 'geo' / 'block_aliases.json').write_text(json.dumps({'old-B1': 'B1'}))
        self.home = root / 'home'
        self.home.mkdir()
        self.write_people(PEOPLE)
        self.district = {**D.DISTRICTS['or-05'], 'geography': root / 'geo', 'rowfw_root': root / 'rf'}
        self.patches = [mock.patch.dict(D.DISTRICTS, {'or-05': self.district}), mock.patch.object(D, 'data_dir', return_value=self.home),
                        mock.patch.object(glassbox, 'EVENT_DIR', root / 'events'), mock.patch.object(glassbox, 'MIRROR', root / 'events.jsonl')]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.temporary.cleanup()

    def write_people(self, frame):
        (self.home / 'scores.sqlite3').unlink(missing_ok=True)
        with closing(sqlite3.connect(self.home / 'scores.sqlite3')) as db:
            frame.to_sql('person', db, index=False)

    def run_main(self, blocks=BLOCKS):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(BD.pyogrio, 'read_dataframe', return_value=blocks), redirect_stdout(out), redirect_stderr(err):
            return BD.main(['--district', 'or-05']), err.getvalue()

    def test_writes_density_json_beside_the_stores(self):
        old = BLOCKS.assign(GEOID20=['old-B1', 'B2', 'B3', 'B4'])
        code, _ = self.run_main(old)
        document = json.loads((self.home / 'density.json').read_text())
        self.assertEqual(code, 0)
        self.assertEqual((document['schema'], document['district']), ('density-1', 'or-05'))
        self.assertEqual(document['blocks_source'], {'file': 'blocks.zip', 'sha256': hashlib.sha256(b'not a real zip').hexdigest()})
        self.assertEqual(document['join']['rate'], 1.0)
        self.assertEqual(document['levels']['precinct'], {'ids': ['P1', 'P2'], 'pop_density': [100.0, 180.0],
                                                          'voter_density': [2.0, 0.8], 'land_km2': [1.5, 2.5]})
        self.assertFalse(list(self.home.glob('*.tmp')))

    def test_a_connecticut_block_outside_the_district_file_gets_its_density(self):
        """The district's block_aliases.json holds only the blocks touching the district (here it knows neither block); the state
        file's re-code reaches the block registered outside it, and a person who still carries the old county id joins too."""
        self.district.update(block_regions=True, state_fips='09')
        tiger = pd.DataFrame({'GEOID20': ['090010001001001', '090030002001002'], 'TRACTCE20': ['000100', '000200'], 'BLOCKCE20': ['1001', '1002'],
                              'INTPTLON20': ['+0.5000000', '+1.5000000'], 'INTPTLAT20': ['+0.5000000'] * 2,
                              'POP20': [100, 50], 'ALAND20': [1_000_000, 500_000]})
        regions = gpd.GeoDataFrame({'COUNTYFP': ['110', '130']}, geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)], crs=4326)
        self.write_people(pd.DataFrame([('091100001001001', 'P1', 'T1', 'Z1'), ('091300002001002', 'P1', 'T2', 'Z1'),
                                        ('091300002001002', 'P2', 'T2', 'Z1'), ('090030002001002', 'P2', 'T3', 'Z1')], columns=PEOPLE.columns))
        with mock.patch.object(gpd, 'read_file', return_value=regions):
            code, _ = self.run_main(tiger)
        document = json.loads((self.home / 'density.json').read_text())
        self.assertEqual((code, document['join']['joined'], document['join']['rate']), (0, 4, 1.0))
        self.assertEqual(document['levels']['block']['ids'], ['090030002001002', '091100001001001', '091300002001002'])
        self.assertEqual(document['levels']['block']['voter_density'], [2.0, 1.0, 4.0])          # the outside block: 2 people / 0.5 km2
        self.assertEqual(document['levels']['tract']['pop_density'], [100.0, 100.0, 100.0])
        self.assertEqual(document['levels']['tract']['voter_density'], [1.0, 4.0, 2.0])

    def test_a_join_rate_under_99_percent_exits_2_and_writes_nothing(self):
        code, err = self.run_main(BLOCKS.iloc[[0, 1, 2]])                               # B4's person falls out: 5 of 6 join
        self.assertEqual(code, 2)
        self.assertIn('join rate', err)
        self.assertFalse((self.home / 'density.json').exists())


if __name__ == '__main__':
    unittest.main()
