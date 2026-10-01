"""Download public boundaries only. Never sends profile data to a geocoder."""
import argparse, concurrent.futures, hashlib, json
from pathlib import Path
import requests
import geopandas as gpd
import shapely
from shapely.geometry import box, mapping, shape

SOURCES = {
    'blocks': 'https://www2.census.gov/geo/tiger/TIGER2025/TABBLOCK20/tl_2025_08_tabblock20.zip',
    'districts': 'https://www2.census.gov/geo/tiger/TIGER2025/CD/tl_2025_08_cd119.zip',
    'roads': 'https://www2.census.gov/geo/tiger/TIGER2025/ROADS/tl_2025_08041_roads.zip',
}
ZCTA_SERVICE = 'https://tigerweb.geo.census.gov/arcgis/rest/services/Census2020/tigerWMS_Census2020/MapServer/84'

def download(url, path):
    if path.exists():
        return path
    partial = path.with_suffix('.part')
    with requests.get(url, timeout=(30,180), stream=True) as r:
        r.raise_for_status()
        with partial.open('wb') as f:
            for chunk in r.iter_content(1024*1024):
                f.write(chunk)
    partial.replace(path)
    print(f'Downloaded {path.name}: {path.stat().st_size:,} bytes', flush=True)
    return path

def prepare(root):
    root.mkdir(parents=True,exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(download,u,root/(k+'.zip')) for k,u in SOURCES.items()]
        for j in jobs: j.result()
    districts = gpd.read_file(root/'districts.zip').to_crs(4326)
    district = districts[(districts.STATEFP=='08') & (districts.CD119FP=='05')].geometry.union_all()
    if district.is_empty: raise ValueError('Colorado CD-005 boundary missing')
    (root/'district.geojson').write_text(json.dumps({'type':'FeatureCollection','features':[{'type':'Feature','properties':{'id':'0805','label':'Colorado · District 05'},'geometry':mapping(district)}]}))
    blocks = gpd.read_file(root/'blocks.zip').to_crs(4326)
    blocks['geoid'] = blocks.GEOID20.astype(str)
    blocks[['geoid','geometry']].to_file(root/'blocks.gpkg',driver='GPKG')
    print(f'Prepared {len(blocks):,} Colorado 2020 blocks (2025 TIGER release)',flush=True)
    zpath=root/'zcta.geojson'
    if not zpath.exists():
        meta=requests.get(ZCTA_SERVICE,params={'f':'json'},timeout=60).json()
        if 'error' in meta: raise ValueError(meta['error'])
        fields=[x['name'] for x in meta['fields']]
        zkey=next(x for x in ['ZCTA5','ZCTA5CE20','GEOID'] if x in fields)
        xmin,ymin,xmax,ymax=district.bounds
        params={'f':'geojson','where':'1=1','outFields':','.join(x for x in ['GEOID',zkey,'NAME'] if x in fields),'returnGeometry':'true','outSR':4326,'geometry':f'{xmin-0.1},{ymin-0.1},{xmax+0.1},{ymax+0.1}','geometryType':'esriGeometryEnvelope','inSR':4326,'spatialRel':'esriSpatialRelIntersects','resultRecordCount':1000}
        data=requests.get(ZCTA_SERVICE+'/query',params=params,timeout=120).json()
        if 'error' in data or data.get('exceededTransferLimit'):raise ValueError('Incomplete ZCTA response')
        for f in data['features']:
            f['properties']={'geoid':str(f['properties'][zkey])}
        zpath.write_text(json.dumps(data),encoding='utf-8')
    zctas=gpd.read_file(zpath)
    if not len(zctas):raise ValueError('ZCTA geometry missing')
    roads=gpd.read_file(root/'roads.zip').to_crs(26913)
    selected=roads[roads.MTFCC.isin(['S1100','S1200','S1400'])]
    network=shapely.union_all(shapely.set_precision(selected.geometry.values,1.0))
    polygons,cuts,dangles,invalid=shapely.polygonize_full(shapely.get_parts(network))
    street=gpd.GeoDataFrame(geometry=list(polygons.geoms),crs=26913)
    street=street[street.area>=100].to_crs(4326)
    street=street[street.intersects(district)]
    features=[]
    for geom in street.geometry:
        features.append({'type':'Feature','properties':{'id':'street-'+hashlib.sha256(geom.wkb).hexdigest()[:16]},'geometry':mapping(geom.simplify(0.00001,preserve_topology=True))})
    (root/'streets.geojson').write_text(json.dumps({'type':'FeatureCollection','features':features}),encoding='utf-8')
    provenance={'block_vintage':'2020 Census blocks; TIGER/Line 2025 release','district_vintage':'119th Congress; TIGER/Line 2025','zcta_vintage':'2020 ZCTAs; Census TIGERweb','street_vintage':'TIGER/Line 2025, El Paso County roads','sources':SOURCES|{'zcta':ZCTA_SERVICE},'street_method':'Closed polygons from noded road centerlines (S1100/S1200/S1400), 1m precision in EPSG:26913; geometry context only, not authoritative street blocks. Open road edges are not closed artificially.','street_polygons':len(features),'street_unclosed_parts':len(cuts.geoms)+len(dangles.geoms),'street_invalid_parts':len(invalid.geoms),'bounds':list(district.bounds),'hashes':{p.name:hashlib.file_digest(p.open('rb'),'sha256').hexdigest() for p in root.glob('*.zip')}}
    (root/'provenance.json').write_text(json.dumps(provenance,indent=2))
    print(json.dumps({'blocks':len(blocks),'zctas':len(zctas),'street_polygons':len(features),'bounds':list(district.bounds)}),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--directory',required=True);args=parser.parse_args();prepare(Path(args.directory))
