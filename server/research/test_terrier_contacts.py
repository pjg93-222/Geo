"""Contact/workspace contracts against small, explicitly synthetic SQLite stores."""
import csv
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, override_settings

from .terrier_contacts import _cached_coverage, contacts, export_contacts
from .terrier_workspace import workspace


@override_settings(LOCAL_ADMIN_ACCESS=True, HOSTED=False, DEMO_MODE=False)
class TerrierSourceContactsTests(SimpleTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.override = override_settings(DATA_DIR=self.directory)
        self.override.enable()
        self.factory = RequestFactory()
        self.source = 'a' * 64
        self.area_sha = 'b' * 64
        self.people = []
        with closing(sqlite3.connect(self.directory / 'contacts.sqlite3')) as connection, connection:
            connection.executescript('''CREATE TABLE metadata (key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE contacts (id TEXT PRIMARY KEY,name TEXT,email TEXT,phone TEXT,wireless TEXT,
                    registration TEXT,residential TEXT);
                CREATE INDEX contacts_name ON contacts(name COLLATE NOCASE,id);''')
            for number in range(55):
                ident = 'p_' + format(number, '064x')
                self.people.append(ident)
                connection.execute('INSERT INTO contacts VALUES (?,?,?,?,?,?,?)', (
                    ident, 'SENTINEL %02d' % number, 'sentinel%02d@example.invalid' % number if number % 2 == 0 else '',
                    '(202) 555-0100' if number == 1 else '', '303-555-0101' if number == 3 else None,
                    'REGISTRATION SENTINEL %02d' % number, 'RESIDENTIAL SENTINEL %02d' % number))
            self.put_meta(connection, {'schema': 'contacts-v1', 'source_sha256': self.source})
        with closing(sqlite3.connect(self.directory / 'scores.sqlite3')) as connection, connection:
            connection.executescript('''CREATE TABLE metadata (key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE person (person_id TEXT PRIMARY KEY,hh INTEGER,reg_block TEXT);
                CREATE TABLE household (hh INTEGER PRIMARY KEY);''')
            for number, ident in enumerate(self.people[:-1]):
                connection.execute('INSERT INTO person VALUES (?,?,?)', (ident, number // 2, '080410001001001'))
            connection.executemany('INSERT INTO household VALUES (?)', [(number,) for number in range(27)])
            self.put_meta(connection, {'schema': 'scores-v1', 'contacts_source_sha256': self.source,
                'w1': {'status': 'verified'}, 'n_people': 54, 'n_households': 27,
                'area_table_sha256': self.area_sha, 'field_catalog': [
                    {'name': 'sci_charitable_donor', 'group': 'soi', 'label': 'Charitable donor', 'kind': 'binary', 'points': 11.25},
                    {'name': 'svi_party_registration', 'group': 'lean', 'label': 'Party registration', 'kind': 'categorical_map', 'points': 50}],
                'built_utc': '2026-10-01T00:00:00Z'})
        with closing(sqlite3.connect(self.directory / 'street.sqlite3')) as connection, connection:
            connection.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY,value TEXT)')
            self.put_meta(connection, {'schema_version': '1', 'source_sha256': self.source,
                                      'bases': {'registration': {'households': 20, 'people': 40},
                                                'residential': {'households': 19, 'people': 39}}})
        self.snapshot = SimpleNamespace(key='source-snapshot', manifest={
            'source_sha256': self.source, 'source_name': 'sentinel-source.csv', 'source_date': '2026-09-04',
            'row_framework': {'area_table_sha256': self.area_sha}, 'geography': {'block_vintage': 'Stored blocks'},
            'geometry_method': {'precinct': 'Stored block dissolve approximation'}})

    def tearDown(self):
        _cached_coverage.cache_clear()
        self.override.disable()
        self.temporary.cleanup()

    @staticmethod
    def put_meta(connection, values):
        connection.executemany('INSERT OR REPLACE INTO metadata VALUES (?,?)',
                               [(key, json.dumps(value)) for key, value in values.items()])

    def request(self, function=contacts, values=None, **options):
        request = self.factory.get('/api/v3/contacts', values or {}, REMOTE_ADDR=options.pop('remote', '127.0.0.1'), **options)
        request.user = SimpleNamespace(is_authenticated=True)
        request.local_admin_access = True
        return function(request)

    def decoded(self, function=contacts, values=None):
        result = self.request(function, values)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result['Cache-Control'], 'no-store, private')
        return json.loads(result.content)

    def test_pages_are_one_based_and_missing_joins_stay_missing(self):
        first = self.decoded()
        self.assertEqual((first['total'], first['page'], first['pages'], first['first'], first['last']), (55, 1, 2, 1, 50))
        self.assertEqual(first['rows'][0]['id'], self.people[0])
        second = self.decoded(values={'page': 999})
        self.assertEqual((second['page'], second['first'], second['last']), (2, 51, 55))
        self.assertEqual(second['rows'][-1]['id'], self.people[-1])
        self.assertIsNone(second['rows'][-1]['household'])
        self.assertIsNone(second['rows'][-1]['block'])
        self.assertIsNone(first['rows'][1]['email'])

    def test_channel_counts_and_filters_are_computed_from_source_values(self):
        self.assertEqual(self.decoded()['coverage'], {'records': 55, 'email': 28, 'phone': 1, 'wireless': 1, 'withoutChannel': 25})
        self.assertEqual(self.decoded(values={'channel': 'email'})['total'], 28)
        self.assertEqual(self.decoded(values={'channel': 'phone'})['total'], 2)
        self.assertEqual(self.decoded(values={'channel': 'none'})['total'], 25)

    def test_search_address_basis_exact_id_and_formatted_phone(self):
        self.assertEqual(self.decoded(values={'q': 'REGISTRATION SENTINEL 01'})['total'], 1)
        self.assertEqual(self.decoded(values={'q': 'REGISTRATION SENTINEL 01', 'basis': 'residential'})['total'], 0)
        self.assertEqual(self.decoded(values={'q': 'RESIDENTIAL SENTINEL 01', 'basis': 'residential'})['total'], 1)
        self.assertEqual(self.decoded(values={'q': self.people[8]})['rows'][0]['id'], self.people[8])
        self.assertEqual(self.decoded(values={'q': '2025550100'})['total'], 1)
        self.assertEqual(self.decoded(values={'q': '(303) 555 0101'})['total'], 1)
        self.assertEqual(self.decoded(values={'q': 'sentinel08@example.invalid'})['total'], 1)

    def test_no_match_and_wildcards_do_not_broaden_search(self):
        for query in ('NO MATCH', '%', '__', "' OR 1=1 --"):
            result = self.decoded(values={'q': query})
            self.assertEqual((result['rows'], result['total'], result['first'], result['last']), ([], 0, 0, 0))

    def test_export_uses_all_matching_rows_and_selected_address(self):
        result = self.decoded(export_contacts, {'channel': 'email', 'basis': 'residential', 'page': 2})
        self.assertEqual(result['count'], 28)
        rows = list(csv.reader(io.StringIO(result['csv'].removeprefix('\ufeff'))))
        self.assertEqual(len(rows), 29)
        self.assertEqual(rows[0][-2:], ['Residential address', 'Registration census block'])
        self.assertTrue(all(row[-2].startswith('RESIDENTIAL SENTINEL') for row in rows[1:]))
        self.assertEqual(rows[1][0], self.people[0])

    def test_export_neutralizes_spreadsheet_formulas_without_changing_the_store(self):
        with closing(sqlite3.connect(self.directory / 'contacts.sqlite3')) as connection, connection:
            connection.execute('UPDATE contacts SET name=? WHERE id=?', ('=1+1', self.people[0]))
        result = self.decoded(export_contacts, {'q': self.people[0]})
        self.assertIn("'=1+1", result['csv'])
        self.assertEqual(self.decoded(values={'q': self.people[0]})['rows'][0]['name'], '=1+1')

    def test_invalid_duplicate_and_unknown_parameters(self):
        for values in ({'page': 0}, {'page': '1.5'}, {'page': 'nan'}, {'basis': 'other'},
                       {'channel': 'other'}, {'q': 'x' * 161}, {'extra': 'x'}, {'q': ['one', 'two']}):
            self.assertEqual(self.request(values=values).status_code, 400)

    def test_authentication_local_access_demo_and_methods(self):
        self.assertEqual(self.request(remote='192.0.2.2').status_code, 403)
        self.assertEqual(self.request(HTTP_X_FORWARDED_FOR='127.0.0.1').status_code, 403)
        with override_settings(DEMO_MODE=True):
            self.assertEqual(self.request().status_code, 403)
            self.assertEqual(self.request(export_contacts).status_code, 403)
        self.assertEqual(self.request(values={'demo': 1}).status_code, 403)
        request = self.factory.post('/api/v3/contacts', REMOTE_ADDR='127.0.0.1')
        request.user = SimpleNamespace(is_authenticated=True)
        request.local_admin_access = True
        self.assertEqual(contacts(request).status_code, 405)
        request.method = 'GET'
        request.user.is_authenticated = False
        self.assertEqual(contacts(request).status_code, 403)

    def test_source_mismatch_and_unverified_crosswalk_fail_closed(self):
        for name, key in (('contacts', 'source_sha256'), ('street', 'source_sha256'), ('scores', 'contacts_source_sha256')):
            with closing(sqlite3.connect(self.directory / (name + '.sqlite3'))) as connection, connection:
                self.put_meta(connection, {key: 'c' * 64})
            self.assertEqual(self.request().status_code, 409)
            self.assertEqual(self.request(export_contacts).status_code, 409)
            with closing(sqlite3.connect(self.directory / (name + '.sqlite3'))) as connection, connection:
                self.put_meta(connection, {key: self.source})
        with closing(sqlite3.connect(self.directory / 'scores.sqlite3')) as connection, connection:
            self.put_meta(connection, {'w1': {'status': 'fixture'}})
        self.assertEqual(self.request().status_code, 409)

    def test_missing_database_fails_without_creating_it(self):
        path = self.directory / 'street.sqlite3'
        path.unlink()
        self.assertEqual(self.request().status_code, 503)
        self.assertFalse(path.exists())

    def test_requests_leave_store_hashes_unchanged(self):
        paths = list(self.directory.glob('*.sqlite3'))
        before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        self.decoded()
        self.decoded(export_contacts, {'channel': 'none'})
        after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        self.assertEqual(before, after)

    def test_workspace_uses_store_counts_catalog_and_snapshot_provenance(self):
        with patch('server.research.terrier_workspace.Snapshot') as snapshots:
            snapshots.objects.filter.return_value.first.return_value = self.snapshot
            snapshots.objects.all.return_value = [self.snapshot]
            result = self.decoded(workspace)
        self.assertEqual((result['people'], result['households'], result['contacts']['records']), (54, 27, 55))
        self.assertEqual((result['sourceName'], result['sourceDate']), ('sentinel-source.csv', '2026-09-04'))
        self.assertEqual(result['fields'][0]['w'], .1125)
        self.assertEqual(result['fields'][1]['w'], 0)
        self.assertEqual(result['fields'][1]['kind'], 'party')
        self.assertEqual(result['availableSnapshots'], 1)
        precinct = next(row for row in result['layers'] if row['key'] == 'precinct')
        self.assertEqual(precinct['detail'], 'Stored block dissolve approximation')
        self.assertEqual(result['locationCoverage']['registration']['people'], 40)

    def test_workspace_rejects_snapshot_source_or_area_mismatch(self):
        with patch('server.research.terrier_workspace.Snapshot') as snapshots:
            snapshots.objects.filter.return_value.first.return_value = self.snapshot
            for key, value in (('source_sha256', 'd' * 64), ('row_framework', {'area_table_sha256': 'd' * 64})):
                original = self.snapshot.manifest[key]
                self.snapshot.manifest[key] = value
                self.assertEqual(self.request(workspace).status_code, 409)
                self.snapshot.manifest[key] = original
