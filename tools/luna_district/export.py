"""P3-14 apply the locked lineup to every blank cell, P3-15 write the completion export, P3-16 verify it a second way.

The export has the exact 851-column layout of Colorado's current export (622 source columns, then the derived columns in
Colorado's order). Patterns of Colorado's export that are reproduced: __completed / __completed_nonzero carry the observed
value on observed rows (CPI as 0/1) and the fill on blank rows; __status is 'observed' on observed rows; the estimate,
__p_* and __completion_p_* columns are filled on blank rows only; the precision-flag columns are blank (lead ruling R-7)
and model_audited_tract is blank (R-10, rebuilt after Phase 2). A field whose model is below the majority code on every
development check is filled with the majority code (status estimated_majority_default, method 'majority'); its estimate
and probability columns still hold the model's probabilities.
"""
import gzip
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

import numpy as np

from . import copies
from .common import (CO_EXPORT, DISTRICTS, DWELLING, HM_FIELDS, N_JOBS, PY312, R1_FIELDS, TARGETS, Step, district_dir,
                     log, read_json, sha256_file, utc_now, write_json)
from .models import (CHUNK, bundle_path, candidate_probs, codes_of, load_block, load_context, plain_probs, prior_of, truth)
from .select import fill_of

STATUS = ['estimated_household_model', 'estimated_accuracy_argmax', 'estimated_majority_default']
METHODS = ['hm_refit', 'legacy_refit', 'legacy_refit_prior_corrected', 'majority']
COL = re.compile(r'^(?P<t>.+?)__(?P<k>completed_nonzero|completed|status|estimate_probability|p_(?P<pc>.+)|flag_p(?P<fp>80|90|95)|completion_p_(?P<cc>.+))$')


def method_of(t, chosen, fill='model'):
    if fill == 'majority':
        return 'majority'
    if chosen == 'H' or t in HM_FIELDS:
        return 'hm_refit'
    return 'legacy_refit_prior_corrected' if t == DWELLING else 'legacy_refit'


def status_code(t, chosen, majority_applies, fill, majority_index, majority_fill=False):
    """Status of each blank fill: household model, accuracy argmax, or majority default (Paul 2026-10-07, every field: the whole
    field when the model is below the majority code on every development check, else only the fills equal to the majority code)."""
    code = np.full(len(fill), 0 if (chosen == 'H' or t in HM_FIELDS) else 1, np.int8)
    if majority_fill:
        code[:] = 2
    elif majority_applies:
        code[fill == majority_index] = 2
    return code


def fmt7(a):
    return np.char.mod('%.7f', a).astype(object)


def co_header():
    import pandas as pd
    return list(pd.read_csv(CO_EXPORT / 'synthetic_completed.csv.gz', nrows=0, encoding='utf-8').columns)


def completed_values(chunk_col, binary_cpi):
    """The completed column's value for observed rows (CPI as 0/1, as the original export)."""
    import pandas as pd
    s = chunk_col.str.strip()
    if not binary_cpi:
        return s.to_numpy(dtype=object).copy()
    num = pd.to_numeric(s.replace('', np.nan), errors='raise')
    return np.where(num.isna(), '', (num > 0).astype(int).astype(str)).astype(object)


