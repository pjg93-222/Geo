"""Synthetic checks for the geography, road, street and contacts tools' district parameters (multi-district build,
Phase 2, Lane B). Plain unittest, geopandas/shapely only, no Django, no real data, no network.

    <venv python> -m unittest tools.test_geography_tools          (from the V3 folder)
"""
import csv
import io
import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
from shapely.geometry import LineString, box, mapping

from tools import build_contacts, build_road_context, build_street, prepare_geography
from tools import districts as D


class RecodeBlocksTests(unittest.TestCase):
    """Test 1: Connecticut-style county -> planning-region re-code."""

    def setUp(self):
        self.regions = gpd.GeoDataFrame({'COUNTYFP': ['110', '120']}, geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)], crs=4326)

    def blocks(self, points):
        rows = [('09001' + '%06d%04d' % (100 + i, 1000 + i), '%06d' % (100 + i), '%04d' % (1000 + i), '%+.7f' % x, '%+.7f' % y)
                for i, (x, y) in enumerate(points)]
        geoms = [box(x - .01, y - .01, x + .01, y + .01) for x, y in points]
        return gpd.GeoDataFrame(rows, columns=['GEOID20', 'TRACTCE20', 'BLOCKCE20', 'INTPTLON20', 'INTPTLAT20'],
                                geometry=geoms, crs=4326)

    def test_ids_carry_the_region_and_old_ids_stay_as_aliases(self):
        blocks = self.blocks([(.5, .5), (1.5, .5), (.2, .2)])      # regions A, B, A
        frame, counts = prepare_geography.recode_blocks(blocks, self.regions, '09')
        new = ['09' + code + '%06d%04d' % (100 + i, 1000 + i) for i, code in enumerate(['110', '120', '110'])]
        old = list(blocks.GEOID20)
        self.assertEqual(list(frame.geoid), new + old)
        self.assertTrue(frame.geoid.is_unique)
        self.assertEqual(counts, {'recoded': 3, 'aliases': 3, 'regions': {'110': 2, '120': 1}})
        self.assertEqual(frame.geometry.iloc[3].wkb, blocks.geometry.iloc[0].wkb)       # an alias keeps its block's shape
        self.assertEqual(frame.crs.to_epsg(), 4326)

    def test_a_block_outside_every_region_raises(self):
        blocks = self.blocks([(.5, .5), (5., 5.)])
        with self.assertRaisesRegex(ValueError, '1 blocks are not in exactly one region'):
            prepare_geography.recode_blocks(blocks, self.regions, '09')

    def test_a_block_whose_id_does_not_change_gets_no_alias(self):
        blocks = self.blocks([(.5, .5)])
        blocks['GEOID20'] = '09110' + blocks.TRACTCE20 + blocks.BLOCKCE20
        frame, counts = prepare_geography.recode_blocks(blocks, self.regions, '09')
        self.assertEqual(list(frame.geoid), list(blocks.GEOID20))
        self.assertEqual((counts['recoded'], counts['aliases']), (0, 0))


class DistrictShapeTests(unittest.TestCase):
    def test_union_of_the_districts_rows_and_failure_when_missing(self):
        frame = gpd.GeoDataFrame({'STATEFP': ['41', '41', '41', '09'], 'CD119FP': ['05', '05', '04', '05']},
                                 geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1), box(5, 5, 6, 6), box(9, 9, 10, 10)], crs=4326)
        shape = prepare_geography.district_shape(frame, D.get('or-05'))
        self.assertEqual(shape.bounds, (0, 0, 2, 1))
        with self.assertRaisesRegex(ValueError, 'missing or invalid'):
            prepare_geography.district_shape(frame, D.get('co-05'))

    def test_co_05_is_never_prepared_without_an_explicit_directory_or_with_district_flags(self):
        with patch.object(prepare_geography, 'prepare') as prepare, patch.object(prepare_geography, 'prepare_district') as other:
            for argv in (['--district', 'co-05'], ['--directory', 'x', '--join-check'], ['--directory', 'x', '--bundle-layers']):
                with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                    prepare_geography.main(argv)
            prepare.assert_not_called()
            other.assert_not_called()
            prepare_geography.main(['--directory', 'x'])
            prepare.assert_called_once_with(Path('x'))


