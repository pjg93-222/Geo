"""Synthetic road-overlay provenance, completeness, bounds and access checks."""
import math
from copy import copy
from unittest import mock

from django.test import Client, SimpleTestCase, override_settings

from tools import districts as D

from . import walk_address_views as A
from . import walk_image_views as I
from . import walk_image_alignment as G
from . import walk_store as W
from .test_walk_api import WalkCase, LOOPBACK

URL = '/api/v3/walk/image-context'
RESOLVE = '/api/v3/walk/image-resolve'


class WalkImageContextTests(WalkCase):
    def body(self, **values):
        return {'snapshot': A.snapshot(W.verify()), 'street_names': ['Cedar Heights Drive'], **values}

    def test_exact_name_road_geometry_and_reliable_points_have_no_person_fields(self):
        result = self.posted(URL, self.body(street_ids=[self.street_id('Cedar Heights Dr')]))
        self.assertEqual(result['district'], 'co-05')
        self.assertEqual(result['roads']['type'], 'FeatureCollection')
        self.assertTrue(result['roads']['features'])
        self.assertEqual(len(result['streets']), 1)
        self.assertEqual(set(result['streets'][0]), {'id', 'label', 'city', 'state', 'zip', 'reliable_points'})
        self.assertTrue(result['streets'][0]['reliable_points'])
        west, south, east, north = result['bounds']
        for feature in result['roads']['features']:
            self.assertEqual(feature['geometry']['type'], 'LineString')
            self.assertEqual(set(feature['properties']), {'name', 'name_key', 'street_ids', 'source'})
            self.assertEqual(feature['properties']['name_key'], 'CEDAR HTS DR')
            for lon, lat in feature['geometry']['coordinates']:
                self.assertTrue(west <= lon <= east and south <= lat <= north)

    def test_same_name_in_multiple_towns_keeps_all_alternatives(self):
        result = self.posted(URL, self.body(street_names=['Pine St']))
        self.assertEqual({s['city'] for s in result['streets']}, {'Colorado Springs', 'Fountain'})
        self.assertTrue(all(s['reliable_points'] for s in result['streets']))
        self.assertNotIn('selected_id', result)
        self.assertNotIn('method', result)

    def test_road_without_registered_doors_never_creates_a_street_selection(self):
        result = self.posted(URL, self.body(street_names=['Academy Boulevard', 'No Such Road']))
        self.assertEqual(result['road_only_names'], ['Academy Boulevard'])
        self.assertEqual(result['unmatched_names'], ['No Such Road'])
        self.assertEqual(result['streets'], [])
        self.assertTrue(result['roads']['features'])
        self.assertTrue(all(f['properties']['street_ids'] == [] for f in result['roads']['features']))

    def test_shared_junction_retains_both_road_labels(self):
        result = self.posted(URL, self.body(street_names=['Cedar Heights Dr', 'Juniper Ln']))
        self.assertTrue(any(set(j['labels']) == {'Cedar Heights Dr', 'Juniper Ln'} for j in result['junctions']))
        self.assertTrue(all(set(j) == {'id', 'lon', 'lat', 'labels'} for j in result['junctions']))

    def test_geometry_uses_only_real_roads_and_reliable_registration_placements(self):
        with W.walk_store() as store:
            ids = sorted(store.directory().by_id)
            expected = {}
            for street, lon, lat in store.conn.execute("SELECT street_id,lon,lat FROM stop WHERE placed_by IN ('geocode','fuzzy')"):
                if I.coordinate((lon, lat)) and (lon or lat):
                    expected.setdefault(street, set()).add((lon, lat))
            real = {r[0] for r in store.conn.execute('SELECT id FROM road_span WHERE virtual=0')}
        result = self.posted(URL, self.body(street_ids=ids, street_names=[]))
        self.assertTrue({f['id'] for f in result['roads']['features']} <= real)
        for street in result['streets']:
            self.assertEqual({tuple(p) for p in street['reliable_points']}, expected.get(street['id'], set()))

    def test_large_geometry_refuses_instead_of_silently_truncating(self):
        for limit in ('MAX_ROADS', 'MAX_VERTICES', 'MAX_STOP_POINTS'):
            with mock.patch.object(I, limit, 0):
                result = self.posted(URL, self.body(), 400)
                self.assertEqual(set(result), {'error'})

    def test_bounds_filter_homonyms_points_and_junctions_without_false_negatives(self):
        all_places = self.posted(URL, self.body(street_names=['Pine St']))
        street = next(s for s in all_places['streets'] if s['city'] == 'Colorado Springs')
        lon, lat = street['reliable_points'][0]
        bounds = [lon - .001, lat - .001, lon + .001, lat + .001]
        local = self.posted(URL, self.body(street_names=['Pine St'], bounds=bounds))
        self.assertEqual(local['bounds'], bounds)
        self.assertEqual({s['city'] for s in local['streets']}, {'Colorado Springs'})
        self.assertTrue(local['roads']['features'])
        self.assertTrue(all(I.intersects(f['geometry']['coordinates'], bounds) for f in local['roads']['features']))
        self.assertTrue(all(I.inside(p, bounds) for s in local['streets'] for p in s['reliable_points']))
        self.assertTrue(all(I.inside((j['lon'], j['lat']), bounds) for j in local['junctions']))
        self.assertTrue(I.intersects([[-2, 0], [2, 0]], [-1, -1, 1, 1]))
        self.assertFalse(I.intersects([[-2, 2], [2, 2]], [-1, -1, 1, 1]))
        self.assertFalse(I.intersects([[-2, 0], [-2, 2], [2, 2]], [-1, -1, 1, 1]))

    def test_bounds_reject_invalid_or_excessively_wide_regions(self):
        for bounds in ([0, 0, 1], [True, 0, 1, 1], [2, 0, 1, 1], [0, 2, 1, 1],
                       [-181, 0, -180, 1], [0, 90, 1, 91], [0, 0, 2, 2], [0, 0, float('inf'), 1]):
            self.posted(URL, self.body(bounds=bounds), 400)

    def test_snapshot_is_bound_to_district_and_rechecked_after_read(self):
        body = self.body()
        self.posted(URL, {**body, 'snapshot': '0' * 64}, 409)
        self.posted(URL, {**body, 'street_ids': [999999999]}, 409)
        with override_settings(DISTRICT=D.get('ct-01')):
            self.posted(URL, body, 409)
        info = W.verify()
        changed = copy(info)
        changed.stamp = (*info.stamp, (0, 0))
        with mock.patch.object(W, 'verify', side_effect=[info, changed]):
            self.posted(URL, body, 409)

    def test_input_caps_exact_names_and_no_address_broadening(self):
        for change in ({'snapshot': None}, {'street_ids': [True]}, {'street_ids': [1] * 201},
                       {'street_ids': None}, {'street_names': [1]}, {'street_names': ['Pine St'] * 101},
                       {'street_names': ['10 Pine St']}, {'street_names': ['Pine\nSt']},
                       {'street_names': []}, {'extra': True}):
            self.posted(URL, self.body(**change), 400)
        result = self.posted(URL, self.body(street_names=['Ceder Heights Dr']))
        self.assertEqual(result['roads']['features'], [])
        self.assertIsNone(result['bounds'])
        self.assertEqual(result['unmatched_names'], ['Ceder Heights Dr'])

    def test_local_owner_post_csrf_and_private_headers(self):
        body = self.body()
        allowed = self.post(URL, body)
        self.assertEqual(allowed['Cache-Control'], 'no-store, private')
        self.assertEqual(allowed['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(self.client.get(URL).status_code, 405)
        for client in (Client(HTTP_HOST='localhost', REMOTE_ADDR='192.0.2.1'),
                       Client(HTTP_HOST='localhost', HTTP_X_FORWARDED_FOR='127.0.0.1'),
                       Client(**LOOPBACK, enforce_csrf_checks=True)):
            self.assertEqual(self.post(URL, body, client).status_code, 403)
        for changes in ({'DEMO_MODE': True}, {'LOCAL_ADMIN_ACCESS': False}, {'HOSTED': True}):
            with override_settings(**changes):
                self.assertEqual(self.post(URL, body).status_code, 403)

    def resolve_body(self, **changes):
        return {'snapshot': A.snapshot(W.verify()), 'street_ids': [self.street_id('Cedar Heights Dr')],
                'image': {'width': 1000, 'height': 800},
                'labels': [{'text': 'Cedar Heights Dr', 'x': 100, 'y': 100, 'width': 100, 'height': 20, 'confidence': 90}],
                **changes}

    def test_image_resolve_validates_snapshot_shapes_and_insufficient_evidence(self):
        good = self.resolve_body()
        result = self.posted(RESOLVE, good)
        self.assertEqual(result['status'], 'review')
        self.assertEqual(result['resolutions'], [])
        self.assertEqual(result['method'], 'image-layout')
        for change in ({'snapshot': None}, {'street_ids': []}, {'street_ids': [1] * 41},
                       {'image': {'width': 1000, 'height': float('inf')}}, {'image': {'width': 0, 'height': 800}},
                       {'labels': [{}]}, {'labels': []}, {'labels': good['labels'] * 81},
                       {'labels': [{**good['labels'][0], 'x': 999}]},
                       {'labels': [{**good['labels'][0], 'confidence': 101}]}):
            self.posted(RESOLVE, {**good, **change}, 400)
        self.posted(RESOLVE, {**good, 'snapshot': '0' * 64}, 409)
        self.posted(RESOLVE, {**good, 'street_ids': [999999999]}, 409)

    def test_image_resolve_has_same_private_owner_and_csrf_boundary(self):
        body = self.resolve_body()
        self.assertEqual(self.post(RESOLVE, body)['Cache-Control'], 'no-store, private')
        self.assertEqual(self.client.get(RESOLVE).status_code, 405)
        for client in (Client(HTTP_HOST='localhost', REMOTE_ADDR='192.0.2.1'),
                       Client(HTTP_HOST='localhost', HTTP_X_FORWARDED_FOR='127.0.0.1'),
                       Client(**LOOPBACK, enforce_csrf_checks=True)):
            self.assertEqual(self.post(RESOLVE, body, client).status_code, 403)
        for change in ({'HOSTED': True}, {'LOCAL_ADMIN_ACCESS': False}, {'DEMO_MODE': True}):
            with override_settings(**change):
                self.assertEqual(self.post(RESOLVE, body).status_code, 403)

    def test_requested_streets_never_supply_circular_fitting_evidence(self):
        result = self.posted(RESOLVE, self.resolve_body(requests=[{'street': name} for name in
                           ('Cedar Heights Dr', 'Juniper Ln', 'Pine St', 'Ridgeview Ct')]))
        self.assertEqual(result['status'], 'review')
        self.assertEqual(result['resolutions'], [])
        self.posted(RESOLVE, self.resolve_body(requests=[{'street': 'Pine St'}] * 101), 400)

    def test_surrounding_roads_narrow_homonyms_but_respect_locality_and_missing_proof(self):
        with W.walk_store() as store:
            data = I.context(store, [], {I.N.name_key(n): n for n in ('Pine St', 'Academy Blvd')})
            street = next(s for s in data['streets'] if s['city'] == 'Colorado Springs')
            x, y = G.mercator(street['reliable_points'][0])
            fit = {'x': [1, 0, x - 200], 'y': [0, -1, y + 150]}
            image = {'width': 400, 'height': 300}
            requests = [A.input_row(row) for row in [{'street': 'Pine St'}, {'street': 'Academy Blvd'},
                        {'street': 'Pine St', 'city': 'Fountain'}, {'street': 'Pine St', 'state': 'CT'},
                        {'street': 'Pine St', 'zip': '80817'}]]
            result = I.surrounding_resolutions(store, requests, data['roads']['features'], fit, image)
            self.assertEqual(result[0]['street_id'], street['id'])
            self.assertEqual(result[0]['method'], 'image-surroundings')
            self.assertEqual(result[1]['status'], 'road_only')
            self.assertTrue(all('street_id' not in r for r in result[2:]))
            # Losing a rival's road association must not turn it into proof of absence.
            fountain = next(s['id'] for s in data['streets'] if s['city'] == 'Fountain')
            incomplete = [r for r in data['roads']['features'] if fountain not in r['properties']['street_ids']]
            missing = I.surrounding_resolutions(store, requests[:1], incomplete, fit, image)
            self.assertEqual(missing[0]['status'], 'review')
            self.assertIn('cannot be ruled out', missing[0]['reason'])
            label = {'row': 1, 'text': 'Pine St', 'key': I.N.name_key('Pine St')}
            span = next(r['id'] for r in incomplete if street['id'] in r['properties']['street_ids'])
            direct = I.image_resolutions(store, [label], [(label, 0, (x, y), span)], incomplete, [])
            self.assertEqual(direct[0]['status'], 'review')
            self.assertIn('cannot be ruled out', direct[0]['reason'])

    def test_one_percent_ocr_confidence_is_not_promoted_to_a_certainty(self):
        body = self.resolve_body()
        body['labels'][0]['confidence'] = 1
        _, _, _, labels, _ = I.resolve_inputs(body)
        self.assertEqual(labels[0]['confidence'], .01)


class ImageLayoutFitTests(SimpleTestCase):
    def world(self, offset=0, road_start=0):
        points = [(150, 100), (700, 160), (250, 550), (750, 650), (480, 350)]
        transform = (1.1, .12, -8100000 + offset, 5100000)
        labels, roads = [], []
        for i, (x, y) in enumerate(points, 1):
            key = 'ROAD %d' % i
            labels.append({'row': i, 'text': key, 'key': key, 'point': (x, y), 'confidence': .95})
            # Short alternating road directions constrain both location and rotation.
            ends = [(x - 80, y), (x + 80, y)] if i % 2 else [(x, y - 80), (x, y + 80)]
            roads.append({'id': road_start + i, 'properties': {'name_key': key, 'street_ids': [1] if i == 1 else []},
                          'geometry': {'coordinates': [list(G.geographic(G.apply(transform, p))) for p in ends]}})
        return labels, roads, transform

    def test_layout_uses_relative_road_positions_and_returns_an_estimate(self):
        labels, roads, expected = self.world()
        result = G.fit_layout(labels, roads, [1], 1000, 800)
        self.assertEqual(result['status'], 'matched', result)
        self.assertGreaterEqual(result['distinct_labels'], 4)
        self.assertFalse(result['fit']['verified'])
        self.assertEqual(result['fit']['projection'], 'EPSG:3857')
        fit = result['fit']
        actual = (fit['x'][0] * 500 + fit['x'][1] * 400 + fit['x'][2], fit['y'][0] * 500 + fit['y'][1] * 400 + fit['y'][2])
        self.assertLess(math.dist(actual, G.apply(expected, (500, 400))), 50)

    def test_missing_anchors_insufficient_labels_and_work_budget_stay_review(self):
        labels, roads, _ = self.world()
        self.assertEqual(G.fit_layout(labels, roads, [999], 1000, 800)['status'], 'review')
        self.assertEqual(G.fit_layout(labels[:3], roads, [1], 1000, 800)['status'], 'review')
        for limit in ('MAX_CHECKS', 'MAX_HYPOTHESES'):
            with mock.patch.object(G, limit, 0):
                result = G.fit_layout(labels, roads, [1], 1000, 800)
                self.assertEqual(result['status'], 'review')

    def test_equally_fitting_distant_neighborhoods_are_not_arbitrarily_chosen(self):
        labels, roads, _ = self.world()
        _, other, _ = self.world(3500, 10)
        result = G.fit_layout(labels, roads + other, [1], 1000, 800)
        self.assertEqual(result['status'], 'review', result)

    def test_contradictory_trusted_anchor_cannot_be_discarded_as_an_outlier(self):
        labels, roads, _ = self.world()
        _, other, _ = self.world(10000, 10)
        roads[1] = other[1]
        roads[1]['properties']['street_ids'] = [2]
        result = G.fit_layout(labels, roads, [1, 2], 1000, 800)
        self.assertEqual(result['status'], 'review', result)

    def test_two_trusted_same_name_streets_in_different_places_remain_distinct(self):
        labels, roads, _ = self.world()
        _, other, _ = self.world(3500, 10)
        other[0]['properties']['street_ids'] = [2]
        result = G.fit_layout(labels, roads + other, [1, 2], 1000, 800)
        self.assertEqual(result['status'], 'review', result)

    def test_collinear_labels_do_not_establish_an_image_plane(self):
        labels, roads, _ = self.world()
        for label in labels:
            label['point'] = label['point'][0], 400
        result = G.fit_layout(labels, roads, [1], 1000, 800)
        self.assertEqual(result['status'], 'review', result)

    def test_road_distance_uses_the_rotated_image_footprint(self):
        transform = (1.1, .4, -8100000, 5100000)
        aa, bb, cc, ff = transform
        fit = {'x': [aa, bb, cc], 'y': [bb, -aa, ff]}
        image = {'width': 1000, 'height': 800}
        inside = [G.geographic(G.apply(transform, p)) for p in ((-100, 400), (1100, 400))]
        outside = [G.geographic(G.apply(transform, p)) for p in ((100, -100), (900, -100))]
        self.assertEqual(I.footprint_distance(inside, fit, image), 0)
        expected = 100 * math.hypot(aa, bb) * math.cos(math.radians(outside[0][1]))
        self.assertAlmostEqual(I.footprint_distance(outside, fit, image), expected, places=4)
