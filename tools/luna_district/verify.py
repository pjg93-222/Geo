"""P3-16: verify the export a second way.

Pass A streams the exported CSV and the source CSV in lockstep and checks the structure and the observed/blank pattern.
Pass B rebuilds the feature rows of 3,000 random blank cells per field straight from the source CSV (typing rules of
search_rare.prepare, engineer, stances), never from features.pkl, loads the saved models and recomputes the probabilities.
"""
import re

import numpy as np

from . import copies
from .common import DISTRICTS, HM_FIELDS, R1_FIELDS, TARGETS, Step, district_dir, read_json, write_json
from .export import STATUS, co_header, expected_blank
from .models import candidate_probs, codes_of, load_block, load_context, truth
from .select import fill_of


def sample_rows(masks, t, index, n=3000):
    blank = np.flatnonzero(~masks[t])
    rng = np.random.default_rng(20261201 + index)
    return np.sort(rng.choice(blank, min(n, len(blank)), replace=False))


def rebuild_frame(c, D, rows, source_csv, group_sizes):
    """Feature rows rebuilt from the CSV itself."""
    import pandas as pd
    from .inputs import numeric_columns
    cols, numeric = numeric_columns(c, D)
    numeric = set(numeric)
    parts, offset = [], 0
    for chunk in pd.read_csv(source_csv, encoding='cp1252', usecols=cols, dtype=str, keep_default_na=False, na_filter=False, chunksize=50000):
        lo, hi = np.searchsorted(rows, [offset, offset + len(chunk)])
        pick = rows[lo:hi] - offset
        offset += len(chunk)
        if len(pick):
            part = chunk.iloc[pick].copy()
            for col in cols:
                part[col] = part[col].str.strip()
                if col in numeric:
                    part[col] = pd.to_numeric(part[col].replace('', np.nan), errors='coerce').astype(np.float32)
            parts.append(part)
    raw = pd.concat(parts, ignore_index=True)
    assert len(raw) == len(rows)
    frame, _ = c.sr.engineer(raw, group_sizes[rows])
    st_parts, offset = [], 0
    for chunk in pd.read_csv(source_csv, encoding='cp1252', usecols=c.ci.STANCES, dtype=str, keep_default_na=False, na_filter=False, chunksize=50000):
        lo, hi = np.searchsorted(rows, [offset, offset + len(chunk)])
        pick = rows[lo:hi] - offset
        offset += len(chunk)
        if len(pick):
            st_parts.append(chunk.iloc[pick].apply(lambda s: pd.to_numeric(s.str.strip().replace('', np.nan), errors='raise')))
    stances = pd.concat(st_parts, ignore_index=True)
    c.ci.add_stances(frame, stances)
    return frame


