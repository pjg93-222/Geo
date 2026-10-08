"""Fixed, descriptive source-field aggregates; no individual scores are created.

The catalog describes literal source values. Code labels intentionally do not
borrow meanings from a different provider's documentation.
"""
import hashlib
import json

import numpy as np
import pandas as pd

SCHEMA = 'cu-summary-v2'
METHOD_VERSION = 'descriptive-geography-2'      # 2: nothing is suppressed (Paul 2026-10-08); 1 withheld cells under 20

SAI_FIELDS = (
    'sai_recession_sensitivity_score', 'sai_activist_score', 'sai_evav_score',
    'sai_evav_timing_score', 'sai_local_voter_score',
    'sai_midterm_general_turnout_score',
    'sai_non_presidential_primary_turnout_score',
    'sai_offyear_general_turnout_score', 'sai_presidential_general_turnout_score',
    'sai_presidential_primary_turnout_score',
)
CPI_FIELDS = (
    'sci_cpi_insurance_auto_index', 'sci_cpi_online_pay_index',
    'sci_cpi_auto_index', 'sci_cpi_internet_buy_index',
    'sci_cpi_credit_repair_index', 'sci_cpi_credit_report_index',
    'sci_cpi_insurance_index', 'sci_cpi_cc_index', 'sci_cpi_donor_index',
    'sci_cpi_conservative_index', 'sci_cpi_liberal_index',
)
BINARY_FIELDS = (
    'sci_ct_smrtphn_typeowns_iphone', 'sci_credit_card_user',
    'sci_charitable_donor', 'sci_ct_dnr_contribamt_high',
    'sci_ct_volunteer_chtyorg', 'sci_ct_political_partyaff_democrat',
    'sci_ct_political_partyaff_gop', 'sci_ct_political_partyaff_ind',
    'sci_ct_political_outlk_vconserv', 'sci_ct_political_outlk_swconserv',
    'sci_ct_political_outlk_mid', 'sci_ct_political_outlk_swliberal',
    'sci_ct_political_outlk_vliberal',
)
CATEGORICAL_FIELDS = {
    'sci_owner_renter': ('H', 'O', 'P', 'R', 'U'),
    'sii_homeowner': ('H', 'R', 'U'),
    'sci_number_of_children_in_household': ('A', 'B', 'C', 'D'),
    'sii_dwelling_type': ('G', 'H', 'P', 'S'),
    'svi_party_registration': ('D', 'G', 'L', 'O', 'R', 'T', 'U'),
}
ADULTS_FIELD = 'sci_number_of_adults_in_household'
DIGITAL_FIELD = 'sai_digital_reachability_score'
FIELD_RANGES = {field: (0, 100) for field in SAI_FIELDS}
FIELD_RANGES[ADULTS_FIELD] = (1, 9)
CODE_DOMAINS = {
    **{field: tuple(str(n) for n in range(10)) for field in CPI_FIELDS},
    **{field: ('0', '1') for field in BINARY_FIELDS},
    **CATEGORICAL_FIELDS,
    ADULTS_FIELD: tuple(str(n) for n in range(1, 10)),
    DIGITAL_FIELD: tuple(str(n) for n in range(11)) + ('50',),
}


def _group(field):
    if field.startswith('sci_ct_political_') or field in {
        'sci_cpi_conservative_index', 'sci_cpi_liberal_index',
        'svi_party_registration',
    }:
        return 'Recorded affiliation'
    if field.startswith('sai_') and field != 'sai_recession_sensitivity_score':
        return 'Participation estimates'
    if field in {
        'sci_number_of_adults_in_household', 'sci_number_of_children_in_household',
        'sci_charitable_donor', 'sci_cpi_donor_index', 'sci_ct_dnr_contribamt_high',
        'sci_ct_volunteer_chtyorg', 'sci_owner_renter', 'sii_homeowner',
        'sii_dwelling_type',
    }:
        return 'Household and giving'
    return 'Consumer attributes'


def _label(field):
    for prefix in ('sci_cpi_', 'sci_ct_', 'sci_', 'sii_', 'svi_', 'sai_'):
        if field.startswith(prefix):
            return field[len(prefix):].replace('_', ' ').capitalize()
    return field.replace('_', ' ').capitalize()


def _definition(field, aggregation, code=None):
    if aggregation == 'mean':
        suffix, detail, units = 'mean', 'Mean source value', 'source_count' if field == ADULTS_FIELD else 'source_score'
        lo, hi = FIELD_RANGES[field]
        description = (
            f'Mean of valid {field} source values ({lo}–{hi}) among mapped profiles. '
            'Original source scale; not a probability or a new individual score.'
        )
        if field == ADULTS_FIELD:
            description = (
                'Mean of observed adult-count codes 1–9 among mapped profiles; '
                'profile-weighted, not a count of distinct households. Code 9 is '
                'retained literally without assuming whether it is top-coded.'
            )
    elif aggregation == 'share_nonzero':
        suffix, detail, units = 'nonzero', 'Nonzero-code share', 'share'
        description = (
            f'Share of valid {field} codes 0–9 that are nonzero. Zero is an '
            'observed code, not missing. Code direction and meaning are not inferred.'
        )
    else:
        suffix, detail, units = 'code_' + code, f'Code {code} share', 'share'
        domain = ', '.join(CODE_DOMAINS[field])
        description = (
            f'Share of mapped profiles with code {code}, among valid {field} '
            f'codes ({domain}). Codes are literal source labels; no provider '
            'comparator mapping or behavioral interpretation is applied.'
        )
    return {
        'id': field + '__' + suffix,
        'label': _label(field) + ' · ' + detail,
        'group': _group(field), 'field': field, 'aggregation': aggregation,
        'units': units, 'description': description,
    }


