"""Recorded opt-ins on invented walk-list rosters. No vendor fields or real records."""
from datetime import timedelta
import json
from pathlib import Path

from django.contrib.auth.models import User
from django.test import Client, override_settings
from django.utils import timezone

from . import access_testkit as K
from . import field_access as FA
from . import walk_address_views as A
from . import walk_opt_ins as O
from . import walk_views as V
from .models import AuditEvent, Disposition, WalkList, WalkOptInRecord
from .test_walk_api import WalkCase, voters_of, file_hashes, JSON

URL = '/api/v3/walk/opt-ins'


class WalkOptInTests(WalkCase):
    def setUp(self):
        super().setUp()
        self.query = {'streets': '%d:both' % self.street_id('Ridgeview Ct')}
        self.saved = self.make_list(self.query)
        self.key = self.saved['list']['key']
        self.person = voters_of(self.saved)[0]['person_id']

    def record(self, **statuses):
        return self.posted(URL, {'list': self.key, 'entries': [{'person_id': self.person, **statuses}]})

    def states(self, key=None):
        shown = self.json('/api/v3/walk/lists/' + (key or self.key))
        return {v['person_id']: v['opt_ins'] for v in voters_of(shown)}

    def test_unknown_is_the_default_and_is_distinct_from_opted_out(self):
        self.assertTrue(all(v['opt_ins'] == O.unknown() for v in voters_of(self.saved)))
        result = self.record(donor='opted_out')
        state = result['opt_ins'][self.person]
        self.assertEqual(result['saved'], 1)
        self.assertEqual(state['donor']['status'], 'opted_out')
        self.assertEqual(state['donor']['source'], 'recorded_manually')
        self.assertIsNotNone(state['donor']['recorded_at'])
        self.assertEqual(state['volunteer'], O.unknown()['volunteer'])
        self.assertEqual(self.states()[self.person], state)

    def test_kinds_append_independently_and_reset_to_unknown_keeps_provenance(self):
        self.record(donor='opted_in', volunteer='opted_out')
        before = self.states()[self.person]['volunteer']
        self.record(donor='unknown')
        state = self.states()[self.person]
        self.assertEqual(state['volunteer'], before)
        self.assertEqual(state['donor']['status'], 'unknown')
        self.assertEqual(state['donor']['source'], 'recorded_manually')
        self.assertIsNotNone(state['donor']['recorded_at'])
        self.assertEqual(WalkOptInRecord.objects.count(), 3)

    def test_latest_appended_record_wins_even_if_its_timestamp_is_earlier(self):
        self.record(donor='opted_in')
        record = WalkOptInRecord.objects.get()
        WalkOptInRecord.objects.create(walk_list=record.walk_list, person_id=record.person_id,
                                      voter_key=record.voter_key, kind='donor', status='opted_out',
                                      recorded_at=timezone.now() - timedelta(days=3))
        self.assertEqual(self.states()[self.person]['donor']['status'], 'opted_out')

    def test_saved_lists_refresh_live_status_without_changing_their_frozen_roster(self):
        other = self.make_list(self.query, 'Another list')
        frozen = WalkList.objects.get(key=self.key).payload
        self.record(volunteer='opted_in')
        self.assertEqual(self.states(other['list']['key'])[self.person]['volunteer']['status'], 'opted_in')
        self.assertEqual(WalkList.objects.get(key=self.key).payload, frozen)
        rebuilt = self.json('/api/v3/walk/list?streets=' + self.query['streets'])
        self.assertEqual(rebuilt['fingerprint'], self.saved['fingerprint'])
        self.assertEqual(rebuilt['totals'], self.saved['totals'])
        self.assertEqual([v['person_id'] for v in voters_of(rebuilt)], [v['person_id'] for v in voters_of(self.saved)])
        self.assertEqual([v['opt_ins']['volunteer']['status'] for v in voters_of(rebuilt) if v['person_id'] == self.person], ['opted_in'])

    def test_private_stable_key_follows_an_updated_person_id_without_being_exposed(self):
        self.record(donor='opted_in')
        record = WalkOptInRecord.objects.get()
        payload = {'runs': [{'doors': [{'voters': [{'person_id': 'new-synthetic-person-id'}]}]}]}
        O.attach(payload, {'new-synthetic-person-id': record.voter_key})
        self.assertEqual(voters_of(payload)[0]['opt_ins']['donor']['status'], 'opted_in')
        public = json.dumps(self.json('/api/v3/walk/lists/' + self.key))
        self.assertNotIn(record.voter_key, public)
        self.assertNotIn(V.PRIVATE, public)

    def test_dispositions_fresh_selection_and_source_files_are_unchanged(self):
        before_files = file_hashes(self.stores.directory)
        before = self.json('/api/v3/walk/list?streets=' + self.query['streets'] + '&fresh=1')
        self.record(donor='opted_in', volunteer='opted_in')
        after = self.json('/api/v3/walk/list?streets=' + self.query['streets'] + '&fresh=1')
        self.assertEqual(before_files, file_hashes(self.stores.directory))
        self.assertFalse(Disposition.objects.exists())
        self.assertEqual(before['fingerprint'], after['fingerprint'])
        self.assertEqual(before['totals'], after['totals'])
        self.assertEqual([v['person_id'] for v in voters_of(before)], [v['person_id'] for v in voters_of(after)])
        self.assertTrue(all(v['last'] is None for v in voters_of(after)))

    def test_reknock_overlays_explicit_opt_ins_without_using_them_to_select_people(self):
        self.record(volunteer='opted_in')
        self.posted('/api/v3/walk/dispositions', {'list': self.key, 'on': '2026-10-01',
                                                'entries': [{'person_id': self.person, 'code': 'NH'}]})
        again = self.posted('/api/v3/walk/lists', {'title': 'Again', 'from': self.key, 'reknock': True}, 201)
        self.assertEqual([v['person_id'] for v in voters_of(again)], [self.person])
        self.assertEqual(voters_of(again)[0]['opt_ins']['volunteer']['status'], 'opted_in')

    def test_imported_address_payloads_show_only_recorded_status_without_changing_membership(self):
        body = {'snapshot': A.snapshot(V.W.verify()), 'split_sides': True,
                'streets': [{'id': self.street_id('Ridgeview Ct'), 'zip': ''}]}
        before = self.posted('/api/v3/walk/address-list', body)['payload']
        self.record(donor='opted_in')
        after = self.posted('/api/v3/walk/address-list', body)['payload']
        self.assertEqual(before['fingerprint'], after['fingerprint'])
        self.assertEqual(before['totals'], after['totals'])
        self.assertEqual([v['person_id'] for v in voters_of(before)], [v['person_id'] for v in voters_of(after)])
        recorded = next(v['opt_ins'] for v in voters_of(after) if v['person_id'] == self.person)
        self.assertEqual(recorded['donor']['status'], 'opted_in')
        self.assertEqual(recorded['volunteer']['status'], 'unknown')

    def test_invalid_entries_refuse_the_entire_batch_without_records(self):
        invalid = [{}, {'person_id': self.person}, {'person_id': [] , 'donor': 'opted_in'},
                   {'person_id': self.person, 'donor': None}, {'person_id': self.person, 'donor': True},
                   {'person_id': self.person, 'donor': 1}, {'person_id': self.person, 'donor': 'likely'},
                   {'person_id': self.person, 'volunteer': ['opted_in']},
                   {'person_id': self.person, 'donor': 'opted_in', 'source': 'vendor'},
                   {'person_id': 'not-on-the-roster', 'donor': 'opted_in'}]
        valid = {'person_id': self.person, 'donor': 'opted_in'}
        for entry in invalid:
            self.error(self.post(URL, {'list': self.key, 'entries': [entry]}), 400)
            self.assertFalse(WalkOptInRecord.objects.exists(), repr(entry))
            self.error(self.post(URL, {'list': self.key, 'entries': [valid, entry]}), 400)
            self.assertFalse(WalkOptInRecord.objects.exists(), repr(entry))
        for body in ({}, {'list': self.key}, {'entries': [valid]}, {'list': self.key, 'entries': []},
                     {'list': self.key, 'entries': {}}, {'list': self.key, 'entries': [valid] * 201},
                     {'list': self.key, 'entries': [valid], 'extra': 1}):
            self.error(self.post(URL, body), 400)
        self.assertFalse(WalkOptInRecord.objects.exists())
        self.error(self.post(URL, {'list': '0123456789', 'entries': [valid]}), 404)

    def test_duplicates_are_refused_and_people_cannot_record_each_others_state(self):
        valid = {'person_id': self.person, 'donor': 'opted_in'}
        self.error(self.post(URL, {'list': self.key, 'entries': [valid, valid]}), 400)
        other = self.make_list({'streets': '%d:both' % self.street_id('Juniper Ln')})
        self.error(self.post(URL, {'list': other['list']['key'], 'entries': [valid]}), 400)
        self.assertFalse(WalkOptInRecord.objects.exists())

    def test_saved_reads_and_writes_do_not_need_the_source_store(self):
        empty = Path(self.temporary.name) / 'no-optin-source-stores'
        empty.mkdir()
        with override_settings(DATA_DIR=empty):
            self.record(donor='opted_in')
            self.assertEqual(self.states()[self.person]['donor']['status'], 'opted_in')

    def test_post_is_csrf_protected(self):
        made = Client(enforce_csrf_checks=True, HTTP_HOST='localhost')
        body = {'list': self.key, 'entries': [{'person_id': self.person, 'donor': 'opted_in'}]}
        self.assertEqual(self.post(URL, body, made).status_code, 403)
        self.assertFalse(WalkOptInRecord.objects.exists())

    def test_hosted_staff_must_have_both_the_matching_identity_and_field_role(self):
        FA.reset_rate_limits()
        staff = User.objects.create_user('field@example.test', password='correct horse battery staple')
        FA.grant(staff)
        manager = User.objects.create_user('manager@example.test', password='correct horse battery staple')
        FA.grant(manager, FA.MANAGER)
        body = {'list': self.key, 'entries': [{'person_id': self.person, 'donor': 'opted_in'}]}
        with K.hosted():
            self.assertEqual(self.post(URL, body, K.client('field@example.test')).status_code, 401)
            self.assertEqual(self.post(URL, body, K.client('other@example.test', login=staff)).status_code, 403)
            self.assertEqual(self.post(URL, body, K.client('manager@example.test', login=manager)).status_code, 403)
            self.assertFalse(WalkOptInRecord.objects.exists())
            made = K.client('field@example.test', login=staff)
            response = self.post(URL, body, made)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Cache-Control'], 'no-store, private')
            self.assertEqual(voters_of(made.get('/api/v3/walk/lists/' + self.key).json())[0]['opt_ins']['donor']['status'], 'opted_in')
        event = AuditEvent.objects.get(action='walk.opt_in.write')
        self.assertEqual(event.actor, 'field@example.test')
        self.assertEqual(set(event.detail), {'route', 'list', 'n', 'voter_keys'})
        self.assertEqual(event.detail['n'], 1)