# ------------------------------------------------------------------ P3-14
def apply_to_blanks(district, shard=0, shards=1, fields=None, redo=False, resume=False, **_):
    D = district_dir(district)
    adir = D / 'work' / 'applied'
    adir.mkdir(parents=True, exist_ok=True)
    universe = [t for i, t in enumerate(TARGETS) if i % shards == shard and (not fields or t in fields)]
    name = 'apply' if shards == 1 and not fields else f'apply-s{shard}of{shards}'
    outputs = [adir / f'applied-{t}.npz' for t in universe]
    c = copies.load(district)
    with Step(district, name, outputs=outputs, heavy=True, redo=redo, resume=resume) as st:
        flags = read_json(D / 'work' / 'holdout-flags.json')
        assert not flags['F2'], 'F2: the lineup is below the overall majority on the holdout; stop before export (run /analyze-issue)'
        sel = read_json(D / 'work' / 'selection.json')
        ctx = load_context(c, D)
        times, counts = {}, {}
        with c.vi.threadpool_limits(limits=2):
            for t in universe:
                if (adir / f'applied-{t}.npz').exists():
                    continue
                t0 = time.time()
                y = truth(ctx, t)
                nc = len(codes_of(ctx, t))
                blank = np.flatnonzero(y < 0)
                chosen = sel['chosen'][t]
                legacy_bundle = joblib_load(D / 'work' / 'models' / f'legacy-{t}.joblib')
                if legacy_bundle['model'] is not None:
                    legacy_bundle['model'].set_params(n_jobs=N_JOBS)
                pfill, pleg = [], []
                for a in range(0, len(blank), CHUNK):
                    rows = blank[a:a + CHUNK]
                    data = ctx.frame.iloc[rows]
                    pfill.append(candidate_probs(c, ctx, t, chosen, data, rows).astype(np.float32))
                    pleg.append(plain_probs(c, legacy_bundle, data, nc).astype(np.float32))
                pfill, pleg = np.concatenate(pfill), np.concatenate(pleg)
                assert np.isfinite(pfill).all() and np.allclose(pfill.sum(axis=1), 1, atol=1e-5), t
                s = sel['fields'][t]
                majority_index = s['codes'].index(s['majority_code'])
                fill = fill_of(pfill, s).astype(np.int16)             # model argmax (ties go to the first code), or the majority code
                scode = status_code(t, chosen, s['majority_default_applies'], fill, majority_index, s['fill'] == 'majority')
                np.savez_compressed(adir / f'applied-{t}.npz.tmp.npz', rows=blank, probabilities=pfill, predicted=fill,
                                    legacy_probabilities=pleg, status_code=scode)
                (adir / f'applied-{t}.npz.tmp.npz').replace(adir / f'applied-{t}.npz')
                counts[t] = {STATUS[k]: int((scode == k).sum()) for k in range(3) if (scode == k).any()}
                times[t] = round(time.time() - t0, 1)
                assert len(blank) == expected_blank(district, t)
                log(f'{t}: {len(blank):,} blank cells filled by {method_of(t, chosen, s["fill"])}; statuses {counts[t]}; {times[t]}s')
        st.note(fields_done=len(times), seconds_per_field=times, status_counts=counts)


def joblib_load(path):
    import joblib
    return joblib.load(path)


def expected_blank(district, t):
    from .common import expected_blanks
    return expected_blanks(district, t)


