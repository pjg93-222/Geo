"""P3-13: score the frozen holdout ONCE.

Loads the locked models, predicts each field's holdout rows for the chosen lineup (the model fill, or the majority code where
the development rule replaced the model), the district refit (R) and the majority code, and writes aggregates only: accuracy
(fill and model), lift over the majority code, AUC of the positive-class probability for binary fields, macro-F1 for the four
nominal fields, per-class support and recall, a paired 95% component bootstrap interval (ca.clustered_intervals, 1000 draws,
seed 20260944), the blank-count-weighted overall accuracy (the way Colorado's 86.760% is weighted), the blank-mix reweighted
accuracy (household_model.reweight), the stress-frame accuracy on the holdout's 5,000-row stress sample, and the flags F1-F3.
A re-score is refused: `--redo` is not accepted for this step.
"""
import time

import numpy as np

from . import copies
from .common import DISTRICTS, NOMINAL, TARGETS, Step, district_dir, log, read_json, sha256_file, write_json
from .models import candidate_probs, codes_of, load_block, load_context, truth
from .select import fill_of

F1_PP, F3_PP = 0.5, 2.0


def verify_lock(D, fields):
    lock = read_json(D / 'work' / 'selection-lock.json')
    assert sha256_file(D / 'work' / 'selection.json') == lock['selection_sha256'], 'selection.json changed after the lock'
    assert sha256_file(D / 'work' / 'holdout-receipt.json') == lock['holdout_receipt_sha256'], 'holdout receipt changed'
    assert sha256_file(D / 'work' / 'holdout.npz') == lock['holdout_npz_sha256'], 'holdout file changed'
    assert sha256_file(D / 'copy-manifest.json') == lock['copy_manifest_sha256'], 'copy manifest changed'
    for name, h in lock['model_sha256'].items():
        assert sha256_file(D / 'work' / 'models' / name) == h, f'model {name} changed after the lock'
    for name, h in lock['dev_prediction_sha256'].items():
        assert sha256_file(D / 'work' / 'dev' / name) == h, f'dev prediction {name} changed after the lock'
    return lock


def per_class(yy, pred, nc):
    support = np.bincount(yy, minlength=nc)
    hit = np.bincount(yy[pred == yy], minlength=nc)
    return {'support': support.tolist(), 'recall': [None if s == 0 else float(h / s) for h, s in zip(hit, support)]}


