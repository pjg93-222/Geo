"""Offline loader: P1 parquet files + area table -> V3/.local/scores.sqlite3 (fails closed).

Run with the system Python that has pyarrow (the app's venv has none):

  env -u PYTHONPATH PYTHONNOUSERSITE=1 /c/Users/pgoff/AppData/Local/Programs/Python/Python312/python.exe \\
      tools/build_scores_v3.py --rowfw "<RFB>" --cache "<RF>/cache/inputs.parquet" \\
      --source-csv "C:/Users/pgoff/Downloads/statara_co_cd005_20260904.csv" --salt-file "<ROOT>/.local/.street-salt" \\
      --data-dir "<V3>/.local"

or the one-command form:  python tools/build_scores_v3.py --real

What it checks before it writes (any failure stops the build and leaves the previous store untouched):
  * sha256 of the three P1 parquet files against their manifests, and the three manifests against each other;
  * the frozen config, cache and area table (the area table has no manifest: its sha256 is recorded, and its structure
    is checked against the D2 receipt: no residence blocks, no hatch column, published and withheld counts);
  * the field weights of tools/field_labels.py against framework_config.json;
  * keys 1..N in all three files, profile summary columns bit-identical with the row file, lean band rule;
  * the crosswalk W1: contacts.rowid == model_source_row and contacts.id == HMAC(salt, persist id of that row) on every
    row, with a shifted-by-one control that must match nothing; every street person id is in the crosswalk;
  * sum of the field contributions equals the index score (and the lean) on every row;
  * balanced K_actual, grade counts, lean band counts and flag counts against the build's own summaries.

The store holds no name, address, email, phone, coordinate or source-row number. Counts only are printed.
"""
import argparse
import csv
import ctypes
import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import districts as D               # noqa: E402
from tools import field_labels as FL           # noqa: E402
from tools import scores_schema as S           # noqa: E402
from tools import scores_writer as W           # noqa: E402
from tools.glassbox import GlassBox, new_run_id    # noqa: E402

V3 = Path(__file__).resolve().parents[1]
ROOT = Path(r'C:\Users\pgoff\Documents\Codex\2026-09-27\we-are-going-to-have-an\outputs\cu-geography')
RF = ROOT / '.local' / 'row-framework-20260930'
RFB = RF / 'build' / 'rowfw-1.0.0'
VERSION = 'rowfw-1.0.0'
STEMS = ('row_scores', 'household_scores', 'profiles')
SUMMARY_COLUMNS_B41 = None      # read from the profiles manifest
AREA_LEVELS_D2 = {('registration', 'block'), ('registration', 'precinct'), ('registration', 'tract'),
                  ('registration', 'zip'), ('residence', 'tract'), ('residence', 'zip')}
MIN_FREE_GB = 5.0
MIN_RAM_GB = 3.5


class BuildError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise BuildError(message)


def check_rollup(district, rollup, reg_group_text):
    """The cache rollup must equal the party mapping, unless the district turns the check off (OR: the vendor moves U to O)."""
    if district['party_rollup_check']:
        require(np.array_equal(rollup, reg_group_text), 'Cache rollup differs from the party mapping')


def available_ram_gb():
    class Status(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong), ('total', ctypes.c_ulonglong),
                    ('avail', ctypes.c_ulonglong), ('tpf', ctypes.c_ulonglong), ('apf', ctypes.c_ulonglong),
                    ('tv', ctypes.c_ulonglong), ('av', ctypes.c_ulonglong), ('ex', ctypes.c_ulonglong)]
    status = Status()
    status.length = ctypes.sizeof(Status)
    try:
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return status.avail / 2 ** 30
    except (AttributeError, OSError):
        return float('inf')


def load_json(path):
    with open(path, encoding='utf-8') as stream:
        return json.load(stream)


# ---- inputs -------------------------------------------------------------------------------------------------------