def verify_export(district, redo=False, **_):
    import pandas as pd
    cfg = DISTRICTS[district]
    D = district_dir(district)
    E = D / f'data-completion-{cfg["dir"]}-luna-v1'
    out = E / 'independent_verification.json'
    c = copies.load(district)
    with Step(district, 'verify', outputs=[out], heavy=True, redo=redo) as st:
        from . import originals
        sel = read_json(D / 'work' / 'selection.json')
        masks = np.load(D / 'work' / 'observed-masks.npz')
        masks = {t: masks[t] for t in TARGETS}
        ctx = load_context(c, D, frame=False)
        header = co_header()
        src_cols = header[:622]
        applied = {t: np.load(D / 'work' / 'applied' / f'applied-{t}.npz') for t in TARGETS}
        samples = {t: sample_rows(masks, t, i) for i, t in enumerate(TARGETS)}
        export_vals = {t: {} for t in TARGETS}
        counts = {t: {'blank': 0, 'observed': 0, **{s: 0 for s in STATUS}} for t in TARGETS}
        derived_cols = header[622:]
        flag_cols = [n for n in derived_cols if re.search(r'__flag_p(80|90|95)$', n)]
        problems = []
        reader = pd.read_csv(E / 'synthetic_completed.csv.gz', encoding='utf-8', dtype=str, keep_default_na=False, na_filter=False, chunksize=10000)
        source = pd.read_csv(cfg['source_csv'], encoding='cp1252', dtype=str, keep_default_na=False, na_filter=False, chunksize=10000)
        offset = 0
        for exp, src in zip(reader, source):
            exp, src = exp.reset_index(drop=True), src.reset_index(drop=True)
            assert list(exp.columns) == header, "column names or order differ from Colorado's 851"
            if not exp[src_cols].equals(src):
                problems.append(f'source columns differ in rows {offset}-{offset + len(src)}')
            n = len(src)
            end = offset + n
            assert (exp['model_source_row'].astype(int).to_numpy() == np.arange(offset + 1, end + 1)).all()
            assert (exp['model_audited_tract'] == '').all()
            for col in flag_cols:
                assert (exp[col] == '').all(), col
            for t in TARGETS:
                blank = src[t].str.strip().eq('').to_numpy()
                status = exp[t + '__status'].to_numpy(dtype=object)
                assert (status[~blank] == 'observed').all(), f'{t}: observed rows must be observed'
                assert np.isin(status[blank], STATUS).all(), f'{t}: status outside the allowed set'
                counts[t]['blank'] += int(blank.sum())
                counts[t]['observed'] += int((~blank).sum())
                for s in STATUS:
                    counts[t][s] += int((status[blank] == s).sum())
                comp_col = [x for x in derived_cols if x in (t + '__completed', t + '__completed_nonzero')][0]
                comp = exp[comp_col].to_numpy(dtype=object)
                assert (comp != '').all(), f'{t}: a completed value is blank'
                for col in [x for x in derived_cols if x.startswith(t + '__') and ('__p_' in x or '__completion_p_' in x or x.endswith('__estimate_probability'))]:
                    vals = exp[col].to_numpy(dtype=object)
                    assert (vals[~blank] == '').all(), f'{col}: observed rows carry a derived value'
                    assert (vals[blank] != '').all(), f'{col}: a blank row has no value'
                lo, hi = np.searchsorted(samples[t], [offset, end])
                pos = samples[t][lo:hi] - offset
                if len(pos):
                    ev = {'completed': comp[pos], 'estimate': exp[t + '__estimate_probability'].to_numpy(dtype=object)[pos].astype(float)}
                    for k, v in ev.items():
                        export_vals[t].setdefault(k, []).append(v)
            offset = end
        assert offset == cfg['rows']
        total = 0
        for t in TARGETS:
            assert counts[t]['blank'] == int((~masks[t]).sum()) == expected_blank(district, t)
            total += counts[t]['blank']
            for k, s in enumerate(STATUS):
                assert counts[t][s] == int((applied[t]['status_code'] == k).sum()), f'{t}: export status counts differ from applied'
        assert total == cfg['completed_values']
        union = np.unique(np.concatenate(list(samples.values())))
        groups = ctx.splits['groups']
        frame = rebuild_frame(c, D, union, cfg['source_csv'], np.bincount(groups)[groups])
        rescore = {}
        worst, argmax_ok, argmax_n, worst_export = 0., 0, 0, 0.
        with c.vi.threadpool_limits(limits=2):
            for t in TARGETS:
                rows = samples[t]
                data = frame.iloc[np.searchsorted(union, rows)]
                p = candidate_probs(c, ctx, t, sel['chosen'][t], data, rows)
                ap = applied[t]
                at = np.searchsorted(ap['rows'], rows)
                assert np.array_equal(ap['rows'][at], rows)
                diff = float(np.abs(p - ap['probabilities'][at]).max())
                fill = fill_of(p, sel['fields'][t])                       # model argmax, or the majority code where the rule replaced the model
                same_arg = int((fill == ap['predicted'][at]).sum())
                codes = np.asarray(codes_of(ctx, t), dtype=object)
                same_export = int((codes[fill] == np.concatenate(export_vals[t]['completed'])).sum())
                top_exp = float(np.abs(p[np.arange(len(p)), fill] - np.concatenate(export_vals[t]['estimate'])).max())
                rescore[t] = {'rows': int(len(rows)), 'max_prob_diff_vs_saved': diff, 'argmax_equal_saved': same_arg,
                              'argmax_equal_export': same_export, 'max_top_prob_diff_vs_export': top_exp}
                worst, worst_export = max(worst, diff), max(worst_export, top_exp)
                argmax_ok += same_arg
                argmax_n += len(rows)
                assert diff <= 1e-6 and same_arg == len(rows) and same_export == len(rows), (t, rescore[t])
                assert top_exp <= 1e-6 + 5.1e-8, (t, rescore[t])
        H = c.hm.household_ids(cfg['source_sha256'])
        G = c.hc.households(ctx.splits)
        block_ok = {}
        rng = np.random.default_rng(20261202)
        for t in HM_FIELDS + R1_FIELDS:
            y = truth(ctx, t)
            nc = len(codes_of(ctx, t))
            binary = ctx.results[t]['field']['task'] == 'binary_nonzero'
            real = load_block(D, t)[0]
            pick = rng.choice(np.flatnonzero(y < 0), 200, replace=False)
            block_ok[t] = all(np.array_equal(c.hm.reference_row(y, H, G, nc, binary, int(i)), real[i]) for i in pick)
            assert block_ok[t], f'{t}: household block differs from the literal definition'
        changes = originals.check_stat(strict=False)
        assert not changes, f'Luna originals changed: {changes[:5]}'
        result = {'passed': not problems, 'problems': problems, 'rows': offset, 'columns': len(header), 'blank_cells_filled': total,
                  'status_counts': {t: {s: counts[t][s] for s in STATUS} for t in TARGETS},
                  'checks': ['622 source columns equal the source CSV', 'observed cells unchanged and carry no derived values',
                             'every blank target cell filled exactly once; total equals the census', 'statuses within the four allowed',
                             'per-field status counts equal applied arrays', "column names and order equal Colorado's 851",
                             'originals unchanged'],
                  'independent_rescoring': {'rows_per_field': 3000, 'seed_base': 20261201,
                                            'features': 'rebuilt from the source CSV (typing, engineer, stances), not read from features.pkl',
                                            'max_probability_difference_vs_saved': worst, 'max_top_probability_difference_vs_export': worst_export,
                                            'argmax_agreeing_rows': argmax_ok, 'rows': argmax_n, 'per_field': rescore},
                  'household_block_literal_check': {'fields': len(block_ok), 'rows_per_field': 200, 'all_equal': all(block_ok.values())}}
        write_json(out, result)
        assert not problems, problems
        st.note(passed=True, blank_cells_filled=total, max_probability_difference=worst, argmax_agreeing=f'{argmax_ok}/{argmax_n}')
