import copy
import csv
import io
import json
from unittest import TestCase as UnitTest
from unittest.mock import patch
import numpy as np
from shapely.geometry import box, Point, mapping
from django.contrib.auth.models import User
from django.test import TestCase
from tools.area_indices import BY_ID, evaluate, reference, summarize
from tools.measures import MEASURE_RANGES
from tools.extend_geography import assign
from tools.geography_levels import EXTRA_LEVELS, SOURCES, label
from .test_measures import measure_bundle
from .bundles import validate
from .models import Snapshot
from .views import composite_rows, reference_for

def source_row(value=.5):
    fields={part['measure'] for definition in BY_ID.values() for group in definition['domains'] for part in group['components']}
    measures={key:[MEASURE_RANGES[key][0]+value*(MEASURE_RANGES[key][1]-MEASURE_RANGES[key][0]),100,1,None,'ok'] for key in fields}
    return {'status':'ok','count':100,'measures':measures}

class AreaIndexMathTests(UnitTest):
    def setUp(self):self.refs=reference([source_row(value) for value in [0,.25,.5,.75,1]])

    def test_known_midrank_and_fixed_domain_weights(self):
        row=source_row(.75)
        result=evaluate(row,'area_participation',self.refs,detail=True)
        self.assertAlmostEqual(result['value'],70)
        self.assertAlmostEqual(result['component_coverage'],1)
        self.assertEqual(result['components_used'],9)
        self.assertIsNone(result['valid_count']) # no invented joint denominator
        # Give turnout a different position to verify the 50/25/25 formula.
        for part in BY_ID['area_participation']['domains'][0]['components']:
            row['measures'][part['measure']][0]=25
        self.assertAlmostEqual(evaluate(row,'area_participation',self.refs)['value'],50)

    def test_opposite_directions_and_ties(self):
        row=source_row(.75)
        for group in BY_ID['area_affiliation']['domains']:
            for part in group['components']:
                if part['direction']<0:row['measures'][part['measure']][0]=.25
        self.assertAlmostEqual(evaluate(row,'area_affiliation',self.refs)['value'],70)
        tied=reference([source_row(.5) for _ in range(5)])
        self.assertAlmostEqual(evaluate(source_row(.5),'area_participation',tied)['value'],50)

    def test_missing_components_are_not_zero_and_group_floor_is_enforced(self):
        row=source_row(.75)
        for field in ('sci_cpi_credit_repair_index__nonzero','sci_cpi_credit_report_index__nonzero'):
            row['measures'][field]=[None,None,None,None,'suppressed']
        result=evaluate(row,'area_consumer',self.refs,detail=True)
        self.assertAlmostEqual(result['value'],70)
        self.assertLess(result['component_coverage'],1)
        for part in BY_ID['area_consumer']['domains'][2]['components']:
            row['measures'][part['measure']]=[None,None,None,None,'unavailable']
        self.assertIsNone(evaluate(row,'area_consumer',self.refs)['value'])
        # A missing side cannot create a partisan balance from the other side.
        row=source_row();row['measures']['sci_ct_political_partyaff_democrat__code_1']=[None,None,None,None,'suppressed']
        self.assertIsNone(evaluate(row,'area_affiliation',self.refs)['value'])

    def test_low_coverage_and_thin_reference_fail_closed(self):
        row=source_row()
        for cell in row['measures'].values():cell[2]=.69
        self.assertIsNone(evaluate(row,'area_participation',self.refs)['value'])
        thin=reference([source_row(v) for v in [0,.25,.5,.75]])
        self.assertIsNone(evaluate(source_row(),'area_participation',thin)['value'])

    def test_zero_is_valid_and_hidden_rows_stay_hidden(self):
        zero=evaluate(source_row(0),'area_participation',self.refs)
        self.assertEqual(zero['measure_status'],'ok');self.assertAlmostEqual(zero['value'],10)
        hidden=evaluate({'status':'suppressed','count':None,'measures':{}},'area_participation',self.refs,detail=True)
        for field in ('value','valid_count','field_coverage','index','component_coverage'):self.assertIsNone(hidden[field])
        self.assertTrue(all(part['value'] is None and part['normalized'] is None for part in hidden['components']))

    def test_single_region_has_score_without_fake_peer_percentile(self):
        result=summarize([source_row(.75)],'area_participation',self.refs)
        self.assertAlmostEqual(result[0]['value'],70);self.assertIsNone(result[0]['index'])
        result=summarize([source_row(.25),source_row(.75)],'area_participation',self.refs)
        self.assertEqual([row['index'] for row in result],[25,75])

    def test_boundary_matches_do_not_double_count_or_force_split_blocks(self):
        boundaries={'left':box(0,0,1,1),'right':box(1,0,2,1)}
        points=np.array([Point(.5,.5),Point(1.5,.5),Point(1,.5),Point(3,.5),Point(.2,.2)])
        result=assign(points,np.array([True,True,True,True,False]),boundaries)
        self.assertEqual(result.tolist(),['left','right','','',''])

