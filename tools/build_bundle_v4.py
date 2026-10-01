"""Build the `cu-summary-v4` bundle: the v3 bundle plus the row-framework area layers (fails closed).

Run with the shared work venv (numpy + shapely), never with system Python:

  work\\.venv\\Scripts\\python.exe tools\\build_bundle_v4.py --real          (set OPENBLAS_NUM_THREADS=1 when RAM is low)

Inputs (all aggregate; no person row is read):
  * the base bundle ZIP (cu-summary-v3): rows, 2020 block and ZCTA polygons, street outlines, district outline;
  * area_scores_rowfw-1.0.0.csv (published areas) and summaries/area_suppressed_ids.csv (withheld areas);
  * the frozen framework_config.json, plan and criteria files (sha256 against tools/area_layers.FROZEN_SHA256);
  * release/P1_D2_suppression_counts.json (the D2 suppression receipt and its final audit round);
  * scores.sqlite3 (counts only: block, precinct, tract and ZIP codes of the registered people, to place blocks in
    precincts and to count blocks per area; its recorded area-table and suppressed-ids hashes must equal the files').

Output: data/co-cd005-<key>.zip and data/co-cd005-<key>.zip.sha256 (import with `manage.py import_summary <zip>`), and a
receipt with counts only in _build/bundle_v4_receipt.json.

Stops (and writes nothing) when: a frozen file, the area table or the suppressed-id list does not match its hash or the
score store's record; the table has a residence block row or a hatch column; the published and withheld counts differ
from the D2 receipt; the final suppression audit is not clean; a registration block, precinct or tract of the table has
no polygon; or the finished bundle fails the app's own validator.
"""
import argparse
import csv
import hashlib
import io
import json
import math
import os
import sqlite3
import sys
import time
import zipfile
from pathlib import Path

import numpy as np
from shapely import STRtree
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import area_layers as AL              # noqa: E402
from tools.geography_levels import area_label    # noqa: E402
from tools.glassbox import GlassBox, new_run_id  # noqa: E402

V3 = Path(__file__).resolve().parents[1]
ROOT = Path(r'C:\Users\pgoff\Documents\Codex\2026-09-27\we-are-going-to-have-an\outputs\cu-geography')
RFB = ROOT / '.local' / 'row-framework-20260930' / 'build' / 'rowfw-1.0.0'
BASE_ZIP = V3 / 'data' / 'co-cd005-b0edb0f04d6c.zip'
BLOCKS_GPKG = Path('C:/Users/pgoff/Documents/Codex/2026-09-27/we-are-going-to-have-an/work/geography/blocks.gpkg')
FILES = ('manifest.json', 'rows.json', 'geometry.json', 'streets.geojson', 'district.geojson')
GROUP_ROW_KEYS = ('basis', 'level', 'area_id')
ROUND = 6                  # coordinate decimals, as in the base bundle
ADJACENT_DEGREES = 2e-5    # polygons closer than about 2 m count as neighbours


class BuildError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise BuildError(message)


def sha256_file(path, chunk=1 << 22):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(chunk), b''):
            digest.update(block)
    return digest.hexdigest()


def dumps(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False)


# ---- inputs ------------------------------------------------------------------------------------------------------

def read_area_table(path):
    with open(path, newline='', encoding='utf-8') as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames
        rows = list(reader)
    require('cua_hatch_cpi_blank' not in columns, 'The area table still carries the hatch column (R6/F24)')
    return columns, rows


