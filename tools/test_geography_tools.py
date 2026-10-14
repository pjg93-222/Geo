"""Synthetic checks for the geography, road, street and contacts tools' district parameters (multi-district build,
Phase 2, Lane B) and for the geography and bundle tools' district parameters (Phase 3b, Lane G). Plain unittest,
geopandas/shapely only, no Django, no real data, no network.

    <venv python> -m unittest tools.test_geography_tools          (from the V3 folder)
"""
import csv
import hashlib
import io
import json
import re
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Polygon, box, mapping

from tools import area_layers as AL
from tools import build_bundle_v4, build_contacts, build_road_context, build_street, extend_geography, ingest, measures, prepare_geography
from tools import districts as D
from tools import geography_levels as G


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
        self.assertEqual(prepare_geography.alias_map(frame), {})

    def test_alias_map_pairs_each_old_id_with_its_region_id(self):
        """Phase 3b Lane G (e): the map ingest, extend and the row-framework cache use."""
        blocks = self.blocks([(.5, .5), (1.5, .5), (.2, .2)])
        frame, counts = prepare_geography.recode_blocks(blocks, self.regions, '09')
        aliases = prepare_geography.alias_map(frame)
        new = ['09' + code + '%06d%04d' % (100 + i, 1000 + i) for i, code in enumerate(['110', '120', '110'])]
        self.assertEqual(aliases, dict(zip(blocks.GEOID20, new)))
        self.assertEqual(len(aliases), counts['aliases'])


def write_shapefile_zip(frame, path):
    """A zipped shapefile of `frame` at `path` (what the Census block and county downloads are)."""
    folder = Path(tempfile.mkdtemp(dir=path.parent))
    frame.to_file(folder / 'layer.shp')
    with zipfile.ZipFile(path, 'w') as archive:
        for part in sorted(folder.iterdir()):
            archive.write(part, part.name)