class JoinReceiptTests(unittest.TestCase):
    """Test 5."""

    def test_counts_and_rate(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'source.csv'
            with path.open('w', newline='', encoding='cp1252') as stream:
                writer = csv.writer(stream)
                writer.writerow(['sii_persist_id', 'svi_registration_censusid', 'sii_censusid'])
                for row in [('1', '090011111111111', '090011111111111'), ('2', '090012222222222', ''),
                            ('3', '090013333333333', ' 090011111111111 '), ('4', '', ''), ('5', '099999999999999', '')]:
                    writer.writerow(row)
            receipt = prepare_geography.join_receipt(path, {'090011111111111', '090012222222222', '090013333333333'})
        self.assertEqual(receipt, {'svi_registration_censusid': {'rows': 5, 'blank': 1, 'joined': 3, 'rate': 0.75},
                                   'sii_censusid': {'rows': 5, 'blank': 3, 'joined': 2, 'rate': 1.0}})


class StreetOutlineTests(unittest.TestCase):
    def test_a_closed_block_of_roads_becomes_one_outline(self):
        x0, y0, x1, y1 = -104.82, 38.82, -104.81, 38.83          # about 860 m x 1110 m
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
        lines = [LineString(corners[i:i + 2]) for i in range(4)] + [LineString([(x0, y0), (x0 - .01, y0 - .01)])]
        roads = gpd.GeoDataFrame({'MTFCC': ['S1400'] * 4 + ['S1640']}, geometry=lines, crs=4326)
        features, unclosed, invalid = prepare_geography.street_outlines([roads], box(-104.9, 38.7, -104.7, 38.9), 26913)
        self.assertEqual((len(features), unclosed, invalid), (1, 0, 0))
        self.assertRegex(features[0]['properties']['id'], r'^street-[0-9a-f]{16}$')


class RoadSelectionTests(unittest.TestCase):
    """Test 2."""

    def frame(self, rows):
        return gpd.GeoDataFrame({'LINEARID': [r[0] for r in rows], 'MTFCC': [r[1] for r in rows]},
                                geometry=[r[2] for r in rows], crs=4326)

    def files(self):
        line = lambda x: LineString([(x, 0), (x + .1, 0)])
        first = self.frame([('1', 'S1100', line(0)), ('2', 'S1400', line(.2)), ('9', 'S1640', line(.4)),
                            ('8', 'S1200', LineString())])
        second = self.frame([('2', 'S1200', line(.2)), ('3', 'S1400', line(.5)), ('4', 'S1200', line(7))])
        return first, second

    def test_shared_road_kept_once_and_outside_line_dropped(self):
        first, second = self.files()
        roads, outside, duplicates = build_road_context.select_roads([first, second], box(-1, -1, 1, 1))
        self.assertEqual(list(roads.LINEARID), ['1', '2', '3'])
        self.assertEqual(list(roads.MTFCC), ['S1100', 'S1400', 'S1400'])           # file one's copy of 2 wins
        self.assertEqual((outside, duplicates), (1, 1))

    def test_without_a_clip_only_the_kind_and_the_repeat_filter(self):
        first, second = self.files()
        roads, outside, duplicates = build_road_context.select_roads([first, second])
        self.assertEqual(list(roads.LINEARID), ['1', '2', '3', '4'])
        self.assertEqual((outside, duplicates), (0, 1))

    def test_clip_area_grows_the_district_by_road_clip_m(self):
        with tempfile.TemporaryDirectory() as folder:
            geojson = {'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'properties': {},
                       'geometry': {'type': 'Polygon', 'coordinates': [[[-122.7, 45.4], [-122.5, 45.4], [-122.5, 45.6], [-122.7, 45.6], [-122.7, 45.4]]]}}]}
            (Path(folder) / 'district.geojson').write_text(json.dumps(geojson))
            clip = build_road_context.clip_area({**D.get('or-05'), 'geography': folder})
        west, south, east, north = clip.bounds
        self.assertAlmostEqual(south, 45.4 - 2000 / 111200, delta=.002)
        self.assertAlmostEqual(north, 45.6 + 2000 / 111200, delta=.002)
        self.assertAlmostEqual(west, -122.7 - 2000 / (111200 * .7), delta=.004)


STREET_HEADER = build_street.SOURCE_FIELDS + [f for f in build_contacts.FIELDS if f not in build_street.SOURCE_FIELDS]


