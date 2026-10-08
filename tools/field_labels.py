"""The 40 framework fields: neutral labels, groups, scoring weights and value display (stdlib only).

Labels are neutral noun phrases (FIDELITY_SPEC section 5.4). They contain no advisory wording; ADVISORY_PATTERN is the
T17(d) style gate and the tests run it over every label and display string.

The weights are the frozen rowfw-1.0.0 index weights (framework_config.json, fields.<name>.index_weight,
index_weight_code50_rows and index_weight_signed). tools/build_scores_v3.py compares this table with the frozen config
on every build and stops on any difference, so the table cannot drift silently.
"""
from fractions import Fraction as F
import re

GROUPS = ('cua', 'part', 'soi', 'lean')
GROUP_LABELS = {
    'cua': 'Credit Union Alignment',
    'part': 'Participation',
    'soi': 'Sphere of Influence',
    'lean': 'Partisan lean',
    'ovr': 'Overall standing',
}
# Short control labels used by the map header (FIDELITY_SPEC 6.1).
CONTROL_LABELS = {'ovr': 'Overall', 'cua': 'Credit Union Alignment', 'part': 'Participation',
                  'soi': 'Sphere of Influence', 'lean': 'Partisan lean'}

# (field, group, label, kind, weight, weight_code50)  weight/weight_code50 are exact fractions; lean weights are signed.
_TABLE = [
    ('sci_cpi_online_pay_index', 'cua', 'Online payment', 'binary', F(1, 15), None),
    ('sci_cpi_internet_buy_index', 'cua', 'Internet buying', 'binary', F(1, 15), None),
    ('sci_ct_smrtphn_typeowns_iphone', 'cua', 'iPhone ownership', 'binary', F(1, 15), None),
    ('sci_cpi_auto_index', 'cua', 'Automobile interest', 'binary', F(1, 15), None),
    ('sci_cpi_insurance_auto_index', 'cua', 'Auto insurance', 'binary', F(1, 15), None),
    ('sci_cpi_insurance_index', 'cua', 'Insurance', 'binary', F(1, 15), None),
    ('sci_cpi_cc_index', 'cua', 'Credit card index', 'binary', F(4, 25), None),
    ('sci_credit_card_user', 'cua', 'Credit card use', 'binary', F(4, 25), None),
    ('sai_recession_sensitivity_score', 'cua', 'Recession sensitivity', 'percentile', F(2, 25), None),
    ('sci_owner_renter', 'cua', 'Owner or renter', 'categorical_map', F(1, 15), None),
    ('sii_homeowner', 'cua', 'Homeowner', 'categorical_map', F(1, 15), None),
    ('sii_dwelling_type', 'cua', 'Dwelling type', 'categorical_map', F(1, 15), None),
    ('sai_activist_score', 'part', 'Activist score', 'percentile', F(1, 4), F(1, 3)),
    ('sai_digital_reachability_score', 'part', 'Digital reachability', 'percentile', F(1, 4), F(0)),
    ('sai_evav_score', 'part', 'Early or mail voting', 'percentile', F(1, 8), F(1, 6)),
    ('sai_evav_timing_score', 'part', 'Early or mail voting timing', 'percentile', F(1, 8), F(1, 6)),
    ('sai_local_voter_score', 'part', 'Local voter', 'percentile', F(1, 24), F(1, 18)),
    ('sai_midterm_general_turnout_score', 'part', 'Midterm general turnout', 'percentile', F(1, 24), F(1, 18)),
    ('sai_non_presidential_primary_turnout_score', 'part', 'Non-presidential primary turnout', 'percentile',
     F(1, 24), F(1, 18)),
    ('sai_offyear_general_turnout_score', 'part', 'Off-year general turnout', 'percentile', F(1, 24), F(1, 18)),
    ('sai_presidential_general_turnout_score', 'part', 'Presidential general turnout', 'percentile',
     F(1, 24), F(1, 18)),
    ('sai_presidential_primary_turnout_score', 'part', 'Presidential primary turnout', 'percentile',
     F(1, 24), F(1, 18)),
    ('household_size', 'soi', 'Household size', 'percentile', F(1, 30), None),
    ('sci_number_of_adults_in_household', 'soi', 'Adults in household', 'ridit', F(1, 30), None),
    ('sci_number_of_children_in_household', 'soi', 'Children in household', 'ridit', F(1, 30), None),
    ('sci_charitable_donor', 'soi', 'Charitable donor', 'binary', F(9, 80), None),
    ('sci_cpi_donor_index', 'soi', 'Donor index', 'binary', F(9, 80), None),
    ('sci_ct_dnr_contribamt_high', 'soi', 'High contribution amount', 'binary', F(9, 40), None),
    ('sci_ct_volunteer_chtyorg', 'soi', 'Charitable-organisation volunteer', 'binary', F(9, 20), None),
    ('sci_cpi_conservative_index', 'lean', 'Conservative index', 'binary', F(-1, 6), None),
    ('sci_cpi_liberal_index', 'lean', 'Liberal index', 'binary', F(1, 6), None),
    ('sci_ct_political_partyaff_democrat', 'lean', 'Democratic affiliation', 'binary', F(1, 6), None),
    ('sci_ct_political_partyaff_gop', 'lean', 'Republican affiliation', 'binary', F(-1, 6), None),
    ('sci_ct_political_partyaff_ind', 'lean', 'Independent affiliation', 'binary', F(0), None),
    ('sci_ct_political_outlk_vconserv', 'lean', 'Very conservative outlook', 'binary', F(-1, 9), None),
    ('sci_ct_political_outlk_swconserv', 'lean', 'Somewhat conservative outlook', 'binary', F(-1, 18), None),
    ('sci_ct_political_outlk_mid', 'lean', 'Middle outlook', 'binary', F(0), None),
    ('sci_ct_political_outlk_swliberal', 'lean', 'Somewhat liberal outlook', 'binary', F(1, 18), None),
    ('sci_ct_political_outlk_vliberal', 'lean', 'Very liberal outlook', 'binary', F(1, 9), None),
    ('svi_party_registration', 'lean', 'Party registration', 'categorical_map', F(1, 2), None),
]

