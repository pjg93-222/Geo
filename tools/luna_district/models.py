"""P3-07 legacy refits, P3-08 household features, P3-09 household-model fits, P3-11 negative control.

Model math is the copied Luna code: encoders (search_rare.Encoder, revised.Encoder), RandomForestClassifier and KNN
probabilities (validate_individual), calibration (complete_individuals), repaired/prior-corrected references,
household_model.household_block / fit_forest / forest_probability. This module only sequences them on the frozen
configuration (individual-results.json of Colorado) and the district's partitions. No search of any kind.

Dev predictions are made only for A1, A2 and B; the frozen holdout is never loaded here.
"""
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from . import copies
from .common import (BASE_FIELDS, CHILDREN, DISTRICTS, DWELLING, HM_FIELDS, HM_FIT_FIELDS, N_JOBS, NOMINAL, R1_FIELDS,
                     SEED_CONTROL, TARGETS, Step, district_dir, log, read_json, sha256_file, write_json)

DEV_SETS = ('A1', 'A2', 'B')
REPAIR = (CHILDREN, DWELLING)
CHUNK = 20000


# ------------------------------------------------------------------ context and small helpers
def _npz(path):
    z = np.load(path)
    return {k: z[k] for k in z.files}


def load_context(c, D, frame=True):
    import pandas as pd
    d = D / '.local'
    ctx = SimpleNamespace(c=c, D=D)
    ctx.labels = _npz(d / 'four-metric-validation-20260928' / 'labels.npz')
    ctx.splits = _npz(d / 'individual-validation-20260928' / 'labels-and-splits.npz')
    ctx.extra = _npz(d / 'rare-feature-search-20260928' / 'partition-extra.npz')
    ctx.results = read_json(D / 'frozen-co' / 'individual-results.json')
    ctx.dev = _npz(D / 'work' / 'dev-partitions.npz')
    if frame:
        fr = pd.read_pickle(d / 'rare-feature-search-20260928' / 'features.pkl')
        raw = c.fx.load_stances()
        c.ci.add_stances(fr, raw)
        ctx.frame, ctx.raw = fr, raw
    return ctx


def truth(ctx, t):
    return ctx.c.ca.truth(t, ctx.labels, ctx.splits)


def codes_of(ctx, t):
    return ctx.results[t]['field']['evaluation_codes']


def prior_of(ctx, t, y=None):
    """Observed split-0 code prevalence; only for the two repaired fields (children D, dwelling G rare codes)."""
    if t not in REPAIR:
        return None
    y = truth(ctx, t) if y is None else y
    nc = len(codes_of(ctx, t))
    counts = np.bincount(y[(ctx.splits['split'] == 0) & (y >= 0)], minlength=nc)
    return counts / counts.sum()


def step_name(base, shard, shards, fields, universe):
    import hashlib
    if fields:
        return base + '-' + hashlib.sha1(','.join(universe).encode()).hexdigest()[:8]
    return base if shards == 1 else f'{base}-s{shard}of{shards}'


def fields_for(universe, shard, shards, fields):
    return [t for i, t in enumerate(universe) if i % shards == shard and (not fields or t in fields)]


def atomic_npz(path, **arrays):
    tmp = path.with_name(path.name + '.partial.npz')
    np.savez_compressed(tmp, **arrays)
    tmp.replace(path)


def atomic_dump(obj, path):
    import joblib
    tmp = path.with_name(path.name + '.partial')
    joblib.dump(obj, tmp, compress=3)
    tmp.replace(path)


def plain_probs(c, bundle, data, nc):
    """Legacy calibrated probabilities of a bundle (CI.predict / tune_knn.predict_current), unrepaired."""
    x = bundle['encoder'].transform(data)
    if bundle['model'] is not None:
        p = c.vr.rf_probability(bundle['model'], x, nc)
    else:
        settings = bundle['configuration']['settings']
        distance, index = c.vr.neighbours(bundle['knn_training_x'], x, settings['k'])
        p = c.vi.knn_probabilities(bundle['knn_training_y'], index, distance, np.arange(nc), **settings)
    return c.ci.calibrated(bundle['calibrator'], p, nc)


