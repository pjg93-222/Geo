"""Synthetic public catalogs only: access, clipping, counts and local file integrity."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest import mock

from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import resolve

from . import area_street_views as A

CATALOGS = '/api/v3/walk/public-streets/catalogs'
QUERY = '/api/v3/walk/public-streets/query'
FULL = [[-122.54, 45.44], [-122.50, 45.44], [-122.50, 45.47], [-122.54, 45.47]]


def fixture():
    return {'version': 1, 'id': 'sample', 'label': 'Public sample roads',
            'bbox': [-122.54, 45.44, -122.50, 45.47], 'source_date': '2026-10-22',
            'count_basis': 'Public residential GIS points: planning estimates, not occupied or accessible doors.',
            'sources': [{'label': 'Public GIS source', 'url': 'https://example.org/gis'}],
            'streets': [
                {'id': 'alpha', 'label': 'Alpha Street',
                 'geometry': {'type': 'MultiLineString', 'coordinates': [
                     [[-122.535, 45.45], [-122.505, 45.45]]]},
                 'address_points': [[-122.53, 45.45, 1], [-122.52, 45.45, 2], [-122.51, 45.45, 3]]},
                {'id': 'empty', 'label': 'Uncounted Road',
                 'geometry': {'type': 'MultiLineString', 'coordinates': [
                     [[-122.535, 45.46], [-122.505, 45.46]]]}, 'address_points': []}]}


@override_settings(LOCAL_ADMIN_ACCESS=True, HOSTED=False, DEMO_MODE=False, DESKTOP_BOUNDARY=False)
class PublicStreetTests(SimpleTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.override = override_settings(DATA_DIR=Path(self.temporary.name))
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.directory = Path(self.temporary.name) / 'public_streets'
        self.directory.mkdir()
        self.path = self.directory / 'sample.json'
        self.value = fixture()
        self.save()
        A._cached_catalog.cache_clear()
        self.addCleanup(A._cached_catalog.cache_clear)
        self.factory = RequestFactory()

    def save(self):
        self.path.write_text(json.dumps(self.value), encoding='utf-8')

    def request(self, method='post', body=None, endpoint=QUERY, **extra):
        if method == 'post':
            request = self.factory.post(endpoint, data=json.dumps(body if body is not None else {
                'catalog_id': 'sample', 'polygon': FULL}), content_type='application/json',
                REMOTE_ADDR='127.0.0.1', **extra)
        else:
            request = getattr(self.factory, method)(endpoint, REMOTE_ADDR='127.0.0.1', **extra)
        request.user = SimpleNamespace(is_authenticated=True)
        request.local_admin_access = True
        return request

    def call(self, body=None, status=200):
        result = A.query(self.request(body=body))
        self.assertEqual(result.status_code, status, result.content)
        self.assertEqual(result['Cache-Control'], 'no-store, private')
        return json.loads(result.content)

    def test_routes_and_catalog_metadata(self):
        self.assertEqual(resolve(CATALOGS).func, A.catalogs)
        self.assertEqual(resolve(QUERY).func, A.query)
        result = A.catalogs(self.request('get', endpoint=CATALOGS))
        metadata = json.loads(result.content)['catalogs'][0]
        self.assertEqual((metadata['street_count'], metadata['estimated_doors']), (2, 3))
        self.assertEqual(set(metadata), {'id', 'label', 'bbox', 'street_count', 'estimated_doors',
                                         'source_date', 'count_basis', 'sources'})
        self.assertNotIn('address_points', metadata)

    def test_clip_counts_boundary_points_and_zero_estimate_street(self):
        polygon = [[-122.53, 45.445], [-122.52, 45.445], [-122.52, 45.465], [-122.53, 45.465]]
        payload = self.call({'catalog_id': 'sample', 'polygon': polygon})
        self.assertEqual(payload['estimated_doors'], 2)
        self.assertEqual([s['estimated_doors'] for s in payload['streets']], [2, 0])
        road = payload['streets'][0]
        self.assertEqual(road['geometry']['coordinates'], [[[-122.53, 45.45], [-122.52, 45.45]]])
        self.assertEqual([p[2] for p in road['address_points']], [1, 2])
        self.assertEqual(payload['polygon'][0], payload['polygon'][-1])
        self.assertEqual(payload['count_basis'], self.value['count_basis'])

    def test_points_away_from_centerline_are_preserved(self):
        self.value['streets'][0]['address_points'][0] = [-122.53, 45.452, 1]
        self.save()
        polygon = [[-122.531, 45.451], [-122.529, 45.451], [-122.529, 45.453], [-122.531, 45.453]]
        payload = self.call({'catalog_id': 'sample', 'polygon': polygon})
        self.assertEqual(payload['estimated_doors'], 1)
        self.assertEqual(payload['streets'][0]['geometry']['coordinates'], [])

    def test_empty_selection_is_explicit(self):
        polygon = [[-122.539, 45.441], [-122.538, 45.441], [-122.538, 45.442], [-122.539, 45.442]]
        payload = self.call({'catalog_id': 'sample', 'polygon': polygon})
        self.assertEqual((payload['streets'], payload['estimated_doors']), ([], 0))

    def test_access_gate_runs_before_catalog_read(self):
        for change in ('remote', 'anonymous', 'proxy', 'not_owner', 'hosted', 'demo'):
            request = self.request()
            if change == 'remote':
                request.META['REMOTE_ADDR'] = '203.0.113.1'
            elif change == 'anonymous':
                request.user.is_authenticated = False
            elif change == 'proxy':
                request.META['HTTP_X_FORWARDED_FOR'] = '127.0.0.1'
            elif change == 'not_owner':
                request.local_admin_access = False
            with self.subTest(change=change), override_settings(HOSTED=change == 'hosted', DEMO_MODE=change == 'demo'):
                with mock.patch.object(A, 'get_catalog', side_effect=AssertionError('must not open')):
                    self.assertEqual(A.query(request).status_code, 403)
        request = self.request('get')
        self.assertEqual(A.query(request).status_code, 405)
        self.assertEqual(A.catalogs(self.request()).status_code, 405)

    def test_bad_input_rejected_before_file_load(self):
        polygons = [None, [], [[0, 0]] * 3, [[False, 45], [-122, 45], [-122, 46]],
                    [[-181, 45], [-122, 45], [-122, 46]],
                    [[-122.53, 45.45], [-122.51, 45.46], [-122.53, 45.46], [-122.51, 45.45]],
                    [[-122.53, 45.45]] * (A.MAX_POLYGON_VERTICES + 1)]
        with mock.patch.object(A, 'get_catalog', side_effect=AssertionError('must not open')):
            for polygon in polygons:
                with self.subTest(polygon=polygon):
                    self.call({'catalog_id': 'sample', 'polygon': polygon}, 400)
            self.call({'catalog_id': 'sample', 'polygon': FULL, 'voters': []}, 400)
        self.call({'catalog_id': '../sample', 'polygon': FULL}, 400)
        self.call({'catalog_id': 'https://example.org/data', 'polygon': FULL}, 400)
        self.call({'catalog_id': 'missing', 'polygon': FULL}, 404)
        outside = deepcopy(FULL)
        outside[0][0] -= .01
        self.call({'catalog_id': 'sample', 'polygon': outside}, 400)

    def test_body_limits_duplicate_keys_and_nonfinite_json(self):
        for raw in ('{"catalog_id":"sample","catalog_id":"sample","polygon":[]}',
                    '{"catalog_id":"sample","polygon":[[NaN,45],[0,0],[0,1]]}', '[]', '{'):
            request = self.factory.post(QUERY, raw, content_type='application/json', REMOTE_ADDR='127.0.0.1')
            request.user = SimpleNamespace(is_authenticated=True)
            request.local_admin_access = True
            self.assertEqual(A.query(request).status_code, 400)
        request = self.request()
        request.META['CONTENT_LENGTH'] = str(A.MAX_BODY + 1)
        with mock.patch.object(A, 'get_catalog', side_effect=AssertionError('must not open')):
            self.assertEqual(A.query(request).status_code, 400)

    def test_catalog_integrity_errors_are_unavailable_not_partial(self):
        variants = []
        for change in ('duplicate_point', 'duplicate_street', 'private_field', 'bbox', 'point_outside', 'bad_version', 'too_many'):
            value = fixture()
            if change == 'duplicate_point':
                value['streets'][1]['address_points'] = [[-122.53, 45.46, 1]]
            elif change == 'duplicate_street':
                value['streets'][1]['id'] = 'alpha'
            elif change == 'private_field':
                value['streets'][0]['address_points'][0].append('a person')
            elif change == 'bbox':
                value['bbox'][2] = value['bbox'][0]
            elif change == 'point_outside':
                value['streets'][0]['address_points'][0][0] = -120
            elif change == 'bad_version':
                value['version'] = True
            else:
                value['streets'][0]['people'] = []
            variants.append((change, value))
        for change, value in variants:
            with self.subTest(change=change):
                self.value = value
                self.save()
                self.call(status=503)

    def test_file_metadata_invalidates_cache_and_missing_directory_is_empty(self):
        self.assertEqual(self.call()['estimated_doors'], 3)
        self.value['streets'][0]['address_points'].pop()
        self.save()
        self.assertEqual(self.call()['estimated_doors'], 2)
        self.path.unlink()
        self.call(status=404)
        self.directory.rmdir()
        result = A.catalogs(self.request('get', endpoint=CATALOGS))
        self.assertEqual(json.loads(result.content), {'catalogs': []})

    def test_symlink_outside_catalog_root_is_never_read(self):
        other = Path(self.temporary.name) / 'outside.json'
        other.write_text(json.dumps(fixture()), encoding='utf-8')
        self.path.unlink()
        try:
            self.path.symlink_to(other)
        except OSError:
            self.skipTest('Symlink creation unavailable on this host')
        with mock.patch.object(A, '_cached_catalog', side_effect=AssertionError('must not read')):
            self.call(status=503)

    def test_no_result_truncation(self):
        self.value['streets'][0]['address_points'] = [[-122.52, 45.45, i] for i in range(1501)]
        self.save()
        payload = self.call()
        self.assertEqual(payload['estimated_doors'], 1501)
        self.assertEqual(len(payload['streets'][0]['address_points']), 1501)
