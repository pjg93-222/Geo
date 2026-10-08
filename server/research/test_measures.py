"""Focused tests for the protected v2 aggregate boundary and selected-measure API."""
import copy
import csv
import io
import json
import zipfile
from unittest import TestCase as UnitTest
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase

from tools.measures import MEASURES, MEASURE_RANGES
from .bundles import validate
from .models import SavedView, Snapshot


MEAN=next(measure for measure in MEASURES if measure['aggregation']=='mean')
SHARE=next(measure for measure in MEASURES if measure['aggregation']!='mean')


def measure_bundle():
    with zipfile.ZipFile(settings.BASE_DIR/'data'/'co-cd005-4c48800b8c21.zip') as archive:
        manifest=json.loads(archive.read('manifest.json'))
    manifest.pop('threshold',None)      # the stored v2 manifest withheld cells under 20; the test bundle withholds nothing
    manifest.update(schema='cu-summary-v2',snapshot='co-cd005-123456789abc',measures=copy.deepcopy(MEASURES),measure_methodology='Protected source-field summaries; percentiles compare published areas within the selected partition.')
    manifest['input']['accepted_unique_profiles']=120
    for basis in manifest['bases']:
        manifest['bases'][basis]={'imported':120,'mapped':120,'unplaced':0,'coverage':1,'excluded':{},'zcta_mapped':120,'zcta_unassigned':0}
    low,high=MEASURE_RANGES[MEAN['id']]
    rows=[]
    for ident,index in [('80901',25),('80902',75)]:
        cells={measure['id']:[None,None,None,None,'unavailable'] for measure in MEASURES}
        cells[MEAN['id']]=[low+(high-low)*index/100,60,1,index,'ok']
        rows.append({'id':ident,'basis':'registration','level':'postal_zip','label':'Postal ZIP '+ident,'count':60,'source_count':60,'status':'ok','partial':False,'share':.5,'coverage':1,'measures':cells})
    return {'manifest.json':manifest,'rows.json':rows,'geometry.json':{'block':{},'zcta':{}},'streets.geojson':{'type':'FeatureCollection','features':[]},'district.geojson':{'type':'FeatureCollection','features':[]}}


