"""P3-00: copy the Luna stage code (import closure) into a district folder and patch only the CO literals.

Luna originals are never edited: files are read, hashed and copied. The patch is applied by exact-string replacement
(every old string must occur the stated number of times), keeps the line count of every file, and is verified by a line-set
gate: every line of a copy equals the original line or is a declared patched line. Writes copy-manifest.json and
copy-diff.patch next to the copies.
"""
import difflib
import json
import re
import shutil
from pathlib import Path

from .common import (CO_EXPORT, DISTRICTS, HM_FIT_FIELDS, HM_FIELDS, L, LUNA, MODEL_STUDY_SITE, TARGETS, Step,
                     district_dir, read_json, sha256_file, utc_now, write_json)

STAGES = ['individual-validation-20260928', 'rare-feature-search-20260928', 'four-metric-validation-20260928',
          'completion-20260929', 'accuracy-completion-20260929', 'knn-tuning-20260929', 'stratified-scaling-20260929',
          'next-stage-fixes-20260929', 'prior-corrected-repair-20260929', 'household-copy-20260929',
          'household-model-20260930', 'area-first-20260929', 'audited-social-20260929']
ENTRY_MODULES = ['household_model', 'complete_individuals', 'prior_repair', 'search_rare', 'validate_individual',
                 'audit_areas', 'correct_accuracy', 'audit_variables', 'validate_remaining']

# Expression that reads district.json from the copy's own location (.local/<stage>/<file> -> parents[2] is the district root).
DJ = ("__import__('json').loads(__import__('pathlib').Path(__file__).resolve().parents[2]"
      ".joinpath('district.json').read_text(encoding='utf-8'))")
ROWS = DJ + "['rows']"
HOUSEHOLDS = DJ + "['households']"
COMPLETED = DJ + "['completed_values']"
SITE = str(MODEL_STUDY_SITE)

# (stage/file) -> [(old, new, count, kind)]; kind a = count/path/hash from the district, c = environment path (read-only Luna env)
PATCHES = {
    'individual-validation-20260928/validate_individual.py': [
        ("SOURCE = Path('C:/Users/pgoff/Downloads/statara_co_cd005_20260904.csv')",
         "SOURCE = Path(json.loads((ROOT / 'district.json').read_text(encoding='utf-8'))['source_csv'])", 1, 'a'),
        ("sys.path.append(str(ROOT / '.local/model-study/.venv/Lib/site-packages'))",
         f"sys.path.append({SITE!r})", 1, 'c'),
    ],
    'four-metric-validation-20260928/audit_variables.py': [
        ("(ROOT.parent / 'requested-column-groups.md')", "(ROOT / 'requested-column-groups.md')", 1, 'a'),
        ("== 491308", "== " + ROWS, 1, 'a'),
    ],
    'completion-20260929/audit_areas.py': [("==491308", "==" + ROWS, 1, 'a')],
    'completion-20260929/export_completion.py': [
        ("source_area=np.full(491308,-1,dtype=int)", "source_area=np.full(" + ROWS + ",-1,dtype=int)", 1, 'a'),
        ("area=np.full(491308,'',dtype=object);area[cohort['source_rows']]=np.asarray(meta['tract_ids'])[cohort['area']]",
         "raise NotImplementedError('CO geography cohort; see P3-15 model_audited_tract')", 1, 'a'),
        ("assert offset==491308 and checks==", "assert offset==" + ROWS + " and checks==", 1, 'a'),
        ("Only the 428874-record, 158-tract validated geography cohort", "Only the validated geography cohort of the district", 1, 'a'),
    ],
    'accuracy-completion-20260929/correct_accuracy.py': [
        ("26 targets and 1722948 missing task values", "26 targets and ' + str(" + COMPLETED + ") + ' missing task values", 1, 'a'),
    ],
    'stratified-scaling-20260929/apply_revised.py': [
        ("assert offset==491308 and digest", "assert offset==" + ROWS + " and digest", 1, 'a'),
        ("sum(counts.values())==1722948 and offset==491308",
         "sum(counts.values())==" + COMPLETED + " and offset==" + ROWS, 1, 'a'),
    ],
    'prior-corrected-repair-20260929/prior_repair.py': [
        ("COMPLETED=1722948", "COMPLETED=" + COMPLETED, 1, 'a'),
        ("assert offset==491308 and digest", "assert offset==" + ROWS + " and digest", 1, 'a'),
        ("assert sum(blanks.values())==1722948 and offset==491308",
         "assert sum(blanks.values())==" + COMPLETED + " and offset==" + ROWS, 1, 'a'),
    ],
    'household-copy-20260929/household_copy.py': [
        ("MAIN_BLANKS=50784", "MAIN_BLANKS=" + DJ + "['blanks']['mid14']", 1, 'a'),
        ("assert offset==491308 and digest", "assert offset==" + ROWS + " and digest", 1, 'a'),
        ("n=receipt['rows'];assert n==491308", "n=receipt['rows'];assert n==" + ROWS, 1, 'a'),
        ("assert verification['rows']==491308 and", "assert verification['rows']==" + ROWS + " and", 1, 'a'),
    ],
    'household-model-20260930/household_model.py': [
        ("ROWS=491308;HOUSEHOLDS=343000;", "ROWS=" + ROWS + ";HOUSEHOLDS=" + HOUSEHOLDS + ";", 1, 'a'),
        ("CHOSEN_COUNT=17", "CHOSEN_COUNT=" + DJ + "['chosen_count']", 1, 'a'),
    ],
    'area-first-20260929/area_benchmark.py': [("assert len(frame)==491308", "assert len(frame)==" + ROWS, 1, 'a')],
}

