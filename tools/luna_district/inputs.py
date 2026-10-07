"""P3-01 source check, P3-02 groups and splits, P3-05 feature frame, P3-03b labels.

Everything model-shaped is the copied Luna code (validate_individual.prepare, search_rare.prepare, audit_variables.main);
this module only sequences it, asserts the district census against the plan, and records receipts (aggregates only).
"""
import json
from pathlib import Path

from . import copies
from .common import (CO_EXPORT, DISTRICTS, TARGETS, Step, district_dir, expected_blanks, read_json, sha256_file,
                     write_json)


def _dirs(D):
    return dict(vi=D / '.local' / 'individual-validation-20260928', rare=D / '.local' / 'rare-feature-search-20260928',
                four=D / '.local' / 'four-metric-validation-20260928', work=D / 'work')


def source_check(district, redo=False):
    import pandas as pd
    cfg = DISTRICTS[district]
    D = district_dir(district)
    out = D / 'work' / 'source-check.json'
    with Step(district, 'source', outputs=[out], redo=redo) as st:
        src = Path(cfg['source_csv'])
        assert src.stat().st_size == cfg['source_bytes'], 'source byte size differs from Phase 1'
        sha = sha256_file(src)
        assert sha == cfg['source_sha256'], 'source sha256 differs from Phase 1'
        co_header = list(pd.read_csv(DISTRICTS['co-replay']['source_csv'], nrows=0, encoding='cp1252').columns)
        header = list(pd.read_csv(src, nrows=0, encoding='cp1252').columns)
        assert len(header) == 622 and header == co_header, 'header differs from the 622 Colorado names'
        rows = 0
        for chunk in pd.read_csv(src, encoding='cp1252', usecols=[0], dtype=str, keep_default_na=False,
                                 na_filter=False, chunksize=200000):
            rows += len(chunk)
        assert rows == cfg['rows'], f'row count {rows} differs from {cfg["rows"]}'
        write_json(out, {'source_sha256': sha, 'bytes': src.stat().st_size, 'rows': rows, 'columns': len(header),
                         'header_equals_colorado': True, 'read': 'encoding=cp1252 strict, dtype=str, keep_default_na=False'})
        st.note(source_sha256=sha, rows=rows, columns=len(header), header_equals_colorado=True)


def groups_and_splits(district, redo=False):
    cfg = DISTRICTS[district]
    D = district_dir(district)
    d = _dirs(D)
    outputs = [d['vi'] / n for n in ('protocol.json', 'features.pkl', 'labels-and-splits.npz', 'input-audit.json')]
    c = copies.load(district)
    with Step(district, 'groups', outputs=outputs, heavy=True, redo=redo) as st:
        assert (D / 'work' / 'source-check.json').exists(), 'run the source step first'
        # search_rare sets validate_individual.OUT to its own folder at import (SR:37); the original ran prepare() in a
        # separate process where OUT was the validation folder. Restore that for this one call, then put SR's value back.
        sr_out = c.vi.OUT
        c.vi.OUT = d['vi']
        try:
            c.vi.prepare()
        finally:
            c.vi.OUT = sr_out
        audit = read_json(d['vi'] / 'input-audit.json')
        assert audit['source_sha256'] == cfg['source_sha256'] and audit['rows'] == cfg['rows']
        assert audit['components'] == cfg['components'], (audit['components'], cfg['components'])
        assert audit['largest_component_rows'] == cfg['largest_component']
        parts = audit['partition_components']
        assert [parts['train'], parts['validation'], parts['test']] == cfg['split_components'], parts
        st.note(rows=audit['rows'], components=audit['components'], largest_component_rows=audit['largest_component_rows'],
                partition_rows=audit['partition_rows'], partition_components=parts, household_overlap=0, address_overlap=0)


def numeric_columns(c, D):
    """The numeric-typed candidate columns exactly as search_rare.prepare derives them (SR:124-133)."""
    sr, base = c.sr, c.vi
    inv = {x['column']: x for x in json.loads((D / '.local' / 'area-variable-audit' / 'column-inventory.json').read_text())}
    base_cols = base.NUMERIC + base.CATEGORICAL + sr.NEW_SII
    consumers = [col for col in inv if col.startswith('sci_') and col not in base.TARGETS.values()
                 and not any(word in col for word in sr.DENY) and inv[col]['nonempty'] > 0]
    cols = list(dict.fromkeys(base_cols + consumers))
    numeric = [col for col in cols if col in base.NUMERIC or col in ['sii_age', 'sii_married', 'sii_children_present',
               'sii_senior_citizen_in_hh', 'sii_single_parent_guardian', 'sii_vacant']
               or col.startswith(('sci_ms_', 'sci_cpi_', 'sci_ct_'))
               or (col.startswith('sci_') and col not in base.CATEGORICAL and inv[col]['numeric_fraction_of_nonempty'] == 1
                   and isinstance(inv[col]['distinct_nonempty'], int) and inv[col]['distinct_nonempty'] <= 2)]
    return cols, numeric