class MeasureValidationTests(UnitTest):
    def setUp(self):self.payload=measure_bundle()

    def test_v1_and_v2_are_supported_without_relaxing_their_exact_shapes(self):
        self.assertEqual(validate(self.payload)['schema'],'cu-summary-v2')
        old=copy.deepcopy(self.payload)
        old['manifest.json']['schema']='cu-summary-v1'
        del old['manifest.json']['measures'];del old['manifest.json']['measure_methodology']
        for row in old['rows.json']:del row['measures']
        self.assertEqual(validate(old)['schema'],'cu-summary-v1')
        old['manifest.json']['measures']=MEASURES
        with self.assertRaises(ValueError):validate(old)

    def test_catalog_and_methodology_must_match_the_approved_contract(self):
        mutations=[lambda p:p['manifest.json']['measures'][0].update(label='Different source meaning'),lambda p:p['manifest.json']['measures'].pop(),lambda p:p['manifest.json'].update(measure_methodology='x'*1201),lambda p:p['rows.json'][0]['measures'].pop(MEAN['id']),lambda p:p['rows.json'][0].update(person_id='forbidden')]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                payload=copy.deepcopy(self.payload);mutate(payload)
                with self.assertRaises(ValueError):validate(payload)

    def test_hidden_cells_must_clear_every_statistic(self):
        for status in ['suppressed','unavailable']:
            for offset in range(4):
                with self.subTest(status=status,offset=offset):
                    payload=copy.deepcopy(self.payload)
                    for row in payload['rows.json']:row['measures'][MEAN['id']]=[None,None,None,None,status]
                    payload['rows.json'][0]['measures'][MEAN['id']][offset]=0
                    with self.assertRaises(ValueError):validate(payload)

    def test_hidden_base_rows_cannot_carry_any_measure_cells(self):
        for row in self.payload['rows.json']:
            row.update(status='suppressed',count=None,source_count=None,share=None,coverage=None,measures={})
        validate(self.payload)
        self.payload['rows.json'][0]['measures'][MEAN['id']]=[None,None,None,None,'suppressed']
        with self.assertRaises(ValueError):validate(self.payload)

    def test_invalid_counts_missingness_ranges_and_percentiles_are_rejected(self):
        valid=self.payload['rows.json'][0]['measures'][MEAN['id']]
        bad_cells=[]
        for offset,values in [(0,[float('nan'),True,MEASURE_RANGES[MEAN['id']][1]+1]),(1,[19,41,59,61,True,60.0,None]),(2,[None,.5,True]),(3,[None,-1,101,True]),(4,['invented'])]:
            for value in values:
                cell=list(valid);cell[offset]=value;bad_cells.append(cell)
        bad_cells.extend([valid[:-1],valid+[None],{}])
        for cell in bad_cells:
            with self.subTest(cell=cell):
                payload=copy.deepcopy(self.payload);payload['rows.json'][0]['measures'][MEAN['id']]=cell
                with self.assertRaises(ValueError):validate(payload)

    def test_share_numerator_must_be_a_whole_count_but_may_be_small(self):
        for row in self.payload['rows.json']:row['measures'][SHARE['id']]=[.5,40,40/60,50,'ok']
        validate(self.payload)
        for value in [.01,.99,.501,-.1,1.1]:
            with self.subTest(value=value):
                payload=copy.deepcopy(self.payload);payload['rows.json'][0]['measures'][SHARE['id']][0]=value
                with self.assertRaises(ValueError):validate(payload)
        for value,low,high in [(.1,25,75),(.9,75,25)]:       # 4 or 36 of 40: shown (nothing is withheld)
            with self.subTest(value=value):
                payload=copy.deepcopy(self.payload)
                payload['rows.json'][0]['measures'][SHARE['id']][0::3]=[value,low]
                payload['rows.json'][1]['measures'][SHARE['id']][3]=high
                validate(payload)
        for value in [0,1]:
            self.payload['rows.json'][0]['measures'][SHARE['id']][0]=value
            self.payload['rows.json'][0]['measures'][SHARE['id']][3]=25 if value==0 else 75
            self.payload['rows.json'][1]['measures'][SHARE['id']][3]=75 if value==0 else 25
            validate(self.payload)

    def test_percentile_integrity_rejects_wrong_in_range_rank_and_accepts_ties(self):
        validate(self.payload)
        self.payload['rows.json'][0]['measures'][MEAN['id']][3]=26
        with self.assertRaisesRegex(ValueError,'Inconsistent measure percentile'):validate(self.payload)
        value=self.payload['rows.json'][0]['measures'][MEAN['id']][0]
        for row in self.payload['rows.json']:
            row['measures'][MEAN['id']][0]=value
            row['measures'][MEAN['id']][3]=50
        validate(self.payload)

    def test_nothing_is_too_small_to_show(self):
        for row in self.payload['rows.json']:
            row.update(count=3,source_count=3,share=3/120,coverage=1)           # a 3-person area ...
            row['measures'][MEAN['id']][1:3]=[1,1/3]                            # ... whose measure rests on 1 profile
        validate(self.payload)
        self.payload['rows.json'][0]['measures'][MEAN['id']]=[None,None,None,None,'unavailable']      # a cell without a value needs no partner
        self.payload['rows.json'][1]['measures'][MEAN['id']][3]=50                                    # (the other one is alone in its partition)
        validate(self.payload)

    def test_categorical_siblings_cannot_reconstruct_a_hidden_code_share(self):
        fields={measure['field'] for measure in MEASURES}
        group=next([measure for measure in MEASURES if measure['field']==field] for field in fields if sum(measure['field']==field for measure in MEASURES)>1)
        for row in self.payload['rows.json']:
            for offset,measure in enumerate(group):row['measures'][measure['id']]=[1 if offset==0 else 0,60,1,50,'ok']
        validate(self.payload)
        for replacement in [[None,None,None,None,'suppressed'],[1,40,40/60,50,'ok'],[0,60,1,50,'ok']]:
            with self.subTest(replacement=replacement):
                payload=copy.deepcopy(self.payload)
                for row in payload['rows.json']:row['measures'][group[0]['id']]=replacement
                with self.assertRaises(ValueError):validate(payload)

    def test_a_stored_v2_snapshot_with_a_threshold_and_suppressed_areas_still_validates(self):
        self.payload['manifest.json']['threshold']=20
        extra=copy.deepcopy(self.payload['rows.json'])
        for offset,row in enumerate(extra):
            row.update(id=str(80903+offset),label='Postal ZIP '+str(80903+offset),status='suppressed',count=None,source_count=None,share=None,coverage=None,measures={})
        self.payload['rows.json'].extend(extra)
        validate(self.payload)
        self.payload['manifest.json']['threshold']=10
        with self.assertRaises(ValueError):validate(self.payload)


