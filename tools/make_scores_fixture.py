"""Synthetic fixture with the exact P1 schema (numpy + stdlib; pyarrow only for the optional parquet files).

Everything here is invented: persons, households, scores, geography ids and the contact layer. Names, addresses, emails
and phones in the fixture contact layer are sentinels (SENTINEL_REAL_NAME_####, SENTINEL_ADDRESS_####, ...) so that a
test can prove that demo mode never lets a "real" name reach a response.

    make_arrays(n, seed)                 aligned arrays shaped like the P1 files (what tools.scores_writer expects)
    make_contacts(path, arrays, salt)    contacts.sqlite3 (schema contacts-v1, rowid = source row) with sentinel values
    make_street(path, arrays, salt)      street.sqlite3 (schema 1) for the registration and residential bases
    make_store(directory)                scores.sqlite3 + contacts + street + area table; returns a Fixture
    write_p1_files(directory, ...)       the three P1 parquet files, manifests, config, cache slice, area CSVs (pyarrow)

One command rebuilds a fixture for local work:  python -m tools.make_scores_fixture <directory> [--parquet]
"""
import argparse
import csv
import hashlib
import hmac
import json
import os
import sqlite3
from pathlib import Path

import numpy as np

from tools import field_labels as FL
from tools import scores_schema as S
from tools import scores_writer as W
from tools.scores_writer import street_household_rows

SEED = 20261001
ROWFW_VERSION = 'rowfw-1.0.0'
SOURCE_SHA256 = hashlib.sha256(b'cu-geography-v3 synthetic fixture source').hexdigest()
TRACTS = ['08041000101', '08041000102', '08041000200', '08041000300']
BLOCKS_PER_TRACT = [5, 4, 3, 3]
PRECINCTS = ['5000021101', '5000021102', '5000021103', '5000021104']
ZIPS = ['80903', '80904', '80905']
RES_TRACTS = TRACTS + ['06009000122']
RES_ZIPS = ZIPS + ['95221']
BREAKS = (20.0, 40.0, 60.0, 80.0)
ALL_GROUPS = ('ovr', 'cua', 'part', 'soi', 'lean')