def number(text):
    """A table cell as a float, or None when blank or NaN (CSV text or an already numeric fixture value)."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return None if math.isnan(text) else float(text)
    text = str(text).strip()
    if text == '' or text.lower() == 'nan':
        return None
    return float(text)


def check_area_table(columns, rows, suppressed, receipt):
    published = {}
    for row in rows:
        require(row['suppressed'] in ('0', '0.0'), 'A suppressed row carries values')
        require(not (row['basis'] == 'residence' and row['level'] == 'block'), 'The table has residence block rows')
        published.setdefault((row['basis'], row['level']), set()).add(row['area_id'])
    withheld = {}
    for row in suppressed:
        require(row['suppressed'] == '1', 'The suppressed list has an unsuppressed row')
        withheld.setdefault((row['basis'], row['level']), set()).add(row['area_id'])
    expected = receipt['D2_counts']
    for key, counts in expected.items():
        basis, level = key.split('/')
        require(len(published.get((basis, level), ())) == counts['published'],
                'Published areas of %s differ from the D2 receipt' % key)
        require(len(withheld.get((basis, level), ())) == counts['areas'] - counts['published'],
                'Withheld areas of %s differ from the D2 receipt' % key)
        require(not (published.get((basis, level), set()) & withheld.get((basis, level), set())),
                'An area is both published and withheld in %s' % key)
    require(set(published) == set(withheld) == {tuple(k.split('/')) for k in expected},
            'The table covers other levels than the D2 level set')
    return published, withheld


def final_audit(receipt):
    last = receipt['D2_suppression_rounds'][-1]
    require(last['symdiff_violations'] == 0 and last['remainder_violations'] == 0 and last['exactly_disclosed'] == 0,
            'The final suppression audit round is not clean')
    return {'exactly_disclosed': 0, 'symdiff_violations': 0, 'remainder_violations': 0}


def read_scores(path):
    """Counts only: the codes of each registered person's block, precinct, tract and ZIP, and the recorded hashes."""
    connection = sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True)
    try:
        meta = {k: json.loads(v) for k, v in connection.execute(
            "SELECT key,value FROM metadata WHERE key IN ('area_table_sha256','suppressed_ids_sha256','n_people')")}
        columns, dicts = {}, {}
        for name, dtype, n, blob in connection.execute(
                "SELECT name,dtype,n,data FROM list_column WHERE name IN "
                "('a_reg_block','a_reg_precinct','a_reg_tract','a_reg_zip')"):
            columns[name] = np.frombuffer(blob, dtype=np.dtype(dtype), count=n)
        for name, ids in connection.execute("SELECT name,ids FROM list_dict WHERE name IN "
                                            "('a_reg_block','a_reg_precinct','a_reg_tract','a_reg_zip')"):
            dicts[name] = json.loads(ids)
    finally:
        connection.close()
    return meta, columns, dicts


def pair_counts(a, b, size_b):
    """Counts of (a, b) code pairs where both are known (>= 0)."""
    keep = (a >= 0) & (b >= 0)
    key = a[keep].astype(np.int64) * size_b + b[keep].astype(np.int64)
    unique, counts = np.unique(key, return_counts=True)
    return unique // size_b, unique % size_b, counts


def modal(first, second, counts, second_ids):
    """{first code: second id with the most people (ties: the smaller id)}."""
    best = {}
    for a, b, c in zip(first.tolist(), second.tolist(), counts.tolist()):
        current = best.get(a)
        if current is None or c > current[0] or (c == current[0] and second_ids[b] < second_ids[current[1]]):
            best[a] = (c, b)
    return {a: second_ids[v[1]] for a, v in best.items()}


# ---- geometry ----------------------------------------------------------------------------------------------------

def round_coords(coords):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], ROUND), round(coords[1], ROUND)]
    return [round_coords(c) for c in coords]


def polygons_only(geom):
    if geom.geom_type in ('Polygon', 'MultiPolygon'):
        return geom
    parts = [g for g in getattr(geom, 'geoms', []) if g.geom_type in ('Polygon', 'MultiPolygon')]
    require(parts, 'A dissolve produced no polygon')
    return unary_union(parts)


def feature(ident, geom):
    geom = polygons_only(geom)
    mapped = mapping(geom)
    minx, miny, maxx, maxy = geom.bounds
    return {'id': ident, 'geometry': {'type': mapped['type'], 'coordinates': round_coords(mapped['coordinates'])},
            'bbox': [round(minx, ROUND), round(miny, ROUND), round(maxx, ROUND), round(maxy, ROUND)]}


def dissolve(members):
    return polygons_only(unary_union([g.buffer(0) if not g.is_valid else g for g in members]))


def neighbours(geoms):
    """{id: sorted ids of polygons that touch or lie within ADJACENT_DEGREES}."""
    ids = sorted(geoms)
    tree = STRtree([geoms[i] for i in ids])
    graph = {i: set() for i in ids}
    for k, ident in enumerate(ids):
        hits = tree.query(geoms[ident].buffer(ADJACENT_DEGREES))
        for h in hits.tolist():
            other = ids[h]
            if other != ident and geoms[ident].distance(geoms[other]) <= ADJACENT_DEGREES:
                graph[ident].add(other)
                graph[other].add(ident)
    return {i: sorted(n) for i, n in graph.items()}


