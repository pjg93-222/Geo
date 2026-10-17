"""Synthetic coverage and access checks for street-to-address imports."""
import os
import sqlite3
from copy import copy
from types import SimpleNamespace
from contextlib import closing
from unittest import mock

from django.test import Client, override_settings

from tools import districts as D

from . import walk_address_views as A
from . import walk_store as W
from .test_walk_api import WalkCase, LOOPBACK

MATCH = '/api/v3/walk/address-match'
DOORS = '/api/v3/walk/address-doors'
ADDRESS_KEYS = {'street_id', 'house', 'street', 'unit', 'city', 'state', 'zip', 'zip_filter', 'address', 'warnings'}
CANDIDATE_KEYS = {'id', 'label', 'city', 'state', 'zip', 'zip_filter', 'doors'}


class WalkAddressTests(WalkCase):
    def matched(self, *rows):
        return self.posted(MATCH, {'rows': list(rows)})

    def expand(self, match, picks=None, status=200):
        if picks is None:
            picks = [c['id'] for row in match['rows'] for c in row['candidates']]
        return self.posted(DOORS, {'snapshot': match['snapshot'], 'streets': picks}, status)

    def test_normalized_exact_names_match_but_ambiguous_localities_need_selection(self):
        result = self.matched({'street': ' cedar heights drive '}, {'street': 'Pine Street'},
                              {'street': 'Pine St', 'city': 'Fountain'}, {'street': 'Pine St', 'zip': '80817-1234'})
        self.assertEqual(result['district'], 'co-05')
        self.assertRegex(result['snapshot'], r'^[a-f0-9]{64}$')
        self.assertEqual([r['row'] for r in result['rows']], [1, 2, 3, 4])
        self.assertEqual([r['status'] for r in result['rows']], ['matched', 'pick', 'matched', 'matched'])
        self.assertEqual([c['city'] for c in result['rows'][1]['candidates']], ['Colorado Springs', 'Fountain'])
        self.assertEqual(result['rows'][3]['candidates'][0]['zip_filter'], '80817')
        for row in result['rows']:
            for candidate in row['candidates']:
                self.assertEqual(set(candidate), CANDIDATE_KEYS)

    def test_fuzzy_or_partial_names_always_need_review(self):
        result = self.matched({'street': 'Cedar Heights'}, {'street': 'Ceder Heights Dr'})
        self.assertEqual([r['status'] for r in result['rows']], ['pick', 'pick'])
        self.assertTrue(all(r['candidates'] for r in result['rows']))

    def test_unknown_or_conflicting_locality_never_falls_back_to_another_place(self):
        rows = [{'street': 'Pine St', 'city': 'Imaginary Town'}, {'street': 'Pine St', 'zip': '99999'},
                {'street': 'Pine St', 'state': 'CT'}, {'street': 'No Such Road'}]
        result = self.matched(*rows)
        self.assertTrue(all(r['status'] == 'none' and not r['candidates'] and r['reason'] for r in result['rows']))

    def test_numbered_addresses_cannot_expand_into_a_street(self):
        result = self.matched({'street': '1112 Cedar Heights Dr'}, {'street': '1350A Cedar Heights Dr'})
        self.assertTrue(all(r['status'] == 'none' and not r['candidates'] for r in result['rows']))
        with W.walk_store() as store:
            # Ordinal street names are not mistaken for a house number.
            result = A.resolve_row({'street': '1st Ave', 'city': '', 'state': '', 'zip': ''}, store.directory(), store)
        self.assertNotIn('house number', result['reason'])

    def test_expansion_covers_whole_doors_once_with_units_and_no_person_fields(self):
        match = self.matched({'street': 'Cedar Heights Dr'})
        candidate = match['rows'][0]['candidates'][0]
        result = self.expand(match, [candidate['id'], candidate['id']])
        addresses = result['addresses']
        expected = {('1112', ''), ('1132', ''), ('1136', ''), ('1152', ''), ('1119', ''), ('1139', ''),
                    ('1166', ''), ('1212', ''), ('1232', ''), ('1219', ''), ('1272', ''), ('1285', 'Apt A'),
                    ('1285', 'Apt B'), ('1316', ''), ('1350A', ''), ('1333', '')}
        self.assertEqual(candidate['doors'], len(expected))
        self.assertEqual(len(addresses), len(expected))
        self.assertEqual({(a['house'], a['unit']) for a in addresses}, expected)
        for address in addresses:
            self.assertEqual(set(address), ADDRESS_KEYS)
            self.assertEqual(address['street_id'], candidate['id'])
            self.assertEqual(address['city'], 'Colorado Springs')
            self.assertEqual(address['state'], 'CO')
            self.assertEqual(address['warnings'], [])
        self.assertIn('Apt A', next(a['address'] for a in addresses if a['unit'] == 'Apt A'))

    def test_all_streets_cover_all_fixture_doors_and_multiple_localities(self):
        match = self.matched(*({'street': name} for name in ('Cedar Heights Dr', 'Juniper Ln', 'Ridgeview Ct', 'Pine St')))
        addresses = self.expand(match)['addresses']
        self.assertEqual(len(addresses), 27)
        self.assertEqual(len({(a['street_id'], a['house'], a['unit']) for a in addresses}), 27)
        self.assertEqual({a['city'] for a in addresses}, {'Colorado Springs', 'Fountain'})

    def test_zip_filter_uses_actual_door_zip_and_is_carried_into_expansion(self):
        street = self.street_id('Cedar Heights Dr')
        path = self.stores.directory / 'walk.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            old = db.execute('SELECT person_id, zip FROM person WHERE street_id=?', [street]).fetchall()
            db.execute("UPDATE person SET zip='80999' WHERE stop_id=(SELECT id FROM stop WHERE street_id=? AND house='1112')", [street])
            db.commit()
        try:
            W.reset()
            match = self.matched({'street': 'Cedar Heights Dr', 'zip': '80999'})
            self.assertEqual(match['rows'][0]['status'], 'matched')
            candidate = match['rows'][0]['candidates'][0]
            self.assertEqual(candidate['doors'], 1)
            addresses = self.expand(match, [{'id': candidate['id'], 'zip': candidate['zip_filter']}])['addresses']
            self.assertEqual([(a['house'], a['zip'], a['zip_filter']) for a in addresses], [('1112', '80999', '80999')])
        finally:
            with closing(sqlite3.connect(path)) as db:
                db.executemany('UPDATE person SET zip=? WHERE person_id=?', [(code, person) for person, code in old])
                db.commit()
            W.reset()

    def test_overflow_refuses_whole_response_without_partial_addresses(self):
        match = self.matched({'street': 'Cedar Heights Dr'})
        with mock.patch.object(A, 'MAX_ADDRESS_DOORS', 3):
            result = self.expand(match, status=400)
        self.assertEqual(set(result), {'error'})
        self.assertIn('exceed 3 doors', result['error'])

    def test_old_snapshot_and_other_district_cannot_reuse_street_ids(self):
        match = self.matched({'street': 'Cedar Heights Dr'})
        path = self.stores.directory / 'walk.sqlite3'
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
        try:
            self.expand(match, status=409)
        finally:
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            W.reset()
        with override_settings(DISTRICT=D.get('ct-01')):
            self.expand(match, status=409)
        self.expand(match, [999999999], status=409)

    def test_repeated_input_rows_keep_their_indices(self):
        result = self.matched({'street': 'Pine St'}, {'street': 'Pine St'})
        self.assertEqual([r['row'] for r in result['rows']], [1, 2])
        self.assertEqual(result['rows'][0]['candidates'], result['rows'][1]['candidates'])

    def test_candidate_limit_stays_ambiguous_and_reports_more_results(self):
        with mock.patch.object(A, 'MAX_CANDIDATES', 1):
            result = self.matched({'street': 'Pine St'})['rows'][0]
        self.assertEqual(result['status'], 'pick')
        self.assertTrue(result['truncated'])
        self.assertEqual(len(result['candidates']), 1)

    def test_large_import_batches_household_queries_and_keeps_all_rows(self):
        rows = [A.input_row({'street': 'Pine St', 'zip': '80817' if i % 2 else ''}) for i in range(500)]
        with W.walk_store() as store:
            directory = store.directory()
            sql = []
            store.conn.set_trace_callback(sql.append)
            result = A.resolve_rows(rows, directory, store)
        self.assertEqual(len(result), 500)
        self.assertEqual(sum('GROUP BY' in statement.upper() for statement in sql), 2)
        self.assertEqual({r['status'] for r in result}, {'matched', 'pick'})

    def test_missing_house_and_conflicting_zip_are_preserved_with_warnings(self):
        street = self.street_id('Cedar Heights Dr')
        path = self.stores.directory / 'walk.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            stop = db.execute("SELECT id FROM stop WHERE street_id=? AND house='1112'", [street]).fetchone()[0]
            person, old_zip = db.execute('SELECT person_id, zip FROM person WHERE stop_id=? LIMIT 1', [stop]).fetchone()
            db.execute("UPDATE stop SET house='' WHERE id=?", [stop])
            db.execute("UPDATE person SET zip='80999' WHERE person_id=?", [person])
            db.commit()
        try:
            W.reset()
            match = self.matched({'street': 'Cedar Heights Dr'})
            addresses = self.expand(match)['addresses']
            self.assertEqual(len(addresses), 16)
            door = next(a for a in addresses if not a['house'])
            self.assertEqual(door['zip'], '')
            self.assertEqual(door['warnings'], ['No house number recorded.', 'Multiple ZIP codes recorded for this door; left blank.'])
            self.assertNotIn('1112', door['address'])
        finally:
            with closing(sqlite3.connect(path)) as db:
                db.execute("UPDATE stop SET house='1112' WHERE id=?", [stop])
                db.execute('UPDATE person SET zip=? WHERE person_id=?', [old_zip, person])
                db.commit()
            W.reset()

    def test_change_during_resolution_refuses_the_response(self):
        info = W.verify()
        changed = copy(info)
        changed.stamp = (*info.stamp, (0, 0))
        with mock.patch.object(W, 'verify', side_effect=[info, changed]):
            result = self.posted(MATCH, {'rows': [{'street': 'Pine St'}]}, 409)
        self.assertEqual(set(result), {'error'})

    def test_bounded_inputs_and_exact_fields(self):
        bad_match = [{}, {'rows': []}, {'rows': [{}]}, {'rows': [{'street': 1}]},
                     {'rows': [{'street': 'Pine St', 'address': '111 Pine St'}]},
                     {'rows': [{'street': 'Pine St', 'zip': 'bad'}]},
                     {'rows': [{'street': 'x' * 121}]}, {'rows': [{'street': 'Pine\nSt'}]},
                     {'rows': [{'street': 'Pine St'}] * 501}]
        for body in bad_match:
            self.posted(MATCH, body, 400)
        stamp = self.matched({'street': 'Pine St'})['snapshot']
        for picks in ([], [True], ['1'], [0], [-1], [1] * 41, [{'id': 1, 'extra': 2}], [{'id': 1, 'zip': 1}]):
            self.posted(DOORS, {'snapshot': stamp, 'streets': picks}, 400)
        self.posted(DOORS, {'snapshot': 'bad', 'streets': [1]}, 400)
        self.posted(MATCH + '?unknown=1', {'rows': [{'street': 'Pine St'}]}, 400)

    def test_access_methods_csrf_and_no_store_headers(self):
        match = self.matched({'street': 'Pine St'})
        requests = [(MATCH, {'rows': [{'street': 'Pine St'}]}),
                    (DOORS, {'snapshot': match['snapshot'], 'streets': [match['rows'][0]['candidates'][0]['id']]})]
        for url, body in requests:
            allowed = self.post(url, body)
            self.assertEqual(allowed['Cache-Control'], 'no-store, private')
            self.assertEqual(allowed['X-Content-Type-Options'], 'nosniff')
            self.assertEqual(self.client.get(url).status_code, 405)
            self.assertEqual(self.post(url, body, Client(HTTP_HOST='localhost', REMOTE_ADDR='192.0.2.1')).status_code, 403)
            self.assertEqual(self.post(url, body, Client(HTTP_HOST='localhost', HTTP_X_FORWARDED_FOR='127.0.0.1')).status_code, 403)
            self.assertEqual(self.post(url, body, Client(**LOOPBACK, enforce_csrf_checks=True)).status_code, 403)
            with override_settings(DEMO_MODE=True):
                self.assertEqual(self.post(url, body).status_code, 403)
            with override_settings(LOCAL_ADMIN_ACCESS=False):
                self.assertEqual(self.post(url, body).status_code, 403)


