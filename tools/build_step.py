"""Run one named store-build step the same way on the laptop and in the build container (Dockerfile.build, COMPUTE.md).

    python tools/build_step.py run <step> [--district co-05]    build one store into $TERRIER_LOCAL_DIR
    python tools/build_step.py export <out_dir> <file> ...      verified copy of files out of $TERRIER_LOCAL_DIR (the stage volume)
    python tools/build_step.py fingerprint                      interpreter and library versions, as JSON

$TERRIER_LOCAL_DIR is required: a step never falls back to the repo's live .local. A step that needs a salt stops when the
salt file is missing (the tool would make a new one, and a new salt re-keys every person id). A step has only run when its
store exists: the exit code alone is not taken for completion. A name with a `*` stands for the files it matches (a bundle's
file name is its snapshot key, which only the build knows). Counts and file names only; no row is read or printed here.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools import districts as D            # noqa: E402
from tools.glassbox import GlassBox, new_run_id    # noqa: E402


class Step(NamedTuple):
    script: str
    store: str          # the file it writes, in the district's data directory; a name with `*` must match exactly one file
    salt: bool = False  # needs the district's street salt to exist already
    image: str = 'geo'  # which toolchain: 'geo' = work\.venv / terrier-build, 'score' = system Python 3.12 / terrier-build-score
    receipt: str = ''   # a tool whose receipt defaults to a folder of the repo gets this file in the data directory instead
                        # (the repo is mounted read-only in the container); counts and timings, never compared
    also: tuple = ()    # other files it writes in the data directory (a `*` name: at least one match): they must exist, and are compared and exported with the store
    demo_key: bool = False      # makes a random .demo-key in the data directory when there is none (see seed_demo_key)
    walk_salt: bool = False     # makes a random .walk-salt in the data directory when there is none (see seed_walk_salt)
    args: tuple = ('--district', '{district}')   # the tool's command line after the script; fields: see render
    env: tuple = ()     # (name, value) pairs the tool needs beside the build's own environment; same fields as args
    needs: tuple = ()   # what the step reads from the data directory that no step before it leaves there: seeded by compute_build --inputs
                        # (names or folders; `@...` is a rule of the district, see step_needs)
    keep: tuple = ()    # files exported with the store but never compared (receipts: counts and timings)
    held: str = ''      # not empty: why the container build of this step is not accepted yet (a parity FAIL). tools/compute_build.py
                        # leaves it out of its default chain and refuses it in a container; the Windows build is the accepted one


# The tools' command lines, in the order of the build chain: roads, street and contacts (work venv; the untracked
# _build/run_remaining.ps1 runs them as R2-R4), then shown and scores (system Python 3.12: pandas and pyarrow, which the work venv has
# not; _build/run_nowithhold.ps1 runs those two), then walk (work venv; run_remaining.ps1 R5), then ingest, extend, bundle and density
# (work venv; run_nowithhold.ps1). tools/compute_build.py runs the chain, in the containers unless TERRIER_COMPUTE=windows.
# Where a runner names a folder of its own (bundle-work, the repo's data/, _build/) the step writes into the data directory instead,
# which is the stage in a container: ingest/ (v2 and its zip), extend/ (v3 and its zip), bundle-out/ (the cu-summary-v4 zip and its
# .sha256), bundle_receipt.json. import_summary and the other steps that write into a district's server data stay on the host.
STEPS = {
    'roads': Step('tools/build_road_context.py', 'roads.sqlite3'),
    'street': Step('tools/build_street.py', 'street.sqlite3', salt=True, needs=('@street-salt',)),
    # build_contacts adds an index to street.sqlite3 in place, so a contacts-only run leaves a changed street store too
    'contacts': Step('tools/build_contacts.py', 'contacts.sqlite3', salt=True, also=('street.sqlite3',),
                     needs=('street.sqlite3', '@street-salt')),
    'shown': Step('tools/shown_areas.py', 'area_scores_shown.csv', image='score', also=('area_scores_shown.manifest.json',),
                  needs=('@rowfw',)),
    'scores': Step('tools/build_scores_v3.py', 'scores.sqlite3', image='score', receipt='scores_receipt.json', demo_key=True,
                   needs=('street.sqlite3', 'contacts.sqlite3', 'area_scores_shown.csv', 'area_scores_shown.manifest.json', '@rowfw')),
    'walk': Step('tools/build_walk.py', 'walk.sqlite3', walk_salt=True,
                 needs=('roads.sqlite3', 'street.sqlite3', 'contacts.sqlite3', '@scores-store')),
    # ingest.py writes ingest_receipt.json into its --geography folder unless told otherwise; that folder is read-only in the container
    # ingest/v2/* are the files the zip holds, which extend reads as a folder: exported with the zip (never compared: the zip is) for an extend run of its own
    'ingest': Step('tools/ingest.py', 'ingest/*.zip', also=('ingest/ingest_receipt.json',), keep=('ingest/v2/*',),
                   args=('--csv', '{csv}', '--geography', '{geography}', '--output', '{data}/ingest/v2',
                         '--receipt', '{data}/ingest/ingest_receipt.json'),
                   env=(('CU_DISTRICT', '{district}'),)),
    # extend_geography.py imports `tools` without putting the repo on sys.path (the runner starts it with python -c, which puts the working
    # directory on it): PYTHONPATH does that here
    'extend': Step('tools/extend_geography.py', 'extend/*.zip', needs=('ingest',),
                   args=('--csv', '{csv}', '--base', '{data}/ingest/v2', '--geography', '{geography}', '--output', '{data}/extend/v3'),
                   env=(('CU_DISTRICT', '{district}'), ('PYTHONPATH', '{repo}'))),
    # the app's own validator needs Django settings: a scratch CU_DATA_DIR and a throwaway key, so nothing is written to a real .local
    'bundle': Step('tools/build_bundle_v4.py', 'bundle-out/*.zip', also=('bundle-out/*.zip.sha256',), keep=('bundle_receipt.json',),
                   needs=('area_scores_shown.csv', 'scores.sqlite3', 'extend', '@rowfw'),
                   args=('--base', '{v3zip}', '--out-dir', '{data}/bundle-out', '--receipt', '{data}/bundle_receipt.json'),
                   env=(('CU_DISTRICT', '{district}'), ('CU_DATA_DIR', '{local}/.cu-validate'), ('CU_SECRET_KEY', 'terrier-build-validate-only')),
                   held='parity 2026-10-13: geometry.json of the container bundle is not byte-identical to the Windows build (one tract and one precinct '
                        'polygon start at another vertex; the same polygons), and D-177 keeps the bundle zip byte-identical'),
    'density': Step('tools/build_density.py', 'density.json', needs=('scores.sqlite3',)),
}
LIBRARIES = ('numpy', 'pandas', 'geopandas', 'shapely', 'pyogrio', 'pyproj', 'pyarrow', 'scipy', 'joblib', 'Django')


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def local_dir():
    local = os.environ.get('TERRIER_LOCAL_DIR')
    if not local:
        raise SystemExit('build_step: TERRIER_LOCAL_DIR is not set; a build never writes the repo\'s live .local by default')
    return local


def seed_demo_key(folder, hexkey):
    """$TERRIER_DEMO_KEY (64 hex digits) becomes `folder`/.demo-key unless one is there. build_scores_v3 makes a random key per folder and
    derives the demo names, their search index and the stored key from it, so builds in different folders never agree; a parity run
    gives every build the same throwaway key to make them comparable. Never replaces a key."""
    key = bytes.fromhex(hexkey)
    if len(key) != 32:
        raise SystemExit('build_step: TERRIER_DEMO_KEY must be 64 hex digits (32 bytes)')
    path = Path(folder) / '.demo-key'
    if not path.exists():
        path.write_bytes(key)


def seed_walk_salt(folder, text):
    """`text` (64 hex digits, the way build_walk makes its own salt) becomes `folder`/.walk-salt unless one is there. build_walk makes
    a random salt per folder and keys every voter with it, so builds in different folders never agree; a parity run gives every
    build the same throwaway salt, and a container build starts from the district's own. Never replaces a salt."""
    text = (text or '').strip()
    if len(text) != 64 or any(c not in '0123456789abcdefABCDEF' for c in text):
        raise SystemExit('build_step: TERRIER_WALK_SALT must be 64 hex digits')
    path = Path(folder) / '.walk-salt'
    if not path.exists():
        path.write_bytes(text.encode('ascii'))


