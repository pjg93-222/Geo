"""Check every shipped geographic layer and area index on the running app."""
import csv, io, json, math
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlencode
from tools.smoke_local_setup import get, ORIGIN
from tools.geography_levels import EXTRA_LEVELS
from tools.area_indices import BY_ID

def main():
    data,_=get('api/snapshots');snapshot=next(item for item in data['snapshots'] if item['active'])
    key=snapshot['key'];manifest=snapshot['manifest'];assert manifest['schema']=='cu-summary-v3'
    receipt={'snapshot':key,'source_sha256':manifest['source_sha256'],'layers':{},'indices':{},'region_scores':{}}
    for basis in ('registration','residential'):
        receipt['layers'][basis]={};receipt['indices'][basis]={};receipt['region_scores'][basis]={}
        for level in EXTRA_LEVELS:
            result,_=get('api/summaries',snapshot=key,basis=basis,level=level,metric='count')
            assert sum(row['count'] or 0 for row in result['rows'])==manifest['bases'][basis]['mapped']
            assert result['meta']['assignment']['unassigned']==0
            geometry,_=get('api/geometry',snapshot=key,level=level)
            assert {row['id'] for row in result['rows']}=={feature['properties']['id'] for feature in geometry['features']}
            assert all(feature['geometry']['type'] in ('Polygon','MultiPolygon') for feature in geometry['features'])
            receipt['layers'][basis][level]=len(result['rows'])
        for metric in BY_ID:
            receipt['indices'][basis][metric]={}
            for level in (*EXTRA_LEVELS,'zcta','block'):
                result,_=get('api/summaries',snapshot=key,basis=basis,level=level,metric=metric,scale='raw')
                valid=[row for row in result['rows'] if row['measure_status']=='ok']
                for row in result['rows']:
                    assert 'measures' not in row and row['valid_count'] is None
                    if row['measure_status']=='ok':
                        assert math.isfinite(row['value']) and 0<=row['value']<=100
                        assert row['component_coverage']>=.75-1e-10 and row['field_coverage']>=.7
                    else:assert row['value'] is None and row['index'] is None
                receipt['indices'][basis][metric][level]={'available':len(valid),'total':len(result['rows'])}
                if level=='region':
                    assert len(valid)==1 and valid[0]['index'] is None
                    receipt['region_scores'][basis][metric]=round(valid[0]['value'],2)
                    details,_=get('api/index-detail',snapshot=key,basis=basis,level=level,metric=metric,id=valid[0]['id'])
                    assert details['value']==valid[0]['value']
                    assert all(part['value'] is None for part in details['components'] if part['status'] in ('suppressed','unavailable'))
        query={'snapshot':key,'basis':basis,'level':'state_house','metric':'area_consumer','scale':'raw'}
        values,_=get('api/summaries',**query)
        with urlopen(ORIGIN+'/api/summaries?'+urlencode({**query,'format':'csv'}),timeout=30) as response:
            exported=list(csv.DictReader(io.StringIO(response.read().decode())))
        assert len(exported)==len(values['rows'])
        for actual,expected in zip(exported,values['rows']):
            assert actual['value']==('' if expected['value'] is None else str(expected['value']))
            assert actual['component_coverage']==('' if expected['component_coverage'] is None else str(expected['component_coverage']))
    receipt['passed']=True
    (Path(__file__).resolve().parents[1]/'.local/hierarchy-smoke.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps(receipt,indent=2))

if __name__=='__main__':main()