def opaque_id(salt, kind, *parts):
    """Same construction as tools.build_street.opaque_id (kept local so this module needs no pandas)."""
    message = json.dumps([kind, *parts], ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return kind + '_' + hmac.new(salt, message, hashlib.sha256).hexdigest()


def midrank_pct(values):
    """100 * (L + E/2) / N on the ranking key round(x, 9) (framework_config standing.formula)."""
    key = np.round(np.asarray(values, dtype=np.float64), 9)
    ordered = np.sort(key)
    below = np.searchsorted(ordered, key, side='left')
    through = np.searchsorted(ordered, key, side='right')
    return 100.0 * (below + (through - below) / 2.0) / len(key)


def competition_rank(values):
    key = np.round(np.asarray(values, dtype=np.float64), 9)
    ordered = np.sort(key)
    greater = len(key) - np.searchsorted(ordered, key, side='right')
    tie = np.searchsorted(ordered, key, side='right') - np.searchsorted(ordered, key, side='left')
    return (1 + greater).astype(np.int64), tie.astype(np.int64)


def _grade_from(measured_share, width, thresholds):
    """Worse of an evidence letter (index into ABCD) and a precision letter."""
    evidence = np.where(measured_share >= 0.999, 0, np.where(measured_share >= 2 / 3, 1,
                        np.where(measured_share > 1 / 3, 2, 3)))
    precision = np.where(width <= thresholds[0], 0, np.where(width <= thresholds[1], 1,
                         np.where(width <= thresholds[2], 2, 3)))
    return np.array(list('ABCD'))[np.maximum(evidence, precision)]


def make_arrays(n=1500, seed=SEED):
    rng = np.random.default_rng(seed)
    # ---- households and geography ----------------------------------------------------------------------
    sizes = []
    while sum(sizes) < n:
        sizes.append(int(rng.choice([1, 2, 3, 4, 5], p=[0.28, 0.34, 0.16, 0.14, 0.08])))
    sizes[-1] -= sum(sizes) - n
    sizes = [s for s in sizes if s > 0]
    hh = np.repeat(np.arange(len(sizes)), sizes)
    hh_size = np.repeat(np.array(sizes), sizes)
    n_hh = len(sizes)

    block_ids, block_tract = [], []
    for tract, count in zip(TRACTS, BLOCKS_PER_TRACT):
        for b in range(count):
            block_ids.append(tract + '%04d' % (1001 + b))
            block_tract.append(tract)
    weights = np.array([0.5, 0.3, 0.14, 0.04, 0.02, 0.35, 0.3, 0.2, 0.15, 0.4, 0.4, 0.2, 0.5, 0.3, 0.2])
    weights = weights[:len(block_ids)] / weights[:len(block_ids)].sum()
    hh_block = rng.choice(len(block_ids), size=n_hh, p=weights)
    block_precinct = [PRECINCTS[i % len(PRECINCTS)] for i in range(len(block_ids))]
    block_zip = [ZIPS[min(TRACTS.index(t), len(ZIPS) - 1)] for t in block_tract]
    reg_block_h = np.array(block_ids)[hh_block]
    reg_tract_h = np.array(block_tract)[hh_block]
    reg_zip_h = np.array(block_zip)[hh_block]
    reg_precinct_h = np.array(block_precinct)[hh_block]
    # some blocks straddle two precincts
    flip = rng.random(n_hh) < 0.08
    reg_precinct_h = np.where(flip, np.array(PRECINCTS)[(np.searchsorted(PRECINCTS, reg_precinct_h) + 1) % len(PRECINCTS)],
                              reg_precinct_h)
    res_stay = rng.random(n_hh) < 0.92
    res_tract_h = np.where(res_stay, reg_tract_h, rng.choice(RES_TRACTS, size=n_hh))
    res_zip_h = np.where(res_stay, reg_zip_h, rng.choice(RES_ZIPS, size=n_hh))
    # a few people carry no residence ids (kept None) to exercise missing codes
    reg_block, reg_tract, reg_zip, reg_precinct = reg_block_h[hh], reg_tract_h[hh], reg_zip_h[hh], reg_precinct_h[hh]
    res_tract, res_zip = res_tract_h[hh], res_zip_h[hh]

    party = rng.choice(['D', 'R', 'U', 'G', 'L', 'O', 'T'], size=n, p=[0.2, 0.33, 0.42, 0.01, 0.02, 0.015, 0.005])
    age = rng.integers(18, 95, size=n)

    # ---- field evidence -------------------------------------------------------------------------------
    fields = FL.FIELDS
    value_dict = []
    ev_value = np.zeros((n, 40), dtype=np.uint16)
    ev_status = np.zeros((n, 40), dtype=np.uint8)
    ev_p = np.full((n, 40), np.nan, dtype=np.float32)
    ev_exposure = np.zeros((n, 40), dtype=np.float32)
    code50 = rng.random(n) < 0.012
    children_rule = rng.random(n) < 0.3
    owner_map = {'H': 1.0, 'O': 0.75, 'U': 0.5, 'P': 0.25, 'R': 0.0}
    for fid, spec in enumerate(fields):
        name, kind = spec['name'], spec['kind']
        estimated = np.zeros(n, dtype=bool)
        if name not in ('household_size', 'svi_party_registration', 'sii_homeowner') and kind != 'percentile':
            estimated = rng.random(n) < (0.16 if spec['group'] != 'lean' else 0.10)
        status = np.where(estimated, rng.integers(1, 5, size=n), 0).astype(np.uint8)
        if name == 'svi_party_registration':
            literals = sorted(set(party.tolist()) | {'D', 'R', 'U', 'G', 'L', 'O', 'T'})
            codes = np.array([literals.index(p) for p in party])
            exposure = np.array([{'D': 1.0, 'R': -1.0}.get(p, 0.0) for p in party])
            p_val = np.full(n, np.nan)
        elif kind == 'binary':
            literals = ['0', '1']
            observed_value = rng.random(n) < 0.45
            shown = np.where(estimated, rng.random(n) < 0.5, observed_value)
            p_shown = np.where(estimated, rng.uniform(0.55, 0.95, size=n), np.nan)
            exposure = np.where(estimated, np.where(shown, p_shown, 1 - p_shown), shown.astype(float))
            codes = shown.astype(int)
            p_val = p_shown
        elif kind == 'percentile':
            if name == 'household_size':
                raw = np.array(sizes)[hh]
                exposure = np.clip((raw - 1) / 6.0, 0, 1)
            else:
                exposure = rng.random(n)
                raw = np.rint(exposure * 100).astype(int)
            if name == 'sai_digital_reachability_score':
                raw = np.where(code50, 50, raw)
                exposure = np.where(code50, np.nan, exposure)
            literals = [str(v) for v in sorted(set(raw.tolist()))]
            codes = np.array([literals.index(str(v)) for v in raw])
            p_val = np.full(n, np.nan)
        elif kind == 'categorical_map':
            labels = list('HOUPR') if name == 'sci_owner_renter' else list('HUR') if name == 'sii_homeowner' \
                else list('GHPS')
            mapping = owner_map if name == 'sci_owner_renter' else {'H': 1.0, 'U': 0.5, 'R': 0.0} \
                if name == 'sii_homeowner' else {'S': 1.0, 'H': 0.5, 'P': 0.5, 'G': 0.5}
            literals = sorted(labels)
            picked = rng.choice(labels, size=n)
            codes = np.array([literals.index(v) for v in picked])
            base = np.array([mapping[v] for v in picked])
            p_shown = np.where(estimated, rng.uniform(0.5, 0.9, size=n), np.nan)
            exposure = np.where(estimated, base * 0.85 + 0.075, base)
            p_val = p_shown
        else:  # ridit
            if name == 'sci_number_of_adults_in_household':
                literals = [str(v) for v in range(1, 6)]
            else:
                literals = list('ABCD')
            picked = rng.integers(0, len(literals), size=n)
            codes = picked
            base = (picked + 0.5) / len(literals)
            p_shown = np.where(estimated, rng.uniform(0.5, 0.9, size=n), np.nan)
            exposure = base
            if name == 'sci_number_of_children_in_household':
                exposure = np.where(children_rule, 0.37, base)
            p_val = p_shown
        value_dict.append(literals)
        ev_value[:, fid] = codes
        ev_status[:, fid] = status
        ev_p[:, fid] = np.where(estimated, p_val, np.nan).astype(np.float32)
        ev_exposure[:, fid] = exposure.astype(np.float32)

    # ---- group scores from the stored (float32) exposures -----------------------------------------------
    contrib = W.contributions(ev_exposure, code50)
    slices = W.group_slices()
    est_weight = {}
    scores = {}
    for group, columns in slices.items():
        scores[group] = contrib[:, columns].sum(axis=1).astype(np.float32)
        weights_abs = np.array([abs(fields[c]['points']) for c in columns])
        est = (ev_status[:, columns] > 0).astype(float)
        est_weight[group] = (est * weights_abs[None, :]).sum(axis=1) / weights_abs.sum()

    data = {
        'person_id': None, 'hh': hh, 'hh_size': hh_size, 'age': age, 'party': party,
        'reg_block': reg_block, 'reg_precinct': reg_precinct, 'reg_tract': reg_tract, 'reg_zip': reg_zip,
        'res_tract': res_tract, 'res_zip': res_zip,
        'flag_no_consumer_match': rng.random(n) < 0.1, 'flag_cpi_blank': rng.random(n) < 0.15,
        'flag_children_rule': children_rule, 'flag_digital_code50': code50,
        'flag_geo_unaudited': rng.random(n) < 0.12, 'flag_rare_class_mass': rng.random(n) < 0.003,
        'has_location': np.zeros(n, dtype=bool),
        'ev_value': ev_value, 'ev_status': ev_status, 'ev_p': ev_p, 'ev_exposure': ev_exposure,
        'value_dict': value_dict,
    }
    reg_codes = np.array([W.REG_GROUP_INDEX[FL.reg_group_of(p)] for p in party])
    for group in ('cua', 'part', 'soi'):
        data[group + '_score'] = scores[group]
        data[group + '_pct'] = midrank_pct(scores[group]).astype(np.float32)
        data[group + '_rank'], data[group + '_tie_n'] = competition_rank(scores[group])
        width = (3 + 22 * est_weight[group]) * (0 if group == 'part' else 1)
        pct = data[group + '_pct'].astype(np.float64)
        data[group + '_pct_lo90'] = np.clip(pct - width, 0, 100).astype(np.float32)
        data[group + '_pct_hi90'] = np.clip(pct + width, 0, 100).astype(np.float32)
        data[group + '_measured_pct'] = (100 * (1 - est_weight[group])).astype(np.float32)
        data[group + '_grade'] = _grade_from(1 - est_weight[group], np.clip(pct + width, 0, 100) -
                                             np.clip(pct - width, 0, 100), (5, 15, 30))
    ovr = 0.40 * data['cua_pct'].astype(np.float64) + 0.35 * data['part_pct'].astype(np.float64) + \
        0.25 * data['soi_pct'].astype(np.float64)
    data['ovr_score'] = ovr.astype(np.float32)
    data['ovr_pct'] = midrank_pct(ovr).astype(np.float32)
    data['ovr_rank'], data['ovr_tie_n'] = competition_rank(ovr)
    spread = 2 + 10 * (0.4 * est_weight['cua'] + 0.35 * est_weight['part'] + 0.25 * est_weight['soi'])
    data['ovr_pct_lo90'] = np.clip(data['ovr_pct'] - spread, 0, 100).astype(np.float32)
    data['ovr_pct_hi90'] = np.clip(data['ovr_pct'] + spread, 0, 100).astype(np.float32)
    data['ovr_measured_pct'] = (0.4 * data['cua_measured_pct'] + 0.35 * data['part_measured_pct'] +
                                0.25 * data['soi_measured_pct']).astype(np.float32)
    worst = np.maximum.reduce([np.array([ 'ABCD'.index(g) for g in data[x + '_grade']]) for x in ('cua', 'part', 'soi')])
    precision = np.where(spread * 2 <= 5, 0, np.where(spread * 2 <= 15, 1, np.where(spread * 2 <= 30, 2, 3)))
    data['ovr_grade'] = np.array(list('ABCD'))[np.maximum(worst, precision)]
    for group in S.INDEX_IDS:
        key = ovr if group == 'ovr' else scores[group]
        within = np.zeros(n)
        for code in np.unique(reg_codes):
            mask = reg_codes == code
            within[mask] = midrank_pct(np.asarray(key)[mask])
        data[group + '_pct_within_reg'] = within.astype(np.float32)
        z = (data[group + '_pct'].astype(np.float64) - 90.0) / 4.0
        data[group + '_p_top10'] = (1 / (1 + np.exp(-z))).astype(np.float32)
    lean = scores['lean'].astype(np.float64)
    data['lean'] = scores['lean']
    spread_l = 4 + 30 * est_weight['lean']
    data['lean_lo90'] = np.clip(lean - spread_l, -100, 100).astype(np.float32)
    data['lean_hi90'] = np.clip(lean + spread_l, -100, 100).astype(np.float32)
    data['lean_measured_pct'] = (100 * (1 - est_weight['lean'])).astype(np.float32)
    data['lean_band'] = np.array([FL.lean_band_of(float(v)) for v in data['lean']], dtype=np.int8)
    data['lean_p_same_band'] = rng.uniform(0.6, 1.0, size=n).astype(np.float32)
    data['lean_band_undetermined'] = (~np.isin(party, ['D', 'R'])) & (data['lean_p_same_band'] < 0.7)
    data['lean_grade'] = _grade_from(1 - est_weight['lean'], np.clip(lean + spread_l, -100, 100) -
                                     np.clip(lean - spread_l, -100, 100), (10, 25, 50))
    # household table
    hh_codes = np.arange(n_hh)
    counts = np.bincount(hh, minlength=n_hh)
    hh_mean = np.bincount(hh, weights=data['ovr_pct'].astype(np.float64), minlength=n_hh) / counts
    hh_max = np.zeros(n_hh)
    np.maximum.at(hh_max, hh, data['ovr_pct'].astype(np.float64))
    household = {
        'hh': hh_codes, 'hh_n': counts, 'hh_ovr_mean_pct': hh_mean, 'hh_ovr_max_pct': hh_max,
        'hh_n_top10': np.bincount(hh, weights=data['ovr_p_top10'].astype(np.float64), minlength=n_hh),
        'hh_pct': midrank_pct(hh_mean),
        'hh_lean_mean': np.bincount(hh, weights=lean, minlength=n_hh) / counts,
        'hh_measured_pct_mean': np.bincount(hh, weights=data['ovr_measured_pct'].astype(np.float64), minlength=n_hh) / counts,
    }
    for band, suffix in ((-2, 'm2'), (-1, 'm1'), (0, 'p0'), (1, 'p1'), (2, 'p2')):
        household['hh_n_lean_band_' + suffix] = np.bincount(hh, weights=(data['lean_band'] == band).astype(float),
                                                            minlength=n_hh).astype(int)
    worst_h = np.zeros(n_hh, dtype=int)
    np.maximum.at(worst_h, hh, np.array(['ABCD'.index(g) for g in data['ovr_grade']]))
    household['hh_worst_grade'] = np.array(list('ABCD'))[worst_h]
    data['household'] = household
    data['street_household'] = []
    persist = np.array(['PERSIST-%06d' % (i + 1) for i in range(n)])
    raw_household = np.array(['RAWHH-%06d' % h for h in hh])
    return data, persist, raw_household


def make_contacts(path, data, persist, salt, source_sha=SOURCE_SHA256):
    path = Path(path)
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    try:
        connection.executescript('''
            CREATE TABLE contacts (id TEXT PRIMARY KEY, name TEXT, email TEXT, email_match TEXT,
                phone TEXT, wireless TEXT, wireless_match TEXT, registration TEXT, residential TEXT);
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
            CREATE VIRTUAL TABLE search USING fts5(name,email,phone,address,content='');
        ''')
        rows, search = [], []
        for i, ident in enumerate(persist.tolist(), start=1):
            name = 'SENTINEL_REAL_NAME_%04d' % i
            rows.append((i, opaque_id(salt, 'p', ident), name, 'sentinel%04d@example.invalid' % i, 'H', '555-%04d' % i,
                         '555-%04d' % i, 'H', 'SENTINEL_ADDRESS_%04d' % i, 'SENTINEL_RESIDENCE_%04d' % i))
            search.append((i, name, 'sentinel%04d@example.invalid' % i, '555%04d' % i, 'SENTINEL_ADDRESS_%04d' % i))
        connection.executemany('INSERT INTO contacts(rowid,id,name,email,email_match,phone,wireless,wireless_match,'
                               'registration,residential) VALUES (?,?,?,?,?,?,?,?,?,?)', [r[:5] + r[5:] for r in rows])
        connection.executemany('INSERT INTO search(rowid,name,email,phone,address) VALUES (?,?,?,?,?)', search)
        connection.execute('CREATE INDEX contacts_name ON contacts(name COLLATE NOCASE,id)')
        for key, value in {'schema': 'contacts-v1', 'source_sha256': source_sha,
                           'totals': {'records': len(rows), 'with_email': len(rows), 'with_phone': len(rows),
                                      'with_wireless': len(rows)}}.items():
            connection.execute('INSERT INTO metadata VALUES (?,?)', (key, json.dumps(value)))
        connection.commit()
    finally:
        connection.close()
    return path


def make_street(path, data, persist, raw_household, salt, seed=SEED, source_sha=SOURCE_SHA256, located=0.9):
    """Registration and residential households with fixture coordinates; returns [(h_id, source_row_index)] pairs."""
    rng = np.random.default_rng(seed + 1)
    path = Path(path)
    if path.exists():
        path.unlink()
    n = len(persist)
    connection = sqlite3.connect(path)
    pairs = []
    try:
        connection.executescript('''
            PRAGMA foreign_keys=ON;
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE households (id TEXT PRIMARY KEY, basis TEXT NOT NULL CHECK(basis IN ('registration','residential')),
                longitude REAL NOT NULL, latitude REAL NOT NULL, address TEXT NOT NULL, count INTEGER NOT NULL CHECK(count > 0));
            CREATE TABLE people (household_id TEXT NOT NULL REFERENCES households(id), id TEXT NOT NULL, name TEXT NOT NULL,
                PRIMARY KEY (household_id, id));
        ''')
        block_origin = {}
        totals = {}
        for basis in ('registration', 'residential'):
            houses, people = {}, []
            for hh_code in np.unique(data['hh']):
                members = np.flatnonzero(data['hh'] == hh_code)
                if rng.random() > located:
                    continue
                block = str(data['reg_block'][members[0]])
                origin = block_origin.setdefault(block, (-104.86 + rng.random() * 0.05, 38.80 + rng.random() * 0.05))
                lon = round(origin[0] + rng.random() * 0.004, 6)
                lat = round(origin[1] + rng.random() * 0.004, 6)
                address = 'SENTINEL_STREET_%s_%d' % (basis[:3].upper(), hh_code)
                h_id = opaque_id(salt, 'h', basis, str(raw_household[members[0]]), address.casefold(), lon, lat)
                houses[h_id] = (basis, lon, lat, address, len(members))
                for m in members:
                    people.append((h_id, opaque_id(salt, 'p', str(persist[m])), 'SENTINEL_REAL_NAME_%04d' % (m + 1)))
                    if basis == 'registration':
                        pairs.append((h_id, int(m)))
            connection.executemany('INSERT INTO households VALUES (?,?,?,?,?,?)',
                                   [(k, *v) for k, v in houses.items()])
            connection.executemany('INSERT INTO people VALUES (?,?,?)', people)
            totals[basis] = {'households': len(houses), 'people': len(people)}
        connection.execute('CREATE INDEX household_basis_id ON households(basis,id)')
        connection.execute('CREATE INDEX household_latitude ON households(basis, latitude, longitude, id)')
        connection.execute('CREATE INDEX household_longitude ON households(basis, longitude, latitude, id)')
        connection.execute('CREATE INDEX people_identifier ON people(id,household_id)')
        for key, value in {'schema_version': '1', 'source_sha256': source_sha, 'bases': totals}.items():
            connection.execute('INSERT INTO metadata VALUES (?,?)', (key, json.dumps(value)))
        connection.commit()
    finally:
        connection.close()
    return path, pairs



# ---- area table (every area with the CSV columns; simplified estimates, valid relations) ----------------------------

def _area_ids(data, basis):
    if basis == 'registration':
        return {'block': data['reg_block'], 'precinct': data['reg_precinct'], 'tract': data['reg_tract'],
                'zip': data['reg_zip']}
    return {'tract': data['res_tract'], 'zip': data['res_zip']}


def _band(value):
    return 1 + sum(1 for b in BREAKS if b <= value)


def make_area_rows(data, seed=SEED):
    """Rows in the layout of the shown-areas table (tools/shown_areas.py): EVERY area, one row per group.

    Estimates are simple (a shrink toward the parent mean), but every relation the v4 validator checks holds: midrank
    standings over all the areas of the level, bands and shares.
    """
    values = {'ovr': data['ovr_score'], 'cua': data['cua_score'], 'part': data['part_score'], 'soi': data['soi_score'],
              'lean': data['lean']}
    parts = {}
    for basis in ('registration', 'residence'):
        for level, ids in _area_ids(data, basis).items():
            ids = np.asarray(ids).astype(str)
            stats = {}
            for area in sorted(set(ids.tolist())):
                members = np.flatnonzero(ids == area)
                stats[area] = (members, len(members), len(set(np.asarray(data['hh'])[members].tolist())))
            parts[(basis, level)] = stats
    tract_ids = np.asarray(data['reg_tract']).astype(str)
    file_share = {g: float(np.asarray(data[g + '_p_top10'], dtype=np.float64).mean()) for g in S.INDEX_IDS}
    rows_out = []
    for (basis, level), stats in parts.items():
        ok = sorted(stats)
        for group in ALL_GROUPS:
            column = np.asarray(values[group], dtype=np.float64)
            measured = np.asarray(data[group + '_measured_pct'], dtype=np.float64)
            file_mean = float(column.mean())
            raw, eb, share_raw, share_eb = {}, {}, {}, {}
            for a in ok:
                members, rows, _ = stats[a]
                raw[a] = float(column[members].mean())
                parent = float(column[tract_ids == a[:11]].mean()) if level == 'block' else file_mean
                weight = 1 / (1 + rows / 30.0)
                eb[a] = weight * parent + (1 - weight) * raw[a]
                if group != 'lean':
                    top = np.asarray(data[group + '_p_top10'], dtype=np.float64)
                    share_raw[a] = float(top[members].mean())
                    share_eb[a] = 0.8 * share_raw[a] + 0.2 * file_share[group]
            if group != 'lean' and ok:
                pct = dict(zip(ok, midrank_pct([eb[a] for a in ok])))
                tpct = dict(zip(ok, midrank_pct([share_eb[a] for a in ok])))
            for a in ok:
                members, rows, households = stats[a]
                row = {name: '' for name in json.load(open(Path(__file__).with_name('p1_schema.json'),
                                                           encoding='utf-8'))['area_scores_columns']}
                row.update({
                    'basis': basis, 'level': level, 'area_id': a, 'group': group,
                    'parent_id': a[:11] if level == 'block' else 'file', 'suppressed': 0, 'suppressed_primary': 0.0,
                    'n_rows': float(rows), 'n_households': float(households), 'raw_mean': raw[a], 'eb_mean': eb[a],
                    'eb_se': float(2.0 / np.sqrt(rows)), 'shrink_B': 1 / (1 + rows / 30.0),
                    'measured_pct_mean': float(measured[members].mean()),
                })
                if group != 'lean':
                    p, t = float(pct[a]), float(tpct[a])
                    row.update({
                        'area_pct': p, 'band': _band(p), 'area_pct_lo90': max(0.0, p - 10),
                        'area_pct_hi90': min(100.0, p + 10), 'top10_share_raw': share_raw[a],
                        'top10_share_eb': share_eb[a], 'top10_shrink_B': 0.2, 'top10_eb_se': 0.02,
                        'top10_area_pct': t, 'top10_band': _band(t), 'top10_area_pct_lo90': max(0.0, t - 12),
                        'top10_area_pct_hi90': min(100.0, t + 12),
                    })
                    if group == 'cua':
                        row['md_share_mean'] = 0.1
                        row['n_cpi_blank'] = float(np.asarray(data['flag_cpi_blank'])[members].sum())
                else:
                    bands = np.asarray(data['lean_band'])[members]
                    shares = [float((bands == b).mean()) for b in (-2, -1, 0, 1, 2)]
                    shares[2] = 1.0 - shares[0] - shares[1] - shares[3] - shares[4]
                    parties = np.asarray(data['party'])[members]
                    row.update({
                        'share_band_m2': shares[0], 'share_band_m1': shares[1], 'share_band_p0': shares[2],
                        'share_band_p1': shares[3], 'share_band_p2': shares[4],
                        'share_reg_D': float((parties == 'D').mean()), 'share_reg_R': float((parties == 'R').mean()),
                        'share_reg_U': float((parties == 'U').mean()),
                        'share_band_undetermined': float(np.asarray(data['lean_band_undetermined'])[members].mean()),
                    })
                rows_out.append(row)
    return rows_out


class Fixture:
    """Paths and ground truth of one synthetic fixture."""
    def __init__(self, **entries):
        self.__dict__.update(entries)


def make_source_csv(path, persist):
    """A tiny stand-in for the supplied CSV: only the persist-id column matters to the W1 census."""
    with open(path, 'w', newline='', encoding='cp1252') as stream:
        writer = csv.writer(stream)
        writer.writerow(['sii_persist_id', 'sii_statara_id', 'filler'])
        for i, ident in enumerate(persist.tolist(), start=1):
            writer.writerow([ident, 'STAT-%06d' % i, 'x'])
    return W.sha256_file(path)


def make_store(directory, n=1500, seed=SEED, salt=None, demo_key=None, parquet=False):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    salt = salt or hashlib.sha256(b'fixture salt %d' % seed).digest()
    demo_key = demo_key or hashlib.sha256(b'fixture demo key %d' % seed).digest()
    data, persist, raw_household = make_arrays(n, seed)
    person_id = np.array([opaque_id(salt, 'p', str(p)) for p in persist.tolist()])
    data['person_id'] = person_id
    source_sha = make_source_csv(directory / 'source.csv', persist)
    (directory / '.street-salt').write_bytes(salt)
    contacts_path = make_contacts(directory / 'contacts.sqlite3', data, persist, salt, source_sha)
    street_path, pairs = make_street(directory / 'street.sqlite3', data, persist, raw_household, salt, seed, source_sha)
    located = {r for _, r in pairs}
    data['has_location'] = np.array([i in located for i in range(n)])
    data['street_household'] = street_household_rows(pairs, data)
    area_rows = make_area_rows(data, seed)
    area_csv = directory / 'area_scores_shown.csv'
    columns = json.load(open(Path(__file__).with_name('p1_schema.json'), encoding='utf-8'))['area_scores_columns']
    with open(area_csv, 'w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(area_rows)
    area_sha = W.sha256_file(area_csv)
    area_csv.with_suffix('.manifest.json').write_text(json.dumps({'sha256': area_sha}), encoding='utf-8')
    meta = {
        'rowfw_version': ROWFW_VERSION,
        'inputs': {'fixture': True, 'note': 'synthetic fixture; no P1 file was read'},
        'w1': {'status': 'fixture', 'note': 'synthetic persist ids; the HMAC census is not applicable'},
        'field_catalog': field_catalog(),
        'contacts_source_sha256': source_sha,
        'area_table_sha256': area_sha,
    }
    scores_path = directory / 'scores.sqlite3'
    receipt = W.write_scores_db(scores_path, data, meta, demo_key)
    fixture = Fixture(directory=directory, scores_path=scores_path, contacts_path=contacts_path,
                      street_path=street_path, data=data, persist=persist, raw_household=raw_household, salt=salt,
                      demo_key=demo_key, area_rows=area_rows, area_csv=area_csv,
                      area_sha=area_sha, receipt=receipt, pairs=pairs, n=n, meta=meta,
                      source_csv=directory / 'source.csv', source_sha=source_sha)
    if parquet:
        write_p1_files(fixture)
    return fixture


def _arrow_type(pa, text):
    if text.startswith('dictionary'):
        return pa.dictionary(pa.int16(), pa.string())
    return pa.type_for_alias({'float': 'float32', 'double': 'float64'}.get(text, text))


def _arrow_column(pa, name, text, values, n):
    """One column: the provided values when we have them, otherwise a neutral filler of the right type."""
    kind = _arrow_type(pa, text)
    if values is None:
        if pa.types.is_dictionary(kind):
            return pa.array(['A'] * n).dictionary_encode()
        if pa.types.is_boolean(kind):
            return pa.array(np.zeros(n, dtype=bool))
        return pa.array(np.zeros(n), type=pa.float64()).cast(kind)
    if pa.types.is_dictionary(kind):
        return pa.array([str(v) for v in np.asarray(values).tolist()]).dictionary_encode()
    array = np.asarray(values)
    if pa.types.is_floating(kind):
        return pa.array(array.astype(np.float64), mask=np.isnan(array.astype(np.float64)), type=pa.float64()).cast(kind)
    if pa.types.is_boolean(kind):
        return pa.array(array.astype(bool))
    return pa.array(array.astype(np.int64)).cast(kind)


def write_p1_files(fixture):
    """Write the three P1 parquet files + manifests, the config and the cache slice (needs pyarrow).

    The layout matches <RFB>: row_scores_rowfw-1.0.0.parquet ..., framework_config.json; the cache is
    <directory>/cache/inputs.parquet; the shown-areas table is <directory>/area_scores_shown.csv (+ .manifest.json).
    """
    import pyarrow as pa
    import pyarrow.parquet as pq
    schema = json.load(open(Path(__file__).with_name('p1_schema.json'), encoding='utf-8'))
    data, n = fixture.data, fixture.n
    root = fixture.directory / 'rowfw'
    (root / 'summaries').mkdir(parents=True, exist_ok=True)
    (fixture.directory / 'cache').mkdir(parents=True, exist_ok=True)
    keys = np.arange(1, n + 1)

    # cache slice (the columns the loader reads)
    party = np.asarray(data['party'])
    cache = {
        'model_source_row': pa.array(keys.astype(np.int32)), 'household_code': pa.array(data['hh'].astype(np.int32)),
        'household_size': pa.array(data['hh_size'].astype(np.int16)), 'sii_age': pa.array(data['age'].astype(np.int8)),
        'svi_party_registration': pa.array(party.tolist()).dictionary_encode(),
        'svi_party_registration_rollup': pa.array([FL.reg_group_of(str(p)) for p in party]).dictionary_encode(),
    }
    for target, column in (('registration_block15', 'reg_block'), ('svi_registration_precinct_code', 'reg_precinct'),
                           ('registration_tract11', 'reg_tract'), ('svi_registration_zip', 'reg_zip'),
                           ('sii_tract11', 'res_tract'), ('sii_zip', 'res_zip')):
        cache[target] = pa.array([str(v) for v in np.asarray(data[column]).tolist()]).dictionary_encode()
    cache_path = fixture.directory / 'cache' / 'inputs.parquet'
    pq.write_table(pa.table(cache), cache_path, compression='zstd')
    cache_sha = W.sha256_file(cache_path)

    # config (only what the loader and the app read)
    fields = {}
    for row in FL.FIELDS:
        entry = {'group': row['group']}
        if row['group'] == 'lean':
            entry['index_weight_signed'] = {'value': row['points'] / 100.0}
        else:
            entry['index_weight'] = {'value': row['points'] / 100.0}
        if row['group'] == 'part':
            entry['index_weight_code50_rows'] = {'value': row['points_code50'] / 100.0}
        fields[row['name']] = entry
    counts = fixture.receipt['reg_group_counts']
    config = {'schema': 'fixture', 'field_order': list(FL.FIELD_ORDER), 'fields': fields,
              'groups': {'lean': {'bands': {'cuts': [25, 75]}}},
              'standing': {'within_registration': {
                  'groups': {'D': ['D'], 'R': ['R'], 'U': ['U'], 'other': ['G', 'L', 'O', 'T']},
                  'group_counts_at_freeze': {'D': counts['D'], 'R': counts['R'], 'U': counts['U'], 'other': counts['O']}}}}
    config_path = root / 'framework_config.json'
    config_path.write_text(json.dumps(config, indent=1), encoding='utf-8')
    frozen = {'config_sha256': W.sha256_file(config_path), 'plan_sha256': hashlib.sha256(b'plan').hexdigest(),
              'criteria_sha256': hashlib.sha256(b'criteria').hexdigest(),
              'prereg_sha256': hashlib.sha256(b'prereg').hexdigest(),
              'rulings_sha256': hashlib.sha256(b'rulings').hexdigest(),
              'deviations_sha256': hashlib.sha256(b'deviations').hexdigest(),
              'dictionary_sha256': hashlib.sha256(b'dictionary').hexdigest(),
              'inputs': {'cache_sha256': cache_sha, 'export_sha256': hashlib.sha256(b'export').hexdigest()}}

    summary_columns = [
        'cua_score', 'part_score', 'soi_score', 'ovr_score', 'lean', 'cua_pct', 'part_pct', 'soi_pct', 'ovr_pct',
        'cua_pct_lo90', 'cua_pct_hi90', 'part_pct_lo90', 'part_pct_hi90', 'soi_pct_lo90', 'soi_pct_hi90',
        'ovr_pct_lo90', 'ovr_pct_hi90', 'lean_lo90', 'lean_hi90', 'cua_grade', 'part_grade', 'soi_grade',
        'lean_grade', 'ovr_grade', 'cua_measured_pct', 'part_measured_pct', 'soi_measured_pct', 'lean_measured_pct',
        'ovr_measured_pct', 'lean_band', 'cua_pct_within_reg', 'part_pct_within_reg', 'soi_pct_within_reg',
        'ovr_pct_within_reg', 'flag_children_rule']

    def write(stem, provided, key, extra):
        columns = schema[stem]['columns']
        arrays = [_arrow_column(pa, name, arrow, provided.get(name), len(provided[key])) for name, _, arrow in columns]
        tbl = pa.table(dict(zip([c[0] for c in columns], arrays)))
        path = root / ('%s_rowfw-1.0.0.parquet' % stem)
        pq.write_table(tbl, path, compression='zstd')
        manifest = {'version': ROWFW_VERSION, 'phase': 'P1', 'file': path.name, 'sha256': W.sha256_file(path),
                    'bytes': os.path.getsize(path), 'rows': tbl.num_rows, 'n_columns': tbl.num_columns,
                    'columns': [{'name': c[0], 'dtype': c[1]} for c in columns], 'key': schema[stem]['key'],
                    'key_unique': True, 'key_sorted_ascending': True, 'run_id': 'fixture-run', **frozen, **extra}
        (root / ('%s_rowfw-1.0.0.manifest.json' % stem)).write_text(json.dumps(manifest, indent=1), encoding='utf-8')

    row_provided = {name: data[name] for name in data if isinstance(data[name], np.ndarray) and
                    getattr(data[name], 'ndim', 0) == 1 and len(data[name]) == n}
    row_provided['model_source_row'] = keys
    write('row_scores', row_provided, 'model_source_row', {})

    profile = {'model_source_row': keys, 'household_code': data['hh'], 'household_size': data['hh_size']}
    for name in summary_columns:
        profile[name] = row_provided[name]
    for fid, field in enumerate(FL.FIELD_ORDER):
        literals = fixture.data['value_dict'][fid]
        profile[field + '__value'] = np.array([literals[c] for c in data['ev_value'][:, fid]])
        profile[field + '__status'] = np.array([FL.STATUSES[c] for c in data['ev_status'][:, fid]])
        profile[field + '__p'] = data['ev_p'][:, fid].astype(np.float64)
        profile[field + '__exposure'] = data['ev_exposure'][:, fid].astype(np.float64)
    # integer-typed __value columns need integers, dictionary-typed ones keep the literal text
    for name, _, arrow in schema['profiles']['columns']:
        if name.endswith('__value') and not arrow.startswith('dictionary'):
            profile[name] = profile[name].astype(np.int64)
    write('profiles', profile, 'model_source_row', {'summary_columns': summary_columns})

    household = dict(data['household'])
    household['household_code'] = household['hh']
    write('household_scores', household, 'household_code', {})

    fixture.rowfw_dir = root
    fixture.cache_path = cache_path
    return root


def field_catalog():
    """Non-personal catalog stored in metadata (labels, groups, kinds, weights)."""
    return [{'fid': f['fid'], 'name': f['name'], 'group': f['group'], 'label': f['label'], 'kind': f['kind'],
             'points': f['points'], 'points_code50': f['points_code50']} for f in FL.FIELDS]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('directory')
    parser.add_argument('--n', type=int, default=1500)
    parser.add_argument('--parquet', action='store_true', help='also write the P1 parquet files (needs pyarrow)')
    args = parser.parse_args(argv)
    fixture = make_store(args.directory, args.n, parquet=args.parquet)
    print(json.dumps({'directory': str(fixture.directory), 'scores': str(fixture.scores_path),
                      'bytes': fixture.receipt['bytes'], 'rows': fixture.receipt['rows']}))


if __name__ == '__main__':
    main()
