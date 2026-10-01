"""Write `scores.sqlite3` from aligned numpy arrays (numpy + stdlib only; no pyarrow, no pandas).

The real loader (tools/build_scores_v3.py, system Python with pyarrow) and the synthetic fixture
(tools/make_scores_fixture.py) both end here, so a test on the fixture exercises the same writer as the real build.

Input arrays are aligned to the source row order (index r is one source row). The writer then orders everyone by the
overall competition rank (ties by the opaque person id, which carries no information) and stores the position `pos`.
"""
import datetime
import hashlib
import json
import os
import sqlite3
import time
import zlib

import numpy as np

from tools import scores_schema as S
from tools import field_labels as FL
from tools.demo_names import demo_name

REG_GROUP_INDEX = {code: index for index, code in enumerate(FL.REG_GROUPS)}
K_VALUES = (100, 4913, 49131)
CHUNK = 25000


def hamilton(total, counts):
    """Largest-remainder apportionment of `total` by `counts`; remainder ties go to the earlier group."""
    counts = list(counts)
    population = sum(counts)
    quotas = [total * count / population for count in counts]
    seats = [int(q) for q in quotas]
    order = sorted(range(len(counts)), key=lambda i: (-(quotas[i] - seats[i]), i))
    for i in order[:total - sum(seats)]:
        seats[i] += 1
    return seats


def competition_rank_within(group_codes, rank):
    """1 + the number of rows of the same group with a strictly smaller global competition rank."""
    result = np.zeros(len(rank), dtype=np.int64)
    for code in np.unique(group_codes):
        mask = group_codes == code
        ordered = np.sort(rank[mask])
        result[mask] = np.searchsorted(ordered, rank[mask], side='left') + 1
    return result


def factorize(values):
    """Strings (or None) -> (int32 codes with -1 for missing, ids list)."""
    text = np.array([('' if v is None else str(v)) for v in values], dtype=object).astype(str)
    unique, inverse = np.unique(text, return_inverse=True)
    if len(unique) and unique[0] == '':
        return (inverse - 1).astype(np.int32), [str(u) for u in unique[1:]]
    return inverse.astype(np.int32), [str(u) for u in unique]


def contributions(exposure, code50, group_of_field=None):
    """Per-field contribution in score points: 100 * weight * exposure (weights from tools.field_labels).

    exposure: (N, 40) float32/64 with NaN where unscored; code50: (N,) bool. Returns (N, 40) float64 with NaN -> 0.
    """
    points = np.array([row['points'] for row in FL.FIELDS], dtype=np.float64)
    points50 = np.array([row['points_code50'] for row in FL.FIELDS], dtype=np.float64)
    expo = np.nan_to_num(np.asarray(exposure, dtype=np.float64), nan=0.0)
    return expo * np.where(np.asarray(code50)[:, None], points50[None, :], points[None, :])


def group_slices():
    return {group: [i for i, row in enumerate(FL.FIELDS) if row['group'] == group] for group in FL.GROUPS}


def check_contribution_sums(exposure, code50, scores, tolerance=1e-3):
    """Sum of contributions equals the index score (lean: the lean) for every row. Returns the maximum abs difference."""
    contrib = contributions(exposure, code50)
    worst = {}
    for group, columns in group_slices().items():
        total = contrib[:, columns].sum(axis=1)
        reference = np.asarray(scores[group], dtype=np.float64)
        worst[group] = float(np.abs(total - reference).max())
    bad = {group: value for group, value in worst.items() if value > tolerance}
    if bad:
        raise ValueError('Field contributions do not sum to the index score: %r' % bad)
    return worst


def _floats(array):
    values = np.asarray(array, dtype=np.float64)
    mask = np.isnan(values)
    listed = values.tolist()
    if mask.any():
        for i in np.flatnonzero(mask).tolist():
            listed[i] = None
    return listed


def _ints(array):
    return np.asarray(array).astype(np.int64).tolist()


def _texts(array):
    return [None if v is None or v == '' else str(v) for v in np.asarray(array, dtype=object).tolist()]


def pack_flags(data):
    flags = np.zeros(len(data['person_id']), dtype=np.int64)
    for bit, name in enumerate(S.FLAG_BITS):
        flags |= np.asarray(data[name]).astype(bool).astype(np.int64) << bit
    return flags


