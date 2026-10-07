"""P3-03a observed masks, P3-04 holdout freeze, P3-06 development partitions and the leakage asserts.

The holdout is every observed split-2 row of a field (so it contains the rows the legacy cohort draw would call test and
stress). It is frozen from a raw blank-mask read, before any feature frame, label file or fit exists, and is read again
only by the one-time scoring step.
"""
import hashlib

import numpy as np

from . import copies
from .common import (DISTRICTS, NOMINAL, SEED_CALIBRATOR, SEED_SPLIT, TARGETS, Step, district_dir, expected_blanks,
                     read_json, sha256_file, utc_now, write_json)

STATEMENT = ("Frozen before any fit, selection or feature build. Never used to fit, tune or select. "
             "Scored once after selection-lock.json.")


# ------------------------------------------------------------------ pure helpers (unit-tested)
def holdout_rows(split, observed):
    """All observed rows of split 2."""
    return np.flatnonzero((np.asarray(split) == 2) & np.asarray(observed, bool)).astype(np.int64)


def content_hash(frozen):
    """CO's digest: field name then its int64 row indices, in target order."""
    digest = hashlib.sha256()
    for t in TARGETS:
        digest.update(t.encode())
        digest.update(np.asarray(frozen[t], np.int64).tobytes())
    return digest.hexdigest()


