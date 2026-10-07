"""P3-12: choose the filling method per field on development folds only (A1, A2, B), apply the majority rules, and lock.

Rules, fixed before any data was read (plan sections 6.2-6.5, lead rulings R-1 and R-8, Paul's majority rules of 2026-10-07):
  R  frozen method of plan table 1.2 trained on the district (household model for 17 fields, legacy fill for 9)
  H  district household-model fit, only for democrat and dwelling (R-1)
  M  majority code (baseline, and the fill itself where the model is worse than it)
Keep R unless H passes fixes.promote_on_development against R (A2 gain >= 0.001 with a positive paired 80% component-bootstrap
lower bound, A1 gain >= 0.001) and has a positive B gain. Then, for the chosen model's lift over M on A1, A2 and B, every field alike:
  below M on every check          every blank gets the majority code, status estimated_majority_default (fill 'majority')
  below 0.5 pp on every check     model fill kept; fills equal to the majority code are relabelled estimated_majority_default
  otherwise                       model fill, status estimated_household_model or estimated_accuracy_argmax
No Colorado-trained model is used anywhere: every candidate is a model trained on this district's own data.
"""
import numpy as np

from . import copies
from .common import DISTRICTS, DWELLING, HM_FIELDS, R1_FIELDS, TARGETS, TER, Step, district_dir, read_json, sha256_file, utc_now, write_json
from .models import DEV_SETS, codes_of, load_context, truth

LIFT_BAR_PP = 0.5
PROMOTE_B_MIN = 0.0


def passes(a1, a2, b_gain, promote):
    """H beats R on development: the existing gate plus a positive B sign check."""
    return bool(promote(a2, a1) and b_gain > PROMOTE_B_MIN)


def pick(passing, mean_gain):
    """Among passing challengers choose the larger mean A1/A2 gain; ties go to H (district household model) over any other."""
    if not passing:
        return 'R'
    return sorted(passing, key=lambda k: (-mean_gain[k], 0 if k == 'H' else 1))[0]


def lift_rule(lifts_pp):
    """No lift when the lift is below the bar on every development check."""
    return bool(all(v < LIFT_BAR_PP for v in lifts_pp.values()))


def worse_rule(lifts_pp):
    """The model is below the majority code on every development check."""
    return bool(all(v < 0 for v in lifts_pp.values()))


def fill_of(p, field):
    """The hard fill of probabilities `p`: the model's argmax (ties to the first code), or the majority code where the rule replaced the model."""
    if field['fill'] == 'majority':
        return np.full(len(p), field['codes'].index(field['majority_code']))
    return p.argmax(axis=1)


def select_and_lock(district, redo=False, fields=None, **_):
    D = district_dir(district)
    universe = list(fields) if fields else list(TARGETS)
    if fields:
        assert DISTRICTS[district]['is_co'], 'a field subset is only for the Colorado replay dry run'
    out_sel, out_lock = D / 'work' / 'selection.json', D / 'work' / 'selection-lock.json'
    c = copies.load(district)
    with Step(district, 'select', outputs=[out_sel, out_lock], redo=redo) as st:
        ctx = load_context(c, D, frame=False)
        ddir = D / 'work' / 'dev'
        assert read_json(D / 'work' / 'control.json')['sets'], 'the negative control must exist before selection is locked'
        split = ctx.splits['split']
        groups = ctx.splits['groups']
        record = {}
        for t in universe:
            y = truth(ctx, t)
            nc = len(codes_of(ctx, t))
            leg = np.load(ddir / f'legacy-{t}.npz')
            hm = np.load(ddir / f'hm-{t}.npz') if t in HM_FIELDS + R1_FIELDS else None
            majority = int(np.bincount(y[(split == 0) & (y >= 0)], minlength=nc).argmax())
            dec = {s: {} for s in DEV_SETS}
            for s in DEV_SETS:
                rows = leg[f'{s}_rows']
                assert np.array_equal(rows, ctx.dev[f'{t}__{s}'])
                if t in HM_FIELDS:
                    dec[s]['R'] = hm[f'{s}_T'].argmax(axis=1)
                else:
                    dec[s]['R'] = (leg[f'{s}_prior'] if t == DWELLING else leg[f'{s}_plain']).argmax(axis=1)
                    if t in R1_FIELDS:
                        dec[s]['H'] = hm[f'{s}_T'].argmax(axis=1)
                dec[s]['M'] = np.full(len(rows), majority)
            acc = {k: {s: float((dec[s][k] == y[leg[f'{s}_rows']]).mean()) for s in DEV_SETS} for k in dec['A1']}
            gains = {}
            if 'H' in dec['A1']:
                gains['H'] = {s: c.tk.gain_interval(y[leg[f'{s}_rows']], dec[s]['H'], dec[s]['R'], groups[leg[f'{s}_rows']], seed=c.rv.SEED)
                              for s in DEV_SETS}
            passing, mean_gain = [], {}
            for k, g in gains.items():
                mean_gain[k] = (g['A1']['gain'] + g['A2']['gain']) / 2
                if passes(g['A1'], g['A2'], g['B']['gain'], c.fx.promote_on_development):
                    passing.append(k)
            chosen = pick(passing, mean_gain)
            lifts = {s: 100 * (acc[chosen][s] - acc['M'][s]) for s in DEV_SETS}
            record[t] = {'group': 'household_model' if t in HM_FIELDS else 'base_fill', 'codes': codes_of(ctx, t),
                         'majority_code': codes_of(ctx, t)[majority], 'accuracy': acc, 'gains_vs_R': gains,
                         'passing_challengers': passing, 'mean_gain_A1_A2': mean_gain, 'chosen': chosen,
                         'lift_over_majority_pp': lifts, 'no_lift_on_all_checks': lift_rule(lifts),
                         'fill': 'majority' if worse_rule(lifts) else 'model',
                         'majority_default_applies': lift_rule(lifts)}
        control = read_json(D / 'work' / 'control.json')
        sel = {'created_utc': utc_now(), 'district': district, 'rules': __doc__, 'fields': record,
               'chosen': {t: record[t]['chosen'] for t in universe}, 'negative_control': control['sets'],
               'majority_default_fields': [t for t in universe if record[t]['majority_default_applies']],
               'majority_fill_fields': [t for t in universe if record[t]['fill'] == 'majority']}
        write_json(out_sel, sel)
        code = {p.name: sha256_file(p) for p in sorted((TER / 'tools' / 'luna_district').glob('*.py'))}
        dev_hashes = {p.name: sha256_file(p) for p in sorted(ddir.glob('*.npz'))}
        model_hashes = {p.name: sha256_file(p) for p in sorted((D / 'work' / 'models').glob('*.joblib'))}
        write_json(out_lock, {'created_utc': utc_now(), 'selection_sha256': sha256_file(out_sel),
                              'holdout_receipt_sha256': sha256_file(D / 'work' / 'holdout-receipt.json'),
                              'holdout_npz_sha256': sha256_file(D / 'work' / 'holdout.npz'),
                              'copy_manifest_sha256': sha256_file(D / 'copy-manifest.json'),
                              'driver_code_sha256': code, 'dev_prediction_sha256': dev_hashes, 'model_sha256': model_hashes,
                              'statement': 'Nothing upstream changes after this lock; a later change is a deviation recorded in the report.'})
        st.note(chosen_counts={k: sum(1 for v in sel['chosen'].values() if v == k) for k in ('R', 'H')},
                majority_default_fields=sel['majority_default_fields'], majority_fill_fields=sel['majority_fill_fields'],
                challengers_that_passed={t: record[t]['passing_challengers'] for t in universe if record[t]['passing_challengers']})
