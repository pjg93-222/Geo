import json
from contextlib import closing
import math
import sqlite3
import threading
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from .views import protected
from .views import snapshot_for
from .bundles import load
from . import v3_cache
from .v3_http import blob_response
from tools.geography_levels import POLYGON_LEVELS


@protected()
@require_GET
def area_focus(request):
    snapshot = snapshot_for(request)
    level, ident = request.GET.get('level'), request.GET.get('id')
    if not snapshot or level not in POLYGON_LEVELS:
        return JsonResponse({'error':'Choose a mapped area.'},status=400)
    feature = load(snapshot.key,'geometry.json').get(level,{}).get(ident)
    if not feature:
        return JsonResponse({'error':'Area geometry is unavailable.'},status=404)
    from shapely.geometry import shape
    district=shape(load(snapshot.key,'district.geojson')['features'][0]['geometry'])
    within=shape(feature['geometry']).intersection(district)
    if within.is_empty:return JsonResponse({'error':'Area is outside the source extent.'},status=404)
    point = within.representative_point()
    return JsonResponse({'center':[point.x,point.y]})


_road_blobs = {}
_road_lock = threading.Lock()
ATTRIBUTION = 'U.S. Census TIGER/Line 2025 · %s roads' % settings.DISTRICT['roads_label']


@protected()
@require_GET
def roads(request):
    """Road lines in view. The body is assembled from the stored geometry text (no per-row JSON decode and encode),
    kept for repeat views of the same extent, and answered with an ETag, so a re-centre or an index switch that asks
    for the same viewport is a lookup (BASELINE L1)."""
    try:
        west,south,east,north = map(float,request.GET.get('bbox','').split(','))
        zoom = float(request.GET.get('zoom','9'))
        if not all(map(math.isfinite,[west,south,east,north,zoom])) or not (-180 <= west < east <= 180 and -90 <= south < north <= 90 and 0 <= zoom <= 22):
            raise ValueError('Invalid bounds')
        database = (settings.DATA_DIR/'roads.sqlite3').resolve()
        stamp = database.stat().st_mtime_ns
        memo = (stamp, round(west,5), round(south,5), round(east,5), round(north,5), zoom < 12)
        cached = _road_blobs.get(memo)
        if cached is None:
            with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as connection:
                clause = " AND r.kind IN ('S1100','S1200')" if zoom < 12 else ''
                rows = connection.execute('SELECT r.id,r.name,r.kind,r.geometry FROM roads r JOIN extents e ON r.id=e.id WHERE e.west<=? AND e.east>=? AND e.south<=? AND e.north>=?'+clause+
                                          " ORDER BY r.kind,r.id LIMIT 6001", (east,west,north,south)).fetchall()
            dumps = json.dumps
            parts = ['{"type":"Feature","id":%s,"properties":{"name":%s,"kind":%s},"geometry":%s}' % (dumps(ident),dumps(name),dumps(kind),geometry) for ident,name,kind,geometry in rows[:6000]]
            body = ('{"type":"FeatureCollection","features":[%s],"truncated":%s,"attribution":%s}' % (','.join(parts),'true' if len(rows)>6000 else 'false',dumps(ATTRIBUTION))).encode('utf-8')
            cached = v3_cache.Blob(body)
            with _road_lock:
                if len(_road_blobs) >= 48:_road_blobs.clear()
                _road_blobs[memo] = cached
        return blob_response(request,cached,'private, max-age=3600')
    except sqlite3.Error:
        return JsonResponse({'error':'Local road detail has not been prepared.'},status=503)