FIELD_ORDER = tuple(row[0] for row in _TABLE)
GROUP_FIELD_COUNTS = {group: sum(1 for row in _TABLE if row[1] == group) for group in GROUPS}
assert len(FIELD_ORDER) == 40 and GROUP_FIELD_COUNTS == {'cua': 12, 'part': 10, 'soi': 7, 'lean': 11}

FIELDS = []
for _fid, (_name, _group, _label, _kind, _w, _w50) in enumerate(_TABLE):
    FIELDS.append({
        'fid': _fid, 'name': _name, 'group': _group, 'label': _label, 'kind': _kind,
        # points per unit of exposure (score points); lean points are signed
        'points': float(_w * 100), 'points_code50': float((_w50 if _w50 is not None else _w) * 100),
        'weight': [_w.numerator, _w.denominator],
        'weight_code50': None if _w50 is None else [_w50.numerator, _w50.denominator],
    })
BY_NAME = {row['name']: row for row in FIELDS}
# Industries (Phase 4A): each entry scores one industry's alignment; the overall uses the selected one. Only CUA exists
# (O-12); its fields and weights are the frozen rowfw-1.0.0 CUA weights above, checked on every score build.
INDUSTRIES = {'cua': {'label': GROUP_LABELS['cua'], 'group': 'cua',
                      'fields': [(r['name'], r['label'], r['weight']) for r in FIELDS if r['group'] == 'cua']}}
# Street plan presets (Phase 4E): id -> (label, formula in words); s = candidate support 0..1, p = participation standing 0..1
ROI_PRESETS = {'mixed': ('Mixed-support streets', 'sum over people of (1 - |2s - 1|) x p'),
               'turnout': ('Supporter turnout', 'sum over people of s x (1 - p)'),
               'identify': ('Voter identification', 'people with s in the middle third or missing')}

# Display of the source status. Order is the stored status code.
STATUSES = ('observed', 'estimated_gpu_model', 'estimated_household_model', 'estimated_majority_default',
            'estimated_accuracy_argmax')