def in_chunks(fn, data, step=CHUNK):
    return np.concatenate([fn(data.iloc[s:s + step]) for s in range(0, len(data), step)])


def decision_probs(c, t, bundle, data, nc, prior):
    """The legacy decision probabilities: prior-corrected for children/dwelling (as household_model.legacy_decisions),
    coverage-repaired reference (identical to the calibrated probabilities when no code is absent) elsewhere."""
    if prior is not None:
        return c.pr.prior_corrected_reference(bundle, data, nc, prior)
    return c.rv.repaired_reference(bundle, data, nc)


def absent_codes(bundle, nc):
    return np.setdiff1d(np.arange(nc), bundle['calibrator'].classes_).tolist()


# ------------------------------------------------------------------ P3-07 legacy refits
def fit_legacy(c, ctx, t):
    item = ctx.results[t]
    cfg = item['configuration']
    feats = list(item['selected_features'])
    nc = len(codes_of(ctx, t))
    y = truth(ctx, t)
    tr, calr = ctx.dev[f'{t}__train'], ctx.dev[f'{t}__cal']
    frame = ctx.frame
    enc = c.sr.Encoder().fit(frame.iloc[tr], feats)
    xtrain = enc.transform(frame.iloc[tr])
    model = None
    if cfg['family'] == 'RF':
        model = c.vi.RandomForestClassifier(n_estimators=128, max_leaf_nodes=1024, n_jobs=N_JOBS, random_state=c.vr.SEED,
                                            **cfg['settings']).fit(xtrain, y[tr])

    def raw_probs(data):
        x = enc.transform(data)
        if model is not None:
            return c.vr.rf_probability(model, x, nc)
        distance, index = c.vr.neighbours(xtrain, x, cfg['settings']['k'])
        return c.vi.knn_probabilities(y[tr], index, distance, np.arange(nc), **cfg['settings'])

    pc = raw_probs(frame.iloc[calr])
    cal = c.ci.LogisticRegression(C=1., max_iter=1000, random_state=c.ci.SEED).fit(c.ci.calibration_x(pc), y[calr])
    return {'configuration': cfg, 'features': feats, 'encoder': enc, 'model': model, 'calibrator': cal, 'threshold': None,
            'codes': codes_of(ctx, t), 'tiers': {}, 'qualified_tiers': {},
            'knn_training_x': xtrain if model is None else None, 'knn_training_y': y[tr] if model is None else None}


