"""Local-only profile validation. The output contains protected aggregates only."""
import argparse, collections, csv, hashlib, json, math, re, shutil, sys, zipfile
from datetime import datetime, timezone
from pathlib import Path
import geopandas as gpd
import pandas as pd
import numpy as np
import shapely
from shapely.geometry import mapping, shape
try:
    from . import districts as D
    from .measures import MEASURES, SOURCE_FIELDS, SCHEMA, MEASURE_METHODOLOGY, aggregate_measures, snapshot_key
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools import districts as D
    from measures import MEASURES, SOURCE_FIELDS, SCHEMA, MEASURE_METHODOLOGY, aggregate_measures, snapshot_key

BASES={'registration':'svi_registration','residential':'sii'}
FIELDS=['sii_persist_id','sii_statara_id','svi_registration_congressional_district']+[b+'_'+x for b in BASES.values() for x in ['state','zip','censusid','latitude','longitude','geolevel']]

def read_profiles(path, encoding='cp1252', extra_fields=()):
    fields=list(dict.fromkeys(FIELDS+list(extra_fields)))
    records={};signatures={};secondary={};conflicts=set();duplicates=0;rows=0
    with path.open(encoding=encoding,errors='strict',newline='') as f:
        reader=csv.reader(f,strict=True);header=next(reader)
        if len(header)!=len(set(header)):raise ValueError('Duplicate headers')
        missing=set(fields)-set(header)
        if missing:raise ValueError('Required source columns missing: '+', '.join(sorted(missing)))
        idx=[header.index(x) for x in fields]
        for row in reader:
            rows+=1
            if len(row)!=len(header):raise ValueError(f'Wrong field count at CSV record {rows}')
            data=tuple(row[i].strip() for i in idx);key=data[0];alt=data[1]
            if not key or not alt:conflicts.add(key or f'blank-{rows}');continue
            if alt in secondary and secondary[alt]!=key:conflicts.update([key,secondary[alt]])
            secondary[alt]=key
            signature=hashlib.sha256(json.dumps(row,separators=(',',':'),ensure_ascii=False).encode('utf-8')).digest()
            if key in records:
                if signatures[key]==signature:duplicates+=1
                else:conflicts.add(key)
            else:records[key]=data;signatures[key]=signature
    for key in conflicts:records.pop(key,None)
    return pd.DataFrame(records.values(),columns=fields),{'input_rows':rows,'column_count':len(header),'duplicate_rows_collapsed':duplicates,'conflicting_ids_quarantined':len(conflicts),'accepted_unique_profiles':len(records),'malformed_width_rows':0}

def canonical_blocks(df, d):
    """Block ids of both bases in the district's block file vintage, in place (Connecticut: 282 voter ids carry the old
    county codes; the file holds an alias row for each, so one polygon would become two areas). No-op elsewhere."""
    aliases = D.block_aliases(d)
    if aliases:
        for prefix in BASES.values(): df[prefix + '_censusid'] = df[prefix + '_censusid'].replace(aliases)
    return df

def polygonal(geom):
    """A valid geometry: a valid one is returned as is; an invalid one is made valid and keeps its polygonal parts only."""
    if geom.is_valid: return geom
    fixed = shapely.make_valid(geom)
    if fixed.geom_type in ('Polygon', 'MultiPolygon'): return fixed
    return shapely.union_all([g for g in shapely.get_parts(fixed) if g.geom_type in ('Polygon', 'MultiPolygon')])

def source_date(name):
    """'2026-09-04' from a source file name ending _20260904.csv."""
    found = re.search(r'_(\d{4})(\d{2})(\d{2})\.csv$', name)
    if not found: raise ValueError('Source file name carries no _YYYYMMDD date: ' + name)
    return '-'.join(found.groups())

