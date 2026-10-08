"""Terrier Phase 3b, Lane R: a district copy of Colorado's frozen rowfw-1.0.0, its input cache, freeze and P1 stages.

Colorado's row-framework folder (RF) is read and never written. `rowfw-copy` copies the code into
.local/<district>/rowfw (DR), changes only the declared one-line literals (same patch gate as copy_stage), and writes
district.json, which every patched literal reads. The other steps run the copied code as child processes of the system
Python 3.12 (pyarrow). Counts, hashes and paths only in every receipt and log. `rowfw-cleanup` removes the transient work
folder; the copy never runs `close` (it would summarise tests that did not run).
Phase 4B: a candidate district's copy takes candidate support as a fourth term of the overall (patch kind e; `rowfw-support` writes
cache/support.npz, `rowfw-reset` clears a finished run so the copy can be redone).
"""
import difflib
import json
import os
import subprocess
import time
from fractions import Fraction as F
from pathlib import Path

from . import copy_stage as CS
from . import originals
from .common import (CO_EXPORT, DISTRICTS, GLOBAL_DIR, L, LUNA, PY312, TER, Step, district_dir, log, read_json, sha256_file,
                     utc_now, write_json)

RF = L / 'row-framework-20260930'                   # Colorado's original (frozen, read-only)
RFB = RF / 'build' / 'rowfw-1.0.0'
RF_BEFORE = GLOBAL_DIR / 'rowfw-originals-before.json'
TERRIER = {'co-replay': 'co-05', 'or-05': 'or-05', 'ct-01': 'ct-01'}
LEGACY5 = ('sci_cpi_cc_index', 'sci_cpi_insurance_auto_index', 'sci_cpi_insurance_index', 'sci_cpi_internet_buy_index',
           'sci_cpi_online_pay_index')
GEO_COLUMNS = ('registration_block15', 'registration_tract11', 'svi_registration_precinct_code', 'svi_registration_zip',
               'sii_tract11', 'sii_zip', 'svi_registration_censusid', 'sii_censusid')
SCRATCH_CO = (r'C:\Users\pgoff\AppData\Local\Temp\claude\C--Users-pgoff-OneDrive-Desktop-Melange-Systems--claude-worktrees-'
              r'codex-websocket-error-468fa9\60d3bc55-b6fd-473b-9b78-a958ba7ee615\scratchpad')
CACHE_SHA_CO = 'cece066ca7e1886fc72abc1b43e82a24ae883ff61481699cc95a377ae8392cbc'
EXPORT_SHA_CO = 'a8dd3028edb9027250a9e3eae73f69cb67c0c753fc2c457fceb1476bbb13b0b0'


def dr(district):
    return district_dir(district).parent / 'rowfw'


def drb(district):
    return dr(district) / 'build' / 'rowfw-1.0.0'


def export_dir(district):
    return CO_EXPORT if DISTRICTS[district]['is_co'] else district_dir(district) / f'data-completion-{district}-luna-v1'


# ------------------------------------------------------------------ the patch table (kind a = district count/path/hash,
# b = a Colorado result pin kept active when district.json is_co, c = environment, d = no Colorado P0 baseline exists,
# e = the 4-term overall of a candidate district, Phase 4B)
def dj(k):
    """Expression that reads district.json from the copy's own location (parents[k] of the file is the district copy root)."""
    return ("__import__('json').loads(__import__('pathlib').Path(__file__).resolve().parents[%d]"
            ".joinpath('district.json').read_text(encoding='utf-8'))" % k)


