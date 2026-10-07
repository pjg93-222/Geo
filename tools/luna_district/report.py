"""P3-17: per-district report (plain words plus the plan's table) and the combined summary.

Aggregates only: counts, shares, hashes, timings. No row value and no identifier appears here.
"""
import csv
import sys
from pathlib import Path

from .common import (BASE_FIELDS, DISTRICTS, HM_FIELDS, R1_FIELDS, TARGETS, Step, district_dir, read_json, write_json)

LABELS = {
    'sci_charitable_donor': 'Charitable donor', 'sci_cpi_auto_index': 'Auto buyer index', 'sci_cpi_cc_index': 'Credit card index',
    'sci_cpi_conservative_index': 'Conservative index', 'sci_cpi_donor_index': 'Donor index',
    'sci_cpi_insurance_auto_index': 'Auto insurance index', 'sci_cpi_insurance_index': 'Insurance index',
    'sci_cpi_internet_buy_index': 'Internet buyer index', 'sci_cpi_liberal_index': 'Liberal index',
    'sci_cpi_online_pay_index': 'Online payment index', 'sci_credit_card_user': 'Credit card user',
    'sci_ct_dnr_contribamt_high': 'High-amount donor', 'sci_ct_political_outlk_mid': 'Political outlook: middle',
    'sci_ct_political_outlk_swconserv': 'Political outlook: somewhat conservative',
    'sci_ct_political_outlk_swliberal': 'Political outlook: somewhat liberal',
    'sci_ct_political_outlk_vconserv': 'Political outlook: very conservative',
    'sci_ct_political_outlk_vliberal': 'Political outlook: very liberal', 'sci_ct_political_partyaff_democrat': 'Party affinity: Democrat',
    'sci_ct_political_partyaff_gop': 'Party affinity: Republican', 'sci_ct_political_partyaff_ind': 'Party affinity: independent',
    'sci_ct_smrtphn_typeowns_iphone': 'iPhone owner', 'sci_ct_volunteer_chtyorg': 'Charity volunteer',
    'sci_number_of_adults_in_household': 'Adults in household', 'sci_number_of_children_in_household': 'Children in household',
    'sci_owner_renter': 'Owner or renter', 'sii_dwelling_type': 'Dwelling type'}
HOW = {'hm_refit': 'Household model', 'legacy_refit': 'Person model (Colorado legacy)',
       'legacy_refit_prior_corrected': 'Person model (Colorado legacy, rare-code corrected)',
       'majority': 'Most common answer (the model was worse on the development checks)'}
# Colorado group lines, no-GPU deployment (CURRENT_MODEL_OVERVIEW.md, "Previous deployment" column; plan section 7)
CO_GROUP = {'Credit Union Alignment': 84.26, 'Lean': 94.04, 'Sphere of Influence': 81.62}
CO_OVERALL = 86.67


def pct(x, d=2):
    return '-' if x is None else f'{100 * x:.{d}f}%'


