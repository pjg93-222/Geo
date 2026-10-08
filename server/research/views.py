import csv, hashlib, io, json, mimetypes, re, secrets, time, zipfile
from datetime import timedelta
from functools import wraps, lru_cache
from pathlib import Path
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse, HttpResponse, FileResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .models import Snapshot, SavedView, Invitation, LoginAttempt, AuditEvent
from .bundles import load, import_bundle
from . import static_files, v3_cache
from .v3_http import blob_response
from .terrier_contacts import csv_value
from tools.measures import MEASURES
from tools.geography_levels import LEVELS, EXTRA_LEVELS, POLYGON_LEVELS, available as _available_levels
from tools.area_indices import CATALOG as INDEX_CATALOG, BY_ID as INDEX_BY_ID, METHOD as INDEX_METHOD, reference as index_reference, summarize as index_summaries, evaluate as index_evaluate

MEASURE_BY_ID={measure['id']:measure for measure in MEASURES}
def available_levels(schema):return _available_levels(schema,settings.DISTRICT)
LEGACY_METRICS={'count','share','coverage'}
METRIC_IDS=LEGACY_METRICS|set(MEASURE_BY_ID)|set(INDEX_BY_ID)

def body(request):
    try:
        value=json.loads(request.body or b'{}')
        if not isinstance(value,dict):raise ValueError()
        return value
    except (ValueError,UnicodeDecodeError):raise ValueError('Expected a JSON object')

def protected(admin=False):
    def deco(fn):
        @wraps(fn)
        def view(request,*args,**kwargs):
            if not request.user.is_authenticated:return JsonResponse({'error':'Sign in to continue'},status=401)
            if admin and not request.user.is_staff:return JsonResponse({'error':'Administrator access required'},status=403)
            try:return fn(request,*args,**kwargs)
            except (ValueError,KeyError,TypeError,zipfile.BadZipFile,UnicodeDecodeError):return JsonResponse({'error':'The request or summary bundle is invalid. Check its format and try again.'},status=400)
        return view
    return deco

def who(request):
    return JsonResponse({'authenticated':request.user.is_authenticated,'username':request.user.username if request.user.is_authenticated else None,'administrator':request.user.is_staff if request.user.is_authenticated else False,'local_admin_access':getattr(request,'local_admin_access',False),'csrf':get_token(request)})

@require_http_methods(['POST'])
def signin(request):
    try:data=body(request)
    except ValueError:return JsonResponse({'error':'Invalid sign-in request'},status=400)
    username=str(data.get('username',''))[:150];address=request.META.get('REMOTE_ADDR','unknown')
    # Independently limit source IP and account name; rotating usernames cannot bypass IP limits.
    names=['ip:'+address,'user:'+username.casefold()]
    limit=timezone.now()-timedelta(minutes=15)
    attempts=[]
    for name in names:
        key=hashlib.sha256(name.encode()).hexdigest()
        attempt,_=LoginAttempt.objects.get_or_create(key=key,defaults={'window_start':timezone.now()})
        if attempt.window_start<limit:attempt.failures=0;attempt.window_start=timezone.now();attempt.save()
        attempts.append(attempt)
    if any(a.failures>=8 for a in attempts):return JsonResponse({'error':'Too many attempts. Try again in 15 minutes.'},status=429)
    user=authenticate(request,username=username,password=data.get('password',''))
    if not user:
        from django.db.models import F
        LoginAttempt.objects.filter(pk__in=[a.pk for a in attempts]).update(failures=F('failures')+1)
        return JsonResponse({'error':'The username or password is incorrect.'},status=401)
    LoginAttempt.objects.filter(pk__in=[a.pk for a in attempts]).update(failures=0)
    login(request,user)
    return who(request)

@require_http_methods(['POST'])
def signout(request):
    logout(request);return JsonResponse({'ok':True})

