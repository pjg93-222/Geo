"""Prepare named local road lines from the already-downloaded Census archive."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import geopandas as gpd
from shapely.geometry import mapping

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT/'../../work/geography/roads.zip'
    frame = gpd.read_file(source).to_crs(4326)
    target = ROOT/'.local/roads.sqlite3'
    if target.exists():
        raise ValueError('Road context already prepared')
    with closing(sqlite3.connect(target)) as connection, connection:
        connection.executescript('CREATE TABLE roads (id INTEGER PRIMARY KEY,name TEXT,kind TEXT,geometry TEXT); CREATE VIRTUAL TABLE extents USING rtree(id,west,east,south,north);')
        count = 0
        for row in frame.itertuples():
            if row.geometry.is_empty or row.MTFCC not in ['S1100','S1200','S1400']:
                continue
            count += 1
            name = row.FULLNAME if isinstance(row.FULLNAME,str) else ''
            west,south,east,north = row.geometry.bounds
            connection.execute('INSERT INTO roads VALUES (?,?,?,?)', (count,name,row.MTFCC,json.dumps(mapping(row.geometry),separators=(',',':'))))
            connection.execute('INSERT INTO extents VALUES (?,?,?,?,?)', (count,west,east,south,north))
    print(json.dumps({'roads':count,'source':'Census TIGER/Line 2025, El Paso County'}))


if __name__ == '__main__':
    main()
