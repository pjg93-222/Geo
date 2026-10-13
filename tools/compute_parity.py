"""Parity of a containerised store build against the Windows build (COMPUTE.md; design sections 4 and 6).

    <venv python> tools/compute_parity.py --scratch <new folder> [--steps roads] [--district co-05]
                  [--image ghcr.io/pjg93-222/terrier-build@sha256:...] [--api] [--negative-control] [--skip-container]
                  [--export-bind]

  The container image is, in this order: --image, $TERRIER_BUILD_IMAGE (an explicit build no signed release names yet: the first
  parity runs), else the one the laptop's verified current release manifest names (Melange ops/RELEASE.md, "Compute jobs follow the
  manifest"; `current.py image terrier-build`). None of the three: nothing runs.

  A1, A2  the Windows build of the steps, twice, into <scratch>/A1 and <scratch>/A2. What differs between them is the legitimate
          run-to-run variance (a stamp, a run id): those tables, and only those, are left out of the comparison with B
  B       the same steps in the container (compute.compose.yaml), built in the stage volume, exported through a named volume
          and `docker cp` to <scratch>/B (--export-bind: straight into the bind-mounted folder, for native-Linux hosts)
  Compared with co_baseline.store_digest per table (a logical comparison) for each SQLite store and byte for byte for every other
  file (--files: the bundle zip, density.json, ...); --api adds co_baseline's golden endpoint check on A1 and B.
  Prints PASS or FAIL per artifact and exits 0 only when every line passes. Counts and names only; no row is printed.
  One heavy job at a time: A1, then A2, then B. Never point --scratch at a .local folder.
"""
import argparse
import datetime
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools import build_step as S            # noqa: E402
from tools import co_baseline as B           # noqa: E402
from tools import districts as D             # noqa: E402
from tools import hostpaths as H             # noqa: E402

IMAGE_NAME = 'terrier-build'         # the name the release manifest gives the geo build image (the score image is terrier-build-score)
RELEASE_CURRENT = r'C:\Users\pgoff\OneDrive\Desktop\Melange Systems\ops\release_lib\current.py'      # $MELANGE_RELEASE_CURRENT moves it
COMPOSE_FILE = REPO / 'compute.compose.yaml'
EXPORT_BIND_FILE = REPO / 'compute.export-bind.compose.yaml'
STORE_META = ('schema_sha256', 'objects', 'user_version')


# ---- comparison (pure: two or three folders in, a verdict per artifact out) ------------------------------------------

def integrity_ok(path):
    try:
        with closing(B.open_readonly(path)) as connection:
            return connection.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
    except sqlite3.Error:
        return False


def compare_store(name, a1, a2, b):
    """(ok, detail) for one SQLite store in folders a1, a2 (Windows) and b (container)."""
    paths = {label: Path(folder) / name for label, folder in (('A1', a1), ('A2', a2), ('B', b))}
    missing = [label for label, path in paths.items() if not path.is_file()]
    if missing:
        return False, 'missing in ' + ', '.join(missing)
    broken = [label for label, path in paths.items() if not integrity_ok(path)]
    if broken:
        return False, 'PRAGMA integrity_check is not ok in ' + ', '.join(broken)
    digests = {label: B.store_digest(path) for label, path in paths.items()}
    for key in STORE_META:
        if digests['A1'][key] != digests['A2'][key]:
            return False, 'the two Windows builds differ in %s: the reference is not stable' % key
        if digests['A1'][key] != digests['B'][key]:
            return False, 'B differs from the Windows build in %s' % key
    tables = {label: digest['tables'] for label, digest in digests.items()}
    names = set(tables['A1']) | set(tables['A2']) | set(tables['B'])
    noise = sorted(t for t in names if tables['A1'].get(t) != tables['A2'].get(t))
    differ = sorted(t for t in names if t not in noise and tables['A1'].get(t) != tables['B'].get(t))
    if differ:
        return False, 'B differs from the Windows build in table(s): ' + ', '.join(differ)
    rows = sum(t['rows'] for t in tables['B'].values())
    return True, '%d tables, %s rows; run-to-run variance (left out): %s' % (len(tables['B']), '{:,}'.format(rows),
                                                                           ', '.join(noise) or 'none')