def verify_manifests(rowfw, log):
    manifests, shas = {}, {}
    for stem in STEMS:
        manifest_path = rowfw / ('%s_%s.manifest.json' % (stem, VERSION))
        parquet_path = rowfw / ('%s_%s.parquet' % (stem, VERSION))
        require(manifest_path.exists() and parquet_path.exists(), 'Missing %s manifest or parquet' % stem)
        manifest = load_json(manifest_path)
        actual = W.sha256_file(parquet_path)
        require(actual == manifest['sha256'], '%s: parquet sha256 does not match its manifest' % stem)
        require(os.path.getsize(parquet_path) == manifest['bytes'], '%s: byte size differs from the manifest' % stem)
        require(manifest['version'] == VERSION and manifest.get('phase') == 'P1', '%s: unexpected version/phase' % stem)
        manifests[stem] = manifest
        shas[stem] = actual
        log('manifest verified', stem, actual[:12])
    for key in ('config_sha256', 'plan_sha256', 'criteria_sha256', 'prereg_sha256', 'rulings_sha256',
                'deviations_sha256', 'dictionary_sha256'):
        values = {manifests[stem].get(key) for stem in STEMS}
        require(len(values) == 1 and None not in values, 'Manifests disagree on ' + key)
    inputs = {json.dumps(manifests[stem]['inputs'], sort_keys=True) for stem in STEMS}
    require(len(inputs) == 1, 'Manifests disagree on inputs')
    rows = {stem: manifests[stem]['rows'] for stem in STEMS}
    require(rows['row_scores'] == rows['profiles'], 'Row counts differ between the row and profile files')
    return manifests, shas


def verify_config(rowfw, manifest, log):
    path = rowfw / 'framework_config.json'
    actual = W.sha256_file(path)
    require(actual == manifest['config_sha256'], 'framework_config.json sha256 differs from the manifests')
    config = load_json(path)
    require(tuple(config['field_order']) == FL.FIELD_ORDER, 'field_order differs from tools/field_labels.py')
    for row in FL.FIELDS:
        spec = config['fields'][row['name']]
        require(spec['group'] == row['group'], 'Group differs for ' + row['name'])
        weight = spec.get('index_weight') or spec.get('index_weight_signed')
        require(abs(weight['value'] * 100 - row['points']) < 1e-9, 'Weight differs for ' + row['name'])
        if row['group'] == 'part':
            require(abs(spec['index_weight_code50_rows']['value'] * 100 - row['points_code50']) < 1e-9,
                    'Code-50 weight differs for ' + row['name'])
    lean_bands = config['groups']['lean']['bands']
    require(lean_bands['cuts'] == [25, 75], 'Lean band cuts differ from 25/75')
    groups = config['standing']['within_registration']['groups']
    require(groups == {'D': ['D'], 'R': ['R'], 'U': ['U'], 'other': ['G', 'L', 'O', 'T']},
            'Registration groups differ')
    log('config verified', actual[:12])
    return config, actual


def check_area_table(rowfw, log):
    """Structure of the D2 area table (it has no manifest): returns the facts to record."""
    csv_path = rowfw / 'area_scores_rowfw-1.0.0.csv'
    suppressed_path = rowfw / 'summaries' / 'area_suppressed_ids.csv'
    require(csv_path.exists() and suppressed_path.exists(), 'Area table or withheld list is missing')
    schema = load_json(V3 / 'tools' / 'p1_schema.json')['area_scores_columns']
    seen, per_level, keys = set(), {}, set()
    with open(csv_path, newline='', encoding='utf-8') as stream:
        reader = csv.DictReader(stream)
        require(reader.fieldnames == schema, 'Area table columns differ from the D2 schema')
        require('cua_hatch_cpi_blank' not in reader.fieldnames, 'Area table still carries the hatch column')
        for row in reader:
            level = (row['basis'], row['level'])
            require(level in AREA_LEVELS_D2, 'Area table holds a level outside the D2 set: %s/%s' % level)
            key = (row['basis'], row['level'], row['area_id'], row['group'])
            require(key not in keys, 'Duplicate area row')
            keys.add(key)
            seen.add(level)
            if row['group'] == 'ovr':
                per_level[level] = per_level.get(level, 0) + 1
            require(row['suppressed'] in ('0', '0.0'), 'A suppressed row is published')
    withheld = {}
    withheld_keys = set()
    with open(suppressed_path, newline='', encoding='utf-8') as stream:
        reader = csv.DictReader(stream)
        for row in reader:
            level = (row['basis'], row['level'])
            require(level in AREA_LEVELS_D2, 'Withheld list holds a level outside the D2 set')
            key = (row['basis'], row['level'], row['area_id'], row['group'])
            require(key not in keys and key not in withheld_keys, 'An area is both published and withheld, or listed twice')
            withheld_keys.add(key)
            if row['group'] == 'ovr':
                withheld[level] = withheld.get(level, 0) + 1
    require(seen == AREA_LEVELS_D2, 'Area table does not cover the D2 level set')
    facts = {('%s/%s' % level): {'published': per_level[level], 'withheld': withheld.get(level, 0)}
             for level in sorted(AREA_LEVELS_D2)}
    receipt_path = rowfw / 'release' / 'P1_D2_suppression_counts.json'
    if receipt_path.exists():
        counts = load_json(receipt_path)['D2_counts']
        for name, value in facts.items():
            require(counts[name]['published'] == value['published'], 'Published count differs from the D2 receipt: ' + name)
            require(counts[name]['areas'] == value['published'] + value['withheld'],
                    'Area count differs from the D2 receipt: ' + name)
        log('area table matches the D2 receipt', json.dumps({k: v['published'] for k, v in facts.items()}))
    return {'area_table_sha256': W.sha256_file(csv_path), 'suppressed_ids_sha256': W.sha256_file(suppressed_path),
            'levels': facts, 'manifest': 'none: sha256 recorded; structure checked against the D2 receipt'}