class WalkAddressProximityTests(WalkCase):
    @staticmethod
    def point(street, x, house='10', unit='', code='80920'):
        return (street * 100, house, unit, code, -104.8 + x, 38.8)

    def candidate(self, street, label):
        return {'id': street, 'label': label, 'city': '', 'state': 'CO', 'zip': '', 'zip_filter': '', 'doors': 1}

    def run_inference(self, locations, anchor_x=0, second_anchor=None, anchors=()):
        rows = [A.input_row({'street': 'Cedar Heights Dr'}), A.input_row({'street': 'Pine St'})]
        results = [dict(status='matched', method='exact', candidates=[self.candidate(1, 'Cedar Heights Dr')], truncated=False),
                   dict(status='pick', method='review', candidates=[self.candidate(2, 'Pine St'), self.candidate(3, 'Pine St')], truncated=False)]
        points = {1: [self.point(1, anchor_x)], 2: locations[0], 3: locations[1]}
        if second_anchor is not None:
            rows.append(A.input_row({'street': 'Juniper Ln'}))
            results.append(dict(status='matched', method='exact', candidates=[self.candidate(4, 'Juniper Ln')], truncated=False))
            points[4] = [self.point(4, second_anchor)]
        with mock.patch.object(A, 'geo_points', return_value=points):
            A.infer_proximity(rows, results, SimpleNamespace(by_key={}), None, anchors, [])
        return results[1]

    def test_geographic_nearest_wins_and_reports_metre_evidence(self):
        result = self.run_inference(([self.point(2, .001)], [self.point(3, .2)]))
        self.assertEqual((result['method'], result['status'], result['selected_id']), ('proximity', 'matched', 2))
        self.assertTrue(80 < result['proximity']['distance_m'] < 90)
        self.assertEqual(result['proximity']['runner_up_beyond_m'], 5000)
        self.assertEqual(result['proximity']['confidence'], 'clear')
        self.assertEqual(len(result['candidates']), 2)  # alternative remains available for an explicit override

    def test_nearest_uses_whole_street_footprint_not_a_centroid(self):
        result = self.run_inference(([self.point(2, .001), self.point(22, .4)], [self.point(3, .2)]))
        self.assertEqual(result['selected_id'], 2)

    def test_equal_distance_and_two_supported_neighborhoods_stay_review(self):
        tied = self.run_inference(([self.point(2, -.001)], [self.point(3, .001)]))
        self.assertEqual(tied['status'], 'pick')
        self.assertIn('too close', tied['proximity']['reason'])
        clusters = self.run_inference(([self.point(2, .0001)], [self.point(3, .101)]), second_anchor=.1)
        self.assertEqual(clusters['status'], 'pick')
        self.assertIn('neighborhoods', clusters['proximity']['reason'])

    def test_missing_coordinates_and_bounded_distance_budget_do_not_guess(self):
        missing = self.run_inference(([self.point(2, .001)], []))
        self.assertEqual(missing['status'], 'pick')
        self.assertIn('lacks reliable coordinates', missing['proximity']['reason'])
        with mock.patch.object(A, 'MAX_DISTANCE_CHECKS', 0):
            limited = self.run_inference(([self.point(2, .001)], [self.point(3, .2)]))
        self.assertEqual(limited['status'], 'pick')
        self.assertIn('limit', limited['proximity']['reason'])

    def test_inferred_choices_never_become_anchors_for_other_rows(self):
        rows = [A.input_row({'street': name}) for name in ('Cedar Heights Dr', 'Pine St', 'Oak St')]
        results = [dict(status='matched', method='exact', candidates=[self.candidate(1, 'Cedar Heights Dr')], truncated=False),
                   dict(status='pick', method='review', candidates=[self.candidate(2, 'Pine St'), self.candidate(3, 'Pine St')], truncated=False),
                   dict(status='pick', method='review', candidates=[self.candidate(4, 'Oak St'), self.candidate(5, 'Oak St')], truncated=False)]
        points = {i: [self.point(i, x)] for i, x in ((1, 0), (2, .01), (3, .2), (4, .03), (5, .3))}
        with mock.patch.object(A, 'geo_points', return_value=points):
            A.infer_proximity(rows, results, SimpleNamespace(by_key={}), None, [], [])
        self.assertEqual(results[1]['method'], 'proximity')
        self.assertEqual(results[2]['status'], 'pick')
        self.assertIn('within 2 km', results[2]['proximity']['reason'])

    def test_name_plausibility_preserves_numeric_and_directional_identity(self):
        for left, right in [('1st Ave', '2nd Ave'), ('N Oak St', 'S Oak St'), ('Oak St', 'Pine St')]:
            self.assertFalse(A.plausible_name(left, right), (left, right))
        self.assertTrue(A.plausible_name('Forest Hill Rd', 'Forest Hill Dr'))
        self.assertTrue(A.plausible_name('Ceder Heights Dr', 'Cedar Heights Dr'))

    def test_exact_address_anchor_is_narrowed_to_house_unit_and_locality(self):
        cedar, pine, fountain = self.street_id('Cedar Heights Dr'), self.street_id('Pine St'), self.street_id('Pine St', 'FOUNTAIN')
        with W.walk_store() as store:
            stop = store.conn.execute("SELECT id FROM stop WHERE street_id=? AND house='1285'", [cedar]).fetchone()[0]
        points = {cedar: [(stop, '1285', 'Apt A', '80919', -104.8, 38.8)],
                  pine: [self.point(pine, .001)], fountain: [self.point(fountain, .2)]}
        anchor = {'house': '1285', 'street': 'Cedar Heights Dr', 'unit': 'A', 'city': 'Colorado Springs', 'zip': '80919'}
        with mock.patch.object(A, 'geo_points', return_value=points):
            good = self.posted(MATCH, {'rows': [{'street': 'Pine St'}], 'proximity': True, 'anchors': [anchor]})
            for change in ({'house': '9999'}, {'unit': '3'}, {'zip': '99999'}, {'city': 'Fountain'}):
                bad = self.posted(MATCH, {'rows': [{'street': 'Pine St'}], 'proximity': True, 'anchors': [{**anchor, **change}]})
                self.assertEqual(bad['rows'][0]['status'], 'pick')
        self.assertEqual(good['rows'][0]['selected_id'], pine)

    def test_unlocated_duplicate_address_still_prevents_an_anchor(self):
        pine, fountain = self.street_id('Pine St'), self.street_id('Pine St', 'FOUNTAIN')
        with W.walk_store() as store:
            stop = store.conn.execute("SELECT id FROM stop WHERE street_id=? AND house='514'", [pine]).fetchone()[0]
        # Both cities contain 514 Pine St; only one has a reliable coordinate in this test.
        points = {pine: [(stop, '514', '', '80920', -104.8, 38.8)], fountain: []}
        with mock.patch.object(A, 'geo_points', return_value=points):
            result = self.posted(MATCH, {'rows': [{'street': 'Pine St'}], 'proximity': True,
                                         'anchors': [{'house': '514', 'street': 'Pine St'}]})
        self.assertEqual(result['rows'][0]['status'], 'pick')
        self.assertIn('No other exact', result['rows'][0]['proximity']['reason'])

    def test_filtered_out_exact_name_cannot_promote_an_approximate_name(self):
        row = A.input_row({'street': 'Pine St'})
        found = [SimpleNamespace(id=1, display='Pine St', city='FOUNTAIN', zip='80817'),
                 SimpleNamespace(id=2, display='Pine Rd', city='FOUNTAIN', zip='80817')]
        result = A.resolved(row, found, True, {'status':'none'}, {(1, ''):0, (2, ''):2})
        self.assertEqual(result['status'], 'pick')

    def test_search_cap_survives_zero_door_and_name_plausibility_filters(self):
        row = A.input_row({'street': 'Pine St'})
        found = [SimpleNamespace(id=i, display='Pine St', city='FOUNTAIN', zip='80817') for i in (1, 2)]
        with mock.patch.object(A, 'MAX_CANDIDATES', 1):
            result = A.resolved(row, found, True, {'status': 'none'}, {(1, ''): 1, (2, ''): 0})
            self.assertEqual(result['status'], 'pick')
            self.assertTrue(result['truncated'])
            directory = SimpleNamespace(streets=found, by_key={}, search=lambda *args: found, near=lambda *args: [])
            with mock.patch.object(A, 'plausible_name', side_effect=[True, False]):
                prepared, candidates, exact = A.find_candidates(row, directory, {}, {}, True)
            self.assertEqual(len(candidates), 1)
            self.assertTrue(prepared['truncated'])
            result = A.resolved(row, candidates, exact, prepared, {(1, ''): 1})
            self.assertEqual(result['status'], 'pick')
            self.assertTrue(result['truncated'])

    def test_no_anchor_and_uncertain_store_placement_remain_review(self):
        result = self.posted(MATCH, {'rows': [{'street': 'Pine St'}], 'proximity': True})
        self.assertEqual(result['rows'][0]['status'], 'pick')
        self.assertIn('No other exact', result['rows'][0]['proximity']['reason'])
        with W.walk_store() as store:
            ids = {s.id for s in store.directory().streets}
            points = A.geo_points(store, ids)
            recorded = {p[0] for values in points.values() for p in values}
            unreliable = {r[0] for r in store.conn.execute("SELECT id FROM stop WHERE placed_by NOT IN ('geocode','fuzzy')")}
            self.assertFalse(recorded & unreliable)

    def test_cross_batch_anchor_streets_require_current_snapshot(self):
        first = self.posted(MATCH, {'rows': [{'street': 'Cedar Heights Dr'}]})
        anchor = first['rows'][0]['candidates'][0]['id']
        body = {'rows': [{'street': 'Pine St'}], 'proximity': True, 'selected_streets': [{'id': anchor}]}
        self.posted(MATCH, body, 409)
        self.posted(MATCH, {**body, 'snapshot': '0' * 64}, 409)
        self.posted(MATCH, {**body, 'snapshot': first['snapshot']})
        for changes in ({'proximity': 1}, {'anchors': [{}]}, {'anchors': [dict(house='1', street='Oak St')] * 501},
                        {'selected_streets': None}, {'selected_streets': [1] * 5001}):
            self.posted(MATCH, {**body, 'snapshot': first['snapshot'], **changes}, 400)
