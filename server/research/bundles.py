"""Strict aggregate import boundary. Raw CSVs and per-person features are rejected."""
import hashlib, io, json, math, re, shutil, tempfile, zipfile
from functools import lru_cache
from threading import RLock
from pathlib import Path
from django.conf import settings
from django.db import transaction
from .models import Snapshot, AuditEvent
from tools.measures import MEASURES, MEASURE_RANGES
from tools.geography_levels import BASE_LEVELS, EXTRA_LEVELS, SOURCES as HIERARCHY_SOURCES, pattern as level_pattern, label as area_label
from tools.geography_levels import V4_GEOMETRY_LEVELS, area_label as v4_label, area_pattern as v4_pattern
from tools import area_layers as AL
from .bundle_v4 import validate_v4

FILES={'manifest.json','rows.json','geometry.json','streets.geojson','district.geojson'}
# cu-summary-v4 = v3 + row-framework area indices (area_indices.json) and the adjacency of the area polygons.
FILES_V4=FILES|{'area_indices.json','adjacency.json'}
FILES_FOR={'cu-summary-v1':FILES,'cu-summary-v2':FILES,'cu-summary-v3':FILES,'cu-summary-v4':FILES_V4}
V3_UP=('cu-summary-v3','cu-summary-v4')
V4_MANIFEST_KEYS={'row_framework','area_index_levels','area_index_catalog','area_floors','geometry_method'}
ROW_KEYS={'id','basis','level','label','count','source_count','status','partial','share','coverage'}
MANIFEST_KEYS={'schema','snapshot','label','source_name','source_sha256','source_date','source_date_basis','created_at','encoding','input','bases','threshold','scope','support_likelihood','persistent_voices','geography','vintage_validation','privacy'}
GEO_KEYS={'block_vintage','district_vintage','zcta_vintage','street_vintage','sources','street_method','street_polygons','street_unclosed_parts','street_invalid_parts','bounds','hashes'}
MEASURE_KEYS={'id','label','group','field','aggregation','units','description'}
MEASURE_FIELDS={}
for measure in MEASURES:MEASURE_FIELDS.setdefault(measure['field'],[]).append(measure['id'])

def keys(value, allowed):
    if not isinstance(value,dict) or set(value)!=set(allowed):raise ValueError('Unexpected summary fields')

def text(value, limit=600):
    if not isinstance(value,str) or len(value)>limit or any(ord(c)<32 and c not in '\n\t' for c in value):raise ValueError('Invalid summary text')

def number(value, minimum=0, maximum=1e9, nullable=False):
    if value is None and nullable:return
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not minimum<=value<=maximum:raise ValueError('Invalid summary number')

def coordinates(value, depth=0):
    if depth>5 or not isinstance(value,list):raise ValueError('Invalid geometry coordinates')
    if value and isinstance(value[0],(int,float)):
        if len(value)!=2:raise ValueError('Only longitude/latitude geometry accepted')
        number(value[0],-180,180);number(value[1],-90,90)
    else:
        for x in value:coordinates(x,depth+1)

def polygon(value):
    keys(value,{'type','coordinates'})
    if value['type'] not in ['Polygon','MultiPolygon']:raise ValueError('Individual-point geometry is forbidden')
    coordinates(value['coordinates'])