def step_needs(spec, d):
    """The names in the district's data directory that `spec` reads and an earlier step (or a mount of the container) must provide.
    @street-salt: the salt file of a district that keeps its own (co-05's is the sibling repo's, mounted). @rowfw: the row-framework
    folder when it lives in the data directory (co-05's is the sibling repo's). @scores-store: the score store, when the district's
    people come from it (person_fields 'scores', co-05) and not from the source CSV."""
    found = []
    for need in spec.needs:
        if need == '@street-salt':
            found += [] if d['street_salt'] else ['.street-salt']
        elif need == '@rowfw':
            try:
                found.append(Path(d['rowfw_root']).relative_to(D.data_dir(d)).as_posix())
            except ValueError:
                pass
        elif need == '@scores-store':
            found += ['scores.sqlite3'] if d['person_fields'] == 'scores' else []
        else:
            found.append(need)
    return found


def inputs_needed(steps, d):
    """What a run of `steps` (in this order) must be given beside what its own steps make: every need of a step that no step before it
    provides (a step provides its store, its `also` and its `keep`; a need that names a folder is provided by a file in it)."""
    provided, found = [], []
    for step in steps:
        spec = STEPS[step]
        for need in step_needs(spec, d):
            if need not in found and not any(p == need or p.startswith(need + '/') for p in provided):
                found.append(need)
        provided += [spec.store, *spec.also, *spec.keep]
    return found