def write_scores_db(path, data, meta, demo_key, log=None, replace=True):
    """Write the store to `path` (temp file, integrity_check, then os.replace). Returns a receipt of counts.

    With replace=False the finished, integrity-checked file is left at receipt['temporary'] and `path` is untouched: the
    caller verifies it and then calls os.replace itself, so a failed check never disturbs the previous store.
    """
    log = log or (lambda *a, **k: None)
    path = os.fspath(path)
    n = len(data['person_id'])
    started = time.time()

    # ---- ordering: overall competition rank, ties by the opaque person id ---------------------------------
    by_id = np.argsort(np.asarray(data['person_id']).astype(str), kind='stable')
    order = by_id[np.argsort(np.asarray(data['ovr_rank'])[by_id], kind='stable')]
    pos = np.arange(1, n + 1)

    def take(name):
        return np.asarray(data[name])[order]

    reg_codes = np.array([REG_GROUP_INDEX[FL.reg_group_of(str(p))] for p in np.asarray(data['party'])], dtype=np.int64)
    reg_group = reg_codes[order]
    ovr_rank = take('ovr_rank')
    reg_rank = competition_rank_within(reg_group, ovr_rank)
    flags = pack_flags(data)[order]
    code50 = take('flag_digital_code50').astype(bool)
    grade_text = {g: np.asarray(data[g + '_grade']).astype(str) for g in S.INDEX_IDS + ('lean',)}
    for g, letters in grade_text.items():
        if not set(np.unique(letters).tolist()) <= set(S.GRADE_LETTERS):
            raise ValueError('Unexpected grade letters in ' + g)

    # ---- evidence checks before anything is written -----------------------------------------------------
    scores_in_order = {'cua': take('cua_score'), 'part': take('part_score'), 'soi': take('soi_score'),
                       'lean': take('lean')}
    sums = check_contribution_sums(np.asarray(data['ev_exposure'])[order], code50, scores_in_order)
    log('contribution sums checked', sums)

    # ---- demo names (display-only pseudonyms; never derived from contacts) -------------------------------
    ids_in_order = np.asarray(data['person_id']).astype(str)[order]
    hh_in_order = take('hh')
    demo_names = [demo_name(demo_key, pid, int(h)) for pid, h in zip(ids_in_order.tolist(), hh_in_order.tolist())]

    # ---- list columns ---------------------------------------------------------------------------------
    list_columns = {
        'reg_group': reg_group.astype(np.uint8), 'reg_rank': reg_rank.astype(np.int32),
        'grade': np.array([S.GRADE_LETTERS.index(g) for g in grade_text['ovr'][order]], dtype=np.uint8),
        'lean_band': take('lean_band').astype(np.int8), 'lean_undet': take('lean_band_undetermined').astype(np.uint8),
    }
    list_dicts = {}
    for column in S.AREA_COLUMNS:
        codes, ids = factorize(take(column))
        list_columns['a_' + column] = codes
        list_dicts['a_' + column] = ids
    positions = np.arange(n)
    for name in ('cua', 'part', 'soi'):
        list_columns['ord_' + name] = np.lexsort((positions, take(name + '_rank'))).astype(np.int32)
    list_columns['ord_within'] = np.lexsort((positions, -take('ovr_pct_within_reg').astype(np.float64))).astype(np.int32)

    # ---- metadata derived from the data ------------------------------------------------------------------
    group_counts = {code: int((reg_group == index).sum()) for code, index in REG_GROUP_INDEX.items()}
    balanced = {'groups': list(FL.REG_GROUPS), 'counts': group_counts, 'k': {}}
    for k in K_VALUES:
        seats = hamilton(k, [group_counts[g] for g in FL.REG_GROUPS])
        actual = [int(((reg_group == REG_GROUP_INDEX[g]) & (reg_rank <= seat)).sum())
                  for g, seat in zip(FL.REG_GROUPS, seats)]
        balanced['k'][str(k)] = {'K_g': dict(zip(FL.REG_GROUPS, seats)), 'K_actual': dict(zip(FL.REG_GROUPS, actual))}
    district = {
        'n': n, 'households': int(len(data['household']['hh'])),
        'top10_rate': {g: float(np.asarray(data[g + '_p_top10'], dtype=np.float64).sum() / n) for g in S.INDEX_IDS},
        'measured_mean': {g: float(np.asarray(data[g + '_measured_pct'], dtype=np.float64).mean())
                          for g in S.INDEX_IDS + ('lean',)},
        'score_mean': {g: float(np.asarray(data[g + '_score'], dtype=np.float64).mean()) for g in S.INDEX_IDS},
        'lean_mean': float(np.asarray(data['lean'], dtype=np.float64).mean()),
        'lean_band_counts': {str(b): int((take('lean_band') == b).sum()) for b in (-2, -1, 0, 1, 2)},
        'grade_counts': {g: {letter: int((grade_text[g] == letter).sum()) for letter in S.GRADE_LETTERS}
                         for g in S.INDEX_IDS + ('lean',)},
    }

    directory = os.path.dirname(path) or '.'
    os.makedirs(directory, exist_ok=True)
    temporary = path + '.building'
    for suffix in ('', '-journal', '-wal', '-shm'):
        if os.path.exists(temporary + suffix):
            os.remove(temporary + suffix)
    connection = sqlite3.connect(temporary)
    try:
        connection.execute('PRAGMA journal_mode=OFF')
        connection.execute('PRAGMA synchronous=OFF')
        connection.execute('PRAGMA cache_size=-262144')
        connection.execute('PRAGMA temp_store=MEMORY')
        for statement in S.TABLES:
            connection.execute(statement)

        # person
        names = S.PERSON_COLUMN_NAMES
        placeholders = ','.join('?' * len(names))
        insert = 'INSERT INTO person(%s) VALUES (%s)' % (','.join(names), placeholders)
        float_columns = {name for name, kind in S.PERSON_COLUMNS if kind == 'REAL'}
        text_columns = {'person_id', 'party', 'reg_block', 'reg_precinct', 'reg_tract', 'reg_zip', 'res_tract',
                        'res_zip', 'ovr_grade', 'cua_grade', 'part_grade', 'soi_grade', 'lean_grade'}
        source = {
            'pos': pos, 'person_id': ids_in_order, 'hh': hh_in_order, 'hh_size': take('hh_size'), 'age': take('age'),
            'reg_group': reg_group, 'party': take('party'), 'flags': flags,
            'has_location': take('has_location').astype(np.int64), 'reg_rank': reg_rank,
            'lean_undet': take('lean_band_undetermined').astype(np.int64),
        }
        for column in S.AREA_COLUMNS:
            source[column] = take(column)
        for g in S.INDEX_IDS:
            for suffix in ('rank', 'tie_n', 'score', 'pct', 'pct_lo90', 'pct_hi90', 'measured_pct', 'pct_within_reg',
                           'p_top10'):
                source[g + '_' + suffix] = take(g + '_' + suffix)
            source[g + '_grade'] = grade_text[g][order]
        for name in ('lean', 'lean_lo90', 'lean_hi90', 'lean_measured_pct', 'lean_band', 'lean_p_same_band'):
            source[name] = take(name)
        source['lean_grade'] = grade_text['lean'][order]
        for start in range(0, n, CHUNK):
            stop = min(n, start + CHUNK)
            columns = []
            for name in names:
                part = source[name][start:stop]
                if name in float_columns:
                    columns.append(_floats(part))
                elif name in text_columns:
                    columns.append(_texts(part))
                elif name == 'age':
                    ages = np.asarray(part).astype(np.int64).tolist()
                    columns.append([a if a > 0 else None for a in ages])
                else:
                    columns.append(_ints(part))
            connection.executemany(insert, zip(*columns))
        log('person rows written', n, '%.1fs' % (time.time() - started))

        # evidence
        values = np.ascontiguousarray(np.asarray(data['ev_value'], dtype='<u2')[order])
        status = np.ascontiguousarray(np.asarray(data['ev_status'], dtype='u1')[order])
        probability = np.ascontiguousarray(np.asarray(data['ev_p'], dtype='<f4')[order])
        exposure = np.ascontiguousarray(np.asarray(data['ev_exposure'], dtype='<f4')[order])
        for start in range(0, n, CHUNK):
            stop = min(n, start + CHUNK)
            rows = []
            for i in range(start, stop):
                raw = values[i].tobytes() + status[i].tobytes() + probability[i].tobytes() + exposure[i].tobytes()
                rows.append((i + 1, zlib.compress(raw, 6)))
            connection.executemany('INSERT INTO evidence(pos,blob) VALUES (?,?)', rows)
        log('evidence written', '%.1fs' % (time.time() - started))

        # value dictionary
        for fid, literals in enumerate(data['value_dict']):
            connection.executemany('INSERT INTO value_dict(fid,code,literal) VALUES (?,?,?)',
                                   [(fid, code, str(text)) for code, text in enumerate(literals)])

        # households
        household = data['household']
        hnames = S.HOUSEHOLD_COLUMN_NAMES
        hfloat = {name for name, kind in S.HOUSEHOLD_COLUMNS if kind == 'REAL'}
        hcolumns = []
        for name in hnames:
            array = household[name]
            hcolumns.append(_floats(array) if name in hfloat else _texts(array) if name == 'hh_worst_grade'
                            else _ints(array))
        connection.executemany('INSERT INTO household(%s) VALUES (%s)' % (','.join(hnames), ','.join('?' * len(hnames))),
                               zip(*hcolumns))

        # street households
        street = data['street_household']
        if len(street):
            connection.executemany('INSERT INTO street_household(h_id,hh,n_people,ovr_mean_pct,bin,reg_block,'
                                   'measured_mean) VALUES (?,?,?,?,?,?,?)', street)

        # list columns, demo names
        for name, array in list_columns.items():
            contiguous = np.ascontiguousarray(array)
            connection.execute('INSERT INTO list_column(name,dtype,n,data) VALUES (?,?,?,?)',
                               (name, contiguous.dtype.str, int(len(contiguous)), contiguous.tobytes()))
        for name, ids in list_dicts.items():
            connection.execute('INSERT INTO list_dict(name,ids) VALUES (?,?)', (name, json.dumps(ids)))
        connection.executemany('INSERT INTO demo_name(pos,name) VALUES (?,?)',
                               ((i + 1, name) for i, name in enumerate(demo_names)))

        for statement in S.INDEXES + S.FTS:
            connection.execute(statement)
        connection.execute("INSERT INTO demo_search(demo_search) VALUES ('rebuild')")
        log('indexes built', '%.1fs' % (time.time() - started))

        # metadata
        metadata = dict(meta)
        metadata.update({
            'schema': S.SCHEMA, 'n_people': n, 'n_households': district['households'],
            'reg_group_counts': group_counts, 'district': district, 'balanced': balanced,
            'demo_key': demo_key.hex(), 'blob_version': S.BLOB_VERSION,
            'status_codes': list(FL.STATUSES),
            'built_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
            'list_columns': {name: {'dtype': np.ascontiguousarray(a).dtype.str, 'n': int(len(a))}
                             for name, a in list_columns.items()},
            'contribution_sum_max_abs_diff': sums,
        })
        missing = [key for key in S.REQUIRED_METADATA if key not in metadata]
        if missing:
            raise ValueError('Missing metadata keys: ' + ', '.join(missing))
        connection.executemany('INSERT INTO metadata(key,value) VALUES (?,?)',
                               [(key, json.dumps(value, separators=(',', ':'), sort_keys=True))
                                for key, value in metadata.items()])
        connection.commit()
        check = connection.execute('PRAGMA integrity_check').fetchone()[0]
        if check != 'ok':
            raise ValueError('Integrity check failed: ' + check)
        connection.execute('ANALYZE')
        connection.commit()
    finally:
        connection.close()
    for suffix in ('-journal',):
        if os.path.exists(temporary + suffix):
            os.remove(temporary + suffix)
    if replace:
        os.replace(temporary, path)
    size = os.path.getsize(path if replace else temporary)
    log('store written', path, size, '%.1fs' % (time.time() - started))
    return {'rows': n, 'households': district['households'], 'bytes': size, 'balanced': balanced,
            'reg_group_counts': group_counts, 'seconds': round(time.time() - started, 1),
            'contribution_sum_max_abs_diff': sums, 'temporary': None if replace else temporary}