class MeasureApiTests(TestCase):
    def setUp(self):
        self.payload=measure_bundle()
        self.user=User.objects.create_user('measure-analyst')
        self.client.force_login(self.user)
        self.snapshot=Snapshot.objects.create(key=self.payload['manifest.json']['snapshot'],manifest=self.payload['manifest.json'],active=True)
        self.loader=patch('server.research.views.load',side_effect=lambda key,filename:self.payload[filename])
        self.loader.start();self.addCleanup(self.loader.stop)

    def query(self,**kwargs):
        return self.client.get('/api/summaries',{'level':'postal_zip','metric':MEAN['id'],**kwargs})

    def config(self,**kwargs):
        return {'snapshot':self.snapshot.key,'basis':'registration','level':'postal_zip','metric':MEAN['id'],'scale':'percentile','center':[-104.8,38.8],'zoom':11,'streets':False,'opacity':.8,'outline':True,**kwargs}

    def test_selected_cell_and_metadata_without_nested_measure_disclosure(self):
        response=self.query(scale='percentile');self.assertEqual(response.status_code,200)
        result=response.json();self.assertEqual(result['meta']['measure'],MEAN);self.assertEqual(result['meta']['scale'],'percentile')
        self.assertEqual(result['meta']['measure_methodology'],self.snapshot.manifest['measure_methodology'])
        for actual,source in zip(result['rows'],self.payload['rows.json']):
            self.assertNotIn('measures',actual)
            self.assertEqual([actual[key] for key in ['value','valid_count','field_coverage','index','measure_status']],source['measures'][MEAN['id']])
        self.assertEqual(self.query().json()['meta']['scale'],'raw')
        self.assertIn('measures',self.payload['rows.json'][0])

    def test_unknown_metric_and_scale_rejected(self):
        for query in [{'metric':'invented'},{'metric':'sii_persist_id'},{'scale':'invented'}]:
            self.assertEqual(self.query(**query).status_code,400)

    def test_csv_exports_the_exact_selected_values_and_status(self):
        data=self.query(scale='percentile').json()
        response=self.query(scale='percentile',format='csv')
        exported=list(csv.DictReader(io.StringIO(response.content.decode())))
        self.assertEqual(len(exported),len(data['rows']))
        for actual,expected in zip(exported,data['rows']):
            for key in ['value','index','valid_count','field_coverage','measure_status']:
                self.assertEqual(actual[key],'' if expected[key] is None else str(expected[key]))
            self.assertEqual(actual['metric'],MEAN['id']);self.assertEqual(actual['scale'],'percentile')
        self.assertNotIn(SHARE['id'],response.content.decode())

    def test_legacy_metrics_receive_selected_values_and_tied_percentiles(self):
        for metric in ['count','share','coverage']:
            data=self.query(metric=metric).json()
            self.assertIsNone(data['meta']['measure'])
            for row in data['rows']:
                self.assertEqual(row['value'],row[metric]);self.assertEqual(row['index'],50);self.assertEqual(row['measure_status'],'ok')
                self.assertNotIn('measures',row)

    def test_v1_snapshot_returns_unavailable_measure_cells(self):
        self.snapshot.manifest['schema']='cu-summary-v1'
        del self.snapshot.manifest['measures'];del self.snapshot.manifest['measure_methodology'];self.snapshot.save()
        for row in self.payload['rows.json']:del row['measures']
        data=self.query().json();self.assertEqual(data['meta']['metric_status'],'unavailable')
        for row in data['rows']:
            self.assertTrue(all(row[key] is None for key in ['value','index','valid_count','field_coverage']))
            self.assertEqual(row['measure_status'],'unavailable')

    def test_suppressed_metric_does_not_expose_statistics_in_json_or_csv(self):
        for row in self.payload['rows.json']:row['measures'][MEAN['id']]=[None,None,None,None,'suppressed']
        row=self.query().json()['rows'][0]
        self.assertEqual(row['measure_status'],'suppressed')
        self.assertTrue(all(row[key] is None for key in ['value','index','valid_count','field_coverage']))
        exported=list(csv.DictReader(io.StringIO(self.query(format='csv').content.decode())))[0]
        self.assertTrue(all(exported[key]=='' for key in ['value','index','valid_count','field_coverage']))

    def test_saved_views_allow_catalog_metrics_and_default_old_scale(self):
        response=self.client.post('/api/views',json.dumps({'name':'Measure view','config':self.config()}),content_type='application/json')
        self.assertEqual(response.status_code,200)
        self.assertEqual(SavedView.objects.get(pk=response.json()['id']).config['scale'],'percentile')
        old=self.config(metric='count');old.pop('scale')
        SavedView.objects.create(owner=self.user,name='Legacy',config=old)
        configs={row['name']:row['config'] for row in self.client.get('/api/views').json()['views']}
        self.assertEqual(configs['Legacy']['scale'],'raw')
        response=self.client.post('/api/views',json.dumps({'name':'Legacy new','config':old}),content_type='application/json')
        self.assertEqual(response.status_code,200);self.assertEqual(SavedView.objects.get(pk=response.json()['id']).config['scale'],'raw')
        for config in [self.config(metric='unknown'),self.config(scale='unknown')]:
            response=self.client.post('/api/views',json.dumps({'name':'Bad','config':config}),content_type='application/json')
            self.assertEqual(response.status_code,400)