def co_scores(D):
    """Colorado's split-2 holdout accuracy for the same no-GPU method, from its own validation table."""
    path = D / 'frozen-co' / 'individual_model_validation.csv'
    rows = {}
    with open(path, newline='', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            if r['phase'] == 'holdout':
                rows[r['field']] = r
    out = {}
    for t in TARGETS:
        key = 'legacy_accuracy' if t in BASE_FIELDS else 'previous_deployed_accuracy'
        out[t] = float(rows[t][key])
    return out, {t: float(rows[t]['previous_deployed_accuracy']) for t in TARGETS}, {t: rows[t]['group'] for t in TARGETS}


def runtime_table(D):
    rows = []
    for p in sorted((D / 'receipts').glob('*.json')):
        if p.stem.count('.'):
            continue
        r = read_json(p)
        rows.append((r['step'], r['status'], r['wall_seconds'], r['peak_working_set_gb']))
    return rows


def write_reports(district, redo=False, **_):
    cfg = DISTRICTS[district]
    D = district_dir(district)
    out_md, out_json = D / 'REPORT.md', D / 'report-data.json'
    with Step(district, 'report', outputs=[out_md, out_json], redo=redo, check_originals=False) as st:
        sel = read_json(D / 'work' / 'selection.json')
        res = read_json(D / 'work' / 'holdout-results.json')
        ver = read_json(D / f'data-completion-{cfg["dir"]}-luna-v1' / 'verification.json')
        ver2 = read_json(D / f'data-completion-{cfg["dir"]}-luna-v1' / 'independent_verification.json')
        co_same, co_prev, co_group = co_scores(D)
        flags = res['flags']
        total_blank = res['blank_cells_total']
        lines = []
        add = lines.append
        add(f'# Terrier Phase 3 imputation report: {cfg["label"]}')
        add('')
        add('Aggregates only. No row, address, name or identifier appears in this file.')
        add('')
        add(f'## How often a filled-in value is right: {cfg["label"]}')
        add('')
        add("We hid the real answers for about one in five people whose answers we know, filled them in with the same kinds of Random Forest, KNN and "
            "household models Colorado uses, trained on this district's own people only (no Colorado-trained model is used), and counted how often the fill "
            'matched. "Most common answer" is what you would get by always guessing the district\'s most common value; a field whose model did worse than '
            'that on the development checks is filled with it. AUC is the area under the ROC curve of the model\'s probability for binary fields; macro-F1 '
            'is the unweighted mean F1 over the classes of the four multi-class fields. "Expected on the real blanks" adjusts for the fact that people with a '
            'blank have fewer known household members to learn from. The GPU step Colorado added last was skipped (about 0.1 point of accuracy).')
        add('')
        add('| Measured value | Blank in file | How it is filled | People checked | Right: our fill | Right: most common answer | Lift (points) | AUC / macro-F1 | Colorado on its own data | Expected on the real blanks | Shown as "file default"? |')
        add('|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---|')
        data = {}
        for t in TARGETS:
            f = res['fields'][t]
            chosen = sel['chosen'][t]
            from .export import method_of
            method = method_of(t, chosen, f['fill'])
            marked = sel['fields'][t]['majority_default_applies']
            how = HOW[method] + (', fills equal to the most common answer shown as file default' if marked and f['fill'] != 'majority' else '')
            quality = f'AUC {f["auc"]:.3f}' if f['auc'] is not None else (f'macro-F1 {f["macro_f1"]:.3f}' if f['macro_f1'] is not None else '-')
            b = f['blank_cells']
            rw = f['blank_mix_reweighted']['lineup']['reweighted']
            share = b / cfg['rows']
            add(f'| {LABELS[t]} | {b:,} ({100 * share:.1f}%) | {how} | {f["rows"]:,} | {pct(f["accuracy"]["lineup"])} | {pct(f["accuracy"]["majority"])} | '
                f'{f["gain_vs_majority_pp"]:+.2f} | {quality} | {pct(co_same[t])} | {pct(rw)} | {"Yes (every fill)" if f["fill"] == "majority" else ("Yes (only fills equal to the most common answer)" if marked else "No")} |')
            data[t] = {'label': LABELS[t], 'method': method, 'chosen': chosen, 'blank': b, 'people_checked': f['rows'],
                       'accuracy': f['accuracy'], 'colorado_on_its_own_data': co_same[t], 'reweighted': rw,
                       'majority_default': marked, 'fill': f['fill'], 'gain_vs_majority_pp': f['gain_vs_majority_pp'],
                       'auc': f['auc'], 'macro_f1': f['macro_f1']}
        o = res['overall_blank_weighted']
        add(f'| **All filled values** | {total_blank:,} | | {sum(v["rows"] for v in res["fields"].values()):,} | **{pct(o["lineup"])}** | {pct(o["majority"])} | '
            f'{100 * (o["lineup"] - o["majority"]):+.2f} | | {CO_OVERALL:.2f}% (before GPU) | **{pct(o["lineup_rw"])}** | |')
        for g in ('Credit Union Alignment', 'Lean', 'Sphere of Influence'):
            gname = {'Credit Union Alignment': 'Credit Union alignment fields', 'Lean': 'Partisan fields', 'Sphere of Influence': 'Sphere of influence fields'}[g]
            gr = res['by_group_blank_weighted'].get(g)
            if gr:
                add(f'| {gname} | | | | {pct(gr["lineup"])} | {pct(gr["majority"])} | {100 * (gr["lineup"] - gr["majority"]):+.2f} | | {CO_GROUP[g]:.2f}% | | |')
        add('')
        gi = o['gain_95_interval_chosen_minus_majority']
        add(f'95% interval for the overall gain of our fill over the most common answer: {100 * gi[0]:+.2f} to {100 * gi[1]:+.2f} points '
            f'(1000 paired household/address component bootstrap draws; conditional on the scored rows).')
        add('')
        add("Footnotes: Colorado's checked group was used twice, so compare direction, not decimals. Democrat and dwelling: the Colorado column shows its "
            "legacy person model (94.96% and 86.75%); Colorado's own pre-GPU fill (household copy) scored 95.45% and 92.88%. Actual correctness of the original "
            'blanks stays unknown; nothing here is "validated" or "confirmed".')
        add('')
        add('## In plain words')
        add('')
        ahead = [t for t in TARGETS if res['fields'][t]['gain_vs_majority_pp'] > 0]
        level = [t for t in TARGETS if abs(res['fields'][t]['gain_vs_majority_pp']) < 0.5]
        add(f'- Our fill beats the "most common answer" guess overall: {pct(o["lineup"])} vs {pct(o["majority"])} (blank-weighted over {total_blank:,} filled values). '
            f'Fields ahead of the most common answer: {len(ahead)} of 26; fields within 0.5 point of it: {len(level)}.')
        replaced = [t for t in TARGETS if res['fields'][t]['fill'] == 'majority']
        add(f'- Fields where the model was below the most common answer on every development check, so every blank got the most common answer: {len(replaced)} of 26.')
        counts = {t: ver['status_counts'][t] for t in TARGETS}
        tot = {s: sum(counts[t][s] for t in TARGETS) for s in ('estimated_household_model', 'estimated_accuracy_argmax', 'estimated_majority_default')}
        add(f'- Values filled, by status: household model {tot["estimated_household_model"]:,}; person model (accuracy argmax) {tot["estimated_accuracy_argmax"]:,}; '
            f'shown as file default (majority default) {tot["estimated_majority_default"]:,}; total {sum(tot.values()):,} '
            f'(census {cfg["completed_values"]:,}).')
        add(f'- Flags fixed before scoring: F1 (a field more than 0.5 point below the most common answer) {flags["F1"] or "none"}; '
            f'F2 (overall below the overall most common answer) {"TRIGGERED" if flags["F2"] else "not triggered"}; '
            f'F3 (holdout differs from development B by more than 2 points) {flags["F3"] or "none"}.')
        add('')
        add('## Method used per field, development accuracy and holdout (percent)')
        add('')
        add('| Field | Method | Model | Dev A1 | Dev A2 | Dev B | Holdout fill | Holdout model | Holdout most common | Fill - most common (pp) | 95% interval (pp) |')
        add('|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|')
        for t in TARGETS:
            s = sel['fields'][t]
            f = res['fields'][t]
            ch = s['chosen']
            dv = s['accuracy'][ch]
            lo, hi = f['gain_95_interval_chosen_minus_majority']
            add(f'| {LABELS[t]} | {HOW[data[t]["method"]]} | {"R (frozen method)" if ch == "R" else "H (household model)"} | '
                f'{pct(dv["A1"])} | {pct(dv["A2"])} | {pct(dv["B"])} | {pct(f["accuracy"]["lineup"])} | {pct(f["accuracy"]["model"])} | '
                f'{pct(f["accuracy"]["majority"])} | {f["gain_vs_majority_pp"]:+.2f} | {100 * lo:+.2f} to {100 * hi:+.2f} |')
        add('')
        add('## Values filled by status, per field')
        add('')
        add('| Field | Household model | Person model (argmax) | File default (majority) | Total |')
        add('|---|---:|---:|---:|---:|')
        for t in TARGETS:
            c = counts[t]
            add(f'| {LABELS[t]} | {c["estimated_household_model"]:,} | {c["estimated_accuracy_argmax"]:,} | {c["estimated_majority_default"]:,} | {c["blank"] if "blank" in c else sum(c.values()):,} |')
        add('')
        add('## Other checks')
        add('')
        add(f'- Negative control (charitable donor, legacy model refit on permuted training labels): ' +
            ', '.join(f'{k} {v["gap_pp"]:+.2f} pp from majority' for k, v in sel['negative_control'].items()) + ' (rule: within 1.0 pp).')
        add(f'- Independent rescoring of 3,000 random blank cells per field from features rebuilt out of the CSV: argmax agreed on {ver2["independent_rescoring"]["argmax_agreeing_rows"]:,} of {ver2["independent_rescoring"]["rows"]:,}; '
            f'largest probability difference {ver2["independent_rescoring"]["max_probability_difference_vs_saved"]:.2e}.')
        add(f'- Export: {ver["rows"]:,} rows x {ver["columns"]} columns (Colorado\'s layout); 622 source columns equal the source CSV; precision-flag columns and model_audited_tract are blank (rulings R-7, R-10).')
        add('')
        add('## Runtime and peak memory per step')
        add('')
        add('| Step | Status | Wall seconds | Peak working set (GB) |')
        add('|---|---|---:|---:|')
        for step, status, wall, peak in runtime_table(D):
            add(f'| {step} | {status} | {wall:,.0f} | {peak:.2f} |')
        add('')
        out_md.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        write_json(out_json, {'district': district, 'overall': o, 'flags': flags, 'fields': data, 'status_totals': tot,
                              'groups': res['by_group_blank_weighted'], 'runtime': runtime_table(D)})
        st.note(report=str(out_md))


def write_combined(path):
    lines = ['# Phase 3 results: imputation for OR-05 and CT-01', '', 'Aggregates only. Per-district reports sit in each district folder (REPORT.md).', '']
    for district in ('or-05', 'ct-01'):
        d = district_dir(district) / 'report-data.json'
        if not d.exists():
            lines += [f'## {DISTRICTS[district]["label"]}', '', 'Not finished.', '']
            continue
        r = read_json(d)
        o = r['overall']
        lines += [f'## {DISTRICTS[district]["label"]}', '',
                  f'- Overall (blank-weighted) right: our fill {pct(o["lineup"])}; most common answer {pct(o["majority"])}; model alone {pct(o["model"])}; '
                  f'expected on the real blanks {pct(o["lineup_rw"])}.',
                  f'- Flags: F1 {r["flags"]["F1"] or "none"}; F2 {r["flags"]["F2"]}; F3 {r["flags"]["F3"] or "none"}.',
                  f'- Status totals: {r["status_totals"]}.', '']
    Path(path).write_text('\n'.join(lines) + '\n', encoding='utf-8')


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[1] == 'combined':
        write_combined(sys.argv[2])