MEASURES = (
    [_definition(field, 'mean') for field in SAI_FIELDS]
    + [_definition(field, 'share_nonzero') for field in CPI_FIELDS]
    + [_definition(field, 'share_code', '1') for field in BINARY_FIELDS]
    + [_definition(ADULTS_FIELD, 'mean')]
    + [_definition(field, 'share_code', code)
       for field, codes in CATEGORICAL_FIELDS.items() for code in codes]
    + [_definition(DIGITAL_FIELD, 'share_code', '50')]
)
SOURCE_FIELDS = tuple(dict.fromkeys(m['field'] for m in MEASURES))
MEASURE_BY_ID = {m['id']: m for m in MEASURES}
MEASURE_RANGES = {
    m['id']: FIELD_RANGES[m['field']] if m['aggregation'] == 'mean' else (0, 1)
    for m in MEASURES
}
MEASURE_METHODOLOGY = (
    'Descriptive source values only, with equal weight per accepted mapped profile. '
    'Numeric means retain the original source scale; shares use literal valid codes. '
    'Missing, nonfinite and out-of-domain values are excluded, never filled with zero. '
    'Each valid-count denominator is field-specific; field coverage equals valid '
    'values divided by mapped profiles in that area. Every area and cell is shown, '
    'however few profiles it rests on; nothing is withheld. '
    'Percentile is 100 × (areas below + half the tied areas) / areas with a value, '
    'calculated separately for each measure, address basis and geography; all ties '
    'are 50. Percentiles describe areas, not people. No support, influence or '
    'composite alignment score is produced; household identifiers are excluded.'
)


def definition(measure_id):
    return MEASURE_BY_ID[measure_id]


def snapshot_key(source_sha256, prefix='co-cd005-'):
    config = {
        'schema': SCHEMA, 'method': METHOD_VERSION,
        'measures': MEASURES, 'ranges': FIELD_RANGES, 'codes': CODE_DOMAINS,
    }
    digest = hashlib.sha256((source_sha256 + json.dumps(
        config, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
    )).encode('utf-8')).hexdigest()
    return prefix + digest[:12]


def _hidden(status):
    return [None, None, None, None, status]


def midrank_percentiles(values):
    """Return tie-aware area ranks on 0–100 without weighting by sample size."""
    if not values:
        return []
    ranks = pd.Series(values, dtype='float64').rank(method='average').to_numpy()
    return ((ranks - 0.5) / len(values) * 100).tolist()


def aggregate_measures(df, keys, eligible, rows, definitions=None):
    """Attach the measure cells to one basis/level partition (tools/ingest.settle gave its rows their status).

    Temporary arrays contain selected source attributes only in local memory.
    Neither raw values, numerators, nor sums are written into the output rows.
    """
    definitions = MEASURES if definitions is None else definitions
    by_field = {}
    for measure in definitions:
        by_field.setdefault(measure['field'], []).append(measure)
    for row in rows:
        row['measures'] = {}
    published_rows = [r for r in rows if r['status'] == 'ok']
    if not published_rows:
        return rows

    positions = np.flatnonzero(np.asarray(eligible, dtype=bool))
    labels = np.asarray(keys, dtype=object)[positions]
    group_codes, group_labels = pd.factorize(labels, sort=False)
    groups = {label: i for i, label in enumerate(group_labels)}
    group_total = len(group_labels)
    for field, field_measures in by_field.items():
        raw = df[field].iloc[positions].astype('string').fillna('').str.strip()
        is_mean = field_measures[0]['aggregation'] == 'mean'
        numeric = None
        if is_mean:
            numeric = pd.to_numeric(raw, errors='coerce').to_numpy(dtype=float, na_value=np.nan)
            lo, hi = FIELD_RANGES[field]
            valid = np.isfinite(numeric) & (numeric >= lo) & (numeric <= hi)
            if field in CODE_DOMAINS:
                valid &= raw.isin(CODE_DOMAINS[field]).to_numpy(dtype=bool)
        else:
            valid = raw.isin(CODE_DOMAINS[field]).to_numpy(dtype=bool)
        valid_counts = np.bincount(group_codes[valid], minlength=group_total)
        values_by_measure = {}
        for measure in field_measures:
            if is_mean:
                totals = np.bincount(group_codes[valid], weights=numeric[valid], minlength=group_total)
            else:
                if measure['aggregation'] == 'share_nonzero':
                    target = raw.ne('0').to_numpy(dtype=bool) & valid
                else:
                    code = measure['id'].rsplit('__code_', 1)[1]
                    target = raw.eq(code).to_numpy(dtype=bool) & valid
                totals = np.bincount(group_codes[target], minlength=group_total)
            values_by_measure[measure['id']] = totals

        field_states = []
        for row in published_rows:
            group = groups.get(row['id'])
            n = int(valid_counts[group]) if group is not None else 0
            if n > row['count']:
                raise ValueError('Field denominator exceeds mapped area count')
            field_states.append([row, group, n, 'ok' if n else 'unavailable'])

        for measure in field_measures:
            visible = []
            for row, group, n, state in field_states:
                if state != 'ok':
                    row['measures'][measure['id']] = _hidden(state)
                else:
                    value = float(values_by_measure[measure['id']][group]) / n
                    cell = [value, n, n / row['count'], None, 'ok']
                    row['measures'][measure['id']] = cell
                    visible.append(cell)
            for cell, percentile in zip(visible, midrank_percentiles([c[0] for c in visible])):
                cell[3] = percentile
    return rows