_B = 'build/rowfw-1.0.0/'
_ROOT_LINE = "ROOT = pathlib.Path(r'%s')" % LUNA
_SCRATCH_LINE = "SCRATCH = pathlib.Path(r'%s')" % SCRATCH_CO
# Phase 4B (kind e): the candidate districts' 4-term overall. district.json holds 'overall' and 'support' only for them; every
# patched line takes the original expression for Colorado (a dict comprehension over the same three keys, `+ 0`, `+ 0.0`).
_OV = dj(3) + ".get('overall', {})"
_SUP = "np.load(cfg['overall'][cfg['overall']['default']]['support']['pct_file'])['sup_pct']"
PATCHES = {
    'build_cache.py': [
        ('SRC_DIR = r"%s"' % CO_EXPORT, "SRC_DIR = %s['export_dir']" % dj(0), 1, 'a'),
        ('    return pacsv.open_csv(', "    return __import__('tools.luna_district.rowfw', fromlist=['x']).district_batches(pacsv.open_csv(", 1, 'a'),
        ('quoted_strings_can_be_null=True))', 'quoted_strings_can_be_null=True)), HERE)', 1, 'a')],
    _B + 'rowfw/common.py': [
        (_ROOT_LINE, 'ROOT = pathlib.Path(__file__).resolve().parents[3]', 1, 'a'),
        ("RF = ROOT / '.local' / 'row-framework-20260930'", 'RF = ROOT', 1, 'a'),
        (_SCRATCH_LINE, "SCRATCH = ROOT / 'scratch'", 1, 'a')],
    _B + 'rowfw/summary.py': [('    N = 491308', "    N = %s['rows']" % dj(3), 1, 'a')],
    _B + 'rowfw_build.py': [("'populate': 6.0}", "'populate': 5.0}", 1, 'c')],      # CO measured 3.47 GB; the 6.0 floor was "not yet measured"
    _B + 'freeze/freeze_rowfw.py': [
        (_ROOT_LINE, 'ROOT = pathlib.Path(__file__).resolve().parents[3]', 1, 'a'),
        ("RF = ROOT / '.local' / 'row-framework-20260930'", 'RF = ROOT', 1, 'a'),
        ("GPU = ROOT / 'data-completion-gpu-model-20260930'", "GPU = pathlib.Path(%s['export_dir'])" % dj(3), 1, 'a'),
        (_SCRATCH_LINE, "SCRATCH = ROOT / 'scratch'", 1, 'a'),
        ("CACHE_SHA = '%s'" % CACHE_SHA_CO, "CACHE_SHA = %s['cache_sha256']" % dj(3), 1, 'a'),
        ("EXPORT_SHA = '%s'" % EXPORT_SHA_CO, "EXPORT_SHA = %s['export_sha256']" % dj(3), 1, 'a'),
        ('N = 491308', "N = %s['rows']" % dj(3), 1, 'a'),
        # party W: lean 0.0 and group "other", like G, L, O, T (D-4); Colorado's own config stays as it was (gate G1)
        ("'T': 0.0}", "'T': 0.0, **({} if %s['is_co'] else {'W': 0.0})}" % dj(3), 1, 'a'),
        ("'other': ['G', 'L', 'O', 'T']}", "'other': ['G', 'L', 'O', 'T'] + ([] if %s['is_co'] else ['W'])}" % dj(3), 1, 'a'),
        ("'rows': N, 'columns': 802}", "'rows': N, 'columns': len(pq.read_schema(CACHE).names)}", 1, 'a'),
        ("'cells_estimated_per_replicate_spec': 1722948,", "'cells_estimated_per_replicate_spec': %s['completed_values']," % dj(3), 1, 'a'),
        ("'rng.random(491308) float64; entry i belongs to model_source_row i + 1'",
         "'rng.random(' + str(N) + ') float64; entry i belongs to model_source_row i + 1'", 1, 'a'),
        ("    if not rc['within_1e-3']:", "    if not rc['within_1e-3'] and %s['is_co']:" % dj(3), 1, 'b'),
        ("    if not all(ev['matches_spec_5_1'].values()):", "    if not all(ev['matches_spec_5_1'].values()) and %s['is_co']:" % dj(3), 1, 'b'),
        ("OVR_DEFAULT = {'cua': F(2, 5), 'part': F(7, 20), 'soi': F(1, 4)}",
         "OVR_DEFAULT = {g: F(*v) for g, v in %s['overall']['weights'].items()} if 'overall' in %s else "
         "{'cua': F(2, 5), 'part': F(7, 20), 'soi': F(1, 4)}" % (dj(3), dj(3)), 1, 'e'),
        ("'default': 'W40_35_25',", "'default': %s.get('key', 'W40_35_25')," % _OV, 1, 'e'),
        ("'W40_35_25': {'formula': 'ovr_score = 0.40 * cua_pct + 0.35 * part_pct + 0.25 * soi_pct',",
         "%s.get('key', 'W40_35_25'): {'formula': %s.get('formula', 'ovr_score = 0.40 * cua_pct + 0.35 * part_pct + 0.25 * soi_pct')," % (_OV, _OV), 1, 'e'),
        ("{g: W(OVR_DEFAULT[g]) for g in ('cua', 'part', 'soi')}", '{g: W(OVR_DEFAULT[g]) for g in OVR_DEFAULT}', 1, 'e'),
        ("'standing': 'ovr_pct = midrank standing of ovr_score',",
         "**({'support': %s['support']} if 'overall' in %s else {}), 'standing': 'ovr_pct = midrank standing of ovr_score'," % (dj(3), dj(3)), 1, 'e'),
        ("'status': S_PAUL + ' (PR1: \"40/35/25 lobbyist\")'},",
         "'status': %s['status'] if 'overall' in %s else S_PAUL + ' (PR1: \"40/35/25 lobbyist\")'}," % (_OV, dj(3)), 1, 'e'),
        ("'D1': {'default': 'W40_35_25 overall (PR1, Paul ruling); lean a filter and colour',",
         "'D1': {'default': (%s['key'] + ' overall (O-11, Paul ruling 2026-10-07); candidate support is a weight') if 'overall' in %s "
         "else 'W40_35_25 overall (PR1, Paul ruling); lean a filter and colour'," % (_OV, dj(3)), 1, 'e'),
        ("add('Overall weights', {'cua': 0.40, 'part': 0.35, 'soi': 0.25},", "add('Overall weights', {g: float(v) for g, v in OVR_DEFAULT.items()},", 1, 'e'),
        ("'overall': {'cua': 0.40, 'part': 0.35, 'soi': 0.25}},", "'overall': {g: float(v) for g, v in OVR_DEFAULT.items()}},", 1, 'e')],
    _B + 'rowfw/engine.py': [
        ("for k, v in cfg['overall']['W40_35_25']['weights'].items()},", "for k, v in cfg['overall'][cfg['overall']['default']]['weights'].items()},", 1, 'e'),
        ("    w = cfg['overall']['W40_35_25']['weights']", "    w = cfg['overall'][cfg['overall']['default']]['weights']", 1, 'e'),
        ("+ float(w['soi']['value']) * out['soi_measured_pct'])",
         "+ float(w['soi']['value']) * out['soi_measured_pct'] + float(w.get('sup', {'value': 0.0})['value']) * 100.0)", 1, 'e'),     # support is fully observed
        ("    return {'ovr_score': w['cua'] * cua_pct + w['part'] * part_pct + w['soi'] * soi_pct,",
         "    return {'ovr_score': w['cua'] * cua_pct + w['part'] * part_pct + w['soi'] * soi_pct + (w['sup'] * %s if 'sup' in w else 0), "
         "**({'sup_pct': %s} if 'sup' in w else {})," % (_SUP, _SUP), 1, 'e')],
    _B + 'rowfw/draws.py': [
        ("ovr = w['cua'] * pct['cua'] + w['part'] * pt.cols['part_pct'] + w['soi'] * pct['soi']",
         "ovr = w['cua'] * pct['cua'] + w['part'] * pt.cols['part_pct'] + w['soi'] * pct['soi'] + (w['sup'] * pt.cols['sup_pct'] if 'sup' in w else 0)", 1, 'e'),
        ("w['cua'] * pc + w['part'] * pt.cols['part_pct'] + w['soi'] * ps)",
         "w['cua'] * pc + w['part'] * pt.cols['part_pct'] + w['soi'] * ps + (w['sup'] * pt.cols['sup_pct'] if 'sup' in w else 0))", 1, 'e')],
    _B + 'rowfw/outputs.py': [
        ("st.ovr_w[g] * cols[f'{g}_pct'][canon] for g in ('cua', 'part', 'soi')}", "st.ovr_w[g] * cols[f'{g}_pct'][canon] for g in st.ovr_w}", 1, 'e')],
    # T03(c)'s exact-arithmetic diagnosis of near pairs (run by finalize whenever a pair is closer than eps): the exact overall of the default key,
    # with the exact value of the support standing as the last term for a candidate district
    _B + 'rowfw/diagnostics.py': [
        ("w = cfg['overall']['W40_35_25']['weights']", "w = cfg['overall'][cfg['overall']['default']]['weights']", 1, 'e'),
        ("return _fw(w['cua']) * a + _fw(w['part']) * b + _fw(w['soi']) * c",
         "return _fw(w['cua']) * a + _fw(w['part']) * b + _fw(w['soi']) * c + (_fw(w['sup']) * F(float(pt.cols['sup_pct'][i])) if 'sup' in w else 0)", 1, 'e')],
    'design/clarity_spec_facts.py': [
        ("ROOT = r'%s'" % LUNA, "ROOT = str(__import__('pathlib').Path(__file__).resolve().parents[1])", 1, 'a'),
        (r"CACHE = ROOT + r'\.local\row-framework-20260930\cache\inputs.parquet'", r"CACHE = ROOT + r'\cache\inputs.parquet'", 1, 'a'),
        (r"DICT = ROOT + r'\data-completion-gpu-model-20260930\column_dictionary.json'",
         "DICT = %s['export_dir'] + r'\\column_dictionary.json'" % dj(1), 1, 'a')],
    _B + 'tools/p1_d2_suppression_counts.py': [
        ('int(p0_pub[k])', 'int(p0_pub.get(k, -1))', 1, 'd')],                      # a district has no P0 area_counts.csv (D1 level set)
    # pandas 3 keeps a blank id as NaN through astype(str) and np.unique then fails; pandas 2 made it the text 'nan', the pseudo-area
    # build_bundle_v4.drop_pseudo_areas expects. Colorado has no blank area id (0 of 491,308 on all seven area columns), so its run is unchanged.
    _B + 'rowfw/areas.py': [
        ('vals = df[col].astype(str).to_numpy(dtype=object)[canon]', "vals = df[col].astype(str).fillna('nan').to_numpy(dtype=object)[canon]", 1, 'c')],
}
COPY_DIRS = ('rowfw', 'tools', 'tests/fixtures', 'tests/predicates')
COPY_FILES = ('rowfw_build.py', 'freeze/freeze_rowfw.py', 'freeze/rulings_verbatim.txt', 'docs/data_dictionary.json',
              'docs/DATA_DICTIONARY.md', 'PREREG_2026_GENERAL.md', 'RULINGS.md', 'DEVIATIONS.md', 'CHANGELOG.md')