# ---- pure assembly (also used by the tests) -----------------------------------------------------------------------

def make_area_file(area_rows, suppressed_rows, relations_for=None):
    """area_indices.json from the area table.

    area_rows: dict rows in the layout of area_scores_rowfw-1.0.0.csv (published areas, one row per group);
    suppressed_rows: dict rows with basis, level and area_id (withheld areas); relations_for(basis, level, id) gives
    the block, precinct, tract and block-count relations of a published area (missing keys stay null).
    Returns (file, published {(basis, level): ids}, withheld {(basis, level): ids}).
    """
    relations_for = relations_for or (lambda basis, level, ident: {})
    published, withheld, by_area = {}, {}, {}
    for row in area_rows:
        published.setdefault((row['basis'], row['level']), set()).add(row['area_id'])
        by_area.setdefault((row['basis'], row['level'], row['area_id']), {})[row['group']] = row
    for row in suppressed_rows:
        withheld.setdefault((row['basis'], row['level']), set()).add(row['area_id'])
    rows = []
    for (basis, level), pub in sorted(published.items()):
        for ident in sorted(pub | withheld.get((basis, level), set())):
            base = {'basis': basis, 'level': level, 'id': ident, 'label': area_label(level, ident),
                    'parent_id': ident[:11] if (basis, level) == ('registration', 'block') else 'file'}
            relations = {key: None for key in AL.RELATIONS[level]}
            if ident in pub:
                groups = by_area[(basis, level, ident)]
                require(set(groups) == set(AL.GROUPS), 'An area lacks one of the five groups')
                first = groups['ovr']
                n_rows, n_hh = int(float(first['n_rows'])), int(float(first['n_households']))
                require(all(int(float(g['n_rows'])) == n_rows and int(float(g['n_households'])) == n_hh
                            for g in groups.values()), 'Counts differ between the groups of one area')
                cells = {}
                for group in AL.GROUPS:
                    record = groups[group]
                    values = []
                    for column in AL.COLUMNS[group]:
                        value = number(record[column])
                        require(value is not None, 'A published value is missing: %s %s' % (group, column))
                        values.append(int(round(value)) if column in AL.INTEGER_COLUMNS else value)
                    cells[group] = values
                relations.update({k: v for k, v in relations_for(basis, level, ident).items() if k in relations})
                base.update({'status': 'ok', 'n_rows': n_rows, 'n_households': n_hh, 'groups': cells, **relations})
            else:
                base.update({'status': 'withheld', 'n_rows': None, 'n_households': None, 'groups': None, **relations})
            rows.append(base)
    return ({'schema': 'area-indices-1', 'rowfw_version': AL.ROWFW_VERSION,
             'columns': {g: list(AL.COLUMNS[g]) for g in AL.GROUPS}, 'rows': rows}, published, withheld)


GEOMETRY_METHOD = {
    'block': ('2020 Census block polygons (TIGER/Line 2025) as in the base bundle, plus the registered blocks of the '
              'table that the base bundle lacked, read from the same block file.'),
    'tract': 'Exact dissolve of the 2020 blocks by the first 11 digits of the block id; blocks nest in tracts.',
    'precinct': ('Dissolve of the registration blocks whose most common precinct among registered people is that '
                 'precinct (ties: lower code). An approximation at block granularity: a block that holds people of '
                 'two precincts is drawn in one.'),
    'zip': 'The 2020 ZCTA polygon with the same five digits; a ZIP without a ZCTA is table-only.',
}


