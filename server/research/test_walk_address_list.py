"""Imported route exports retain exact selection membership and every recorded voter."""
import json
import shutil
import sqlite3
import unittest
from contextlib import closing
from copy import copy
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.test import Client, override_settings

from . import access_testkit as K
from . import field_access as FA
from . import walk_address_views as A
from . import walk_store as W
from . import walk_views as V
from .models import AuditEvent
from .test_hosted import BOSS, STAFF, manager, staff
from .test_walk_api import WalkCase, LOOPBACK, doors_of, voters_of

URL = '/api/v3/walk/address-list'
LOCALITY_KEYS = {'city', 'state', 'zip', 'address', 'warnings'}


class ImportedWalkListTests(WalkCase):
    def request_body(self, streets=None, split_sides=True):
        return {'snapshot': A.snapshot(W.verify()), 'split_sides': split_sides,
                'streets': streets if streets is not None else [{'id': self.street_id('Cedar Heights Dr'), 'zip': ''}]}

    def build(self, streets=None, split_sides=True):
        return self.posted(URL, self.request_body(streets, split_sides))

    def edited_world(self, sql, args=()):
        directory = Path(self.temporary.name) / self._testMethodName
        shutil.copytree(self.stores.directory, directory)
        with closing(sqlite3.connect(directory / 'walk.sqlite3')) as db, db:
            db.execute(sql, args)
        return directory

    def test_full_route_matches_normal_people_doors_context_and_fields(self):
        street = self.street_id('Cedar Heights Dr')
        result = self.build()
        payload = result['payload']
        normal = self.json('/api/v3/walk/list?streets=%d:both' % street)
        self.assertEqual(set(result), {'district', 'snapshot', 'payload', 'selections'})
        self.assertEqual(result['district'], 'co-05')
        self.assertEqual(payload['schema'], 'walk-list-1')
        base_runs = [{**{key: value for key, value in run.items() if key != 'approach_m'}, 'doors': [{key: value for key, value in door.items() if key not in LOCALITY_KEYS | {'evidence'}}
                                      for door in run['doors']]} for run in payload['runs']]
        self.assertEqual(base_runs, normal['runs'])
        self.assertEqual(payload['totals'], normal['totals'])
        self.assertEqual(payload['context'], normal['context'])
        self.assertEqual(payload['window'], normal['window'])
        people = voters_of(payload)
        self.assertEqual({v['person_id'] for v in people}, self.people_on('Cedar Heights Dr'))
        self.assertTrue(all(v['name'] for v in people))
        self.assertTrue(any(v['phone'] for v in people))
        self.assertEqual(result['selections'], [{'id': street, 'zip': '', 'person_ids': sorted(v['person_id'] for v in people)}])
        self.assertEqual(payload['query']['snapshot'], result['snapshot'])
        self.assertRegex(payload['fingerprint'], r'^[a-f0-9]{64}$')

    def test_all_streets_conserve_every_person_and_recorded_door(self):
        with W.walk_store() as store:
            picks = [{'id': s.id, 'zip': ''} for s in store.directory().streets]
            expected_people = {row[0] for row in store.conn.execute('SELECT person_id FROM person WHERE stop_id IS NOT NULL')}
            expected_doors = store.conn.execute("SELECT COUNT(*) FROM (SELECT stop_id, COALESCE(unit,'') FROM person WHERE stop_id IS NOT NULL GROUP BY stop_id, COALESCE(unit,''), precinct)").fetchone()[0]
        payload = self.build(picks)['payload']
        people = [v['person_id'] for v in voters_of(payload)]
        self.assertEqual(set(people), expected_people)
        self.assertEqual(len(people), len(set(people)))
        self.assertEqual(payload['totals']['doors'], expected_doors)
        self.assertEqual(payload['totals']['voters'], len(expected_people))
        self.assertEqual(len(doors_of(payload)), expected_doors)

    def test_zip_membership_and_overlapping_selections_do_not_broaden_or_duplicate(self):
        street = self.street_id('Cedar Heights Dr')
        selected_person = sorted(self.people_on('Cedar Heights Dr'))[0]
        directory = self.edited_world("UPDATE person SET zip='80999-1234' WHERE person_id=?", [selected_person])
        with override_settings(DATA_DIR=directory):
            filtered = self.build([{'id': street, 'zip': '80999-9876'}])
            self.assertEqual([v['person_id'] for v in voters_of(filtered['payload'])], [selected_person])
            self.assertEqual(filtered['selections'], [{'id': street, 'zip': '80999', 'person_ids': [selected_person]}])
            result = self.build([{'id': street, 'zip': ''}, {'id': street, 'zip': '80999'}, {'id': street, 'zip': '80999'}])
        people = [v['person_id'] for v in voters_of(result['payload'])]
        self.assertEqual(set(people), self.people_on('Cedar Heights Dr'))
        self.assertEqual(len(people), len(set(people)))
        self.assertEqual(len(result['selections']), 2)
        self.assertEqual(result['selections'][1]['person_ids'], [selected_person])
        self.assertEqual(set(result['selections'][0]['person_ids']), set(people))

    def test_actual_door_locality_missing_house_and_conflicting_zip_warnings(self):
        street = self.street_id('Cedar Heights Dr')
        directory = self.edited_world("UPDATE stop SET house='' WHERE street_id=? AND house='1112'", [street])
        with closing(sqlite3.connect(directory / 'walk.sqlite3')) as db, db:
            person = db.execute("SELECT person_id FROM person WHERE stop_id=(SELECT id FROM stop WHERE street_id=? AND house='') LIMIT 1", [street]).fetchone()[0]
            db.execute("UPDATE person SET zip='80999' WHERE person_id=?", [person])
        with override_settings(DATA_DIR=directory):
            result = self.build()
            door = next(d for d in doors_of(result['payload']) if any(v['person_id'] == person for v in d['voters']))
            self.assertEqual((door['city'], door['state'], door['zip']), ('Colorado Springs', 'CO', ''))
            self.assertEqual(door['warnings'], ['No house number recorded.', 'Multiple ZIP codes recorded for this door; left blank.'])
            self.assertEqual(door['address'], 'Cedar Heights Dr, Colorado Springs, CO')
            filtered = self.build([{'id': street, 'zip': '80999'}])
            only = doors_of(filtered['payload'])[0]
            self.assertEqual((only['zip'], only['warnings']), ('80999', ['No house number recorded.']))
            self.assertTrue(only['address'].endswith('CO 80999'))
            original = self.posted('/api/v3/walk/address-doors', {'snapshot': result['snapshot'], 'streets': [{'id': street, 'zip': ''}]})
            missing = next(address for address in original['addresses'] if not address['house'])
            self.assertEqual({key: door[key] for key in LOCALITY_KEYS}, {key: missing[key] for key in LOCALITY_KEYS})

    def test_split_side_toggle_only_changes_grouping_and_not_membership(self):
        split, mixed = self.build()['payload'], self.build(split_sides=False)['payload']
        self.assertEqual({v['person_id'] for v in voters_of(split)}, {v['person_id'] for v in voters_of(mixed)})
        self.assertEqual(split['totals']['doors'], mixed['totals']['doors'])
        self.assertTrue({'even', 'odd'} <= {run['side'] for run in split['runs']})
        self.assertEqual({run['side'] for run in mixed['runs']}, {'both'})
        self.assertEqual(mixed['query']['split_sides'], '0')

    def test_imported_connector_distance_sums_to_total_without_changing_normal_shape(self):
        picks = [{'id': self.street_id(name), 'zip': ''} for name in ('Cedar Heights Dr', 'Juniper Ln')]
        payload = self.build(picks)['payload']
        self.assertTrue(any(run['approach_m'] > 0 for run in payload['runs']))
        self.assertEqual(round(sum(run['walk_m'] + run['approach_m'] for run in payload['runs']), 1), payload['totals']['walk_m'])
        normal = self.json('/api/v3/walk/list?streets=' + self.cedar_juniper())
        self.assertTrue(all('approach_m' not in run for run in normal['runs']))

    def test_missing_coordinates_are_preserved_without_changing_existing_get(self):
        street = self.street_id('Cedar Heights Dr')
        directory = self.edited_world("UPDATE stop SET lon=NULL, lat=NULL, span_id=NULL, pos_m=NULL WHERE street_id=? AND house='1132'", [street])
        with override_settings(DATA_DIR=directory):
            payload = self.build()['payload']
            legacy = self.json('/api/v3/walk/list?streets=%d:both' % street)
        door = next(d for d in doors_of(payload) if d['house'] == '1132')
        self.assertEqual((door['lon'], door['lat'], door['placed_by']), (None, None, 'unlocated'))
        self.assertEqual({v['person_id'] for v in voters_of(payload)}, self.people_on('Cedar Heights Dr'))
        self.assertEqual(payload['totals']['doors'], legacy['totals']['doors'])
        self.assertTrue(all(d['lon'] is not None and d['lat'] is not None for d in doors_of(legacy)))
        self.assertNotIn(0, payload['bbox'])

    def test_all_unlocated_retains_rows_and_omits_false_map_context(self):
        directory = self.edited_world('UPDATE stop SET lon=NULL, lat=NULL, span_id=NULL, pos_m=NULL')
        with override_settings(DATA_DIR=directory), mock.patch.object(V.C, 'attach') as context:
            payload = self.build()['payload']
        context.assert_not_called()
        self.assertNotIn('context', payload)
        self.assertEqual(payload['bbox'], [0.0, 0.0, 0.0, 0.0])
        self.assertTrue(all(d['lon'] is None and d['lat'] is None for d in doors_of(payload)))
        self.assertEqual({v['person_id'] for v in voters_of(payload)}, self.people_on('Cedar Heights Dr'))

    def test_history_is_the_same_as_normal_walk_list(self):
        street = self.street_id('Cedar Heights Dr')
        saved = self.make_list({'streets': '%d:both' % street})
        person = voters_of(saved)[0]['person_id']
        self.posted('/api/v3/walk/dispositions', {'list': saved['list']['key'], 'on': '2026-10-01',
                                               'entries': [{'person_id': person, 'code': 'NH'}]})
        found = next(v for v in voters_of(self.build()['payload']) if v['person_id'] == person)
        self.assertEqual(found['last'], {'code': 'NH', 'on': '2026-10-01', 'list': saved['list']['key']})

    def test_overflow_refuses_atomically_before_route_planning(self):
        body = self.request_body()
        with mock.patch.object(V, 'MAX_VOTERS', 3), mock.patch.object(V, 'plan_payload') as plan:
            result = self.posted(URL, body, 400)
        self.assertEqual(set(result), {'error'})
        self.assertIn('exceed 3 voters', result['error'])
        plan.assert_not_called()

    def test_invalid_missing_or_stale_selection_never_returns_partial_list(self):
        body = self.request_body()
        self.posted(URL, {**body, 'snapshot': '0' * 64}, 409)
        self.posted(URL, {**body, 'streets': [*body['streets'], {'id': 999999999, 'zip': ''}]}, 409)
        self.posted(URL, {**body, 'streets': [{'id': body['streets'][0]['id'], 'zip': '99999'}]}, 409)
        for changes in ({'snapshot': 'bad'}, {'split_sides': '0'}, {'split_sides': 1}, {'split_sides': None},
                        {'streets': []}, {'streets': [True]}, {'streets': [{'id': 1, 'zip': 'invalid'}]},
                        {'streets': [1] * 41}, {'extra': True}):
            self.posted(URL, {**body, **changes}, 400)
        self.posted(URL + '?unknown=1', body, 400)

    def test_snapshot_change_during_build_rejects_payload(self):
        body = self.request_body()
        info = W.verify()
        changed = copy(info)
        changed.stamp = (*info.stamp, (0, 0))
        with mock.patch.object(W, 'verify', side_effect=[info, changed]):
            result = self.posted(URL, body, 409)
        self.assertEqual(set(result), {'error'})

    def test_permissions_methods_csrf_and_cache_headers(self):
        body = self.request_body()
        response = self.post(URL, body)
        self.assertEqual(response['Cache-Control'], 'no-store, private')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(self.client.get(URL).status_code, 405)
        for client in (Client(HTTP_HOST='localhost', REMOTE_ADDR='192.0.2.1'),
                       Client(HTTP_HOST='localhost', HTTP_X_FORWARDED_FOR='127.0.0.1'),
                       Client(**LOOPBACK, enforce_csrf_checks=True)):
            self.assertEqual(self.post(URL, body, client).status_code, 403)
        for settings in ({'DEMO_MODE': True}, {'LOCAL_ADMIN_ACCESS': False}, {'HOSTED': True}):
            with override_settings(**settings):
                self.assertEqual(self.post(URL, body).status_code, 403)

    def test_hosted_manager_gate_rate_limit_and_route_only_audit(self):
        body = self.request_body()
        boss, field = manager(), staff()
        FA.reset_rate_limits()
        with K.hosted(FIELD_RATE_PER_MINUTE=1):
            denied = self.post(URL, body, K.client(STAFF, login=field))
            self.assertEqual(denied.status_code, 403)
            self.assertFalse(AuditEvent.objects.filter(action='walk.build.address_list').exists())
            made = K.client(BOSS, login=boss)
            self.assertEqual(self.post(URL, body, made).status_code, 200)
            self.assertEqual(self.post(URL, body, made).status_code, 429)
        event = AuditEvent.objects.get(action='walk.build.address_list')
        self.assertEqual(event.actor, BOSS)
        self.assertEqual(event.detail, {'route': URL})

    def test_local_groups_partition_all_people_and_exact_selection_memberships(self):
        with W.walk_store() as store:
            picks = [{'id': street.id, 'zip': ''} for street in store.directory().streets]
            expected_rows, expected_selections = A.selected_voters(store, [(pick['id'], '') for pick in picks])
        result = self.posted(URL, {**self.request_body(picks, False), 'local_groups': True})
        self.assertEqual(set(result), {'district', 'snapshot', 'groups', 'warnings', 'map_placement_omissions', 'map_placement_corrections'})
        people = [v['person_id'] for group in result['groups'] for v in voters_of(group['payload'])]
        self.assertCountEqual(people, [row['person_id'] for row in expected_rows])
        self.assertEqual(len(people), len(set(people)))
        for selection in expected_selections:
            grouped = [person for group in result['groups'] for item in group['selections']
                       if (item['id'], item['zip']) == (selection['id'], selection['zip']) for person in item['person_ids']]
            self.assertCountEqual(grouped, selection['person_ids'])
        for group in result['groups']:
            self.assertTrue(all(item['person_ids'] for item in group['selections']))
            self.assertTrue(all(run['side'] == 'both' for run in group['payload']['runs']))
            self.assertEqual(round(sum(run['walk_m'] + run['approach_m'] for run in group['payload']['runs']), 1),
                             group['payload']['totals']['walk_m'])

    def test_local_groups_keep_missing_house_people_and_original_inferred_location(self):
        street = self.street_id('Cedar Heights Dr')
        directory = self.edited_world("UPDATE stop SET house='',placed_by='interpolated' WHERE street_id=? AND house='1112'", [street])
        with override_settings(DATA_DIR=directory):
            legacy = self.build()['payload']
            result = self.posted(URL, {**self.request_body(), 'local_groups': True})
        old = next(d for d in doors_of(legacy) if d['house'] == '(no number)')
        found = [d for group in result['groups'] for d in doors_of(group['payload']) if d['house'] == '(no number)']
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['voters'], old['voters'])
        self.assertEqual((found[0]['lon'], found[0]['lat'], found[0]['placed_by']), (None, None, 'unlocated'))
        self.assertIn('No house number recorded.', found[0]['warnings'])
        self.assertEqual(found[0]['evidence'], {'version': 1, 'address_status': 'unresolved', 'location_precision': 'unlocated',
                                                'decision': 'review', 'reason_codes': ['inferred_no_house', 'no_house_number'],
                                                'source_placement': 'interpolated', 'original_house': None})
        omission = next(item for item in result['map_placement_omissions'] if item['house'] == '')
        self.assertEqual(omission['codes'], ['inferred_no_house'])
        self.assertEqual((omission['original']['lon'], omission['original']['lat']), (old['lon'], old['lat']))
        self.assertCountEqual(omission['person_ids'], [v['person_id'] for v in old['voters']])

    def test_ordinary_source_doors_are_exact_accepted_and_keep_source_placement(self):
        street = self.street_id('Cedar Heights Dr')
        result = self.posted(URL, {**self.request_body(), 'local_groups': True})
        doors = [d for group in result['groups'] for d in doors_of(group['payload'])]
        self.assertEqual({d['placed_by'] for d in doors}, {'geocode', 'interpolated'})
        for door in doors:
            precision = 'interpolated' if door['placed_by'] == 'interpolated' else 'source_point'
            self.assertEqual(door['evidence'], {'version': 1, 'address_status': 'source_exact', 'location_precision': precision,
                                                'decision': 'accepted', 'reason_codes': [],
                                                'source_placement': door['placed_by'], 'original_house': None})
        # The plain import path carries the same object, including honest missing coordinates.
        directory = self.edited_world("UPDATE stop SET lon=NULL,lat=NULL,span_id=NULL,pos_m=NULL WHERE street_id=? AND house='1132'", [street])
        with override_settings(DATA_DIR=directory):
            plain = doors_of(self.build()['payload'])
        missing = next(d for d in plain if d['house'] == '1132')
        self.assertEqual((missing['evidence']['address_status'], missing['evidence']['location_precision'], missing['evidence']['decision']),
                         ('source_exact', 'unlocated', 'review'))
        self.assertTrue(all(d['evidence']['decision'] == 'accepted' for d in plain if d['house'] != '1132'))

    def test_local_groups_overlap_keeps_strict_zip_membership_without_duplicate_people(self):
        street = self.street_id('Cedar Heights Dr')
        person = sorted(self.people_on('Cedar Heights Dr'))[0]
        directory = self.edited_world("UPDATE person SET zip='80999-1234' WHERE person_id=?", [person])
        picks = [{'id': street, 'zip': ''}, {'id': street, 'zip': '80999'}]
        with override_settings(DATA_DIR=directory):
            result = self.posted(URL, {**self.request_body(picks), 'local_groups': True})
        people = [v['person_id'] for group in result['groups'] for v in voters_of(group['payload'])]
        self.assertCountEqual(people, self.people_on('Cedar Heights Dr'))
        filtered = [p for group in result['groups'] for selection in group['selections'] if selection['zip'] == '80999' for p in selection['person_ids']]
        self.assertEqual(filtered, [person])

    def test_local_groups_all_unlocated_preserve_one_print_group_without_map(self):
        directory = self.edited_world('UPDATE stop SET lon=NULL,lat=NULL,span_id=NULL,pos_m=NULL')
        with override_settings(DATA_DIR=directory), mock.patch.object(V.C, 'attach') as context:
            result = self.posted(URL, {**self.request_body(), 'local_groups': True})
        context.assert_not_called()
        self.assertEqual(len(result['groups']), 1)
        self.assertEqual({v['person_id'] for v in voters_of(result['groups'][0]['payload'])}, self.people_on('Cedar Heights Dr'))
        self.assertEqual(result['groups'][0]['payload']['bbox'], [0.0, 0.0, 0.0, 0.0])

    def test_local_group_limits_and_flag_reject_before_returning_partial_payload(self):
        body = {**self.request_body(), 'local_groups': True}
        with mock.patch.object(A, 'MAX_LOCAL_GROUPS', 0), mock.patch.object(V, 'plan_payload') as plan:
            result = self.posted(URL, body, 400)
        plan.assert_not_called()
        self.assertIn('no records have been omitted', result['error'])
        for invalid in (1, 'true', None):
            self.posted(URL, {**body, 'local_groups': invalid}, 400)


class ImportLocationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript("CREATE TABLE road_span(id INTEGER,name_key TEXT); INSERT INTO road_span VALUES(1,'OAK RD'),(2,'ELM RD');")
        self.store = SimpleNamespace(conn=self.db, directory=lambda: SimpleNamespace(by_id={1: SimpleNamespace(key='OAK RD', display='Oak Rd')}))

    def row(self, number, x=0, **changes):
        return {'person_id': str(number), 'stop_id': number, 'street_id': 1, 'house': str(100+number),
                'unit': '', 'precinct': 'P1', 'lon': -72.88+x/83500, 'lat': 41.64,
                'placed_by': 'geocode', 'span_id': 1, 'pos_m': 20, **changes}

    def test_inferred_mismatched_road_retains_membership_original_location_and_no_route(self):
        original = [self.row(1, placed_by='nearest', span_id=2), self.row(2, placed_by='interpolated', span_id=2), self.row(3)]
        clean, omissions = A.import_locations(self.store, original)
        self.assertEqual([row['person_id'] for row in clean], ['1', '2', '3'])
        for row in clean[:2]:
            self.assertEqual((row['lon'], row['lat'], row['span_id'], row['pos_m']), (None, None, None, None))
        self.assertEqual(clean[2], original[2])
        self.assertTrue(all(row['lon'] is not None for row in original))
        self.assertEqual([item['original']['road_name'] for item in omissions], ['ELM RD', 'ELM RD'])
        self.assertEqual([item['codes'] for item in omissions], [['inferred_off_road']] * 2)
        self.assertEqual([item['street'] for item in omissions], ['Oak Rd'] * 2)
        unsupported = A.import_locations(self.store, [self.row(4, placed_by=None)])[1]
        self.assertEqual(unsupported[0]['codes'], ['unsupported_provenance'])
        self.assertEqual([item['original']['lon'] for item in omissions], [row['lon'] for row in original[:2]])

    def test_geocodes_are_not_discarded_for_distance_or_road_name_but_bad_coordinates_are(self):
        rows = [self.row(1, x=20000, span_id=2), self.row(2, placed_by='fuzzy', span_id=2),
                self.row(3, lon=float('inf')), self.row(4, lat=91), self.row(5, lon=None, lat=None, span_id=None)]
        clean, omissions = A.import_locations(self.store, rows)
        self.assertEqual(clean[:2], rows[:2])
        self.assertTrue(all(row['lon'] is None and row['span_id'] is None for row in clean[2:]))
        self.assertEqual(len(omissions), 2)
        self.assertEqual([item['codes'] for item in omissions], [['missing_coordinates']] * 2)
        json.dumps(omissions, allow_nan=False)

    def test_inferred_same_named_road_requires_house_number(self):
        rows = [self.row(1, placed_by='interpolated'), self.row(2, house='', placed_by='interpolated'),
                self.row(3, placed_by='nearest', span_id=None)]
        clean, omissions = A.import_locations(self.store, rows)
        self.assertEqual(clean[0], rows[0])
        self.assertTrue(all(row['lon'] is None for row in clean[1:]))
        self.assertIn('no house number', omissions[0]['reason'])

    def test_actual_clusters_split_distant_homonymous_street_and_keep_unlocated_households(self):
        rows = [self.row(1), self.row(2, x=100), self.row(3, x=5000), self.row(4, x=5100),
                self.row(5, lon=None, lat=None, span_id=None), self.row(6, stop_id=1, unit='A', precinct='P2')]
        groups = A.local_import_groups(rows)
        self.assertEqual(len(groups), 2)
        self.assertEqual([{row['person_id'] for row in group} for group in groups], [{'1','2','5','6'}, {'3','4'}])
        reversed_groups = A.local_import_groups(list(reversed(rows)))
        self.assertEqual([{row['person_id'] for row in group} for group in reversed_groups], [{'1','2','5','6'}, {'3','4'}])
        self.assertIsNone(next(row for group in groups for row in group if row['person_id']=='5')['lon'])

    def test_chain_extent_is_bounded_and_dense_same_cell_needs_no_pairwise_work(self):
        rows = [self.row(number+1, x=number*900) for number in range(8)]
        groups = A.local_import_groups(rows)
        self.assertGreater(len(groups), 1)
        self.assertCountEqual([r['person_id'] for group in groups for r in group], [r['person_id'] for r in rows])
        for group in groups:
            self.assertLess(max(r['lon'] for r in group)-min(r['lon'] for r in group), 3100/83500)
        with mock.patch.object(A, 'MAX_DISTANCE_CHECKS', 0):
            dense = A.local_import_groups([self.row(number, x=0) for number in range(50)])
            self.assertEqual(len(dense), 1)
            with self.assertRaises(V.Refused):
                A.local_import_groups([self.row(1), self.row(2, x=1000)])