class BlocksFromSourceTests(unittest.TestCase):
    """Part B: a published block registered outside the district is read from the state block file, not blocks.gpkg."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def source(self, rows):
        cols = ['GEOID20', 'TRACTCE20', 'BLOCKCE20', 'INTPTLON20', 'INTPTLAT20']
        geoms = [box(x - .01, y - .01, x + .01, y + .01) for _, _, _, x, y in rows]
        frame = gpd.GeoDataFrame([(g, g[5:11], g[11:], '%+.7f' % x, '%+.7f' % y) for g, _, _, x, y in rows], columns=cols,
                                 geometry=geoms, crs=4326)
        write_shapefile_zip(frame, self.root / 'blocks.zip')
        return self.root / 'blocks.zip'

    def test_oregon_blocks_are_found_by_their_geoid(self):
        zipped = self.source([('410050001001001', 0, 0, .5, .5), ('410050001001002', 0, 0, .6, .5), ('410050002001001', 0, 0, .7, .5)])
        found = prepare_geography.blocks_from_source(zipped, D.get('or-05'), ['410050001001002', '410050002001001', '419999999999999'])
        self.assertEqual(sorted(found), ['410050001001002', '410050002001001'])
        self.assertTrue(all(g.geom_type == 'Polygon' for g in found.values()))

    def test_connecticut_blocks_are_found_by_their_region_id(self):
        zipped = self.source([('090010462201013', 0, 0, .5, .5), ('090010462201014', 0, 0, 1.5, .5), ('090030462201013', 0, 0, 1.5, .6)])
        regions = gpd.GeoDataFrame({'STATEFP': ['09', '09'], 'COUNTYFP': ['110', '130']}, geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)], crs=4326)
        write_shapefile_zip(regions, self.root / 'county.zip')
        with patch.object(prepare_geography, 'COUNTIES', self.root / 'county.zip'):
            found = prepare_geography.blocks_from_source(zipped, D.get('ct-01'), ['091100462201013', '091300462201013', '091100462201099'])
        self.assertEqual(sorted(found), ['091100462201013', '091300462201013'])      # the same tract and block number in two regions

    def test_nothing_found_is_an_empty_answer(self):
        zipped = self.source([('410050001001001', 0, 0, .5, .5)])
        self.assertEqual(prepare_geography.blocks_from_source(zipped, D.get('or-05'), ['410059999999999']), {})


class AliasesFromSourceTests(unittest.TestCase):
    """Phase 4 density: the region id of the state blocks that share a tract and block number with a table block."""

    def test_only_blocks_sharing_a_table_suffix_are_recoded(self):
        regions = gpd.GeoDataFrame({'STATEFP': ['09', '09'], 'COUNTYFP': ['110', '130']}, geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)], crs=4326)
        # the last two are water blocks of two old counties that would meet in one region id (recode_blocks stops on that)
        rows = [('090010462201013', .5, .5), ('090030462201013', 1.5, .6), ('090010462201014', .5, .5), ('090079901000005', 1.5, .5), ('090119901000005', 1.6, .5)]
        blocks = pd.DataFrame([(g, g[5:11], g[11:], '%+.7f' % x, '%+.7f' % y) for g, x, y in rows],
                              columns=['GEOID20', 'TRACTCE20', 'BLOCKCE20', 'INTPTLON20', 'INTPTLAT20'])
        with patch.object(prepare_geography.gpd, 'read_file', return_value=regions):
            aliases = prepare_geography.aliases_from_source(blocks, D.get('ct-01'), ['091100462201013', '091300462201013', '091100462299999'])
        self.assertEqual(aliases, {'090010462201013': '091100462201013', '090030462201013': '091300462201013'})


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


class CanonicalBlocksTests(unittest.TestCase):
    """Phase 3b Lane G, B-8: the old-county -> planning-region block map applied to the voter file."""

    def test_canonical_blocks_maps_both_bases_and_leaves_other_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'block_aliases.json').write_text(json.dumps({'090010001001001': '091100001001001'}))
            d = {**D.get('ct-01'), 'geography': folder}
            frame = pd.DataFrame({'svi_registration_censusid': ['090010001001001', '091100001001002', ''],
                                  'sii_censusid': ['091100001001001', '090010001001001', '']})
            ingest.canonical_blocks(frame, d)
            self.assertEqual(list(frame.svi_registration_censusid), ['091100001001001', '091100001001002', ''])
            self.assertEqual(list(frame.sii_censusid), ['091100001001001', '091100001001001', ''])
            other = {**D.get('or-05'), 'geography': Path(folder) / 'none'}            # no alias file: nothing changes
            again = pd.DataFrame({'svi_registration_censusid': ['090010001001001'], 'sii_censusid': ['090010001001001']})
            self.assertEqual(list(ingest.canonical_blocks(again, other).sii_censusid), ['090010001001001'])


# Colorado's outputs of tools.geography_levels, recorded from the code BEFORE Lane G changed it.
CO_GOLDEN = {
    'idents': {'block': '080410001001001', 'zcta': '80903', 'postal_zip': '80903', 'county': '08041', 'state_house': '08014',
               'state_senate': '08010', 'congressional': '0805', 'region': '0805'},
    'pattern': {'block': r'\d{15}', 'congressional': '0805', 'county': '08041', 'postal_zip': r'\d{5}', 'precinct': r'\d{10}',
                'region': '0805', 'state_house': r'08\d{3}', 'state_senate': r'08\d{3}', 'tract': r'\d{11}', 'zcta': r'\d{5}'},
    'label': {'block': 'Block 1001 · 08041000100', 'congressional': 'Colorado · Congressional District 05',
              'county': 'El Paso County · Colorado', 'postal_zip': 'Postal ZIP 80903', 'region': 'Colorado CD-05 study region',
              'state_house': 'Colorado House District 14', 'state_senate': 'Colorado Senate District 10', 'zcta': 'ZCTA 80903'},
    'area_label': {'block': 'Block 1001', 'precinct': 'Precinct 621', 'tract': 'Tract 1.02', 'zip': 'ZIP 80916'},
    'area_pattern': {'block': r'\d{15}', 'precinct': r'\d{10}', 'tract': r'\d{11}', 'zip': r'\d{5}'},
    'sources': {'county': 'https://www2.census.gov/geo/tiger/TIGER2025/COUNTY/tl_2025_us_county.zip',
                'state_house': 'https://www2.census.gov/geo/tiger/TIGER2025/SLDL/tl_2025_08_sldl.zip',
                'state_senate': 'https://www2.census.gov/geo/tiger/TIGER2025/SLDU/tl_2025_08_sldu.zip'},
    'catalog': [
        {'id': 'region', 'label': 'Regional overview', 'vintage': 'Study region: supplied Colorado CD-05 extract'},
        {'id': 'congressional', 'label': 'Congressional districts', 'vintage': '119th Congress · TIGER/Line 2025'},
        {'id': 'county', 'label': 'Counties', 'vintage': 'TIGER/Line 2025 counties'},
        {'id': 'state_senate', 'label': 'State Senate districts', 'vintage': '2024 legislative plans · TIGER/Line 2025'},
        {'id': 'state_house', 'label': 'State House districts', 'vintage': '2024 legislative plans · TIGER/Line 2025'},
        {'id': 'zcta', 'label': 'ZCTAs · ZIP approximation', 'vintage': '2020 ZCTAs'},
        {'id': 'block', 'label': 'Census blocks', 'vintage': '2020 Census blocks · TIGER/Line 2025'},
        {'id': 'postal_zip', 'label': 'Postal ZIPs · table only', 'vintage': 'Not applicable: supplied postal ZIP'}],
}
AREA_IDS = {'block': '080410001001001', 'precinct': '0804100621', 'tract': '08041000102', 'zip': '80916'}


class GeographyLevelsTests(unittest.TestCase):
    """Phase 3b Lane G, test (a) and (b): Colorado's strings do not move; Oregon and Connecticut get theirs."""

    def test_colorado_outputs_equal_the_golden_table_with_and_without_the_district(self):
        for d in (None, D.get('co-05')):
            ids = CO_GOLDEN['idents']
            self.assertEqual({lv: G.pattern(lv, d) for lv in CO_GOLDEN['pattern']}, CO_GOLDEN['pattern'])
            self.assertEqual({lv: G.label(lv, ids[lv], d) for lv in CO_GOLDEN['label']}, CO_GOLDEN['label'])
            self.assertEqual({lv: G.area_label(lv, AREA_IDS[lv], d) for lv in CO_GOLDEN['area_label']}, CO_GOLDEN['area_label'])
            self.assertEqual({lv: G.area_pattern(lv, d) for lv in CO_GOLDEN['area_pattern']}, CO_GOLDEN['area_pattern'])
            self.assertEqual(G.sources(d), CO_GOLDEN['sources'])
            self.assertEqual(G.catalog(d), CO_GOLDEN['catalog'])
            self.assertEqual(G.available('cu-summary-v4', d), CO_GOLDEN['catalog'])
            self.assertEqual(G.available('cu-summary-v1', d), [x for x in CO_GOLDEN['catalog'] if x['id'] in G.BASE_LEVELS])
        self.assertEqual((G.SOURCES, G.CATALOG), (CO_GOLDEN['sources'], CO_GOLDEN['catalog']))

    def test_oregon_labels_and_patterns(self):
        d = D.get('or-05')
        self.assertEqual(G.pattern('county', d), '41003|41005|41017|41031|41043|41047|41051')
        self.assertEqual((G.pattern('region', d), G.pattern('congressional', d), G.pattern('state_house', d)),
                         ('4105', '4105', r'41\d{3}'))
        self.assertEqual(G.label('county', '41005', d), 'Clackamas County · Oregon')
        self.assertEqual(G.label('state_house', '41014', d), 'Oregon House District 14')
        self.assertEqual(G.label('state_senate', '41007', d), 'Oregon Senate District 7')
        self.assertEqual(G.label('congressional', '4105', d), 'Oregon · Congressional District 05')
        self.assertEqual(G.label('region', '4105', d), 'Oregon CD-05 study region')
        self.assertEqual(G.sources(d)['state_house'], 'https://www2.census.gov/geo/tiger/TIGER2025/SLDL/tl_2025_41_sldl.zip')
        self.assertEqual(G.sources(d)['county'], G.SOURCES['county'])
        self.assertEqual(G.catalog(d)[0]['vintage'], 'Study region: supplied Oregon CD-05 extract')
        self.assertTrue(re.fullmatch(G.area_pattern('precinct', d), '41005-123'))
        self.assertTrue(re.fullmatch(G.pattern('precinct', d), '123'))
        self.assertFalse(re.fullmatch(G.area_pattern('precinct', d), '12345'))
        self.assertEqual(G.area_label('precinct', '41005-123', d), 'Precinct 41005-123')
        self.assertEqual(G.area_label('precinct', '0804100621', d), 'Precinct 621')       # a 10-digit code keeps Colorado's form

    def test_connecticut_labels_and_patterns(self):
        d = D.get('ct-01')
        self.assertEqual(G.pattern('county', d), '09110|09130|09140|09160|09170')
        self.assertEqual((G.pattern('region', d), G.pattern('state_senate', d)), ('0901', r'09\d{3}'))
        self.assertEqual(G.label('county', '09110', d), 'Capitol Planning Region · Connecticut')
        self.assertEqual(G.label('state_senate', '09003', d), 'Connecticut Senate District 3')
        self.assertEqual(G.label('region', '0901', d), 'Connecticut CD-01 study region')
        self.assertTrue(re.fullmatch(G.area_pattern('precinct', d), '005-00-WEST-HARTFORD-1'))
        self.assertFalse(re.fullmatch(G.area_pattern('precinct', d), '0804100621'))
        self.assertEqual(G.area_label('precinct', '005-00-WEST-HARTFORD-1', d), 'Precinct 005-00-WEST-HARTFORD-1')


