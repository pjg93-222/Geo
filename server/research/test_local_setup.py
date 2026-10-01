from contextlib import closing
import csv
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from django.test import RequestFactory, SimpleTestCase, override_settings
from .contact_views import contacts, person, search_query
from .road_views import roads
from . import views
from tools.build_credit_quality import audit, FIELDS


class LocalSetupTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = override_settings(DATA_DIR=self.root, HOSTED=False, LOCAL_ADMIN_ACCESS=True)
        self.settings.enable()
        self.factory = RequestFactory()
        self.ident = 'p_'+'a'*64
        self.house = 'h_'+'b'*64
        with closing(sqlite3.connect(self.root/'contacts.sqlite3')) as db, db:
            db.executescript('CREATE TABLE contacts(id TEXT,name TEXT,email TEXT,email_match TEXT,phone TEXT,wireless TEXT,wireless_match TEXT,registration TEXT,residential TEXT);CREATE TABLE metadata(key TEXT,value TEXT);CREATE VIRTUAL TABLE search USING fts5(name,email,phone,address,content="");')
            db.execute('INSERT INTO contacts VALUES (?,?,?,?,?,?,?,?,?)', (self.ident,'Synthetic Person','sample@example.invalid','A','5550100','','','1 Synthetic Road','2 Synthetic Road'))
            db.execute('INSERT INTO search(rowid,name,email,phone,address) VALUES (1,?,?,?,?)', ('Synthetic Person','sample@example.invalid','5550100','1 Synthetic Road 2 Synthetic Road'))
            for key,value in {'schema':'contacts-v1','source_sha256':'f'*64,'totals':{'records':1}}.items():
                db.execute('INSERT INTO metadata VALUES (?,?)', (key,json.dumps(value)))
        with closing(sqlite3.connect(self.root/'street.sqlite3')) as db, db:
            db.executescript('CREATE TABLE metadata(key TEXT,value TEXT);CREATE TABLE households(id TEXT,basis TEXT,address TEXT,longitude REAL,latitude REAL);CREATE TABLE people(id TEXT,household_id TEXT);')
            db.executemany('INSERT INTO metadata VALUES (?,?)', [('schema_version','"1"'),('source_sha256',json.dumps('f'*64))])
            db.execute('INSERT INTO households VALUES (?,?,?,?,?)', (self.house,'registration','1 Synthetic Road',-104.8,38.8))
            db.execute('INSERT INTO people VALUES (?,?)', (self.ident,self.house))
        with closing(sqlite3.connect(self.root/'roads.sqlite3')) as db, db:
            db.executescript('CREATE TABLE roads(id INTEGER,name TEXT,kind TEXT,geometry TEXT);CREATE VIRTUAL TABLE extents USING rtree(id,west,east,south,north);')
            db.execute('INSERT INTO roads VALUES (1,?,?,?)', ('Synthetic Road','S1400',json.dumps({'type':'LineString','coordinates':[[-104.8,38.8],[-104.79,38.81]]})))
            db.execute('INSERT INTO extents VALUES (1,-104.8,-104.79,38.8,38.81)')

    def tearDown(self):
        self.settings.disable();self.temp.cleanup()
        views.geometry_entries.cache_clear();views.viewport_feature.cache_clear()

    def request(self, view, query, **headers):
        req=self.factory.get('/api/test',query,REMOTE_ADDR='127.0.0.1',**headers)
        req.user=SimpleNamespace(is_authenticated=True,is_staff=True)
        req.local_admin_access=True
        return view(req)

    def test_search_supplied_contacts_and_formatted_phone(self):
        for query in ['Synthetic','sample@example.invalid','(555) 0100','1 Synthetic']:
            response=self.request(contacts,dict(q=query,page='0',basis='registration'))
            self.assertEqual(response.status_code,200)
            self.assertEqual(json.loads(response.content)['total'],1)
            self.assertEqual(response['Cache-Control'],'no-store, private')

    def test_search_operators_are_literal_and_page_bounded(self):
        self.assertEqual(search_query('foo OR bar'),'"foo"* AND "OR"* AND "bar"*')
        self.assertEqual(self.request(contacts,dict(q='',page='-1',basis='registration')).status_code,400)
        self.assertEqual(self.request(contacts,dict(q='x'*161,page='0',basis='registration')).status_code,400)

    def test_contacts_reject_forwarding_and_hosted_requests(self):
        params=dict(q='',page='0',basis='registration')
        self.assertEqual(self.request(contacts,params,HTTP_X_FORWARDED_FOR='127.0.0.1').status_code,403)
        with override_settings(HOSTED=True):
            self.assertEqual(self.request(contacts,params).status_code,403)

    def test_person_map_join_is_same_source_and_basis(self):
        data=json.loads(self.request(person,dict(id=self.ident,basis='registration')).content)
        self.assertEqual(data['email'],'sample@example.invalid')
        self.assertEqual(data['location']['id'],self.house)
        data=json.loads(self.request(person,dict(id=self.ident,basis='residential')).content)
        self.assertIsNone(data['location'])
        with closing(sqlite3.connect(self.root/'contacts.sqlite3')) as db, db:
            db.execute("UPDATE metadata SET value=? WHERE key='source_sha256'", (json.dumps('c'*64),))
        self.assertEqual(self.request(person,dict(id=self.ident,basis='registration')).status_code,409)

    def test_person_rejects_raw_or_missing_identifiers(self):
        self.assertEqual(self.request(person,dict(id='raw-id',basis='registration')).status_code,400)
        self.assertEqual(self.request(person,dict(id='p_'+'c'*64,basis='registration')).status_code,404)

    def test_registry_does_not_include_inferred_attributes(self):
        data=json.loads(self.request(contacts,dict(q='',page='0',basis='registration')).content)
        self.assertEqual(set(data['people'][0]),{'id','name','address','email','phone','wireless'})

    def test_road_detail_is_viewport_and_zoom_bounded(self):
        params={'bbox':'-104.81,38.79,-104.78,38.82','zoom':'16'}
        self.assertEqual(len(json.loads(self.request(roads,params).content)['features']),1)
        self.assertEqual(len(json.loads(self.request(roads,{**params,'zoom':'9'}).content)['features']),0)
        self.assertEqual(self.request(roads,{**params,'bbox':'nan,0,1,2'}).status_code,400)

    def test_high_detail_geometry_keeps_original_coordinates(self):
        geometry={'type':'Polygon','coordinates':[[[0,0],[1,0],[1,1],[0,0]]]}
        with patch.object(views,'geometry_entries',return_value={'1':{'geometry':geometry}}):
            with patch('shapely.geometry.shape',side_effect=AssertionError('Unnecessary conversion')):
                self.assertEqual(views.viewport_feature('synthetic','block',2,'1')['geometry'],geometry)

    def test_audit_distinguishes_blanks_zero_and_invalid_codes(self):
        path=self.root/'test.csv'
        fields=[spec[1] for spec in FIELDS.values()]
        with path.open('w',newline='',encoding='cp1252') as stream:
            writer=csv.writer(stream);writer.writerow(fields)
            writer.writerows([['0']*4,['']*4,['bad']*4,[' 1 ']*4])
        count,results=audit(path)
        self.assertEqual(count,4)
        for row in results.values():
            self.assertEqual((row['valid'],row['missing'],row['invalid'],row['zero'],row['whitespace_normalized']),(2,1,1,1,1))
