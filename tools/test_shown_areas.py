"""tools/shown_areas.py fails closed: every released row must come back bit for bit. Synthetic tables, no real data.

    <system Python 3.12: pandas>  -m unittest tools.test_shown_areas          (from the V3 folder)
"""
import unittest

import numpy as np
import pandas as pd

from tools import shown_areas as SA


def table(ids, **override):
    """One 'cua' row per area id with every SAME column filled, plus the re-ranked standings."""
    rows = []
    for k, ident in enumerate(ids):
        row = {'basis': 'registration', 'level': 'block', 'group': 'cua', 'area_id': ident, 'band': 2.0, 'top10_band': 3.0,
               'area_pct': 10.0 * k, 'top10_area_pct': 20.0 * k}
        row.update({c: 0.1 * (k + 1) + 0.001 * i for i, c in enumerate(SA.SAME)})
        rows.append(row)
    frame = pd.DataFrame(rows)
    for column, values in override.items():
        frame[column] = values
    return frame


class LocalCacheTests(unittest.TestCase):
    def test_the_frozen_configs_windows_cache_path_becomes_the_districts_own_and_nothing_else_moves(self):
        cfg = {'inputs': {'cache': {'path': r'C:\\old\\place\\inputs.parquet', 'sha256': 'abc', 'rows': 3}, 'export': {'path': 'x'}}, 'fields': {}}
        d = SA.D.get('co-05')
        found = SA.local_cache(cfg, d)
        self.assertIs(found, cfg)
        self.assertEqual(cfg['inputs']['cache'], {'path': str(SA.D.rowfw_cache(d)), 'sha256': 'abc', 'rows': 3})
        self.assertEqual((cfg['inputs']['export'], cfg['fields']), ({'path': 'x'}, {}))
        self.assertEqual(SA.D.rowfw_cache(d).name, 'inputs.parquet')


class ReproducedTests(unittest.TestCase):
    def test_new_areas_and_re_ranked_standings_pass(self):
        released = table(['B1', 'B2'])
        shown = table(['B1', 'B2', 'B3'])                             # B3 was withheld
        shown['area_pct'] = [5.0, 50.0, 95.0]                           # every standing is ranked over all three areas
        shown['band'] = [1.0, 3.0, 5.0]
        merged = SA.check_reproduced(released, shown)
        self.assertEqual(len(merged), 2)                                # one merged row per released row
        self.assertEqual(SA.bands_moved(merged), {'registration/block': 2})       # B1 2 -> 1, B2 2 -> 3

    def test_the_lean_group_has_no_standing_and_never_counts_as_moved(self):
        released = pd.concat([table(['B1']), table(['B1']).assign(group='lean', band=np.nan, top10_band=np.nan)])
        shown = pd.concat([table(['B1']), table(['B1']).assign(group='lean', band=np.nan, top10_band=np.nan)])
        self.assertEqual(SA.bands_moved(SA.check_reproduced(released, shown)), {})

    def test_one_ulp_in_a_released_value_stops_it(self):
        released = table(['B1', 'B2'])
        shown = table(['B1', 'B2', 'B3'])
        shown.loc[1, 'eb_mean'] = np.nextafter(shown.loc[1, 'eb_mean'], 1.0)
        with self.assertRaisesRegex(SystemExit, r"not reproduced exactly.*'eb_mean': 1"):
            SA.check_reproduced(released, shown)

    def test_a_released_row_missing_from_the_shown_table_stops_it(self):
        with self.assertRaisesRegex(SystemExit, 'not in the shown table'):
            SA.check_reproduced(table(['B1', 'B2']), table(['B1']))

    def test_a_value_that_turns_blank_stops_it_but_two_blanks_agree(self):
        released = table(['B1', 'B2'], md_share_mean=[np.nan, 0.5])
        blank = table(['B1', 'B2'], md_share_mean=[np.nan, np.nan])
        with self.assertRaisesRegex(SystemExit, "'md_share_mean': 1"):
            SA.check_reproduced(released, blank)
        SA.check_reproduced(released, table(['B1', 'B2'], md_share_mean=[np.nan, 0.5]))      # NaN against NaN is not a difference

    def test_a_duplicate_shown_row_stops_it(self):
        with self.assertRaises(Exception):                              # merge(validate='1:1')
            SA.check_reproduced(table(['B1']), table(['B1', 'B1']))


if __name__ == '__main__':
    unittest.main()