COPY_RF_FILES = ('build_cache.py', 'design/SPEC_DRAFT.md', 'design/clarity_spec_facts.py')


def copy_list(rf=RF):
    """Paths (relative to rf, forward slashes) of everything copied. Outputs and frozen files of Colorado's run are not."""
    out = list(COPY_RF_FILES)
    rfb = rf / 'build' / 'rowfw-1.0.0'
    for d in COPY_DIRS:
        out += sorted(_B + p.relative_to(rfb).as_posix() for p in (rfb / d).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    return out + [_B + f for f in COPY_FILES]


def apply_patches(rel, text, table=None):
    """copy_stage.apply_patches on this module's table: each old string occurs exactly `count` times, no patch adds a line."""
    changed = set()
    for old, new, count, kind in (PATCHES if table is None else table).get(rel, []):
        assert text.count(old) == count, f'{rel}: expected {count} occurrence(s) of {old[:60]!r}, found {text.count(old)}'
        assert '\n' not in new, 'a patch may not add lines'
        changed.update(i + 1 for i, line in enumerate(text.splitlines()) if old in line)
        text = text.replace(old, new)
    return text, changed


def district_json(district, cache_sha=None):
    cfg = DISTRICTS[district]
    manifest = read_json(export_dir(district) / 'manifest.json')
    out = {'id': district, 'terrier_district': TERRIER[district], 'is_co': cfg['is_co'], 'rows': cfg['rows'],
           'households': cfg['households'], 'completed_values': cfg['completed_values'], 'export_dir': str(export_dir(district)),
           'export_sha256': manifest['files']['synthetic_completed.csv.gz']['sha256'], 'cache_sha256': cache_sha}
    d = _district(district)
    if d['candidate']:                       # Phase 4B: the 4-term overall (the e patches read these two keys); Colorado has neither
        w = d['score_weights']
        out['overall'] = {'key': 'W' + '_'.join(str(v) for v in w.values()), 'weights': {g: [v, 100] for g, v in w.items()},
                          'formula': 'ovr_score = ' + ' + '.join('%.2f * %s_pct' % (v / 100, g) for g, v in w.items()),
                          'status': 'Paul ruling 2026-10-07 (O-11: candidate support counts in the overall, %s)' % '/'.join(str(v) for v in w.values())}
        out['support'] = {'pct_file': str(dr(district) / 'cache' / 'support.npz'), 'sha256': None, 'candidate': d['candidate'],
                          'party': d['party'], 'race_mode': d['race_mode'], 'signals': d['support']['signals']}
    return out


def keep_pins(cfg, old):
    """A redone copy keeps the shas the cache and support steps wrote. The support file is not recomputed after the freeze: np.savez stamps
    the time in the zip, so a second write has another sha and the frozen config no longer pins it."""
    cfg['cache_sha256'] = old.get('cache_sha256')
    if 'support' in cfg:
        cfg['support']['sha256'] = old.get('support', {}).get('sha256')
    return cfg


def _rulings_entry(label, overall=None):
    return (f'\n## 2026-10-07: Terrier Phase 3b, {label} (a district copy of this build)\n\n'
            'This folder is a copy of the Colorado rowfw-1.0.0 code run on the district\'s own completed export. The R1 "Go, '
            'populate" above is Colorado\'s. For this district the go is Paul\'s approval of the multi-district build '
            '(Terrier Phase 3b), as ruled by the lead on 2026-10-07 (Q-1). R2 to R10 apply unchanged; none is re-asked.\n'
            + (f'\nPaul ruled on 2026-10-07 (O-11) that candidate support counts in this district\'s overall: {overall["key"]}. '
               'Colorado has no candidate and keeps 40/35/25.\n' if overall else ''))


def _deviations_entry(label, overall=None):
    return (f'\n## D-3b (2026-10-07): the district copy for {label}\n\n'
            'The method code is Colorado\'s, byte for byte, except the one-line patches in `copy-manifest.json` and '
            '`copy-diff.patch` (paths, counts and hashes read from `district.json`).\n\n'
            '- Party code W: lean 0.0 and registration group "other", like G, L, O and T (Phase 3b ruling Q-4). The freeze stops on any other unmapped code.\n'
            + ('- Overall (Phase 4B, O-11): ' + overall['key'] + ' with candidate support as the fourth term. `sup_pct` is read from `cache/support.npz` '
               '(tools/support.py, general mode; its sha256 is pinned in the frozen config) by 18 one-line patches (kind e); Colorado\'s branch of each is '
               'the original expression. The T03 exact-arithmetic diagnosis (`diagnostics.py`) is among them: finalize runs it whenever two scores are closer than eps. '
               'Left as text, run by no stage here: the copied data dictionary text, the synthetic overall example, '
               'and the docs, sensitivity and T01 readers of `W40_35_25` (see `residual-literals.txt`).\n' if overall else
               '- Overall: Colorado\'s 40/35/25. Candidate support does not exist before Phase 4B, so the app shows this overall as interim.\n') +
            '- Tests: only T00 (the input contract) runs for this district. T01 to T18 are not run; the method code is Colorado\'s, where they ran. '
            'The Colorado ridit pin and the Colorado spec-evidence pin of the freeze are recorded, not enforced.\n'
            '- Geography: the precinct id is the district\'s precinct key (OR county-code, CT code-name); Connecticut block ids are normalised to the '
            'planning-region id. A person with no registration block gets the residence block as the registration block, marked estimated '
            '(`cache/registration_geo_estimated.npy`, count in the `rowfw-cache` receipt); a person with no geography at all keeps a blank area.\n'
            '- Map-check sample (`model_audited_tract`): rebuilt from the district street store by the Colorado rule, without the earlier '
            'stage\'s tract list Colorado also intersected with.\n'
            '- Column dictionary: five legacy fields now list `__p_nonzero` as their completion probability column, as Colorado\'s does '
            '(`rowfw-dict`; the CSV is unchanged).\n'
            '- `populate` RAM floor 6.0 to 5.0 GB (Colorado measured 3.47 GB). `p1_d2_suppression_counts.py` compares with the district\'s own '
            '`area_counts.csv` (no P0 run exists), so its `D1_reproduces_P0_area_counts_csv` flag is informational.\n'
            '- Area ids: a blank id (a person with no block, tract or ZIP) is the text `nan` in `areas.py`, as pandas 2 made it and as '
            '`build_bundle_v4.drop_pseudo_areas` expects (pandas 3 kept NaN and `np.unique` failed). Colorado has no blank area id.\n'
            '- Colorado literals left as text, read by no stage that runs: the per-field expected estimated rows and statuses, the Colorado judge and '
            'study ridits, the spec evidence counts, the T10 tilt block sizes and the spec level counts.\n')


def build_copy(dest, district_cfg, label, rf=RF):
    """Copy the code to `dest` (the district root), apply the patches, check the line gate, compile. Returns the manifest."""
    dest = Path(dest)
    files, diff, residual = {}, [], []
    todo = copy_list(rf)
    unused = set(PATCHES) - set(todo)
    assert not unused, f'patch table names files that are not copied: {sorted(unused)}'
    for rel in todo:
        src, target = rf / rel, dest / rel
        original = src.read_bytes()
        target.parent.mkdir(parents=True, exist_ok=True)
        if not rel.endswith('.py'):
            target.write_bytes(original)
            files[rel] = {'original_sha256': sha256_file(src), 'copy_sha256': sha256_file(target), 'patched_lines': []}
            continue
        text = original.decode('utf-8')
        patched, declared = apply_patches(rel, text)
        CS.line_gate(text, patched, declared)
        compile(patched, str(target), 'exec')
        target.write_bytes(patched.encode('utf-8'))
        files[rel] = {'original_sha256': sha256_file(src), 'copy_sha256': sha256_file(target), 'patched_lines': sorted(declared)}
        if declared:
            diff.extend(difflib.unified_diff(text.splitlines(True), patched.splitlines(True), 'a/' + rel, 'b/' + rel, n=0))
        residual += [f'{rel}:{i}: [{m.group(0)}]' for i, line in enumerate(patched.splitlines(), 1) for m in CS.CENSUS.finditer(line)]
    rfb = dest / _B.rstrip('/')
    overall = district_cfg.get('overall')
    for name, entry in (('RULINGS.md', _rulings_entry(label, overall)), ('DEVIATIONS.md', _deviations_entry(label, overall))):
        with open(rfb / name, 'a', encoding='utf-8', newline='') as f:
            f.write(entry)
        files[_B + name]['appended_sha256'] = sha256_file(rfb / name)
    (dest / 'copy-diff.patch').write_text(''.join(diff), encoding='utf-8')
    (dest / 'residual-literals.txt').write_text('\n'.join(residual) + '\n', encoding='utf-8')
    write_json(dest / 'district.json', district_cfg)
    manifest = {'created_utc': utc_now(), 'district': district_cfg['id'], 'files': files, 'residual_literal_count': len(residual),
                'patched_files': sum(1 for v in files.values() if v['patched_lines']),
                'patched_lines': sum(len(v['patched_lines']) for v in files.values()),
                'district_json_sha256_at_copy': sha256_file(dest / 'district.json')}
    write_json(dest / 'copy-manifest.json', manifest)
    return manifest


# ------------------------------------------------------------------ step wrapper: Luna originals + Colorado's RF are hash-guarded,
# a child process dies with its step
def is_crash(rc):
    return rc < 0 or rc >= 0xC0000000


class RowfwStep(Step):
    def __init__(self, district, name, **kw):
        super().__init__(district, name, **kw)
        self.children = []

    def _rf_check(self):
        if not RF_BEFORE.exists():                  # the first rowfw step of any district takes the snapshot, once
            originals.snapshot(RF_BEFORE, roots=[RF])
        originals.check_stat(strict=True, before_path=RF_BEFORE, roots=[RF])

    def __enter__(self):
        super().__enter__()
        self._rf_check()
        return self

    def __exit__(self, etype, exc, tb):
        failed = None
        if etype is None:
            try:
                self._rf_check()
            except Exception as error:          # noqa: BLE001
                failed = error
        if failed is not None:
            super().__exit__(type(failed), failed, failed.__traceback__)
            raise failed
        return super().__exit__(etype, exc, tb)

    def _finish(self, status, detail):           # also runs from the watchdog thread before os._exit: leave no orphan child
        for p in self.children:
            if p.poll() is None:
                p.kill()
        super()._finish(status, detail)


WMI_OFF = "import platform,runpy,sys; platform._wmi_query=lambda *a,**k:(_ for _ in ()).throw(OSError('wmi disabled')); "


def run_code(st, code, cwd, pythonpath=None, out=None):
    """PY312 child (WMI query off); output to logs/<step>.log, or stdout to `out`; one rerun after a native crash (0xC000070A)."""
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONIOENCODING': 'utf-8', 'PYTHONNOUSERSITE': '1',
           'OMP_NUM_THREADS': '2', 'OPENBLAS_NUM_THREADS': '2'}
    env.pop('PYTHONPATH', None)
    if pythonpath:
        env['PYTHONPATH'] = str(pythonpath)
    logfile = st.D / 'logs' / f'{st.name}.log'
    for attempt in (1, 2):
        with open(logfile, 'ab') as lf, (open(out, 'wb') if out else lf) as sink:
            lf.write(f'\n=== {utc_now()} attempt {attempt}\n'.encode())
            lf.flush()
            p = subprocess.Popen([str(PY312), '-B', '-X', 'faulthandler', '-c', WMI_OFF + code], cwd=str(cwd), env=env, stdout=sink, stderr=lf)
            st.children.append(p)
            rc = p.wait()
        if rc == 0 or not is_crash(rc):
            break
        time.sleep(5)
    if rc != 0:
        raise RuntimeError(f'child exited {rc:#x} (see logs/{st.name}.log)')