# Literals deliberately left in copied code: constants used only by CLI modes the driver never calls.
RESIDUAL_NOTE = ('Residual CO literals (text in reports, CV-grid counts 780/2340, 96-model collect, PINNED_* and EXPECTED_* '
                 'pins, 622-column asserts) sit in stage modes the driver never calls; the driver calls only library '
                 'functions. Listed in residual-literals.txt.')
CENSUS = re.compile(r'491308|491,308|343000|1722948|1,722,948|428874|428,874|statara|C:/Users|pgoff|50784|79551|7554|208908|\b780\b|\b2340\b')

# Frozen CO config inputs copied read-only and verified by sha256.
FROZEN_CO = {
    'individual-results.json': L / 'completion-20260929' / 'individual-results.json',
    'individual-policy.json': L / 'completion-20260929' / 'individual-policy.json',
    'predictor-catalog.json': L / 'four-metric-validation-20260928' / 'predictor-catalog.json',
    'household-model-results.json': L / 'household-model-20260930' / 'household-model-results.json',
    'household-model-policy.json': L / 'household-model-20260930' / 'household-model-policy.json',
    'train-summary.json': L / 'household-model-20260930' / 'train-summary.json',
    'gpu-export-receipt.json': L / 'gpu-publish-20260930' / 'gpu-export-receipt.json',
    'labels.npz': L / 'four-metric-validation-20260928' / 'labels.npz',
    'labels-and-splits.npz': L / 'individual-validation-20260928' / 'labels-and-splits.npz',
    'individual_model_validation.csv': CO_EXPORT / 'individual_model_validation.csv',
    'column_dictionary.json': CO_EXPORT / 'column_dictionary.json',
}


def closure():
    """Static import scan from the entry modules over the in-scope stage folders (same scan the plan describes)."""
    mods = {}
    for s in STAGES:
        for p in (L / s).glob('*.py'):
            mods.setdefault(p.stem, []).append(p)

    def imports(p):
        found = set()
        for line in p.read_text(encoding='utf-8', errors='replace').splitlines():
            m = re.match(r'\s*import\s+(.+)', line)
            if m:
                for part in re.split(r'[;,]', m.group(1)):
                    name = part.strip().split(' as ')[0].strip().split('.')[0]
                    if name:
                        found.add(name)
            m = re.match(r'\s*from\s+([\w\.]+)\s+import', line)
            if m:
                found.add(m.group(1).split('.')[0])
        return found

    seen, todo = {}, list(ENTRY_MODULES)
    while todo:
        m = todo.pop()
        if m in seen or m not in mods:
            continue
        seen[m] = mods[m]
        for p in mods[m]:
            todo.extend(i for i in imports(p) if i in mods and i not in seen)
    out = []
    for m, paths in sorted(seen.items()):
        assert len(paths) == 1, f'module {m} defined in several stage folders: {paths}'
        out.append(paths[0])
    return out


def apply_patches(rel, text):
    changed_lines = set()
    for old, new, count, kind in PATCHES.get(rel, []):
        assert text.count(old) == count, f'{rel}: expected {count} occurrence(s) of {old[:60]!r}, found {text.count(old)}'
        assert '\n' not in new, 'a patch may not add lines'
        for i, line in enumerate(text.splitlines()):
            if old in line:
                changed_lines.add(i + 1)
        text = text.replace(old, new)
    return text, changed_lines


def line_gate(original, copy, declared):
    """Every line equals the original or is declared patched; line counts match."""
    a, b = original.splitlines(), copy.splitlines()
    assert len(a) == len(b), 'patched copy changed the number of lines'
    differing = {i + 1 for i, (x, y) in enumerate(zip(a, b)) if x != y}
    assert differing == set(declared), f'undeclared or missing differences: {sorted(differing ^ set(declared))}'
    return sorted(differing)