class SnapshotKeyTests(unittest.TestCase):
    """Test (f): the district prefix is the only difference."""

    def test_prefix_changes_the_prefix_only(self):
        sha = 'a' * 64
        self.assertEqual(measures.snapshot_key(sha), measures.snapshot_key(sha, 'co-cd005-'))
        self.assertEqual(measures.snapshot_key(sha, 'or-cd005-')[len('or-cd005-'):], measures.snapshot_key(sha)[len('co-cd005-'):])
        self.assertRegex(measures.snapshot_key(sha, 'ct-cd001-'), r'^ct-cd001-[a-f0-9]{12}$')
        args = ('b' * 64, 'c' * 64, 'd' * 64)
        self.assertEqual(AL.snapshot_key(*args), AL.snapshot_key(*args, prefix='co-cd005-'))
        self.assertEqual(AL.snapshot_key(*args, 'or-cd005-')[len('or-cd005-'):], AL.snapshot_key(*args)[len('co-cd005-'):])

    def test_the_geometry_form_token_is_part_of_the_v4_key(self):
        """Canonical-geometry bundles get a key of their own: bundles.import_bundle refuses to replace the contents of an immutable snapshot
        under an existing key, so a bundle whose geometry.json is written another way must not share the key of the older ones."""
        args = ('b' * 64, 'c' * 64, 'd' * 64)
        self.assertEqual(AL.GEOMETRY_FORM, 'geom-canonical-1')
        joined = lambda *parts: 'co-cd005-' + hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()[:12]
        self.assertEqual(AL.snapshot_key(*args), joined(*args, AL.ROWFW_VERSION, 'geom-canonical-1'))
        self.assertNotEqual(AL.snapshot_key(*args), joined(*args, AL.ROWFW_VERSION))          # the key of the same inputs before the token
        kwargs = dict(base_key='co-cd005-0123456789ab', area_sha='c' * 64, run_id='r', created_at='x', concordance={},
                      frozen={'framework_config_sha256': 'e' * 64, 'plan_sha256': 'f' * 64, 'criteria_sha256': '1' * 64})
        manifest = build_bundle_v4.apply_v4_manifest({}, [], {}, **kwargs)
        self.assertEqual(manifest['snapshot'], joined('co-cd005-0123456789ab', 'c' * 64, 'e' * 64, AL.ROWFW_VERSION, 'geom-canonical-1'))

    def test_a_manifest_takes_the_district_prefix(self):
        kwargs = dict(base_key='or-cd005-0123456789ab', area_sha='c' * 64,
                      frozen={'framework_config_sha256': 'e' * 64, 'plan_sha256': 'f' * 64, 'criteria_sha256': '1' * 64},
                      run_id='r', created_at='t', concordance={})
        manifest = build_bundle_v4.apply_v4_manifest({}, [], {}, prefix='or-cd005-', **kwargs)
        self.assertRegex(manifest['snapshot'], r'^or-cd005-[a-f0-9]{12}$')
        self.assertTrue(build_bundle_v4.apply_v4_manifest({}, [], {}, **kwargs)['snapshot'].startswith('co-cd005-'))

    def test_district_pins_are_each_districts_own_freeze(self):
        self.assertIs(AL.FROZEN_SHA256_BY_DISTRICT['co-05'], AL.FROZEN_SHA256)
        self.assertEqual(set(AL.FROZEN_SHA256_BY_DISTRICT), set(D.DISTRICTS))
        for district in ('or-05', 'ct-01'):
            pin = AL.FROZEN_SHA256_BY_DISTRICT[district]
            self.assertEqual(set(pin), set(AL.FROZEN_SHA256))
            self.assertTrue(all(AL.HEX64.fullmatch(sha) for sha in pin.values()))
            self.assertNotEqual(pin, AL.FROZEN_SHA256)
        self.assertNotEqual(AL.FROZEN_SHA256_BY_DISTRICT['or-05'], AL.FROZEN_SHA256_BY_DISTRICT['ct-01'])

    def test_district_pins_hold_the_four_term_freeze(self):
        """The 30/25/25/20 frameworks of Oregon and Connecticut (their rowfw-pin receipts, 2026-10-08); the plan file is unchanged."""
        self.assertEqual(AL.FROZEN_SHA256_BY_DISTRICT['or-05'], {
            'framework_config_sha256': '6879bca9a482b106c5ca3379224611e7c9d7f2acb12705cda234ed9ee8f775f4',
            'plan_sha256': '7645762d4e17aa2a023b780e64a63aa8459c124e4c6ff5d439fb96f0b1fd856a',
            'criteria_sha256': 'd1ba4ff5c81d6226eb5d5fd88c6179a5b7234ae2f8585f15e9773852fefc9c6c'})
        self.assertEqual(AL.FROZEN_SHA256_BY_DISTRICT['ct-01'], {
            'framework_config_sha256': 'b866a12f67eb1ade0f6b4b0abb782b0b8e1cc9b859997a88712e1a5005d04cfc',
            'plan_sha256': '81249df41506a6c836fffb3fa9c620e4b4d2cebfd9ad3a188a4e4c1266e63530',
            'criteria_sha256': '963a26d5123fc05963822ea0dee3339ad5d2c33e516b1317390587b9f507850f'})