@require_http_methods(['POST'])
def accept_invitation(request):
    try:
        data=body(request);token=str(data.get('token',''))
        if not re.fullmatch(r'[A-Za-z0-9_-]{40,100}',token):raise ValueError()
        with transaction.atomic():
            invite=Invitation.objects.select_for_update().filter(token_hash=hashlib.sha256(token.encode()).hexdigest(),used_at=None,expires_at__gt=timezone.now()).first()
            if not invite:return JsonResponse({'error':'This invitation has expired or was already used. Ask an administrator for a new link.'},status=400)
            user=User.objects.filter(username=invite.username).first()
            if user and not invite.reset:return JsonResponse({'error':'An account with that name already exists.'},status=400)
            if not user:user=User(username=invite.username,is_staff=invite.administrator)
            validate_password(data.get('password',''),user)
            user.set_password(data['password']);user.is_active=True;user.save()
            invite.used_at=timezone.now();invite.save()
            AuditEvent.objects.create(actor=user.username,action='account.reset' if invite.reset else 'account.activate')
        login(request,user);return who(request)
    except ValidationError as e:return JsonResponse({'error':' '.join(e.messages)},status=400)
    except (ValueError,KeyError,TypeError):return JsonResponse({'error':'Invalid invitation request'},status=400)

def create_invitation(username,administrator=False,reset=False):
    if not re.fullmatch(r'[A-Za-z0-9_.@+-]{1,150}',username):raise ValueError('Invalid username')
    if User.objects.filter(username=username).exists()!=reset:raise ValueError('Use reset for an existing account, invitation for a new account')
    Invitation.objects.filter(username=username,used_at=None).update(expires_at=timezone.now())
    token=secrets.token_urlsafe(36)
    Invitation.objects.create(token_hash=hashlib.sha256(token.encode()).hexdigest(),username=username,administrator=administrator,reset=reset,expires_at=timezone.now()+timedelta(hours=24))
    return '/?invite='+token

@protected(admin=True)
@require_http_methods(['GET','POST'])
def accounts(request):
    if request.method=='GET':return JsonResponse({'users':list(User.objects.values('id','username','is_staff','is_active'))})
    data=body(request);action=data.get('action','invite')
    if action in ['invite','reset']:
        link=create_invitation(data['username'],bool(data.get('administrator',False)),action=='reset')
        AuditEvent.objects.create(actor=request.user.username,action='account.'+action,detail={'username':data['username']})
        return JsonResponse({'link':link,'expires_in_hours':24})
    if action=='deactivate':
        target=User.objects.filter(username=data['username']).first()
        if not target:raise ValueError('Unknown account')
        if target.pk==request.user.pk:raise ValueError('Cannot deactivate yourself')
        target.is_active=False;target.save();return JsonResponse({'ok':True})
    raise ValueError('Unknown account action')

def snapshot_for(request):
    key=request.GET.get('snapshot')
    return Snapshot.objects.filter(key=key).first() if key else Snapshot.objects.filter(active=True).first()

@protected()
def snapshots(request):
    return JsonResponse({'snapshots':[{'key':s.key,'active':s.active,'manifest':{**s.manifest,
        'levels':available_levels(s.manifest['schema']),
        'composites':INDEX_CATALOG if s.manifest['schema']!='cu-summary-v1' else [],'index_method':INDEX_METHOD}}
        for s in Snapshot.objects.order_by('-created_at')]})

@protected(admin=True)
@require_http_methods(['POST'])
def upload(request):
    file=request.FILES.get('bundle')
    if not file or file.size>50*1024*1024:return JsonResponse({'error':'Choose a summary ZIP bundle under 50 MB. Raw CSV files are not accepted.'},status=400)
    snap=import_bundle(file,request.user.username,request.POST.get('sha256') or None);return JsonResponse({'snapshot':snap.key})

@protected(admin=True)
@require_http_methods(['POST'])
def activate(request):
    key=body(request)['snapshot']
    with transaction.atomic():
        snap=Snapshot.objects.filter(key=key).first()
        if not snap:raise ValueError()
        Snapshot.objects.filter(active=True).update(active=False);snap.active=True;snap.save()
        AuditEvent.objects.create(actor=request.user.username,action='snapshot.activate',detail={'snapshot':key})
    return JsonResponse({'ok':True})

