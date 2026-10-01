"""Synthetic world for the v3 backend tests. Nothing here is real: people, names, addresses and areas are invented.

`build_world(directory)` writes, under `directory`:
  scores.sqlite3, contacts.sqlite3 (sentinel names/addresses/emails/phones), street.sqlite3   tools.make_scores_fixture
  a valid cu-summary-v4 bundle (ZIP + sha256)                                                  built from the fixture's
                                                                                               area table
The contact and street layers hold strings like SENTINEL_REAL_NAME_0007 so a test can prove that a response never
carries them (demo mode) or carries exactly them (real mode).
"""
import copy
import hashlib
import io
import json
import zipfile
from pathlib import Path

import numpy as np
from shapely.geometry import box, mapping
from shapely.ops import unary_union

from tools import area_layers as AL
from tools import make_scores_fixture as MF
from tools.build_bundle_v4 import apply_v4_manifest, make_area_file, neighbours
from tools.geography_levels import EXTRA_LEVELS

from .test_area_indices import hierarchy_bundle


class World:
    def __init__(self, **entries):
        self.__dict__.update(entries)


def polygon(ident, geom):
    minx, miny, maxx, maxy = geom.bounds
    return {'id': ident, 'geometry': json.loads(json.dumps(mapping(geom))), 'bbox': [minx, miny, maxx, maxy]}


def modal_pairs(first, second):
    """{first value: second value with the most rows (ties: smaller)} and the distinct-pair counts per first value."""
    counts = {}
    for a, b in zip(first, second):
        counts.setdefault(a, {}).setdefault(b, 0)
        counts[a][b] += 1
    modal = {a: sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] for a, c in counts.items()}
    return modal, {a: len(c) for a, c in counts.items()}


def v4_payload(fixture):
    """A valid in-memory cu-summary-v4 payload built from the fixture's area table (small geometry, real relations)."""
    data = fixture.data
    payload = hierarchy_bundle()
    reg_block = np.asarray(data['reg_block']).astype(str)
    reg_precinct = np.asarray(data['reg_precinct']).astype(str)
    reg_tract = np.asarray(data['reg_tract']).astype(str)
    reg_zip = np.asarray(data['reg_zip']).astype(str)
    block_ids = sorted(set(reg_block.tolist()) | {r['area_id'] for r in fixture.withheld
                                                  if r['basis'] == 'registration' and r['level'] == 'block'})
    block_geoms = {}
    for tract_index, tract in enumerate(MF.TRACTS):
        for k, ident in enumerate(i for i in block_ids if i[:11] == tract):
            block_geoms[ident] = box(-104.90 + 0.02 * tract_index + 0.003 * k, 38.80, -104.90 + 0.02 * tract_index
                                     + 0.003 * k + 0.0025, 38.8025)
    tract_geoms = {t: unary_union([g for i, g in block_geoms.items() if i[:11] == t]) for t in MF.TRACTS}
    precinct_geoms = {p: box(-104.90 + 0.003 * k, 38.81, -104.90 + 0.003 * k + 0.0028, 38.8125)
                      for k, p in enumerate(MF.PRECINCTS)}
    zcta_geoms = {z: box(-104.90 + 0.02 * k, 38.82, -104.90 + 0.02 * k + 0.0195, 38.83) for k, z in enumerate(MF.ZIPS)}
    geometry = payload['geometry.json']
    geometry['block'] = {i: polygon(i, g) for i, g in block_geoms.items()}
    geometry['tract'] = {i: polygon(i, g) for i, g in tract_geoms.items()}
    geometry['precinct'] = {i: polygon(i, g) for i, g in precinct_geoms.items()}
    geometry['zcta'] = {i: polygon(i, g) for i, g in zcta_geoms.items()}
    adjacency = {'precinct': neighbours(precinct_geoms), 'tract': neighbours(tract_geoms),
                 'zip': neighbours(zcta_geoms)}

    block_precinct, _ = modal_pairs(reg_block.tolist(), reg_precinct.tolist())
    precinct_tract, precinct_ntracts = modal_pairs(reg_precinct.tolist(), reg_tract.tolist())
    _, precinct_nblocks = modal_pairs(reg_precinct.tolist(), reg_block.tolist())
    _, tract_nblocks = modal_pairs(reg_tract.tolist(), reg_block.tolist())
    _, zip_nblocks = modal_pairs(reg_zip.tolist(), reg_block.tolist())

    def relations_for(basis, level, ident):
        if basis != 'registration':
            return {}
        if level == 'block':
            return {'precinct': block_precinct.get(ident)}
        if level == 'precinct':
            return {'tract': precinct_tract.get(ident), 'n_tracts': precinct_ntracts.get(ident),
                    'n_blocks': precinct_nblocks.get(ident)}
        if level == 'tract':
            return {'n_blocks': tract_nblocks.get(ident)}
        return {'n_blocks': zip_nblocks.get(ident)}

    area_file, _, _ = make_area_file(fixture.area_rows, fixture.withheld, relations_for)
    manifest = payload['manifest.json']
    base_key = manifest['snapshot']
    suppressed_sha = fixture.meta['suppressed_ids_sha256']
    district = unary_union(list(tract_geoms.values()) + list(zcta_geoms.values())).envelope
    payload['district.geojson'] = {'type': 'FeatureCollection', 'features': [{
        'type': 'Feature', 'properties': {'id': '0805', 'label': 'Colorado · District 05'},
        'geometry': json.loads(json.dumps(mapping(district)))}]}
    apply_v4_manifest(
        manifest, area_file['rows'], adjacency, base_key=base_key, area_sha=fixture.area_sha,
        suppressed_sha=suppressed_sha, frozen=dict(AL.FROZEN_SHA256), run_id='fixture-run',
        d1_audit={'exactly_disclosed': 0, 'symdiff_violations': 0, 'remainder_violations': 0},
        created_at='Synthetic fixture for the v3 tests',
        concordance={'precinct_person_share': 0.9, 'zip_person_share': 0.9, 'precincts_without_block_majority': 0,
                     'zips_without_zcta': 0, 'blocks_added_from_source': 0, 'withheld_without_polygon': 0})
    payload['area_indices.json'] = area_file
    payload['adjacency.json'] = adjacency
    return payload


def retarget(payload):
    """Recompute the manifest's content hashes (and snapshot-independent checks) after a test edits the files."""
    rf = payload['manifest.json']['row_framework']
    rows = payload['area_indices.json']['rows']
    rf['area_indices_sha256'] = AL.sha256_json(rows)
    rf['withheld_set_sha256'] = AL.withheld_set_sha256(rows)
    rf['adjacency_sha256'] = AL.sha256_json(payload['adjacency.json'])
    return payload


def write_zip(payload, path):
    """Write the payload as a ZIP (deterministic) and return its sha256."""
    path = Path(path)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(payload):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, json.dumps(payload[name], separators=(',', ':')))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_world(directory, n=1500, seed=MF.SEED):
    directory = Path(directory)
    fixture = MF.make_store(directory, n=n, seed=seed)
    payload = v4_payload(fixture)
    zip_path = directory / ('%s.zip' % payload['manifest.json']['snapshot'])
    sha = write_zip(payload, zip_path)
    return World(directory=directory, fixture=fixture, payload=payload, zip_path=zip_path, zip_sha256=sha,
                 snapshot=payload['manifest.json']['snapshot'])