class ZctaCleanTests(unittest.TestCase):
    """Test (d): invalid ZCTAs are made valid and keep their polygonal parts; valid ones are the same objects."""

    def test_bowtie_becomes_two_triangles(self):
        bowtie = Polygon([(0, 0), (2, 2), (2, 0), (0, 2), (0, 0)])
        self.assertFalse(bowtie.is_valid)
        fixed = ingest.polygonal(bowtie)
        self.assertTrue(fixed.is_valid)
        self.assertIn(fixed.geom_type, ('Polygon', 'MultiPolygon'))
        self.assertAlmostEqual(fixed.area, 2.0)

    def test_a_spike_loses_its_line_and_keeps_the_polygon(self):
        spiked = Polygon([(0, 0), (2, 0), (2, 2), (1, 2), (1, 3), (1, 2), (0, 2), (0, 0)])
        self.assertFalse(spiked.is_valid)
        fixed = ingest.polygonal(spiked)
        self.assertTrue(fixed.is_valid)
        self.assertIn(fixed.geom_type, ('Polygon', 'MultiPolygon'))
        self.assertAlmostEqual(fixed.area, 4.0)

    def test_valid_geometry_is_returned_unchanged(self):
        square = box(0, 0, 1, 1)
        self.assertIs(ingest.polygonal(square), square)


