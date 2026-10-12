"""Synthetic checks for the compute-plane build lane: the path seams (tools/hostpaths.py), the build step runner
(tools/build_step.py), the parity harness (tools/compute_parity.py) and the files that make the build image (Dockerfile.build,
compute.compose.yaml, the two hashed locks, the build job of .github/workflows/release.yml). No real data, no Docker, no network.

    <venv python> -m unittest tools.test_compute          (from the V3 folder)
"""
import contextlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path, PureWindowsPath
from unittest import mock

import geopandas as gpd
from shapely.geometry import LineString

from tools import build_step as S
from tools import compute_parity as P

REPO = Path(__file__).resolve().parents[1]
CODEX = r'C:\Users\pgoff\Documents\Codex\2026-09-27\we-are-going-to-have-an'


def run_py(code, extra=None):
    """Run `code` in a fresh interpreter from the repo with no TERRIER_*/CU_* variables except `extra`; its stdout parsed as JSON."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(('TERRIER_', 'CU_'))}
    env.update(extra or {})
    done = subprocess.run([sys.executable, '-c', code], cwd=str(REPO), env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def quiet_home(folder):
    return {'HOME': str(folder), 'USERPROFILE': str(folder)}      # tools/glassbox.py writes to ~/.claude/glassbox/events


PRINT_PATHS = """
import json
from tools import districts as D, build_scores_v3 as BS
from tools.luna_district import common as LC, rowfw
co, orr, ct = (D.get(i) for i in ('co-05', 'or-05', 'ct-01'))
print(json.dumps({
 'D.LOCAL': D.LOCAL, 'D.WORK': D.WORK, 'D.GEOGRAPHY': D.GEOGRAPHY, 'D.SIBLING_LOCAL': D.SIBLING_LOCAL, 'D.DOWNLOADS': D.DOWNLOADS,
 'co.source_csv': co['source_csv'], 'co.geography': co['geography'], 'co.street_salt': D.street_salt(co),
 'co.rowfw_root': co['rowfw_root'], 'co.data_dir': D.data_dir(co), 'or.data_dir': D.data_dir(orr),
 'or.source_csv': orr['source_csv'], 'or.geography': orr['geography'], 'ct.geography': ct['geography'],
 'or.rowfw_root': orr['rowfw_root'], 'BS.ROOT': BS.ROOT, 'BS.RF': BS.RF, 'BS.RFB': BS.RFB,
 'LC.LUNA': LC.LUNA, 'LC.L': LC.L, 'LC.WORK_PY': LC.WORK_PY, 'LC.PY312': LC.PY312, 'LC.GLOBAL_DIR': LC.GLOBAL_DIR,
 'LC.co_csv': LC.DISTRICTS['co-replay']['source_csv'], 'LC.or_csv': LC.DISTRICTS['or-05']['source_csv'],
 'LC.ct_csv': LC.DISTRICTS['ct-01']['source_csv'], 'LC.or_dir': LC.district_dir('or-05'), 'rowfw.SCRATCH_CO': rowfw.SCRATCH_CO,
}, default=str))
"""


class PathSeamTests(unittest.TestCase):
    def test_without_environment_every_path_is_todays_windows_path(self):
        """The laptop keeps working exactly as before: each default is the literal the tools used to carry."""
        found = run_py(PRINT_PATHS)
        work, down = CODEX + r'\work', r'C:\Users\pgoff\Downloads'
        sibling = CODEX + r'\outputs\cu-geography'
        expected = {
            'D.WORK': work, 'D.GEOGRAPHY': work + r'\geography', 'D.SIBLING_LOCAL': sibling + r'\.local', 'D.DOWNLOADS': down,
            'co.source_csv': down + r'\statara_co_cd005_20260904.csv', 'co.geography': work + r'\geography',
            'co.street_salt': sibling + r'\.local\.street-salt',
            'co.rowfw_root': sibling + r'\.local\row-framework-20260930',
            'or.source_csv': down + r'\terrier-sources\statara_or_cd005_20260904.csv',
            'or.geography': work + r'\geography\or-05', 'ct.geography': work + r'\geography\ct-01',
            'BS.ROOT': sibling, 'BS.RF': sibling + r'\.local\row-framework-20260930',
            'BS.RFB': sibling + r'\.local\row-framework-20260930\build\rowfw-1.0.0',
            'LC.LUNA': sibling, 'LC.L': sibling + r'\.local', 'LC.WORK_PY': work + r'\.venv\Scripts\python.exe',
            'LC.PY312': r'C:\Users\pgoff\AppData\Local\Programs\Python\Python312\python.exe',
            'LC.co_csv': down + r'\statara_co_cd005_20260904.csv',
            'LC.or_csv': down + r'\terrier-sources\statara_or_cd005_20260904.csv',
            'LC.ct_csv': down + r'\terrier-sources\statara_ct_cd001_20260904.csv',
            'rowfw.SCRATCH_CO': (r'C:\Users\pgoff\AppData\Local\Temp\claude\C--Users-pgoff-OneDrive-Desktop-Melange-Systems--claude-worktrees-'
                                 r'codex-websocket-error-468fa9\60d3bc55-b6fd-473b-9b78-a958ba7ee615\scratchpad')}
        for key, literal in expected.items():
            self.assertEqual(PureWindowsPath(found[key]), PureWindowsPath(literal), key)
            if os.name == 'nt':
                self.assertEqual(found[key], literal, key)              # byte for byte on the laptop
        local = Path(found['D.LOCAL'])
        self.assertEqual(local, REPO / '.local')
        self.assertEqual(Path(found['co.data_dir']), local)
        self.assertEqual(Path(found['or.data_dir']), local / 'or-05')
        self.assertEqual(Path(found['or.rowfw_root']), local / 'or-05' / 'rowfw')
        self.assertEqual(Path(found['LC.GLOBAL_DIR']), local / '_phase3')
        self.assertEqual(Path(found['LC.or_dir']), local / 'or-05' / 'luna-v1')

    def test_each_variable_moves_what_it_names(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        env = {'TERRIER_SOURCES_DIR': root / 'sources', 'TERRIER_GEOGRAPHY_DIR': root / 'geo', 'TERRIER_SIBLING_DIR': root / 'sib',
               'TERRIER_LOCAL_DIR': root / 'out', 'TERRIER_WORK_PY': root / 'py' / 'work', 'TERRIER_PY312': root / 'py' / '312',
               'TERRIER_LUNA_SCRATCH': root / 'scratch'}
        found = {k: Path(v) for k, v in run_py(PRINT_PATHS, {k: str(v) for k, v in env.items()}).items()}
        self.assertEqual(found['D.DOWNLOADS'], root / 'sources')
        self.assertEqual(found['co.source_csv'], root / 'sources' / 'statara_co_cd005_20260904.csv')
        self.assertEqual(found['or.source_csv'], root / 'sources' / 'terrier-sources' / 'statara_or_cd005_20260904.csv')
        self.assertEqual(found['LC.ct_csv'], root / 'sources' / 'terrier-sources' / 'statara_ct_cd001_20260904.csv')
        self.assertEqual(found['co.geography'], root / 'geo')
        self.assertEqual(found['or.geography'], root / 'geo' / 'or-05')
        self.assertEqual(found['ct.geography'], root / 'geo' / 'ct-01')
        self.assertEqual(found['co.street_salt'], root / 'sib' / '.local' / '.street-salt')
        self.assertEqual(found['co.rowfw_root'], root / 'sib' / '.local' / 'row-framework-20260930')
        self.assertEqual(found['BS.ROOT'], root / 'sib')
        self.assertEqual(found['BS.RFB'], root / 'sib' / '.local' / 'row-framework-20260930' / 'build' / 'rowfw-1.0.0')
        self.assertEqual(found['LC.LUNA'], root / 'sib')
        self.assertEqual(found['LC.WORK_PY'], root / 'py' / 'work')
        self.assertEqual(found['LC.PY312'], root / 'py' / '312')
        self.assertEqual(found['rowfw.SCRATCH_CO'], root / 'scratch')
        self.assertEqual(found['D.LOCAL'], root / 'out')
        self.assertEqual(found['co.data_dir'], root / 'out')
        self.assertEqual(found['or.data_dir'], root / 'out' / 'or-05')
        self.assertEqual(found['or.rowfw_root'], root / 'out' / 'or-05' / 'rowfw')
        self.assertEqual(found['LC.GLOBAL_DIR'], root / 'out' / '_phase3')
        self.assertEqual(found['LC.or_dir'], root / 'out' / 'or-05' / 'luna-v1')

    def test_an_empty_variable_means_the_default(self):
        found = run_py(PRINT_PATHS, {'TERRIER_SOURCES_DIR': '', 'TERRIER_LOCAL_DIR': ''})
        self.assertEqual(PureWindowsPath(found['D.DOWNLOADS']), PureWindowsPath(r'C:\Users\pgoff\Downloads'))
        self.assertEqual(Path(found['D.LOCAL']), REPO / '.local')


def write_roads_zip(path):
    """A zipped shapefile like the Census roads file: two kept lines (S1400 named, S1100 unnamed) and one dropped kind."""
    folder = Path(tempfile.mkdtemp(dir=path.parent))
    frame = gpd.GeoDataFrame({'MTFCC': ['S1400', 'S1100', 'S1500'], 'LINEARID': ['1', '2', '3'], 'FULLNAME': ['Main St', None, 'Trail']},
                             geometry=[LineString([(0, 0), (1, 0)]), LineString([(0, 1), (1, 1)]), LineString([(0, 2), (1, 2)])], crs=4326)
    frame.to_file(folder / 'roads.shp')
    with zipfile.ZipFile(path, 'w') as archive:
        for part in sorted(folder.iterdir()):
            archive.write(part, part.name)


class BuildStepTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(('TERRIER_', 'CU_'))}, **quiet_home(self.root)}

    def step(self, *args, **extra):
        env = {**self.env, **{k: str(v) for k, v in extra.items()}}
        return subprocess.run([sys.executable, str(REPO / 'tools' / 'build_step.py'), *args], cwd=str(REPO), env=env,
                              capture_output=True, text=True)

    def test_roads_builds_from_a_tiny_geography_and_never_overwrites(self):
        (self.root / 'geo').mkdir()
        write_roads_zip(self.root / 'geo' / 'roads.zip')
        out = self.root / 'out'
        done = self.step('run', 'roads', '--district', 'co-05', TERRIER_GEOGRAPHY_DIR=self.root / 'geo', TERRIER_LOCAL_DIR=out)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout.splitlines()[-1])['store'], 'roads.sqlite3')
        digest = P.B.store_digest(out / 'roads.sqlite3')
        self.assertEqual(digest['tables']['roads']['rows'], 2)
        self.assertEqual(digest['tables']['extents']['rows'], 2)
        self.assertTrue(P.integrity_ok(out / 'roads.sqlite3'))
        again = self.step('run', 'roads', '--district', 'co-05', TERRIER_GEOGRAPHY_DIR=self.root / 'geo', TERRIER_LOCAL_DIR=out)
        self.assertNotEqual(again.returncode, 0)                      # the tool refuses to replace a store
        self.assertEqual(P.B.store_digest(out / 'roads.sqlite3'), digest)

    def test_a_run_without_a_local_dir_is_refused(self):
        done = self.step('run', 'roads')
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('TERRIER_LOCAL_DIR is not set', done.stderr)

    def test_a_missing_salt_stops_the_step_before_anything_is_built(self):
        done = self.step('run', 'street', TERRIER_SIBLING_DIR=self.root / 'sib', TERRIER_LOCAL_DIR=self.root / 'out')
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('street salt', done.stderr)
        self.assertFalse((self.root / 'out' / 'street.sqlite3').exists())

    def test_exit_zero_without_a_store_is_not_completion(self):
        with mock.patch.dict(os.environ, {'TERRIER_LOCAL_DIR': str(self.root / 'out')}), \
                mock.patch.object(S.D, 'data_dir', return_value=self.root / 'out'), \
                mock.patch.object(S.subprocess, 'call', return_value=0), mock.patch.object(S, 'GlassBox'):
            self.assertEqual(S.run('roads', 'co-05'), 1)

    def test_export_copies_with_a_receipt_and_stays_inside_the_stage(self):
        stage, out = self.root / 'stage', self.root / 'out'
        stage.mkdir()
        make_store(stage / 'roads.sqlite3', [(1, 'a'), (2, 'b')])
        (stage / 'area.csv').write_text('a,b\n1,2\n', encoding='utf-8')
        done = self.step('export', str(out), 'roads.sqlite3', 'area.csv', TERRIER_LOCAL_DIR=stage)
        self.assertEqual(done.returncode, 0, done.stderr)
        receipt = json.loads((out / 'export_receipt.json').read_text(encoding='utf-8'))
        self.assertEqual(sorted(receipt), ['area.csv', 'roads.sqlite3'])
        self.assertEqual(receipt['area.csv']['sha256'], P.B.sha256_file(stage / 'area.csv'))
        self.assertEqual(P.B.store_digest(out / 'roads.sqlite3'), P.B.store_digest(stage / 'roads.sqlite3'))
        escape = self.step('export', str(out), '../secret.sqlite3', TERRIER_LOCAL_DIR=stage)
        self.assertNotEqual(escape.returncode, 0)

    def test_fingerprint_names_the_interpreter_and_sqlite(self):
        found = json.loads(self.step('fingerprint').stdout)
        self.assertEqual(found['python'], '.'.join(map(str, sys.version_info[:3])))
        self.assertEqual(found['sqlite'], sqlite3.sqlite_version)


# ---- the parity harness ------------------------------------------------------------------------------------------------------

def make_store(path, rows, stamp='t0', extra_column=False):
    """A tiny store shaped like roads.sqlite3: an ordinary table, an R-tree, and a metadata table that carries a stamp."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript('CREATE TABLE roads (id INTEGER PRIMARY KEY, name TEXT%s);' % (', extra TEXT' if extra_column else '')
                                 + 'CREATE VIRTUAL TABLE extents USING rtree(id, west, east, south, north);'
                                 + 'CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);')
        for number, name in rows:
            connection.execute('INSERT INTO roads (id, name) VALUES (?, ?)', (number, name))
            connection.execute('INSERT INTO extents VALUES (?, 0, 1, 0, 1)', (number,))
        connection.execute("INSERT INTO metadata VALUES ('built_utc', ?)", (stamp,))