def validate_measures(row):
    """Validate compact, protected cells against the one approved source catalog."""
    cells=row['measures']
    if not isinstance(cells,dict):raise ValueError('Invalid measure cells')
    if row['status']!='ok':
        if cells:raise ValueError('Measures leaked from a hidden aggregate')
        return
    if set(cells)!={measure['id'] for measure in MEASURES}:raise ValueError('Incomplete measure catalog')
    for measure in MEASURES:
        cell=cells[measure['id']]
        if not isinstance(cell,list) or len(cell)!=5:raise ValueError('Invalid compact measure cell')
        value,valid_count,coverage,percentile,status=cell
        if status not in ['ok','suppressed','unavailable']:raise ValueError('Invalid measure status')
        if status!='ok':
            if any(item is not None for item in cell[:4]):raise ValueError('Hidden measure values leaked')
            continue
        if isinstance(valid_count,bool) or not isinstance(valid_count,int) or not 20<=valid_count<=row['count']:raise ValueError('Invalid measure denominator')
        missing_count=row['count']-valid_count
        if 0<missing_count<20:raise ValueError('Small measure missingness cell leaked')
        number(coverage,0,1)
        if coverage!=valid_count/row['count']:raise ValueError('Inconsistent measure coverage')
        number(percentile,0,100)
        number(value,*MEASURE_RANGES[measure['id']])
        if measure['aggregation'] in ['share_nonzero','share_code']:
            numerator=value*valid_count
            if not math.isclose(numerator,round(numerator),rel_tol=0,abs_tol=1e-7):raise ValueError('Invalid measure numerator')
            if any(0<count<20 for count in [round(numerator),valid_count-round(numerator)]):raise ValueError('Small measure share cell leaked')
    for identifiers in MEASURE_FIELDS.values():
        if len(identifiers)<2:continue
        field_cells=[cells[identifier] for identifier in identifiers]
        if len({(cell[1],cell[2],cell[4]) for cell in field_cells})!=1:raise ValueError('Categorical measures must share their protection and denominator')
        if field_cells[0][4]=='ok' and not math.isclose(sum(cell[0] for cell in field_cells),1,rel_tol=0,abs_tol=1e-9):raise ValueError('Categorical shares must reconcile')