# ---- parquet helpers ------------------------------------------------------------------------------------------------

def table(pq, path, columns):
    return pq.read_table(path, columns=columns)


def numeric(tbl, name):
    return tbl.column(name).combine_chunks().to_numpy(zero_copy_only=False)


def strings(tbl, name):
    column = tbl.column(name).combine_chunks()
    if hasattr(column, 'dictionary'):
        dictionary = np.array(column.dictionary.to_pylist(), dtype=object)
        indices = column.indices.to_numpy(zero_copy_only=False)
        return dictionary[indices].astype(str)
    return np.array(column.to_pylist(), dtype=object).astype(str)


# ---- crosswalk W1 ----------------------------------------------------------------------------------------------------

def contacts_crosswalk(contacts_path, n, log):
    uri = Path(contacts_path).resolve().as_uri() + '?mode=ro&immutable=1'
    connection = sqlite3.connect(uri, uri=True)
    try:
        meta = {k: json.loads(v) for k, v in connection.execute("SELECT key,value FROM metadata")}
        require(meta.get('schema') == 'contacts-v1', 'Unexpected contacts schema')
        require(meta['totals']['records'] == n, 'contacts record count differs from the score rows')
        ids = []
        expected_rowid = 1
        pattern = re.compile(r'p_[0-9a-f]{64}')
        for rowid, ident in connection.execute('SELECT rowid, id FROM contacts ORDER BY rowid'):
            require(rowid == expected_rowid, 'contacts rowid is not 1..N')
            require(pattern.fullmatch(ident) is not None, 'contacts id has an unexpected shape')
            ids.append(ident)
            expected_rowid += 1
    finally:
        connection.close()
    require(len(ids) == n and len(set(ids)) == n, 'contacts ids are not unique or do not number N')
    log('contacts crosswalk read', len(ids))
    return ids, meta