def legacy_refits(district, shard=0, shards=1, fields=None, redo=False, resume=False):
    D = district_dir(district)
    universe = fields_for(TARGETS, shard, shards, fields)
    name = step_name('legacy', shard, shards, fields, universe)
    mdir, ddir = D / 'work' / 'models', D / 'work' / 'dev'
    for p in (mdir, ddir):
        p.mkdir(parents=True, exist_ok=True)
    outputs = [p for t in universe for p in (mdir / f'legacy-{t}.joblib', ddir / f'legacy-{t}.npz', ddir / f'legacy-{t}.json')]
    c = copies.load(district)
    with Step(district, name, outputs=outputs, heavy=True, redo=redo, resume=resume) as st:
        ctx = load_context(c, D)
        done = {}
        with c.vi.threadpool_limits(limits=1):
            for t in universe:
                if all(p.exists() for p in (mdir / f'legacy-{t}.joblib', ddir / f'legacy-{t}.npz', ddir / f'legacy-{t}.json')):
                    log(f'{t}: complete, skipped (resume)')
                    continue
                t0 = time.time()
                nc = len(codes_of(ctx, t))
                y = truth(ctx, t)
                bundle = fit_legacy(c, ctx, t)
                prior = prior_of(ctx, t, y)
                arrays = {}
                for s in DEV_SETS:
                    rows = ctx.dev[f'{t}__{s}']
                    data = ctx.frame.iloc[rows]
                    arrays[f'{s}_rows'] = rows
                    arrays[f'{s}_plain'] = plain_probs(c, bundle, data, nc).astype(np.float32)
                    arrays[f'{s}_decision'] = c.hm.legacy_decisions(t, bundle, data, nc, prior).astype(np.int16)
                    if prior is not None:
                        arrays[f'{s}_prior'] = c.pr.prior_corrected_reference(bundle, data, nc, prior)
                absent = absent_codes(bundle, nc)
                if t in BASE_FIELDS and t != DWELLING:
                    assert not absent, f'{t}: calibrator omits {absent}; prior correction is ruled for dwelling only (R-2)'
                rec = {'field': t, 'family': bundle['configuration']['family'], 'settings': bundle['configuration']['settings'],
                       'features': len(bundle['features']), 'codes': bundle['codes'],
                       'classes_in_training': np.unique(y[ctx.dev[f"{t}__train"]]).tolist(),
                       'classes_in_calibration': bundle['calibrator'].classes_.tolist(),
                       'codes_absent_from_calibrator': [bundle['codes'][i] for i in absent],
                       'training_class_counts': np.bincount(y[ctx.dev[f"{t}__train"]], minlength=nc).tolist(),
                       'calibration_rows': int(len(ctx.dev[f'{t}__cal'])), 'seconds': round(time.time() - t0, 1)}
                atomic_dump(bundle, mdir / f'legacy-{t}.joblib')
                atomic_npz(ddir / f'legacy-{t}.npz', **arrays)
                write_json(ddir / f'legacy-{t}.json', rec)
                done[t] = rec['seconds']
                log(f'{t}: legacy refit {rec["family"]}, absent from calibrator {rec["codes_absent_from_calibrator"] or "none"}, {rec["seconds"]}s')
        st.note(fields_done=len(done), seconds_per_field=done, threads=f'threadpool_limits(1); RF n_jobs={N_JOBS} (CI: 2; same trees)')