def validate(payload):
    if not isinstance(payload,dict) or not isinstance(payload.get('manifest.json'),dict):raise ValueError('Invalid summary manifest')
    m=payload['manifest.json']
    version=m.get('schema')
    if version not in FILES_FOR or m.get('threshold')!=20:raise ValueError('Unsupported summary schema or suppression threshold')
    keys(payload,FILES_FOR[version])
    measured=version in ('cu-summary-v2','cu-summary-v3','cu-summary-v4')
    levels=BASE_LEVELS+EXTRA_LEVELS if version in V3_UP else BASE_LEVELS
    keys(m,MANIFEST_KEYS|({'measures','measure_methodology'} if measured else set())|({'hierarchy'} if version in V3_UP else set())|(V4_MANIFEST_KEYS if version=='cu-summary-v4' else set()))
    if measured:
        if not isinstance(m['measures'],list):raise ValueError('Invalid measure catalog')
        for measure in m['measures']:keys(measure,MEASURE_KEYS)
        if m['measures']!=MEASURES:raise ValueError('Unapproved measure catalog')
        text(m['measure_methodology'],1200)
    if not re.fullmatch(r'co-cd005-[a-f0-9]{12}',m['snapshot']):raise ValueError('Invalid snapshot key')
    if not re.fullmatch(r'[a-f0-9]{64}',m['source_sha256']):raise ValueError('Invalid source checksum')
    if m['support_likelihood']!='Not measured' or m['persistent_voices']!='Not measured':raise ValueError('Modeled supporter metrics are not part of this app')
    for k in ['label','source_name','source_date','source_date_basis','created_at','encoding','scope','vintage_validation','privacy']:text(m[k],1200)
    keys(m['input'],{'input_rows','column_count','duplicate_rows_collapsed','conflicting_ids_quarantined','accepted_unique_profiles','malformed_width_rows'})
    for v in m['input'].values():number(v)
    keys(m['bases'],{'registration','residential'})
    for basis,b in m['bases'].items():
        keys(b,{'imported','mapped','unplaced','coverage','excluded','zcta_mapped','zcta_unassigned'})
        for k in ['imported','mapped','unplaced','zcta_mapped','zcta_unassigned']:number(b[k])
        if b['mapped']+b['unplaced']!=b['imported']:raise ValueError('Unreconciled source totals')
        if b['imported']!=m['input']['accepted_unique_profiles'] or b['zcta_mapped']+b['zcta_unassigned']!=b['mapped']:raise ValueError('Unreconciled placement totals')
        if b['coverage']!=(b['mapped']/b['imported'] if b['imported'] else None):raise ValueError('Inconsistent coverage')
        number(b['coverage'],0,1,True)
        if not isinstance(b['excluded'],dict) or set(b['excluded'])-{'coarse_precision','out_of_scope','invalid_coordinates','invalid_geography','spatial_conflict','missing_evidence'}:raise ValueError('Unknown exclusion reason')
        for v in b['excluded'].values():
            number(v,nullable=True)
            if v is not None and 0<v<20:raise ValueError('Small exclusion cell must be suppressed')
    g=m['geography'];keys(g,GEO_KEYS)
    for k in ['block_vintage','district_vintage','zcta_vintage','street_vintage','street_method']:text(g[k],2000)
    keys(g['sources'],{'blocks','districts','roads','zcta'})
    for url in g['sources'].values():
        text(url)
        if not url.startswith(('https://www2.census.gov/','https://tigerweb.geo.census.gov/')):raise ValueError('Unexpected geographic source')
    keys(g['hashes'],{'blocks.zip','districts.zip','roads.zip'})
    for v in g['hashes'].values():
        if not re.fullmatch(r'[a-f0-9]{64}',v):raise ValueError('Invalid geographic checksum')
    for k in ['street_polygons','street_unclosed_parts','street_invalid_parts']:number(g[k])
    if len(g['bounds'])!=4:raise ValueError('Invalid extent')
    for n in g['bounds']:number(n,-180,180)
    if version in V3_UP:
        h=m['hierarchy'];keys(h,{'method','sources','hashes','reconciliation','region_definition','assignment'})
        if h['method']!='geographic-extension-1' or h['sources']!=HIERARCHY_SOURCES:raise ValueError('Unapproved geographic extension')
        keys(h['hashes'],HIERARCHY_SOURCES)
        for digest in h['hashes'].values():
            if not isinstance(digest,str) or not re.fullmatch(r'[a-f0-9]{64}',digest):raise ValueError('Invalid hierarchy checksum')
        for field in ('region_definition','assignment'):text(h[field],1000)
        keys(h['reconciliation'],m['bases'])
        for basis,totals in h['reconciliation'].items():
            keys(totals,EXTRA_LEVELS)
            for value in totals.values():
                keys(value,{'assigned','unassigned'})
                for n in value.values():
                    number(n)
                    if not isinstance(n,int):raise ValueError('Assignment totals must be integers')
                if value['assigned']+value['unassigned']!=m['bases'][basis]['mapped']:raise ValueError('Unreconciled hierarchy assignment')
    rows=payload['rows.json']
    if not isinstance(rows,list) or len(rows)>100000:raise ValueError('Invalid aggregate rows')
    seen=set();partitions={}
    for r in rows:
        keys(r,ROW_KEYS|({'measures'} if measured else set()))
        if r['basis'] not in m['bases'] or r['level'] not in levels:raise ValueError('Unsupported aggregate partition')
        if not isinstance(r['id'],str) or not re.fullmatch(level_pattern(r['level']),r['id']):raise ValueError('Only geographic identifiers are accepted')
        ident=(r['basis'],r['level'],r['id'])
        if ident in seen:raise ValueError('Duplicate aggregate key')
        seen.add(ident);partitions.setdefault(ident[:2],[]).append(r)
        # Labels are generated, not user-provided arbitrary strings.
        expected=area_label(r['level'],r['id'])
        if r['label']!=expected or not isinstance(r['partial'],bool):raise ValueError('Unexpected aggregate label')
        if r['status'] not in ['ok','suppressed','unavailable']:raise ValueError('Invalid status')
        for k in ['count','source_count']:number(r[k],nullable=True)
        if any(r[k] is not None and not isinstance(r[k],int) for k in ['count','source_count']):raise ValueError('Profile counts must be integers')
        for k in ['share','coverage']:number(r[k],0,1,True)
        if r['status']!='ok' and any(r[k] is not None for k in ['count','source_count','share','coverage']):raise ValueError('Suppressed values leaked')
        if r['status']=='ok':
            if r['count'] is None or 0<r['count']<20:raise ValueError('Small count must be suppressed')
            source=r['source_count']
            if source is not None and (source<r['count'] or 0<source<20 or 0<source-r['count']<20):raise ValueError('Small source or remainder cell leaked')
            denominator=m['bases'][r['basis']]['zcta_mapped' if r['level']=='zcta' else 'mapped']
            expected_share=r['count']/denominator if denominator else None
            if r['share']!=expected_share:raise ValueError('Inconsistent share')
            if source is not None and r['coverage']!=(r['count']/source if source else None):raise ValueError('Inconsistent area coverage')
            if r['level'] in ('zcta',)+EXTRA_LEVELS and (source is not None or r['coverage'] is not None):raise ValueError('Assignment coverage is unavailable for polygon-assigned levels')
        if measured:validate_measures(r)
    for group in partitions.values():
        if sum(r['status']=='suppressed' for r in group)==1:raise ValueError('Complementary suppression missing')
        if measured:
            for measure in MEASURES:
                hidden=sum(r['status']=='suppressed' or (r['status']=='ok' and r['measures'][measure['id']][4]=='suppressed') for r in group)
                published=sorted([r['measures'][measure['id']] for r in group if r['status']=='ok' and r['measures'][measure['id']][4]=='ok'],key=lambda cell:cell[0])
                if hidden==1 and published:raise ValueError('Complementary measure suppression missing')
                size=len(published);start=0
                while start<size:
                    end=start+1
                    while end<size and published[end][0]==published[start][0]:end+=1
                    expected=100*(start+(end-start)/2)/size
                    if any(not math.isclose(cell[3],expected,rel_tol=1e-9,abs_tol=1e-9) for cell in published[start:end]):raise ValueError('Inconsistent measure percentile')
                    start=end
    geoms=payload['geometry.json'];keys(geoms,(set(levels)-{'postal_zip'})|(set(V4_GEOMETRY_LEVELS) if version=='cu-summary-v4' else set()))
    for level,features in geoms.items():
        if len(features)>100000:raise ValueError('Too many polygons')
        for ident,f in features.items():
            keys(f,{'id','geometry','bbox'})
            if f['id']!=ident or not re.fullmatch(level_pattern(level),ident):raise ValueError('Invalid geometry key')
            polygon(f['geometry'])
            if len(f['bbox'])!=4:raise ValueError('Invalid polygon extent')
            for n in f['bbox']:number(n,-180,180)
    if version in V3_UP:
        for basis in m['bases']:
            for level in EXTRA_LEVELS:
                group=partitions.get((basis,level),[])
                if {r['id'] for r in group}!=set(geoms[level]):raise ValueError('Hierarchy geometry and rows disagree')
                total=sum(r['count'] or 0 for r in group)
                assigned=m['hierarchy']['reconciliation'][basis][level]['assigned']
                if total>assigned or (not any(r['status']=='suppressed' for r in group) and total!=assigned):raise ValueError('Hierarchy published totals do not reconcile')
    for filename in ['streets.geojson','district.geojson']:
        fc=payload[filename];keys(fc,{'type','features'})
        if fc['type']!='FeatureCollection' or len(fc['features'])>100000:raise ValueError('Invalid outline collection')
        for f in fc['features']:
            keys(f,{'type','properties','geometry'});polygon(f['geometry'])
            if f['type']!='Feature':raise ValueError('Invalid feature')
            p=f['properties']
            if filename=='streets.geojson':
                keys(p,{'id'})
                if not re.fullmatch(r'street-[a-f0-9]{16}',p['id']):raise ValueError('Unexpected street identifier')
            elif p!={'id':'0805','label':'Colorado · District 05'}:raise ValueError('Unexpected district label')
    if version=='cu-summary-v4':validate_v4(payload,m)
    return m

