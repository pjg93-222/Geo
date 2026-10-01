"""Create a new immutable aggregate snapshot from validated source locations.

Existing block/ZCTA summaries are preserved. New levels are independently
aggregated from the source, never from already suppressed child cells.
Only public Census boundary downloads leave this machine.
"""
import argparse, hashlib, json, zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import shape
from tools.geography_levels import SOURCES, EXTRA_LEVELS, label
from tools.ingest import BASES, read_profiles, classify, protect, feature
from tools.measures import SOURCE_FIELDS, aggregate_measures
from tools.prepare_geography import download

METHOD = 'geographic-extension-1'

def assign(points, eligible, features):
    """Unique polygon membership only; shared-edge matches stay unassigned."""
    identifiers=list(features)
    positions=np.flatnonzero(eligible)
    keys=np.full(len(points),'',dtype=object)
    if not identifiers or not len(positions): return pd.Series(keys)
    tree=shapely.STRtree(list(features.values()))
    pi,gi=tree.query(points[positions],predicate='intersects')
    counts=np.bincount(pi,minlength=len(positions))
    unique=counts[pi]==1
    keys[positions[pi[unique]]]=np.asarray(identifiers,dtype=object)[gi[unique]]
    return pd.Series(keys)

def build(csv, base, geo, output):
    output.mkdir(parents=True,exist_ok=True)
    payload={p.name:json.loads(p.read_text(encoding='utf-8')) for p in base.glob('*.json') if p.name in {'manifest.json','rows.json','geometry.json','streets.geojson','district.geojson'}}
    for name in ('streets.geojson','district.geojson'):
        payload[name]=json.loads((base/name).read_text(encoding='utf-8'))
    manifest=payload['manifest.json']
    if manifest['schema']!='cu-summary-v2':raise ValueError('Use the established version-2 source snapshot')
    checksum=hashlib.file_digest(csv.open('rb'),'sha256').hexdigest()
    if checksum!=manifest['source_sha256']:raise ValueError('Source does not match the base snapshot')
    extension=geo/'hierarchy';extension.mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs=[pool.submit(download,url,extension/(level+'.zip')) for level,url in SOURCES.items()]
        for job in jobs:job.result()
    district=shape(payload['district.geojson']['features'][0]['geometry'])
    boundaries={}
    for level in SOURCES:
        frame=gpd.read_file(extension/(level+'.zip')).to_crs(4326)
        frame=frame[frame.STATEFP=='08']
        boundaries[level]={str(row.GEOID):row.geometry for row in frame.itertuples() if row.geometry.intersection(district).area>1e-10}
        if any(geom.is_empty or not geom.is_valid for geom in boundaries[level].values()):raise ValueError('Invalid official boundary')
    if set(boundaries['county'])!={'08041'}:raise ValueError('Unexpected county scope: review the approved pilot catalog')
    boundaries['congressional']={'0805':district}
    boundaries['region']={'0805':district}
    frame,receipt=read_profiles(csv,extra_fields=SOURCE_FIELDS)
    if receipt!=manifest['input']:raise ValueError('Source input receipt changed')
    print('Validated source identity; assigning county and legislative polygons',flush=True)
    blocks=gpd.read_file(geo/'blocks.gpkg').set_index('geoid').geometry.to_dict()
    reconciliation={}
    for basis,prefix in BASES.items():
        status,points,_,_=classify(frame,prefix,blocks,district)
        mapped=status=='mapped';total=int(mapped.sum())
        if total!=manifest['bases'][basis]['mapped']:raise ValueError('Mapped-source baseline changed')
        reconciliation[basis]={}
        for level in EXTRA_LEVELS:
            keys=assign(points,mapped,boundaries[level]);eligible=mapped&keys.ne('').to_numpy()
            counts=keys[eligible].value_counts().to_dict()
            assigned=int(eligible.sum())
            reconciliation[basis][level]={'assigned':assigned,'unassigned':total-assigned}
            rows=[{'id':ident,'basis':basis,'level':level,'label':label(level,ident),'count':int(counts.get(ident,0)),
                   'source_count':int(counts.get(ident,0)),'status':'ok','partial':not district.covers(geom),
                   'share':None,'coverage':None} for ident,geom in boundaries[level].items()]
            protect(rows,total);aggregate_measures(frame,keys,eligible,rows)
            for row in rows:row['source_count']=None;row['coverage']=None
            payload['rows.json'].extend(rows)
            print(f'{basis} / {level}: {len(rows)} areas, {assigned:,} assigned, {total-assigned:,} unassigned',flush=True)
    for level,features in boundaries.items():
        payload['geometry.json'][level]={ident:feature(ident,geom) for ident,geom in features.items()}
    hashes={level:hashlib.file_digest((extension/(level+'.zip')).open('rb'),'sha256').hexdigest() for level in SOURCES}
    hierarchy={'method':METHOD,'sources':SOURCES,'hashes':hashes,'reconciliation':reconciliation,
               'region_definition':'The supplied Colorado CD-05 study region; not statewide or national coverage.',
               'assignment':'Validated source coordinates intersect exactly one official polygon. Ambiguous boundary matches are unassigned. Legislative layers overlap and are not nested parents.'}
    digest=hashlib.sha256((manifest['snapshot']+json.dumps(hierarchy,sort_keys=True)).encode()).hexdigest()[:12]
    manifest.update(schema='cu-summary-v3',snapshot='co-cd005-'+digest,created_at=datetime.now(timezone.utc).isoformat(),hierarchy=hierarchy)
    if hashlib.file_digest(csv.open('rb'),'sha256').hexdigest()!=checksum:raise ValueError('Source changed during aggregation')
    for name,data in payload.items():(output/name).write_text(json.dumps(data,separators=(',',':'),allow_nan=False),encoding='utf-8')
    archive=output.parent/(manifest['snapshot']+'.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as bundle:
        for name in payload:bundle.write(output/name,name)
    print(json.dumps({'snapshot':manifest['snapshot'],'bundle':str(archive),'source_unchanged':True,'reconciliation':reconciliation}),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--csv',type=Path,required=True);p.add_argument('--base',type=Path,required=True);p.add_argument('--geography',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();build(a.csv,a.base,a.geography,a.output)