def hierarchy_bundle():
    payload=measure_bundle();manifest=payload['manifest.json'];manifest['schema']='cu-summary-v3'
    manifest['hierarchy']={'method':'geographic-extension-1','sources':SOURCES.copy(),'hashes':{level:'a'*64 for level in SOURCES},
      'reconciliation':{basis:{level:{'assigned':120,'unassigned':0} for level in EXTRA_LEVELS} for basis in manifest['bases']},
      'region_definition':'The supplied CD-05 extract','assignment':'Unique official polygon membership'}
    for level in EXTRA_LEVELS:
        ident='08041' if level=='county' else '08001' if level.startswith('state_') else '0805'
        payload['geometry.json'][level]={ident:{'id':ident,'geometry':json.loads(json.dumps(mapping(box(-105,38,-104,39)))),'bbox':[-105,38,-104,39]}}
        for basis in manifest['bases']:
            row=copy.deepcopy(payload['rows.json'][0]);row.update(id=ident,level=level,basis=basis,label=label(level,ident),count=120,source_count=None,coverage=None,share=1)
            for cell in row['measures'].values():
                if cell[4]=='ok':cell[1]=120;cell[3]=50
            payload['rows.json'].append(row)
    return payload

class HierarchyContractTests(UnitTest):
    def test_extension_validates_and_rejects_leaks_mismatches_and_wrong_sources(self):
        good=hierarchy_bundle();self.assertEqual(validate(good)['schema'],'cu-summary-v3')
        mutations=[lambda p:p['manifest.json']['hierarchy']['reconciliation']['registration']['county'].update(assigned=119),
                   lambda p:p['manifest.json']['hierarchy']['sources'].update(county='https://example.com/private'),
                   lambda p:p['geometry.json']['region'].clear(),
                   lambda p:p['rows.json'][-1].update(person_id='not-allowed'),
                   lambda p:p['rows.json'][-1].update(count=119),
                   lambda p:p['geometry.json']['county']['08041'].update(geometry={'type':'Point','coordinates':[-104.8,38.8]})]
        for mutate in mutations:
            bad=copy.deepcopy(good);mutate(bad)
            with self.assertRaises(ValueError):validate(bad)

class HierarchyApiTests(TestCase):
    def setUp(self):
        self.payload=hierarchy_bundle()
        self.user=User.objects.create_user('hierarchy-analyst');self.client.force_login(self.user)
        Snapshot.objects.create(key=self.payload['manifest.json']['snapshot'],active=True,manifest=self.payload['manifest.json'])
        self.loader=patch('server.research.views.load',side_effect=lambda key,filename:self.payload[filename]);self.loader.start();self.addCleanup(self.loader.stop)
        composite_rows.cache_clear();reference_for.cache_clear()
        self.addCleanup(composite_rows.cache_clear);self.addCleanup(reference_for.cache_clear)

    def test_all_new_layers_and_catalog_are_available(self):
        for level in EXTRA_LEVELS:
            result=self.client.get('/api/summaries',{'level':level,'metric':'count'})
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json()['rows'][0]['value'],120)
            self.assertEqual(result.json()['meta']['assignment']['unassigned'],0)
        manifest=self.client.get('/api/snapshots').json()['snapshots'][0]['manifest']
        self.assertEqual(len(manifest['levels']),8);self.assertEqual(len(manifest['composites']),4)

    def test_composite_json_csv_and_detail_agree(self):
        for i,value in enumerate([0,.25,.5,.75,1]):
            self.payload['rows.json'].append({**source_row(value),'id':str(80900+i),'basis':'registration','level':'zcta','label':'ZCTA '+str(80900+i),'partial':False,'source_count':None,'coverage':None,'share':.2})
        region=next(row for row in self.payload['rows.json'] if row['level']=='region' and row['basis']=='registration')
        region['measures']=source_row(.75)['measures']
        query={'level':'region','metric':'area_participation'}
        data=self.client.get('/api/summaries',query).json()['rows'][0]
        self.assertAlmostEqual(data['value'],70);self.assertIsNone(data['index']);self.assertNotIn('measures',data)
        exported=list(csv.DictReader(io.StringIO(self.client.get('/api/summaries',{**query,'format':'csv'}).content.decode())))[0]
        self.assertEqual(float(exported['value']),data['value']);self.assertEqual(exported['valid_count'],'')
        detail=self.client.get('/api/index-detail',{**query,'id':'0805'}).json()
        self.assertEqual(detail['value'],data['value']);self.assertEqual(len(detail['components']),9)
        self.assertEqual(self.client.get('/api/index-detail',{**query,'id':'unknown'}).status_code,404)

    def test_legislative_composite_view_can_be_saved(self):
        config={'snapshot':self.payload['manifest.json']['snapshot'],'basis':'registration','level':'state_house','metric':'area_consumer','scale':'raw','center':[-104.8,38.8],'zoom':9.5,'streets':False,'opacity':.8,'outline':True}
        response=self.client.post('/api/views',json.dumps({'name':'House composite','config':config}),content_type='application/json')
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.get('/api/views').json()['views'][0]['config'],config)