def run_script(st, script, argv, cwd, **kw):
    run_code(st, f"sys.argv={[str(script), *map(str, argv)]!r}; runpy.run_path({str(script)!r}, run_name='__main__')", cwd, **kw)


def _build(st, stage, extra=()):
    """One rowfw_build.py release stage in the copy (P1: the go is in the copied RULINGS.md, the P0 snapshot is `rowfw-p1snap`)."""
    run_script(st, drb(st.district) / 'rowfw_build.py', ['--mode', 'release', '--phase', 'P1', '--stage', stage, *extra], drb(st.district))
    receipts = read_json(drb(st.district) / 'release' / 'stage_receipts.json')
    last = [r for r in receipts if r['stage'] == stage][-1]
    st.note(child_seconds=last['runtime']['seconds'], child_peak_gb=last['runtime']['peak_working_set_gb_process'])
    return last['result']


def _district(district):
    import tools.districts as D
    return D.get(TERRIER[district])


# ------------------------------------------------------------------ steps
def copy(district, redo=False, **_):
    R = dr(district)
    frozen = drb(district) / 'PLAN.receipt.json'
    if frozen.exists() and not redo:
        raise SystemExit(f'{district}: the plan is frozen (PLAN.receipt.json); a code fix after the freeze needs --redo')
    with RowfwStep(district, 'rowfw-copy', outputs=[R / 'copy-manifest.json'], redo=redo) as st:
        cfg = district_json(district)
        if (R / 'district.json').exists():
            cfg = keep_pins(cfg, read_json(R / 'district.json'))
        manifest = build_copy(R, cfg, DISTRICTS[district]['label'])
        if frozen.exists():           # a redo after the freeze may change only code the freeze did not read: no outputs are copied, so those stay
            receipt = read_json(frozen)
            for key, path in (('freeze_script', drb(district) / 'freeze' / 'freeze_rowfw.py'),
                              ('rulings_source', drb(district) / 'freeze' / 'rulings_verbatim.txt')):
                assert sha256_file(path) == receipt[key]['sha256'], f'{key} differs from the frozen plan: re-freeze instead of a redo'
            st.note(redo_after_freeze=True)
        st.note(files=len(manifest['files']), patched_files=manifest['patched_files'], patched_lines=manifest['patched_lines'],
                residual_literals=manifest['residual_literal_count'], rowfw_originals_before=sha256_file(RF_BEFORE))