def selection(request):
    basis=request.GET.get('basis','registration');level=request.GET.get('level','zcta');metric=request.GET.get('metric','count');scale=request.GET.get('scale','raw')
    if basis not in ['registration','residential'] or level not in LEVELS or metric not in METRIC_IDS or scale not in ['raw','percentile']:raise ValueError()
    return basis,level,metric,scale

def selected_rows(rows,metric):
    """Publish one selected measure, never the complete nested measure matrix."""
    selected=[]
    for source in rows:
        row={key:value for key,value in source.items() if key!='measures'}
        if metric in MEASURE_BY_ID:
            cell=source.get('measures',{}).get(metric,[None,None,None,None,'suppressed' if source['status']=='suppressed' else 'unavailable'])
            row.update(zip(['value','valid_count','field_coverage','index','measure_status'],cell))
        else:
            value=source[metric]
            status=source['status'] if source['status']!='ok' or value is not None else 'unavailable'
            row.update(value=value,index=None,valid_count=source['count'] if status=='ok' else None,field_coverage=source['coverage'] if status=='ok' else None,measure_status=status)
        selected.append(row)
    if metric in LEGACY_METRICS:
        # Rank only publishable values in this address-basis/geography partition.
        ordered=sorted([row for row in selected if row['measure_status']=='ok'],key=lambda row:row['value'])
        size=len(ordered);start=0
        while start<size:
            end=start+1
            while end<size and ordered[end]['value']==ordered[start]['value']:end+=1
            percentile=100*(start+(end-start)/2)/size
            for row in ordered[start:end]:row['index']=percentile
            start=end
    return selected

@lru_cache(maxsize=8)
def reference_for(key,basis):
    return index_reference(partition_rows(key,basis,'zcta'))

@lru_cache(maxsize=96)
def composite_rows(key,basis,level,metric):
    return index_summaries(partition_rows(key,basis,level),metric,reference_for(key,basis))

@protected()
def index_detail(request):
    snap=snapshot_for(request)
    basis,level,metric,_=selection(request)
    if not snap or metric not in INDEX_BY_ID:raise ValueError('Select a combined area index')
    ident=request.GET.get('id')
    source=next((row for row in partition_rows(snap.key,basis,level) if row['id']==ident),None)
    if source is None:return JsonResponse({'error':'Area not found'},status=404)
    return JsonResponse({'snapshot':snap.key,'basis':basis,'level':level,'metric':metric,'id':ident,
                         **index_evaluate(source,metric,reference_for(snap.key,basis),detail=True)})

_summary_blobs={}
_partitions={}

def partition_rows(key,basis,level):
    """Rows of one (basis, level) partition. The index is rebuilt whenever load() hands back a different rows object,
    so it can never outlive the data it was built from (a re-import, a test that patches load)."""
    rows=load(key,'rows.json');entry=_partitions.get(key)
    if entry is None or entry[0] is not rows:
        index={}
        for row in rows:index.setdefault((row['basis'],row['level']),[]).append(row)
        if len(_partitions)>=6:_partitions.clear()
        entry=_partitions[key]=(rows,index)
    return entry[1].get((basis,level),[])

import server.research.bundles as _bundles
_bundles.dependents.extend([_partitions.clear,_summary_blobs.clear,v3_cache.forget])

