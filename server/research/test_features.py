"""GET /api/v3/features and /api/v3/density (Phase 4 Lane 0): the district's settings and its density file, against a
throw-away data directory. Configuration only: no person, no store row is read. Synthetic data only."""
import json
import tempfile
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.urls import resolve

from tools import co_baseline
from tools import districts as D
from tools import field_labels as FL

from . import scoresdb, v3_area_views as v3a

LOOPBACK = dict(HTTP_HOST='localhost')
OR, CT, CO = D.get('or-05'), D.get('ct-01'), D.get('co-05')
BASE_LAYERS = ['ovr', 'cua', 'part', 'soi', 'lean']


@override_settings(LOCAL_ADMIN_ACCESS=True, HOSTED=False, DEMO_MODE=False)
class FeaturesTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.client = Client(**LOOPBACK)

    def features(self, district, store=None):
        """The payload for `district` with the data directory `self.root`; `store` is what scoresdb.info() gives."""
        info = mock.patch.object(scoresdb, 'info', side_effect=store or scoresdb.StoreError('not built'))
        with override_settings(DISTRICT=district, DATA_DIR=self.root), info:
            response = self.client.get('/api/v3/features')
        self.assertEqual(response.status_code, 200, response.content[:300])
        return json.loads(response.content)

    def test_the_routes_are_before_the_catch_all(self):
        self.assertIs(resolve('/api/v3/features').func, v3a.features)
        self.assertIs(resolve('/api/v3/density').func, v3a.density)

    def test_colorado_has_no_candidate_no_street_plan_and_no_support_layer(self):
        body = self.features(CO)
        self.assertEqual((body['schema'], body['district'], body['industry']), ('v3-features-1', 'co-05', 'cua'))
        self.assertEqual((body['candidate'], body['roi']), (None, None))
        self.assertEqual(body['overlay'], {'layers': BASE_LAYERS, 'density': False})
        # even a store that holds a support table adds nothing for a district without a candidate
        self.assertEqual(self.features(CO, lambda: SimpleNamespace(meta={'support': {}}))['overlay']['layers'], BASE_LAYERS)

    def test_the_industry_is_cua_with_the_frozen_weights(self):
        (cua,) = self.features(CO)['industries']
        self.assertEqual((cua['id'], cua['label']), ('cua', 'Credit Union Alignment'))
        rows = [r for r in FL.FIELDS if r['group'] == 'cua']
        self.assertEqual([(f['name'], f['label'], f['weight']) for f in cua['fields']],
                         [(r['name'], r['label'], r['weight']) for r in rows])
        self.assertEqual(len(rows), 12)
        self.assertEqual(sum(Fraction(*f['weight']) for f in cua['fields']), 1)
        self.assertEqual(FL.INDUSTRIES['cua']['fields'], [(r['name'], r['label'], r['weight']) for r in rows])

    def test_party_codes_name_working_families_and_leave_t_unnamed(self):
        codes = self.features(CO)['party_codes']
        self.assertEqual(codes, {'D': 'Democrat', 'R': 'Republican', 'U': 'Unaffiliated', 'G': 'Green', 'L': 'Libertarian',
                                 'O': 'Other', 'W': 'Working Families'})
        self.assertEqual(FL.value_text('svi_party_registration', 'W'), 'Working Families')
        self.assertEqual(FL.value_text('svi_party_registration', 'T'), 'Code T')
        self.assertGreater(FL.self_check(), 0)

    def test_oregon_and_connecticut_name_the_candidate_the_mode_and_the_street_plan(self):
        for district, name in ((OR, 'Janelle Bynum'), (CT, 'Luke Bronin')):
            body = self.features(district)
            self.assertEqual(body['candidate'], {
                'name': name, 'party': 'D', 'party_label': 'Democrat', 'race_mode': 'general',
                'primary_universe': 'party_only',
                'signals': {'partisan': 1, 'presidential': 1, 'registration': 1, 'primary_ballot': 1}})
            roi = body['roi']
            self.assertEqual([p['id'] for p in roi['presets']], ['push', 'mixed', 'turnout', 'identify'])
            self.assertEqual([p['label'] for p in roi['presets']],
                             ['Needs a push', 'Mixed-support streets', 'Supporter turnout', 'Voter identification'])
            self.assertTrue(all(p['formula'] for p in roi['presets']))
            self.assertEqual({k: v for k, v in roi.items() if k != 'presets'}, {
                'preset': 'push', 'p': 'participation', 'weights': {'value': 1.0, 'pop_density': 0.0, 'voter_density': 0.0},
                'min_doors': 5, 'min_length_m': 50})

    def test_the_mode_shown_is_the_configured_one_primary_included(self):
        body = self.features({**OR, 'race_mode': 'primary', 'primary_universe': 'party_plus_unaffiliated'})
        self.assertEqual((body['candidate']['race_mode'], body['candidate']['primary_universe']),
                         ('primary', 'party_plus_unaffiliated'))

    def test_support_and_density_layers_follow_what_is_built(self):
        self.assertEqual(self.features(OR)['overlay'], {'layers': BASE_LAYERS, 'density': False})
        store = lambda: SimpleNamespace(meta={'support': {}})
        self.assertEqual(self.features(OR, store)['overlay'], {'layers': BASE_LAYERS + ['sup'], 'density': False})
        self.assertEqual(self.features(OR, lambda: SimpleNamespace(meta={}))['overlay']['layers'], BASE_LAYERS)
        (self.root / 'density.json').write_text('{}', encoding='utf-8')
        self.assertEqual(self.features(OR, store)['overlay'],
                         {'layers': BASE_LAYERS + ['sup', 'pop_density', 'voter_density'], 'density': True})
        self.assertEqual(self.features(CO)['overlay'],
                         {'layers': BASE_LAYERS + ['pop_density', 'voter_density'], 'density': True})

    def test_it_is_a_signed_in_get_with_no_parameters_and_revalidates(self):
        with override_settings(DISTRICT=CO, DATA_DIR=self.root):
            first = self.client.get('/api/v3/features')
            self.assertEqual(first['Cache-Control'], 'private, no-cache')
            self.assertEqual(self.client.get('/api/v3/features?x=1').status_code, 400)
            self.assertEqual(self.client.post('/api/v3/features').status_code, 405)
            self.assertEqual(Client(HTTP_HOST='localhost', REMOTE_ADDR='192.0.2.10').get('/api/v3/features').status_code, 401)
            self.assertEqual(self.client.get('/api/v3/features', HTTP_IF_NONE_MATCH=first['ETag']).status_code, 304)

    def test_the_new_paths_stay_out_of_the_colorado_gate(self):
        self.assertFalse([t for _, t in co_baseline.ENDPOINTS if t.startswith(('/api/v3/features', '/api/v3/density'))])