def fix_dictionary(dictionary, expected=LEGACY5, header=None):
    """B-1: a field without completion_probability_columns takes its reference_probability_columns (Colorado's convention).
    Returns the changed field names; exactly `expected` must change, and every new column must be in `header`."""
    changed = [f for f, spec in dictionary['fields'].items() if not spec['completion_probability_columns']]
    assert sorted(changed) in ([], sorted(expected)), f'unexpected fields without a completion probability column: {changed}'
    for f in changed:
        spec = dictionary['fields'][f]
        spec['completion_probability_columns'] = list(spec['reference_probability_columns'])
        assert spec['completion_probability_columns'] and (header is None or set(spec['completion_probability_columns']) <= set(header)), f
    return changed


def dict_fix(district, redo=False, **_):
    """B-1 one-off (Q-5): rewrite the export's column_dictionary.json and its manifest entry; the CSV is not touched."""
    import gzip
    E = export_dir(district)
    dpath, mpath = E / 'column_dictionary.json', E / 'manifest.json'
    with RowfwStep(district, 'rowfw-dict', redo=redo) as st:
        before = {p.name: sha256_file(p) for p in (dpath, mpath)}
        dictionary = read_json(dpath)
        format_preserved = json.dumps(dictionary, indent=2, allow_nan=False) == dpath.read_text(encoding='utf-8')   # write_json's layout
        with gzip.open(E / 'synthetic_completed.csv.gz', 'rt', encoding='utf-8', newline='') as f:
            header = f.readline().rstrip('\r\n').split(',')
        changed = fix_dictionary(dictionary, header=header)
        co = read_json(CO_EXPORT / 'column_dictionary.json')['fields']
        same = all(dictionary['fields'][f]['completion_probability_columns'] == co[f]['completion_probability_columns'] for f in co)
        assert same, 'the completion probability columns differ from Colorado\'s'
        if changed:
            write_json(dpath, dictionary)
            manifest = read_json(mpath)
            manifest['files']['column_dictionary.json'] = {'bytes': dpath.stat().st_size, 'sha256': sha256_file(dpath)}
            write_json(mpath, manifest)
        after = {p.name: sha256_file(p) for p in (dpath, mpath)}
        st.note(fields_changed=len(changed), equals_colorado_lists=same, layout_preserved=format_preserved, sha256_before=before, sha256_after=after,
                csv_sha256_unchanged=read_json(mpath)['files']['synthetic_completed.csv.gz']['sha256'])
    if changed:
        ledger = district_dir(district) / 'ledger.json'
        entries = read_json(ledger) if ledger.exists() else []
        entries += [{'step': 'rowfw-dict', 'path': str(p), 'bytes': p.stat().st_size, 'sha256': after[p.name], 'utc': utc_now()}
                    for p in (dpath, mpath)]
        write_json(ledger, entries)


def audited_tracts(tract, mapped, household, groups, minimum=500):
    """The map-check sample (R-10), Colorado's rule of area_benchmark/audit_areas: the registration tract of a row that is mapped to
    the street store, whose household and whose connected group lie in one tract, in a tract left with >= `minimum` such rows; '' else."""
    import numpy as np
    import pandas as pd
    df = pd.DataFrame({'t': np.asarray(tract, dtype=object), 'h': np.asarray(household, dtype=object), 'g': np.asarray(groups),
                       'm': np.asarray(mapped, dtype=bool)})
    spans = df[df.m].groupby('h').t.nunique()
    ok = df.m & ~df.h.isin(spans[spans > 1].index) & (df.t != '')
    spans = df[ok].groupby('g').t.nunique()
    ok &= ~df.g.isin(spans[spans > 1].index)
    counts = df[ok].t.value_counts()
    ok &= df.t.isin(counts[counts >= minimum].index)
    return np.where(ok, df.t, '').astype('<U11')


def audited(district, redo=False, **_):
    """R-10: write cache/model_audited_tract.npy (length N, indexed by source row - 1) from the district CSV, street store and groups."""
    import hashlib
    import hmac
    import sqlite3
    import numpy as np
    import pandas as pd
    import tools.districts as D
    cfg, d = DISTRICTS[district], _district(district)
    out = dr(district) / 'cache' / 'model_audited_tract.npy'
    with RowfwStep(district, 'rowfw-audited', outputs=[out], heavy=True, redo=redo) as st:
        aliases = D.block_aliases(d)
        if d['block_regions'] and not aliases:
            raise SystemExit('Connecticut block aliases are missing (prepare_geography writes block_aliases.json first)')
        frame = pd.read_csv(d['source_csv'], encoding='cp1252', dtype=str, keep_default_na=False, na_filter=False,
                            usecols=['sii_persist_id', 'sii_household_id', 'svi_registration_censusid']).apply(lambda s: s.str.strip())
        assert len(frame) == cfg['rows'], len(frame)
        block = frame.svi_registration_censusid.map(lambda v: aliases.get(v, v))
        block = block.where(block.str.fullmatch(r'\d{15}'), '')
        salt = D.street_salt(d).read_bytes()
        with sqlite3.connect((D.data_dir(d) / 'street.sqlite3').resolve().as_uri() + '?mode=ro', uri=True) as db:
            assert json.loads(db.execute("SELECT value FROM metadata WHERE key='source_sha256'").fetchone()[0]) == d['sha256']
            allowed = {r[0] for r in db.execute("SELECT p.id FROM people p JOIN households h ON h.id=p.household_id WHERE h.basis='registration'")}
        opaque = lambda v: 'p_' + hmac.new(salt, json.dumps(['p', v], ensure_ascii=False, separators=(',', ':')).encode(), hashlib.sha256).hexdigest()
        mapped = frame.sii_persist_id.map(opaque).isin(allowed).to_numpy()
        household = frame.sii_household_id.where(frame.sii_household_id.ne(''), 'missing:' + frame.sii_persist_id).to_numpy()
        groups = np.load(district_dir(district) / '.local' / 'individual-validation-20260928' / 'labels-and-splits.npz')['groups']
        assert len(groups) == len(frame)
        tract = audited_tracts(block.str[:11].to_numpy(), mapped, household, groups)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(str(out) + '.partial', 'wb') as f:
            np.save(f, tract)
        os.replace(str(out) + '.partial', out)
        st.note(rows=len(tract), mapped_to_street=int(mapped.sum()), no_registration_block=int((block == '').sum()),
                audited_rows=int((tract != '').sum()), audited_tracts=int(len(set(tract.tolist()) - {''})))