def a_halves(groups, split, half, cal_seed, search_seed):
    """CI:115-118 and TK:166-169: calibration half of the A components, then A1 (search) and A2 (confirmation).

    Returns boolean row masks (iscal, issearch)."""
    ag = np.unique(groups[(split == 1) & (half == 0)])
    shuffled = np.random.default_rng(cal_seed).permutation(ag)
    calgroup, tgroup = shuffled[:len(shuffled) // 2], shuffled[len(shuffled) // 2:]
    tgroup = np.random.default_rng(search_seed).permutation(tgroup)
    searchgroup = tgroup[:len(tgroup) // 2]
    return np.isin(groups, calgroup), np.isin(groups, searchgroup)


def split_a(rows_a, iscal, issearch):
    cal = rows_a[iscal[rows_a]]
    a1 = rows_a[~iscal[rows_a] & issearch[rows_a]]
    a2 = rows_a[~iscal[rows_a] & ~issearch[rows_a]]
    return cal, a1, a2


def leakage_check(groups, sets, holdout):
    """No component in more than one of the development sets; no holdout row or component in any of them."""
    names = list(sets)
    comps = {n: set(np.unique(groups[sets[n]]).tolist()) for n in names}
    for i, a in enumerate(names):
        for b in names[:i]:
            assert not comps[a] & comps[b], f'components shared between {a} and {b}'
    allrows = np.concatenate([sets[n] for n in names]) if names else np.array([], np.int64)
    assert not np.isin(holdout, allrows).any(), 'a holdout row sits in a development set'
    hc = set(np.unique(groups[holdout]).tolist())
    for n in names:
        assert not comps[n] & hc, f'a holdout component is shared with {n}'
    return {n: len(sets[n]) for n in names}


# ------------------------------------------------------------------ steps
def observed_masks(district, redo=False):
    import pandas as pd
    cfg = DISTRICTS[district]
    D = district_dir(district)
    out = D / 'work' / 'observed-masks.npz'
    census = D / 'work' / 'target-census.json'
    with Step(district, 'masks', outputs=[out, census], redo=redo) as st:
        assert (D / 'work' / 'source-check.json').exists(), 'run the source step first'
        results = read_json(D / 'frozen-co' / 'individual-results.json')
        masks = {t: [] for t in TARGETS}
        codes = {t: {} for t in NOMINAL}
        for chunk in pd.read_csv(cfg['source_csv'], encoding='cp1252', usecols=TARGETS, dtype=str, keep_default_na=False,
                                 na_filter=False, chunksize=100000):
            for t in TARGETS:
                s = chunk[t].str.strip()
                masks[t].append(s.ne('').to_numpy())
                if t in NOMINAL:
                    for k, v in s[s.ne('')].value_counts().items():
                        codes[t][k] = codes[t].get(k, 0) + int(v)
        masks = {t: np.concatenate(v) for t, v in masks.items()}
        total = 0
        report = {}
        for t in TARGETS:
            assert len(masks[t]) == cfg['rows']
            blanks = int((~masks[t]).sum())
            assert blanks == expected_blanks(district, t), (t, blanks, expected_blanks(district, t))
            total += blanks
            report[t] = {'blank': blanks, 'observed': int(masks[t].sum())}
        assert total == cfg['completed_values'], (total, cfg['completed_values'])
        for t in NOMINAL:
            got = sorted(codes[t])
            want = results[t]['field']['evaluation_codes']
            assert got == want, f'{t}: observed codes {got} differ from Colorado {want}; transfer indexing would break'
            report[t]['codes'] = {k: codes[t][k] for k in got}
        np.savez_compressed(out, **masks)
        write_json(census, {'blank_cells_total': total, 'fields': report})
        st.note(blank_cells_total=total, nominal_codes_equal_colorado=True, completed_values=cfg['completed_values'])


def freeze_holdout(district, redo=False):
    cfg = DISTRICTS[district]
    D = district_dir(district)
    out, receipt = D / 'work' / 'holdout.npz', D / 'work' / 'holdout-receipt.json'
    with Step(district, 'holdout', outputs=[out, receipt], redo=redo) as st:
        rare = D / '.local' / 'rare-feature-search-20260928' / 'features.pkl'
        downstream = [rare] + list((D / 'work' / 'models').glob('*')) + list((D / 'work' / 'dev').glob('*')) \
            + [D / 'work' / 'dev-partitions.npz']
        present = [str(p) for p in downstream if p.exists()]
        assert not present, f'the holdout is frozen before any feature frame, fit or dev prediction: {present}'
        masks = np.load(D / 'work' / 'observed-masks.npz')
        split = np.load(D / '.local' / 'individual-validation-20260928' / 'labels-and-splits.npz')['split']
        frozen = {t: holdout_rows(split, masks[t]) for t in TARGETS}
        np.savez_compressed(out, **frozen)
        counts = {t: {'observed_total': int(masks[t].sum()), 'holdout_rows': int(len(frozen[t]))} for t in TARGETS}
        rec = {'statement': STATEMENT, 'created': utc_now(), 'district': district,
               'definition': 'Per field: every row with an observed label in split 2 (union-find household/address '
                             'components, seed 20260928, 60/20/20 by component). Includes the rows the legacy cohort draw '
                             'would call test and stress.',
               'holdout_npz_sha256': sha256_file(out), 'rows_content_sha256': content_hash(frozen),
               'source_sha256': cfg['source_sha256'],
               'seeds': {'split': SEED_SPLIT, 'calibrator_half': SEED_CALIBRATOR}, 'fields': counts,
               'total_holdout_rows': int(sum(v['holdout_rows'] for v in counts.values()))}
        write_json(receipt, rec)
        st.note(holdout_npz_sha256=rec['holdout_npz_sha256'], rows_content_sha256=rec['rows_content_sha256'],
                total_holdout_rows=rec['total_holdout_rows'])


def dev_partitions(district, redo=False):
    cfg = DISTRICTS[district]
    D = district_dir(district)
    out = D / 'work' / 'dev-partitions.npz'
    outputs = [out, D / 'work' / 'dev-partitions-summary.json']
    if cfg['is_co']:
        outputs.append(D / 'work' / 'co-cohorts.npz')
    c = copies.load(district)
    with Step(district, 'partitions', outputs=outputs, redo=redo) as st:
        receipt = read_json(D / 'work' / 'holdout-receipt.json')
        assert sha256_file(D / 'work' / 'holdout.npz') == receipt['holdout_npz_sha256'], 'the frozen holdout changed'
        d = D / '.local'
        labels = np.load(d / 'four-metric-validation-20260928' / 'labels.npz')
        labels = {k: labels[k] for k in labels.files}
        splits = np.load(d / 'individual-validation-20260928' / 'labels-and-splits.npz')
        splits = {k: splits[k] for k in splits.files}
        extra = np.load(d / 'rare-feature-search-20260928' / 'partition-extra.npz')
        half = extra['validation_half']
        hold = np.load(D / 'work' / 'holdout.npz')
        groups, split = splits['groups'], splits['split']
        iscal, issearch = a_halves(groups, split, half, c.ci.SEED, c.tk.SEED)
        saved, summary, co_cohorts = {}, {}, {}
        for t in TARGETS:
            y = c.ca.truth(t, labels, splits)
            pool, rows = c.vr.cohorts(y, split, half)
            h = hold[t]
            assert np.array_equal(h, pool['test']), f'{t}: holdout differs from the observed split-2 pool'
            assert np.isin(rows['test'], h).all() and np.isin(rows['stress'], h).all()
            cal, a1, a2 = split_a(rows['A'], iscal, issearch)
            sets = {'train': rows['train'], 'cal': cal, 'A1': a1, 'A2': a2, 'B': rows['B']}
            counts = leakage_check(groups, sets, h)
            assert (split[rows['train']] == 0).all() and (y[rows['train']] >= 0).all()
            for name, arr in sets.items():
                saved[f'{t}__{name}'] = arr.astype(np.int64)
            summary[t] = {**counts, 'holdout': int(len(h))}
            if cfg['is_co']:       # G0 only: Colorado's own test/stress cohorts, never stored for a district
                co_cohorts[f'{t}__test'] = rows['test']
                co_cohorts[f'{t}__stress'] = rows['stress']
                co_cohorts[f'{t}__A'] = rows['A']
        np.savez_compressed(out, **saved)
        write_json(D / 'work' / 'dev-partitions-summary.json', {'sets': summary, 'leakage_asserts': 'passed',
                                                               'holdout_receipt_sha256': sha256_file(D / 'work' / 'holdout-receipt.json')})
        if cfg['is_co']:
            np.savez_compressed(D / 'work' / 'co-cohorts.npz', **co_cohorts)
        st.note(fields=len(TARGETS), leakage_asserts='passed', train_rows=80000,
                example_sets=summary[TARGETS[0]])
