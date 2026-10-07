"""Download public boundaries only. Never sends profile data to a geocoder."""
import argparse, concurrent.futures, hashlib, json, sys
from pathlib import Path
import pandas as pd
import requests
import geopandas as gpd
import shapely
from shapely.geometry import box, mapping, shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import districts as D  # noqa: E402

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

KINDS = ['S1100', 'S1200', 'S1400']
JOIN_COLUMNS = ('svi_registration_censusid', 'sii_censusid')
MIN_REGISTRATION_RATE = 0.99
BBOX_PAD = 0.001   # degrees: TIGER blocks are NAD83, the district is WGS84; keeps blocks on the box edge
WORK_GEOGRAPHY = D.WORK / 'geography'
COUNTIES = WORK_GEOGRAPHY / 'hierarchy' / 'county.zip'
DOWNLOADS = WORK_GEOGRAPHY / 'DOWNLOADS_2026-10-07.json'
STREET_METHOD = 'Closed polygons from noded road centerlines (S1100/S1200/S1400), 1m precision in EPSG:%d; geometry context only, not authoritative street blocks. Open road edges are not closed artificially.'


def district_shape(frame, d):
    """The district boundary (one EPSG:4326 geometry) out of a congressional-district frame; ValueError if missing or invalid."""
    boundary = frame[(frame.STATEFP == d['state_fips']) & (frame.CD119FP == d['cd119fp'])].geometry.union_all()
    if boundary.is_empty or not boundary.is_valid:
        raise ValueError('%s boundary missing or invalid' % d['label'])
    return boundary


def recode_blocks(blocks, regions, state_fips):
    """(frame, counts): blocks of a state whose county codes changed (Connecticut: counties -> planning regions) with
    the id built from the region holding each block's interior point (INTPTLON20/INTPTLAT20), then one alias row per
    re-coded block under its original GEOID20, so ids of either vintage join. `blocks` and `regions` are EPSG:4326
    frames; `regions` has COUNTYFP. ValueError unless every block lands in exactly one region."""
    points = gpd.GeoDataFrame(geometry=gpd.points_from_xy(blocks.INTPTLON20.astype(float), blocks.INTPTLAT20.astype(float)),
                              index=blocks.index, crs=4326)
    hit = gpd.sjoin(points, regions[['COUNTYFP', 'geometry']], how='left', predicate='within')
    matches = hit.groupby(level=0).COUNTYFP.count()
    if (matches != 1).any():
        raise ValueError('%d blocks are not in exactly one region' % int((matches != 1).sum()))
    original = blocks.GEOID20.astype(str)
    fresh = gpd.GeoDataFrame({'geoid': state_fips + hit.COUNTYFP.reindex(blocks.index) + blocks.TRACTCE20 + blocks.BLOCKCE20}, geometry=blocks.geometry, crs=blocks.crs)
    changed = fresh.geoid.to_numpy() != original.to_numpy()
    aliases = fresh.assign(geoid=original)[changed]
    out = pd.concat([fresh, aliases])
    if not out.geoid.is_unique:
        raise ValueError('Re-coded block identifiers are not unique')
    return out, {'recoded': int(changed.sum()), 'aliases': len(aliases),
                 'regions': {k: int(v) for k, v in hit.COUNTYFP.value_counts().sort_index().items()}}


def alias_map(blocks):
    """{old block id: region block id} out of a recode_blocks frame: each alias row repeats the index of the re-coded row
    it copies, which comes first. Every alias maps to exactly one region id (a row index holds at most one of each)."""
    alias = blocks.index.duplicated(keep='first')
    return dict(zip(blocks.geoid[alias], blocks.geoid[~alias].loc[blocks.index[alias]]))


def join_receipt(csv_path, geoids):
    """{column: {rows, blank, joined, rate}} for the two block-id columns of a source CSV (counts only); rate is
    joined / non-blank rows."""
    total = {c: [0, 0, 0] for c in JOIN_COLUMNS}
    for chunk in pd.read_csv(csv_path, usecols=list(JOIN_COLUMNS), dtype=str, na_filter=False, encoding='cp1252', chunksize=200000):
        for column in JOIN_COLUMNS:
            ids = chunk[column].str.strip()
            for slot, n in enumerate((len(ids), int(ids.eq('').sum()), int(ids.isin(geoids).sum()))):
                total[column][slot] += n
    return {c: {'rows': r, 'blank': b, 'joined': j, 'rate': round(j / (r - b), 6) if r > b else 0.0} for c, (r, b, j) in total.items()}