def _file_sha256(file):
    """sha256 of a ZIP given as a path or as a file object (the position of a file object is restored)."""
    digest=hashlib.sha256()
    if hasattr(file,'read'):
        position=file.tell();file.seek(0)
        for block in iter(lambda:file.read(1<<20),b''):digest.update(block)
        file.seek(position)
    else:
        with open(file,'rb') as stream:
            for block in iter(lambda:stream.read(1<<20),b''):digest.update(block)
    return digest.hexdigest()

def import_bundle(file, actor='local operator', expected_sha256=None):
    """Validate and atomically activate a summaries-only ZIP. A given sha256 must match the ZIP's bytes (fail closed)."""
    if expected_sha256 is not None:
        if not isinstance(expected_sha256,str) or not re.fullmatch(r'[a-f0-9]{64}',expected_sha256):raise ValueError('Invalid expected checksum')
        if _file_sha256(file)!=expected_sha256:raise ValueError('Bundle checksum does not match the expected sha256')
    with zipfile.ZipFile(file) as archive:
        infos=archive.infolist()
        names=set(archive.namelist())
        if 'manifest.json' not in names or len(infos)!=len(names):raise ValueError('Only the approved summary files are accepted')
        schema=json.loads(archive.read('manifest.json')).get('schema')
        expected=FILES_FOR.get(schema)
        if schema=='cu-summary-v4' and expected_sha256 is None:raise ValueError('A cu-summary-v4 bundle can only be imported with its sha256')
        if expected is None or names!=expected:raise ValueError('Only the %s approved summary files are accepted'%(len(expected) if expected else 'five'))
        limit=(200 if schema=='cu-summary-v4' else 150)*1024*1024
        if sum(x.file_size for x in infos)>limit:raise ValueError('Unpacked summary exceeds %d MB'%(limit>>20))
        payload={name:json.loads(archive.read(name)) for name in expected}
    manifest=validate(payload);key=manifest['snapshot']
    files=FILES_FOR[manifest['schema']]
    root=settings.DATA_DIR/'snapshots';root.mkdir(exist_ok=True)
    target=root/key
    if target.exists():
        existing=json.loads((target/'manifest.json').read_text(encoding='utf-8'))
        if {k:v for k,v in existing.items() if k!='created_at'}!={k:v for k,v in manifest.items() if k!='created_at'}:raise ValueError('An immutable snapshot with that ID already exists')
        for name in files-{'manifest.json'}:
            if json.loads((target/name).read_text(encoding='utf-8'))!=payload[name]:raise ValueError('Immutable snapshot contents cannot be replaced')
        manifest=existing
    else:
        stage=Path(tempfile.mkdtemp(prefix='staging-',dir=root))
        try:
            for name,data in payload.items():(stage/name).write_text(json.dumps(data,separators=(',',':')),encoding='utf-8')
            stage.rename(target)
        finally:
            if stage.exists():shutil.rmtree(stage)
    with transaction.atomic():
        snap,_=Snapshot.objects.get_or_create(key=key,defaults={'manifest':manifest})
        Snapshot.objects.filter(active=True).update(active=False);snap.active=True;snap.save()
        AuditEvent.objects.create(actor=actor,action='snapshot.import',detail={'snapshot':key})
    load.cache_clear()
    if manifest['schema']=='cu-summary-v4':prepare_v4(key)
    return snap