@override_settings(LOCAL_ADMIN_ACCESS=True, HOSTED=False, DEMO_MODE=False)
class DensityTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.client = Client(**LOOPBACK)

    def get(self, url='/api/v3/density', **headers):
        with override_settings(DATA_DIR=self.root):
            return self.client.get(url, **headers)

    def test_it_is_a_404_with_a_plain_message_until_the_file_is_built(self):
        response = self.get()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(json.loads(response.content)['error'], 'Density is not built yet.')

    def test_it_serves_the_file_as_it_is_and_revalidates(self):
        body = {'schema': 'density-1', 'district': 'co-05', 'levels': {'block': {'ids': ['b1'], 'pop_density': [12.5]}}}
        (self.root / 'density.json').write_text(json.dumps(body), encoding='utf-8')
        response = self.get()
        self.assertEqual((response.status_code, response['Content-Type']), (200, 'application/json'))
        self.assertEqual(json.loads(response.content), body)
        self.assertEqual(response['Cache-Control'], 'private, no-cache')
        self.assertEqual(self.get(HTTP_IF_NONE_MATCH=response['ETag']).status_code, 304)
        body['district'] = 'oregon-05'                                            # a rebuilt file is served at once
        (self.root / 'density.json').write_text(json.dumps(body), encoding='utf-8')
        self.assertEqual(json.loads(self.get().content)['district'], 'oregon-05')

    def test_it_is_a_signed_in_get_with_no_parameters(self):
        (self.root / 'density.json').write_text('{}', encoding='utf-8')
        self.assertEqual(self.get('/api/v3/density?x=1').status_code, 400)
        with override_settings(DATA_DIR=self.root):
            self.assertEqual(self.client.post('/api/v3/density').status_code, 405)
            self.assertEqual(Client(HTTP_HOST='localhost', REMOTE_ADDR='192.0.2.10').get('/api/v3/density').status_code, 401)
