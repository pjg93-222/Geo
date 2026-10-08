"""Phase 3b Lane S: the bundle validator, the meta answer, the area filter and the level catalog follow the served district
(settings.DISTRICT). The Colorado fixture world is renamed into an Oregon one (precinct, county, legislative and region ids,
labels, hierarchy and road sources, snapshot key). Synthetic data only."""
import copy
import json
import re
import tempfile
from pathlib import Path
from unittest import mock

from django.test import RequestFactory, SimpleTestCase, override_settings

from tools import area_layers as AL
from tools import districts as D
from tools.geography_levels import area_label, label, sources

from . import bundle_v4, bundles, v3_cache, v3_people_views as v3p, views
from .v3_http import BadRequest
from .v3_testkit import build_world, retarget

CO, OR, CT = D.get('co-05'), D.get('or-05'), D.get('ct-01')
NOTE = 'Interim overall: candidate support not yet included; final weights 30/25/25/20 arrive with candidate support'
ROADS = [f.rsplit('/', 1)[-1] for f in OR['road_files']]


def oregon(payload):
    """A copy of the Colorado fixture bundle that is a valid Oregon one."""
    payload = copy.deepcopy(payload)
    old = sorted({r['id'] for r in payload['area_indices.json']['rows'] if r['level'] == 'precinct'}
                 | set(payload['geometry.json']['precinct']))
    ids = {'08041': '41005', '08001': '41001', '0805': '4105'}
    ids.update({o: '41005-%03d' % (101 + k) for k, o in enumerate(old)})       # same order, so the rows stay sorted
    text = json.dumps(payload)
    for o, n in ids.items():
        text = text.replace('"%s"' % o, '"%s"' % n)
    payload = json.loads(text)
    for r in payload['rows.json']:
        r['label'] = label(r['level'], r['id'], OR)
    for r in payload['area_indices.json']['rows']:
        r['label'] = area_label(r['level'], r['id'], OR)
    payload['district.geojson']['features'][0]['properties']['label'] = OR['label']
    manifest = payload['manifest.json']
    manifest['hierarchy']['sources'] = sources(OR)
    manifest['geography']['sources']['roads'] = ['https://www2.census.gov/geo/tiger/TIGER2025/ROADS/' + f for f in ROADS]
    manifest['geography']['hashes'] = {name: 'a' * 64 for name in ['blocks.zip', 'districts.zip', *ROADS]}
    rf = manifest['row_framework']
    rf['base_snapshot'] = 'or-cd005-' + rf['base_snapshot'].split('-')[-1]
    manifest['snapshot'] = AL.snapshot_key(rf['base_snapshot'], rf['area_table_sha256'], rf['suppressed_ids_sha256'],
                                           rf['framework_config_sha256'], OR['snapshot_prefix'])
    return retarget(payload)


def validate_as(district, payload, pin='frozen'):
    """bundles.validate in a server process of `district`, whose frozen inputs are pinned to `pin` (None: not pinned)."""
    pin = dict(AL.FROZEN_SHA256) if pin == 'frozen' else pin
    with override_settings(DISTRICT=district), mock.patch.dict(AL.FROZEN_SHA256_BY_DISTRICT, {district['id']: pin}), \
            mock.patch.object(bundle_v4, 'SNAPSHOT_RE', re.compile(D.snapshot_pattern(district))):
        return bundles.validate(payload)


class DistrictBundleTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.co = build_world(Path(cls.temporary.name)).payload
        cls.oregon = oregon(cls.co)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()
        super().tearDownClass()

    def reject(self, mutate, message=None, **kwargs):
        payload = copy.deepcopy(self.oregon)
        mutate(payload)
        retarget(payload)
        with self.assertRaises(ValueError) as caught:
            validate_as(OR, payload, **kwargs)
        if message:
            self.assertIn(message, str(caught.exception))

    def test_oregon_bundle_validates_with_list_roads_and_road_file_hashes(self):
        manifest = validate_as(OR, copy.deepcopy(self.oregon))
        self.assertTrue(manifest['snapshot'].startswith('or-cd005-'))
        self.assertEqual(len(manifest['geography']['sources']['roads']), 6)
        self.assertEqual(set(manifest['geography']['hashes']), {'blocks.zip', 'districts.zip', *ROADS})
        self.assertTrue(validate_as(CO, copy.deepcopy(self.co))['snapshot'].startswith('co-cd005-'))

    def test_a_bundle_belongs_to_one_district(self):
        with self.assertRaises(ValueError):
            validate_as(CO, copy.deepcopy(self.oregon))
        with self.assertRaises(ValueError):
            validate_as(OR, copy.deepcopy(self.co))

    def test_precinct_ids_follow_the_district_pattern(self):
        with override_settings(DISTRICT=OR):
            pattern = bundle_v4.id_pattern('registration', 'precinct')
        self.assertTrue(re.fullmatch(pattern, '41005-123'))
        self.assertFalse(re.fullmatch(pattern, '12345'))
        with override_settings(DISTRICT=CO):
            self.assertEqual(bundle_v4.id_pattern('registration', 'precinct'), r'\d{10}')
            self.assertEqual(bundle_v4.id_pattern('registration', 'block'), r'\d{15}')

        def other_id(payload):
            for row in payload['area_indices.json']['rows']:
                if row['level'] == 'precinct':
                    row['id'] = '12345'
        self.reject(other_id, message='Invalid area id')

    def test_sources_and_hashes_are_checked(self):
        self.reject(lambda p: p['manifest.json']['geography']['sources'].update(roads=['https://example.com/r.zip']),
                    message='geographic source')
        self.reject(lambda p: p['manifest.json']['geography']['hashes'].pop(ROADS[-1]), message='fields')
        self.reject(lambda p: p['manifest.json']['hierarchy']['sources'].update(state_house=sources(CO)['state_house']),
                    message='geographic extension')

    def test_frozen_inputs_are_pinned_per_district_and_none_fails_closed(self):
        for pin, message in ((None, 'not pinned'), ({k: '0' * 64 for k in AL.FROZEN_SHA256}, 'frozen')):
            self.reject(lambda p: None, message=message, pin=pin)
        self.assertIs(AL.FROZEN_SHA256_BY_DISTRICT['co-05'], AL.FROZEN_SHA256)

    def test_a_wrong_freeze_is_refused_against_the_real_district_pins(self):
        # self.oregon claims Colorado's three frozen shas; Oregon's real pin must still refuse it, and so must Connecticut's.
        self.reject(lambda p: None, message='frozen', pin=AL.FROZEN_SHA256_BY_DISTRICT['or-05'])
        with self.assertRaises(ValueError) as caught:
            validate_as(OR, copy.deepcopy(self.oregon), pin=AL.FROZEN_SHA256_BY_DISTRICT['ct-01'])
        self.assertIn('frozen', str(caught.exception))


class DistrictMetaTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.payload = build_world(Path(cls.temporary.name)).payload

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()
        super().tearDownClass()

    def meta(self, district):
        with override_settings(DISTRICT=district), \
                mock.patch.object(v3_cache, 'read_json', lambda key, name: self.payload[name]):
            return v3_cache.build_meta(self.payload['manifest.json']['snapshot'])

    def test_colorado_meta_has_no_note_and_keeps_its_keys_and_weights(self):
        meta = self.meta(CO)
        self.assertNotIn('overall_note', meta)
        self.assertEqual(list(meta)[-1], 'registered_people')
        self.assertEqual((meta['district_label'], json.dumps(meta['weights'])), ('District 5', '{"cua": 40, "part": 35, "soi": 25}'))

    def test_oregon_and_connecticut_meta_carry_the_interim_note_and_their_label(self):
        for district, short in ((OR, 'District 5'), (CT, 'District 1')):
            meta = self.meta(district)
            self.assertEqual(meta['overall_note'], NOTE)
            self.assertEqual(meta['weights'], {'cua': 40, 'part': 35, 'soi': 25})
            self.assertEqual(meta['district_label'], short)

    def test_people_area_filter_and_level_catalog_follow_the_district(self):
        def query(area):
            return v3p.list_query(RequestFactory().get('/api/v3/people', {'level': 'precinct', 'area': area}))
        with override_settings(DISTRICT=OR):
            self.assertEqual(query('41005-123')['area'], '41005-123')
            with self.assertRaises(BadRequest):
                query('0804100621')
            self.assertEqual(views.available_levels('cu-summary-v4')[0]['vintage'], 'Study region: supplied Oregon CD-05 extract')
        with override_settings(DISTRICT=CO):
            self.assertEqual(query('0804100621')['area'], '0804100621')
            with self.assertRaises(BadRequest):
                query('41005-123')
            self.assertEqual(views.available_levels('cu-summary-v4')[0]['vintage'], 'Study region: supplied Colorado CD-05 extract')