def classify(df, prefix, block_lookup, district, state='CO', cd='005'):
    geoid=df[prefix+'_censusid'];lat=pd.to_numeric(df[prefix+'_latitude'],errors='coerce');lon=pd.to_numeric(df[prefix+'_longitude'],errors='coerce')
    coords=lat.between(-90,90)&lon.between(-180,180)&~(lat.eq(0)&lon.eq(0))
    points=shapely.points(lon.fillna(0).to_numpy(),lat.fillna(0).to_numpy())
    geoms=np.empty(len(df),dtype=object);geoms[:]=[block_lookup.get(g) for g in geoid]
    known=np.array([g is not None for g in geoms])
    covered=shapely.covers(geoms,points)
    in_scope=shapely.covers(district,points)&df[prefix+'_state'].eq(state).to_numpy()&df['svi_registration_congressional_district'].eq(cd).to_numpy()
    status=np.full(len(df),'mapped',dtype=object)
    status[~covered]='spatial_conflict'
    status[~known | ~geoid.str.fullmatch(r'\d{15}').to_numpy()]='invalid_geography'
    status[df[prefix+'_geolevel'].ne('ROOFTOP').to_numpy()]='coarse_precision'
    status[~in_scope]='out_of_scope'
    status[~coords.to_numpy()]='invalid_coordinates'
    missing=df[[prefix+'_censusid',prefix+'_state',prefix+'_latitude',prefix+'_longitude',prefix+'_geolevel']].eq('').any(axis=1).to_numpy()
    status[missing]='missing_evidence'
    return status,points,known,covered

def settle(rows, total):
    """Status and shares of one partition. Every area with records is shown, however few (nothing is withheld, Paul 2026-10-08);
    an area with no source record is 'unavailable'."""
    for row in rows:
        if row['source_count']==0:
            row.update(count=None,source_count=None,share=None,coverage=None,status='unavailable')
        else:
            row['status']='ok'
            row['share']=row['count']/total if total else None
            row['coverage']=row['count']/row['source_count']
    return rows

def feature(geoid,geom):
    return {'id':geoid,'geometry':mapping(geom),'bbox':list(geom.bounds)}