def score_holdout_once(district, redo=False, fields=None, **_):
    assert not redo, 'the holdout is scored once; a re-score is refused'
    cfg = DISTRICTS[district]
    D = district_dir(district)
    universe = list(fields) if fields else list(TARGETS)
    if not cfg['is_co']:
        assert not fields, 'a district holdout is scored for all 26 fields at once'
    out, flags_path = D / 'work' / 'holdout-results.json', D / 'work' / 'holdout-flags.json'
    c = copies.load(district)
    with Step(district, 'score', outputs=[out, flags_path], heavy=True) as st:
        verify_lock(D, universe)
        sel = read_json(D / 'work' / 'selection.json')
        ctx = load_context(c, D)
        hold = np.load(D / 'work' / 'holdout.npz')
        groups, split, half = ctx.splits['groups'], ctx.splits['split'], ctx.extra['validation_half']
        comp = c.hc.households(ctx.splits)
        comp_size = np.bincount(comp)[comp]
        weights, rec_gain = {}, []
        fieldres, flags = {}, {'F1': [], 'F3': [], 'F2': False}
        sums = {k: 0. for k in ('lineup', 'model', 'majority', 'refit', 'lineup_rw', 'majority_rw')}
        from sklearn.metrics import f1_score, roc_auc_score          # scikit-learn comes from the model-study site-packages the copies add
        with c.vi.threadpool_limits(limits=2):
            for t in universe:
                t0 = time.time()
                rows = hold[t]
                y = truth(ctx, t)
                yy = y[rows]
                nc = len(codes_of(ctx, t))
                chosen = sel['chosen'][t]
                sf = sel['fields'][t]
                data = ctx.frame.iloc[rows]
                p_line = candidate_probs(c, ctx, t, chosen, data, rows)
                p_refit = p_line if chosen == 'R' else candidate_probs(c, ctx, t, 'R', data, rows)
                majority = int(np.bincount(y[(split == 0) & (y >= 0)], minlength=nc).argmax())
                dec = {'lineup': fill_of(p_line, sf), 'model': p_line.argmax(axis=1), 'refit': p_refit.argmax(axis=1),
                       'majority': np.full(len(rows), majority)}
                correct = {k: (v == yy).astype(float) for k, v in dec.items()}
                acc = {k: float(v.mean()) for k, v in correct.items()}
                w = int((y < 0).sum())
                weights[t] = w
                rec_gain.append({'rows': rows, 'weight': w, 'correct': correct['lineup'], 'basecorrect': correct['majority']})
                iv_gain = c.ca.clustered_intervals([rec_gain[-1]], groups)
                binary = ctx.results[t]['field']['task'] == 'binary_nonzero'
                assert not binary or nc == 2, t
                auc = float(roc_auc_score(yy, p_line[:, 1])) if binary and len(np.unique(yy)) == 2 else None     # model's probability of the nonzero class
                macro_f1 = float(f1_score(yy, dec['lineup'], average='macro')) if t in NOMINAL else None            # of the fill, over the classes present
                # blank-mix reweighting: holdout rows weighted to the field's blank-row mix of observed household others x component size
                real, _, names = load_block(D, t)
                ix = names.index('hh_n_obs')
                blank = np.flatnonzero(y < 0)
                cells_b = c.hm.bucket_cells(real[blank, ix], comp_size[blank])
                cells_h = c.hm.bucket_cells(real[rows, ix], comp_size[rows])
                rw = {k: c.hm.reweight(cells_b, cells_h, correct[k]) for k in ('lineup', 'model', 'majority', 'refit')}
                # stress frame on the holdout's own 5,000-row stress sample (comparison with Colorado's stress line)
                _, cohort = c.vr.cohorts(y, split, half)
                srows = cohort['stress']
                assert np.isin(srows, rows).all()
                sdata = c.ev.evaluation_data(ctx.frame, y, ctx.splits, ctx.extra, ctx.raw, srows, 'stress')
                sdec = fill_of(candidate_probs(c, ctx, t, chosen, sdata, srows), sf)
                stress = {'rows': int(len(srows)), 'accuracy': float((sdec == y[srows]).mean()),
                          'majority_accuracy': float((y[srows] == majority).mean())}
                sel_b = sf['accuracy']['M' if sf['fill'] == 'majority' else chosen]['B']
                f1 = acc['lineup'] < acc['majority'] - F1_PP / 100
                f3 = abs(acc['lineup'] - sel_b) * 100 > F3_PP
                if f1:
                    flags['F1'].append(t)
                if f3:
                    flags['F3'].append(t)
                fieldres[t] = {
                    'chosen': chosen, 'fill': sf['fill'], 'rows': int(len(rows)), 'blank_cells': w, 'majority_code': codes_of(ctx, t)[majority],
                    'accuracy': acc, 'gain_vs_majority_pp': 100 * (acc['lineup'] - acc['majority']),
                    'model_gain_vs_majority_pp': 100 * (acc['model'] - acc['majority']), 'auc': auc, 'macro_f1': macro_f1,
                    'gain_95_interval_chosen_minus_majority': iv_gain['gain_95_interval'],
                    'accuracy_95_interval_chosen': iv_gain['accuracy_95_interval'],
                    'blank_mix_reweighted': {k: {kk: v[kk] for kk in ('unweighted', 'reweighted', 'unsupported_blank_share')}
                                             for k, v in rw.items()},
                    'blank_mix_cells_chosen': rw['lineup']['cells'], 'per_class_chosen': per_class(yy, dec['lineup'], nc),
                    'stress': stress, 'dev_B_accuracy_chosen': sel_b, 'flags': {'F1': bool(f1), 'F3': bool(f3)}}
                for k in ('lineup', 'model', 'majority', 'refit'):
                    sums[k] += w * acc[k]
                sums['lineup_rw'] += w * rw['lineup']['reweighted']
                sums['majority_rw'] += w * rw['majority']['reweighted']
                log(f'{t}: chosen {chosen}; holdout acc {100 * acc["lineup"]:.2f}% vs majority {100 * acc["majority"]:.2f}%, refit {100 * acc["refit"]:.2f}%, '
                    f'model {100 * acc["model"]:.2f}% ({sf["fill"]} fill); AUC {auc if auc is None else round(auc, 4)} macro-F1 {macro_f1 if macro_f1 is None else round(macro_f1, 4)}; reweighted {100 * rw["lineup"]["reweighted"]:.2f}%; {time.time() - t0:.0f}s')
                del data, p_line, p_refit, sdata
        total = sum(weights.values())
        overall = {k: sums[k] / total for k in sums}
        iv_all = c.ca.clustered_intervals(rec_gain, groups)
        flags['F2'] = bool(overall['lineup'] < overall['majority'])
        groups_of = {}
        for t in universe:
            groups_of.setdefault(ctx.results[t]['field']['group'], []).append(t)
        by_group = {g: {k: sum(weights[t] * fieldres[t]['accuracy'][k] for t in ts) / sum(weights[t] for t in ts)
                        for k in ('lineup', 'model', 'majority', 'refit')} for g, ts in groups_of.items()}
        result = {'district': district, 'fields_scored': len(universe), 'blank_cells_total': total,
                  'holdout_receipt_sha256': sha256_file(D / 'work' / 'holdout-receipt.json'),
                  'selection_lock_sha256': sha256_file(D / 'work' / 'selection-lock.json'),
                  'overall_blank_weighted': {**overall, 'accuracy_95_interval_chosen': iv_all['accuracy_95_interval'],
                                             'gain_95_interval_chosen_minus_majority': iv_all['gain_95_interval'],
                                             'interval_method': iv_all['method']},
                  'by_group_blank_weighted': by_group, 'fields': fieldres, 'flags': flags}
        write_json(out, result)
        write_json(flags_path, {**flags, 'rules': {'F1': 'chosen holdout accuracy more than 0.5 pp below majority: flag, fills stay as locked',
                                                   'F2': 'overall blank-weighted lineup accuracy below overall majority: stop before export',
                                                   'F3': '|holdout - dev B| > 2 pp: flag as possible leakage or shift'}})
        st.note(overall_blank_weighted={k: round(v, 5) for k, v in overall.items()}, flags=flags)
        log(f'overall (blank-weighted) lineup {100 * overall["lineup"]:.3f}% vs majority {100 * overall["majority"]:.3f}%, refit {100 * overall["refit"]:.3f}%, '
            f'model {100 * overall["model"]:.3f}%; F1 {flags["F1"]} F2 {flags["F2"]} F3 {flags["F3"]}')