# ------------------------------------------------------------------ P3-08 household features
def household_features(district, redo=False):
    D = district_dir(district)
    cfg = DISTRICTS[district]
    hdir = D / 'work' / 'hm'
    hdir.mkdir(parents=True, exist_ok=True)
    outputs = [hdir / 'features-summary.json', hdir / 'shuffle-donors.npz', hdir / 'fixture-checks-copy.json'] + \
              [hdir / f'features-{t}.npz' for t in TARGETS] + [D / '.local' / 'household-model-20260930' / 'household-ids.npz',
                                                              D / '.local' / 'household-model-20260930' / 'fixture-checks.json']
    c = copies.load(district)
    with Step(district, 'hhfeat', outputs=outputs, heavy=True, redo=redo) as st:
        hm = c.hm
        hm.fixtures()                       # toy proofs incl. detection of a self-including builder; writes fixture-checks.json
        fx = read_json(D / '.local' / 'household-model-20260930' / 'fixture-checks.json')
        assert fx['passed'] is True and fx['self_leak_detected'] is True
        write_json(hdir / 'fixture-checks-copy.json', fx)
        ctx = load_context(c, D, frame=False)
        split = ctx.splits['split']
        sha = cfg['source_sha256']
        H = hm.household_ids(sha)
        G = c.hc.households(ctx.splits)
        hm.assert_groups(H, G, split)
        Hs, Gs, donor = hm.shuffled_assignment(H, G, split)
        hm.assert_groups(Hs, Gs, split)
        assert np.array_equal(np.bincount(Hs), np.bincount(H)) and np.array_equal(np.bincount(Gs), np.bincount(G))
        np.savez_compressed(hdir / 'shuffle-donors.npz', donor=donor.astype(np.int32))
        summary = {}
        rng = np.random.default_rng(20261047)
        for t in TARGETS:
            codes = codes_of(ctx, t)
            nc = len(codes)
            binary = ctx.results[t]['field']['task'] == 'binary_nonzero'
            y = truth(ctx, t)
            real = hm.household_block(y, H, G, nc, binary)
            shuffled = hm.household_block(y, Hs, Gs, nc, binary)
            names = hm.block_names(codes, binary)
            assert real.shape == shuffled.shape == (cfg['rows'], len(names)) and np.isfinite(real).all() and np.isfinite(shuffled).all()
            atomic_npz(hdir / f'features-{t}.npz', real=real, shuffled=shuffled, columns=np.array(names))
            ix, jx = names.index('hh_n_obs'), names.index('comp_n_obs')
            # own-label flip test on 300 random observed rows in distinct components (exactly 0 for a builder that never sees a row's label)
            flip = None
            if t in HM_FIT_FIELDS:
                Gs_ = Gs
                rows = hm.flip_rows(y, G, Gs_, rng, 300)
                flip = {'rows': int(len(rows)), 'largest_change_real': hm.flip_test(hm.household_block, y, H, G, nc, binary, rows),
                        'largest_change_shuffled': hm.flip_test(hm.household_block, y, Hs, Gs, nc, binary, rows)}
                assert flip['largest_change_real'] == 0. and flip['largest_change_shuffled'] == 0., (t, flip)
            summary[t] = {'columns': names, 'sha256': sha256_file(hdir / f'features-{t}.npz'),
                          'share_rows_with_observed_household_other': float((real[:, ix] > 0).mean()),
                          'share_rows_with_observed_component_other': float((real[:, jx] > 0).mean()), 'flip_test': flip}
        write_json(hdir / 'features-summary.json', {'source_sha256': sha, 'households': int(H.max() + 1), 'components': int(G.max() + 1),
                                                    'shuffle': 'within-split permutation, np.random.default_rng(20261047), one permutation shared by all fields',
                                                    'fields': summary})
        st.note(households=int(H.max() + 1), components=int(G.max() + 1), fields=len(summary), flip_tests='passed (0 change)',
                groups_asserts='household nested in component; no household or component spans splits (real and shuffled)')


def load_block(D, t):
    z = np.load(D / 'work' / 'hm' / f'features-{t}.npz')
    return z['real'], z['shuffled'], [str(x) for x in z['columns']]