def prepare_v4(key):
    """Build the derived per-level payloads of a v4 snapshot at import so the first request is a lookup (S1)."""
    try:
        from . import v3_cache
        v3_cache.forget(key)
        return v3_cache.prepare(key)
    except Exception:
        import logging
        logging.getLogger('cu.v3').exception('Derived payloads of %s could not be prepared; they build on first use',key)

_lock_guard = RLock()
_file_locks = {}

def _lock_for(key,filename):
    with _lock_guard:
        return _file_locks.setdefault((key,filename),RLock())

@lru_cache(maxsize=14)
def _load_cached(key,filename):
    if filename not in FILES_V4 or not re.fullmatch(r'co-cd005-[a-f0-9]{12}',key):raise ValueError('Invalid snapshot resource')
    return json.loads((settings.DATA_DIR/'snapshots'/key/filename).read_text(encoding='utf-8'))

def load(key,filename):
    # Cold concurrent requests must not parse the same large file repeatedly; different files load in parallel.
    with _lock_for(key,filename):
        return _load_cached(key,filename)

# Derived caches (partition index, summary bodies, v3 payloads, area tables) register here so that load.cache_clear()
# drops everything that was computed from a snapshot file, not only the parsed JSON.
dependents = []

def _clear_all():
    _load_cached.cache_clear()
    for clear in dependents:clear()

load.cache_clear = _clear_all