class OregonStreetAndContactsTests(unittest.TestCase):
    """Tests 3 and 4: an OR district built on a 4-row synthetic file, salt kept where it is told."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        root = Path(cls.temporary.name)
        cls.geography, cls.directory, cls.salt = root / 'geography', root / 'data', root / 'elsewhere' / 'x.salt'
        cls.geography.mkdir()
        cls.salt.parent.mkdir()
        block = box(-122.70, 45.50, -122.60, 45.55)
        gpd.GeoDataFrame({'geoid': ['410050001001001']}, geometry=[block], crs=4326).to_file(cls.geography / 'blocks.gpkg', driver='GPKG')
        (cls.geography / 'district.geojson').write_text(json.dumps({'type': 'FeatureCollection', 'features': [{
            'type': 'Feature', 'properties': {}, 'geometry': mapping(box(-122.8, 45.4, -122.5, 45.6))}]}))
        (cls.geography / 'provenance.json').write_text(json.dumps({'block_vintage': 'synthetic', 'district_vintage': 'synthetic'}))

        def record(number, household, state='OR'):
            row = {field: '' for field in STREET_HEADER}
            row.update(sii_persist_id='RAW-P%d' % number, sii_statara_id='RAW-S%d' % number, sii_household_id='RAW-H%d' % household,
                       sii_first_name='Synthetic', sii_last_name='Person %d' % number, svi_registration_congressional_district='005',
                       sii_phone='555-010%d' % number, sii_email='p%d@example.test' % number)
            for basis, prefix in build_street.BASES.items():
                row.update({prefix + '_state': state, prefix + '_censusid': '410050001001001', prefix + '_latitude': '45.52',
                            prefix + '_longitude': '-122.65', prefix + '_geolevel': 'ROOFTOP',
                            prefix + '_address': '%d Synthetic %s' % (household, basis)})
            return row

        cls.csv_path = root / 'statara_or_cd005_20260904.csv'
        with cls.csv_path.open('w', newline='', encoding='cp1252') as stream:
            writer = csv.DictWriter(stream, fieldnames=STREET_HEADER)
            writer.writeheader()
            writer.writerows([record(1, 1), record(2, 1), record(3, 2), record(4, 3, state='CO')])
        cls.directory.mkdir()
        cls.receipt = build_street.build_database(cls.csv_path, cls.geography, cls.directory / 'street.sqlite3',
                                                  state='OR', cd='005', salt_file=cls.salt)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_or_rows_are_mapped_and_the_state_is_in_the_validation_text(self):
        bases = self.receipt['bases']
        for basis in ('registration', 'residential'):
            self.assertEqual((bases[basis]['people'], bases[basis]['households']), (3, 2), basis)
            self.assertEqual(bases[basis]['geography_outcomes'], {'mapped': 3, 'out_of_scope': 1}, basis)
        with closing(sqlite3.connect((self.directory / 'street.sqlite3').as_uri() + '?mode=ro', uri=True)) as connection:
            geography = json.loads(connection.execute("SELECT value FROM metadata WHERE key='geography'").fetchone()[0])
        self.assertEqual(geography['validation'],
                         'ROOFTOP; OR; source CD005; point covered by its exact official block and district')

    def test_the_salt_file_is_created_at_the_given_path_and_nowhere_else(self):
        self.assertEqual(len(self.salt.read_bytes()), 32)
        self.assertFalse((self.directory / '.street-salt').exists())
        self.assertEqual(sorted(p.name for p in self.salt.parent.iterdir()), ['x.salt'])

    def test_contacts_use_the_given_salt(self):
        folder = Path(self.temporary.name) / 'contacts'
        folder.mkdir()
        shutil.copy(self.directory / 'street.sqlite3', folder / 'street.sqlite3')
        totals = build_contacts.build(self.csv_path, folder, self.salt)
        self.assertEqual(totals['records'], 4)
        salt = self.salt.read_bytes()
        with closing(sqlite3.connect((folder / 'contacts.sqlite3').as_uri() + '?mode=ro', uri=True)) as connection:
            ids = {row[0] for row in connection.execute('SELECT id FROM contacts')}
        self.assertEqual(ids, {build_street.opaque_id(salt, 'p', 'RAW-P%d' % n) for n in range(1, 5)})
        self.assertFalse((folder / '.street-salt').exists())
        with self.assertRaises(FileNotFoundError):       # the default is still directory/.street-salt
            build_contacts.build(self.csv_path, folder)


if __name__ == '__main__':
    unittest.main()