def run(district, redo=False):
    D = district_dir(district)
    cfg = DISTRICTS[district]
    with Step(district, 'copy', outputs=[D / 'copy-manifest.json'], redo=redo) as st:
        D.mkdir(parents=True, exist_ok=True)
        files = closure()
        manifest = {'created_utc': utc_now(), 'district': district, 'files': {}, 'frozen_co': {}, 'co_transfer': {},
                    'residual_note': RESIDUAL_NOTE}
        diff_text = []
        residual = []
        for src in files:
            rel = f'{src.parent.name}/{src.name}'
            original = src.read_bytes()
            text = original.decode('utf-8')
            patched, declared = apply_patches(rel, text)
            line_gate(text, patched, declared)
            dest = D / '.local' / src.parent.name / src.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(patched.encode('utf-8'))
            compile(patched, str(dest), 'exec')
            manifest['files'][rel] = {'original_sha256': sha256_file(src), 'copy_sha256_before_patch': sha256_file(src),
                                      'copy_sha256_after_patch': sha256_file(dest), 'patched_lines': sorted(declared),
                                      'bytes': len(original)}
            if declared:
                diff_text.extend(difflib.unified_diff(text.splitlines(True), patched.splitlines(True), f'a/{rel}', f'b/{rel}', n=0))
            for i, line in enumerate(patched.splitlines(), 1):
                for m in CENSUS.finditer(line):
                    residual.append(f'{rel}:{i}: [{m.group(0)}]')
        (D / 'copy-diff.patch').write_text(''.join(diff_text), encoding='utf-8')
        (D / 'residual-literals.txt').write_text('\n'.join(residual) + '\n', encoding='utf-8')
        manifest['residual_literal_count'] = len(residual)
        # data inputs the copies expect at ROOT-relative paths
        shutil.copy2(LUNA.parent / 'requested-column-groups.md', D / 'requested-column-groups.md')
        inv_dest = D / '.local' / 'area-variable-audit' / 'column-inventory.json'
        inv_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(L / 'area-variable-audit' / 'column-inventory.json', inv_dest)
        manifest['inputs'] = {'requested-column-groups.md': sha256_file(D / 'requested-column-groups.md'),
                              'area-variable-audit/column-inventory.json': sha256_file(inv_dest)}
        fro = D / 'frozen-co'
        fro.mkdir(exist_ok=True)
        for name, src in FROZEN_CO.items():
            shutil.copy2(src, fro / name)
            assert sha256_file(fro / name) == sha256_file(src)
            manifest['frozen_co'][name] = {'source': str(src), 'sha256': sha256_file(fro / name)}
        results = read_json(fro / 'individual-results.json')
        train = read_json(fro / 'train-summary.json')
        for t in TARGETS:
            dst = D / 'co-transfer' / 'legacy' / f'model-{t}.joblib'
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(L / 'completion-20260929' / f'model-{t}.joblib', dst)
            assert sha256_file(dst) == results[t]['model_sha256'], f'legacy bundle hash differs for {t}'
            manifest['co_transfer'][f'legacy/{t}'] = results[t]['model_sha256']
        for t in HM_FIT_FIELDS:
            dst = D / 'co-transfer' / 'hm' / f'model-T-{t}.joblib'
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(L / 'household-model-20260930' / f'model-T-{t}.joblib', dst)
            assert sha256_file(dst) == train[t]['T']['model_sha256'], f'household bundle hash differs for {t}'
            manifest['co_transfer'][f'hm/{t}'] = train[t]['T']['model_sha256']
        # district.json is what every patched literal reads
        write_json(D / 'district.json', {
            'id': cfg['id'], 'label': cfg['label'], 'source_csv': cfg['source_csv'], 'source_sha256': cfg['source_sha256'],
            'source_bytes': cfg['source_bytes'], 'rows': cfg['rows'], 'households': cfg['households'],
            'completed_values': cfg['completed_values'], 'blanks': cfg['blanks'], 'components': cfg['components'],
            'largest_component': cfg['largest_component'], 'split_components': cfg['split_components'],
            'chosen_count': len(HM_FIELDS), 'is_co': cfg['is_co'],
            'seeds': {'legacy_forest': 20260939, 'calibrator': 20260941, 'hm_forest': 20260948, 'shuffle': 20261047},
            'luna': str(LUNA), 'model_study_site': SITE})
        manifest['district_json_sha256'] = sha256_file(D / 'district.json')
        write_json(D / 'copy-manifest.json', manifest)
        st.note(files_copied=len(files), patched_files=len([1 for v in manifest['files'].values() if v['patched_lines']]),
                patched_lines=sum(len(v['patched_lines']) for v in manifest['files'].values()),
                residual_literals=len(residual), closure=sorted(f'{p.parent.name}/{p.name}' for p in files))