def compare_file(name, a1, a2, b):
    """(ok, detail) for a non-SQLite artifact: the bytes must be the same in all three folders."""
    paths = {label: Path(folder) / name for label, folder in (('A1', a1), ('A2', a2), ('B', b))}
    missing = [label for label, path in paths.items() if not path.is_file()]
    if missing:
        return False, 'missing in ' + ', '.join(missing)
    shas = {label: B.sha256_file(path) for label, path in paths.items()}
    if shas['A1'] != shas['A2']:
        return False, 'the two Windows builds differ byte for byte: the reference is not stable'
    if shas['A1'] != shas['B']:
        return False, 'B differs from the Windows build byte for byte'
    return True, 'byte-identical, sha256 %s' % shas['B'][:12]


def compare(name, a1, a2, b):
    return (compare_store if name.endswith('.sqlite3') else compare_file)(name, a1, a2, b)


def baseline_note(name, folder):
    """Non-gating: does the Windows store equal the one co_baseline recorded long before (tests/fixtures/co_baseline.json)?"""
    try:
        recorded = json.loads(B.FIXTURE.read_text(encoding='utf-8'))['stores'].get(name.removesuffix('.sqlite3'))
        if recorded is None:
            return None
        return 'equals the recorded co_baseline fixture: %s' % ('yes' if recorded == B.store_digest(Path(folder) / name) else 'no')
    except (OSError, ValueError, KeyError, sqlite3.Error):
        return None


def noise_tables(name, a1, a2):
    """The tables whose digest differs between the two Windows builds of a store: the legitimate run-to-run variance."""
    first, second = (B.store_digest(Path(folder) / name)['tables'] for folder in (a1, a2))
    return {t for t in set(first) | set(second) if first.get(t) != second.get(t)}


def remove_one_row(path, skip=()):
    """Negative control: delete the last row of the first ordinary table that has rows and is not in `skip` (the variance, which
    the comparison ignores), nor a shadow table of an R-tree or FTS5."""
    tables = B.store_digest(path)['tables']
    virtual = [name for name, digest in tables.items() if digest.get('virtual')]
    for table, digest in sorted(tables.items()):
        if (digest['rows'] and table not in skip and not digest.get('virtual')
                and not any(table.startswith(v + '_') for v in virtual)):
            with closing(sqlite3.connect(path)) as connection, connection:
                try:
                    connection.execute('DELETE FROM %s WHERE rowid = (SELECT max(rowid) FROM %s)' % (B.quote(table), B.quote(table)))
                    return table
                except sqlite3.Error:
                    continue
    raise ValueError('no table to remove a row from in %s' % path)


# ---- the two builds ---------------------------------------------------------------------------------------------------

def windows_env(out):
    env = {k: v for k, v in os.environ.items() if k not in ('CU_DATA_DIR', 'TERRIER_LOCAL_DIR')}
    env['TERRIER_LOCAL_DIR'] = str(out)
    return env


def windows_build(python, repo, district, steps, out):
    for step in steps:
        subprocess.run([python, str(repo / 'tools' / 'build_step.py'), 'run', step, '--district', district],
                       cwd=str(repo), env=windows_env(out), check=True)


def windows_fingerprint(python, repo):
    return json.loads(subprocess.run([python, str(repo / 'tools' / 'build_step.py'), 'fingerprint'], cwd=str(repo),
                                     check=True, capture_output=True, text=True).stdout)


def manifest_image(name):
    """The image the laptop's verified current release manifest names. No manifest, a bad signature or no such name is a refusal
    here, never a guess: the container build does not start."""
    tool = H.env_path('MELANGE_RELEASE_CURRENT', RELEASE_CURRENT)
    done = subprocess.run([sys.executable, str(tool), 'image', name], capture_output=True, text=True, timeout=60)
    image = done.stdout.strip()
    if done.returncode or not image:
        raise SystemExit('compute_parity: no verified image for %s: not running (%s). For a build no signed release names yet, pass '
                         '--image or set TERRIER_BUILD_IMAGE to its digest.' % (name, done.stderr.strip() or 'current.py printed nothing'))
    return image


def docker_exe():
    docker = shutil.which('docker')
    if not docker:
        raise SystemExit('compute_parity: docker is not on PATH (Docker Desktop is not installed or not running)')
    return docker