ROWS = [(1, 'Main St'), (2, 'Oak Ave'), (3, '')]


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def stores(self, a1=None, a2=None, b=None):
        for label, kwargs in (('A1', a1), ('A2', a2), ('B', b)):
            kwargs = {'rows': ROWS, **(kwargs or {})}
            make_store(self.root / label / 'roads.sqlite3', **kwargs)
        return [self.root / label for label in ('A1', 'A2', 'B')]

    def compare(self, *folders, name='roads.sqlite3'):
        return P.compare(name, *folders)

    def test_identical_stores_pass(self):
        ok, detail = self.compare(*self.stores())
        self.assertTrue(ok, detail)
        self.assertIn('variance (left out): none', detail)

    def test_a_stamp_that_differs_between_the_two_windows_builds_is_left_out_everywhere(self):
        ok, detail = self.compare(*self.stores(a1={'stamp': 't1'}, a2={'stamp': 't2'}, b={'stamp': 't3'}))
        self.assertTrue(ok, detail)
        self.assertIn('variance (left out): metadata', detail)

    def test_a_stamp_that_is_stable_on_windows_must_match(self):
        ok, detail = self.compare(*self.stores(b={'stamp': 'other'}))
        self.assertFalse(ok)
        self.assertIn('metadata', detail)

    def test_one_changed_row_fails_in_its_table_only(self):
        ok, detail = self.compare(*self.stores(b={'rows': ROWS[:2] + [(3, 'Elm Rd')]}))
        self.assertFalse(ok)
        self.assertIn('table(s): roads', detail)
        self.assertNotIn('extents', detail)

    def test_a_schema_change_fails(self):
        ok, detail = self.compare(*self.stores(b={'extra_column': True}))
        self.assertFalse(ok)
        self.assertIn('schema_sha256', detail)

    def test_windows_builds_that_differ_in_schema_make_the_reference_unstable(self):
        ok, detail = self.compare(*self.stores(a2={'extra_column': True}))
        self.assertFalse(ok)
        self.assertIn('reference is not stable', detail)

    def test_a_missing_store_fails(self):
        folders = self.stores()
        (folders[2] / 'roads.sqlite3').unlink()
        ok, detail = self.compare(*folders)
        self.assertFalse(ok)
        self.assertEqual(detail, 'missing in B')

    def test_a_damaged_store_fails_the_integrity_check(self):
        folders = self.stores()
        damaged = folders[2] / 'roads.sqlite3'
        damaged.write_bytes(damaged.read_bytes()[:-4096])           # the header still counts the last page
        ok, detail = self.compare(*folders)
        self.assertFalse(ok)
        self.assertIn('integrity_check', detail)

    def test_files_compare_byte_for_byte(self):
        for label, payload in (('A1', b'zip'), ('A2', b'zip'), ('B', b'zip')):
            (self.root / label).mkdir(parents=True, exist_ok=True)
            (self.root / label / 'b.zip').write_bytes(payload)
        folders = [self.root / label for label in ('A1', 'A2', 'B')]
        self.assertTrue(P.compare('b.zip', *folders)[0])
        (folders[2] / 'b.zip').write_bytes(b'zip!')
        ok, detail = P.compare('b.zip', *folders)
        self.assertFalse(ok)
        self.assertIn('B differs', detail)
        (folders[1] / 'b.zip').write_bytes(b'other')
        self.assertIn('reference is not stable', P.compare('b.zip', *folders)[1])

    def test_negative_control_removes_one_row_and_is_detected(self):
        folders = self.stores()
        control = self.root / 'NC'
        control.mkdir()
        shutil.copyfile(folders[2] / 'roads.sqlite3', control / 'roads.sqlite3')
        self.assertEqual(P.noise_tables('roads.sqlite3', folders[0], folders[1]), set())
        table = P.remove_one_row(control / 'roads.sqlite3')
        self.assertEqual(table, 'metadata')
        self.assertFalse(P.compare_store('roads.sqlite3', folders[0], folders[1], control)[0])

    def test_negative_control_skips_the_variance_tables(self):
        folders = self.stores(a1={'stamp': 't1'}, a2={'stamp': 't2'})
        self.assertEqual(P.noise_tables('roads.sqlite3', folders[0], folders[1]), {'metadata'})
        control = self.root / 'NC'
        control.mkdir()
        shutil.copyfile(folders[2] / 'roads.sqlite3', control / 'roads.sqlite3')
        self.assertEqual(P.remove_one_row(control / 'roads.sqlite3', {'metadata'}), 'roads')
        self.assertFalse(P.compare_store('roads.sqlite3', folders[0], folders[1], control)[0])