# ------------------------------------------------------------------ P3-15
def write_export(district, redo=False, **_):
    import pandas as pd
    cfg = DISTRICTS[district]
    assert not cfg['is_co'], 'the export is written for OR-05 and CT-01'
    D = district_dir(district)
    E = D / f'data-completion-{cfg["dir"]}-luna-v1'
    out_csv = E / 'synthetic_completed.csv.gz'
    outputs = [E]
    c = copies.load(district)
    with Step(district, 'export', outputs=outputs, redo=redo) as st:
        assert not read_json(D / 'work' / 'holdout-flags.json')['F2']
        E.mkdir(parents=True)
        sel = read_json(D / 'work' / 'selection.json')
        results = read_json(D / 'work' / 'holdout-results.json')
        ctx = load_context(c, D, frame=False)
        adir = D / 'work' / 'applied'
        applied = {t: dict(np.load(adir / f'applied-{t}.npz')) for t in TARGETS}
        header = co_header()
        assert len(header) == 851
        src_cols = header[:622]
        extra_cols = header[622:]
        field_spec = {}
        for t in TARGETS:
            item = ctx.results[t]
            codes = item['field']['evaluation_codes']
            binary = item['field']['task'] == 'binary_nonzero'
            field_spec[t] = {'codes': np.asarray(codes, dtype=object), 'binary': binary, 'cpi': t.startswith('sci_cpi_')}
        parsed = []
        for name in extra_cols:
            if name in ('model_source_row', 'model_audited_tract'):
                parsed.append((name, None))
                continue
            m = COL.match(name)
            assert m and m['t'] in field_spec, name
            parsed.append((name, m))
        n_completion = sum(1 for _, m in parsed if m is not None and m['cc'] is not None)
        n_flags = sum(1 for _, m in parsed if m is not None and m['fp'] is not None)
        n_p = sum(1 for _, m in parsed if m is not None and m['pc'] is not None)
        assert (n_completion, n_flags, n_p) == (39, 66, 44), (n_completion, n_flags, n_p)
        counts = {t: {'blank': 0, **{s: 0 for s in STATUS}} for t in TARGETS}
        digest = hashlib.sha256()
        offset = 0
        prov = {k: [] for k in ('row', 'field', 'status', 'method', 'model')}
        chosen = sel['chosen']
        t0 = time.time()
        with gzip.open(out_csv, 'wt', encoding='utf-8', newline='', compresslevel=3) as handle:
            for chunk in pd.read_csv(cfg['source_csv'], encoding='cp1252', dtype=str, keep_default_na=False, na_filter=False, chunksize=10000):
                chunk = chunk.reset_index(drop=True)
                assert list(chunk.columns) == src_cols
                digest.update(pd.util.hash_pandas_object(chunk, index=False).to_numpy().tobytes())
                end = offset + len(chunk)
                n = len(chunk)
                derived = {}
                local = {}
                for t in TARGETS:
                    ap = applied[t]
                    lo, hi = np.searchsorted(ap['rows'], [offset, end])
                    pos = ap['rows'][lo:hi] - offset
                    blank_src = chunk[t].str.strip().eq('').to_numpy()
                    assert np.array_equal(np.flatnonzero(blank_src), pos), f'{t}: blank cells differ from the applied rows'
                    fill = ap['predicted'][lo:hi].astype(np.int64)
                    spec = field_spec[t]
                    comp = completed_values(chunk[t], spec['cpi'])
                    comp[pos] = spec['codes'][fill]
                    assert all(v != '' for v in comp)
                    status = np.full(n, 'observed', dtype=object)
                    status[pos] = np.asarray(STATUS, dtype=object)[ap['status_code'][lo:hi]]
                    est = np.full(n, '', dtype=object)
                    pf = ap['probabilities'][lo:hi]
                    est[pos] = fmt7(pf[np.arange(len(pos)), fill])
                    local[t] = (pos, fill, comp, status, est, pf, ap['legacy_probabilities'][lo:hi])
                    counts[t]['blank'] += len(pos)
                    for k in range(3):
                        counts[t][STATUS[k]] += int((ap['status_code'][lo:hi] == k).sum())
                    prov['row'].append((pos + offset + 1).astype(np.int32))
                    prov['field'].append(np.full(len(pos), TARGETS.index(t), np.int8))
                    prov['status'].append(ap['status_code'][lo:hi].astype(np.int8))
                    prov['method'].append(np.full(len(pos), METHODS.index(method_of(t, chosen[t], sel['fields'][t]['fill'])), np.int8))
                    prov['model'].append(np.full(len(pos), TARGETS.index(t), np.int16))
                for name, m in parsed:
                    if m is None:
                        derived[name] = np.arange(offset + 1, end + 1) if name == 'model_source_row' else np.full(n, '', dtype=object)
                        continue
                    t = m['t']
                    pos, fill, comp, status, est, pf, pl = local[t]
                    spec = field_spec[t]
                    kind = m['k']
                    if kind in ('completed', 'completed_nonzero'):
                        assert (kind == 'completed_nonzero') == spec['cpi'], name
                        derived[name] = comp
                    elif kind == 'status':
                        derived[name] = status
                    elif kind == 'estimate_probability':
                        derived[name] = est
                    elif m['fp'] is not None:
                        derived[name] = np.full(n, '', dtype=object)          # R-7: precision flags blank
                    else:
                        code = m['pc'] if m['pc'] is not None else m['cc']
                        k = 1 if code == 'nonzero' else list(spec['codes']).index(code)
                        if code == 'nonzero':
                            assert spec['binary'], name
                        arr = np.full(n, '', dtype=object)
                        arr[pos] = fmt7((pl if m['pc'] is not None else pf)[:, k])
                        derived[name] = arr
                out = pd.concat([chunk, pd.DataFrame(derived)], axis=1)
                assert list(out.columns) == header
                out.to_csv(handle, index=False, header=offset == 0)
                offset = end
                if offset % 100000 == 0:
                    log(f'export written through source row {offset:,} ({time.time() - t0:.0f}s)')
        assert offset == cfg['rows']
        for t in TARGETS:
            assert counts[t]['blank'] == expected_blank(district, t)
        total = sum(v['blank'] for v in counts.values())
        assert total == cfg['completed_values'], (total, cfg['completed_values'])
        # provenance: one row per filled cell
        P = {k: np.concatenate(v) for k, v in prov.items()}
        model_ids, methods_used, registry = {}, {}, {}
        for t in TARGETS:
            kind = chosen[t]
            path = bundle_path(D, t, kind)
            sha = sha256_file(path)
            m = method_of(t, kind, sel['fields'][t]['fill'])
            model_ids[t] = f'{district}/luna-v1/{m}/{t}/{sha[:12]}'
            item = ctx.results[t]
            fr = results['fields'][t]
            registry[t] = {'field': t, 'group': item['field']['group'], 'task': item['field']['task'], 'codes': item['field']['evaluation_codes'],
                           'chosen': kind, 'method': m, 'model_id': model_ids[t], 'model_sha256': sha,
                           'family': item['configuration']['family'], 'settings': item['configuration']['settings'],
                           'features': list(item['selected_features']), 'seeds': {'legacy_forest': 20260939, 'calibrator': 20260941, 'household_forest': 20260948, 'shuffle': 20261047},
                           'status_vocabulary': STATUS, 'blank_cells': counts[t]['blank'], 'status_counts': {s: counts[t][s] for s in STATUS},
                           'development': sel['fields'][t]['accuracy'], 'holdout_accuracy': fr['accuracy'],
                           'majority_code': sel['fields'][t]['majority_code'], 'majority_default_applies': sel['fields'][t]['majority_default_applies'],
                           'precision_flags': 'blank for this district (lead ruling R-7)'}
        P['model'] = np.array([TARGETS.index(t) for t in TARGETS])[P['field']].astype(np.int16)
        np.savez_compressed(E / 'fill_provenance.npz', **P)
        write_json(E / 'fill_provenance_lookup.json', {'field': TARGETS, 'status': STATUS, 'method': METHODS,
                                                       'model': [model_ids[t] for t in TARGETS],
                                                       'row': 'one-based data-row number in the supplied CSV'})
        parquet = 'ok'
        script = ("import numpy as np, pyarrow as pa, pyarrow.parquet as pq, sys\n"
                  "z = np.load(sys.argv[1])\n"
                  "pq.write_table(pa.table({k: z[k] for k in z.files}), sys.argv[2], compression='zstd')\n")
        r = subprocess.run([str(PY312), '-B', '-c', script, str(E / 'fill_provenance.npz'), str(E / 'fill_provenance.parquet')],
                           capture_output=True, text=True)
        if r.returncode != 0:
            parquet = 'failed: ' + r.stderr[-300:]
        else:
            (E / 'fill_provenance.npz').unlink()
        dictionary = build_dictionary(ctx, header, parsed, field_spec, registry)
        write_json(E / 'column_dictionary.json', dictionary)
        write_json(E / 'individual_model_registry.json', registry)
        md = {'rule': 'Per field, on the development checks (A1, A2, B) only: a model below the majority code on every check is replaced by the majority '
                      'code for every blank (status estimated_majority_default, method majority); a model less than 0.5 pp above it on every check keeps '
                      'its fill and only the fills equal to the majority code are relabelled estimated_majority_default (values unchanged); otherwise the '
                      'model fill keeps its model status. Applies to household-model and base-fill fields alike (Paul 2026-10-07).',
              'status': 'estimated_majority_default', 'checks': 'A1, A2, B (district development sets; the holdout plays no part)',
              'fields': {t: {'majority_code': sel['fields'][t]['majority_code'], 'lift_pp': sel['fields'][t]['lift_over_majority_pp'],
                             'fill': sel['fields'][t]['fill']} for t in TARGETS if sel['fields'][t]['majority_default_applies']},
              'fields_not_marked': {t: sel['fields'][t]['lift_over_majority_pp'] for t in TARGETS if not sel['fields'][t]['majority_default_applies']}}
        write_json(E / 'majority_default.json', md)
        shutil_copy(D / 'work' / 'holdout-results.json', E / 'holdout_results.json')
        shutil_copy(D / 'work' / 'selection.json', E / 'selection.json')
        (E / 'README.md').write_text(
            f'Terrier Phase 3 completed export for {cfg["label"]}: the supplied CSV with every blank value of the 26 Luna target fields '
            f'filled by the locked lineup (Colorado\'s Random Forest / KNN / household-model methods, trained on this district only). '
            f'Layout equals Colorado\'s 851-column export. Provenance of every filled cell is in fill_provenance.parquet.\n', encoding='utf-8')
        verification = {'passed': None, 'rows': offset, 'columns': len(header), 'source_columns': 622, 'source_value_hash': digest.hexdigest(),
                        'source_sha256': cfg['source_sha256'], 'file_sha256': sha256_file(out_csv), 'fields': TARGETS,
                        'blank_cells_filled': total, 'status_counts': {t: {s: counts[t][s] for s in STATUS} for t in TARGETS},
                        'status_vocabulary': ['observed'] + STATUS, 'provenance_parquet': parquet,
                        'note': 'written by export; independently re-checked by the verify step'}
        write_json(E / 'verification.json', verification)
        files = {p.name: {'bytes': p.stat().st_size, 'sha256': sha256_file(p)} for p in sorted(E.iterdir()) if p.is_file() and p.name != 'manifest.json'}
        write_json(E / 'manifest.json', {'version': f'terrier_luna_v1_{district}', 'verification': verification, 'files': files})
        st.note(rows=offset, columns=len(header), blank_cells_filled=total, file_sha256=verification['file_sha256'],
                source_value_hash=verification['source_value_hash'], provenance_parquet=parquet,
                status_counts_total={s: sum(counts[t][s] for t in TARGETS) for s in STATUS}, seconds=round(time.time() - t0))