STATUS_LABELS = {
    'observed': 'Observed',
    'estimated_gpu_model': 'Estimated (person model)',
    'estimated_household_model': 'Estimated (household model)',
    'estimated_majority_default': 'Estimated (file default)',
    'estimated_accuracy_argmax': 'Estimated (legacy fill)',
}
CHILDREN_RULE_SOURCE = 'Estimated · children rule'
CODE50_VALUE = 'Unscored'

PARTY_CODES = {'D': 'Democrat', 'R': 'Republican', 'U': 'Unaffiliated', 'G': 'Green', 'L': 'Libertarian',
               'O': 'Other', 'W': 'Working Families'}
# The owner / renter field is the one categorical field whose code meanings are documented (FIDELITY_SPEC 5.4).
OWNER_RENTER = {'H': 'Known homeowner', 'O': 'Probable homeowner', 'U': 'Unknown', 'P': 'Probable renter',
                'R': 'Known renter'}
REG_GROUPS = ('D', 'R', 'U', 'O')
REG_GROUP_LABELS = {'D': 'Democrat', 'R': 'Republican', 'U': 'Unaffiliated', 'O': 'Other'}
LEAN_BAND_LABELS = {-2: 'Strong R', -1: 'Lean R', 0: 'Centre', 1: 'Lean D', 2: 'Strong D'}
LEAN_UNDETERMINED_LABEL = 'Undetermined'

# T17(d) style gate: advisory or ranking-intent words never appear in labels or display strings.
ADVISORY_PATTERN = re.compile(
    r'\b(prospect|prospects|target|targets|targeting|best|recommend|recommended|priority|priorities|should|ideal|'
    r'worth|likely to join|persuad|persuasion|convert|opportunity|high[- ]value|insufficient evidence)\b', re.I)


def reg_group_of(party_code):
    """The registration rollup used by the list, the balanced view and within-registration standings."""
    return party_code if party_code in ('D', 'R', 'U') else 'O'


def lean_band_of(lean):
    """Fixed band rule: |lean| < 25 Centre, 25..75 Lean, >= 75 Strong; sign + is D (framework_config groups.lean.bands)."""
    magnitude = abs(lean)
    band = 0 if magnitude < 25 else 1 if magnitude < 75 else 2
    return band if lean > 0 else -band if lean < 0 else 0


def display_round(value):
    """sign(x) * floor(|x| + 0.5): half goes away from zero (framework_config display.rounding_rule)."""
    import math
    if value is None:
        return None
    return int(math.copysign(math.floor(abs(value) + 0.5), value)) if value else 0


def value_text(field, literal):
    """The neutral literal for one stored value. Codes are shown as codes (the vendor code book is not in the repo)."""
    kind = BY_NAME[field]['kind']
    if literal is None:
        return None
    if field == 'svi_party_registration':
        return PARTY_CODES.get(literal, 'Code ' + str(literal))
    if kind == 'binary':
        return 'Yes' if str(literal) not in ('0', '0.0') else 'No'
    if field == 'sci_owner_renter':
        return OWNER_RENTER.get(str(literal), 'Code ' + str(literal))
    if kind == 'categorical_map':
        return 'Code ' + str(literal)
    if field == 'sci_number_of_children_in_household':
        return 'Code ' + str(literal)
    return str(literal)


def source_text(status, p, children_rule=False, code50=False):
    if code50:
        return CODE50_VALUE
    if status == 'observed':
        return 'Observed'
    if children_rule:
        return CHILDREN_RULE_SOURCE
    return 'Estimated · p ' + ('%.2f' % p if p is not None else '')


def self_check():
    """Every user-facing string passes the advisory gate."""
    strings = [row['label'] for row in FIELDS] + list(GROUP_LABELS.values()) + list(STATUS_LABELS.values())
    strings += list(OWNER_RENTER.values()) + list(PARTY_CODES.values()) + list(LEAN_BAND_LABELS.values()) + [CHILDREN_RULE_SOURCE, CODE50_VALUE]
    strings += [text for preset in ROI_PRESETS.values() for text in preset]
    bad = [text for text in strings if ADVISORY_PATTERN.search(text)]
    if bad:
        raise ValueError('Advisory wording in labels: ' + ', '.join(bad))
    return len(strings)


self_check()