STREET_BREAKS = (20.0, 40.0, 60.0, 80.0)


def street_household_rows(pairs, data):
    """Per registration street household: (h_id, hh, n_people, ovr_mean_pct, bin, reg_block, measured_mean).

    pairs is an iterable of (h_id, source_row_index). A street household must lie inside one household code; the
    block is the one holding most of its members (ties by id); bin is 1 + the number of breaks at or below the mean.
    """
    groups = {}
    for h_id, row in pairs:
        groups.setdefault(h_id, []).append(int(row))
    ovr = np.asarray(data['ovr_pct'], dtype=np.float64)
    measured = np.asarray(data['ovr_measured_pct'], dtype=np.float64)
    hh = np.asarray(data['hh'])
    blocks = np.asarray(data['reg_block'])
    out = []
    for h_id, rows in groups.items():
        codes = set(hh[rows].tolist())
        if len(codes) != 1:
            raise ValueError('A street household spans more than one household code')
        counts = {}
        for r in rows:
            counts[str(blocks[r])] = counts.get(str(blocks[r]), 0) + 1
        mean = float(ovr[rows].mean())
        out.append((h_id, int(codes.pop()), len(rows), mean, 1 + sum(1 for b in STREET_BREAKS if b <= mean),
                    sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0], float(measured[rows].mean())))
    return out


def sha256_file(path, chunk=1 << 22):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(chunk), b''):
            digest.update(block)
    return digest.hexdigest()