# ------------------------------------------------------------------ the build_cache.py hook
def transform_batch(batch, d, aliases, audited, filled):
    """The district's geography keys on one export batch: precinct key, Connecticut block aliases, the residence block as the
    registration block where that has none (marked estimated), the map-check sample. `filled` collects the estimated rows."""
    import numpy as np
    import pyarrow as pa
    import pyarrow.compute as pc
    import tools.districts as D
    col, new = batch.column, {}
    reg, res = col('svi_registration_censusid'), col('sii_censusid')
    if aliases:
        keys, vals = pa.array(list(aliases), pa.string()), pa.array(list(aliases.values()), pa.string())
        reg, res = (pc.coalesce(pc.take(vals, pc.index_in(a, value_set=keys)), a) for a in (reg, res))
    ok = lambda a: pc.fill_null(pc.match_substring_regex(a, r'^[0-9]{15}$'), False)
    need = pc.and_(pc.invert(ok(reg)), ok(res))
    filled.extend(pc.filter(pc.cast(col('model_source_row'), pa.int64()), need).to_pylist())
    new['svi_registration_censusid'], new['sii_censusid'] = pc.if_else(need, res, reg), res
    code, county, name = (col(n).to_pylist() for n in ('svi_registration_precinct_code', 'svi_registration_county', 'svi_registration_precinct_name'))
    new['svi_registration_precinct_code'] = pa.array([D.precinct_key(d, c, k, n) or None for c, k, n in zip(code, county, name)], pa.string())
    if audited is not None:
        t = audited[pc.cast(col('model_source_row'), pa.int64()).to_numpy(zero_copy_only=False) - 1]
        new['model_audited_tract'] = pa.array(t.astype(object), pa.string(), mask=(t == ''))
    for name_, array in new.items():
        batch = batch.set_column(batch.schema.get_field_index(name_), name_, array)
    return batch


def district_batches(reader, root):
    """build_cache.py reads the export through this generator. Colorado (is_co) passes the batches through unchanged; a district
    gets its geography keys (transform_batch). After a full pass the estimated rows go to cache/registration_geo_estimated.npy."""
    import numpy as np
    root = Path(root)
    cfg = read_json(root / 'district.json')
    if cfg['is_co']:
        yield from reader
        return
    import tools.districts as D
    d = D.get(cfg['terrier_district'])
    aliases = D.block_aliases(d)
    if d['block_regions'] and not aliases:
        raise SystemExit('Connecticut block aliases are missing (prepare_geography writes block_aliases.json first)')
    sidecar = root / 'cache' / 'model_audited_tract.npy'
    audited = np.load(sidecar) if sidecar.exists() else None
    filled = []
    for batch in reader:
        yield transform_batch(batch, d, aliases, audited, filled)
    np.save(root / 'cache' / 'registration_geo_estimated.npy', np.asarray(sorted(filled), dtype=np.int32))


def cache(district, redo=False, **_):
    R = dr(district)
    out, cols = R / 'cache' / 'inputs.parquet', R / 'cache' / 'columns.json'
    cfg = DISTRICTS[district]
    with RowfwStep(district, 'rowfw-cache', outputs=[out, cols, R / 'cache' / 'README.md'], heavy=True, redo=redo) as st:
        out.parent.mkdir(exist_ok=True)
        run_script(st, R / 'build_cache.py', [], R, pythonpath=TER)
        doc = read_json(cols)
        assert (doc['cache']['rows'], doc['cache']['columns'], doc['counts']['households']) == (cfg['rows'], 802, cfg['households']), doc['cache']
        sha = sha256_file(out)
        meta = read_json(R / 'district.json')
        write_json(R / 'district.json', {**meta, 'cache_sha256': sha})
        import numpy as np
        estimated = len(np.load(R / 'cache' / 'registration_geo_estimated.npy')) if not cfg['is_co'] else 0
        st.note(cache_sha256=sha, rows=doc['cache']['rows'], columns=doc['cache']['columns'], households=doc['counts']['households'],
                registration_block_estimated_from_residence=estimated,
                geography_missing_counts={k['name']: k['missing_count'] for k in doc['kept'] if k['name'] in GEO_COLUMNS})


# ------------------------------------------------------------------ Phase 4B: candidate support and the redo of the run with the 4-term overall
RESET_GLOBS = ('framework_config.json', 'criteria_rowfw-1.0.0.json', 'PLAN_rowfw-1.0.0.md', 'PLAN.receipt.json', 'area_scores_rowfw-1.0.0.csv',
               '*_rowfw-1.0.0.parquet', '*_rowfw-1.0.0.manifest.json', 'summaries', 'release', 'tests/T*.json', 'tests/predicate_io',
               'tests/superseded')


def move_outputs(B, dest):
    """Move the run outputs under `B` (RESET_GLOBS: the frozen files, the stage outputs, the tests' results) to `dest`; the code, the
    cache and everything else stay. Returns the moved paths relative to `B`."""
    moved = []
    for pattern in RESET_GLOBS:
        for p in sorted(Path(B).glob(pattern)):
            target = Path(dest) / p.relative_to(B)
            target.parent.mkdir(parents=True, exist_ok=True)
            p.replace(target)
            moved.append(p.relative_to(B).as_posix())
    return moved


def reset(district, redo=False, **_):
    """The run outputs of an earlier freeze go to superseded/<tag>/ so that `rowfw-copy --redo` and the later steps run afresh (nothing is deleted)."""
    R = dr(district)
    with RowfwStep(district, 'rowfw-reset') as st:
        dest = R / 'superseded' / ('3b-' + utc_now().replace(':', ''))
        moved = move_outputs(drb(district), dest)
        st.note(moved=len(moved), superseded_dir=str(dest) if moved else None, paths=moved)


def write_support(cache, out, d):
    """Read the support inputs of the cache, check the score orientation, score every row and write `out` (npz of model_source_row, sup_score,
    sup_pct, in_universe). Returns aggregates only."""
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq
    import tools.support as S
    names = pq.read_schema(cache).names
    want = ['model_source_row', *S.INPUT_COLUMNS, *S.ballot_columns(names, d['support']['ballot_years'])]
    table = pq.read_table(cache, columns=want)
    msr = table.column('model_source_row').to_numpy().astype(np.int64)
    assert (np.diff(msr) > 0).all(), 'model_source_row is not strictly increasing in the cache'
    # a dictionary column must be decoded by cast: to_numpy() of one turns a null into its first dictionary entry
    text = lambda col: col.cast(pa.string()) if pa.types.is_dictionary(col.type) else col
    cols = {c: text(table.column(c)).to_numpy(zero_copy_only=False) for c in want[1:]}
    means = S.orientation(cols)
    r = S.compute(d, cols)
    with open(str(out) + '.partial', 'wb') as f:
        np.savez(f, model_source_row=msr, sup_score=r['sup_score'], sup_pct=r['sup_pct'], in_universe=r['in_universe'])
    os.replace(str(out) + '.partial', out)
    q = [0, 1, 5, 25, 50, 75, 95, 99, 100]
    return {'rows': len(msr), 'ballot_columns': len(want) - 1 - len(S.INPUT_COLUMNS), 'coverage': r['coverage'],
            'orientation_means': means, 'in_universe': int(r['in_universe'].sum()),
            'sup_score_quantiles': dict(zip(map(str, q), np.round(np.percentile(r['sup_score'], q), 3).tolist()))}


