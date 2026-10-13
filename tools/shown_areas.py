"""The table of EVERY area, none withheld (Paul 2026-10-08: "nothing should ever be withheld").

The row framework computes every area's counts, means and shrunk means, then blanks the small ones when it writes the released
table. This tool recomputes them with the build's own no-floor code (tools/p1_nofloor_area_table.py, read-only: no draws, the frozen
folder is imported and never written) in one way: each area is shrunk with the between-area variance (tau2) the frozen run estimated
from the areas it published, so every value of an area that was already released stays bit for bit; standings and bands are ranked
over ALL areas of the level; likely ranges are the analytic empirical-Bayes interval. It stops, and writes nothing, unless every
released row is reproduced exactly.

  env -u PYTHONPATH PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 <system Python 3.12: pandas, pyarrow> -B tools/shown_areas.py --district co-05

Writes D.area_table(d) (area_scores_shown.csv; local only) and its .manifest.json.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True                  # the frozen folder is read and imported only
import numpy as np                              # noqa: E402
import pandas as pd                             # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import districts as D                # noqa: E402
from tools.glassbox import GlassBox, new_run_id    # noqa: E402

KEY = ['basis', 'level', 'group', 'area_id']
# a released row must come back identical in these (the standings and their ranges are re-ranked over all areas, so they differ)
SAME = ('n_rows', 'n_households', 'raw_mean', 'eb_mean', 'eb_se', 'shrink_B', 'measured_pct_mean', 'top10_share_raw',
        'top10_share_eb', 'top10_shrink_B', 'top10_eb_se', 'md_share_mean', 'n_cpi_blank', 'share_band_m2', 'share_band_m1',
        'share_band_p0', 'share_band_p1', 'share_band_p2', 'share_reg_D', 'share_reg_R', 'share_reg_U', 'share_band_undetermined')


def sha256_file(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def check_reproduced(released, shown):
    """Raise SystemExit unless every released row is in `shown` with the SAME columns equal to the bit (NaN only where NaN)."""
    m = released.merge(shown, on=KEY, suffixes=('_old', ''), how='left', validate='1:1')
    if not m['n_rows'].notna().all():
        raise SystemExit('FAILED: %d released rows are not in the shown table' % int(m['n_rows'].isna().sum()))
    bad = {}
    for c in SAME:
        a, b = m[c + '_old'], m[c]
        n = int(((a.notna() != b.notna()) | (a.notna() & (a != b))).sum())
        if n:
            bad[c] = n
    if bad:
        raise SystemExit('FAILED: released rows not reproduced exactly (rows differing per column): %s' % bad)
    return m


def bands_moved(m):
    """{'basis/level': released areas whose band or top-10 band changed with the new ranking} (the lean group has no standing)."""
    index = m[m.group != 'lean']
    moved = index[(index.band_old != index.band) | (index.top10_band_old != index.top10_band)]
    return {'%s/%s' % k: int(n) for k, n in moved.groupby(['basis', 'level']).area_id.nunique().items()}


def local_cache(cfg, d):
    """The frozen config names its cache by the Windows path it was built at; read the district's own cache instead (tools/districts.py,
    which tools/hostpaths.py can move: the container mounts it elsewhere). On the laptop that is the same file; its sha256 is unchanged."""
    cfg['inputs']['cache']['path'] = str(D.rowfw_cache(d))
    return cfg


def build(d, gb):
    b = D.rowfw_build(d)
    spec = importlib.util.spec_from_file_location('p1_nofloor_area_table', b / 'tools' / 'p1_nofloor_area_table.py')
    NF = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(b))
    spec.loader.exec_module(NF)
    AR = NF.AR
    cfg = local_cache(json.loads((b / 'framework_config.json').read_text(encoding='utf-8')), d)
    gb.stage('running', 'load', 'person scores and geography of %s (counts only)' % d['id'])
    geo, rows, chk = NF.load_inputs(cfg)
    if not (chk['point_stage_scores_equal_row_file_float32'] and chk['lean_band_equal_row_file']
            and max(v for k, v in chk.items() if k.endswith('max_err')) < 1e-3):
        raise SystemExit('FAILED: the point stage does not reproduce the row file: %s' % chk)
    canon, hh_c, nuniv, levels = NF.build_all_levels(geo, cfg)           # every area shown: standings over all of them
    released = pd.read_csv(NF.OLD_CSV, dtype={'area_id': str, 'parent_id': str}, keep_default_na=False, na_values=[''],
                           low_memory=False, float_precision='round_trip')   # an id may read 'nan' (people with no block or ZIP)
    published = {k: set(g.area_id) for k, g in released[released.group == 'cua'].groupby(['basis', 'level'])}
    mask = {lv.key: ~np.isin(lv.ids.astype(str), list(published.get(lv.key, ()))) for lv in levels}

    def frozen_tau2(f):                   # tau2 exactly as the frozen run estimated it: from the areas it published
        def g(y, lvls, icc, floor):
            keep = [lv.hidden for lv in lvls]
            for lv in lvls:
                lv.hidden = mask[lv.key]
            try:
                return f(y, lvls, icc, floor)
            finally:
                for lv, h in zip(lvls, keep):
                    lv.hidden = h
        return g
    AR.eb_mean_params, AR.eb_top10_params = frozen_tau2(AR.eb_mean_params), frozen_tau2(AR.eb_top10_params)
    gb.stage('running', 'table', 'shrinking every area with the frozen between-area variance')
    shown, _ = NF.make_table(geo, rows, cfg, canon, hh_c, nuniv, levels)
    m = check_reproduced(released, shown)
    receipt = json.loads((b / 'release' / 'P1_D2_suppression_counts.json').read_text(encoding='utf-8'))['D2_counts']
    areas = {'%s/%s' % k: int(n) for k, n in shown[shown.group == 'ovr'].groupby(['basis', 'level']).size().items()}
    if areas != {k: v['areas'] for k, v in receipt.items()} or not (shown.n_rows >= 1).all():
        raise SystemExit('FAILED: areas per level differ from the D2 receipt, or an area has no people: %s' % areas)
    return shown, {'areas_per_basis_level': areas, 'released_rows': len(released), 'newly_shown_rows': len(shown) - len(released),
                   'released_rows_differing': 0, 'released_areas_whose_band_moved': bands_moved(m),
                   'method': 'frozen_tau2_all_standings', 'range_method': 'analytic_eb_posterior_90',
                   'inputs': {'row_scores_sha256': sha256_file(b / 'row_scores_rowfw-1.0.0.parquet'),
                              'released_table_sha256': sha256_file(NF.OLD_CSV), 'cache_sha256': cfg['inputs']['cache']['sha256']},
                   'tool_sha256': sha256_file(Path(__file__))}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Write the table of every area (none withheld).')
    parser.add_argument('--district', choices=list(D.DISTRICTS), default=D.DEFAULT)
    d = D.get(parser.parse_args(argv).district)
    gb = GlassBox(new_run_id('cu-geo-v3-shown'), 'cu-geography-v3/tools/shown_areas.py')
    gb.run('running', 'start', 'shown-areas table for %s (counts only)' % d['id'])
    try:
        shown, facts = build(d, gb)
        out = D.area_table(d)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(str(out) + '.tmp', 'w', encoding='utf-8', newline='') as stream:
            shown.to_csv(stream, index=False, lineterminator='\n')
        os.replace(str(out) + '.tmp', out)
        facts.update(file=out.name, sha256=sha256_file(out), bytes=out.stat().st_size, rows=len(shown),
                     ruling='Paul 2026-10-08: nothing should ever be withheld (O-9)', use='local only; never exported or hosted')
        manifest = out.with_suffix('.manifest.json')
        manifest.write_text(json.dumps(facts, indent=1, sort_keys=True), encoding='utf-8', newline='\n')     # LF on Windows too, like the CSV
    except BaseException as error:
        gb.failed('build', 'shown-areas table stopped: %s: %s' % (type(error).__name__, error))
        raise
    gb.run('completed', 'complete', 'shown-areas table written: %d rows' % len(shown), bytes_=facts['bytes'])
    print(json.dumps({k: facts[k] for k in ('rows', 'sha256', 'newly_shown_rows', 'areas_per_basis_level')}, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
