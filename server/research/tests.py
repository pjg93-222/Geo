import copy, csv, io, json, sqlite3, tempfile, zipfile
from datetime import timedelta
from pathlib import Path
from unittest import TestCase as UnitTest
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, Client, override_settings
from django.utils import timezone
from shapely.geometry import box
import pandas as pd
from .bundles import validate, import_bundle, load, FILES
from .models import Snapshot, Invitation, LoginAttempt
from .views import create_invitation
from tools.ingest import protect, read_profiles, classify, FIELDS

BUNDLE=settings.BASE_DIR/'data'/'co-cd005-4c48800b8c21.zip'

@override_settings(LOCAL_ADMIN_ACCESS=True, HOSTED=False)
class LocalAdministratorTests(TestCase):
    def test_direct_loopback_gets_full_owner_without_password_or_session(self):
        c=Client(HTTP_HOST='localhost')
        r=c.get('/api/auth/session').json()
        self.assertTrue(r['authenticated']);self.assertTrue(r['administrator']);self.assertTrue(r['local_admin_access'])
        owner=User.objects.get(username='admin')
        self.assertTrue(owner.is_superuser);self.assertFalse(owner.has_usable_password())
        self.assertNotIn('_auth_user_id',c.session)
        self.assertEqual(c.get('/api/accounts').status_code,200)
    def test_local_mutations_still_require_csrf(self):
        c=Client(HTTP_HOST='localhost',enforce_csrf_checks=True)
        token=c.get('/api/auth/session').json()['csrf']
        self.assertEqual(c.post('/api/accounts',{'username':'colleague'}).status_code,403)
        r=c.post('/api/accounts',json.dumps({'username':'colleague'}),content_type='application/json',HTTP_X_CSRFTOKEN=token)
        self.assertEqual(r.status_code,200)
    def test_nonloopback_request_cannot_get_owner(self):
        self.assertEqual(Client(HTTP_HOST='localhost',REMOTE_ADDR='192.0.2.10').get('/api/accounts').status_code,401)
        self.assertFalse(User.objects.exists())
    def test_forwarded_requests_cannot_get_owner(self):
        for header in ['HTTP_FORWARDED','HTTP_X_FORWARDED_FOR','HTTP_X_FORWARDED_HOST','HTTP_X_FORWARDED_PROTO']:
            self.assertEqual(Client(HTTP_HOST='localhost',**{header:'127.0.0.1'}).get('/api/accounts').status_code,401)
    @override_settings(HOSTED=True, LOCAL_ADMIN_ACCESS=True)
    def test_hosted_mode_never_gets_automatic_owner(self):
        self.assertEqual(Client(HTTP_HOST='localhost').get('/api/accounts').status_code,401)
        self.assertFalse(User.objects.exists())
    @override_settings(LOCAL_ADMIN_ACCESS=False)
    def test_switch_can_restore_normal_signin(self):
        self.assertEqual(Client(HTTP_HOST='localhost').get('/api/accounts').status_code,401)