def support(district, redo=False, **_):
    """cache/support.npz (tools/support.py, general mode) and its sha256 in district.json. A pure function of the cache and the config, so it
    is always recomputed (no --redo needed). The work runs in a Python 3.12 child (pyarrow), like every rowfw step; primary mode stops in
    tools/support.compute with PRIMARY_NOT_APPROVED."""
    R, d = dr(district), _district(district)
    if not d['candidate']:
        raise SystemExit(f'{district}: no candidate, so no support score')
    out = R / 'cache' / 'support.npz'
    with RowfwStep(district, 'rowfw-support', outputs=[out], heavy=True, redo=True) as st:
        result_file = st.D / 'work' / 'support-result.json'
        result_file.parent.mkdir(parents=True, exist_ok=True)
        code = (f'sys.path.insert(0, {str(TER)!r}); import json, tools.districts as D; from tools.luna_district.rowfw import write_support; '
                f'print(json.dumps(write_support({str(R / "cache" / "inputs.parquet")!r}, {str(out)!r}, D.get({d["id"]!r}))))')
        run_code(st, code, R, out=result_file)
        result = json.loads(result_file.read_text(encoding='utf-8').strip().splitlines()[-1])
        result_file.unlink()
        assert result['rows'] == DISTRICTS[district]['rows'], result['rows']
        sha = sha256_file(out)
        meta = read_json(R / 'district.json')
        if 'support' in meta:          # a copy made before the 4-term table has no support block; the run order (copy --redo, then support) always has it
            write_json(R / 'district.json', {**meta, 'support': {**meta['support'], 'sha256': sha}})
        st.note(support_sha256=sha, district_json_updated='support' in meta, **result)


def freeze(district, redo=False, **_):
    R, B = dr(district), drb(district)
    outs = [B / n for n in ('framework_config.json', 'criteria_rowfw-1.0.0.json', 'PLAN_rowfw-1.0.0.md', 'PLAN.receipt.json')]
    clarity = R / 'design' / 'clarity_spec_facts_stdout.txt'
    candidate = _district(district)['candidate']
    npz = R / 'cache' / 'support.npz'
    with RowfwStep(district, 'rowfw-freeze', outputs=outs, heavy=True, redo=redo) as st:
        if candidate:
            assert npz.exists() and read_json(R / 'district.json').get('support', {}).get('sha256') == sha256_file(npz), \
                'cache/support.npz is missing or differs from district.json: run rowfw-copy --redo, then rowfw-support'
        run_script(st, R / 'design' / 'clarity_spec_facts.py', [], R / 'design', out=str(clarity) + '.partial')
        os.replace(str(clarity) + '.partial', clarity)
        run_script(st, B / 'freeze' / 'freeze_rowfw.py', ['write'], B)
        cfg = read_json(B / 'framework_config.json')
        if candidate:
            ov = cfg['overall']
            block, want = ov[ov['default']], read_json(R / 'district.json')['overall']
            assert ov['default'] == want['key'], ov['default']
            assert sum(F(w['num'], w['den']) for w in block['weights'].values()) == 1, block['weights']
            assert block['support']['sha256'] == sha256_file(npz), 'the frozen config does not pin this support.npz'
            st.note(overall=ov['default'], overall_weights={g: w['value'] for g, w in block['weights'].items()}, support_sha256=block['support']['sha256'])
        unmapped = {k: v for k, v in cfg['verification_at_freeze']['code_counts'].items() if k.endswith('__unmapped_codes') and v}
        assert not unmapped, f'unmapped codes (the lead decides): {unmapped}'
        counts = cfg['standing']['within_registration']['group_counts_at_freeze']
        assert sum(counts.values()) == DISTRICTS[district]['rows'], counts
        run_script(st, B / 'freeze' / 'freeze_rowfw.py', ['receipt'], B)
        st.note(group_counts=counts, ridit_within_1e3=cfg['ridit']['freeze_check']['within_1e-3'], config_sha256=sha256_file(B / 'framework_config.json'),
                top_k=cfg['top_sets']['balanced_by_registration_view']['K'])


def s0(district, redo=False, **_):
    t00 = drb(district) / 'tests' / 'T00.json'
    with RowfwStep(district, 'rowfw-s0', outputs=[t00], heavy=True, redo=redo) as st:
        run_script(st, drb(district) / 'rowfw_build.py', ['--mode', 'release', '--stage', 's0'], drb(district))
        holds = read_json(t00)['contract_holds']
        assert holds is True, 'T00 does not hold: stop'
        st.note(t00_contract_holds=holds)


def p1snap(district, redo=False, **_):
    with RowfwStep(district, 'rowfw-p1snap', heavy=True) as st:
        found = sorted((drb(district) / 'tests' / 'superseded').glob('*_P1_pre/SNAPSHOT_MANIFEST.json'))
        if not found:
            run_script(st, drb(district) / 'tools' / 'p1_snapshot_p0.py', [], drb(district))
            found = sorted((drb(district) / 'tests' / 'superseded').glob('*_P1_pre/SNAPSHOT_MANIFEST.json'))
        assert found, 'no P1_pre snapshot'
        st.note(snapshot=found[0].parent.name)


MAX_DRAW_CHILDREN = 8       # 8 x 480 s per invocation stays inside the 2-hour watchdog; the runner repeats the step until complete
DRAW_SECONDS = 480


def draws(district, redo=False, **_):
    with RowfwStep(district, 'rowfw-draws', heavy=True) as st:
        done, n = False, 0
        while not done and n < MAX_DRAW_CHILDREN:
            n += 1
            result = _build(st, 'draws', ['--max-seconds', str(DRAW_SECONDS), '--coupling', 'both'])
            done = bool(result['complete'])
        st.note(complete=done, children=n, draw_status=result['draw_status'])


def finalize(district, redo=False, **_):
    B = drb(district)
    with RowfwStep(district, 'rowfw-finalize', outputs=[B / 'area_scores_rowfw-1.0.0.csv'], heavy=True, redo=redo) as st:
        result = _build(st, 'finalize')
        st.note(T03=result['T03'], T04=result['T04'], area_suppressed_rows=sum(1 for _ in open(B / 'summaries' / 'area_suppressed_ids.csv', 'rb')) - 1)


def populate(district, redo=False, **_):
    B = drb(district)
    files = [B / f'{s}_rowfw-1.0.0.parquet' for s in ('row_scores', 'household_scores', 'profiles')]
    with RowfwStep(district, 'rowfw-populate', outputs=files, heavy=True, redo=redo) as st:
        _build(st, 'populate')
        manifests = {f.name: read_json(f.with_suffix('.manifest.json')) for f in files}
        assert all(m['phase'] == 'P1' for m in manifests.values())
        assert manifests[files[0].name]['rows'] == DISTRICTS[district]['rows']
        st.note(p1_files={k: {'rows': v['rows'], 'columns': v['n_columns']} for k, v in manifests.items()})