def compose(image, repo, out, run_id, *args, capture=False, files=()):
    env = {**os.environ, 'TERRIER_BUILD_IMAGE': image, 'HOST_REPO': str(repo), 'HOST_OUT': str(out), 'RUN_ID': run_id}
    overrides = [part for f in files for part in ('-f', str(f))]
    return subprocess.run([docker_exe(), 'compose', '-f', str(COMPOSE_FILE), *overrides, '--profile', 'terrier', *args], env=env,
                          check=True, capture_output=capture, text=True).stdout


def export_via_volume(image, repo, out, run_id, names):
    """The export with /out in a named volume (compute.compose.yaml), then `docker cp` into `out`. The volume is created here
    and given to uid 10001 (a new volume is root's), and is removed again, also when the export fails."""
    docker, volume, holder = docker_exe(), 'terrier-export-' + run_id, 'terrier-export-cp-' + run_id

    def run(*argv):
        subprocess.run([docker, *argv], check=True, stdout=subprocess.DEVNULL)

    run('volume', 'create', volume)
    try:
        run('run', '--rm', '--pull', 'never', '--network', 'none', '--user', '0', '-v', volume + ':/out', '--entrypoint', 'chown',
            image, '10001:10001', '/out')
        compose(image, repo, out, run_id, 'run', '--rm', 'terrier-export', 'export', '/out', *names)
        run('create', '--name', holder, '--pull', 'never', '--network', 'none', '-v', volume + ':/out:ro', image)
        try:
            run('cp', holder + ':/out/.', str(out))
        finally:
            run('rm', '-v', holder)
    finally:
        run('volume', 'rm', volume)


def container_build(image, repo, district, steps, out, run_id, names, export_bind=False):
    for step in steps:
        compose(image, repo, out, run_id, 'run', '--rm', 'terrier-build', 'run', step, '--district', district)
    if export_bind:
        compose(image, repo, out, run_id, 'run', '--rm', 'terrier-export', 'export', '/out', *names, files=[EXPORT_BIND_FILE])
    else:
        export_via_volume(image, repo, out, run_id, names)


def container_fingerprint(image, repo, out, run_id):
    return json.loads(compose(image, repo, out, run_id, 'run', '--rm', 'terrier-build', 'fingerprint', capture=True).splitlines()[-1])


def api_check(python, repo, folder, log):
    """co_baseline's golden endpoint check against a folder of stores: (ok, headline). The full output goes to `log`."""
    done = subprocess.run([python, str(repo / 'tools' / 'co_baseline.py'), 'check', '--parts', 'api', '--data-dir', str(folder),
                           '--python', python], cwd=str(repo), capture_output=True, text=True)
    Path(log).write_text(done.stdout + done.stderr, encoding='utf-8')
    lines = [line for line in done.stdout.splitlines() if line.startswith('CHECK ')]
    return done.returncode == 0, (lines[-1] if lines else 'no CHECK line (see %s)' % log)