class IngestionTests(UnitTest):
    def test_conflict_in_excluded_field_is_quarantined(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'test.csv';r=['00001','00002','005']+['CO','00123','080410001001001','0.5','0.5','ROOFTOP']*2
            with p.open('w',newline='',encoding='cp1252') as f:
                w=csv.writer(f);w.writerow(FIELDS+['unused_field']);w.writerow(r+['one']);w.writerow(r+['two'])
            df,receipt=read_profiles(p)
            self.assertEqual(len(df),0);self.assertEqual(receipt['conflicting_ids_quarantined'],1)
    def test_primary_and_complementary_suppression(self):
        rows=[{'id':'1','count':4,'source_count':4},{'id':'2','count':40,'source_count':40},{'id':'3','count':80,'source_count':80}]
        protect(rows,124)
        self.assertEqual([r['status'] for r in rows],['suppressed','suppressed','ok'])
        self.assertIsNone(rows[0]['share']);self.assertIsNone(rows[1]['count'])
    def test_derived_small_remainder_is_hidden(self):
        rows=[{'id':'1','count':40,'source_count':42},{'id':'2','count':50,'source_count':50}]
        protect(rows,90);self.assertTrue(all(r['count'] is None for r in rows))
    def test_unavailable_and_zero_are_distinct(self):
        rows=[{'id':'1','count':0,'source_count':0},{'id':'2','count':0,'source_count':40}]
        protect(rows,0);self.assertEqual(rows[0]['status'],'unavailable');self.assertIsNone(rows[0]['count']);self.assertEqual(rows[1]['count'],0)
    def test_duplicates_and_leading_zero_identifiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'test.csv';r=['00001','00002','005']+['CO','00123','080410001001001','0.5','0.5','ROOFTOP']*2
            with p.open('w',newline='',encoding='cp1252') as f:
                w=csv.writer(f);w.writerow(FIELDS);w.writerow(r);w.writerow(r)
            df,receipt=read_profiles(p);self.assertEqual(len(df),1);self.assertEqual(df.iloc[0].sii_persist_id,'00001');self.assertEqual(receipt['duplicate_rows_collapsed'],1)
            with p.open('a',newline='',encoding='cp1252') as f:
                w=csv.writer(f);r[4]='00999';w.writerow(r)
            df,receipt=read_profiles(p);self.assertEqual(len(df),0);self.assertEqual(receipt['conflicting_ids_quarantined'],1)
    def test_wrong_width_and_encoding_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'bad.csv';p.write_text(','.join(FIELDS)+'\nshort,row\n',encoding='utf-8')
            with self.assertRaises(ValueError):read_profiles(p)
            p.write_bytes(b'\xff')
            with self.assertRaises(UnicodeDecodeError):read_profiles(p,'utf-8')
    def test_precision_conflict_unknown_and_coordinates(self):
        rows=[]
        for i in range(6):rows.append(['x'+str(i),'y'+str(i),'005']+['CO','80901','080410001001001','0.5','0.5','ROOFTOP']*2)
        df=pd.DataFrame(rows,columns=FIELDS);p='svi_registration'
        df.loc[1,p+'_geolevel']='PLACE';df.loc[2,p+'_censusid']='999990001001001';df.loc[3,p+'_longitude']='0.9';df.loc[4,p+'_latitude']='100';df.loc[5,p+'_latitude']=''
        result,*_=classify(df,p,{'080410001001001':box(0,0,.7,.7)},box(0,0,1,1))
        self.assertEqual(list(result),['mapped','coarse_precision','invalid_geography','spatial_conflict','invalid_coordinates','missing_evidence'])

class ApplicationTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.override=override_settings(DATA_DIR=Path(cls.temp.name));cls.override.enable();super().setUpClass()
        with zipfile.ZipFile(BUNDLE) as z:cls.payload={n:json.loads(z.read(n)) for n in FILES}
    @classmethod
    def tearDownClass(cls):
        load.cache_clear();cls.override.disable();cls.temp.cleanup();super().tearDownClass()
    @classmethod
    def setUpTestData(cls):
        cls.admin=User.objects.create_user('administrator',password='Reliable test passphrase 72!',is_staff=True)
        cls.analyst=User.objects.create_user('analyst',password='Reliable test passphrase 83!')
        cls.snap=import_bundle(BUNDLE)
    def setUp(self):self.client.force_login(self.analyst)
    def test_actual_baseline_and_geography(self):
        m=validate(self.payload);self.assertEqual(m['input']['input_rows'],491308);self.assertEqual(m['input']['column_count'],622)
        self.assertEqual(m['bases']['registration']['mapped'],436057);self.assertEqual(m['bases']['registration']['unplaced'],55251)
        self.assertEqual(m['bases']['residential']['mapped'],434967)
        self.assertEqual(m['geography']['street_polygons'],14058)
    def test_reject_inconsistent_coverage_and_fractional_counts(self):
        p=copy.deepcopy(self.payload);p['manifest.json']['bases']['registration']['coverage']=.5
        with self.assertRaises(ValueError):validate(p)
        p=copy.deepcopy(self.payload);r=next(r for r in p['rows.json'] if r['status']=='ok' and r['count']>20);r['count']+=.5
        with self.assertRaises(ValueError):validate(p)
    def test_postal_vintage_and_zoom_validation(self):
        r=self.client.get('/api/summaries?level=postal_zip').json()
        self.assertEqual(r['meta']['boundary_vintage'],'Not applicable: supplied postal ZIP')
        self.assertEqual(self.client.get('/api/geometry?level=block&zoom=nan').status_code,400)
    def test_publish_then_rollback_keeps_previous_valid_snapshot(self):
        payload=copy.deepcopy(self.payload);payload['manifest.json']['snapshot']='co-cd005-123456789abc'
        archive=io.BytesIO()
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
            for name,data in payload.items():z.writestr(name,json.dumps(data))
        archive.seek(0);second=import_bundle(archive)
        self.assertEqual(Snapshot.objects.get(active=True).key,second.key)
        self.client.force_login(self.admin)
        r=self.client.post('/api/snapshots/activate',json.dumps({'snapshot':self.snap.key}),content_type='application/json')
        self.assertEqual(r.status_code,200);self.assertEqual(Snapshot.objects.get(active=True).key,self.snap.key)
        self.assertEqual(self.client.get('/api/summaries?level=zcta').json()['meta']['denominator'],436057)
    def test_authentication_and_role_boundaries(self):
        self.client.logout();self.assertEqual(self.client.get('/api/snapshots').status_code,401)
        self.client.force_login(self.analyst);self.assertEqual(self.client.get('/api/accounts').status_code,403)
        self.assertEqual(self.client.post('/api/snapshots/import').status_code,403)
    def test_csrf_enforced_on_mutation(self):
        c=Client(enforce_csrf_checks=True);self.assertEqual(c.post('/api/auth/login',{'username':'a'}).status_code,403)
    def test_summary_table_export_parity(self):
        data=self.client.get('/api/summaries?level=zcta').json();response=self.client.get('/api/summaries?level=zcta&format=csv')
        exported=list(csv.DictReader(io.StringIO(response.content.decode())))
        self.assertEqual(len(exported),len(data['rows']))
        byid={r['id']:r for r in data['rows']}
        for row in exported:self.assertEqual(row['count'],'' if byid[row['geographic_id']]['count'] is None else str(byid[row['geographic_id']]['count']))
        self.assertTrue(all(r['coverage'] is None for r in data['rows']))
    def test_all_published_cells_are_protected(self):
        for row in self.payload['rows.json']:
            for key in ['count','source_count']:
                if row[key] is not None:self.assertTrue(row[key]==0 or row[key]>=20)
            if row['status']!='ok':self.assertTrue(all(row[k] is None for k in ['count','source_count','share','coverage']))
    def test_geometry_contains_no_individual_points(self):
        data=self.client.get('/api/geometry?level=block&bbox=-104.9,38.7,-104.7,38.9').json()
        self.assertGreater(len(data['features']),0)
        for f in data['features']:self.assertEqual(set(f['properties']),{'id'});self.assertIn(f['geometry']['type'],['Polygon','MultiPolygon'])
    def test_reject_raw_fields_and_person_points(self):
        p=copy.deepcopy(self.payload);p['rows.json'][0]['sii_first_name']='Not allowed'
        with self.assertRaises(ValueError):validate(p)
        p=copy.deepcopy(self.payload);next(iter(p['geometry.json']['block'].values()))['geometry']={'type':'Point','coordinates':[-104.8,38.8]}
        with self.assertRaises(ValueError):validate(p)
    def test_reject_raw_csv_and_archive_traversal(self):
        self.client.force_login(self.admin)
        raw=io.BytesIO(b'sii_first_name,sii_email\n');raw.name='raw.csv'
        self.assertEqual(self.client.post('/api/snapshots/import',{'bundle':raw}).status_code,400)
        file=io.BytesIO()
        with zipfile.ZipFile(file,'w') as z:z.writestr('../escape.json','{}')
        file.seek(0);file.name='bad.zip'
        self.assertEqual(self.client.post('/api/snapshots/import',{'bundle':file}).status_code,400)
    def test_criteria_and_dictionary(self):
        d=self.client.get('/api/criteria').json();systems={x['id']:x for x in d['systems']}
        for key,total in [('footprint',1),('ranking',1),('importance',17.45)]:self.assertAlmostEqual(sum(r['weight'] for r in systems[key]['rows']),total)
        self.assertEqual(d['support_likelihood'],'Not measured')
        self.assertEqual(d['persistent_voices'],'Not measured')
        response=self.client.get('/api/criteria?format=csv');text=b''.join(response.streaming_content).decode();self.assertEqual(len(list(csv.DictReader(io.StringIO(text)))),622)
    def test_invitation_one_time_and_expiry(self):
        self.client.logout();link=create_invitation('new-user');token=link.split('=')[1]
        result=self.client.post('/api/auth/accept',json.dumps({'token':token,'password':'Long unique password for testing 84!'}),content_type='application/json')
        self.assertEqual(result.status_code,200)
        self.assertEqual(self.client.post('/api/auth/accept',json.dumps({'token':token,'password':'Long unique password for testing 84!'}),content_type='application/json').status_code,400)
        link=create_invitation('expired-user');Invitation.objects.filter(username='expired-user').update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.client.post('/api/auth/accept',json.dumps({'token':link.split('=')[1],'password':'Long unique password for testing 84!'}),content_type='application/json').status_code,400)
    def test_login_throttling_survives_username_rotation(self):
        self.client.logout()
        for i in range(8):self.assertEqual(self.client.post('/api/auth/login',json.dumps({'username':str(i),'password':'wrong'}),content_type='application/json').status_code,401)
        self.assertEqual(self.client.post('/api/auth/login',json.dumps({'username':'different','password':'wrong'}),content_type='application/json').status_code,429)
    def test_saved_view_owner_isolation(self):
        config={'snapshot':self.snap.key,'basis':'registration','level':'block','metric':'count','center':[-104.8,38.8],'zoom':11,'streets':False,'opacity':.8,'outline':True}
        r=self.client.post('/api/views',json.dumps({'name':'Test view','config':config}),content_type='application/json');self.assertEqual(r.status_code,200)
        self.client.force_login(self.admin);self.assertEqual(self.client.get('/api/views').json()['views'],[])
    def test_unknown_snapshot_and_bad_geography(self):
        self.assertEqual(self.client.get('/api/summaries?snapshot=missing').status_code,404)
        self.assertEqual(self.client.get('/api/geometry?level=point').status_code,400)
        self.assertEqual(self.client.get('/api/geometry?level=block&bbox=0,90,-10,-90').status_code,400)
    def test_rollback_is_administrator_only(self):
        other=Snapshot.objects.create(key='co-cd005-000000000000',manifest=self.snap.manifest)
        self.assertEqual(self.client.post('/api/snapshots/activate',json.dumps({'snapshot':other.key}),content_type='application/json').status_code,403)
        self.client.force_login(self.admin);self.assertEqual(self.client.post('/api/snapshots/activate',json.dumps({'snapshot':self.snap.key}),content_type='application/json').status_code,200)
        self.assertEqual(Snapshot.objects.filter(active=True).count(),1)
    def test_backup_restore_uses_consistent_sqlite_copy(self):
        live=settings.DATA_DIR/'accounts.sqlite3';backup=settings.DATA_DIR/'backup.sqlite3'
        with sqlite3.connect(live) as c:c.execute('CREATE TABLE IF NOT EXISTS proof(value TEXT)');c.execute('INSERT INTO proof VALUES (?)',('preserved',))
        call_command('backup',str(backup),stdout=io.StringIO())
        with sqlite3.connect(live) as c:c.execute('DELETE FROM proof')
        call_command('backup',str(backup),restore=True,stdout=io.StringIO())
        with sqlite3.connect(live) as c:self.assertEqual(c.execute('SELECT value FROM proof').fetchone()[0],'preserved')