def ingest(csv_path, geo, output, encoding='cp1252', d=None):
    d=d or D.current()
    output.mkdir(parents=True,exist_ok=True)
    source_hash=hashlib.file_digest(csv_path.open('rb'),'sha256').hexdigest()
    df,receipt=read_profiles(csv_path,encoding,extra_fields=SOURCE_FIELDS)
    canonical_blocks(df,d)
    print(f'Parsed {receipt["input_rows"]:,} source records; validating geometry',flush=True)
    blocks=gpd.read_file(geo/'blocks.gpkg').set_index('geoid')
    lookup=blocks.geometry.to_dict();district=shape(json.loads((geo/'district.geojson').read_text())['features'][0]['geometry'])
    zcta=gpd.read_file(geo/'zcta.geojson');zcta['geoid']=zcta.geoid.astype(str)
    zcta=zcta[zcta.geoid.str.fullmatch(r'\d{5}')].copy();zcta['geometry']=[polygonal(g) for g in zcta.geometry]
    zlookup=dict(zip(zcta.geoid,zcta.geometry));ztree=shapely.STRtree(zcta.geometry.values)
    all_rows=[];summaries={};geometry={'block':{},'zcta':{}};local_receipt={}
    for basis,prefix in BASES.items():
        status,points,known,covered=classify(df,prefix,lookup,district,d['state'],d['cd'])
        mapped=status=='mapped';total=int(mapped.sum());counts=collections.Counter(status)
        reason_rows=[{'id':k,'count':int(v),'source_count':int(v)} for k,v in counts.items() if k!='mapped']
        settle(reason_rows,len(df)-total)
        summaries[basis]={'imported':len(df),'mapped':total,'unplaced':len(df)-total,'coverage':total/len(df) if len(df) else None,'excluded':{r['id']:r['count'] for r in reason_rows}}
        geoids=df[prefix+'_censusid'];zips=df[prefix+'_zip']
        zctaid=np.full(len(df),'',dtype=object)
        pairs=ztree.query(points[mapped],predicate='intersects')
        eligible_indices=np.flatnonzero(mapped)
        assigned=set()
        for pi,zi in zip(*pairs):
            original=eligible_indices[pi]
            # Ambiguous ZCTA boundary points remain unavailable, never counted twice.
            if original in assigned:zctaid[original]='ambiguous';continue
            assigned.add(original);zctaid[original]=str(zcta.iloc[zi].geoid)
        zvalid=mapped&~np.isin(zctaid,['','ambiguous'])
        summaries[basis]['zcta_mapped']=int(zvalid.sum())
        summaries[basis]['zcta_unassigned']=int((mapped&~zvalid).sum())
        local_receipt[basis]={'source_census_ids':int(geoids.nunique()),'source_postal_zips':int(zips.nunique()),'known_2020_ids':int(known.sum()),'coordinate_agreement_all_precisions':int(covered.sum()),'precision_counts':{str(k):int(v) for k,v in df[prefix+'_geolevel'].value_counts().items()},'outcomes':{str(k):int(v) for k,v in counts.items()}}
        for level,keys,elig,total_level in [('block',geoids,mapped,total),('postal_zip',zips,mapped,total),('zcta',pd.Series(zctaid),zvalid,int(zvalid.sum()))]:
            valid_keys=keys.str.fullmatch(r'\d{15}' if level=='block' else r'\d{5}')
            source_counts=keys[valid_keys].value_counts().to_dict();mapped_counts=keys[valid_keys&elig].value_counts().to_dict()
            rows=[]
            for key,source_n in source_counts.items():
                n=int(mapped_counts.get(key,0));geom=lookup.get(key) if level=='block' else zlookup.get(key) if level=='zcta' else None
                partial=not district.covers(geom) if geom is not None else True
                rows.append({'id':key,'basis':basis,'level':level,'label':('Block '+key[-4:]+' · '+key[:11]) if level=='block' else ('Postal ZIP '+key if level=='postal_zip' else 'ZCTA '+key),'count':n,'source_count':int(source_n),'status':'ok','partial':bool(partial),'share':None,'coverage':None})
                if geom is not None and level in geometry and geom.intersects(district):
                    geometry[level][key]=feature(key,geom.simplify(0.000005 if level=='block' else 0.00003,preserve_topology=True))
            settle(rows,total_level)
            aggregate_measures(df,keys,valid_keys&elig,rows)
            if level=='zcta':
                for row in rows:row['source_count']=None;row['coverage']=None
            all_rows.extend(rows)
    provenance=json.loads((geo/'provenance.json').read_text())
    key=snapshot_key(source_hash,d['snapshot_prefix'])
    manifest={'schema':SCHEMA,'snapshot':key,'label':d['label'],'source_name':csv_path.name,'source_sha256':source_hash,'source_date':source_date(csv_path.name),'source_date_basis':'Filename; not a verified date for every underlying field','created_at':datetime.now(timezone.utc).isoformat(),'encoding':encoding,'input':receipt,'bases':summaries,'scope':'%s CD-%s source-file extract; does not establish complete electorate coverage'%(d['state_name'],d['cd']),'support_likelihood':'Not measured','persistent_voices':'Not measured','geography':provenance,'vintage_validation':'Vendor Census vintage is unspecified. Accepted fine-grained records match a 2020 Census identifier and their coordinate is covered by that exact official polygon.','privacy':'Fixed summary partitions; no individual records, arbitrary cohort filters, or cross-layer intersection totals. Every area and cell is shown, however small (nothing is withheld).','measures':MEASURES,'measure_methodology':MEASURE_METHODOLOGY}
    files={'manifest.json':manifest,'rows.json':all_rows,'geometry.json':geometry,'streets.geojson':json.loads((geo/'streets.geojson').read_text()),'district.geojson':json.loads((geo/'district.geojson').read_text())}
    for name,data in files.items():(output/name).write_text(json.dumps(data,separators=(',',':'),allow_nan=False),encoding='utf-8')
    # Local receipt contains only diagnostics, never identifiers or individual coordinates.
    (geo/'ingest_receipt.json').write_text(json.dumps({'source_sha256':source_hash,'input':receipt,'bases':local_receipt},indent=2))
    archive=output.parent/(key+'.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for name in files:z.write(output/name,name)
    print(json.dumps({'snapshot':key,'rows':len(all_rows),'summaries':summaries,'bundle_bytes':archive.stat().st_size,'bundle':str(archive)}),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--csv',required=True);p.add_argument('--geography',required=True);p.add_argument('--output',required=True);p.add_argument('--encoding',default='cp1252');a=p.parse_args();ingest(Path(a.csv),Path(a.geography),Path(a.output),a.encoding)
