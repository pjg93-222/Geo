"""Prepare named local road lines from the already-downloaded Census archives of a district."""
import argparse
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import geopandas as gpd
import pandas as pd
from shapely.geometry import mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import districts as D  # noqa: E402

KINDS = ['S1100', 'S1200', 'S1400']


def select_roads(frames, clip=None):
    """(roads, outside_clip, duplicates): the road lines to keep from one frame per file (EPSG:4326, file order).
    Empty lines and other MTFCC kinds are dropped; with `clip`, lines that miss it; a LINEARID seen in an earlier
    file is dropped from the later file (county files share their boundary roads)."""
    kept, seen, outside, duplicates = [], set(), 0, 0
    for frame in frames:
        frame = frame[~frame.geometry.is_empty & frame.MTFCC.isin(KINDS)]
        if clip is not None:
            inside = frame.intersects(clip)
            outside += int((~inside).sum())
            frame = frame[inside]
        repeated = frame.LINEARID.isin(seen)
        duplicates += int(repeated.sum())
        seen.update(frame.LINEARID)
        kept.append(frame[~repeated])
    return pd.concat(kept), outside, duplicates


def clip_area(district):
    """The district boundary grown by its road_clip_m metres (in its UTM zone), as an EPSG:4326 geometry."""
    boundary = gpd.read_file(Path(district['geography'])/'district.geojson').to_crs(district['utm_epsg'])
    return boundary.buffer(district['road_clip_m']).to_crs(4326).union_all()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--district', default=D.DEFAULT)
    parser.add_argument('--out', type=Path, help='default: roads.sqlite3 in the district data directory')
    args = parser.parse_args(argv)
    district = D.get(args.district)
    target = args.out or D.data_dir(district)/'roads.sqlite3'
    if target.exists():
        raise ValueError('Road context already prepared')
    clip = clip_area(district) if district['road_clip_m'] else None
    frame, outside, duplicates = select_roads((gpd.read_file(p).to_crs(4326) for p in D.road_paths(district)), clip)
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(target)) as connection, connection:
        connection.executescript('CREATE TABLE roads (id INTEGER PRIMARY KEY,name TEXT,kind TEXT,geometry TEXT); CREATE VIRTUAL TABLE extents USING rtree(id,west,east,south,north);')
        count = 0
        for row in frame.itertuples():
            count += 1
            name = row.FULLNAME if isinstance(row.FULLNAME,str) else ''
            west,south,east,north = row.geometry.bounds
            connection.execute('INSERT INTO roads VALUES (?,?,?,?)', (count,name,row.MTFCC,json.dumps(mapping(row.geometry),separators=(',',':'))))
            connection.execute('INSERT INTO extents VALUES (?,?,?,?,?)', (count,west,east,south,north))
    receipt = {'roads':count,'source':'Census TIGER/Line 2025, '+district['roads_label']}
    if district['id'] != D.DEFAULT:
        receipt.update(outside_clip=outside, duplicates=duplicates)
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