def apply_v4_manifest(manifest, rows, adjacency, *, base_key, area_sha, suppressed_sha, frozen, run_id, d1_audit,
                      created_at, concordance):
    """Turn a v3 manifest into the v4 manifest (mutates and returns it)."""
    key = AL.snapshot_key(base_key, area_sha, suppressed_sha, frozen['framework_config_sha256'])
    manifest.update({
        'schema': 'cu-summary-v4', 'snapshot': key, 'created_at': created_at,
        'row_framework': {
            'version': AL.ROWFW_VERSION, 'base_snapshot': base_key, 'area_table_sha256': area_sha,
            'suppressed_ids_sha256': suppressed_sha, **frozen, 'run_id': run_id, 'rulings': AL.RULINGS,
            'd1_audit': d1_audit, 'area_indices_sha256': AL.sha256_json(rows),
            'withheld_set_sha256': AL.withheld_set_sha256(rows), 'adjacency_sha256': AL.sha256_json(adjacency)},
        'area_index_levels': {b: list(l) for b, l in AL.BASIS_LEVELS.items()},
        'area_index_catalog': {'groups': AL.INDEX_CATALOG, 'layers': AL.LAYERS},
        'area_floors': {'min_n': AL.MIN_N, 'min_households': AL.MIN_HOUSEHOLDS},
        'geometry_method': {**GEOMETRY_METHOD, 'concordance': concordance}})
    return manifest


# ---- build -------------------------------------------------------------------------------------------------------