def street_metadata(street_path):
    connection = sqlite3.connect(Path(street_path).resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        return {k: json.loads(v) for k, v in connection.execute("SELECT key,value FROM metadata")}
    finally:
        connection.close()


def w1_census(source_csv, salt, ids, expected_sha, gb, log):
    """Recompute HMAC(salt, persist id) for every source row; match it to contacts.id at that rowid. Counts only."""
    n = len(ids)
    gb.stage('running', 'w1-hash', 'sha256 of the source CSV (%d bytes)' % os.path.getsize(source_csv))
    actual = W.sha256_file(source_csv)
    require(actual == expected_sha, 'Source CSV sha256 differs from contacts/street metadata')
    gb.stage('running', 'w1-census', 'HMAC census over %d source rows (counts only)' % n)
    matches = control = rows = 0
    started = time.time()
    with open(source_csv, encoding='cp1252', newline='') as stream:
        reader = csv.reader(stream)
        header = next(reader)
        column = header.index('sii_persist_id')
        for row in reader:
            rows += 1
            if rows > n:
                break
            message = json.dumps(['p', row[column].strip()], ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            digest = 'p_' + hmac.new(salt, message, hashlib.sha256).hexdigest()
            if digest == ids[rows - 1]:
                matches += 1
            if rows < n and digest == ids[rows]:
                control += 1
            if rows % 100000 == 0:
                gb.stage('running', 'w1-census', 'census %d of %d rows, %d matches' % (rows, n, matches))
    require(rows == n, 'Source CSV has %d records, expected %d' % (rows, n))
    require(matches == n, 'W1 census failed: %d of %d rows match' % (matches, n))
    require(control == 0, 'W1 control failed: %d shifted rows match' % control)
    log('w1 census', matches, 'control', control, '%.0fs' % (time.time() - started))
    return {'status': 'verified', 'rows_checked': rows, 'matches': matches, 'shifted_control_matches': control,
            'source_sha256': actual, 'seconds': round(time.time() - started, 1)}


# ---- main build ----------------------------------------------------------------------------------------------------

def build(args, gb, log):
    import pyarrow.parquet as pq
    rowfw = Path(args.rowfw)
    out = Path(args.out)
    started = time.time()

    # preflight
    gb.stage('running', 'preflight', 'disk, memory, inputs')
    free = shutil.disk_usage(out.parent if out.parent.exists() else out.anchor).free / 2 ** 30
    ram = available_ram_gb()
    require(free >= MIN_FREE_GB, 'Free disk %.1f GB is below the %.1f GB floor' % (free, MIN_FREE_GB))
    require(ram >= MIN_RAM_GB or args.low_memory, 'Available RAM %.1f GB is below the %.1f GB floor' % (ram, MIN_RAM_GB))
    log('preflight', 'free_gb=%.1f' % free, 'ram_gb=%.1f' % ram)

    gb.stage('running', 'verify-inputs', 'manifests, config, cache and area table')
    manifests, shas = verify_manifests(rowfw, log)
    config, config_sha = verify_config(rowfw, manifests['row_scores'], log)
    cache_path = Path(args.cache)
    cache_sha = W.sha256_file(cache_path)
    require(cache_sha == manifests['row_scores']['inputs']['cache_sha256'], 'Cache sha256 differs from the manifests')
    area = check_area_table(rowfw, log)
    gb.stage('completed', 'verify-inputs', 'inputs verified: 3 manifests, config, cache, area table (%d levels)' %
             len(area['levels']))

    # ---- read the files -----------------------------------------------------------------------------------------
    gb.stage('running', 'load-parquet', 'reading the row, profile and household files')
    n = manifests['row_scores']['rows']
    row_columns = ['model_source_row', 'lean_band_undetermined', 'lean_p_same_band', 'ovr_rank', 'ovr_tie_n',
                   'ovr_p_top10', 'cua_p_top10', 'part_p_top10', 'soi_p_top10', 'cua_rank', 'cua_tie_n', 'part_rank',
                   'part_tie_n', 'soi_rank', 'soi_tie_n'] + list(S.FLAG_BITS)
    summary = list(manifests['profiles']['summary_columns'])
    rows_tbl = table(pq, rowfw / ('row_scores_%s.parquet' % VERSION), sorted(set(row_columns + summary)))
    profiles_path = rowfw / ('profiles_%s.parquet' % VERSION)
    # the 160 field columns are read one field at a time below, which keeps the peak memory well under 3 GB
    prof_tbl = table(pq, profiles_path, ['model_source_row', 'household_code', 'household_size', 'svi_party_registration__value']
                     + summary)
    hh_tbl = pq.read_table(rowfw / ('household_scores_%s.parquet' % VERSION))
    cache_tbl = table(pq, cache_path, ['model_source_row', 'household_code', 'household_size', 'sii_age',
                                      'svi_party_registration', 'svi_party_registration_rollup',
                                      'registration_block15', 'svi_registration_precinct_code', 'registration_tract11',
                                      'svi_registration_zip', 'sii_tract11', 'sii_zip'])
    log('parquet loaded', n)

    expected_keys = np.arange(1, n + 1)
    for name, tbl in (('row_scores', rows_tbl), ('profiles', prof_tbl), ('cache', cache_tbl)):
        key = numeric(tbl, 'model_source_row')
        require(len(key) == n and np.array_equal(key, expected_keys), name + ': keys are not 1..N ascending')

    # B41: the profile summary columns are bit-for-bit copies of the row file
    for column in summary:
        a, b = rows_tbl.column(column).combine_chunks(), prof_tbl.column(column).combine_chunks()
        if hasattr(a, 'dictionary'):
            same = np.array_equal(strings(rows_tbl, column), strings(prof_tbl, column))
        else:
            same = np.array_equal(a.to_numpy(zero_copy_only=False), b.to_numpy(zero_copy_only=False), equal_nan=True)
        require(same, 'Profile summary column differs from the row file: ' + column)
    for column in ('household_code', 'household_size'):
        require(np.array_equal(numeric(prof_tbl, column), numeric(cache_tbl, column)),
                'Profile and cache disagree on ' + column)
    log('B41 summary columns identical', len(summary))

    hh_code = numeric(prof_tbl, 'household_code').astype(np.int64)
    hh_size = numeric(prof_tbl, 'household_size').astype(np.int64)
    party = strings(prof_tbl, 'svi_party_registration__value')
    require(np.array_equal(party, strings(cache_tbl, 'svi_party_registration')), 'Profile and cache disagree on party')
    reg_group_text = np.array([FL.reg_group_of(p) for p in party])
    counts = {g: int((reg_group_text == g).sum()) for g in FL.REG_GROUPS}
    frozen = config['standing']['within_registration']['group_counts_at_freeze']
    require(counts == {'D': frozen['D'], 'R': frozen['R'], 'U': frozen['U'], 'O': frozen['other']},
            'Registration group counts differ from the frozen counts: %r' % counts)
    rollup = strings(cache_tbl, 'svi_party_registration_rollup')
    check_rollup(D.get(args.district), rollup, reg_group_text)

    lean = numeric(rows_tbl, 'lean').astype(np.float64)
    lean_band = numeric(rows_tbl, 'lean_band').astype(np.int64)
    expected_band = np.array([FL.lean_band_of(v) for v in lean.astype(np.float32).astype(np.float64)])
    # the stored point band was computed on the float64 lean; allow only values within 1e-4 of a cut
    mismatch = np.flatnonzero(expected_band != lean_band)
    if len(mismatch):
        near = np.abs(np.abs(lean[mismatch]) - np.array([25.0, 75.0])[:, None]).min(axis=0) < 1e-4
        require(bool(near.all()), 'Lean band rule fails on %d rows' % int((~near).sum()))
    log('lean band rule', 'rows differing at a cut:', len(mismatch))

    # ---- crosswalk -----------------------------------------------------------------------------------------------
    gb.stage('running', 'crosswalk', 'contacts crosswalk and W1 census')
    ids, contacts_meta = contacts_crosswalk(args.contacts, n, log)
    street_meta = street_metadata(args.street)
    source_sha = contacts_meta['source_sha256']
    require(street_meta.get('source_sha256') == source_sha, 'contacts and street use different source files')
    if args.w1 == 'verify':
        require(args.source_csv and args.salt_file, 'W1 verification needs --source-csv and --salt-file')
        salt = Path(args.salt_file).read_bytes()
        require(len(salt) == 32, 'Salt file has an unexpected size')
        w1 = w1_census(args.source_csv, salt, ids, source_sha, gb, log)
        del salt
    else:
        require(args.w1 == 'fixture', 'W1 must be verify or fixture')
        w1 = {'status': 'fixture', 'note': 'synthetic persist ids; the HMAC census is not applicable'}
    id_to_row = {ident: i for i, ident in enumerate(ids)}
    person_id = np.array(ids, dtype='U66')

    # ---- street households (registration basis) ----------------------------------------------------------------
    gb.stage('running', 'street', 'street households joined to person rows')
    connection = sqlite3.connect(Path(args.street).resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    pairs = []
    try:
        query = ("SELECT p.household_id, p.id FROM people p JOIN households h ON h.id=p.household_id "
                 "WHERE h.basis='registration'")
        for h_id, p_id in connection.execute(query):
            row = id_to_row.get(p_id)
            require(row is not None, 'A street person id is not in the crosswalk')
            pairs.append((h_id, row))
        expected = street_meta['bases']['registration']
        n_households = connection.execute("SELECT COUNT(*) FROM households WHERE basis='registration'").fetchone()[0]
    finally:
        connection.close()
    require(len(pairs) == expected['people'] and n_households == expected['households'],
            'Street counts differ from the street metadata')
    log('street people mapped', len(pairs), 'households', n_households)

    # ---- assemble the arrays -------------------------------------------------------------------------------------
    gb.stage('running', 'evidence', 'field evidence (40 fields) and person attributes')
    data = {'person_id': person_id, 'hh': hh_code, 'hh_size': hh_size, 'party': party,
            'age': numeric(cache_tbl, 'sii_age').astype(np.int64)}
    for target, source in (('reg_block', 'registration_block15'), ('reg_precinct', 'svi_registration_precinct_code'),
                           ('reg_tract', 'registration_tract11'), ('reg_zip', 'svi_registration_zip'),
                           ('res_tract', 'sii_tract11'), ('res_zip', 'sii_zip')):
        data[target] = strings(cache_tbl, source)
    for g in S.INDEX_IDS:
        for suffix in ('score', 'pct', 'pct_lo90', 'pct_hi90', 'measured_pct', 'pct_within_reg'):
            data['%s_%s' % (g, suffix)] = numeric(rows_tbl, '%s_%s' % (g, suffix))
        data[g + '_grade'] = strings(rows_tbl, g + '_grade')
        data[g + '_rank'] = numeric(rows_tbl, g + '_rank')
        data[g + '_tie_n'] = numeric(rows_tbl, g + '_tie_n')
        data[g + '_p_top10'] = numeric(rows_tbl, g + '_p_top10')
    for name in ('lean', 'lean_lo90', 'lean_hi90', 'lean_measured_pct', 'lean_band', 'lean_p_same_band'):
        data[name] = numeric(rows_tbl, name)
    data['lean_grade'] = strings(rows_tbl, 'lean_grade')
    data['lean_band_undetermined'] = numeric(rows_tbl, 'lean_band_undetermined').astype(bool)
    for flag in S.FLAG_BITS:
        data[flag] = numeric(rows_tbl, flag).astype(bool)

    ev_value = np.zeros((n, 40), dtype=np.uint16)
    ev_status = np.zeros((n, 40), dtype=np.uint8)
    ev_p = np.full((n, 40), np.nan, dtype=np.float32)
    ev_exposure = np.full((n, 40), np.nan, dtype=np.float32)
    value_dict = []
    status_index = {s: i for i, s in enumerate(FL.STATUSES)}
    for fid, field in enumerate(FL.FIELD_ORDER):
        ftbl = table(pq, profiles_path, [field + '__' + part for part in ('value', 'status', 'p', 'exposure')])
        column = ftbl.column(field + '__value').combine_chunks()
        if hasattr(column, 'dictionary'):
            literal = strings(ftbl, field + '__value')
            unique = sorted(set(literal.tolist()))
            ev_value[:, fid] = np.searchsorted(np.array(unique), literal)
            value_dict.append(unique)
        else:
            raw = column.to_numpy(zero_copy_only=False).astype(np.int64)
            unique = np.unique(raw)
            ev_value[:, fid] = np.searchsorted(unique, raw)
            value_dict.append([str(int(v)) for v in unique.tolist()])
        status_text = strings(ftbl, field + '__status')
        kinds, inverse = np.unique(status_text, return_inverse=True)
        unknown = set(kinds.tolist()) - set(status_index)
        require(not unknown, 'Unknown status in ' + field)
        ev_status[:, fid] = np.array([status_index[s] for s in kinds.tolist()], dtype=np.uint8)[inverse]
        ev_p[:, fid] = numeric(ftbl, field + '__p').astype(np.float32)
        ev_exposure[:, fid] = numeric(ftbl, field + '__exposure').astype(np.float32)
        require(len(value_dict[-1]) < 65535, 'Too many distinct values in ' + field)
        del ftbl, column
    data.update({'ev_value': ev_value, 'ev_status': ev_status, 'ev_p': ev_p, 'ev_exposure': ev_exposure,
                 'value_dict': value_dict})
    nulls = int(np.isnan(ev_exposure).sum())
    require(nulls == int(data['flag_digital_code50'].sum()) and
            bool(np.isnan(ev_exposure[:, FL.FIELD_ORDER.index('sai_digital_reachability_score')])[
                data['flag_digital_code50']].all()),
            'Null exposures are not exactly the digital code-50 rows')

    household = {name: numeric(hh_tbl, name) for name in hh_tbl.column_names if name != 'hh_worst_grade'}
    household['hh'] = household.pop('household_code').astype(np.int64)
    household['hh_worst_grade'] = strings(hh_tbl, 'hh_worst_grade')
    require(len(household['hh']) == manifests['household_scores']['rows'], 'Household row count differs')
    require(int(household['hh_n'].sum()) == n, 'Household sizes do not sum to N')
    require(set(np.unique(hh_code).tolist()) == set(household['hh'].tolist()), 'Household codes differ between files')
    data['household'] = household

    located = np.zeros(n, dtype=bool)
    for _, row in pairs:
        located[row] = True
    data['has_location'] = located
    data['street_household'] = W.street_household_rows(pairs, data)
    require(len(data['street_household']) == n_households, 'Street household count differs after grouping')
    log('street households', len(data['street_household']), 'people with a location', int(located.sum()))

    # ---- checks against the build's own summaries ------------------------------------------------------------------
    checks = check_summaries(rowfw, data, log)

    # ---- write ---------------------------------------------------------------------------------------------------
    gb.stage('running', 'write', 'writing scores.sqlite3 (temp file, integrity check, replace)')
    demo_key_path = out.parent / '.demo-key'
    if demo_key_path.exists():
        demo_key = demo_key_path.read_bytes()
    else:
        demo_key = os.urandom(32)
        demo_key_path.write_bytes(demo_key)
    require(len(demo_key) == 32, 'Demo key has an unexpected size')
    code_hash = hashlib.sha256()
    for module in ('scores_schema', 'scores_writer', 'field_labels', 'demo_names', 'build_scores_v3'):
        code_hash.update((V3 / 'tools' / (module + '.py')).read_bytes())
    meta = {
        'rowfw_version': VERSION,
        'inputs': {
            'parquet_sha256': shas, 'config_sha256': config_sha, 'plan_sha256': manifests['row_scores']['plan_sha256'],
            'criteria_sha256': manifests['row_scores']['criteria_sha256'], 'cache_sha256': cache_sha,
            'export_sha256': manifests['row_scores']['inputs']['export_sha256'],
            'run_ids': {stem: manifests[stem]['run_id'] for stem in STEMS},
            'rows': {stem: manifests[stem]['rows'] for stem in STEMS},
        },
        'w1': w1,
        'field_catalog': [{'fid': f['fid'], 'name': f['name'], 'group': f['group'], 'label': f['label'],
                           'kind': f['kind'], 'points': f['points'], 'points_code50': f['points_code50']}
                          for f in FL.FIELDS],
        'contacts_source_sha256': source_sha,
        'area_table_sha256': area['area_table_sha256'], 'suppressed_ids_sha256': area['suppressed_ids_sha256'],
        'area_table': area, 'summary_checks': checks, 'build_code_sha256': code_hash.hexdigest(),
    }
    receipt = W.write_scores_db(out, data, meta, demo_key, log=log, replace=False)
    try:
        verify_written(receipt['temporary'], n, receipt, rowfw, log)
    except Exception:
        os.remove(receipt['temporary'])      # fail closed: the previous store, if any, is untouched
        raise
    os.replace(receipt['temporary'], out)
    receipt['temporary'] = None
    receipt.update({'w1': w1, 'area_table': area, 'summary_checks': checks, 'free_gb_before': round(free, 1),
                    'total_seconds': round(time.time() - started, 1)})
    return receipt


def check_summaries(rowfw, data, log):
    """Compare the loaded values with the build's own summary CSVs (counts only)."""
    done = []
    summaries = rowfw / 'summaries'

    def rows_of(name):
        path = summaries / name
        if not path.exists():
            return None
        with open(path, newline='', encoding='utf-8') as stream:
            return list(csv.DictReader(stream))

    grades = rows_of('grades.csv')
    if grades:
        for row in grades:
            if row['kind'] != 'final':
                continue
            letters = data[row['index'] + '_grade']
            actual = int((letters == row['grade']).sum())
            require(actual == int(row['rows']), 'Grade count differs: %s %s' % (row['index'], row['grade']))
        done.append('grades.csv')
    bands = rows_of('lean_bands.csv')
    if bands:
        for row in bands:
            actual = int((np.asarray(data['lean_band']) == int(row['lean_band'])).sum())
            require(actual == int(row['rows']), 'Lean band count differs: ' + row['lean_band'])
        done.append('lean_bands.csv')
    flags = rows_of('flags.csv')
    if flags:
        for row in flags:
            if row['flag'] in S.FLAG_BITS:
                require(int(np.asarray(data[row['flag']]).sum()) == int(row['rows']), 'Flag count differs: ' + row['flag'])
            elif row['flag'] == 'lean_band_undetermined_rows':
                require(int(np.asarray(data['lean_band_undetermined']).sum()) == int(row['rows']), 'Undetermined differs')
        done.append('flags.csv')
    balanced = rows_of('balanced_by_registration_topK.csv')
    if balanced:
        done.append('balanced_by_registration_topK.csv (checked after the write)')
    log('summary checks', done)
    return done


def verify_written(out, n, receipt, rowfw, log):
    """Re-open the finished store read-only and check it against the frozen balanced allocations and counts."""
    path = rowfw / 'summaries' / 'balanced_by_registration_topK.csv'
    if path.exists():
        with open(path, newline='', encoding='utf-8') as stream:
            for row in csv.DictReader(stream):
                group = 'O' if row['registration_group'] == 'other' else row['registration_group']
                computed = receipt['balanced']['k'][row['K']]
                require(computed['K_g'][group] == int(row['K_g']) and computed['K_actual'][group] == int(row['K_actual']),
                        'Balanced allocation differs from the build summary: K=%s %s' % (row['K'], group))
        log('balanced allocations match the build summary')
    connection = sqlite3.connect(Path(out).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        got = {table_name: connection.execute('SELECT COUNT(*) FROM %s' % table_name).fetchone()[0]
               for table_name in ('person', 'evidence', 'household', 'street_household', 'demo_name')}
        require(got['person'] == n and got['evidence'] == n and got['demo_name'] == n, 'Row counts differ after write')
        pos_ok = connection.execute('SELECT MIN(pos), MAX(pos), COUNT(DISTINCT pos) FROM person').fetchone()
        require(tuple(pos_ok) == (1, n, n), 'pos is not 1..N')
        forbidden = [c for c in (r[1] for r in connection.execute('PRAGMA table_info(person)'))
                     if re.search(r'name|address|email|phone|lon|lat|source_row|model_source', c)]
        require(not forbidden, 'Forbidden columns in person: %r' % forbidden)
    finally:
        connection.close()
    log('store verified', got)
    receipt['counts'] = got


def main(argv=None):
    parser = argparse.ArgumentParser(description='Build V3/.local/scores.sqlite3 from the P1 files (fails closed).')
    parser.add_argument('--real', action='store_true', help='use the real P1 files, cache, contacts, street and source CSV')
    parser.add_argument('--rowfw', default=str(RFB))
    parser.add_argument('--cache', default=str(RF / 'cache' / 'inputs.parquet'))
    parser.add_argument('--district', choices=list(D.DISTRICTS), default=D.DEFAULT,
                        help='fills the defaults below from tools/districts.py (co-05 gives the old literals)')
    parser.add_argument('--contacts')
    parser.add_argument('--street')
    parser.add_argument('--source-csv')
    parser.add_argument('--salt-file')
    parser.add_argument('--w1', choices=('verify', 'fixture'), default='verify')
    parser.add_argument('--out')
    parser.add_argument('--receipt', default=str(V3 / '_build' / 'scores_build_receipt.json'))
    parser.add_argument('--low-memory', action='store_true', help='skip the available-RAM floor')
    parser.add_argument('--run-id', default=None)
    args = parser.parse_args(argv)
    district = D.get(args.district)
    home = D.data_dir(district)
    args.contacts = args.contacts or str(home / 'contacts.sqlite3')
    args.street = args.street or str(home / 'street.sqlite3')
    args.source_csv = args.source_csv or str(district['source_csv'])
    args.salt_file = args.salt_file or str(D.street_salt(district))
    args.out = args.out or str(home / 'scores.sqlite3')
    run_id = args.run_id or new_run_id('cu-geo-v3-scores')
    gb = GlassBox(run_id, 'cu-geography-v3/tools/build_scores_v3.py')

    def log(*parts):
        print(time.strftime('%H:%M:%S'), *parts, flush=True)

    gb.run('running', 'start', 'scores.sqlite3 build: P1 parquet + area table (counts only, no names)')
    try:
        receipt = build(args, gb, log)
    except BuildError as error:
        gb.failed('build', 'scores build stopped: ' + str(error))
        print('STOPPED:', error, file=sys.stderr)
        return 2
    except Exception as error:      # keep the previous store; report type and message only
        gb.failed('build', 'scores build crashed: %s: %s' % (type(error).__name__, error))
        raise
    Path(args.receipt).parent.mkdir(parents=True, exist_ok=True)
    Path(args.receipt).write_text(json.dumps(receipt, indent=1, sort_keys=True, default=str), encoding='utf-8')
    gb.run('completed', 'complete', 'scores.sqlite3 written: %d people, %d households, %d bytes' %
           (receipt['rows'], receipt['households'], receipt['bytes']), bytes_=receipt['bytes'])
    print(json.dumps({k: receipt[k] for k in ('rows', 'households', 'bytes', 'total_seconds')}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
