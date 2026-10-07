"""Gate G0: replay the copies and the driver on Colorado's own data and compare with Colorado's saved artifacts.

Pass criteria are fixed in the plan (section 5, G0) and written here before the comparison runs:
 1 splits, groups and labels equal Colorado's arrays exactly
 2 features.pkl exactly equal (pandas assert_frame_equal, check_exact) and partition-extra arrays equal
 3 cohorts (train, calibration, A1, A2, B, test, stress) equal Colorado's
 4 legacy refit for cc, liberal (KNN), democrat (stances) and dwelling: calibrated blank probabilities within 1e-7 of
   accuracy-completion individual-predictions and identical argmax on every blank; dwelling prior-corrected probabilities
   within 1e-6 of prior-corrected-repair applied-sii_dwelling_type
 5 household model for owner_renter and charitable_donor: household blocks equal Colorado's features files; blank-row
   probabilities of the replayed T model within 1e-6 of applied-<t>.npz; S tripwire values reproduced
 6 path guard and originals check pass; copy-diff contains only the declared patch lines
"""
import json

import numpy as np

from . import copies
from .common import DISTRICTS, HM_FIELDS, L, TARGETS, Step, district_dir, log, read_json, sha256_file, write_json
from .models import (DEV_SETS, bundle_path, candidate_probs, codes_of, load_block, load_context, plain_probs, prior_of,
                     truth)

G0_LEGACY = ['sci_cpi_cc_index', 'sci_cpi_liberal_index', 'sci_ct_political_partyaff_democrat', 'sii_dwelling_type']
G0_HM = ['sci_owner_renter', 'sci_charitable_donor']
CO_COMP, CO_ACC, CO_PRIOR, CO_HM, CO_KNN = (L / 'completion-20260929', L / 'accuracy-completion-20260929',
                                            L / 'prior-corrected-repair-20260929', L / 'household-model-20260930',
                                            L / 'knn-tuning-20260929')


