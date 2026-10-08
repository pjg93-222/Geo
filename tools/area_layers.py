"""Shared constants and helpers of the row-framework area layers (`cu-summary-v4`; stdlib only).

Used by the bundle builder (tools/build_bundle_v4.py), the validator (server/research/bundles.py) and the area
endpoints. Nothing here reads a person row: an area value is the mean of its members' scores and never stands for a
person.
"""
import hashlib
import json
import re

ROWFW_VERSION = 'rowfw-1.0.0'
# The frozen inputs of rowfw-1.0.0. A bundle that claims another config, plan or criteria file is rejected.
FROZEN_SHA256 = {
    'framework_config_sha256': '28e8d53db7e258d60d9eba87d0e186e877ac3cb4f4b0e6863e53d72b03cfc95a',
    'plan_sha256': 'f1ffa1a097605c781e8b6eaa65641308e7ec192f983de96f01b13db7d3da5e3a',
    'criteria_sha256': '114a051cdbf2ed00fccb94ab44551a1592e570354c06c66d9dac7eacd0aeb90b',
}
# The district's own freeze (Phase 3b re-freezes rowfw on each district's data), pinned from its PLAN.receipt.json. A
# district without an entry (None) fails closed: the builder and the validator reject its bundle until it is pinned here.
FROZEN_SHA256_BY_DISTRICT = {
    'co-05': FROZEN_SHA256,
    'or-05': {
        'framework_config_sha256': 'e8499d0a9a371adc6871eda658089ddf6f109067721f4a7110c0b13adfb12540',
        'plan_sha256': '7645762d4e17aa2a023b780e64a63aa8459c124e4c6ff5d439fb96f0b1fd856a',
        'criteria_sha256': '55ffd72049c3fb7416576441da6406d854c6198f38143ed9d948aaa6cc6b1dd4',
    },
    'ct-01': {
        'framework_config_sha256': '0df70c9d47df8ce87d43ae6a022c292a242d2facecac046d0d3bfb15a138d33c',
        'plan_sha256': '81249df41506a6c836fffb3fa9c620e4b4d2cebfd9ad3a188a4e4c1266e63530',
        'criteria_sha256': '579f6a53787b3d747f2507b5ce7eea73737c78cc4e10e362492d4755e6d03fd3',
    },
}
GROUPS = ('ovr', 'cua', 'part', 'soi', 'lean')
INDEX_GROUPS = ('ovr', 'cua', 'part', 'soi')
BASIS_LEVELS = {'registration': ('block', 'precinct', 'tract', 'zip'), 'residence': ('tract', 'zip')}
LEVEL_ORDER = ('block', 'precinct', 'tract', 'zip')
ADJACENCY_LEVELS = ('precinct', 'tract', 'zip')
MIN_N, MIN_HOUSEHOLDS = 20, 10
BREAKS = (20.0, 40.0, 60.0, 80.0)                   # area standing bands (framework_config bands)
SHARE_BREAKS = (0.05, 0.08, 0.12, 0.18)             # legend of the default colour layer (FIDELITY_SPEC 5.3)
MEASURED_BREAKS = (20.0, 40.0, 60.0, 80.0)          # the "% measured" companion layer
LEAN_CUTS = (25.0, 75.0)

STANDING_COLUMNS = ('raw_mean', 'eb_mean', 'eb_se', 'shrink_B', 'measured_pct_mean', 'area_pct', 'band',
                    'area_pct_lo90', 'area_pct_hi90', 'top10_share_raw', 'top10_share_eb', 'top10_shrink_B',
                    'top10_eb_se', 'top10_area_pct', 'top10_band', 'top10_area_pct_lo90', 'top10_area_pct_hi90')
CUA_COLUMNS = STANDING_COLUMNS + ('md_share_mean', 'n_cpi_blank')
LEAN_COLUMNS = ('raw_mean', 'eb_mean', 'eb_se', 'shrink_B', 'measured_pct_mean', 'share_band_m2', 'share_band_m1',
                'share_band_p0', 'share_band_p1', 'share_band_p2', 'share_reg_D', 'share_reg_R', 'share_reg_U',
                'share_band_undetermined')
