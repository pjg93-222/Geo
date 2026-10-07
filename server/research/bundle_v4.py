"""Validator for the row-framework part of a `cu-summary-v4` bundle (area_indices.json and adjacency.json).

The v3 checks in bundles.validate() run first (a v4 bundle contains a v3 bundle). Everything here is aggregate: an area
row has a count of people and households and means of person scores, never a person. The validator recomputes every
derived value it can (bands, midrank percentiles, withheld-set hash, nesting remainders) instead of trusting the file,
and it fails closed: any mismatch rejects the whole bundle.
"""
import math
import re

from django.conf import settings

from tools import area_layers as AL
from tools import districts as D
from tools.geography_levels import area_label

AREA_FILE_KEYS = {'schema', 'rowfw_version', 'columns', 'rows'}
AREA_SCHEMA = 'area-indices-1'
ROW_BASE_KEYS = {'basis', 'level', 'id', 'label', 'parent_id', 'status', 'n_rows', 'n_households', 'groups'}
RF_KEYS = {'version', 'base_snapshot', 'area_table_sha256', 'suppressed_ids_sha256', 'framework_config_sha256',
           'plan_sha256', 'criteria_sha256', 'run_id', 'rulings', 'd1_audit', 'area_indices_sha256',
           'withheld_set_sha256', 'adjacency_sha256'}
D1_AUDIT = {'exactly_disclosed': 0, 'symdiff_violations': 0, 'remainder_violations': 0}
GEOMETRY_METHOD_KEYS = {'block', 'tract', 'precinct', 'zip', 'concordance'}
CONCORDANCE_KEYS = {'precinct_person_share', 'zip_person_share', 'precincts_without_block_majority', 'zips_without_zcta',
                    'blocks_added_from_source', 'withheld_without_polygon'}
SNAPSHOT_RE = re.compile(D.snapshot_pattern(settings.DISTRICT))
# Keys that must never appear anywhere in an area file: a person or a source row is not an area.
FORBIDDEN_KEYS = {'person_id', 'model_source_row', 'name', 'address', 'email', 'phone', 'persist_id', 'household_code',
                  'hh', 'p_id', 'h_id'}
TOLERANCE = 1e-9


def _fail(message):
    raise ValueError(message)


def _keys(value, allowed, what):
    if not isinstance(value, dict) or set(value) != set(allowed):
        _fail('Unexpected %s fields' % what)


def _number(value, low=-1e12, high=1e12, nullable=False):
    if value is None and nullable:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not low <= value <= high:
        _fail('Invalid area number')


def _integer(value, low=0, high=10 ** 9, nullable=False):
    if value is None and nullable:
        return
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        _fail('Invalid area count')


def id_pattern(basis, level):
    if basis == 'registration':
        # people registered outside the district appear as withheld areas of other counties, so no county prefix
        return {'block': r'\d{15}', 'precinct': r'\d{10}', 'tract': r'\d{11}', 'zip': r'\d{5}'}[level]
    return {'tract': r'\d{11}', 'zip': r'\d{5}'}[level]