def compare(district, redo=False, fields=None, **_):
    assert DISTRICTS[district]['is_co'], 'G0 compares the Colorado replay only'
    D = district_dir(district)
    out = D / 'work' / 'g0-result.json'
    c = copies.load(district)
    checks = []

    def check(name, ok, detail):
        checks.append({'check': name, 'passed': bool(ok), 'detail': detail})
        log(f'G0 {"PASS" if ok else "FAIL"}: {name} | {detail}')

    with Step(district, 'g0', outputs=[out], heavy=True, redo=redo) as st:
        import pandas as pd
        d = D / '.local'
        # ---- 1 splits, groups, labels
        mine = np.load(d / 'individual-validation-20260928' / 'labels-and-splits.npz')
        co = np.load(L / 'individual-validation-20260928' / 'labels-and-splits.npz')
        same = {k: bool(np.array_equal(mine[k], co[k])) for k in co.files}
        check('labels-and-splits.npz equals Colorado', all(same.values()) and set(mine.files) == set(co.files), same)
        mine_l = np.load(d / 'four-metric-validation-20260928' / 'labels.npz')
        co_l = np.load(L / 'four-metric-validation-20260928' / 'labels.npz')
        same_l = {k: bool(np.array_equal(mine_l[k], co_l[k])) for k in co_l.files}
        check('labels.npz equals Colorado (24 fields)', all(same_l.values()) and set(mine_l.files) == set(co_l.files),
              f'{sum(same_l.values())}/{len(same_l)} arrays equal')
        # ---- 2 feature frame
        a = pd.read_pickle(d / 'rare-feature-search-20260928' / 'features.pkl')
        b = pd.read_pickle(L / 'rare-feature-search-20260928' / 'features.pkl')
        try:
            pd.testing.assert_frame_equal(a, b, check_exact=True)
            ok, detail = True, f'{a.shape[0]} x {a.shape[1]} identical, dtypes and categories included'
        except AssertionError as ex:
            ok, detail = False, str(ex)[:600]
        check('features.pkl exactly equals Colorado', ok, detail)
        del a, b
        pe_m = np.load(d / 'rare-feature-search-20260928' / 'partition-extra.npz')
        pe_c = np.load(L / 'rare-feature-search-20260928' / 'partition-extra.npz')
        check('partition-extra.npz equals Colorado', all(np.array_equal(pe_m[k], pe_c[k]) for k in pe_c.files),
              {k: bool(np.array_equal(pe_m[k], pe_c[k])) for k in pe_c.files})
        check('feature-catalog.json equals Colorado', read_json(d / 'rare-feature-search-20260928' / 'feature-catalog.json')
              == read_json(L / 'rare-feature-search-20260928' / 'feature-catalog.json'), 'json equality')
        # ---- 3 cohorts
        ctx = load_context(c, D)
        cohorts_co = np.load(D / 'work' / 'co-cohorts.npz')
        coh = {}
        for t in TARGETS:
            ev = np.load(CO_COMP / f'individual-evaluation-{t}.npz')
            dev = np.load(CO_KNN / f'development-{t}.npz')
            coh[t] = (np.array_equal(ctx.dev[f'{t}__B'], ev['B_rows']) and np.array_equal(cohorts_co[f'{t}__test'], ev['test_rows'])
                      and np.array_equal(cohorts_co[f'{t}__stress'], ev['stress_rows'])
                      and np.array_equal(ctx.dev[f'{t}__cal'], dev['calibration_rows'])
                      and np.array_equal(ctx.dev[f'{t}__A1'], dev['A1_rows']) and np.array_equal(ctx.dev[f'{t}__A2'], dev['A2_rows']))
        check('cohorts B/test/stress/cal/A1/A2 equal Colorado (26 fields)', all(coh.values()), f'{sum(coh.values())}/26 fields')
        import joblib
        trains = {}
        for t in [x for x in G0_HM + ['sci_ct_political_partyaff_democrat', 'sii_dwelling_type']]:
            bundle = joblib.load(D / 'co-transfer' / 'hm' / f'model-T-{t}.joblib')
            trains[t] = bool(np.array_equal(ctx.dev[f'{t}__train'], bundle['training_rows']))
        check('train rows equal Colorado household-model training rows', all(trains.values()), trains)
        # ---- 4 legacy refits
        with c.vi.threadpool_limits(limits=1):
            for t in G0_LEGACY:
                bundle = joblib.load(bundle_path(D, t, 'R'))
                bundle['model'].set_params(n_jobs=2) if bundle['model'] is not None else None
                nc = len(codes_of(ctx, t))
                y = truth(ctx, t)
                blank = np.flatnonzero(y < 0)
                probs = np.concatenate([plain_probs(c, bundle, ctx.frame.iloc[blank[s:s + 10000]], nc) for s in range(0, len(blank), 10000)]).astype(np.float32)
                ref = np.load(CO_ACC / f'individual-predictions-{t}.npz')
                same_rows = np.array_equal(ref['rows'], blank)
                diff = float(np.abs(probs - ref['probabilities']).max())
                arg = bool(np.array_equal(probs.argmax(axis=1), ref['probabilities'].argmax(axis=1)))
                check(f'legacy refit {t}: calibrated blank probabilities', same_rows and diff <= 1e-7 and arg,
                      f'{len(blank)} blanks, max abs diff {diff:.3e}, argmax identical {arg}')
                if t == 'sii_dwelling_type':
                    prior = prior_of(ctx, t, y)
                    pc = np.concatenate([c.pr.prior_corrected_reference(bundle, ctx.frame.iloc[blank[s:s + 10000]], nc, prior) for s in range(0, len(blank), 10000)])
                    ref2 = np.load(CO_PRIOR / 'applied-sii_dwelling_type.npz')
                    diff2 = float(np.abs(pc - ref2['probabilities']).max())
                    check('dwelling prior-corrected probabilities', np.array_equal(ref2['rows'], blank) and diff2 <= 1e-6
                          and bool(np.array_equal(pc.argmax(axis=1), ref2['predicted'])), f'max abs diff {diff2:.3e}')
        # ---- 5 household model
        co_train = read_json(CO_HM / 'train-summary.json')
        co_sel = read_json(CO_HM / 'selection.json')
        for t in TARGETS:
            real, shuf, names = load_block(D, t)
            ref = np.load(CO_HM / f'features-{t}.npz')
            ok = np.array_equal(real, ref['real']) and np.array_equal(shuf, ref['shuffled']) and names == [str(x) for x in ref['columns']]
            if not ok:
                check(f'household block {t} equals Colorado', False, 'arrays differ')
        check('household blocks equal Colorado (26 fields, real and shuffled)', not [x for x in checks if x['check'].startswith('household block')], '26/26 unless listed above')
        for t in G0_HM:
            nc = len(codes_of(ctx, t))
            y = truth(ctx, t)
            blank = np.flatnonzero(y < 0)
            bundle = joblib.load(bundle_path(D, t, 'R'))
            bundle['model'].set_params(n_jobs=2)
            block = load_block(D, t)[0]
            with c.vi.threadpool_limits(limits=2):
                p = np.concatenate([c.hm.forest_probability(bundle, ctx.frame.iloc[blank[s:s + 20000]], block[blank[s:s + 20000]], nc)
                                    for s in range(0, len(blank), 20000)])
            ref = np.load(CO_HM / f'applied-{t}.npz')
            diff = float(np.abs(p - ref['probabilities']).max())
            check(f'household model {t}: blank probabilities', np.array_equal(ref['rows'], blank) and diff <= 1e-6
                  and bool(np.array_equal(p.argmax(axis=1), ref['predicted'])), f'{len(blank)} blanks, max abs diff {diff:.3e}')
            mine_rec = read_json(D / 'work' / 'dev' / f'hm-{t}.json')
            co_rec = co_train[t]
            imp = {k: abs(mine_rec[k]['household_importance_share'] - co_rec[k]['household_importance_share']) for k in ('T', 'S')}
            trip = {s: abs(mine_rec['tripwire_S_minus_legacy'][s] - co_sel[t]['tripwire'][f'S_minus_legacy_{s}']) for s in ('A1', 'A2')}
            check(f'S tripwire reproduced {t}', max(list(imp.values()) + list(trip.values())) <= 1e-9,
                  {'importance_share_abs_diff': imp, 'S_minus_legacy_abs_diff': trip,
                   'mine': mine_rec['tripwire_S_minus_legacy'], 'colorado': {s: co_sel[t]['tripwire'][f'S_minus_legacy_{s}'] for s in ('A1', 'A2')}})
        # ---- 6 copies and originals
        from . import copy_stage
        manifest = read_json(D / 'copy-manifest.json')
        bad = []
        for rel, info in manifest['files'].items():
            src = L / rel
            cp = D / '.local' / rel
            assert sha256_file(src) == info['original_sha256']
            assert sha256_file(cp) == info['copy_sha256_after_patch']
            copy_stage.line_gate(src.read_text(encoding='utf-8'), cp.read_text(encoding='utf-8'), info['patched_lines'])
        check('copy-diff contains only the declared patch lines', not bad, f'{len(manifest["files"])} files, every non-patched line byte-identical')
        from . import originals
        changes = originals.check_stat(strict=False)
        check('Luna originals unchanged (stat check)', not changes, str(changes[:5]) or 'identical')
        write_json(out, {'passed': all(x['passed'] for x in checks), 'checks': checks})
        st.note(passed=all(x['passed'] for x in checks), failed=[x['check'] for x in checks if not x['passed']])
        assert all(x['passed'] for x in checks), 'G0 failed: ' + '; '.join(x['check'] for x in checks if not x['passed'])