# ------------------------------------------------------------------ P3-09 household-model fits
def household_fits(district, shard=0, shards=1, fields=None, redo=False, resume=False):
    D = district_dir(district)
    universe = fields_for(HM_FIT_FIELDS, shard, shards, fields)
    name = step_name('hmfit', shard, shards, fields, universe)
    mdir, ddir = D / 'work' / 'models', D / 'work' / 'dev'
    outputs = [p for t in universe for p in (mdir / f'hm-T-{t}.joblib', mdir / f'hm-S-{t}.joblib', ddir / f'hm-{t}.npz', ddir / f'hm-{t}.json')]
    c = copies.load(district)
    with Step(district, name, outputs=outputs, heavy=True, redo=redo, resume=resume) as st:
        ctx = load_context(c, D)
        hm = c.hm
        times, tripwire = {}, {}
        with c.vi.threadpool_limits(limits=2):
            for t in universe:
                if all(p.exists() for p in (mdir / f'hm-T-{t}.joblib', mdir / f'hm-S-{t}.joblib', ddir / f'hm-{t}.npz', ddir / f'hm-{t}.json')):
                    log(f'{t}: complete, skipped (resume)')
                    continue
                t0 = time.time()
                item = ctx.results[t]
                codes = codes_of(ctx, t)
                nc = len(codes)
                y = truth(ctx, t)
                train = ctx.dev[f'{t}__train']
                features = list(item['selected_features'])
                real, shuffled, names = load_block(D, t)
                assert names == hm.block_names(codes, item['field']['task'] == 'binary_nonzero')
                enc = c.rv.Encoder().fit(ctx.frame.iloc[train], features, 'standard')
                x = enc.transform(ctx.frame.iloc[train])
                settings = hm.rf_settings(item)
                rec = {'field': t, 'settings': settings, 'features': len(features), 'encoded_columns': int(x.shape[1]),
                       'household_columns': names, 'class_counts': np.bincount(y[train], minlength=nc).tolist()}
                bundles = {}
                for kind, block in (('T', real), ('S', shuffled)):
                    model = hm.fit_forest(np.hstack([x, block[train]]), y[train], settings)
                    seen = [int(k) for k in model.classes_]
                    rec[kind] = {'classes_seen': [codes[k] for k in seen],
                                 'codes_absent_from_training': [codes[k] for k in range(nc) if k not in seen],
                                 'household_importance_share': float(model.feature_importances_[-len(names):].sum()),
                                 'trees': len(model.estimators_), 'leaves_mean': float(np.mean([e.get_n_leaves() for e in model.estimators_]))}
                    bundles[kind] = {'kind': kind, 'model': model, 'encoder': enc, 'features': features, 'household_columns': names,
                                     'training_rows': train, 'settings': settings, 'codes': codes, 'classes_seen': seen}
                    atomic_dump(bundles[kind], mdir / f'hm-{kind}-{t}.joblib')
                    rec[kind]['model_sha256'] = sha256_file(mdir / f'hm-{kind}-{t}.joblib')
                legacy = np.load(ddir / f'legacy-{t}.npz')
                arrays = {}
                gains = {}
                for s in DEV_SETS:
                    rows = ctx.dev[f'{t}__{s}']
                    data = ctx.frame.iloc[rows]
                    arrays[f'{s}_rows'] = rows
                    for kind, block in (('T', real), ('S', shuffled)):
                        arrays[f'{s}_{kind}'] = hm.forest_probability(bundles[kind], data, block[rows], nc).astype(np.float32)
                    if s in ('A1', 'A2'):
                        yy = y[rows]
                        gains[s] = float((arrays[f'{s}_S'].argmax(axis=1) == yy).mean() - (legacy[f'{s}_decision'] == yy).mean())
                rec['tripwire_S_minus_legacy'] = gains
                tripped = max(gains.values()) > hm.TRIPWIRE
                rec['tripwire_tripped'] = bool(tripped)
                rec['seconds'] = round(time.time() - t0, 1)
                atomic_npz(ddir / f'hm-{t}.npz', **arrays)
                write_json(ddir / f'hm-{t}.json', rec)
                times[t] = rec['seconds']
                tripwire[t] = gains
                log(f'{t}: household model T/S fitted; household importance T {rec["T"]["household_importance_share"]:.3f} S {rec["S"]["household_importance_share"]:.3f}; '
                    f'S-legacy A1 {gains["A1"]:+.4f} A2 {gains["A2"]:+.4f}; absent from training T {rec["T"]["codes_absent_from_training"] or "none"}; {rec["seconds"]}s')
                assert not tripped, f'{t}: leakage tripwire, S beats the legacy decision by more than 3 pp: {gains}'
        st.note(fields_done=len(times), seconds_per_field=times, tripwire_S_minus_legacy=tripwire, tripwire_limit=hm.TRIPWIRE)


# ------------------------------------------------------------------ candidate probabilities
def bundle_path(D, t, kind):
    """R = the frozen method refit on the district; H = district household model. Only district-trained bundles exist here."""
    if kind == 'H' or (kind == 'R' and t in HM_FIELDS):
        return D / 'work' / 'models' / f'hm-T-{t}.joblib'
    assert kind == 'R', f'{t}: unknown candidate {kind!r}; every fill comes from a model trained on this district'
    return D / 'work' / 'models' / f'legacy-{t}.joblib'