class OfficialBoundariesTests(unittest.TestCase):
    """Test (c): the water-only 'ZZZ' areas of Connecticut's legislative files are dropped; a Colorado file loses nothing."""

    def frame(self, state, ids):
        return gpd.GeoDataFrame({'STATEFP': [state] * len(ids), 'GEOID': ids},
                                geometry=[box(0, 0, 1, 1)] * len(ids), crs=4326)

    def test_zzz_other_states_and_non_overlapping_areas_are_dropped(self):
        frame = pd.concat([self.frame('09', ['09001', '09ZZZ']), self.frame('25', ['25001']),
                           gpd.GeoDataFrame({'STATEFP': ['09'], 'GEOID': ['09002']}, geometry=[box(5, 5, 6, 6)], crs=4326)])
        found = extend_geography.official_boundaries(frame, box(0, 0, 2, 2), '09')
        self.assertEqual(list(found), ['09001'])

    def test_colorado_ids_all_stay(self):
        found = extend_geography.official_boundaries(self.frame('08', ['08001', '08002']), box(0, 0, 2, 2), '08')
        self.assertEqual(list(found), ['08001', '08002'])


class PseudoAreaTests(unittest.TestCase):
    """Test (g): the pseudo-area rowfw makes of a blank id is dropped, and the D2 receipt arithmetic counts it."""

    @staticmethod
    def row(level, ident, suppressed='0', group='ovr', basis='registration'):
        return {'basis': basis, 'level': level, 'area_id': ident, 'suppressed': suppressed, 'group': group}

    def test_the_nan_area_is_dropped_once_and_the_counts_still_reconcile(self):
        area = [self.row('block', '410050001001001'), self.row('block', 'nan'), self.row('block', 'nan', group='cua')]
        receipt = {'D2_counts': {'registration/block': {'areas': 2, 'published': 1}}}
        kept_area, dropped = build_bundle_v4.drop_pseudo_areas(area, D.get('or-05'))
        self.assertEqual([r['area_id'] for r in kept_area], ['410050001001001'])
        self.assertEqual(dropped, {'registration/block': 1})                                    # two group rows, one area
        shown = build_bundle_v4.check_area_table(kept_area, receipt, dropped)
        self.assertEqual(len(shown[('registration', 'block')]), 1)
        with self.assertRaisesRegex(build_bundle_v4.BuildError, 'Areas of registration/block'):
            build_bundle_v4.check_area_table(kept_area, receipt)                                # without the drop count
        with self.assertRaisesRegex(build_bundle_v4.BuildError, 'marks an area suppressed'):
            build_bundle_v4.check_area_table([self.row('block', '410050001001001', '1')], receipt, dropped)

    def test_ids_follow_each_levels_pattern(self):
        d = D.get('or-05')
        area = [self.row('precinct', '41005-123'), self.row('precinct', '12345'), self.row('precinct', 'nan'),
                self.row('zip', '97045', basis='residence'), self.row('zip', 'nan', basis='residence')]
        kept_area, dropped = build_bundle_v4.drop_pseudo_areas(area, d)
        self.assertEqual([r['area_id'] for r in kept_area], ['41005-123', '97045'])
        self.assertEqual(dropped, {'registration/precinct': 2, 'residence/zip': 1})

    def test_standings_of_a_level_are_re_ranked_without_its_dropped_area(self):
        ids, values = ['97001', '97002', '97003', 'nan'], [10.0, 20.0, 30.0, 40.0]
        ranks = AL.midrank(values)
        area = [dict(self.row('zip', i, group='ovr'), eb_mean=v, area_pct=p, band=AL.band(p), top10_share_eb=v / 100,
                     top10_area_pct=p, top10_band=AL.band(p)) for i, v, p in zip(ids, values, ranks)]
        other = dict(self.row('block', '410050001001001'), eb_mean=5.0, area_pct=50.0, band=3, top10_share_eb=0.05,
                     top10_area_pct=50.0, top10_band=3)
        kept, dropped = build_bundle_v4.drop_pseudo_areas(area + [other], D.get('or-05'))
        changed = build_bundle_v4.rerank_after_drop(kept, dropped)
        zips = [r for r in kept if r['level'] == 'zip']
        self.assertEqual([r['area_pct'] for r in zips], AL.midrank(values[:3]))      # what the app's validator recomputes
        self.assertTrue(all(r['band'] == AL.band(r['area_pct']) and r['top10_band'] == AL.band(r['top10_area_pct']) for r in zips))
        self.assertEqual(changed, {'registration/zip': 2})                                # bands 2 -> 3 and 4 -> 5
        self.assertEqual((other['area_pct'], other['band']), (50.0, 3))                   # a level with no dropped area is untouched
        self.assertEqual(build_bundle_v4.rerank_after_drop(kept, {}), {})

    def test_colorado_ids_all_stay(self):
        area = [self.row('block', '080410001001001'), self.row('precinct', '0804100621'), self.row('tract', '08041000102'),
                self.row('zip', '80916')]
        kept_area, dropped = build_bundle_v4.drop_pseudo_areas(area, D.get('co-05'))
        self.assertEqual((len(kept_area), dropped), (4, {}))


