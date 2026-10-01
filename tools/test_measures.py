"""Synthetic-only checks for geographic source aggregation and disclosure rules."""
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from tools.ingest import FIELDS, read_profiles
from tools.measures import (
    ADULTS_FIELD, DIGITAL_FIELD, MEASURES, MEASURE_BY_ID, SOURCE_FIELDS,
    MEASURE_METHODOLOGY, aggregate_measures, midrank_percentiles, snapshot_key,
)


MEAN = 'sai_activist_score__mean'
CPI = 'sci_cpi_auto_index__nonzero'
BINARY = 'sci_credit_card_user__code_1'


def aggregate(field, areas, measure_ids, eligible=None, statuses=None):
    """Build small independent synthetic geography partitions."""
    values, keys, rows = [], [], []
    for ident, area_values in areas.items():
        values.extend(area_values)
        keys.extend([ident] * len(area_values))
        rows.append({'id': ident, 'count': len(area_values), 'status': 'ok'})
    if statuses:
        for row in rows:
            row['status'] = statuses.get(row['id'], 'ok')
            if row['status'] != 'ok':
                row['count'] = None
    if eligible is None:
        eligible = np.ones(len(values), dtype=bool)
    else:
        for row in rows:
            if row['status'] == 'ok':
                row['count'] = sum(e and key == row['id'] for e, key in zip(eligible, keys))
    aggregate_measures(
        pd.DataFrame({field: values}), pd.Series(keys), eligible, rows,
        definitions=[MEASURE_BY_ID[mid] for mid in measure_ids],
    )
    return {row['id']: row['measures'] for row in rows}