def build(args, log):
    box = GlassBox(args.run_id, 'cu-geography-v3/tools/build_bundle_v4.py')
    box.run('running', 'start', 'cu-summary-v4 bundle build: area table + geometry (counts only, no names)')
    started = time.time()
    rfb = Path(args.rowfw)

    # 1. frozen inputs and hashes ------------------------------------------------------------------------------
    box.stage('running', 'verify-inputs', 'frozen files, area table, suppressed ids, receipt')
    frozen = {'framework_config_sha256': sha256_file(rfb / 'framework_config.json'),
              'plan_sha256': sha256_file(rfb / 'PLAN_rowfw-1.0.0.md'),
              'criteria_sha256': sha256_file(rfb / 'criteria_rowfw-1.0.0.json')}
    require(frozen == AL.FROZEN_SHA256, 'A frozen row-framework file does not match its recorded sha256')
    area_path = rfb / 'area_scores_rowfw-1.0.0.csv'
    suppressed_path = rfb / 'summaries' / 'area_suppressed_ids.csv'
    area_sha, suppressed_sha = sha256_file(area_path), sha256_file(suppressed_path)
    meta, codes, dicts = read_scores(args.scores)
    require(meta['area_table_sha256'] == area_sha, 'The area table is not the one the score store was built from')
    require(meta['suppressed_ids_sha256'] == suppressed_sha,
            'The suppressed-id list is not the one the score store was built from')
    receipt = json.load(open(rfb / 'release' / 'P1_D2_suppression_counts.json', encoding='utf-8'))
    require(receipt['frozen_sha256']['framework_config.json'] == frozen['framework_config_sha256'],
            'The suppression receipt belongs to another config')
    d1_audit = final_audit(receipt)
    columns, area_rows = read_area_table(area_path)
    with open(suppressed_path, newline='', encoding='utf-8') as stream:
        suppressed_rows = list(csv.DictReader(stream))
    published, withheld = check_area_table(columns, area_rows, suppressed_rows, receipt)
    log('inputs verified', {'published': sum(len(v) for v in published.values()),
                            'withheld': sum(len(v) for v in withheld.values())})

    # 2. base bundle ----------------------------------------------------------------------------------------------
    box.stage('running', 'read-base', 'base bundle files')
    base_sha = sha256_file(args.base)
    with zipfile.ZipFile(args.base) as archive:
        require(set(archive.namelist()) == set(FILES), 'The base bundle does not hold the five approved files')
        blobs = {name: archive.read(name) for name in FILES}
    manifest = json.loads(blobs['manifest.json'])
    require(manifest['schema'] == 'cu-summary-v3', 'The base bundle must be cu-summary-v3')
    base_key = manifest['snapshot']
    geometry = json.loads(blobs['geometry.json'])
    block_geoms = {i: shape(f['geometry']) for i, f in geometry['block'].items()}
    log('base read', {'blocks': len(block_geoms), 'zcta': len(geometry['zcta'])})

    # 3. relations from the registered people's codes (counts only) -----------------------------------------------
    box.stage('running', 'relations', 'block-precinct placement and block counts')
    block_ids, precinct_ids = dicts['a_reg_block'], dicts['a_reg_precinct']
    tract_ids, zip_ids = dicts['a_reg_tract'], dicts['a_reg_zip']
    b, p, c_bp = pair_counts(codes['a_reg_block'], codes['a_reg_precinct'], len(precinct_ids))
    block_precinct = modal(b, p, c_bp, precinct_ids)                       # block code -> precinct id
    pb_p, pb_b, _ = pair_counts(codes['a_reg_precinct'], codes['a_reg_block'], len(block_ids))
    precinct_nblocks = {}
    for pc in pb_p.tolist():
        precinct_nblocks[pc] = precinct_nblocks.get(pc, 0) + 1
    pt_p, pt_t, c_pt = pair_counts(codes['a_reg_precinct'], codes['a_reg_tract'], len(tract_ids))
    precinct_tract = modal(pt_p, pt_t, c_pt, tract_ids)
    precinct_ntracts = {}
    for pc in pt_p.tolist():
        precinct_ntracts[pc] = precinct_ntracts.get(pc, 0) + 1
    populated_blocks = sorted({block_ids[k] for k in np.unique(codes['a_reg_block'][codes['a_reg_block'] >= 0]).tolist()})
    tract_nblocks = {}
    for ident in populated_blocks:
        tract_nblocks[ident[:11]] = tract_nblocks.get(ident[:11], 0) + 1
    zb_z, zb_b, _ = pair_counts(codes['a_reg_zip'], codes['a_reg_block'], len(block_ids))
    zip_nblocks = {}
    for zc in zb_z.tolist():
        zip_nblocks[zc] = zip_nblocks.get(zc, 0) + 1
    # concordance: the share of people whose precinct is their block's modal precinct
    total_bp = int(c_bp.sum())
    agree = sum(int(c) for blk, pr, c in zip(b.tolist(), p.tolist(), c_bp.tolist())
                if block_precinct.get(blk) == precinct_ids[pr])
    precinct_person_share = agree / total_bp if total_bp else 0.0

    # 4. area index rows ------------------------------------------------------------------------------------------
    box.stage('running', 'area-rows', 'area index rows for six levels')
    block_code = {ident: k for k, ident in enumerate(block_ids)}
    precinct_code = {ident: k for k, ident in enumerate(precinct_ids)}
    zip_code = {ident: k for k, ident in enumerate(zip_ids)}

    def relations_for(basis, level, ident):
        if basis != 'registration':
            return {}
        if level == 'block':
            return {'precinct': block_precinct.get(block_code.get(ident))}
        if level == 'precinct':
            code = precinct_code.get(ident)
            return {'tract': precinct_tract.get(code), 'n_tracts': precinct_ntracts.get(code),
                    'n_blocks': precinct_nblocks.get(code)}
        if level == 'tract':
            return {'n_blocks': tract_nblocks.get(ident)}
        return {'n_blocks': zip_nblocks.get(zip_code.get(ident))}

    area_file, _, _ = make_area_file(area_rows, suppressed_rows, relations_for)
    rows = area_file['rows']
    log('area rows', len(rows))

    # 5. geometry --------------------------------------------------------------------------------------------------
    box.stage('running', 'geometry', 'tract, precinct and ZIP polygons')
    universe = {lv: {r['id'] for r in rows if r['basis'] == 'registration' and r['level'] == lv}
                for lv in AL.LEVEL_ORDER}
    published_ids = {lv: {r['id'] for r in rows if r['basis'] == 'registration' and r['level'] == lv
                          and r['status'] == 'ok'} for lv in AL.LEVEL_ORDER}
    # a block of the table that the base bundle lacks (a registered block with no placed address) is read from the
    # same 2020 block file; people registered outside Colorado stay polygon-less withheld areas
    missing_blocks = sorted(i for i in universe['block'] if i not in block_geoms and i.startswith('08'))
    added = {}
    if missing_blocks:
        import pyogrio
        frame = pyogrio.read_dataframe(args.blocks_gpkg, where='geoid IN (%s)' % ','.join(
            "'%s'" % i for i in missing_blocks))
        for ident, geom in zip(frame['geoid'].tolist(), frame.geometry.tolist()):
            added[ident] = geom
            block_geoms[ident] = geom
            geometry['block'][ident] = feature(ident, geom)
    require(published_ids['block'] <= set(block_geoms), 'A published registration block has no polygon')
    tract_members = {}
    for ident, geom in block_geoms.items():
        tract_members.setdefault(ident[:11], []).append(geom)
    require(published_ids['tract'] <= set(tract_members), 'A published registration tract has no polygon')
    tract_geoms = {t: dissolve(g) for t, g in tract_members.items() if t in universe['tract']}
    # precinct = the blocks whose modal precinct it is; a precinct that is nobody's modal precinct takes the block
    # where it holds the most people (reported, never silent)
    precinct_blocks = {}
    for blk_code, pid in block_precinct.items():
        precinct_blocks.setdefault(pid, []).append(block_ids[blk_code])
    orphans = sorted(universe['precinct'] - set(precinct_blocks))
    for pid in orphans:
        code = precinct_code.get(pid)
        if code is None:
            continue
        mask = p == code
        if mask.any():
            best_block = block_ids[int(b[mask][np.argmax(c_bp[mask])])]
            for other, members in precinct_blocks.items():
                if best_block in members and len(members) > 1:
                    members.remove(best_block)
            precinct_blocks.setdefault(pid, []).append(best_block)
    precinct_geoms = {}
    for pid in sorted(universe['precinct']):
        members = [block_geoms[i] for i in precinct_blocks.get(pid, []) if i in block_geoms]
        if members:
            precinct_geoms[pid] = dissolve(members)
    require(published_ids['precinct'] <= set(precinct_geoms), 'A published registration precinct has no polygon')
    zcta_geoms = {z: shape(f['geometry']) for z, f in geometry['zcta'].items()}
    zip_universe = universe['zip']
    zips_without_zcta = sorted(zip_universe - set(zcta_geoms))
    # concordance of ZIP: the share of people whose registration ZIP is the ZCTA containing their block's point
    block_zcta = {}
    zcta_tree = STRtree([zcta_geoms[z] for z in sorted(zcta_geoms)])
    zcta_order = sorted(zcta_geoms)
    for ident in populated_blocks:
        point = block_geoms[ident].representative_point() if ident in block_geoms else None
        if point is None:
            continue
        for h in zcta_tree.query(point, predicate='within').tolist():
            block_zcta[ident] = zcta_order[h]
            break
    bz_b, bz_z, c_bz = pair_counts(codes['a_reg_block'], codes['a_reg_zip'], len(zip_ids))
    total_bz = int(c_bz.sum())
    agree_zip = sum(int(c) for blk, zc, c in zip(bz_b.tolist(), bz_z.tolist(), c_bz.tolist())
                    if block_zcta.get(block_ids[blk]) == zip_ids[zc])
    zip_person_share = agree_zip / total_bz if total_bz else 0.0
    withheld_without_polygon = (len(universe['block'] - set(block_geoms)) + len(universe['tract'] - set(tract_geoms))
                                + len(universe['precinct'] - set(precinct_geoms)))
    log('geometry', {'added_blocks': len(added), 'withheld_without_polygon': withheld_without_polygon,
                     'tracts': len(tract_geoms), 'precincts': len(precinct_geoms), 'orphan_precincts': len(orphans),
                     'zips_without_zcta': len(zips_without_zcta)})
    adjacency = {'precinct': neighbours(precinct_geoms), 'tract': neighbours(tract_geoms),
                 'zip': neighbours({z: g for z, g in zcta_geoms.items() if z in zip_universe})}
    geometry['tract'] = {t: feature(t, g) for t, g in sorted(tract_geoms.items())}
    geometry['precinct'] = {pid: feature(pid, g) for pid, g in sorted(precinct_geoms.items())}

    # 6. manifest --------------------------------------------------------------------------------------------------
    concordance = {'precinct_person_share': round(precinct_person_share, 6),
                   'zip_person_share': round(zip_person_share, 6),
                   'precincts_without_block_majority': len(orphans),
                   'zips_without_zcta': len(zips_without_zcta),
                   'blocks_added_from_source': len(added),
                   'withheld_without_polygon': withheld_without_polygon}
    apply_v4_manifest(manifest, rows, adjacency, base_key=base_key, area_sha=area_sha, suppressed_sha=suppressed_sha,
                      frozen=frozen, run_id=receipt['run_id'], d1_audit=d1_audit,
                      created_at=meta_built(args.scores), concordance=concordance)
    key = manifest['snapshot']
    payload_files = {
        'manifest.json': dumps(manifest).encode('utf-8'), 'rows.json': blobs['rows.json'],
        'geometry.json': dumps(geometry).encode('utf-8'), 'streets.geojson': blobs['streets.geojson'],
        'district.geojson': blobs['district.geojson'],
        'area_indices.json': dumps(area_file).encode('utf-8'), 'adjacency.json': dumps(adjacency).encode('utf-8'),
    }

    # 7. write the ZIP, then check it with the app's own validator -------------------------------------------------
    box.stage('running', 'write', 'writing the ZIP (deterministic)')
    out = Path(args.out_dir) / ('co-cd005-%s.zip' % key[len('co-cd005-'):])
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_suffix('.zip.building')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in sorted(payload_files):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, payload_files[name])
    zip_sha = sha256_file(temporary)
    if not args.skip_validate:
        box.stage('running', 'validate', 'the app validator on the finished bundle')
        validate_zip(temporary)
    os.replace(temporary, out)
    Path(str(out) + '.sha256').write_text('%s *%s\n' % (zip_sha, out.name), encoding='ascii')
    result = {
        'snapshot': key, 'zip': out.name, 'zip_sha256': zip_sha, 'zip_bytes': out.stat().st_size,
        'base_snapshot': base_key, 'base_zip_sha256': base_sha, 'area_table_sha256': area_sha,
        'suppressed_ids_sha256': suppressed_sha, 'rows': len(rows),
        'levels': {'%s/%s' % (bs, lv): {'published': len(published[(bs, lv)]),
                                         'withheld': len(withheld[(bs, lv)])} for (bs, lv) in sorted(published)},
        'geometry': {'tracts': len(tract_geoms), 'precincts': len(precinct_geoms),
                     'orphan_precincts': len(orphans), 'zips_without_zcta': len(zips_without_zcta)},
        'concordance': manifest['geometry_method']['concordance'],
        'adjacency_edges': {lv: sum(len(v) for v in g.values()) // 2 for lv, g in adjacency.items()},
        'file_bytes': {name: len(data) for name, data in payload_files.items()},
        'seconds': round(time.time() - started, 1), 'validated': not args.skip_validate,
    }
    box.run('completed', 'complete', 'cu-summary-v4 bundle written: %d area rows, %d bytes' % (len(rows), result['zip_bytes']))
    return result


def meta_built(scores_path):
    connection = sqlite3.connect(Path(scores_path).as_uri() + '?mode=ro', uri=True)
    try:
        return 'Built from the score store written ' + json.loads(
            connection.execute("SELECT value FROM metadata WHERE key='built_utc'").fetchone()[0])
    finally:
        connection.close()


def validate_zip(path):
    """The same validator the importer uses (needs Django settings; no database is touched)."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'server.settings')
    os.environ.setdefault('CU_DATA_DIR', str(V3 / '.local'))
    import django
    django.setup()
    from server.research import bundles
    with zipfile.ZipFile(path) as archive:
        payload = {name: json.loads(archive.read(name)) for name in archive.namelist()}
    bundles.validate(payload)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Build the cu-summary-v4 bundle (fails closed).')
    parser.add_argument('--real', action='store_true', help='use the real rowfw files and the V3 score store')
    parser.add_argument('--rowfw', default=str(RFB))
    parser.add_argument('--base', default=str(BASE_ZIP))
    parser.add_argument('--scores', default=str(V3 / '.local' / 'scores.sqlite3'))
    parser.add_argument('--blocks-gpkg', default=str(BLOCKS_GPKG))
    parser.add_argument('--out-dir', default=str(V3 / 'data'))
    parser.add_argument('--receipt', default=str(V3 / '_build' / 'bundle_v4_receipt.json'))
    parser.add_argument('--skip-validate', action='store_true')
    parser.add_argument('--run-id', default=new_run_id('cu-geo-v3-bundle-v4'))
    args = parser.parse_args(argv)
    log = lambda *a: print(time.strftime('%H:%M:%S'), *a, flush=True)       # noqa: E731
    try:
        result = build(args, log)
    except BuildError as error:
        GlassBox(args.run_id, 'cu-geography-v3/tools/build_bundle_v4.py').failed('build', 'bundle build stopped',
                                                                              detail=str(error))
        print('STOPPED:', error, file=sys.stderr)
        return 2
    Path(args.receipt).write_text(json.dumps(result, indent=1), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('snapshot', 'zip', 'zip_sha256', 'zip_bytes', 'rows', 'geometry',
                                              'concordance', 'seconds')}, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