FIELDS_ALL = list(dict.fromkeys(ingest.FIELDS + list(measures.SOURCE_FIELDS)))


class DistrictChainTests(unittest.TestCase):
    """ingest -> extend_geography on a tiny synthetic Connecticut-like district (a re-coded block, an old-county id,
    an invalid ZCTA, a water-only 'ZZZ' legislative area), no network and no real data."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        root = Path(cls.temporary.name)
        cls.geography = root / 'geography'
        (cls.geography / 'hierarchy').mkdir(parents=True)
        district = box(-72.80, 41.70, -72.60, 41.90)
        cls.d = {**D.get('ct-01'), 'geography': cls.geography, 'counties': ('09110',),
                 'county_names': {'09110': 'Test Planning Region'}}
        new_a, old_a, new_b = '091100001001001', '090010001001001', '091100001001002'
        block_a, block_b = box(-72.75, 41.75, -72.74, 41.76), box(-72.65, 41.75, -72.64, 41.76)
        gpd.GeoDataFrame({'geoid': [new_a, new_b, old_a]}, geometry=[block_a, block_b, block_a], crs=4326).to_file(
            cls.geography / 'blocks.gpkg', driver='GPKG')
        (cls.geography / 'block_aliases.json').write_text(json.dumps({old_a: new_a}))
        (cls.geography / 'district.geojson').write_text(json.dumps({'type': 'FeatureCollection', 'features': [{
            'type': 'Feature', 'properties': {}, 'geometry': mapping(district)}]}))
        (cls.geography / 'streets.geojson').write_text(json.dumps({'type': 'FeatureCollection', 'features': []}))
        (cls.geography / 'provenance.json').write_text(json.dumps({'block_vintage': 'synthetic'}))
        bowtie = Polygon([(-72.9, 42.0), (-72.5, 42.4), (-72.5, 42.0), (-72.9, 42.4), (-72.9, 42.0)])
        gpd.GeoDataFrame({'geoid': ['06001', '06002', 'XX'], 'geometry': [district, bowtie, box(0, 0, 1, 1)]}, crs=4326).to_file(
            cls.geography / 'zcta.geojson', driver='GeoJSON')
        for name, ids in (('county', ['09110']), ('state_house', ['09001', '09ZZZ']), ('state_senate', ['09001', '09ZZZ'])):
            cls.shapefile_zip(root / 'shp' / name, cls.geography / 'hierarchy' / (name + '.zip'), ids, district)
        header = FIELDS_ALL
        cls.csv_path = root / 'statara_ct_cd001_20260904.csv'
        people = [(new_a, 30), (old_a, 30), (new_b, 5)]          # new_b: an area under 20 people, shown like any other
        with cls.csv_path.open('w', newline='', encoding='cp1252') as stream:
            writer = csv.DictWriter(stream, fieldnames=header)
            writer.writeheader()
            n = 0
            for geoid, count in people:
                x, y = (-72.745, 41.755) if geoid != new_b else (-72.645, 41.755)
                for _ in range(count):
                    n += 1
                    row = {field: '' for field in header}
                    row.update(sii_persist_id='P%d' % n, sii_statara_id='S%d' % n, svi_registration_congressional_district='001')
                    for prefix in ingest.BASES.values():
                        row.update({prefix + '_state': 'CT', prefix + '_zip': '06001', prefix + '_censusid': geoid,
                                    prefix + '_latitude': y, prefix + '_longitude': x, prefix + '_geolevel': 'ROOFTOP'})
                    writer.writerow(row)
        cls.v2 = root / 'v2'
        with redirect_stdout(io.StringIO()):
            ingest.ingest(cls.csv_path, cls.geography, cls.v2, d=cls.d)
            extend_geography.build(cls.csv_path, cls.v2, cls.geography, root / 'v3', cls.d)
        cls.v2_payload = {p.name: json.loads(p.read_text(encoding='utf-8')) for p in cls.v2.glob('*.json')}
        cls.v3_payload = {p.name: json.loads(p.read_text(encoding='utf-8')) for p in (root / 'v3').glob('*.json')}
        cls.new_a, cls.new_b, cls.old_a = new_a, new_b, old_a

    @staticmethod
    def shapefile_zip(folder, archive, ids, district):
        folder.mkdir(parents=True)
        gpd.GeoDataFrame({'STATEFP': ['09'] * len(ids), 'GEOID': ids}, geometry=[district] * len(ids), crs=4326).to_file(folder / 'x.shp')
        with zipfile.ZipFile(archive, 'w') as bundle:
            for part in folder.iterdir():
                bundle.write(part, part.name)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_v2_uses_the_district_label_key_scope_and_file_date(self):
        manifest = self.v2_payload['manifest.json']
        self.assertEqual((manifest['label'], manifest['source_date']), ('Connecticut · District 01', '2026-09-04'))
        self.assertRegex(manifest['snapshot'], r'^ct-cd001-[a-f0-9]{12}$')
        self.assertTrue(manifest['scope'].startswith('Connecticut CD-001 source-file extract'))
        self.assertEqual(manifest['bases']['registration']['mapped'], 65)

    def test_an_old_county_id_and_its_region_id_are_one_block_area(self):
        for basis in ('registration', 'residential'):
            blocks = {r['id']: r for r in self.v2_payload['rows.json'] if r['basis'] == basis and r['level'] == 'block'}
            self.assertEqual(set(blocks), {self.new_a, self.new_b}, basis)
            self.assertEqual((blocks[self.new_a]['count'], blocks[self.new_b]['count']), (60, 5), basis)
        self.assertNotIn(self.old_a, self.v2_payload['geometry.json']['block'])

    def test_nothing_is_suppressed_not_even_a_five_person_block(self):
        for payload in (self.v2_payload, self.v3_payload):
            self.assertNotIn('threshold', payload['manifest.json'])
            self.assertTrue(all(r['status'] == 'ok' for r in payload['rows.json']))
            self.assertTrue(all(c[4] != 'suppressed' for r in payload['rows.json'] for c in r['measures'].values()))
        small = next(r for r in self.v2_payload['rows.json'] if r['id'] == self.new_b and r['basis'] == 'registration')
        self.assertEqual((small['count'], small['source_count'], small['share']), (5, 5, 5 / 65))
        self.assertIn(small['id'], self.v2_payload['geometry.json']['block'])

    def test_zctas_are_five_digit_and_valid(self):
        self.assertEqual({r['id'] for r in self.v2_payload['rows.json'] if r['level'] == 'zcta'}, {'06001'})
        self.assertEqual(set(self.v2_payload['geometry.json']['zcta']), {'06001'})
        self.assertEqual(self.v2_payload['manifest.json']['bases']['registration']['zcta_mapped'], 65)

    def test_v3_hierarchy_is_the_districts_own(self):
        manifest = self.v3_payload['manifest.json']
        self.assertEqual(manifest['schema'], 'cu-summary-v3')
        self.assertRegex(manifest['snapshot'], r'^ct-cd001-[a-f0-9]{12}$')
        self.assertEqual(manifest['hierarchy']['sources'], G.sources(self.d))
        self.assertEqual(manifest['hierarchy']['region_definition'],
                         'The supplied Connecticut CD-01 study region; not statewide or national coverage.')
        rows = [r for r in self.v3_payload['rows.json'] if r['basis'] == 'registration']
        by_level = {lv: {r['id']: r['label'] for r in rows if r['level'] == lv} for lv in G.EXTRA_LEVELS}
        self.assertEqual(by_level['state_house'], {'09001': 'Connecticut House District 1'})          # '09ZZZ' dropped
        self.assertEqual(by_level['county'], {'09110': 'Test Planning Region · Connecticut'})
        self.assertEqual(by_level['region'], {'0901': 'Connecticut CD-01 study region'})
        self.assertEqual(set(self.v3_payload['geometry.json']['state_senate']), {'09001'})

    def test_a_missing_hierarchy_file_is_never_downloaded_for_another_district(self):
        with tempfile.TemporaryDirectory() as folder:
            geography = Path(folder) / 'geography'
            shutil.copytree(self.geography, geography)
            (geography / 'hierarchy' / 'county.zip').unlink()
            with self.assertRaisesRegex(FileNotFoundError, 'county'):
                extend_geography.build(self.csv_path, self.v2, geography, Path(folder) / 'v3', {**self.d, 'geography': geography})

    def test_source_date_needs_a_dated_file_name(self):
        self.assertEqual(ingest.source_date('statara_or_cd005_20260904.csv'), '2026-09-04')
        with self.assertRaises(ValueError):
            ingest.source_date('statara.csv')


if __name__ == '__main__':
    unittest.main()