class GeographicMeasureTests(unittest.TestCase):
    def test_catalog_is_fixed_descriptive_and_excludes_household_identifiers(self):
        self.assertEqual(len(MEASURES), 59)
        self.assertEqual(len(SOURCE_FIELDS), 41)
        self.assertNotIn('sii_household_id', SOURCE_FIELDS)
        self.assertEqual(len(MEASURE_BY_ID), len(MEASURES))
        self.assertLessEqual(len(MEASURE_METHODOLOGY), 1200)
        for measure in MEASURES:
            self.assertEqual(set(measure), {'id', 'label', 'group', 'field', 'aggregation', 'units', 'description'})
        self.assertNotIn(DIGITAL_FIELD + '__mean', MEASURE_BY_ID)
        self.assertIn('Nonzero-code share', MEASURE_BY_ID[CPI]['label'])
        self.assertIn('Code U share', MEASURE_BY_ID['svi_party_registration__code_U']['label'])

    def test_original_scale_mean_valid_denominator_and_invalid_values(self):
        invalid = ['', 'bad', 'NaN', 'inf', '-1', '101', None] * 3
        result = aggregate('sai_activist_score', {'A': ['0'] * 20 + ['100'] * 20 + invalid}, [MEAN])
        value, n, coverage, percentile, status = result['A'][MEAN]
        self.assertEqual((value, n, percentile, status), (50, 40, 50, 'ok'))
        self.assertAlmostEqual(coverage, 40 / 61)
        json.dumps(result, allow_nan=False)

    def test_only_geographically_eligible_records_enter_values_and_denominators(self):
        result = aggregate('sai_activist_score', {'A': ['10'] * 20 + ['90'] * 20}, [MEAN],
                           eligible=[True] * 20 + [False] * 20)
        self.assertEqual(result['A'][MEAN], [10, 20, 1, 50, 'ok'])

    def test_small_missing_remainder_and_complementary_area_are_hidden(self):
        result = aggregate('sai_activist_score', {
            'A': ['10'] * 20 + [''], 'B': ['20'] * 40, 'C': ['30'] * 80,
        }, [MEAN])
        for ident in ('A', 'B'):
            self.assertEqual(result[ident][MEAN], [None, None, None, None, 'suppressed'])
        self.assertEqual(result['C'][MEAN], [30, 80, 1, 50, 'ok'])

    def test_small_valid_sample_never_publishes_mean_or_sample_size(self):
        result = aggregate('sai_activist_score', {'A': ['10'] * 19 + [''] * 20}, [MEAN])
        self.assertEqual(result['A'][MEAN], [None, None, None, None, 'suppressed'])

    def test_no_values_is_unavailable_but_observed_zero_is_available(self):
        result = aggregate('sai_activist_score', {'A': [''] * 40, 'B': ['0'] * 40, 'C': []}, [MEAN])
        self.assertEqual(result['A'][MEAN], [None, None, None, None, 'unavailable'])
        self.assertEqual(result['C'][MEAN], [None, None, None, None, 'unavailable'])
        self.assertEqual(result['B'][MEAN], [0, 40, 1, 50, 'ok'])

    def test_cpi_uses_nonzero_share_and_keeps_zero_in_denominator(self):
        result = aggregate('sci_cpi_auto_index', {'A': ['0'] * 20 + ['9'] * 20 + ['10'] * 20}, [CPI])
        self.assertEqual(result['A'][CPI], [0.5, 40, 2 / 3, 50, 'ok'])

    def test_share_numerator_and_complement_each_require_protection(self):
        result = aggregate('sci_credit_card_user', {
            'small_yes': ['1'] * 19 + ['0'] * 40,
            'small_no': ['1'] * 40 + ['0'] * 19,
            'safe': ['1'] * 20 + ['0'] * 20,
            'all_yes': ['1'] * 40,
            'all_no': ['0'] * 40,
        }, [BINARY])
        for ident in ('small_yes', 'small_no'):
            self.assertEqual(result[ident][BINARY], [None, None, None, None, 'suppressed'])
        self.assertEqual(result['safe'][BINARY][0], 0.5)
        self.assertEqual(result['all_yes'][BINARY][0], 1)
        self.assertEqual(result['all_no'][BINARY][0], 0)

    def test_categorical_suppression_cannot_be_reconstructed_from_other_codes(self):
        ids = ['sii_homeowner__code_' + code for code in ('H', 'R', 'U')]
        result = aggregate('sii_homeowner', {
            'small_code': ['H'] * 39 + ['R'] + ['U'] * 40,
            'complement': ['H'] * 40 + ['R'] * 20 + ['U'] * 20,
            'safe': ['H'] * 50 + ['R'] * 30 + ['U'] * 20,
        }, ids)
        for ident in ('small_code', 'complement'):
            for mid in ids:
                self.assertEqual(result[ident][mid], [None, None, None, None, 'suppressed'])
        self.assertEqual([result['safe'][mid][0] for mid in ids], [0.5, 0.3, 0.2])

    def test_digital_code_50_has_literal_domain_without_mean(self):
        mid = DIGITAL_FIELD + '__code_50'
        result = aggregate(DIGITAL_FIELD, {'A': ['0'] * 20 + ['50'] * 20 + ['11'] * 20}, [mid])
        self.assertEqual(result['A'][mid], [0.5, 40, 2 / 3, 50, 'ok'])

    def test_adult_codes_are_not_unique_household_counts_or_recoded(self):
        mid = ADULTS_FIELD + '__mean'
        result = aggregate(ADULTS_FIELD, {'A': ['1'] * 20 + ['9'] * 20 + ['0'] * 20}, [mid])
        self.assertEqual(result['A'][mid], [5, 40, 2 / 3, 50, 'ok'])

    def test_base_suppression_withholds_every_field_statistic(self):
        result = aggregate('sai_activist_score', {'A': ['10'] * 40, 'B': ['30'] * 40}, [MEAN],
                           statuses={'A': 'suppressed', 'B': 'unavailable'})
        self.assertEqual(result, {'A': {}, 'B': {}})

    def test_percentile_midrank_ties_and_partition_isolation(self):
        self.assertEqual(midrank_percentiles([0, 0, 10, 20]), [25, 25, 62.5, 87.5])
        self.assertEqual(midrank_percentiles([7, 7, 7]), [50, 50, 50])
        self.assertEqual(midrank_percentiles([]), [])
        first = aggregate('sai_activist_score', {'A': ['10'] * 20, 'B': ['20'] * 20}, [MEAN])
        separate = aggregate('sai_activist_score', {'A': ['10'] * 20}, [MEAN])
        self.assertEqual(first['A'][MEAN][3], 25)
        self.assertEqual(separate['A'][MEAN][3], 50)

    def test_every_allowed_measure_has_complete_cells_on_publishable_rows(self):
        data = {field: [''] * 40 for field in SOURCE_FIELDS}
        rows = [{'id': 'A', 'count': 40, 'status': 'ok'}]
        aggregate_measures(pd.DataFrame(data), pd.Series(['A'] * 40), [True] * 40, rows)
        self.assertEqual(set(rows[0]['measures']), set(MEASURE_BY_ID))
        self.assertTrue(all(cell == [None, None, None, None, 'unavailable']
                            for cell in rows[0]['measures'].values()))

    def test_snapshot_identity_includes_configuration_and_raw_source_hash(self):
        source = 'a' * 64
        first = snapshot_key(source)
        self.assertRegex(first, r'^co-cd005-[a-f0-9]{12}$')
        self.assertNotEqual(first, 'co-cd005-' + source[:12])
        self.assertEqual(first, snapshot_key(source))
        self.assertNotEqual(first, snapshot_key('b' * 64))
        with patch('tools.measures.METHOD_VERSION', 'changed-method'):
            self.assertNotEqual(first, snapshot_key(source))

    def test_optional_source_fields_preserve_default_and_duplicate_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'synthetic.csv'
            row = ['00001', '00002', '005'] + ['CO', '00123', '080410001001001', '0.5', '0.5', 'ROOFTOP'] * 2
            with path.open('w', encoding='cp1252', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(FIELDS + ['sai_activist_score'])
                writer.writerow(row + ['10'])
                writer.writerow(row + ['10'])
            default, receipt = read_profiles(path)
            self.assertEqual(list(default.columns), FIELDS)
            self.assertEqual(receipt['duplicate_rows_collapsed'], 1)
            selected, _ = read_profiles(path, extra_fields=['sai_activist_score'])
            self.assertEqual(selected.iloc[0]['sii_persist_id'], '00001')
            self.assertEqual(selected.iloc[0]['sai_activist_score'], '10')
            with path.open('a', encoding='cp1252', newline='') as stream:
                csv.writer(stream).writerow(row + ['20'])
            selected, receipt = read_profiles(path, extra_fields=['sai_activist_score'])
            self.assertEqual(len(selected), 0)
            self.assertEqual(receipt['conflicting_ids_quarantined'], 1)


if __name__ == '__main__':
    unittest.main()