def zcta_layer(path, district, fetch):
    """The ZCTA file of the bundle layers: an existing zcta.geojson, else (fetch) the TIGERweb query of prepare()."""
    if not path.exists():
        if not fetch:
            raise ValueError('%s is missing; pass --fetch-zcta to query TIGERweb' % path.name)
        meta = requests.get(ZCTA_SERVICE, params={'f': 'json'}, timeout=60).json()
        if 'error' in meta: raise ValueError(meta['error'])
        fields = [x['name'] for x in meta['fields']]
        zkey = next(x for x in ['ZCTA5', 'ZCTA5CE20', 'GEOID'] if x in fields)
        xmin, ymin, xmax, ymax = district.bounds
        params = {'f': 'geojson', 'where': '1=1', 'outFields': ','.join(x for x in ['GEOID', zkey, 'NAME'] if x in fields), 'returnGeometry': 'true', 'outSR': 4326, 'geometry': f'{xmin-0.1},{ymin-0.1},{xmax+0.1},{ymax+0.1}', 'geometryType': 'esriGeometryEnvelope', 'inSR': 4326, 'spatialRel': 'esriSpatialRelIntersects', 'resultRecordCount': 1000}
        data = requests.get(ZCTA_SERVICE + '/query', params=params, timeout=120).json()
        if 'error' in data or data.get('exceededTransferLimit'): raise ValueError('Incomplete ZCTA response')
        for f in data['features']:
            f['properties'] = {'geoid': str(f['properties'][zkey])}
        path.write_text(json.dumps(data), encoding='utf-8')
    zctas = gpd.read_file(path)
    if not len(zctas): raise ValueError('ZCTA geometry missing')
    return len(zctas)


def street_outlines(frames, district, epsg):
    """(features, unclosed, invalid): closed polygons from the noded S1100/S1200/S1400 lines of the road frames (the
    street outlines of prepare(), in the district's UTM zone), those touching the district."""
    roads = pd.concat(frames)
    roads = roads[roads.MTFCC.isin(KINDS)].to_crs(epsg)
    network = shapely.union_all(shapely.set_precision(roads.geometry.values, 1.0))
    polygons, cuts, dangles, invalid = shapely.polygonize_full(shapely.get_parts(network))
    street = gpd.GeoDataFrame(geometry=list(polygons.geoms), crs=epsg)
    street = street[street.area >= 100].to_crs(4326)
    street = street[street.intersects(district)]
    features = [{'type': 'Feature', 'properties': {'id': 'street-' + hashlib.sha256(g.wkb).hexdigest()[:16]}, 'geometry': mapping(g.simplify(0.00001, preserve_topology=True))} for g in street.geometry]
    return features, len(cuts.geoms) + len(dangles.geoms), len(invalid.geoms)