def expand(folder, name):
    """The files `name` stands for in `folder`, relative with `/`: itself, or its matches when it holds a `*` (sorted)."""
    if '*' not in name:
        return [name]
    root = Path(folder)
    return sorted(p.relative_to(root).as_posix() for p in root.glob(name) if p.is_file())


def v3_zip(data):
    """The cu-summary-v3 zip that the extend step wrote (the bundle's --base): the one zip in extend/. Two would not say which."""
    found = sorted((Path(data) / 'extend').glob('*.zip'))
    if len(found) != 1:
        raise SystemExit('build_step: the bundle needs the extend step\'s output: %d zip(s) in %s, expected one' % (len(found), Path(data) / 'extend'))
    return found[0]


def render(template, d):
    """`template` with its fields filled: {district}, {csv} (the source file), {geography}, {data} (the data directory), {local}
    (TERRIER_LOCAL_DIR), {repo}, {v3zip} (see v3_zip)."""
    values = {'district': d['id'], 'csv': d['source_csv'], 'geography': d['geography'], 'data': D.data_dir(d), 'local': local_dir(),
              'repo': REPO}
    if '{v3zip}' in template:
        values['v3zip'] = v3_zip(D.data_dir(d))
    return template.format(**values)


def written(data, name, exactly_one=False):
    """True when `name` (or a `*` name) left what it should in `data`: non-empty files, and exactly one of them for `exactly_one`."""
    found = [Path(data) / n for n in expand(data, name)]
    ok = [p for p in found if p.is_file() and p.stat().st_size > 0]
    return bool(ok) and len(ok) == (1 if exactly_one else len(found))