def shutil_copy(a, b):
    import shutil
    shutil.copy2(a, b)


def build_dictionary(ctx, header, parsed, field_spec, registry):
    fields = {}
    for t in TARGETS:
        names = [n for n, m in parsed if m is not None and m['t'] == t]
        comp = [n for n in names if re.search(r'__completed(_nonzero)?$', n)]
        fields[t] = {
            'completed_column': comp[0], 'task': ctx.results[t]['field']['task'], 'codes': ctx.results[t]['field']['evaluation_codes'],
            'original_column_preserved': True, 'status_column': t + '__status',
            'estimate_probability_column': t + '__estimate_probability',
            'reference_probability_columns': [n for n in names if '__p_' in n],
            'completion_probability_columns': [n for n in names if '__completion_p_' in n],
            'majority_code': registry[t]['majority_code'], 'method': registry[t]['method'], 'model_id': registry[t]['model_id'],
            'probabilities': 'Populated only for originally blank targets. __p_* are the district legacy calibrated probabilities; '
                             '__completion_p_* (where present) and __estimate_probability belong to the filling method.',
            'flags': 'Precision-flag columns are blank for this district (lead ruling R-7).'}
    return {'source_columns': 'All 622 original columns preserved verbatim as parsed string values; the source file is unchanged.',
            'source_row': 'One-based data-row number in the unchanged supplied CSV, excluding its header.',
            'audited_tract': 'Blank on every row: the map-check sample is rebuilt from the district street store after Phase 2 (ruling R-10).',
            'completed_columns': 'Observed rows carry their observed value (CPI as 0/1); blank rows carry the fill.',
            'status_vocabulary': ['observed'] + STATUS, 'revision': 'terrier_luna_v1', 'fields': fields}
