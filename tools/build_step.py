"""Run one named store-build step the same way on the laptop and in the build container (Dockerfile.build, COMPUTE.md).

    python tools/build_step.py run <step> [--district co-05]    build one store into $TERRIER_LOCAL_DIR
    python tools/build_step.py export <out_dir> <file> ...      verified copy of files out of $TERRIER_LOCAL_DIR (the stage volume)
    python tools/build_step.py fingerprint                      interpreter and library versions, as JSON

$TERRIER_LOCAL_DIR is required: a step never falls back to the repo's live .local. A step that needs a salt stops when the
salt file is missing (the tool would make a new one, and a new salt re-keys every person id). A step has only run when its
store exists: the exit code alone is not taken for completion. Counts and file names only; no row is read or printed here.
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
    store: str          # the file it writes, in the district's data directory
    salt: bool = False  # needs the district's street salt to exist already
    image: str = 'geo'  # which toolchain: 'geo' = work\.venv / terrier-build, 'score' = system Python 3.12 / terrier-build-score
    receipt: str = ''   # a tool whose receipt defaults to a folder of the repo gets this file in the data directory instead
                        # (the repo is mounted read-only in the container); counts and timings, never compared
    also: tuple = ()    # other files it writes in the data directory: they must exist, and are compared and exported with the store
    demo_key: bool = False      # makes a random .demo-key in the data directory when there is none (see seed_demo_key)


# The tools' plain `--district` command lines, in the order Terrier's Windows runner (_build/run_nowithhold.ps1) runs them: roads,
# street and contacts (work venv), then shown and scores (system Python 3.12: pandas and pyarrow, which the work venv has not).
STEPS = {
    'roads': Step('tools/build_road_context.py', 'roads.sqlite3'),
    'street': Step('tools/build_street.py', 'street.sqlite3', salt=True),
    'contacts': Step('tools/build_contacts.py', 'contacts.sqlite3', salt=True),
    'shown': Step('tools/shown_areas.py', 'area_scores_shown.csv', image='score', also=('area_scores_shown.manifest.json',)),
    'scores': Step('tools/build_scores_v3.py', 'scores.sqlite3', image='score', receipt='scores_receipt.json', demo_key=True),
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


def run(step, district):
    local = local_dir()
    spec, d = STEPS[step], D.get(district)
    target = D.data_dir(d) / spec.store
    if spec.salt and not D.street_salt(d).is_file():
        raise SystemExit('build_step: the street salt of %s is missing (%s); a new one would re-key every person id'
                         % (d['id'], D.street_salt(d)))
    Path(local).mkdir(parents=True, exist_ok=True)
    if spec.demo_key and os.environ.get('TERRIER_DEMO_KEY'):
        D.data_dir(d).mkdir(parents=True, exist_ok=True)
        seed_demo_key(D.data_dir(d), os.environ['TERRIER_DEMO_KEY'])
    box = GlassBox(new_run_id('terrier-build-%s-%s' % (d['id'], step)), 'cu-geography-v3/tools/build_step.py')
    box.run('running', step, 'build step started', metadata={'district': d['id'], 'local': local})
    command = [sys.executable, str(REPO / spec.script), '--district', d['id']]
    if spec.receipt:
        command += ['--receipt', str(D.data_dir(d) / spec.receipt)]
    code = subprocess.call(command, cwd=str(REPO))
    for written in (target, *(D.data_dir(d) / name for name in spec.also)):
        if code == 0 and not (written.is_file() and written.stat().st_size > 0):
            code = 1
            print('build_step: %s exited 0 but %s was not written' % (step, written), file=sys.stderr)
    box.run('ok' if code == 0 else 'failed', step, 'build step finished', metadata={'exit': code})
    if code == 0:
        print(json.dumps({'step': step, 'district': d['id'], 'store': spec.store, 'bytes': target.stat().st_size}))
    return code


def export(out_dir, names):
    """Copy files from $TERRIER_LOCAL_DIR to out_dir: a SQLite store through the backup API with its integrity check and row
    counts (tools/copy_db.py), anything else byte for byte; every copy is re-hashed. Writes export_receipt.json (sha256 per file)."""
    from tools import copy_db
    stage, out = Path(local_dir()).resolve(), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    receipt = {}
    for name in names:
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise SystemExit('build_step: export takes names inside TERRIER_LOCAL_DIR, got %r' % name)
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