def run(step, district):
    local = local_dir()
    spec, d = STEPS[step], D.get(district)
    data = D.data_dir(d)
    if spec.salt and not D.street_salt(d).is_file():
        raise SystemExit('build_step: the street salt of %s is missing (%s); a new one would re-key every person id'
                         % (d['id'], D.street_salt(d)))
    Path(local).mkdir(parents=True, exist_ok=True)
    if spec.demo_key and os.environ.get('TERRIER_DEMO_KEY'):
        data.mkdir(parents=True, exist_ok=True)
        seed_demo_key(data, os.environ['TERRIER_DEMO_KEY'])
    if spec.walk_salt and os.environ.get('TERRIER_WALK_SALT'):
        data.mkdir(parents=True, exist_ok=True)
        seed_walk_salt(data, os.environ['TERRIER_WALK_SALT'])
    box = GlassBox(new_run_id('terrier-build-%s-%s' % (d['id'], step)), 'cu-geography-v3/tools/build_step.py')
    box.run('running', step, 'build step started', metadata={'district': d['id'], 'local': local})
    command = [sys.executable, str(REPO / spec.script), *(render(a, d) for a in spec.args)]
    if spec.receipt:
        command += ['--receipt', str(data / spec.receipt)]
    options = {'env': {**os.environ, **{name: render(value, d) for name, value in spec.env}}} if spec.env else {}
    code = subprocess.call(command, cwd=str(REPO), **options)
    for name, one in ((spec.store, True), *((n, False) for n in spec.also)):
        if code == 0 and not written(data, name, one):
            code = 1
            print('build_step: %s exited 0 but %s was not written' % (step, data / name), file=sys.stderr)
    box.run('ok' if code == 0 else 'failed', step, 'build step finished', metadata={'exit': code})
    if code == 0:
        target = data / expand(data, spec.store)[0]
        print(json.dumps({'step': step, 'district': d['id'], 'store': spec.store, 'bytes': target.stat().st_size}))
    return code


def export(out_dir, names):
    """Copy files from $TERRIER_LOCAL_DIR to out_dir: a SQLite store through the backup API with its integrity check and row
    counts (tools/copy_db.py), anything else byte for byte; every copy is re-hashed. A name with `*` is every file it matches in
    the stage. Writes export_receipt.json (sha256 per file)."""
    from tools import copy_db
    stage, out = Path(local_dir()).resolve(), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    receipt = {}
    expanded = []
    for name in names:
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise SystemExit('build_step: export takes names inside TERRIER_LOCAL_DIR, got %r' % name)
        found = expand(stage, name)
        if not found:
            raise SystemExit('build_step: export found nothing for %r in the stage' % name)
        expanded += found
    for name in expanded:
        relative = Path(name)
        source, target = stage / relative, out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith('.sqlite3'):
            copy_db.copy_one(source, target)
        else:
            shutil.copyfile(source, target)
        receipt[relative.as_posix()] = {'bytes': target.stat().st_size, 'sha256': sha256_file(target)}
    (out / 'export_receipt.json').write_text(json.dumps(receipt, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({'exported': len(receipt)}))
    return 0


def fingerprint():
    found = {'python': platform.python_version(), 'platform': platform.platform(), 'sqlite': sqlite3.sqlite_version}
    for name in LIBRARIES:
        try:
            found[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    for module, attribute in (('shapely', 'geos_version_string'), ('pyproj', 'proj_version_str'),
                              ('pyogrio', '__gdal_version_string__')):
        try:
            found[module + '_native'] = str(getattr(__import__(module), attribute))
        except (ImportError, AttributeError):
            pass
    print(json.dumps(found, sort_keys=True))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    modes = parser.add_subparsers(dest='mode', required=True)
    runner = modes.add_parser('run')
    runner.add_argument('step', choices=sorted(STEPS))
    runner.add_argument('--district', default=D.DEFAULT, choices=list(D.DISTRICTS))
    exporter = modes.add_parser('export')
    exporter.add_argument('out_dir')
    exporter.add_argument('names', nargs='+')
    modes.add_parser('fingerprint')
    args = parser.parse_args(argv)
    if args.mode == 'run':
        return run(args.step, args.district)
    if args.mode == 'export':
        return export(args.out_dir, args.names)
    return fingerprint()


if __name__ == '__main__':
    sys.exit(main())