class RunTests(unittest.TestCase):
    """main() with the two builds replaced by functions that write tiny stores."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.calls = []

    def fakes(self, b_rows=ROWS):
        def windows(python, repo, district, steps, out):
            self.calls.append(('windows', out.name))
            make_store(out / 'roads.sqlite3', ROWS, stamp='w%d' % len(self.calls))

        def container(image, repo, district, steps, out, run_id, names):
            self.calls.append(('container', out.name, tuple(names)))
            make_store(out / 'roads.sqlite3', b_rows, stamp='c')

        return [mock.patch.object(P, 'windows_build', windows), mock.patch.object(P, 'container_build', container),
                mock.patch.object(P, 'windows_fingerprint', lambda python, repo: {'python': 'win'}),
                mock.patch.object(P, 'container_fingerprint', lambda image, repo, out, run_id: {'python': 'box'})]

    def main(self, *args, b_rows=ROWS, scratch=None):
        argv = ['--scratch', str(scratch or self.root / 'scratch'), '--image', 'ghcr.io/x/terrier-build@sha256:' + 'a' * 64, *args]
        out = io.StringIO()
        with contextlib.ExitStack() as stack:
            for patch in self.fakes(b_rows):
                stack.enter_context(patch)
            stack.enter_context(contextlib.redirect_stdout(out))
            code = P.main(argv)
        return code, out.getvalue()

    def test_a_matching_container_build_passes_in_order(self):
        code, text = self.main('--negative-control')
        self.assertEqual(code, 0, text)
        self.assertEqual([c[:2] for c in self.calls], [('windows', 'A1'), ('windows', 'A2'), ('container', 'B')])
        self.assertEqual(self.calls[2][2], ('roads.sqlite3',))
        self.assertRegex(text, r'PASS  roads\.sqlite3 .*variance \(left out\): metadata')
        self.assertRegex(text, r'PASS  negative control roads\.sqlite3 .*detected')
        self.assertIn('PARITY PASS', text)
        report = json.loads((self.root / 'scratch' / 'parity_report.json').read_text(encoding='utf-8'))
        self.assertTrue(report['ok'])
        self.assertEqual(report['fingerprint_container'], {'python': 'box'})

    def test_a_different_container_store_fails_the_run(self):
        code, text = self.main(b_rows=ROWS[:2])
        self.assertEqual(code, 1)
        self.assertRegex(text, r'FAIL  roads\.sqlite3 .*roads')
        self.assertIn('PARITY FAIL', text)

    def test_windows_only_learns_the_variance_without_docker(self):
        code, text = self.main('--skip-container')
        self.assertEqual(code, 0, text)
        self.assertEqual([c[0] for c in self.calls], ['windows', 'windows'])
        self.assertIn('(Windows only)', text)

    def test_reuse_windows_skips_the_windows_builds(self):
        self.main('--skip-container')
        self.calls.clear()
        code, text = self.main('--reuse-windows')
        self.assertEqual(code, 0, text)
        self.assertEqual([c[0] for c in self.calls], ['container'])

    def test_refusals(self):
        with contextlib.redirect_stderr(io.StringIO()):
            for argv in (['--scratch', str(self.root / '.local' / 'x'), '--image', 'x@sha256:' + 'a' * 64],
                         ['--scratch', str(self.root / 'new'), '--image', 'ghcr.io/x/terrier-build:v1'],
                         ['--scratch', str(self.root / 'new'), '--image', 'x@sha256:' + 'a' * 64, '--steps', 'nope']):
                with self.assertRaises(SystemExit) as stop:
                    P.main(argv)
                self.assertEqual(stop.exception.code, 2, argv)
            (self.root / 'full').mkdir()
            (self.root / 'full' / 'file').write_text('x')
            with self.assertRaises(SystemExit):
                P.main(['--scratch', str(self.root / 'full'), '--image', 'x@sha256:' + 'a' * 64])


# ---- the image, the compose file, the locks, the workflow ------------------------------------------------------------------------

def read(name):
    return (REPO / name).read_text(encoding='utf-8')


def pins(text):
    """{normalised name: version} of the `name==version` lines of a requirements file or lock."""
    return {re.sub(r'[-_.]+', '-', m.group(1).lower()): m.group(2)
            for m in re.finditer(r'^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\]+)', text, re.M)}


class BuildImageFilesTests(unittest.TestCase):
    def test_the_geo_lock_is_the_laptop_set_and_every_entry_is_hashed(self):
        lock = read('requirements.build.lock')
        self.assertEqual(pins(lock), pins(read('requirements.lock.txt')))
        for entry in re.split(r'\n(?=\S)', '\n'.join(l for l in lock.splitlines() if not l.startswith('#'))):
            self.assertRegex(entry, r'--hash=sha256:[0-9a-f]{64}', entry[:40])

    def test_the_score_lock_is_the_pinned_input_and_every_entry_is_hashed(self):
        lock = read('requirements.score.lock')
        self.assertEqual(pins(lock), pins(read('requirements.score.in')))
        for entry in re.split(r'\n(?=\S)', '\n'.join(l for l in lock.splitlines() if not l.startswith('#'))):
            self.assertRegex(entry, r'--hash=sha256:[0-9a-f]{64}', entry[:40])

    def test_the_two_interpreters_are_the_ones_the_windows_steps_use(self):
        text = read('Dockerfile.build')
        froms = re.findall(r'^FROM (\S+) AS (\S+)', text, re.M)
        self.assertEqual([name for _ref, name in froms], ['geo-deps', 'geo', 'score-deps', 'score'])
        # geo is Debian 13 (D-181); score stays on bookworm because python:3.12.10 has no trixie image (see the header of Dockerfile.build)
        self.assertEqual([ref.split('@')[0] for ref, _ in froms], ['python:3.12.14-slim-trixie', 'python:3.12.14-slim-trixie',
                                                                  'python:3.12.10-slim-bookworm', 'python:3.12.10-slim-bookworm'])
        for ref, _name in froms:
            self.assertRegex(ref, r'@sha256:[0-9a-f]{64}$')
        self.assertEqual(froms[0][0], froms[1][0])
        self.assertEqual(froms[2][0], froms[3][0])
        self.assertEqual(text.count('apt-get -y --no-install-recommends upgrade'), 1)       # the frozen score base gets Debian's security updates

    def test_the_image_is_toolchain_only_non_root_and_runs_a_named_step(self):
        text = read('Dockerfile.build')
        copies = re.findall(r'^COPY (.+)$', text, re.M)
        self.assertEqual(sorted(copies), sorted(['requirements.build.lock /tmp/requirements.lock', '--from=geo-deps /opt/venv /opt/venv',
                                                 'requirements.score.lock /tmp/requirements.lock', '--from=score-deps /opt/venv /opt/venv']))
        self.assertEqual(text.count('USER 10001:10001'), 2)
        self.assertEqual(text.count('ENTRYPOINT ["python", "/terrier/tools/build_step.py"]'), 2)
        self.assertEqual(text.count('--require-hashes --no-deps --only-binary=:all:'), 2)
        context = [l for l in read('Dockerfile.build.dockerignore').splitlines() if l and not l.startswith('#')]
        self.assertEqual(context, ['**', '!requirements.build.lock', '!requirements.score.lock'])

    def test_compose_mounts_inputs_read_only_writes_only_stage_and_out_and_has_no_network(self):
        text = read('compute.compose.yaml')
        self.assertNotIn('cu-geography-v3', text)                   # the live checkout (and its .local) is never a mount source
        self.assertEqual(text.count('network_mode: none'), 1)       # defined once in the shared block both services merge
        for key in ('read_only: true', 'cap_drop: [ALL]', 'no-new-privileges:true', 'mem_limit:', 'cpus:', 'pids_limit:'):
            self.assertIn(key, text)
        mounts, current = [], None
        for line in text.splitlines():
            if re.match(r'\s+- type: (bind|volume)', line):
                current = {'type': line.split(':')[1].strip()}
                mounts.append(current)
            elif current is not None and (m := re.match(r'\s+(source|target|read_only): (.+)$', line)):
                current[m.group(1)] = m.group(2).strip()
        self.assertGreaterEqual(len(mounts), 10)
        writable = sorted(m['target'] for m in mounts if m.get('read_only') != 'true')
        self.assertEqual(writable, ['/glassbox-home/.claude/glassbox/events', '/out', '/stage'])
        self.assertEqual({m['target'] for m in mounts if m['type'] == 'bind' and m.get('read_only') == 'true'},
                         {'/terrier', '/sources/statara_co_cd005_20260904.csv', '/sources/terrier-sources', '/geography',
                          '/sibling/.local/.street-salt', '/sibling/.local/row-framework-20260930'})

    def test_the_release_workflow_builds_the_two_images_by_digest_with_pinned_actions(self):
        text = read('.github/workflows/release.yml')
        for use in re.findall(r'^\s+(?:- )?uses: (\S+)', text, re.M):
            self.assertRegex(use, r'^[\w./-]+@[0-9a-f]{40}$', use)
        self.assertIn('BUILD_IMAGE: ghcr.io/pjg93-222/terrier-build\n', text.replace('\r\n', '\n'))
        self.assertEqual(text.count('file: Dockerfile.build'), 2)
        self.assertIn('needs: [image, build-image, scan]', text)      # the draft release waits for the image scan
        self.assertIn('tools.test_compute', text)
        self.assertIn('tools.test_image_gate', text)
        self.assertIn('python tools/image_gate.py "$WEB" "$GEO" "$SCORE"', text)
        self.assertRegex(text, r'TRIVY_SHA256: [0-9a-f]{64}')
        self.assertEqual(text.count('no-cache: true'), 3)           # the web image and both build images


if __name__ == '__main__':
    unittest.main()