def candidate_probs(c, ctx, t, kind, data, rows, bundle=None):
    """Decision probabilities of one candidate (R or H) for the rows of `data` (a frame slice of `rows`)."""
    import joblib
    D = ctx.D
    nc = len(codes_of(ctx, t))
    if bundle is None:
        bundle = joblib.load(bundle_path(D, t, kind))
    if bundle.get('model') is not None and hasattr(bundle['model'], 'set_params'):
        bundle['model'].set_params(n_jobs=N_JOBS)
    assert list(bundle['codes']) == codes_of(ctx, t), f'{t}: code order differs from Colorado'
    if kind == 'H' or t in HM_FIELDS:
        block = load_block(D, t)[0]
        parts = [c.hm.forest_probability(bundle, data.iloc[a:a + CHUNK], block[rows[a:a + CHUNK]], nc) for a in range(0, len(rows), CHUNK)]
        return np.concatenate(parts)
    prior = prior_of(ctx, t) if t in REPAIR else None
    parts = [decision_probs(c, t, bundle, data.iloc[a:a + CHUNK], nc, prior) for a in range(0, len(rows), CHUNK)]
    return np.concatenate(parts)


# ------------------------------------------------------------------ P3-11 negative control
def negative_control(district, shard=0, shards=1, fields=None, redo=False, resume=False):
    D = district_dir(district)
    out = D / 'work' / 'control.json'
    c = copies.load(district)
    with Step(district, 'control', outputs=[out], heavy=True, redo=redo) as st:
        ctx = load_context(c, D)
        t = 'sci_charitable_donor'
        item = ctx.results[t]
        nc = len(codes_of(ctx, t))
        y = truth(ctx, t).copy()
        split = ctx.splits['split']
        pool = np.flatnonzero((split == 0) & (y >= 0))
        y[pool] = np.random.default_rng(SEED_CONTROL).permutation(y[pool])      # permuted within split 0
        ctx2 = SimpleNamespace(**{**vars(ctx)})
        ctx2.labels = dict(ctx.labels)
        ctx2.labels[t] = y.copy()
        y_true = truth(ctx, t)
        tr, calr = ctx.dev[f'{t}__train'], ctx.dev[f'{t}__cal']
        cfg = item['configuration']
        feats = list(item['selected_features'])
        frame = ctx.frame
        with c.vi.threadpool_limits(limits=1):
            enc = c.sr.Encoder().fit(frame.iloc[tr], feats)
            xtrain = enc.transform(frame.iloc[tr])
            model = c.vi.RandomForestClassifier(n_estimators=128, max_leaf_nodes=1024, n_jobs=N_JOBS, random_state=c.vr.SEED,
                                                **cfg['settings']).fit(xtrain, y[tr])
            pc = c.vr.rf_probability(model, enc.transform(frame.iloc[calr]), nc)
            cal = c.ci.LogisticRegression(C=1., max_iter=1000, random_state=c.ci.SEED).fit(c.ci.calibration_x(pc), y_true[calr])
            bundle = {'encoder': enc, 'model': model, 'calibrator': cal, 'configuration': cfg}
            majority = int(np.bincount(y_true[(split == 0) & (y_true >= 0)], minlength=nc).argmax())
            rows_out = {}
            for s in DEV_SETS:
                rows = ctx.dev[f'{t}__{s}']
                p = plain_probs(c, bundle, frame.iloc[rows], nc)
                acc = float((p.argmax(axis=1) == y_true[rows]).mean())
                maj = float((y_true[rows] == majority).mean())
                rows_out[s] = {'accuracy': acc, 'majority': maj, 'gap_pp': round(100 * (acc - maj), 3)}
        write_json(out, {'field': t, 'control': 'legacy refit on training labels permuted within split 0 (seed 20261101); calibrator on true calibration labels',
                         'sets': rows_out, 'pass_rule': 'every set within +/-1.0 pp of majority'})
        bad = {s: v for s, v in rows_out.items() if abs(v['gap_pp']) > 1.0}
        st.note(sets=rows_out, passed=not bad)
        assert not bad, f'negative control failed: the evaluation can pass a model with no signal {bad}'