def d2(district, redo=False, **_):
    B = drb(district)
    out = B / 'release' / 'P1_D2_suppression_counts.json'
    with RowfwStep(district, 'rowfw-d2', outputs=[out], heavy=True, redo=redo) as st:
        run_script(st, B / 'tools' / 'p1_d2_suppression_counts.py', [], B)
        last = read_json(out)['D2_suppression_rounds'][-1]
        assert last['symdiff_violations'] == 0 and last['remainder_violations'] == 0 and last['exactly_disclosed'] == 0, last
        st.note(final_audit_clean=True, d2_levels={k: [v['areas'], v['published']] for k, v in read_json(out)['D2_counts'].items()})


def cleanup(district, redo=False, **_):
    with RowfwStep(district, 'rowfw-cleanup') as st:
        _build(st, 'cleanup')
        st.note(work_dir_exists=(dr(district) / 'work').exists())


def pin(district, **_):
    """The three frozen shas (what tools/area_layers.FROZEN_SHA256_BY_DISTRICT must hold) and whether it holds them."""
    with RowfwStep(district, 'rowfw-pin') as st:
        files = read_json(drb(district) / 'PLAN.receipt.json')['files']
        shas = {'framework_config_sha256': files['framework_config.json']['sha256'], 'plan_sha256': files['PLAN_rowfw-1.0.0.md']['sha256'],
                'criteria_sha256': files['criteria_rowfw-1.0.0.json']['sha256']}
        import tools.area_layers as AL
        pinned = getattr(AL, 'FROZEN_SHA256_BY_DISTRICT', {}).get(TERRIER[district])
        for k, v in shas.items():
            log(f'{district} {k} {v}')
        st.note(shas=shas, pinned=pinned == shas)


# ------------------------------------------------------------------ gate G1: the copy on Colorado's data reproduces Colorado's cache and config
EXPECTED_DIFFERENT = {('inputs', 'cache_columns_json', 'sha256'): 'columns.json holds the build time and paths',
                      ('verification_at_freeze', 'preflight'): 'RAM and disk at the time of the run'}


def _normal(value, subs):
    if isinstance(value, str):
        for a, b in subs:
            value = value.replace(a, b)
        return value
    if isinstance(value, dict):
        return {k: _normal(v, subs) for k, v in value.items()}
    if isinstance(value, list):
        return [_normal(v, subs) for v in value]
    return value


def json_diff(a, b, path=()):
    """Paths at which two parsed JSON values differ (a differing leaf, or a missing key / list length)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return [p for k in sorted(set(a) | set(b)) for p in (json_diff(a[k], b[k], path + (k,)) if k in a and k in b else [path + (k,)])]
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [p for i, (x, y) in enumerate(zip(a, b)) for p in json_diff(x, y, path + (i,))]
    return [] if a == b else [path]


def table_equal(a, b):
    """Parquet files hold the same table (schema and values), compared row group by row group."""
    import pyarrow.parquet as pq
    fa, fb = pq.ParquetFile(a), pq.ParquetFile(b)
    if fa.schema_arrow != fb.schema_arrow:
        return {'schema_equal': False}
    if fa.num_row_groups == fb.num_row_groups:
        return {'schema_equal': True, 'row_groups': fa.num_row_groups,
                'tables_equal': all(fa.read_row_group(i).equals(fb.read_row_group(i)) for i in range(fa.num_row_groups))}
    return {'schema_equal': True, 'row_groups': [fa.num_row_groups, fb.num_row_groups],
            'tables_equal': all(fa.read(columns=[c]).equals(fb.read(columns=[c])) for c in fa.schema_arrow.names)}


def g1(district, redo=False, **_):
    assert DISTRICTS[district]['is_co'], 'G1 compares the Colorado replay only'
    R, B = dr(district), drb(district)
    out = district_dir(district) / 'work' / 'g1-result.json'
    with RowfwStep(district, 'rowfw-g1', outputs=[out], heavy=True, redo=redo) as st:
        subs = [(str(R / 'scratch'), '<SCRATCH>'), (str(R), '<RF>'), (str(RF), '<RF>'), (SCRATCH_CO, '<SCRATCH>'), (str(CO_EXPORT), '<EXPORT>'), (str(LUNA), '<ROOT>')]
        mine_sha = read_json(R / 'district.json')['cache_sha256']
        result = {'cache_sha256_equal': mine_sha == CACHE_SHA_CO}
        if not result['cache_sha256_equal']:
            code = (f'sys.path.insert(0, {str(TER)!r}); from tools.luna_district.rowfw import table_equal; import json; '
                    f'print(json.dumps(table_equal({str(RF / "cache" / "inputs.parquet")!r}, {str(R / "cache" / "inputs.parquet")!r})))')
            tmp = district_dir(district) / 'work' / 'g1-tables.json'
            tmp.parent.mkdir(parents=True, exist_ok=True)
            run_code(st, code, R, out=tmp)
            result['tables'] = json.loads(tmp.read_text(encoding='utf-8').strip().splitlines()[-1])
        for name in ('framework_config.json', 'criteria_rowfw-1.0.0.json'):
            diffs = json_diff(_normal(read_json(RFB / name), subs), _normal(read_json(B / name), subs))
            result[name] = {'differences': [list(map(str, p)) for p in diffs], 'unexplained': [list(map(str, p)) for p in diffs if not any(tuple(p)[:len(k)] == k for k in EXPECTED_DIFFERENT)]}
        plan_a, plan_b = (_normal((p / 'PLAN_rowfw-1.0.0.md').read_text(encoding='utf-8'), subs) for p in (RFB, B))
        result['plan_text_equal'] = plan_a == plan_b
        columns = [read_json(p / 'cache' / 'columns.json') for p in (RF, R)]     # the config holds columns.json's sha, which has the build time in it
        for doc in columns:
            doc.pop('built_utc'), doc.pop('build')
        result['columns_json_equal_except_build_info'] = _normal(columns[0], subs) == _normal(columns[1], subs)
        passed = ((result['cache_sha256_equal'] or (result['tables'].get('schema_equal') and result['tables'].get('tables_equal')))
                  and not any(result[n]['unexplained'] for n in ('framework_config.json', 'criteria_rowfw-1.0.0.json')) and result['plan_text_equal']
                  and result['columns_json_equal_except_build_info'])
        write_json(out, {**result, 'passed': bool(passed)})
        st.note(g1_passed=bool(passed), cache_sha256_equal=result['cache_sha256_equal'], plan_text_equal=result['plan_text_equal'],
                config_differences=len(result['framework_config.json']['differences']),
                config_unexplained=len(result['framework_config.json']['unexplained']),
                criteria_unexplained=len(result['criteria_rowfw-1.0.0.json']['unexplained']))
        assert passed, 'G1 failed: see work/g1-result.json'