def prepare_district(d, root, join_check=False, bundle_layers=False, fetch_zcta=False):
    """Stores part for a district other than co-05 (no network unless fetch_zcta): district.geojson, blocks.gpkg,
    provenance.json and geography_receipt.json (counts only) in `root`, from the files already downloaded there."""
    root = Path(root)
    roads = [root / name for name in d['road_files']]
    missing = [p.name for p in (root / 'blocks.zip', root / 'districts.zip', *roads) if not p.is_file()]
    if missing:
        raise FileNotFoundError('Not downloaded (this tool does not download): ' + ', '.join(missing))
    district = district_shape(gpd.read_file(root / 'districts.zip').to_crs(4326), d)
    (root / 'district.geojson').write_text(json.dumps({'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'properties': {'id': d['district_id'], 'label': d['label']}, 'geometry': mapping(district)}]}))
    xmin, ymin, xmax, ymax = district.bounds
    blocks = gpd.read_file(root / 'blocks.zip', bbox=(xmin - BBOX_PAD, ymin - BBOX_PAD, xmax + BBOX_PAD, ymax + BBOX_PAD)).to_crs(4326)
    read = len(blocks)
    blocks = blocks[blocks.intersects(district)]
    receipt = {'district': d['id'], 'blocks': {'read': read, 'kept': len(blocks)}}
    if d['block_regions']:
        regions = gpd.read_file(COUNTIES, where="STATEFP = '%s'" % d['state_fips']).to_crs(4326)
        blocks, receipt['recode'] = recode_blocks(blocks, regions, d['state_fips'])
    else:
        blocks = blocks.assign(geoid=blocks.GEOID20.astype(str))
    if not blocks.geoid.is_unique:
        raise ValueError('Block identifiers are not unique')
    if d['block_regions']:
        # the voter file still carries the old county codes on some rows: ingest, extend and the row-framework cache map them
        aliases = alias_map(blocks)
        if len(aliases) != receipt['recode']['aliases']:
            raise ValueError('Alias count differs from the re-code count')
        (root / 'block_aliases.json').write_text(json.dumps(aliases, sort_keys=True), encoding='utf-8')
    (root / 'blocks.gpkg').unlink(missing_ok=True)
    blocks[['geoid', 'geometry']].to_file(root / 'blocks.gpkg', driver='GPKG')
    urls = {row['file'].replace('\\', '/'): row['url'] for row in json.loads(DOWNLOADS.read_text(encoding='utf-8'))}
    provenance = {'block_vintage': '2020 Census blocks; TIGER/Line 2025 release', 'district_vintage': '119th Congress; TIGER/Line 2025',
                  'street_vintage': 'TIGER/Line 2025, ' + d['roads_label'] + ' roads',
                  'sources': {'blocks': urls[d['id'] + '/blocks.zip'], 'districts': urls[d['id'] + '/districts.zip'],
                              'roads': [urls[d['id'] + '/' + name] for name in d['road_files']]},
                  'bounds': list(district.bounds),
                  'hashes': {p.name: hashlib.file_digest(p.open('rb'), 'sha256').hexdigest() for p in (root / 'blocks.zip', root / 'districts.zip', *roads)}}
    if bundle_layers:
        zcount = zcta_layer(root / 'zcta.geojson', district, fetch_zcta)
        features, unclosed, invalid = street_outlines((gpd.read_file(p) for p in roads), district, d['utm_epsg'])
        (root / 'streets.geojson').write_text(json.dumps({'type': 'FeatureCollection', 'features': features}), encoding='utf-8')
        provenance.update(zcta_vintage='2020 ZCTAs; Census TIGERweb', street_method=STREET_METHOD % d['utm_epsg'],
                          street_polygons=len(features), street_unclosed_parts=unclosed, street_invalid_parts=invalid)
        provenance['sources']['zcta'] = ZCTA_SERVICE
        receipt['bundle_layers'] = {'zctas': zcount, 'street_polygons': len(features)}
    (root / 'provenance.json').write_text(json.dumps(provenance, indent=2))
    if join_check:
        receipt['join'] = join_receipt(d['source_csv'], set(blocks.geoid))
    (root / 'geography_receipt.json').write_text(json.dumps(receipt, indent=2))
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--district', default=D.DEFAULT)
    parser.add_argument('--directory', type=Path, help='default: the district geography folder (required for co-05)')
    parser.add_argument('--join-check', action='store_true', help='count how many source block ids join the blocks')
    parser.add_argument('--bundle-layers', action='store_true', help='also write the ZCTA and street-outline layers')
    parser.add_argument('--fetch-zcta', action='store_true', help='allow the TIGERweb ZCTA query when zcta.geojson is missing')
    args = parser.parse_args(argv)
    d = D.get(args.district)
    if d['id'] == D.DEFAULT:
        if not args.directory or args.join_check or args.bundle_layers or args.fetch_zcta:
            parser.error('co-05 takes --directory only (its run is the original prepare())')
        return prepare(args.directory)
    receipt = prepare_district(d, args.directory or d['geography'], args.join_check, args.bundle_layers, args.fetch_zcta)
    print(json.dumps(receipt), flush=True)
    if args.join_check and receipt['join']['svi_registration_censusid']['rate'] < MIN_REGISTRATION_RATE:
        sys.exit(2)


if __name__ == '__main__':
    main()