COLUMNS = {'ovr': STANDING_COLUMNS, 'cua': CUA_COLUMNS, 'part': STANDING_COLUMNS, 'soi': STANDING_COLUMNS,
           'lean': LEAN_COLUMNS}
INTEGER_COLUMNS = {'band', 'top10_band', 'n_cpi_blank'}

# Relations stored beside the values (aggregate structure of the area levels; never a person).
RELATIONS = {'block': ('precinct',), 'precinct': ('tract', 'n_tracts', 'n_blocks'), 'tract': ('n_blocks',),
             'zip': ('n_blocks',)}

INDEX_CATALOG = [
    {'id': 'ovr', 'label': 'Overall standing', 'control': 'Overall', 'top10': 'overall'},
    {'id': 'cua', 'label': 'Credit Union Alignment', 'control': 'Credit Union Alignment',
     'top10': 'Credit Union Alignment'},
    {'id': 'part', 'label': 'Participation', 'control': 'Participation', 'top10': 'Participation'},
    {'id': 'soi', 'label': 'Sphere of Influence', 'control': 'Sphere of Influence', 'top10': 'Sphere of Influence'},
    {'id': 'lean', 'label': 'Partisan lean', 'control': 'Partisan lean', 'top10': None},
]
# A candidate district (Phase 4B) adds the 'sup' group right after 'ovr': a plain mean of person support scores, STANDING_COLUMNS.
CANDIDATE_GROUPS = ('ovr', 'sup') + GROUPS[1:]
CANDIDATE_COLUMNS = {**COLUMNS, 'sup': STANDING_COLUMNS}
CANDIDATE_CATALOG = INDEX_CATALOG[:1] + [{'id': 'sup', 'label': 'Candidate support', 'control': 'Candidate support',
                                          'top10': 'Candidate support'}] + INDEX_CATALOG[1:]
LAYERS = [
    {'id': 'top10_share_eb', 'label': 'Share of people in the top 10%', 'default': True},
    {'id': 'eb_mean', 'label': 'Area mean of person scores', 'default': False},
    {'id': 'measured_pct_mean', 'label': '% measured', 'default': False, 'breaks': list(MEASURED_BREAKS)},
]
RULINGS = ['R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7']
HEX64 = re.compile(r'[0-9a-f]{64}')
PERSON_KEY = re.compile(r'\b[ph]_[0-9a-f]{64}\b')


def band(value):
    """1 + the number of breaks at or below the value (a value equal to a break goes up)."""
    return 1 + sum(1 for cut in BREAKS if cut <= value)


def midrank(values):
    """100 * (L + E/2) / N on the ranking key round(x, 9): the area standing among published areas."""
    keyed = [round(float(v), 9) for v in values]
    n = len(keyed)
    ordered = sorted(keyed)
    import bisect
    return [100.0 * (bisect.bisect_left(ordered, k) + (bisect.bisect_right(ordered, k) - bisect.bisect_left(ordered, k))
                     / 2.0) / n for k in keyed]


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def sha256_json(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def withheld_set_sha256(rows):
    """sha256 of the sorted 'basis|level|id' lines of the withheld areas (group-independent)."""
    lines = sorted('%s|%s|%s' % (r['basis'], r['level'], r['id']) for r in rows if r['status'] == 'withheld')
    return hashlib.sha256(('\n'.join(lines) + '\n').encode('utf-8')).hexdigest()


def snapshot_key(base_key, area_table_sha256, suppressed_ids_sha256, config_sha256, prefix='co-cd005-'):
    digest = hashlib.sha256(('|'.join([base_key, area_table_sha256, suppressed_ids_sha256, config_sha256,
                                       ROWFW_VERSION])).encode('utf-8')).hexdigest()
    return prefix + digest[:12]


def group_cells(group):
    return COLUMNS[group]


def groups(d=None):
    """The area groups of district entry `d`: GROUPS itself for Colorado and any district without a candidate."""
    return CANDIDATE_GROUPS if d and d.get('candidate') else GROUPS


def columns(d=None):
    return CANDIDATE_COLUMNS if d and d.get('candidate') else COLUMNS


def index_catalog(d=None):
    return CANDIDATE_CATALOG if d and d.get('candidate') else INDEX_CATALOG