@protected()
def summaries(request):
    snap=snapshot_for(request)
    if not snap:return JsonResponse({'error':'No summary snapshot has been imported'},status=404)
    basis,level,metric,scale=selection(request)
    if level not in {item['id'] for item in available_levels(snap.manifest['schema'])}:raise ValueError('This snapshot does not include that geographic layer')
    memo=(snap.key,basis,level,metric,scale);source=load(snap.key,'rows.json')
    if request.GET.get('format')!='csv':
        cached=_summary_blobs.get(memo)
        if cached is not None and cached[0] is source:return blob_response(request,cached[1],'private, no-cache')
    rows=composite_rows(snap.key,basis,level,metric) if metric in INDEX_BY_ID else selected_rows(partition_rows(snap.key,basis,level),metric)
    base=snap.manifest['bases'][basis];denom=base['zcta_mapped'] if level=='zcta' else base['mapped']
    vintage=next(item['vintage'] for item in available_levels(snap.manifest['schema']) if item['id']==level)
    meta={'snapshot':snap.key,'basis':basis,'level':level,'metric':metric,'scale':scale,'measure':MEASURE_BY_ID.get(metric) or next((item for item in INDEX_CATALOG if item['id']==metric),None),'measure_methodology':snap.manifest.get('measure_methodology',''),'denominator':denom,'source_date':snap.manifest['source_date'],'boundary_vintage':vintage,'metric_status':'unavailable' if (level not in ('block','postal_zip') and metric=='coverage') or (metric in set(MEASURE_BY_ID)|set(INDEX_BY_ID) and snap.manifest['schema']=='cu-summary-v1') else 'available','coverage':base['coverage'],'provenance':snap.manifest['vintage_validation'],
        'index_method':INDEX_METHOD if metric in INDEX_BY_ID else None,'assignment':snap.manifest.get('hierarchy',{}).get('reconciliation',{}).get(basis,{}).get(level)}
    if request.GET.get('format')=='csv':
        buff=io.StringIO(newline='');w=csv.writer(buff);w.writerow(['snapshot','basis','level','geographic_id','metric','scale','value','index','valid_count','field_coverage','measure_status','count','share','coverage','source_count','status','partial','denominator','source_date','boundary_vintage','component_coverage','components_used','components_total'])
        # Free text such as source_date must not open as a formula; numbers, negatives included, are written as they are.
        for r in rows:w.writerow([csv_value(v) if isinstance(v,str) else v for v in [snap.key,basis,level,r['id'],metric,scale,r['value'],r['index'],r['valid_count'],r['field_coverage'],r['measure_status'],r['count'],r['share'],r['coverage'],r['source_count'],r['status'],r['partial'],denom,meta['source_date'],meta['boundary_vintage'],r.get('component_coverage'),r.get('components_used'),r.get('components_total')]])
        response=HttpResponse(buff.getvalue(),content_type='text/csv; charset=utf-8');response['Content-Disposition']=f'attachment; filename="{snap.key}-{basis}-{level}-{metric}-{scale}.csv"';return response
    blob=v3_cache.Blob(json.dumps({'meta':meta,'rows':rows},separators=(',',':'),allow_nan=False).encode('utf-8'))
    if len(_summary_blobs)>=256:_summary_blobs.clear()
    _summary_blobs[memo]=(source,blob)
    return blob_response(request,blob,'private, no-cache')

def intersects(bounds,box):return bounds[0]<=box[2] and bounds[2]>=box[0] and bounds[1]<=box[3] and bounds[3]>=box[1]

@lru_cache(maxsize=12)
def geometry_entries(key,level):
    if level!='streets':return load(key,'geometry.json')[level]
    from shapely.geometry import shape
    return {feature['properties']['id']:{'id':feature['properties']['id'],'geometry':feature['geometry'],
            'bbox':shape(feature['geometry']).bounds} for feature in load(key,'streets.geojson')['features']}

@lru_cache(maxsize=24000)
def viewport_feature(key,level,detail,ident):
    from shapely.geometry import shape,mapping
    entry=geometry_entries(key,level)[ident]
    tolerance={0:0.00008,1:0.00002,2:0}[detail]
    # Exact street-scale geometry needs no Shapely conversion at all.
    geometry=entry['geometry'] if not tolerance else mapping(shape(entry['geometry']).simplify(tolerance,preserve_topology=True))
    return {'type':'Feature','id':ident,'properties':{'id':ident},'geometry':geometry}

@lru_cache(maxsize=24)
def display_geometry(key,level,detail):
    from shapely.geometry import shape, mapping
    tolerance={0:0.00008,1:0.00002,2:0}[detail]
    if level=='streets':
        entries=[{'id':f['properties']['id'],'geometry':f['geometry']} for f in load(key,'streets.geojson')['features']]
    else:entries=load(key,'geometry.json')[level].values()
    result=[]
    for entry in entries:
        geom=shape(entry['geometry'])
        display=geom.simplify(tolerance,preserve_topology=True) if tolerance else geom
        result.append((geom.bounds,{'type':'Feature','id':entry['id'],'properties':{'id':entry['id']},'geometry':mapping(display)}))
    return result

