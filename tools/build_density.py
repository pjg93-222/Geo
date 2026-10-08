"""Population and voter density per area, one density.json per district (Terrier Phase 4C). Counts and ratios only.

Population density = POP20 / ALAND20 (square km) of the TIGER2025 tabblock20 blocks; a block with ALAND20 = 0 has none. An
area's blocks are the distinct blocks its registered people live in (a block shared by two precincts counts in each), so its
land is the land of its inhabited blocks. Voter density = those people / the same land, for every area (nothing is withheld).
People in a block with no block row or with ALAND20 = 0 count in neither number. The road-span voter density (people per 100 m)
belongs to the street plan.

    <venv python> -m tools.build_density --district or-05          (writes <data dir>/density.json)

Exit 2, and no file, when under 99% of the people with a block id join a block row.
"""
import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pandas as pd
import pyogrio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import districts as D               # noqa: E402
from tools import scores_schema as S           # noqa: E402
from tools.glassbox import GlassBox, new_run_id    # noqa: E402

SCHEMA = 'density-1'
MIN_RATE = 0.99
LEVELS = {level: column for (basis, level), column in S.AREA_LEVEL_OF.items() if basis == 'registration'}


def read_people(path):
    """The four registration area ids of every person ('' when blank); nothing else is read."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        return pd.read_sql('SELECT %s FROM person' % ', '.join(LEVELS.values()), db).fillna('')


def listed(series, digits):
    return [None if pd.isna(v) else v for v in series.round(digits).tolist()]


def compute(people, blocks, aliases):
    """(levels, counts, join). blocks: GEOID20, POP20, ALAND20 as TIGER has them; aliases carry a TIGER id to the id the
    voter file and the area table use (Connecticut: county codes to planning-region codes; other ids stay)."""
    blocks = blocks.assign(block=blocks.GEOID20.map(lambda g: aliases.get(g, g))).set_index('block')
    assert blocks.index.is_unique, 'Block identifiers are not unique after the alias map'
    row = people.join(blocks[['POP20', 'ALAND20']], on='reg_block')
    with_block, joined = int((people.reg_block != '').sum()), int(row.ALAND20.notna().sum())
    join = {'people': len(people), 'with_block': with_block, 'joined': joined, 'rate': round(joined / with_block, 6),
            'blocks_in_file': len(blocks), 'land_zero_blocks': int((blocks.ALAND20 == 0).sum()),
            'land_zero_people': int((row.ALAND20 == 0).sum())}
    usable = row[row.ALAND20 > 0]                                       # no row (NaN) or no land: no density
    levels, counts = {}, {}
    for level, column in LEVELS.items():
        ids = sorted(set(people[column]) - {''})
        used = usable[usable[column] != '']
        inhabited = used.drop_duplicates([column, 'reg_block']).groupby(column)[['POP20', 'ALAND20']].sum().reindex(ids)
        km2 = inhabited.ALAND20 / 1e6
        voters = used.groupby(column).size().reindex(ids)
        levels[level] = {'ids': ids, 'pop_density': listed(inhabited.POP20 / km2, 3),
                         'voter_density': listed(voters / km2, 3), 'land_km2': listed(km2, 6)}
        counts[level] = {'areas': len(ids),
                         'pop_density': sum(v is not None for v in levels[level]['pop_density']),
                         'voter_density': sum(v is not None for v in levels[level]['voter_density'])}
    return levels, counts, join


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--district', choices=list(D.DISTRICTS), default=D.DEFAULT)
    parser.add_argument('--out', help='default: <data dir>/density.json')
    args = parser.parse_args(argv)
    d = D.get(args.district)
    out = Path(args.out or D.data_dir(d) / 'density.json')
    blocks_zip = Path(d['geography']) / 'blocks.zip'
    gb = GlassBox(new_run_id('cu-geo-v3-density'), 'cu-geography-v3/tools/build_density.py')
    gb.run('running', 'start', 'density.json build for %s (counts only)' % d['id'])
    try:
        blocks = pyogrio.read_dataframe(blocks_zip, columns=['GEOID20', 'POP20', 'ALAND20'], read_geometry=False)
        levels, counts, join = compute(read_people(D.data_dir(d) / 'scores.sqlite3'), blocks, D.block_aliases(d))
    except Exception as error:
        gb.failed('build', 'density build crashed: %s: %s' % (type(error).__name__, error))
        raise
    if join['rate'] < MIN_RATE:
        gb.failed('join', 'density not written: join rate %s is under %s' % (join['rate'], MIN_RATE))
        print('STOPPED: block join rate %s is under %s' % (join['rate'], MIN_RATE), file=sys.stderr)
        return 2
    with open(blocks_zip, 'rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    document = {'schema': SCHEMA, 'district': d['id'], 'blocks_source': {'file': blocks_zip.name, 'sha256': digest},
                'join': join, 'counts': counts, 'levels': levels,
                'notes': 'per square km; a block shared by two areas counts in each; every area is shown'}
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_name(out.name + '.tmp')
    temporary.write_text(json.dumps(document, separators=(',', ':')), encoding='utf-8')
    temporary.replace(out)                                              # the server may be reading the old file
    gb.run('completed', 'complete', 'density.json written for %s: join rate %s' % (d['id'], join['rate']), bytes_=out.stat().st_size)
    print(json.dumps({'district': d['id'], 'join': join, 'counts': counts}, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