def _scan_forbidden(value, path='area file', depth=0):
    """Reject person-level keys and person or household ids anywhere in a JSON value."""
    if depth > 8:
        _fail('Area file nesting is too deep')
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                _fail('Person-level key in the %s' % path)
            _scan_forbidden(item, path, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _scan_forbidden(item, path, depth + 1)
    elif isinstance(value, str):
        if AL.PERSON_KEY.search(value):
            _fail('Person or household identifier in the %s' % path)


def _midrank_matches(values, stored):
    expected = AL.midrank(values)
    return all(abs(a - b) <= 1e-7 for a, b in zip(expected, stored))


def _check_group_values(group, cells, columns):
    """Types and ranges of one group's cells. Returns {column: value}."""
    if not isinstance(cells, list) or len(cells) != len(columns):
        _fail('Invalid %s cells' % group)
    record = dict(zip(columns, cells))
    for column, value in record.items():
        if column in AL.INTEGER_COLUMNS:
            _integer(value, 0, 10 ** 6)
        else:
            _number(value)
    if group == 'lean':
        _number(record['raw_mean'], -100, 100)
        _number(record['eb_mean'], -100, 100)
        _number(record['eb_se'], 0, 100)
        _number(record['shrink_B'], 0, 1)
        _number(record['measured_pct_mean'], 0, 100)
        shares = [record['share_band_' + k] for k in ('m2', 'm1', 'p0', 'p1', 'p2')]
        for share in shares + [record['share_reg_D'], record['share_reg_R'], record['share_reg_U'],
                               record['share_band_undetermined']]:
            _number(share, 0, 1)
        if abs(sum(shares) - 1) > 1e-9:
            _fail('Lean band shares must sum to 1')
        if record['share_reg_D'] + record['share_reg_R'] + record['share_reg_U'] > 1 + 1e-9:
            _fail('Registration shares exceed 1')
        return record
    _number(record['raw_mean'], 0, 100)
    _number(record['eb_mean'], 0, 100)
    _number(record['eb_se'], 0, 100)
    _number(record['shrink_B'], 0, 1)
    _number(record['measured_pct_mean'], 0, 100)
    for column in ('area_pct', 'top10_area_pct'):
        _number(record[column], 0, 100)
    for column in ('top10_share_raw', 'top10_share_eb'):
        _number(record[column], 0, 1)
    _number(record['top10_shrink_B'], 0, 1)
    _number(record['top10_eb_se'], 0, 1)
    # lo90 / hi90 are whole-number bounds of the standing's own 90% interval (posterior draws of the area's rank). The
    # published point standing is not always inside it (measured: up to 4 points for top-10 shares), so only the order
    # of the bounds is checked.
    for lo, hi in (('area_pct_lo90', 'area_pct_hi90'), ('top10_area_pct_lo90', 'top10_area_pct_hi90')):
        _number(record[lo], 0, 100)
        _number(record[hi], 0, 100)
        if record[lo] > record[hi]:
            _fail('Likely range bounds are out of order')
    if record['band'] != AL.band(record['area_pct']) or record['top10_band'] != AL.band(record['top10_area_pct']):
        _fail('Band does not follow the standing')
    if group == 'cua':
        _number(record['md_share_mean'], 0, 1)
    return record


def validate_area_indices(file, manifest):
    _keys(file, AREA_FILE_KEYS, 'area index file')
    if file['schema'] != AREA_SCHEMA or file['rowfw_version'] != AL.ROWFW_VERSION:
        _fail('Unsupported area index file')
    if file['columns'] != {group: list(AL.COLUMNS[group]) for group in AL.GROUPS}:
        _fail('Unexpected area index columns')
    rows = file['rows']
    if not isinstance(rows, list) or not rows or len(rows) > 100000:
        _fail('Invalid area index rows')
    seen, previous = set(), None
    by_partition = {}
    for row in rows:
        if not isinstance(row, dict):
            _fail('Invalid area row')
        basis, level = row.get('basis'), row.get('level')
        if level not in AL.BASIS_LEVELS.get(basis, ()):
            _fail('Unsupported area basis or level')
        _keys(row, ROW_BASE_KEYS | set(AL.RELATIONS[level]), 'area row')
        ident = row['id']
        if not isinstance(ident, str) or not re.fullmatch(id_pattern(basis, level), ident):
            _fail('Invalid area id')
        order = (basis, level, ident)
        if previous is not None and order <= previous:
            _fail('Area rows must be unique and sorted')
        previous = order
        if row['label'] != area_label(level, ident):
            _fail('Area label is not the generated label')
        expected_parent = ident[:11] if (basis, level) == ('registration', 'block') else 'file'
        if row['parent_id'] != expected_parent:
            _fail('Invalid area parent')
        if row['status'] not in ('ok', 'withheld'):
            _fail('Invalid area status')
        relations = {key: row[key] for key in AL.RELATIONS[level]}
        if row['status'] == 'withheld':
            if row['n_rows'] is not None or row['n_households'] is not None or row['groups'] is not None \
                    or any(value is not None for value in relations.values()):
                _fail('Values leaked from a withheld area')
        else:
            _integer(row['n_rows'], AL.MIN_N)
            _integer(row['n_households'], AL.MIN_HOUSEHOLDS)
            if row['n_households'] > row['n_rows']:
                _fail('More households than people')
            if not isinstance(row['groups'], dict) or set(row['groups']) != set(AL.GROUPS):
                _fail('Invalid area groups')
            row['_records'] = {group: _check_group_values(group, row['groups'][group], AL.COLUMNS[group])
                               for group in AL.GROUPS}
            for key, value in relations.items():
                if basis != 'registration':
                    if value is not None:
                        _fail('Residence areas carry no block relations')
                elif key == 'precinct':
                    if value is not None and not re.fullmatch(r'\d{10}', str(value)):
                        _fail('Invalid precinct relation')
                elif key == 'tract':
                    if value is not None and not re.fullmatch(r'\d{11}', str(value)):
                        _fail('Invalid tract relation')
                else:
                    _integer(value, 1, 10 ** 6, nullable=True)
        seen.add(order)
        by_partition.setdefault((basis, level), []).append(row)
    if set(by_partition) != {(b, l) for b, levels in AL.BASIS_LEVELS.items() for l in levels}:
        _fail('Area index levels are incomplete')

    for (basis, level), partition in by_partition.items():
        withheld = [r for r in partition if r['status'] == 'withheld']
        # one withheld area alone is the difference between the file total and the published rest
        if len(withheld) == 1:
            _fail('A single withheld area can be recovered by subtraction')
        published = [r for r in partition if r['status'] == 'ok']
        if not published:
            _fail('A partition has no published area')
        for group in ('ovr', 'cua', 'part', 'soi'):
            for source, target in (('eb_mean', 'area_pct'), ('top10_share_eb', 'top10_area_pct')):
                values = [r['_records'][group][source] for r in published]
                stored = [r['_records'][group][target] for r in published]
                if not _midrank_matches(values, stored):
                    _fail('Inconsistent area percentile')

    # nesting remainder (D1): a published tract minus its published blocks leaves nothing or at least the floors
    blocks = {}
    for row in by_partition[('registration', 'block')]:
        if row['status'] == 'ok':
            blocks.setdefault(row['parent_id'], [0, 0])
            blocks[row['parent_id']][0] += row['n_rows']
            blocks[row['parent_id']][1] += row['n_households']
    for row in by_partition[('registration', 'tract')]:
        if row['status'] != 'ok':
            continue
        taken_rows, taken_households = blocks.get(row['id'], [0, 0])
        remainder = row['n_rows'] - taken_rows
        remainder_households = row['n_households'] - taken_households
        if remainder < 0 or 0 < remainder < AL.MIN_N or 0 < remainder_households < AL.MIN_HOUSEHOLDS:
            _fail('A tract remainder under the floor can be recovered by subtraction')
    for rows_ in by_partition.values():
        for row in rows_:
            row.pop('_records', None)

    for row in rows:
        row.pop('_records', None)
    if file_hash(rows) != manifest['row_framework']['area_indices_sha256']:
        _fail('Area index checksum does not match the manifest')
    if AL.withheld_set_sha256(rows) != manifest['row_framework']['withheld_set_sha256']:
        _fail('Withheld set does not match the manifest')
    _scan_forbidden(file, 'area index file')
    return by_partition


def file_hash(rows):
    return AL.sha256_json(rows)


def validate_adjacency(adjacency, by_partition, manifest):
    _keys(adjacency, set(AL.ADJACENCY_LEVELS), 'adjacency')
    for level in AL.ADJACENCY_LEVELS:
        graph = adjacency[level]
        if not isinstance(graph, dict):
            _fail('Invalid adjacency')
        known = {r['id'] for r in by_partition[('registration', level)]}
        for ident, neighbours in graph.items():
            if ident not in known or not isinstance(neighbours, list) or neighbours != sorted(set(neighbours)) \
                    or ident in neighbours or len(neighbours) > 60:
                _fail('Invalid adjacency list')
            for other in neighbours:
                if other not in known or ident not in graph.get(other, ()):
                    _fail('Adjacency must be symmetric')
    if AL.sha256_json(adjacency) != manifest['row_framework']['adjacency_sha256']:
        _fail('Adjacency checksum does not match the manifest')
    _scan_forbidden(adjacency, 'adjacency file')


def validate_v4(payload, manifest):
    """Raise ValueError unless the v4 additions are consistent. Called by bundles.validate() for cu-summary-v4."""
    rf = manifest['row_framework']
    _keys(rf, RF_KEYS, 'row framework')
    if rf['version'] != AL.ROWFW_VERSION:
        _fail('Unsupported row framework version')
    for key, expected in AL.FROZEN_SHA256.items():
        if rf[key] != expected:
            _fail('The bundle was not built from the frozen row framework inputs')
    for key in ('area_table_sha256', 'suppressed_ids_sha256', 'area_indices_sha256', 'withheld_set_sha256',
                'adjacency_sha256'):
        if not isinstance(rf[key], str) or not AL.HEX64.fullmatch(rf[key]):
            _fail('Invalid row framework checksum')
    if not isinstance(rf['base_snapshot'], str) or not SNAPSHOT_RE.fullmatch(rf['base_snapshot']):
        _fail('Invalid base snapshot key')
    if not isinstance(rf['run_id'], str) or not 0 < len(rf['run_id']) <= 120:
        _fail('Invalid run id')
    if rf['rulings'] != AL.RULINGS or rf['d1_audit'] != D1_AUDIT:
        _fail('The suppression audit is not clean')
    expected_key = AL.snapshot_key(rf['base_snapshot'], rf['area_table_sha256'], rf['suppressed_ids_sha256'],
                                   rf['framework_config_sha256'])
    if manifest['snapshot'] != expected_key:
        _fail('Snapshot key does not follow from its inputs')
    if manifest['area_index_levels'] != {b: list(l) for b, l in AL.BASIS_LEVELS.items()}:
        _fail('Unexpected area index levels')
    if manifest['area_index_catalog'] != {'groups': AL.INDEX_CATALOG, 'layers': AL.LAYERS}:
        _fail('Unexpected area index catalog')
    if manifest['area_floors'] != {'min_n': AL.MIN_N, 'min_households': AL.MIN_HOUSEHOLDS}:
        _fail('Unexpected suppression floors')
    method = manifest['geometry_method']
    _keys(method, GEOMETRY_METHOD_KEYS, 'geometry method')
    for key in ('block', 'tract', 'precinct', 'zip'):
        if not isinstance(method[key], str) or not 0 < len(method[key]) <= 600:
            _fail('Invalid geometry method')
    _keys(method['concordance'], CONCORDANCE_KEYS, 'concordance')
    for key in ('precinct_person_share', 'zip_person_share'):
        _number(method['concordance'][key], 0, 1)
    _integer(method['concordance']['precincts_without_block_majority'])
    _integer(method['concordance']['zips_without_zcta'])
    _integer(method['concordance']['blocks_added_from_source'])
    _integer(method['concordance']['withheld_without_polygon'])

    by_partition = validate_area_indices(payload['area_indices.json'], manifest)
    validate_adjacency(payload['adjacency.json'], by_partition, manifest)

    geometry = payload['geometry.json']
    missing_withheld = 0
    for level in ('block', 'precinct', 'tract'):
        rows_ = by_partition[('registration', level)]
        if not {r['id'] for r in rows_ if r['status'] == 'ok'} <= set(geometry[level]):
            _fail('A published %s area has no polygon' % level)
        missing_withheld += sum(1 for r in rows_ if r['status'] == 'withheld' and r['id'] not in geometry[level])
    if missing_withheld != method['concordance']['withheld_without_polygon']:
        _fail('Withheld-area polygon coverage does not match the manifest')
    zip_rows = by_partition[('registration', 'zip')]
    if len({r['id'] for r in zip_rows} - set(geometry['zcta'])) != method['concordance']['zips_without_zcta']:
        _fail('ZIP polygon coverage does not match the manifest')
    # the manifest repeats the snapshot-wide reconciliation of the v3 bundle: nothing here may contradict it
    return manifest
