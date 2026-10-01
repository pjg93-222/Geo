"""Verify the running local app without printing personal contact values."""
import json
import os
import time
from urllib.parse import urlencode
from urllib.request import urlopen
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# v3 copy: smoke checks target the 8766 preview. 8765 is the live app and is never the default here.
ORIGIN = os.environ.get('CU_ORIGIN', 'http://127.0.0.1:8766').rstrip('/')


def get(path, **params):
    start=time.perf_counter()
    with urlopen(ORIGIN+'/'+path+('?' + urlencode(params) if params else ''),timeout=30) as response:
        data=json.load(response)
    return data, round((time.perf_counter()-start)*1000,1)


def main():
    receipt={}
    session,_=get('api/auth/session')
    assert session['authenticated'] and session['local_admin_access']
    snapshots,_=get('api/snapshots')
    active=next(row for row in snapshots['snapshots'] if row['active'])['key']
    quality,_=get('api/credit-quality',snapshot=active)
    assert quality['records']==491308 and not quality['imputation_enabled']
    registry,elapsed=get('api/contacts',q='',page=0,basis='registration')
    assert registry['total']==491308 and len(registry['people'])==50
    receipt['registry_first_page_ms']=elapsed
    row=next((person for person in registry['people'] if person['email']),registry['people'][0])
    person,_=get('api/person',id=row['id'],basis='registration')
    assert person['id']==row['id'] and person['email']==row['email']
    if row['email']:
        found,elapsed=get('api/contacts',q=row['email'],page=0,basis='registration')
        assert found['total']>=1
        receipt['email_search_ms']=elapsed
    receipt['source_contact_counts']=registry['source_totals']
    location=person['location']
    lon,lat=(location['longitude'],location['latitude']) if location else (-104.8,38.84)
    bbox=f'{lon-.006},{lat-.006},{lon+.006},{lat+.006}'
    roads,elapsed=get('api/roads',bbox=bbox,zoom=16)
    assert roads['features'] and all(feature['geometry']['type'] in ['LineString','MultiLineString'] for feature in roads['features'])
    receipt['road_viewport']={'features':len(roads['features']),'ms':elapsed}
    for attempt in ['cold','cached']:
        geometry,elapsed=get('api/geometry',snapshot=active,level='block',bbox=bbox,zoom=16)
        assert geometry['features']
        receipt['block_viewport_'+attempt]={'features':len(geometry['features']),'ms':elapsed}
    street,elapsed=get('api/street',basis='registration',bbox=bbox,zoom=16)
    assert street['source_sha256']==quality['source_sha256']
    receipt['street_viewport']={'households':street['total_households'],'ms':elapsed}
    if street['households']:
        household,_=get('api/household',id=street['households'][0]['id'],basis='registration')
        detail,_=get('api/person',id=household['people'][0]['id'],basis='registration')
        assert detail['location']['id']==household['id']
    with urlopen(ORIGIN+'/?view=sources',timeout=10) as response:
        assert response.status==200 and b'/assets/' in response.read()
    receipt.update(passed=True,active_snapshot=active,individual_imputation=False)
    (ROOT/'.local/launcher-smoke-results.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps(receipt,indent=2))


if __name__=='__main__':
    main()