# ---- the run ----------------------------------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--scratch', required=True, type=Path, help='a NEW folder for A1, A2, B and the report')
    parser.add_argument('--district', default=D.DEFAULT, choices=list(D.DISTRICTS))
    parser.add_argument('--steps', default='roads', help='comma list of %s (default roads, the smoke)' % ', '.join(sorted(S.STEPS)))
    parser.add_argument('--files', default='', help='comma list of other files to compare byte for byte (relative to the data folder)')
    parser.add_argument('--python', default=os.environ.get('CU_PYTHON') or str(H.WORK_PY), help='the Windows build interpreter')
    parser.add_argument('--image', default=os.environ.get('TERRIER_BUILD_IMAGE'),
                        help='terrier-build image, pinned by digest (default: $TERRIER_BUILD_IMAGE, else the verified release manifest)')
    parser.add_argument('--repo', type=Path, default=REPO, help='the tree both builds run (default: this one)')
    parser.add_argument('--api', action='store_true', help='also run co_baseline\'s golden endpoint check on A1 and B (co-05, full set)')
    parser.add_argument('--negative-control', action='store_true', help='also prove the comparison fails when one row is removed')
    parser.add_argument('--skip-container', action='store_true', help='Windows only: learn the run-to-run variance (A1 vs A2)')
    parser.add_argument('--reuse-windows', action='store_true', help='keep an existing <scratch>/A1 and A2 instead of building them again')
    parser.add_argument('--export-bind', action='store_true',
                        help='export straight into the bind-mounted scratch folder instead of a named volume + docker cp: only for a '
                             'native-Linux host (on Docker Desktop, SQLite integrity_check over the Windows file share takes tens of '
                             'minutes on street)')
    args = parser.parse_args(argv)
    steps = [s for s in args.steps.split(',') if s]
    unknown = [s for s in steps if s not in S.STEPS]
    if unknown or not steps:
        parser.error('unknown step(s) %s; known: %s' % (unknown, ', '.join(sorted(S.STEPS))))
    scratch = args.scratch.resolve()
    if '.local' in scratch.parts:
        parser.error('--scratch must not be inside a .local folder')
    if scratch.exists() and any(scratch.iterdir()) and not args.reuse_windows:
        parser.error('--scratch %s is not empty; a parity run starts from a new folder' % scratch)
    source = 'explicit'
    if not args.skip_container and not args.image:
        args.image, source = manifest_image(IMAGE_NAME), 'manifest'
    if not args.skip_container and '@sha256:' not in args.image:
        parser.error('--image (or $TERRIER_BUILD_IMAGE) must be pinned by digest: ghcr.io/pjg93-222/terrier-build@sha256:...')
    d = D.get(args.district)
    sub = D.data_dir(d, Path('.'))                      # co-05: the folder itself; another district: its folder below it
    names = [(sub / S.STEPS[s].store).as_posix() for s in steps] + [(sub / f).as_posix() for f in args.files.split(',') if f]
    folders = {label: scratch / label for label in ('A1', 'A2', 'B')}
    run_id = 'B-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    report = {'district': d['id'], 'steps': steps, 'image': args.image, 'image_source': source, 'results': []}

    for label in ('A1', 'A2'):
        if args.reuse_windows and folders[label].is_dir():
            print('%s: reusing %s' % (label, folders[label]), flush=True)
            continue
        print('%s: Windows build of %s' % (label, ', '.join(steps)), flush=True)
        folders[label].mkdir(parents=True, exist_ok=True)
        windows_build(args.python, args.repo, d['id'], steps, folders[label])
    report['fingerprint_windows'] = windows_fingerprint(args.python, args.repo)
    if not args.skip_container:
        print('B: container build of %s (stage volume %s)' % (', '.join(steps), run_id), flush=True)
        folders['B'].mkdir(parents=True, exist_ok=True)
        container_build(args.image, args.repo, d['id'], steps, folders['B'], run_id, names, export_bind=args.export_bind)
        report['fingerprint_container'] = container_fingerprint(args.image, args.repo, folders['B'], run_id)

    def verdict(name, ok, detail):
        print('%-5s %-34s %s' % ('PASS' if ok else 'FAIL', name, detail), flush=True)
        report['results'].append({'artifact': name, 'ok': ok, 'detail': detail})

    for name in names:
        if args.skip_container:
            ok, detail = compare(name, folders['A1'], folders['A2'], folders['A2'])
            detail = '(Windows only) ' + detail
        else:
            ok, detail = compare(name, folders['A1'], folders['A2'], folders['B'])
        verdict(name, ok, detail)
        note = baseline_note(name, folders['A1']) if name.endswith('.sqlite3') and d['id'] == D.DEFAULT else None
        if note:
            print('      %-34s %s' % ('', note), flush=True)
    if not args.skip_container and args.negative_control:
        for name in (n for n in names if n.endswith('.sqlite3')):
            control = scratch / 'NC'
            target = control / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(folders['B'] / name, target)
            table = remove_one_row(target, noise_tables(name, folders['A1'], folders['A2']))
            ok, detail = compare_store(name, folders['A1'], folders['A2'], control)
            verdict('negative control ' + name, not ok, 'one row removed from %s: %s' % (table, 'detected' if not ok else 'NOT detected'))
    if args.api and not args.skip_container:
        results = {}
        for label in ('A1', 'B'):
            results[label] = api_check(args.python, args.repo, folders[label], scratch / ('api_%s.log' % label))
        ok = results['B'][0]
        verdict('golden endpoints', ok, '; '.join('%s: %s' % (k, v[1]) for k, v in results.items())
                + ('' if results['A1'][0] or not ok else ' (the Windows build does not match the fixture either)'))

    failed = [r for r in report['results'] if not r['ok']]
    report['ok'] = not failed
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / 'parity_report.json').write_text(json.dumps(report, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    print('PARITY %s: %d artifact check(s), %d failed; report %s' % ('PASS' if not failed else 'FAIL', len(report['results']),
                                                                   len(failed), scratch / 'parity_report.json'))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