def prescan_numeric(c, D, source_csv):
    """Count values that would trip search_rare's 'Unexpected numeric code' assert, per numeric-typed column."""
    import numpy as np
    import pandas as pd
    cols, numeric = numeric_columns(c, D)
    hits = {col: 0 for col in numeric}
    rows = 0
    for chunk in pd.read_csv(source_csv, encoding='cp1252', usecols=numeric, dtype=str, keep_default_na=False,
                             na_filter=False, chunksize=100000):
        rows += len(chunk)
        for col in numeric:
            raw = chunk[col].str.strip()
            num = pd.to_numeric(raw.replace('', np.nan), errors='coerce')
            hits[col] += int((raw.ne('') & num.isna()).sum())
    return {'rows': rows, 'columns_scanned': len(numeric), 'columns_with_hits': {k: v for k, v in hits.items() if v}}


def feature_frame(district, redo=False):
    import pandas as pd
    cfg = DISTRICTS[district]
    D = district_dir(district)
    d = _dirs(D)
    outputs = [d['rare'] / n for n in ('features.pkl', 'partition-extra.npz', 'feature-catalog.json', 'input-audit.json',
                                       'protocol.json')] + [D / 'work' / 'numeric-prescan.json', D / 'work' / 'frame-check.json']
    c = copies.load(district)
    with Step(district, 'frame', outputs=outputs, heavy=True, redo=redo) as st:
        assert (D / 'work' / 'holdout-receipt.json').exists(), 'the holdout must be frozen before the feature frame is built'
        scan = prescan_numeric(c, D, cfg['source_csv'])
        write_json(D / 'work' / 'numeric-prescan.json', scan)
        st.note(prescan=scan)
        assert not scan['columns_with_hits'], f'numeric-typed columns hold non-numeric values: {scan["columns_with_hits"]}'
        c.sr.prepare()
        raw = c.fx.load_stances()
        frame = pd.read_pickle(d['rare'] / 'features.pkl')
        assert len(frame) == cfg['rows'] == len(raw)
        c.ci.add_stances(frame, raw)
        results = read_json(D / 'frozen-co' / 'individual-results.json')
        needed = sorted({f for t in TARGETS for f in results[t]['selected_features']})
        missing = [f for f in needed if f not in frame.columns]
        assert not missing, f'frozen features missing from the district frame: {missing}'
        dtypes = {}
        for col in needed:
            dtypes[str(frame[col].dtype)] = dtypes.get(str(frame[col].dtype), 0) + 1
        info = {'rows': len(frame), 'columns': frame.shape[1], 'frozen_feature_union': len(needed),
                'frozen_dtypes': dtypes, 'memory_gb': round(frame.memory_usage(deep=True).sum() / 2 ** 30, 3),
                'raw_candidate_columns': read_json(d['rare'] / 'input-audit.json')['raw_candidates']}
        write_json(D / 'work' / 'frame-check.json', info)
        st.note(**info)


def labels(district, redo=False):
    import numpy as np
    cfg = DISTRICTS[district]
    D = district_dir(district)
    d = _dirs(D)
    outputs = [d['four'] / n for n in ('labels.npz', 'missingness-audit.json', 'missingness.csv', 'predictor-catalog.json')]
    c = copies.load(district)
    with Step(district, 'labels', outputs=outputs, heavy=True, redo=redo) as st:
        assert (d['rare'] / 'feature-catalog.json').exists(), 'run the frame step first (audit_variables reads its catalog)'
        c.av.main()
        audit = read_json(d['four'] / 'missingness-audit.json')
        lab = np.load(d['four'] / 'labels.npz')
        split = np.load(d['vi'] / 'labels-and-splits.npz')
        masks = np.load(D / 'work' / 'observed-masks.npz')
        hold = np.load(D / 'work' / 'holdout.npz')
        results = read_json(D / 'frozen-co' / 'individual-results.json')
        fields = {f['column']: f for f in audit['fields']}
        total = 0
        for t in TARGETS:
            if t in ('sci_cpi_cc_index', 'sci_credit_card_user'):
                y = split['cc' if t == 'sci_cpi_cc_index' else 'card_user']
            else:
                y = lab[t]
            observed = y >= 0
            assert np.array_equal(observed, masks[t]), f'{t}: labels disagree with the raw blank mask'
            blanks = int((~observed).sum())
            assert blanks == expected_blanks(district, t), (t, blanks, expected_blanks(district, t))
            total += blanks
            assert np.array_equal(hold[t], np.flatnonzero((split['split'] == 2) & observed)), f'{t}: holdout differs'
            if 'task' in fields[t]:      # the two retained_RF fields (cc, card user) carry no task in the audit file
                assert fields[t]['task'] == results[t]['field']['task'], t
                assert fields[t]['evaluation_codes'] == results[t]['field']['evaluation_codes'], \
                    (t, fields[t]['evaluation_codes'], results[t]['field']['evaluation_codes'])
        assert total == cfg['completed_values'], (total, cfg['completed_values'])
        st.note(blank_cells=total, fields=len(TARGETS), codes_equal_colorado=True, labels_equal_raw_blank_masks=True,
                holdout_equals_split2_observed=True)