@protected()
def geometry(request):
    snap=snapshot_for(request)
    if not snap:return JsonResponse({'error':'No snapshot'},status=404)
    level=request.GET.get('level','zcta')
    if level=='district':return JsonResponse(load(snap.key,'district.geojson'))
    bbox=None
    if request.GET.get('bbox'):
        bbox=[float(x) for x in request.GET['bbox'].split(',')]
        if len(bbox)!=4 or not (-180<=bbox[0]<bbox[2]<=180 and -90<=bbox[1]<bbox[3]<=90):raise ValueError()
    zoom=float(request.GET.get('zoom',14))
    if not 0<=zoom<=22:raise ValueError()
    if level=='streets' or level in POLYGON_LEVELS:
        if level!='streets' and level not in {item['id'] for item in available_levels(snap.manifest['schema'])}:raise ValueError('Layer unavailable in this snapshot')
        detail=0 if zoom<11 else 1 if zoom<14 else 2
        entries=geometry_entries(snap.key,level)
        selected=[ident for ident,entry in entries.items() if bbox is None or intersects(entry['bbox'],bbox)]
        if len(selected)>15000:return JsonResponse({'error':'Zoom in to display this detail layer.'},status=422)
        features=[viewport_feature(snap.key,level,detail,ident) for ident in selected]
    else:raise ValueError()
    if len(features)>15000:return JsonResponse({'error':'Zoom in to display this detail layer.'},status=422)
    response=JsonResponse({'type':'FeatureCollection','features':features})
    response['Cache-Control']='private, max-age=3600';return response

@protected()
def criteria(request):
    if request.GET.get('format')=='csv':
        response=FileResponse((settings.BASE_DIR/'data'/'field_dictionary.csv').open('rb'),content_type='text/csv');response['Content-Disposition']='attachment; filename="field_dictionary.csv"';return response
    return JsonResponse(json.loads((settings.BASE_DIR/'data'/'criteria.json').read_text(encoding='utf-8')))

@protected()
@require_http_methods(['GET','POST','DELETE'])
def saved_views(request):
    if request.method=='GET':
        views=list(SavedView.objects.filter(owner=request.user).values('id','name','config'))
        for view in views:view['config'].setdefault('scale','raw')
        return JsonResponse({'views':views})
    data=body(request)
    if request.method=='DELETE':SavedView.objects.filter(owner=request.user,pk=data.get('id')).delete();return JsonResponse({'ok':True})
    config=data.get('config',{})
    if not isinstance(config,dict) or set(config)-{'streetOrder','scale'}!={'basis','level','metric','snapshot','center','zoom','streets','opacity','outline'}:raise ValueError()
    if config['basis'] not in ['registration','residential'] or config['level'] not in LEVELS or config['metric'] not in METRIC_IDS:raise ValueError()
    if config.get('scale','raw') not in ['raw','percentile']:raise ValueError()
    config.setdefault('scale','raw')
    saved_snapshot=Snapshot.objects.filter(key=config['snapshot']).first()
    if not saved_snapshot or config['level'] not in {item['id'] for item in available_levels(saved_snapshot.manifest['schema'])}:raise ValueError()
    if config.get('streetOrder','above') not in ['above','below']:raise ValueError()
    if not isinstance(config['center'],list) or len(config['center'])!=2:raise ValueError()
    from .bundles import number
    number(config['center'][0],-180,180);number(config['center'][1],-90,90);number(config['zoom'],0,22);number(config['opacity'],0,1)
    if not isinstance(config['streets'],bool) or not isinstance(config['outline'],bool):raise ValueError()
    name=str(data.get('name','')).strip()
    if not 1<=len(name)<=100:raise ValueError()
    view=SavedView.objects.create(owner=request.user,name=name,config=config)
    return JsonResponse({'id':view.pk})

def health(request):
    try:snap=Snapshot.objects.filter(active=True).values_list('manifest',flat=True).first()
    except Exception:snap=None
    schema=snap.get('schema') if isinstance(snap,dict) else None
    return JsonResponse({'status':'ok','schema':schema or 'cu-summary-v1'})

def frontend(request,path=''):
    return static_files.serve(request,path)
