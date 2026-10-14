"""Synthetic checks for the compute-plane build lane: the path seams (tools/hostpaths.py), the build step runner
(tools/build_step.py), the parity harness (tools/compute_parity.py) and the files that make the build image (Dockerfile.build,
compute.compose.yaml, the two hashed locks, the build job of .github/workflows/release.yml). No real data, no Docker, no network.

    <venv python> -m unittest tools.test_compute          (from the V3 folder)
"""
import contextlib
import io
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path, PureWindowsPath
from unittest import mock

import geopandas as gpd
from shapely.geometry import LineString

from tools import build_step as S
from tools import compute_build as CB
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

    # The five steps, in the order Terrier's Windows runner (_build/run_nowithhold.ps1) runs them, each with its toolchain.

    def test_the_steps_are_the_windows_runners_in_its_order_with_their_toolchains(self):
        self.assertEqual(list(S.STEPS), ['roads', 'street', 'contacts', 'shown', 'scores', 'walk', 'ingest', 'extend', 'bundle', 'density'])
        self.assertEqual({name: spec.image for name, spec in S.STEPS.items()},
                         {'roads': 'geo', 'street': 'geo', 'contacts': 'geo', 'shown': 'score', 'scores': 'score', 'walk': 'geo',
                          'ingest': 'geo', 'extend': 'geo', 'bundle': 'geo', 'density': 'geo'})
        self.assertEqual((S.STEPS['shown'].script, S.STEPS['shown'].store), ('tools/shown_areas.py', 'area_scores_shown.csv'))
        self.assertEqual((S.STEPS['scores'].script, S.STEPS['scores'].store), ('tools/build_scores_v3.py', 'scores.sqlite3'))
        for spec in S.STEPS.values():
            self.assertTrue((REPO / spec.script).is_file(), spec.script)
            self.assertIn(spec.image, P.IMAGE_NAMES)
        # the file name each tool itself writes: D.area_table, and the store the app and the bundle read
        self.assertEqual(S.D.area_table(S.D.get('co-05')).name, S.STEPS['shown'].store)
        self.assertEqual(S.D.area_table(S.D.get('co-05')).with_suffix('.manifest.json').name, S.STEPS['shown'].also[0])

    def run_recorded(self, step, district='co-05', written=('area_scores_shown.csv', 'area_scores_shown.manifest.json', 'scores.sqlite3')):
        """S.run(step) with the tool replaced by a recorder that writes `written` into the data directory; (exit code, command)."""
        data = self.root / 'out'
        shutil.rmtree(data, ignore_errors=True)
        commands = []

        def fake_call(command, **kwargs):
            commands.append(command)
            data.mkdir(parents=True, exist_ok=True)
            for name in written:
                (data / name).write_text('x', encoding='utf-8')
            return 0

        with mock.patch.dict(os.environ, {'TERRIER_LOCAL_DIR': str(data)}), mock.patch.object(S.D, 'data_dir', return_value=data), \
                mock.patch.object(S.subprocess, 'call', fake_call), mock.patch.object(S, 'GlassBox'), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return S.run(step, district), commands[0]

    def test_shown_and_scores_run_their_tools_plain_and_the_receipt_goes_beside_the_store(self):
        code, command = self.run_recorded('shown')
        self.assertEqual(code, 0)
        self.assertEqual(command, [sys.executable, str(REPO / 'tools' / 'shown_areas.py'), '--district', 'co-05'])
        code, command = self.run_recorded('scores')
        self.assertEqual(code, 0)
        # build_scores_v3 would write its receipt into the repo's _build, which is read-only in the container
        self.assertEqual(command, [sys.executable, str(REPO / 'tools' / 'build_scores_v3.py'), '--district', 'co-05',
                                   '--receipt', str(self.root / 'out' / 'scores_receipt.json')])

    def test_seed_demo_key_writes_a_32_byte_key_once_and_never_replaces_one(self):
        key = 'ab' * 32
        S.seed_demo_key(self.root, key)
        self.assertEqual((self.root / '.demo-key').read_bytes(), bytes.fromhex(key))
        S.seed_demo_key(self.root, 'cd' * 32)
        self.assertEqual((self.root / '.demo-key').read_bytes(), bytes.fromhex(key))                  # the first key stays
        for bad in ('ab' * 31, 'ab' * 33, ''):
            with self.assertRaises(SystemExit):
                S.seed_demo_key(self.root / 'elsewhere', bad)
        with self.assertRaises(ValueError):
            S.seed_demo_key(self.root / 'elsewhere', 'not hex')
        self.assertFalse((self.root / 'elsewhere').exists())

    def test_the_scores_step_seeds_the_key_before_its_tool_runs_and_only_when_asked_to(self):
        key = 'ab' * 32
        seen = []

        def fake_call(command, **kwargs):
            seen.append((Path(command[1]).name, (self.root / 'out' / '.demo-key').is_file()))
            for name in ('area_scores_shown.csv', 'area_scores_shown.manifest.json', 'scores.sqlite3', 'roads.sqlite3'):
                (self.root / 'out' / name).write_text('x', encoding='utf-8')
            return 0

        def run(step, **env):
            shutil.rmtree(self.root / 'out', ignore_errors=True)
            seen.clear()
            with mock.patch.dict(os.environ, {'TERRIER_LOCAL_DIR': str(self.root / 'out'), **env}), \
                    mock.patch.object(S.D, 'data_dir', return_value=self.root / 'out'), mock.patch.object(S.subprocess, 'call', fake_call), \
                    mock.patch.object(S, 'GlassBox'), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(S.run(step, 'co-05'), 0)
            return seen[0], (self.root / 'out' / '.demo-key')

        (tool, present_at_start), path = run('scores', TERRIER_DEMO_KEY=key)
        self.assertEqual((tool, present_at_start, path.read_bytes()), ('build_scores_v3.py', True, bytes.fromhex(key)))
        self.assertFalse(run('scores')[1].exists())                                           # no variable: the tool makes its own key
        self.assertFalse(run('scores', TERRIER_DEMO_KEY='')[1].exists())
        self.assertFalse(run('shown', TERRIER_DEMO_KEY=key)[1].exists())                      # only the step that has a demo key
        self.assertEqual([name for name, spec in S.STEPS.items() if spec.demo_key], ['scores'])

    def test_a_step_that_names_two_files_has_run_only_when_both_exist(self):
        code, _ = self.run_recorded('shown', written=('area_scores_shown.csv',))                 # the manifest is missing
        self.assertEqual(code, 1)
        code, _ = self.run_recorded('shown', written=('area_scores_shown.manifest.json',))      # the table is missing
        self.assertEqual(code, 1)
        self.assertEqual(self.run_recorded('scores', written=('scores.sqlite3',))[0], 0)


# ---- the parity harness ------------------------------------------------------------------------------------------------------

def make_store(path, rows, stamp='t0', extra_column=False, fts=None):
    """A tiny store shaped like roads.sqlite3: an ordinary table, an R-tree, and a metadata table that carries a stamp. `fts` adds what
    scores.sqlite3 has: demo_name and demo_search (FTS5, external content) over `rows`; 'rebuilt' lays the index out in one segment,
    'rows' builds it one row at a time (the same index logically, another physical layout)."""
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
        if fts:
            connection.executescript('CREATE TABLE demo_name (pos INTEGER PRIMARY KEY, name TEXT);'
                                     "CREATE VIRTUAL TABLE demo_search USING fts5(name, content='demo_name', content_rowid='pos');")
            connection.executemany('INSERT INTO demo_name VALUES (?, ?)', rows)
            if fts == 'rebuilt':
                connection.execute("INSERT INTO demo_search(demo_search) VALUES('rebuild')")
            else:
                for number, name in rows:
                    connection.execute('INSERT INTO demo_search(rowid, name) VALUES (?, ?)', (number, name))
                    connection.commit()


def make_walk_store(path, people, built='t0', qa=None):
    """A tiny store shaped like walk.sqlite3: a `meta` key/value table (built_utc and the qa document, which carries timings) and a
    WITHOUT ROWID person table."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);'
                                 'CREATE TABLE person (person_id TEXT PRIMARY KEY, age INTEGER) WITHOUT ROWID;'
                                 'CREATE TABLE stop (id INTEGER PRIMARY KEY, lon REAL);')
        connection.executemany('INSERT INTO person VALUES (?, ?)', [('p%d' % i, 20 + i) for i in range(people)])
        connection.executemany('INSERT INTO stop VALUES (?, ?)', [(i, 0.5 + i) for i in range(1, people + 1)])
        connection.executemany('INSERT INTO meta VALUES (?, ?)', [('schema', 'walk-v1'), ('built_utc', built), ('n_people', str(people)),
                                                                   ('qa', json.dumps(qa if qa is not None else {'rows': people}, sort_keys=True))])


def make_zip(path, members, stamp=(2026, 10, 1, 0, 0, 0)):
    """A zip of `members` {name: bytes, or any JSON value}, every member stamped with `stamp` (a zip is as stable as its stamps)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, payload if isinstance(payload, bytes) else json.dumps(payload, sort_keys=True).encode('utf-8'))


ROWS = [(1, 'Main St'), (2, 'Oak Ave'), (3, '')]
EXPLICIT ='ghcr.io/x/terrier-build@sha256:' + 'a' * 64        # what a caller names with --image or $TERRIER_BUILD_IMAGE
MANIFEST = 'ghcr.io/pjg93-222/terrier-build@sha256:' + 'b' * 64    # what the verified release manifest would name
SCORE_EXPLICIT = 'ghcr.io/x/terrier-build-score@sha256:' + 'c' * 64        # --score-image or $TERRIER_SCORE_IMAGE
SCORE_MANIFEST = 'ghcr.io/pjg93-222/terrier-build-score@sha256:' + 'd' * 64
SHOWN_MANIFEST = '{\n "areas": 1,\n "rows": 2\n}'              # the shown-areas manifest as the tool writes it: LF on Windows too


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

    # The metadata table of a scores store: stamps beside facts. Only what varies between the two Windows builds is left out.

    W1 = '{"matches":3,"rows_checked":3,"seconds":%s,"status":"verified"}'

    def scores_like(self, label, stamp, seconds, matches=3, facts='abc'):
        make_store(self.root / label / 'scores.sqlite3', ROWS, stamp=stamp)
        with closing(sqlite3.connect(self.root / label / 'scores.sqlite3')) as connection, connection:
            connection.execute("INSERT INTO metadata VALUES ('w1', ?)", (self.W1.replace('3,"rows', '%d,"rows' % matches) % seconds,))
            connection.execute("INSERT INTO metadata VALUES ('inputs', ?)", (facts,))
        return label

    def stores_like(self, a1=(), a2=(), b=()):
        for label in ('A1', 'A2', 'B'):
            shutil.rmtree(self.root / label, ignore_errors=True)
        defaults = {'A1': ('t1', 60.0), 'A2': ('t2', 61.5), 'B': ('t3', 232.0)}
        for label, extra in (('A1', a1), ('A2', a2), ('B', b)):
            kwargs = dict(zip(('stamp', 'seconds'), defaults[label]))
            kwargs.update(dict(extra))
            self.scores_like(label, **kwargs)
        return [self.root / label for label in ('A1', 'A2', 'B')]

    def test_metadata_leaves_out_the_stamps_and_the_timings_and_nothing_beside_them(self):
        ok, detail = P.compare('scores.sqlite3', *self.stores_like())
        self.assertTrue(ok, detail)
        self.assertIn('run-to-run variance (left out): metadata[built_utc, w1.seconds]', detail)      # named, never hidden

    def test_a_fact_in_metadata_that_differs_in_the_container_fails_even_though_the_table_is_noisy(self):
        ok, detail = P.compare('scores.sqlite3', *self.stores_like(b={'facts': 'changed'}))
        self.assertFalse(ok)
        self.assertIn('table(s): metadata[inputs]', detail)
        ok, detail = P.compare('scores.sqlite3', *self.stores_like(b={'matches': 2}))               # inside the JSON value that also holds a timing
        self.assertFalse(ok)
        self.assertIn('metadata[w1]', detail)

    def test_metadata_variance_cases(self):
        v = P.metadata_variance
        self.assertEqual(v({'a': 'x'}, {'a': 'x'}, {'a': 'x'}), ([], []))
        self.assertEqual(v({'a': 'x'}, {'a': 'x'}, {'a': 'y'}), ([], ['a']))                       # stable on Windows: B must match
        self.assertEqual(v({'a': 'x'}, {'a': 'y'}, {'a': 'z'}), (['a'], []))                       # a plain value that varies
        self.assertEqual(v({'a': 'x'}, {'a': 'y'}, {}), ([], ['a']))                               # ... but B must have the key
        self.assertEqual(v({'a': 'x'}, {'a': 'x'}, {}), ([], ['a']))
        self.assertEqual(v({'a': '{"n":1,"s":5}'}, {'a': '{"n":1,"s":6}'}, {'a': '{"n":1,"s":9}'}), (['a.s'], []))
        self.assertEqual(v({'a': '{"n":1,"s":5}'}, {'a': '{"n":1,"s":6}'}, {'a': '{"n":2,"s":9}'}), (['a.s'], ['a']))
        self.assertEqual(v({'a': '{"n":1,"s":5}'}, {'a': '{"n":1,"s":6}'}, {'a': '{"n":1}'}), (['a.s'], []))    # a field only on the varying side
        self.assertEqual(v({'a': '{"n":1,"s":5}'}, {'a': '{"n":1,"s":6}'}, {'a': 'text'}), (['a.s'], ['a']))
        self.assertEqual(v({'only': '1'}, {}, {}), ([], ['only']))

    def test_a_metadata_table_that_is_not_key_value_is_left_out_whole_as_before(self):
        folders = self.stores(a1={'stamp': 't1'}, a2={'stamp': 't2'})
        with closing(sqlite3.connect(folders[0] / 'roads.sqlite3')) as connection, connection:
            connection.executescript('DROP TABLE metadata; CREATE TABLE metadata (stamp TEXT); INSERT INTO metadata VALUES ("t1");')
        with closing(sqlite3.connect(folders[1] / 'roads.sqlite3')) as connection, connection:
            connection.executescript('DROP TABLE metadata; CREATE TABLE metadata (stamp TEXT); INSERT INTO metadata VALUES ("t2");')
        with closing(sqlite3.connect(folders[2] / 'roads.sqlite3')) as connection, connection:
            connection.executescript('DROP TABLE metadata; CREATE TABLE metadata (stamp TEXT); INSERT INTO metadata VALUES ("t3");')
        ok, detail = self.compare(*folders)
        self.assertTrue(ok, detail)
        self.assertTrue(detail.endswith('variance (left out): metadata'), detail)

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

    def put(self, name, a1, a2, b):
        for label, payload in (('A1', a1), ('A2', a2), ('B', b)):
            (self.root / label).mkdir(parents=True, exist_ok=True)
            (self.root / label / name).write_bytes(payload)
        return [self.root / label for label in ('A1', 'A2', 'B')]

    def test_no_file_gets_a_line_ending_allowance(self):
        for name in ('m.json', 'area.csv', 'b.zip'):
            ok, detail = P.compare(name, *self.put(name, b'a,b\r\n', b'a,b\r\n', b'a,b\n'))
            self.assertFalse(ok, name)
            self.assertIn('B differs', detail)
            ok, detail = P.compare(name, *self.put(name, b'a,b\n', b'a,b\n', b'a,b\n'))
            self.assertTrue(ok, detail)

    def test_flip_one_byte_changes_exactly_one_byte_and_a_file_comparison_sees_it(self):
        folders = self.put('area.csv', b'a,b\n1,2\n3,4\n', b'a,b\n1,2\n3,4\n', b'a,b\n1,2\n3,4\n')
        control = self.root / 'NC'
        control.mkdir()
        shutil.copyfile(folders[2] / 'area.csv', control / 'area.csv')
        before = (control / 'area.csv').read_bytes()
        at = P.flip_one_byte(control / 'area.csv')
        after = (control / 'area.csv').read_bytes()
        self.assertEqual([i for i in range(len(before)) if before[i] != after[i]], [at])
        self.assertFalse(P.compare_file('area.csv', folders[0], folders[1], control)[0])

    def real_store(self, label):
        path = self.root / label / 'real.sqlite3'
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.executescript('CREATE TABLE score (id INTEGER PRIMARY KEY, x REAL, n INTEGER, name TEXT);'
                                     'CREATE TABLE keyed (k TEXT PRIMARY KEY, v REAL) WITHOUT ROWID;'
                                     'CREATE VIRTUAL TABLE box USING rtree(id, west, east, south, north);'
                                     'CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);'
                                     "INSERT INTO score VALUES (1, 0.5, 7, 'a'), (2, 1.25, 8, 'b'); INSERT INTO keyed VALUES ('k', 2.5);"
                                     'INSERT INTO box VALUES (1, 0, 1, 0, 1); '
                                     "INSERT INTO metadata VALUES ('built_utc', 't')")
        return path

    def test_step_value_is_the_smallest_step_of_each_kind(self):
        stepped, what = P.step_value(1.25)
        self.assertEqual((stepped - 1.25, what), (math.ulp(1.25), 'REAL by one ULP'))
        self.assertEqual(P.step_value(float('inf'))[0], math.nextafter(float('inf'), -math.inf))        # never "no change"
        self.assertEqual(P.step_value(8), (9, 'INTEGER by 1'))
        self.assertEqual(P.step_value(9)[0], 8)
        self.assertEqual(P.step_value('b')[0], 'c')
        self.assertEqual(P.step_value('')[0], 'x')
        self.assertEqual(P.step_value(b'\x00\x02')[0], b'\x00\x03')
        self.assertEqual(P.step_value(b'')[0], b'\x00')

    def test_perturb_one_value_steps_one_real_cell_by_one_ulp_and_the_comparison_sees_it(self):
        paths = [self.real_store(label) for label in ('A1', 'A2', 'B')]
        control = self.root / 'NC'
        control.mkdir()
        shutil.copyfile(paths[2], control / 'real.sqlite3')
        before = P.B.store_digest(control / 'real.sqlite3')
        table, column, what = P.perturb_one_value(control / 'real.sqlite3')
        self.assertEqual((table, column, what), ('score', 'x', 'REAL by one ULP'))
        with closing(sqlite3.connect(control / 'real.sqlite3')) as connection:
            self.assertEqual(connection.execute('SELECT x FROM score ORDER BY id').fetchall(), [(0.5,), (1.25 + math.ulp(1.25),)])
            self.assertEqual(connection.execute('SELECT n, name FROM score ORDER BY id').fetchall(), [(7, 'a'), (8, 'b')])
        after = P.B.store_digest(control / 'real.sqlite3')
        self.assertEqual([t for t in before['tables'] if before['tables'][t] != after['tables'][t]], ['score'])
        ok, detail = P.compare_store('real.sqlite3', paths[0].parent, paths[1].parent, control)
        self.assertFalse(ok)
        self.assertIn('table(s): score', detail)

    def test_perturb_one_value_skips_the_variance_virtual_and_without_rowid_tables_and_falls_back_to_other_kinds(self):
        path = self.real_store('A1')
        self.assertEqual(P.perturb_one_value(path, skip={'score'})[0], 'metadata')          # keyed is WITHOUT ROWID, box is virtual
        with self.assertRaises(ValueError):
            P.perturb_one_value(path, skip={'score', 'metadata', 'keyed', 'box'})

    def test_a_control_is_detected_only_when_the_changed_table_is_what_fails(self):
        failure = 'B differs from the Windows build in table(s): demo_search_data, metadata[inputs, w1], person'
        self.assertEqual(P.differing_tables(failure), ['demo_search_data', 'metadata', 'person'])
        self.assertEqual(P.differing_tables('missing in B'), [])
        self.assertEqual(P.differing_tables('PRAGMA integrity_check is not ok in B'), [])
        self.assertTrue(P.control_detected(False, failure, 'person'))
        self.assertFalse(P.control_detected(False, failure, 'household'))                    # it failed, but for other tables
        self.assertFalse(P.control_detected(False, failure, 'person', already=['person']))   # that table already differed
        self.assertFalse(P.control_detected(False, 'missing in B', 'person'))
        self.assertFalse(P.control_detected(True, '3 tables, 9 rows; variance (left out): none', 'person'))

    def test_a_store_that_already_fails_elsewhere_does_not_make_the_controls_look_caught(self):
        folders = self.stores()
        with closing(sqlite3.connect(folders[2] / 'roads.sqlite3')) as connection, connection:
            connection.execute("INSERT INTO metadata VALUES ('extra', 'x')")                # B differs from A in a table of its own ...
        ok, detail = P.compare_store('roads.sqlite3', *folders)
        self.assertFalse(ok)
        before = P.differing_tables(detail)
        self.assertEqual(before, ['metadata'])
        control = self.root / 'NC'
        control.mkdir()
        shutil.copyfile(folders[2] / 'roads.sqlite3', control / 'roads.sqlite3')
        table = P.remove_one_row(control / 'roads.sqlite3', {'metadata'})                   # ... and the control touches another one
        self.assertEqual(table, 'roads')
        self.assertTrue(P.control_detected(*P.compare_store('roads.sqlite3', *folders[:2], control), table, before))
        # a control that touches the table that already differs proves nothing and is reported as not detected
        self.assertFalse(P.control_detected(False, detail, 'metadata', before))

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


class FtsParityTests(unittest.TestCase):
    """D-177, ruling of 2026-10-13: the shadow tables of an FTS5 table are physical index structures. Equal by content (rebuilt by one
    library, and the same MATCH answers), not by layout; every other table, and a table that only looks like a shadow, stays exact."""

    NAMES = list(enumerate(['Ann Lee', 'Bob Stone and Sons', 'Cy Not Near', 'Dee Oak', 'Eve Stone', 'Fay Lee Oak', 'Gus Moore',
                            'Hal Moore Jr', 'Ida Lee', ''], 1))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def stores(self, a1='rebuilt', a2='rebuilt', b='rebuilt', names=NAMES, b_names=None, name='scores.sqlite3'):
        for label, layout, rows in (('A1', a1, names), ('A2', a2, names), ('B', b, b_names or names)):
            shutil.rmtree(self.root / label, ignore_errors=True)
            make_store(self.root / label / name, rows, fts=layout)
        return [self.root / label for label in ('A1', 'A2', 'B')]

    @staticmethod
    def raw(folder, tables=('demo_search_data', 'demo_search_idx')):
        digests = P.B.store_digest(folder / 'scores.sqlite3')['tables']
        return [digests[t] for t in tables]

    def test_fts5_tables_are_found_by_their_schema_with_their_shadow_tables_only(self):
        folders = self.stores()
        self.assertEqual(P.fts5_tables(folders[0] / 'scores.sqlite3'), {
            'demo_search': ['demo_search_data', 'demo_search_idx', 'demo_search_docsize', 'demo_search_config']})        # external content: no _content
        own = self.root / 'own.sqlite3'
        with closing(sqlite3.connect(own)) as connection, connection:
            connection.executescript('CREATE VIRTUAL TABLE find USING fts5(name); CREATE TABLE find_data_extra (x); CREATE TABLE plain_data (x);'
                                     'CREATE VIRTUAL TABLE box USING rtree(id, a, b, c, d);')
        self.assertEqual(P.fts5_tables(own), {'find': ['find_data', 'find_idx', 'find_docsize', 'find_config', 'find_content']})

    def test_shadow_tables_that_differ_only_in_layout_pass_and_the_line_says_how(self):
        folders = self.stores(b='rows')
        self.assertNotEqual(self.raw(folders[0]), self.raw(folders[2]))              # the premise: physically different
        self.assertEqual(self.raw(folders[0]), self.raw(folders[1]))
        ok, detail = P.compare('scores.sqlite3', *folders)
        self.assertTrue(ok, detail)
        self.assertIn('FTS5 demo_search: shadow tables equal after rebuild (SQLite %s), 16 of 16 MATCH queries identical' % sqlite3.sqlite_version,
                      detail)                                                         # the 16 words of more than two letters
        self.assertIn('variance (left out): none', detail)

    def test_exactly_equal_shadow_tables_pass_without_a_rebuild(self):
        with mock.patch.object(P, 'fts_parity', side_effect=AssertionError('no rebuild needed')):
            ok, detail = P.compare('scores.sqlite3', *self.stores())
        self.assertTrue(ok, detail)
        self.assertIn('FTS5 demo_search: shadow tables exactly equal', detail)

    def test_a_second_windows_build_with_another_layout_is_rebuilt_too(self):
        folders = self.stores(a2='rows', b='rows')                       # A2 and B agree with each other, not with A1: all three are rebuilt
        seen = []
        real = P.rebuilt_shadows
        with mock.patch.object(P, 'rebuilt_shadows', lambda label, *args: seen.append(label) or real(label, *args)):
            ok, detail = P.compare('scores.sqlite3', *folders)
        self.assertTrue(ok, detail)
        self.assertEqual(seen, ['A1', 'B', 'A2'])
        seen.clear()
        with mock.patch.object(P, 'rebuilt_shadows', lambda label, *args: seen.append(label) or real(label, *args)):
            self.assertTrue(P.compare('scores.sqlite3', *self.stores(b='rows'))[0])
        self.assertEqual(seen, ['A1', 'B'])                              # A2 equals A1 exactly: nothing more to learn from it

    def test_a_stale_index_fails_through_the_match_queries_although_its_rebuilt_form_is_equal(self):
        folders = self.stores(b='rebuilt')
        with closing(sqlite3.connect(folders[2] / 'scores.sqlite3')) as connection, connection:        # the text of row 3 is unchanged ...
            connection.execute("INSERT INTO demo_search(demo_search, rowid, name) VALUES('delete', 3, 'Cy Not Near')")
            connection.execute("INSERT INTO demo_search(rowid, name) VALUES (3, 'Wholly Different')")       # ... the index says otherwise
        ok, detail = P.compare('scores.sqlite3', *folders)
        self.assertFalse(ok)
        self.assertIn('(i) rebuilt shadow tables equal', detail)                       # a rebuild repairs it ...
        self.assertRegex(detail, r'\(ii\) \d+ of 16 MATCH queries identical')                # ... the queries on the store as built do not
        self.assertNotRegex(detail, r'\(ii\) 16 of 16')
        self.assertIn('table(s): demo_search_data, demo_search_idx, demo_search_docsize, demo_search_config', detail)

    def test_negative_control_one_changed_indexed_name_fails_the_fts_comparison_itself(self):
        folders = self.stores(b='rows')
        control = self.root / 'NC' / 'scores.sqlite3'
        control.parent.mkdir()
        shutil.copyfile(folders[2] / 'scores.sqlite3', control)
        before = P.B.store_digest(control)['tables']
        self.assertEqual(P.change_one_indexed_name(control), ('demo_search', 'demo_name', 'name'))
        after = P.B.store_digest(control)['tables']
        self.assertEqual([t for t in before if before[t] != after[t] and not t.startswith('demo_search')], ['demo_name'])      # one text changed
        with closing(sqlite3.connect(control)) as connection:
            connection.execute("INSERT INTO demo_search(demo_search, rank) VALUES('integrity-check', 1)")       # the index is in line with it
            self.assertEqual(connection.execute("SELECT count(*) FROM demo_search WHERE demo_search MATCH 'zzqx'").fetchone(), (1,))
        ok, detail = P.fts_parity({'A1': folders[0] / 'scores.sqlite3', 'B': control}, 'demo_search', P.fts5_tables(control)['demo_search'])
        self.assertFalse(ok)
        self.assertIn('(i) rebuilt shadow tables differ', detail)
        self.assertIn('table(s): demo_search_data', detail)
        self.assertTrue(P.control_detected(*P.compare_store('scores.sqlite3', *folders[:2], control.parent), 'demo_name'))          # and the whole store fails too
        # the same without a rebuild to blame: a table that holds its own text
        own = [self.root / ('own%d.sqlite3' % i) for i in (1, 2)]
        for path in own:
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.executescript('CREATE VIRTUAL TABLE demo_search USING fts5(name);')
                connection.executemany('INSERT INTO demo_search(rowid, name) VALUES (?, ?)', self.NAMES)
        self.assertEqual(P.change_one_indexed_name(own[1]), ('demo_search', 'demo_search', 'name'))
        ok, detail = P.fts_parity({'A1': own[0], 'B': own[1]}, 'demo_search', P.fts5_tables(own[0])['demo_search'])
        self.assertFalse(ok)
        self.assertIn('demo_search_content', detail)
        make_store(self.root / 'plain.sqlite3', ROWS)
        with self.assertRaises(ValueError):
            P.change_one_indexed_name(self.root / 'plain.sqlite3')

    def add_contentless(self, folders, layouts):
        """A contentless FTS5 table (content='', as contacts.sqlite3 has) beside demo_search: one batch then 'optimize', or one row at a time."""
        for folder, layout in zip(folders, layouts):
            with closing(sqlite3.connect(folder / 'scores.sqlite3')) as connection:
                connection.execute("CREATE VIRTUAL TABLE search USING fts5(name, content='')")
                if layout == 'batch':
                    connection.executemany('INSERT INTO search(rowid, name) VALUES (?, ?)', self.NAMES)
                    connection.execute("INSERT INTO search(search) VALUES('optimize')")
                else:
                    for row in self.NAMES:
                        connection.execute('INSERT INTO search(rowid, name) VALUES (?, ?)', row)
                        connection.commit()
                connection.commit()

    def test_a_contentless_fts5_table_cannot_be_rebuilt_so_it_is_exact_or_it_fails(self):
        folders = self.stores()
        self.add_contentless(folders, ['batch'] * 3)
        path = folders[2] / 'scores.sqlite3'
        self.assertEqual(sorted(P.fts5_tables(path)), ['demo_search', 'search'])
        self.assertEqual(sorted(P.fts5_tables(path, rebuildable=True)), ['demo_search'])
        ok, detail = P.compare('scores.sqlite3', *folders)
        self.assertTrue(ok, detail)
        self.assertIn('FTS5 search: shadow tables exactly equal', detail)
        folders = self.stores()
        self.add_contentless(folders, ['batch', 'batch', 'rows'])
        self.assertNotEqual(self.raw(folders[0], ('search_data', 'search_idx')), self.raw(folders[2], ('search_data', 'search_idx')))
        ok, detail = P.compare('scores.sqlite3', *folders)
        self.assertFalse(ok)
        self.assertIn('FTS5 search is contentless', detail)
        self.assertIn('table(s): search_data, search_idx, search_docsize, search_config', detail)
        self.assertNotIn('after rebuild', detail)                           # nothing was rebuilt, and nothing was guessed

    def test_the_negative_control_takes_the_table_that_has_content_and_needs_one(self):
        folders = self.stores()
        self.add_contentless(folders, ['batch'] * 3)
        self.assertEqual(P.change_one_indexed_name(folders[2] / 'scores.sqlite3')[0], 'demo_search')
        only = self.root / 'only.sqlite3'
        with closing(sqlite3.connect(only)) as connection, connection:
            connection.execute("CREATE VIRTUAL TABLE search USING fts5(name, content='')")
        with self.assertRaises(ValueError):
            P.change_one_indexed_name(only)

    def test_a_table_that_only_looks_like_a_shadow_is_still_compared_exactly(self):
        folders = self.stores()
        for label, value in (('A1', 1), ('A2', 1), ('B', 2)):
            with closing(sqlite3.connect(self.root / label / 'scores.sqlite3')) as connection, connection:
                connection.executescript('CREATE TABLE thing_data (x INTEGER); INSERT INTO thing_data VALUES (%d);' % value)
        ok, detail = P.compare('scores.sqlite3', *folders)
        self.assertFalse(ok)
        self.assertIn('table(s): thing_data', detail)

    def test_every_other_table_stays_exact_beside_an_index_that_differs_in_layout(self):
        ok, detail = P.compare('scores.sqlite3', *self.stores(b='rows', b_names=self.NAMES[:-1] + [(10, 'Ida Lee')]))
        self.assertFalse(ok)                                             # demo_name differs in B: failed before any rebuild
        self.assertIn('table(s): demo_name', detail)
        self.assertNotIn('FTS5', detail)

    def test_the_query_words_are_the_same_every_time_bounded_and_safe_for_fts5_operators(self):
        path = self.stores()[0] / 'scores.sqlite3'
        words = P.query_words(path, 'demo_search')
        self.assertEqual(words, P.query_words(path, 'demo_search'))
        self.assertEqual(sorted(words), sorted({'ann', 'lee', 'bob', 'stone', 'and', 'sons', 'not', 'near', 'dee', 'oak', 'eve', 'fay', 'gus',
                                                 'moore', 'hal', 'ida'}))
        self.assertNotEqual(words, sorted(words))                        # shuffled, with a fixed seed
        answers = P.match_answers(path, 'demo_search', words)            # 'and', 'not', 'near' are quoted: words, not operators
        self.assertEqual(len(answers), len(words))
        self.assertEqual(answers[words.index('oak')][0], 2)
        many = [(i, 'w%04d x' % i) for i in range(1, 601)]
        make_store(self.root / 'many.sqlite3', many, fts='rebuilt')
        self.assertEqual(len(P.query_words(self.root / 'many.sqlite3', 'demo_search')), P.MATCH_QUERIES)


class RunTests(unittest.TestCase):
    """main() with the two builds replaced by functions that write tiny stores (and, for `shown`, a tiny table and manifest)."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.calls = []
        self.images = []            # the {'geo': ref, 'score': ref} of each container build
        self.pythons = []           # the {'geo': path, 'score': path} of each Windows build
        self.keys = []              # the $TERRIER_DEMO_KEY each build (Windows or container) was started with
        self.seeded = []            # (names, source) of each stage seeding
        self.salts = []             # the $TERRIER_WALK_SALT each build was started with
        self.after_container = None  # a function of B's folder, called once the fake container build has written it

    @staticmethod
    def write_step(out, step, stamp, rows, fts=None):
        out.mkdir(parents=True, exist_ok=True)
        if step == 'shown':
            (out / 'area_scores_shown.csv').write_bytes(b'area,score\n1,2\n')
            (out / 'area_scores_shown.manifest.json').write_bytes(SHOWN_MANIFEST.encode())
        elif step == 'walk':      # the walk store: a meta table that carries the stamps, and the peak memory only Windows can measure
            make_walk_store(out / 'walk.sqlite3', 5, built=stamp, qa={'rows': 5, 'timing': {'total': stamp},
                                                                       'peak_memory_mb': None if stamp == 'c' else 1234})
        elif step == 'ingest':    # ingest and extend stamp created_at in manifest.json: the zips differ between the two Windows builds
            make_zip(out / 'ingest' / 'k1.zip', {'manifest.json': {'created_at': stamp, 'snapshot': 'k1'}, 'rows.json': [1, 2]})
            (out / 'ingest' / 'ingest_receipt.json').write_bytes(b'{"rows": 2}')
        elif step == 'extend':
            make_zip(out / 'extend' / 'k2.zip', {'manifest.json': {'created_at': stamp, 'snapshot': 'k2'}, 'rows.json': [1, 2, 3]})
        elif step == 'bundle':    # the bundle's writer stamps every member alike: the same bytes every time
            make_zip(out / 'bundle-out' / 'k3.zip', {'manifest.json': {'snapshot': 'k3'}, 'rows.json': [1, 2, 3], 'geometry.json': {'a': [1] * 40}})
            (out / 'bundle-out' / 'k3.zip.sha256').write_bytes(b'abc *k3.zip\n')
            (out / 'bundle_receipt.json').write_bytes(('{"seconds": "%s"}' % stamp).encode())
        elif step == 'density':
            (out / 'density.json').write_bytes(b'{"levels": {"block": [1, 2]}}')
        else:
            make_store(out / S.STEPS[step].store, rows, stamp=stamp, fts=fts if step == 'scores' else None)

    def fakes(self, b_rows=ROWS):
        def windows(pythons, repo, district, steps, out):
            self.calls.append(('windows', out.name))
            self.pythons.append(pythons)
            self.keys.append(os.environ.get('TERRIER_DEMO_KEY'))
            self.salts.append(os.environ.get('TERRIER_WALK_SALT'))
            for step in steps:
                self.write_step(out, step, 'w%d' % len(self.calls), ROWS, fts='rebuilt')

        def container(images, repo, district, steps, out, run_id, names, export_bind=False):
            self.calls.append(('container', out.name, tuple(names), export_bind))
            self.images.append(images)
            self.keys.append(os.environ.get('TERRIER_DEMO_KEY'))
            self.salts.append(os.environ.get('TERRIER_WALK_SALT'))
            for step in steps:
                self.write_step(out, step, 'c', b_rows, fts='rows')       # the scores store's FTS5 index is laid out differently in B
            if self.after_container:
                self.after_container(out)

        return [mock.patch.object(P, 'windows_build', windows), mock.patch.object(P, 'container_build', container),
                mock.patch.object(P, 'windows_fingerprint', lambda pythons, repo, images: {i: {'python': 'win'} for i in images}),
                mock.patch.object(P, 'container_fingerprint', lambda images, repo, out, run_id: {i: {'python': 'box'} for i in images}),
                mock.patch.object(P, 'seed_stage', lambda images, repo, out, run_id, district, source, names: self.seeded.append((tuple(names), str(source))))]

    def main(self, *args, b_rows=ROWS, scratch=None, image=EXPLICIT, score_image=SCORE_EXPLICIT, environ=None):
        """`image`, `score_image`: the --image / --score-image flags (None: no flag). `environ`: the process environment's
        TERRIER_BUILD_IMAGE / TERRIER_SCORE_IMAGE (both unset by default)."""
        steps = args[args.index('--steps') + 1].split(',') if '--steps' in args else ['roads']
        needed = S.inputs_needed(steps, S.D.get('co-05'))
        if needed and '--inputs-from' not in args:               # steps that read stores no step of the run makes: a folder that holds them
            (self.root / 'inputs').mkdir(exist_ok=True)
            for name in needed:
                (self.root / 'inputs' / name).write_bytes(b'x')
            args = (*args, '--inputs-from', str(self.root / 'inputs'))
        argv = ['--scratch', str(scratch or self.root / 'scratch'), *(['--image', image] if image else []),
                *(['--score-image', score_image] if score_image else []), *args]
        out = io.StringIO()
        with contextlib.ExitStack() as stack:
            for patch in self.fakes(b_rows):
                stack.enter_context(patch)
            env = {k: v for k, v in os.environ.items() if k not in ('TERRIER_BUILD_IMAGE', 'TERRIER_SCORE_IMAGE')}
            stack.enter_context(mock.patch.dict(os.environ, {**env, **(environ or {})}, clear=True))
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
        self.assertEqual(report['fingerprint_container'], {'geo': {'python': 'box'}})
        self.assertEqual(report['fingerprint_windows'], {'geo': {'python': 'win'}})

    # The full chain: each step in its own toolchain, the outputs of `shown` among the compared files, the controls on every artifact.

    CHAIN = 'roads,street,contacts,shown,scores'

    def test_the_full_chain_compares_every_output_and_controls_each_one(self):
        code, text = self.main('--steps', self.CHAIN, '--negative-control')
        self.assertEqual(code, 0, text)
        self.assertEqual(self.calls[2][2], ('roads.sqlite3', 'street.sqlite3', 'contacts.sqlite3', 'area_scores_shown.csv',
                                            'area_scores_shown.manifest.json', 'scores.sqlite3'))
        for name in self.calls[2][2]:
            self.assertRegex(text, r'PASS  %s ' % re.escape(name))
            self.assertRegex(text, r'PASS  negative control %s .*detected' % re.escape(name))
        self.assertRegex(text, r'PASS  area_scores_shown\.csv +byte-identical')
        self.assertRegex(text, r'PASS  area_scores_shown\.manifest\.json +byte-identical')
        self.assertRegex(text, r'negative control area_scores_shown\.csv +one byte changed at offset \d+: detected')
        self.assertRegex(text, r'negative control scores\.sqlite3 +one row removed from \w+: detected')
        self.assertRegex(text, r'PASS  negative control scores\.sqlite3 \(value\) +one \w+\.\w+ value changed \(.+\): detected')
        self.assertRegex(text, r'PASS  scores\.sqlite3 .*FTS5 demo_search: shadow tables equal after rebuild')           # B lays the index out differently
        self.assertRegex(text, r'PASS  negative control scores\.sqlite3 \(indexed name\) +one demo_name\.name indexed value changed, index kept in line: detected')
        self.assertNotRegex(text, r'negative control (roads|street|contacts)\.sqlite3 \(indexed name\)')               # only a store with an FTS5 table
        report = json.loads((self.root / 'scratch' / 'parity_report.json').read_text(encoding='utf-8'))
        self.assertEqual(sorted(report['fingerprint_container']), ['geo', 'score'])
        self.assertEqual(sorted(report['fingerprint_windows']), ['geo', 'score'])

    def test_reuse_container_judges_the_finished_builds_again_without_building_anything(self):
        self.main('--steps', 'scores')
        self.calls.clear()
        code, text = self.main('--steps', 'scores', '--reuse-windows', '--reuse-container', '--negative-control',
                               image=None, score_image=SCORE_EXPLICIT)
        self.assertEqual((code, self.calls), (0, []), text)                       # no Windows build, no container build, no manifest asked for
        self.assertIn('B: reusing', text)
        self.assertRegex(text, r'PASS  negative control scores\.sqlite3 \(indexed name\)')
        report = json.loads((self.root / 'scratch' / 'parity_report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['images'], {'score': SCORE_EXPLICIT})
        self.assertEqual(report['fingerprint_container'], {'score': {'python': 'box'}})      # an image that is given is asked for its versions
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):       # B must be there, and A1 and A2 too
            self.main('--steps', 'scores', '--reuse-container', scratch=self.root / 'scratch')
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.main('--steps', 'scores', '--reuse-windows', '--reuse-container', scratch=self.root / 'none')

    def test_a_container_table_or_store_that_differs_fails_the_chain(self):
        code, text = self.main('--steps', 'shown,scores', b_rows=ROWS[:2])
        self.assertEqual(code, 1)
        self.assertRegex(text, r'PASS  area_scores_shown\.csv ')                 # the fake writes the same table in A and B ...
        self.assertRegex(text, r'FAIL  scores\.sqlite3 .*table\(s\): .*roads')     # ... and a different row set in B's store
        self.assertIn('PARITY FAIL', text)

    def test_the_later_steps_are_judged_from_one_set_of_inputs_copied_into_a1_a2_and_the_stage(self):
        code, text = self.main('--steps', 'walk,ingest,extend,bundle,density', '--negative-control')
        self.assertEqual(code, 0, text)
        needed = ['roads.sqlite3', 'street.sqlite3', 'contacts.sqlite3', 'scores.sqlite3', 'area_scores_shown.csv']
        self.assertEqual(self.seeded, [(tuple(needed), str(self.root / 'inputs'))])                    # B's stage ...
        for label in ('A1', 'A2'):                                                                     # ... and both Windows folders
            for name in needed:
                self.assertTrue((self.root / 'scratch' / label / name).is_file(), (label, name))
        # a `*` name is what A1 made; the receipt (bundle_receipt.json) is exported by compute_build but never compared
        self.assertEqual(self.calls[2][2], ('walk.sqlite3', 'ingest/k1.zip', 'ingest/ingest_receipt.json', 'extend/k2.zip',
                                            'bundle-out/k3.zip', 'bundle-out/k3.zip.sha256', 'density.json'))
        self.assertNotIn('bundle_receipt.json', text)
        # the walk store: its stamps and its timings vary, the peak memory only Windows measures is always left out, the facts must match
        self.assertRegex(text, r'PASS  walk\.sqlite3 .*variance \(left out\): meta\[built_utc, qa\.peak_memory_mb, qa\.timing\]')
        self.assertRegex(text, r'PASS  ingest/k1\.zip +1 members identical, 1 with run-to-run variance \(left out\): manifest\.json\[created_at\]')
        self.assertRegex(text, r'PASS  extend/k2\.zip +1 members identical, 1 with run-to-run variance')
        self.assertRegex(text, r'PASS  bundle-out/k3\.zip +byte-identical')
        self.assertRegex(text, r'PASS  density\.json +byte-identical')
        for name in ('ingest/k1.zip', 'extend/k2.zip', 'bundle-out/k3.zip'):
            self.assertRegex(text, r'PASS  negative control %s +one bit of (rows|geometry)\.json changed: detected' % re.escape(name))
        self.assertRegex(text, r'PASS  negative control walk\.sqlite3 +one row removed from \w+: detected')
        self.assertRegex(text, r'PASS  negative control walk\.sqlite3 \(value\) +one \w+\.\w+ value changed \(.+\): detected')
        self.assertRegex(text, r'negative control density\.json +one byte changed at offset \d+: detected')
        self.assertIn('PARITY PASS', text)
        report = json.loads((self.root / 'scratch' / 'parity_report.json').read_text(encoding='utf-8'))
        self.assertEqual((report['inputs'], report['inputs_from']), (needed, str(self.root / 'inputs')))
        self.assertTrue(re.fullmatch(r'[0-9a-f]{64}', (self.root / 'scratch' / 'walk_salt.hex').read_text(encoding='utf-8')))

    def test_a_file_that_already_differs_in_b_gets_no_negative_control_because_it_would_look_caught_anyway(self):
        self.after_container = lambda out: (out / 'density.json').write_bytes(b'{"levels": {"block": [1, 3]}}')
        code, text = self.main('--steps', 'ingest,density', '--negative-control')
        self.assertEqual(code, 1, text)
        self.assertRegex(text, r'FAIL  density\.json +B differs from the Windows build byte for byte')
        self.assertIn('negative control density.json: not run, the comparison itself already fails', text)
        self.assertNotRegex(text, r'PASS  negative control density\.json')
        self.assertRegex(text, r'PASS  negative control ingest/k1\.zip +one bit of rows\.json changed: detected')      # the others still get theirs

    def test_one_walk_salt_serves_a1_a2_and_b_like_the_demo_key(self):
        self.main('--steps', 'walk', '--skip-container')
        salt = (self.root / 'scratch' / 'walk_salt.hex').read_text(encoding='utf-8')
        self.assertRegex(salt, r'^[0-9a-f]{64}$')
        self.assertEqual(self.salts, [salt, salt])
        self.main('--steps', 'walk', '--reuse-windows')
        self.assertEqual(self.salts[2:], [salt])                                  # kept in the scratch folder for a later invocation
        self.salts.clear()
        self.main('--steps', 'roads', scratch=self.root / 'other')
        self.assertEqual(self.salts, [None, None, None])
        self.assertFalse((self.root / 'other' / 'walk_salt.hex').exists())      # only a run with a step that has a salt makes one

    def test_link_inputs_is_refused_where_a_step_writes_what_it_reads_and_without_inputs(self):
        (self.root / 'inputs').mkdir()
        for argv in (['--steps', 'contacts', '--inputs-from', str(self.root / 'inputs'), '--link-inputs'],     # contacts changes street.sqlite3 in place
                     ['--steps', 'roads', '--link-inputs']):                                                      # nothing to link
            with self.assertRaises(SystemExit) as stop, contextlib.redirect_stderr(io.StringIO()) as err:
                P.main(['--scratch', str(self.root / 'scratch'), '--image', EXPLICIT, *argv])
            self.assertEqual(stop.exception.code, 2)
            self.assertIn('--link-inputs', err.getvalue())
        self.assertEqual(self.calls, [])

    def test_steps_that_read_stores_no_step_of_the_run_makes_need_a_folder_to_take_them_from(self):
        for argv, message in ((['--steps', 'walk'], '--inputs-from'), (['--steps', 'roads', '--inputs-from', '.'], 'read nothing')):
            with self.assertRaises(SystemExit) as stop, contextlib.redirect_stderr(io.StringIO()) as err:
                P.main(['--scratch', str(self.root / 'scratch'), '--image', EXPLICIT, *argv])
            self.assertEqual(stop.exception.code, 2)
            self.assertIn(message, err.getvalue())
        self.assertEqual(self.calls, [])

    # One throwaway demo key for A1, A2 and B.

    def test_one_demo_key_serves_every_build_and_is_kept_for_a_later_reuse_run(self):
        self.main('--steps', 'scores', '--skip-container')                     # the Windows builds first (A1, A2) ...
        self.assertEqual(len(self.keys), 2)
        key = self.keys[0]
        self.assertRegex(key, r'^[0-9a-f]{64}$')
        self.assertEqual(set(self.keys), {key})
        self.assertEqual((self.root / 'scratch' / 'demo_key.hex').read_text(encoding='utf-8'), key)
        self.keys.clear()
        self.main('--steps', 'scores', '--reuse-windows')                      # ... then the container, in a second invocation
        self.assertEqual(self.keys, [key])
        self.keys.clear()
        self.main('--steps', 'scores', scratch=self.root / 'other')
        self.assertNotEqual(self.keys[0], key)                                 # a new run, a new key

    def test_a_key_given_in_the_environment_is_used_and_no_key_file_is_made(self):
        given = 'ab' * 32
        self.main('--steps', 'scores', environ={'TERRIER_DEMO_KEY': given})
        self.assertEqual(set(self.keys), {given})
        self.assertFalse((self.root / 'scratch' / 'demo_key.hex').exists())

    # Image and interpreter per step.

    def test_each_step_runs_in_its_toolchains_image_and_interpreter(self):
        code, text = self.main('--steps', self.CHAIN, '--python', 'C:/work/python.exe', '--score-python', 'C:/py312/python.exe')
        self.assertEqual(code, 0, text)
        self.assertEqual(self.images, [{'geo': EXPLICIT, 'score': SCORE_EXPLICIT}])
        self.assertEqual(self.pythons, [{'geo': 'C:/work/python.exe', 'score': 'C:/py312/python.exe'}] * 2)       # A1, A2
        report = json.loads((self.root / 'scratch' / 'parity_report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['images'], {'geo': EXPLICIT, 'score': SCORE_EXPLICIT})
        self.assertEqual(report['image_sources'], {'geo': 'explicit', 'score': 'explicit'})
        self.assertEqual(report['python'], {'geo': 'C:/work/python.exe', 'score': 'C:/py312/python.exe'})

    def test_the_windows_score_interpreter_defaults_to_the_one_terriers_own_scripts_use(self):
        self.main('--steps', 'shown,scores')
        self.assertEqual(self.pythons[0]['score'], str(P.H.PY312))
        self.assertTrue(str(P.H.PY312).endswith(r'Python312\python.exe') or os.environ.get('TERRIER_PY312'))
        self.assertEqual(self.pythons[0]['geo'], os.environ.get('CU_PYTHON') or str(P.H.WORK_PY))

    def test_only_the_images_the_steps_need_are_asked_for(self):
        for steps, wanted in (('roads,street', {'geo'}), ('shown,scores', {'score'}), ('street,scores', {'geo', 'score'})):
            self.images.clear()
            self.main('--steps', steps, scratch=self.root / steps)
            self.assertEqual(set(self.images[0]), wanted, steps)

    def test_a_score_step_with_no_score_image_and_no_verified_manifest_stops_before_any_build(self):
        refusal = subprocess.CompletedProcess([], 1, stdout='', stderr='current: NO_CURRENT: no release has been published')
        with mock.patch.object(P.subprocess, 'run', return_value=refusal) as run, self.assertRaises(SystemExit) as stop:
            self.main('--steps', 'street,scores', score_image=None)           # the geo image is given; the score image is not
        self.assertIn('no verified image for terrier-build-score: not running', str(stop.exception))
        self.assertEqual(self.calls, [])
        self.assertEqual(run.call_count, 1)                                    # only current.py was asked, and only for the score image

    def test_the_score_image_comes_from_the_flag_then_the_variable_then_the_manifest_by_its_own_name(self):
        asked, patch = self.manifest(answer={'terrier-build': MANIFEST, 'terrier-build-score': SCORE_MANIFEST})
        with patch:
            self.assertEqual(self.main('--steps', 'street,scores', image=None, score_image=None, scratch=self.root / 'm')[0], 0)
            self.assertEqual(asked, ['terrier-build', 'terrier-build-score'])
            self.assertEqual(self.images[-1], {'geo': MANIFEST, 'score': SCORE_MANIFEST})
            asked.clear()
            self.assertEqual(self.main('--steps', 'street,scores', image=None, score_image=None, scratch=self.root / 'v',
                                       environ={'TERRIER_SCORE_IMAGE': SCORE_EXPLICIT})[0], 0)
            self.assertEqual(asked, ['terrier-build'])                                         # the variable beat the manifest
            self.assertEqual(self.images[-1], {'geo': MANIFEST, 'score': SCORE_EXPLICIT})
            asked.clear()
            self.assertEqual(self.main('--steps', 'street,scores', image=EXPLICIT, score_image=SCORE_EXPLICIT,
                                       scratch=self.root / 'f', environ={'TERRIER_SCORE_IMAGE': 'ghcr.io/x/y@sha256:' + 'e' * 64})[0], 0)
            self.assertEqual((asked, self.images[-1]), ([], {'geo': EXPLICIT, 'score': SCORE_EXPLICIT}))           # the flag beat both
            asked.clear()
            self.assertEqual(self.main('--steps', 'roads', image=None, score_image=None, scratch=self.root / 'g')[0], 0)
            self.assertEqual(asked, ['terrier-build'])                                         # a geo-only run never asks for the score image

    def test_a_score_image_that_is_not_pinned_by_digest_is_refused_before_any_build(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stop:
            self.main('--steps', 'shown', score_image='ghcr.io/pjg93-222/terrier-build-score:v1')
        self.assertEqual(stop.exception.code, 2)
        self.assertEqual(self.calls, [])

    def test_a_windows_only_run_needs_no_image_for_any_step(self):
        asked, patch = self.manifest(answer=None)
        with patch:
            code, _ = self.main('--steps', self.CHAIN, '--skip-container', image=None, score_image=None)
        self.assertEqual((code, asked, self.images), (0, [], []))
        self.assertEqual(len(self.pythons), 2)

    def test_the_volume_export_is_the_default_and_export_bind_opts_out(self):
        self.main()
        self.assertIs(self.calls[2][3], False)
        self.calls.clear()
        code, text = self.main('--export-bind', scratch=self.root / 'scratch2')
        self.assertEqual(code, 0, text)
        self.assertIs(self.calls[2][3], True)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):       # the old opt-in flag is gone, loudly
            self.main('--export-volume', scratch=self.root / 'scratch3')

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

    def test_single_reference_builds_the_windows_side_once_and_is_judged_exactly(self):
        code, text = self.main('--single-reference', '--steps', 'bundle', '--negative-control')       # the bundle has no stamp: B equals the one build
        self.assertEqual(code, 0, text)
        self.assertEqual([c[0] for c in self.calls], ['windows', 'container'])
        self.assertFalse((self.root / 'scratch' / 'A2').exists())
        self.assertRegex(text, r'PASS  bundle-out/k3\.zip +byte-identical')
        self.assertRegex(text, r'PASS  negative control bundle-out/k3\.zip .*detected')
        self.assertIs(json.loads((self.root / 'scratch' / 'parity_report.json').read_text(encoding='utf-8'))['single_reference'], True)
        code, text = self.main('--single-reference', scratch=self.root / 'roads')            # a store with a stamp is not equal to its one reference
        self.assertEqual(code, 1, text)
        self.assertRegex(text, r'FAIL  roads\.sqlite3')
        self.calls.clear()
        self.main('--steps', 'bundle', scratch=self.root / 'two')
        self.assertEqual([c[0] for c in self.calls], ['windows', 'windows', 'container'])      # the default is still A1, A2, B

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


    # The container image: --image, then $TERRIER_BUILD_IMAGE, then the verified release manifest (Melange current.py); none = stop.

    def manifest(self, answer=MANIFEST):
        """`answer`: what current.py would print for any name, or a {name: ref} dict."""
        asked = []
        reply = lambda name: asked.append(name) or (answer[name] if isinstance(answer, dict) else answer)      # noqa: E731
        return asked, mock.patch.object(P, 'manifest_image', reply)

    def test_without_a_flag_or_variable_the_image_is_the_verified_manifests(self):
        asked, patch = self.manifest()
        with patch:
            code, text = self.main(image=None)
        self.assertEqual(code, 0, text)
        self.assertEqual((asked, self.images), (['terrier-build'], [{'geo': MANIFEST}]))
        report = json.loads((self.root / 'scratch' / 'parity_report.json').read_text(encoding='utf-8'))
        self.assertEqual((report['images'], report['image_sources']), ({'geo': MANIFEST}, {'geo': 'manifest'}))

    def test_the_flag_and_then_the_variable_beat_the_manifest(self):
        asked, patch = self.manifest(answer=None)
        with patch:
            self.assertEqual(self.main(image='ghcr.io/x/terrier-build@sha256:' + 'c' * 64, environ={'TERRIER_BUILD_IMAGE': EXPLICIT})[0], 0)
            self.assertEqual(self.main(image=None, environ={'TERRIER_BUILD_IMAGE': EXPLICIT}, scratch=self.root / 'two')[0], 0)
        self.assertEqual(asked, [])
        self.assertEqual(self.images, [{'geo': 'ghcr.io/x/terrier-build@sha256:' + 'c' * 64}, {'geo': EXPLICIT}])
        report = json.loads((self.root / 'two' / 'parity_report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['image_sources'], {'geo': 'explicit'})

    def test_windows_only_never_asks_the_manifest(self):
        asked, patch = self.manifest(answer=None)
        with patch:
            self.assertEqual(self.main('--skip-container', image=None)[0], 0)
        self.assertEqual((asked, self.images), ([], []))

    def test_no_verified_manifest_and_no_explicit_image_stops_before_any_build(self):
        refusal = subprocess.CompletedProcess([], 1, stdout='', stderr='current: NO_CURRENT: no release has been published')
        with mock.patch.object(P.subprocess, 'run', return_value=refusal) as run, self.assertRaises(SystemExit) as stop:
            self.main(image=None)
        self.assertIn('no verified image for terrier-build: not running (current: NO_CURRENT', str(stop.exception))
        self.assertEqual(self.calls, [])                                     # neither Windows build ran, nor the container
        self.assertEqual(run.call_count, 1)                                  # the only subprocess was current.py: no docker

    def test_a_manifest_answer_that_is_not_pinned_by_digest_is_still_refused(self):
        asked, patch = self.manifest(answer='ghcr.io/pjg93-222/terrier-build:v1')
        with patch, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stop:
            self.main(image=None)
        self.assertEqual(stop.exception.code, 2)
        self.assertEqual(self.calls, [])

    def test_manifest_image_asks_current_py_by_name_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            tool = Path(folder) / 'current.py'
            tool.write_text('import sys\nif sys.argv[1:] == ["image", "terrier-build"]:\n    print("%s")\nelse:\n    sys.exit(1)\n' % MANIFEST)
            with mock.patch.dict(os.environ, {'MELANGE_RELEASE_CURRENT': str(tool)}):
                self.assertEqual(P.manifest_image('terrier-build'), MANIFEST)
                with self.assertRaises(SystemExit) as stop:                   # a name the manifest does not have: exit 1, no output
                    P.manifest_image('nope')
                self.assertIn('no verified image for nope', str(stop.exception))
            tool.write_text('')                                              # exit 0 and nothing printed is a refusal too
            with mock.patch.dict(os.environ, {'MELANGE_RELEASE_CURRENT': str(tool)}), self.assertRaises(SystemExit) as stop:
                P.manifest_image('terrier-build')
            self.assertIn('current.py printed nothing', str(stop.exception))
            with mock.patch.dict(os.environ, {'MELANGE_RELEASE_CURRENT': str(Path(folder) / 'missing.py')}), self.assertRaises(SystemExit):
                P.manifest_image('terrier-build')                            # no release tool at all
        self.assertTrue(P.RELEASE_CURRENT.endswith(r'ops\release_lib\current.py'))


class ExportViaVolumeTests(unittest.TestCase):
    """export_via_volume and container_build with the docker CLI replaced by a recorder: the order of the calls and the cleanup."""

    IMAGE = 'ghcr.io/x/terrier-build@sha256:' + 'a' * 64
    SCORE = 'ghcr.io/x/terrier-build-score@sha256:' + 'c' * 64

    def record(self, fail_on=None, stdout=''):
        calls, self.envs = [], []

        def fake_run(argv, **kwargs):
            calls.append(argv[1:] if argv[0] == 'docker' else argv)
            self.envs.append(kwargs.get('env'))
            if fail_on and argv[1:3] == fail_on:
                raise subprocess.CalledProcessError(1, argv)
            return subprocess.CompletedProcess(argv, 0, stdout=stdout)

        return calls, [mock.patch.object(P.shutil, 'which', lambda name: 'docker'), mock.patch.object(P.subprocess, 'run', fake_run)]

    def export(self, patches):
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            P.export_via_volume({'geo': self.IMAGE}, Path('repo'), Path('out'), 'B-1', ['street.sqlite3', 'contacts.sqlite3'])

    def test_the_volume_is_made_owned_by_the_build_user_used_copied_out_and_removed(self):
        calls, patches = self.record()
        self.export(patches)
        self.assertEqual([c[:2] for c in calls], [['volume', 'create'], ['run', '--rm'], ['compose', '-f'], ['create', '--name'],
                                                  ['cp', 'terrier-export-cp-B-1:/out/.'], ['rm', '-v'],
                                                  ['volume', 'rm']])
        self.assertEqual(calls[0], ['volume', 'create', 'terrier-export-B-1'])
        self.assertIn('10001:10001', calls[1])
        self.assertEqual(calls[1][calls[1].index('--user') + 1], '0')
        self.assertEqual(calls[2][1:3], ['-f', str(P.COMPOSE_FILE)])                       # the default compose file alone: no override
        self.assertNotIn(str(P.EXPORT_BIND_FILE), calls[2])
        self.assertEqual(calls[2][-5:], ['terrier-export', 'export', '/out', 'street.sqlite3', 'contacts.sqlite3'])
        self.assertEqual(calls[-1], ['volume', 'rm', 'terrier-export-B-1'])

    def test_a_failed_export_still_removes_the_holder_and_the_volume(self):
        calls, patches = self.record(fail_on=['cp', 'terrier-export-cp-B-1:/out/.'])
        with self.assertRaises(subprocess.CalledProcessError):
            self.export(patches)
        self.assertEqual([c[:2] for c in calls][-2:], [['rm', '-v'], ['volume', 'rm']])
        calls, patches = self.record(fail_on=['compose', '-f'])
        with self.assertRaises(subprocess.CalledProcessError):
            self.export(patches)
        self.assertEqual(calls[-1], ['volume', 'rm', 'terrier-export-B-1'])

    def build(self, patches, images=None, steps=('street',), names=('street.sqlite3',), **kwargs):
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            P.container_build(images or {'geo': self.IMAGE}, Path('repo'), 'co-05', list(steps), Path('out'), 'B-1', list(names), **kwargs)

    def test_a_container_build_exports_through_a_volume_unless_export_bind_is_given(self):
        calls, patches = self.record()
        self.build(patches)
        self.assertEqual(calls[0][-5:], ['terrier-build', 'run', 'street', '--district', 'co-05'])
        self.assertEqual([c[:2] for c in calls[1:]], [['volume', 'create'], ['run', '--rm'], ['compose', '-f'], ['create', '--name'],
                                                      ['cp', 'terrier-export-cp-B-1:/out/.'], ['rm', '-v'], ['volume', 'rm']])
        self.assertFalse(any(str(P.EXPORT_BIND_FILE) in c for c in calls))

    # Image and service per step: the geo steps in terrier-build, the score steps in terrier-score, one stage volume for all.

    def test_each_step_runs_in_the_service_of_its_toolchain_and_both_image_variables_are_set(self):
        calls, patches = self.record()
        self.build(patches, images={'geo': self.IMAGE, 'score': self.SCORE}, steps=('roads', 'street', 'contacts', 'shown', 'scores'),
                   names=('scores.sqlite3',))
        runs = calls[:5]
        self.assertEqual([c[-5] for c in runs], ['terrier-build'] * 3 + ['terrier-score'] * 2)
        self.assertEqual([c[-3] for c in runs], ['roads', 'street', 'contacts', 'shown', 'scores'])
        self.assertTrue(all(c[-4] == 'run' and c[-2:] == ['--district', 'co-05'] for c in runs))
        compose = [env for c, env in zip(calls, self.envs) if c[0] == 'compose']
        self.assertEqual(len(compose), 6)                                                 # five steps and the export
        for env in compose:
            self.assertEqual((env['TERRIER_BUILD_IMAGE'], env['TERRIER_SCORE_IMAGE']), (self.IMAGE, self.SCORE))
        self.assertEqual(calls[-1], ['volume', 'rm', 'terrier-export-B-1'])

    def test_a_run_of_one_toolchain_gives_the_other_variable_the_same_image_and_exports_with_the_image_it_has(self):
        calls, patches = self.record()
        self.build(patches, images={'geo': self.IMAGE})
        compose = [env for c, env in zip(calls, self.envs) if c[0] == 'compose']
        self.assertTrue(all((e['TERRIER_BUILD_IMAGE'], e['TERRIER_SCORE_IMAGE']) == (self.IMAGE, self.IMAGE) for e in compose))
        calls, patches = self.record()
        self.build(patches, images={'score': self.SCORE}, steps=('shown', 'scores'), names=('scores.sqlite3',))
        self.assertEqual([c[-5] for c in calls[:2]], ['terrier-score', 'terrier-score'])
        compose = [env for c, env in zip(calls, self.envs) if c[0] == 'compose']
        self.assertTrue(all((e['TERRIER_BUILD_IMAGE'], e['TERRIER_SCORE_IMAGE']) == (self.SCORE, self.SCORE) for e in compose))
        self.assertIn(self.SCORE, calls[3])                                               # the chown of the export volume
        self.assertIn(self.SCORE, calls[5])                                               # the holder `docker cp` reads from
        self.assertFalse(any(self.IMAGE in c for c in calls))

    def test_the_container_fingerprint_asks_each_toolchains_service(self):
        calls, patches = self.record(stdout='noise\n{"python": "3.12.10"}\n')
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            found = P.container_fingerprint({'geo': self.IMAGE, 'score': self.SCORE}, Path('repo'), Path('out'), 'B-1')
        self.assertEqual(found, {'geo': {'python': '3.12.10'}, 'score': {'python': '3.12.10'}})
        self.assertEqual([(c[-2], c[-1]) for c in calls], [('terrier-build', 'fingerprint'), ('terrier-score', 'fingerprint')])

    def test_export_bind_exports_straight_into_the_scratch_folder_with_the_bind_override_and_no_volume(self):
        calls, patches = self.record()
        self.build(patches, export_bind=True)
        self.assertEqual(len(calls), 2)                                                      # the build step, the export: nothing else
        self.assertEqual(calls[1][1:5], ['-f', str(P.COMPOSE_FILE), '-f', str(P.EXPORT_BIND_FILE)])
        self.assertEqual(calls[1][-4:], ['terrier-export', 'export', '/out', 'street.sqlite3'])
        self.assertFalse(any(c[0] in ('volume', 'cp', 'create', 'rm') for c in calls))

    def test_the_default_file_exports_into_a_named_volume_and_the_bind_override_replaces_only_that_mount(self):
        default = read('compute.compose.yaml')
        export = default[default.index('  terrier-export:'):default.index('\nvolumes:')]
        self.assertEqual(re.findall(r'- type: (\w+)\n\s+source: export_out\n\s+target: /out', export), ['volume'])
        self.assertNotIn('HOST_OUT', re.sub(r'(?m)^\s*#.*$', '', default))                  # the default no longer needs the scratch folder
        self.assertRegex(default, r'(?m)^  export_out:\n    external: true\n    name: terrier-export-\$\{RUN_ID:\?')
        text = read('compute.export-bind.compose.yaml')
        body = '\n'.join(l for l in text.splitlines() if not l.lstrip().startswith('#'))
        self.assertEqual(re.findall(r'^  (\S+):$', body, re.M), ['terrier-export'])           # one service, no volumes of its own
        self.assertEqual(re.findall(r'^\s+- type: (\w+)', body, re.M), ['bind'])
        self.assertEqual(re.findall(r'^\s+target: (.+)$', body, re.M), ['/out'])
        self.assertIn('source: ${HOST_OUT:?', body)
        self.assertNotIn('type: volume', body)
        self.assertNotIn('cu-geography-v3', text)


class WindowsBuildTests(unittest.TestCase):
    """windows_build and windows_fingerprint with subprocess.run replaced by a recorder: which interpreter and environment each step gets."""

    ENV = {'PYTHONPATH': 'elsewhere', 'CU_DATA_DIR': 'live', 'TERRIER_LOCAL_DIR': 'live', 'KEEP': '1'}
    PYTHONS = {'geo': 'GEO.exe', 'score': 'SCORE.exe'}

    def record(self, stdout='{"python": "3"}'):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs.get('env')))
            return subprocess.CompletedProcess(argv, 0, stdout=stdout)

        return calls, mock.patch.object(P.subprocess, 'run', fake_run)

    def test_each_step_runs_in_the_interpreter_of_its_toolchain_into_the_scratch_folder_only(self):
        calls, patch = self.record()
        with patch, mock.patch.dict(os.environ, self.ENV, clear=True):
            P.windows_build(self.PYTHONS, REPO, 'co-05', ['roads', 'street', 'contacts', 'shown', 'scores'], Path('scratch') / 'A1')
        self.assertEqual([c[0][0] for c in calls], ['GEO.exe'] * 3 + ['SCORE.exe'] * 2)
        self.assertEqual([c[0][1:] for c in calls], [[str(REPO / 'tools' / 'build_step.py'), 'run', step, '--district', 'co-05']
                                                     for step in ('roads', 'street', 'contacts', 'shown', 'scores')])
        for _argv, env in calls:
            self.assertEqual(env['TERRIER_LOCAL_DIR'], str(Path('scratch') / 'A1'))      # never the live .local
            self.assertNotIn('CU_DATA_DIR', env)
            self.assertEqual(env['KEEP'], '1')

    def test_the_score_steps_run_isolated_from_the_user_site_as_their_documented_command_line_has_it(self):
        calls, patch = self.record()
        with patch, mock.patch.dict(os.environ, self.ENV, clear=True):
            P.windows_build(self.PYTHONS, REPO, 'co-05', ['street', 'shown'], Path('A1'))
        geo, score = (env for _argv, env in calls)
        self.assertEqual(geo['PYTHONPATH'], 'elsewhere')                                   # the geo steps keep the environment as it was
        self.assertNotIn('PYTHONNOUSERSITE', geo)
        self.assertNotIn('PYTHONPATH', score)                                              # env -u PYTHONPATH PYTHONNOUSERSITE=1 ...
        self.assertEqual((score['PYTHONNOUSERSITE'], score['PYTHONDONTWRITEBYTECODE']), ('1', '1'))

    def test_the_fingerprint_is_taken_in_each_toolchains_interpreter(self):
        calls, patch = self.record()
        with patch, mock.patch.dict(os.environ, self.ENV, clear=True):
            found = P.windows_fingerprint(self.PYTHONS, REPO, ['geo', 'score'])
        self.assertEqual(found, {'geo': {'python': '3'}, 'score': {'python': '3'}})
        self.assertEqual([(c[0][0], c[0][-1]) for c in calls], [('GEO.exe', 'fingerprint'), ('SCORE.exe', 'fingerprint')])
        self.assertEqual(calls[1][1]['PYTHONNOUSERSITE'], '1')
        self.assertNotIn('TERRIER_LOCAL_DIR', calls[1][1])              # a fingerprint builds nothing: it is not pointed at any folder


# ---- the image, the compose file, the locks, the workflow ------------------------------------------------------------------------

def read(name):
    return (REPO / name).read_text(encoding='utf-8')


def pins(text):
    """{normalised name: version} of the `name==version` lines of a requirements file or lock."""
    return {re.sub(r'[-_.]+', '-', m.group(1).lower()): m.group(2)
            for m in re.finditer(r'^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\]+)', text, re.M)}


class BuildImageFilesTests(unittest.TestCase):
    def test_the_runbook_documents_the_switch_the_images_and_the_demo_key_of_the_production_build(self):
        text = read('COMPUTE.md')
        for needed in ('tools/compute_build.py', 'TERRIER_COMPUTE', "$env:TERRIER_COMPUTE = 'windows'", 'TERRIER_BUILD_IMAGE', 'TERRIER_SCORE_IMAGE',
                       'terrier-build@sha256:62d17090', 'terrier-build-score@sha256:9f0c4fe5', '.demo-key', '_build/', 'none is tracked in git'):
            self.assertIn(needed, text)
        self.assertIn('TERRIER_COMPUTE', read('tools/compute_build.py'))

    def test_the_runbook_documents_the_later_steps_their_inputs_the_bundle_parity_and_the_runners(self):
        text = read('COMPUTE.md')
        for needed in ('## 10.', '--inputs-from', '--link-inputs', '--drop-stage', 'TERRIER_WALK_SALT', '.walk-salt', 'Step.held', 'D-177', 'import_summary',
                       '--single-reference', 'canonical ring start', 'create_system',
                       '.bak-20261013', 'run_remaining.ps1', 'run_fast.ps1', 'run_nowithhold.ps1', '(b)', '(c)', 'meta[built_utc, qa.peak_memory_mb, qa.timing]'):
            self.assertIn(needed, text)
        for step in S.STEPS:
            self.assertIn('`%s`' % step, text)

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
        self.assertEqual(text.count('apt-get -y --no-install-recommends upgrade'), 2)       # both final stages get Debian's security updates

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
            if re.match(r'\s+- type: (bind|volume|tmpfs)', line):
                current = {'type': line.split(':')[1].strip()}
                mounts.append(current)
            elif current is not None and (m := re.match(r'\s+(source|target|read_only): (.+)$', line)):
                current[m.group(1)] = m.group(2).strip()
        self.assertGreaterEqual(len(mounts), 10)
        writable = sorted(m['target'] for m in mounts if m.get('read_only') != 'true' and m['type'] != 'tmpfs')
        self.assertEqual(writable, ['/glassbox-home/.claude/glassbox/events', '/out', '/stage'])
        self.assertEqual({m['target'] for m in mounts if m['type'] == 'bind' and m.get('read_only') == 'true'},
                         {'/terrier', '/sources/statara_co_cd005_20260904.csv', '/sources/terrier-sources', '/geography',
                          '/sibling/.local/.street-salt', '/sibling/.local/row-framework-20260930'})

    def test_the_repo_bind_is_masked_at_its_live_local_and_build_folders_in_every_service_that_mounts_it(self):
        """The /terrier bind is the whole tree, so the live checkout's .local (voter data) and _build would show through it: both are
        covered by an empty tmpfs, in the shared mount list (build and score) and in the export service."""
        text = read('compute.compose.yaml')
        build = text[text.index('  terrier-build:'):text.index('  terrier-score:')]
        export = text[text.index('  terrier-export:'):text.index('\nvolumes:')]
        for part in (build, export):
            self.assertEqual(part.count('target: /terrier\n'), 1)
            for folder in ('/terrier/.local', '/terrier/_build'):
                self.assertRegex(part, r'(?m)^      - type: tmpfs\n        target: %s\n' % re.escape(folder))
        self.assertEqual(text.count('type: tmpfs'), 4)                      # these two masks, twice: nothing else is in memory
        self.assertNotRegex(text, r'(?m)target: /terrier/\.local\n\s+read_only: false')
        try:
            import yaml
        except ImportError:
            return
        config = yaml.safe_load(text.replace('\r\n', '\n'))
        for name in ('terrier-build', 'terrier-score', 'terrier-export'):
            targets = [v['target'] for v in config['services'][name]['volumes'] if v['type'] == 'tmpfs']
            self.assertEqual(targets, ['/terrier/.local', '/terrier/_build'], name)

    def test_the_score_service_has_the_score_image_and_exactly_the_mounts_of_the_build_service(self):
        text = read('compute.compose.yaml')
        self.assertEqual(re.findall(r'(?m)^  (terrier-[a-z]+):$', text), ['terrier-build', 'terrier-score', 'terrier-export'])
        build = text[text.index('  terrier-build:'):text.index('  terrier-score:')]
        score = text[text.index('  terrier-score:'):text.index('  terrier-export:')]
        self.assertRegex(build, r'(?m)^    volumes: &build-volumes\n')                        # one list of mounts, defined once ...
        self.assertRegex(score, r'(?m)^    <<: \*build\n    image: \$\{TERRIER_SCORE_IMAGE:\?pin the score image by digest')
        self.assertRegex(score, r'(?m)^    volumes: \*build-volumes\n')                       # ... and shared: the score steps mount nothing else
        self.assertNotIn('- type:', score)
        self.assertEqual(text.count('TERRIER_DEMO_KEY: ${TERRIER_DEMO_KEY:-}'), 1)            # in the shared environment, empty by default
        self.assertEqual(text.count('TERRIER_WALK_SALT: ${TERRIER_WALK_SALT:-}'), 1)          # likewise the walk salt
        self.assertEqual(text.count('TERRIER_SCORE_IMAGE:?'), 1)                              # the same :? rule as TERRIER_BUILD_IMAGE
        self.assertEqual(text.count('TERRIER_BUILD_IMAGE:?'), 1)
        self.assertNotIn('cu-geography-v3', score)

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

    def test_the_draft_release_folds_the_build_images_into_digests_json_for_the_platform_manifest(self):
        """ops/release.py reads ONE digests.json: images.terrier-web plus every other images.<name> whose ref is
        ghcr.io/pjg93-222/<name>@sha256:... (extra_images). The merge step is run here with the workflow's own jq programs."""
        text = read('.github/workflows/release.yml')
        step = text[text.index('- name: Attach to a draft release'):]
        check, merge = re.findall(r"jq (?:-e )?-s '([^']+)' digests\.json terrier-build\.digests\.json", step)
        self.assertLess(step.index(merge), step.index('gh release upload'))                 # folded before anything is attached
        self.assertIn('terrier-build.digests.json', step[step.index('gh release upload'):])  # and the separate file is still attached
        self.assertIn('IMAGE: ghcr.io/pjg93-222/terrier-web\n', text)
        self.assertIn('BUILD_IMAGE: ghcr.io/pjg93-222/terrier-build\n', text)
        self.assertIn('SCORE_IMAGE: ghcr.io/pjg93-222/terrier-build-score\n', text)         # repository name == manifest image name
        jq = shutil.which('jq')
        if not jq:
            self.skipTest('jq is not installed (the release workflow runs this on ubuntu, where it is)')

        def run(program, *files, flags=()):
            with tempfile.TemporaryDirectory() as folder:
                for name, doc in zip(('digests.json', 'terrier-build.digests.json'), files):
                    (Path(folder) / name).write_text(json.dumps(doc), encoding='utf-8')
                return subprocess.run([jq, *flags, '-s', program, 'digests.json', 'terrier-build.digests.json'], cwd=folder,
                                      capture_output=True, text=True)

        head = {'tag': 'v2026.10.12.9', 'commit': 'c' * 40}
        web = {**head, 'images': {'terrier-web': 'ghcr.io/pjg93-222/terrier-web@sha256:' + '1' * 64}}
        build = {**head, 'images': {'terrier-build': 'ghcr.io/pjg93-222/terrier-build@sha256:' + '2' * 64,
                                    'terrier-build-score': 'ghcr.io/pjg93-222/terrier-build-score@sha256:' + '3' * 64}}
        self.assertEqual(run(check, web, build, flags=['-e']).returncode, 0)
        merged = json.loads(run(merge, web, build).stdout)
        self.assertEqual({k: merged[k] for k in head}, head)
        self.assertEqual(merged['images'], {**web['images'], **build['images']})
        for name, ref in merged['images'].items():                                          # the shape release.extra_images accepts
            self.assertRegex(ref, r'^ghcr\.io/pjg93-222/%s@sha256:[0-9a-f]{64}$' % re.escape(name))
        for other in ({'tag': 'v2026.10.12.8'}, {'commit': 'd' * 40}):                      # two builds of different tags or commits
            self.assertNotEqual(run(check, web, {**build, **other}, flags=['-e']).returncode, 0, other)


class ComputeBuildTests(unittest.TestCase):
    """tools/compute_build.py: the store build runs in the containers unless TERRIER_COMPUTE=windows. Both builds are replaced by fakes
    that write the stores, so nothing starts: no Docker, no Windows tool."""

    CHAIN = ['roads', 'street', 'contacts', 'shown', 'scores', 'walk', 'ingest', 'extend', 'bundle', 'density']          # nothing is held
    KEY = '0123456789abcdef' * 4
    SALT = 'fedcba9876543210' * 4

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.calls, self.manifest, self.seeded, self.salts, self.dropped = [], [], [], [], []

    @staticmethod
    def write(steps, folder):
        for step in steps:
            for name in (S.STEPS[step].store, *S.STEPS[step].also, *S.STEPS[step].keep):
                path = folder / name.replace('*', 'x')              # a `*` name stands for one file here
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'x')

    def run_main(self, *args, environ=None, key_file=None, out='out', write=True, fail=None):
        """-> (exit code, stdout). The environment holds both image variables and only what `environ` adds. `key_file`: the folder that
        stands for the district's data folder (where its .demo-key would be)."""
        def container(images, repo, district, steps, folder, run_id, names, export_bind=False):
            self.calls.append(('container', images, district, list(steps), run_id, tuple(names), export_bind, os.environ.get('TERRIER_DEMO_KEY')))
            self.salts.append(os.environ.get('TERRIER_WALK_SALT'))
            if fail is not None:
                raise fail
            if write:
                self.write(steps, folder)

        def windows(pythons, repo, district, steps, folder):
            self.calls.append(('windows', pythons, district, list(steps), os.environ.get('TERRIER_DEMO_KEY')))
            self.salts.append(os.environ.get('TERRIER_WALK_SALT'))
            if write:
                self.write(steps, folder)

        def asked(name):
            self.manifest.append(name)
            return 'ghcr.io/pjg93-222/%s@sha256:%s' % (name, 'e' * 64)

        env = {'TERRIER_BUILD_IMAGE': EXPLICIT, 'TERRIER_SCORE_IMAGE': SCORE_EXPLICIT, 'TERRIER_WALK_SALT': self.SALT, **(environ or {})}
        text = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, env, clear=True))
            for target, fake in (('container_build', container), ('windows_build', windows), ('manifest_image', asked),
                                 ('container_fingerprint', lambda *a: {'where': 'box'}), ('windows_fingerprint', lambda *a: {'where': 'win'}),
                                 ('seed_stage', lambda *a: self.seeded.append(('stage', a[5], tuple(a[6])))),
                                 ('copy_inputs', lambda *a: self.seeded.append(('copy', a[3], tuple(a[2])))),
                                 ('drop_stage', lambda *a: self.dropped.append(a[1]))):
                stack.enter_context(mock.patch.object(P, target, fake))
            if key_file is not None:
                real = CB.D.data_dir
                stack.enter_context(mock.patch.object(CB.D, 'data_dir', lambda d, local=None: key_file if local is None else real(d, local)))
            stack.enter_context(contextlib.redirect_stdout(text))
            code = CB.main(['--out', str(self.root / out), *args])
        return code, text.getvalue()

    def test_the_default_is_the_containers_each_step_in_its_toolchain_and_the_volume_export(self):
        code, text = self.run_main(key_file=self.root / 'no-key-here', environ={'TERRIER_DEMO_KEY': self.KEY})
        self.assertEqual(code, 0, text)
        (mode, images, district, steps, run_id, names, bind, key), = self.calls
        self.assertEqual((mode, images, district, steps, bind), ('container', {'geo': EXPLICIT, 'score': SCORE_EXPLICIT}, 'co-05', self.CHAIN, False))
        self.assertEqual(names, ('roads.sqlite3', 'street.sqlite3', 'contacts.sqlite3', 'area_scores_shown.csv',
                                 'area_scores_shown.manifest.json', 'scores.sqlite3', 'scores_receipt.json', 'walk.sqlite3', 'ingest/*.zip',
                                 'ingest/ingest_receipt.json', 'ingest/v2/*', 'extend/*.zip', 'bundle-out/*.zip', 'bundle-out/*.zip.sha256',
                                 'bundle_receipt.json', 'density.json'))
        self.assertRegex(run_id, r'^P-\d{8}T\d{6}Z$')
        self.assertIn('BUILD OK (container): 16 file(s)', text)
        receipt = json.loads((self.root / 'out' / 'compute_build_receipt.json').read_text(encoding='utf-8'))
        self.assertEqual((receipt['mode'], receipt['image_sources'], receipt['fingerprint']),
                         ('container', {'geo': 'explicit', 'score': 'explicit'}, {'where': 'box'}))
        self.assertNotIn(self.KEY, json.dumps(receipt) + text)                  # the key goes to the build, never into a log or receipt
        self.assertNotIn(self.SALT, json.dumps(receipt) + text)                 # nor the walk salt
        self.assertIn('ingest/x.zip', receipt['files'])                          # the receipt names the files the `*` names stood for

    def test_terrier_compute_windows_is_the_one_switch_back_and_asks_for_no_image(self):
        for number, value in enumerate(('windows', ' Windows '), 1):
            self.calls.clear()
            code, text = self.run_main(environ={'TERRIER_COMPUTE': value, 'TERRIER_DEMO_KEY': self.KEY}, out='win%d' % number,
                                       key_file=self.root / 'x')
            self.assertEqual(code, 0, text)
            (mode, pythons, district, steps, key), = self.calls
            self.assertEqual((mode, district, steps), ('windows', 'co-05', self.CHAIN))
            self.assertEqual(pythons, {'geo': os.environ.get('CU_PYTHON') or str(CB.H.WORK_PY), 'score': str(CB.H.PY312)})
            self.assertIn('BUILD OK (windows)', text)
        self.assertEqual(self.manifest, [])
        receipt = json.loads((self.root / 'win1' / 'compute_build_receipt.json').read_text(encoding='utf-8'))
        self.assertEqual((receipt['mode'], receipt['images'], receipt['fingerprint']), ('windows', {}, {'where': 'win'}))

    def test_any_other_value_of_the_switch_stops_before_anything_runs(self):
        for value in ('docker', 'linux', 'true'):
            with self.assertRaises(SystemExit) as stop:
                self.run_main(environ={'TERRIER_COMPUTE': value})
            self.assertIn('TERRIER_COMPUTE', str(stop.exception))
        self.assertEqual((self.calls, CB.compute_mode({}), CB.compute_mode({'TERRIER_COMPUTE': ''})), ([], 'container', 'container'))

    def test_the_images_are_the_variables_then_the_verified_manifest_and_only_what_the_steps_need(self):
        self.assertEqual(self.run_main('--steps', 'roads', environ={'TERRIER_SCORE_IMAGE': ''}, out='geo')[0], 0)
        self.assertEqual((self.calls[-1][1], self.manifest), ({'geo': EXPLICIT}, []))
        self.assertEqual(self.run_main('--steps', 'shown', environ={'TERRIER_SCORE_IMAGE': ''}, out='score')[0], 0)
        self.assertEqual((self.calls[-1][1], self.manifest),
                         ({'score': 'ghcr.io/pjg93-222/terrier-build-score@sha256:' + 'e' * 64}, ['terrier-build-score']))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stop:        # a tag is never an image
            self.run_main('--steps', 'roads', environ={'TERRIER_BUILD_IMAGE': 'ghcr.io/x/terrier-build:v1'}, out='tag')
        self.assertEqual(stop.exception.code, 2)

    def test_plan_prints_what_would_run_and_starts_nothing(self):
        code, text = self.run_main('--plan', environ={'TERRIER_DEMO_KEY': ''}, key_file=self.root / 'no-key-here')
        self.assertEqual((code, self.calls), (0, []))                      # not even the demo key is needed to look
        plan = json.loads(text)
        self.assertEqual((plan['mode'], plan['steps'], plan['images']), ('container', self.CHAIN, {'geo': EXPLICIT, 'score': SCORE_EXPLICIT}))
        self.assertEqual(plan['where'], {'roads': 'terrier-build', 'street': 'terrier-build', 'contacts': 'terrier-build',
                                         'shown': 'terrier-score', 'scores': 'terrier-score', 'walk': 'terrier-build',
                                         'ingest': 'terrier-build', 'extend': 'terrier-build', 'bundle': 'terrier-build', 'density': 'terrier-build'})
        self.assertFalse((self.root / 'out').exists())
        plan = json.loads(self.run_main('--plan', environ={'TERRIER_COMPUTE': 'windows'})[1])
        self.assertEqual((plan['mode'], plan['images'], plan['where']['shown']), ('windows', {}, str(CB.H.PY312)))

    def test_scores_keeps_the_districts_demo_key_and_stops_without_one(self):
        with self.assertRaises(SystemExit) as stop:
            self.run_main(environ={'TERRIER_DEMO_KEY': ''}, key_file=self.root / 'no-key-here')
        self.assertIn('demo key', str(stop.exception))
        self.assertEqual(self.calls, [])
        kept = self.root / 'district'
        kept.mkdir()
        (kept / '.demo-key').write_bytes(bytes.fromhex(self.KEY))
        self.assertEqual(self.run_main(environ={'TERRIER_DEMO_KEY': ''}, key_file=kept, out='kept')[0], 0)
        self.assertEqual(self.calls[-1][-1], self.KEY)                          # the build starts from the key that is there
        self.assertEqual(self.run_main(environ={'TERRIER_DEMO_KEY': 'ab' * 32}, key_file=kept, out='given')[0], 0)
        self.assertEqual(self.calls[-1][-1], 'ab' * 32)                         # a key that is given wins
        self.assertEqual(self.run_main('--steps', 'roads,street,contacts', environ={'TERRIER_DEMO_KEY': ''}, key_file=self.root / 'none',
                                       out='geo')[0], 0)
        self.assertFalse(self.calls[-1][-1])                                    # no scores step: no key needed, none set

    def test_refusals_a_local_folder_a_folder_with_files_and_an_unknown_step(self):
        (self.root / 'full').mkdir()
        (self.root / 'full' / 'file').write_text('x')
        env = {'TERRIER_BUILD_IMAGE': EXPLICIT, 'TERRIER_SCORE_IMAGE': SCORE_EXPLICIT}
        with contextlib.redirect_stderr(io.StringIO()):
            for argv in (['--out', str(self.root / '.local' / 'x')], ['--out', str(self.root / 'full')],
                         ['--out', str(self.root / 'new'), '--steps', 'nope'], ['--out', str(self.root / 'new'), '--steps', '']):
                with mock.patch.dict(os.environ, env), self.assertRaises(SystemExit) as stop:
                    CB.main(argv)
                self.assertEqual(stop.exception.code, 2, argv)

    def test_a_build_that_exits_cleanly_without_its_files_is_a_failure_with_no_receipt(self):
        code, text = self.run_main(environ={'TERRIER_DEMO_KEY': self.KEY}, write=False)
        self.assertEqual(code, 1)
        self.assertFalse((self.root / 'out' / 'compute_build_receipt.json').exists())
        self.assertNotIn('BUILD OK', text)

    # steps that read the stores of earlier steps: --inputs

    NEEDED_BY_WALK = ('roads.sqlite3', 'street.sqlite3', 'contacts.sqlite3', 'scores.sqlite3')

    def district_folder(self, salt=True):
        folder = self.root / 'district'
        folder.mkdir(exist_ok=True)
        for name in self.NEEDED_BY_WALK:
            (folder / name).write_bytes(b'x')
        if salt:
            (folder / '.walk-salt').write_text(self.SALT, encoding='ascii')
        return folder

    def test_steps_that_read_earlier_stores_take_them_from_inputs_in_the_stage_or_the_out_folder_and_the_salt_comes_from_there(self):
        folder = self.district_folder()
        code, text = self.run_main('--steps', 'walk', '--inputs', str(folder), environ={'TERRIER_WALK_SALT': ''}, key_file=folder)
        self.assertEqual(code, 0, text)
        self.assertEqual(self.seeded, [('stage', folder, self.NEEDED_BY_WALK)])
        self.assertEqual((self.calls[-1][3], self.salts[-1]), (['walk'], self.SALT))
        self.assertNotIn(self.SALT, text)
        receipt = json.loads((self.root / 'out' / 'compute_build_receipt.json').read_text(encoding='utf-8'))
        self.assertEqual(receipt['inputs'], {'from': str(folder), 'names': list(self.NEEDED_BY_WALK)})
        self.assertIn('walk.sqlite3', receipt['files'])
        self.seeded.clear()
        code, text = self.run_main('--steps', 'walk', '--inputs', str(folder), environ={'TERRIER_COMPUTE': 'windows'}, key_file=folder, out='win')
        self.assertEqual(code, 0, text)
        self.assertEqual(self.seeded, [('copy', folder, self.NEEDED_BY_WALK)])                  # the Windows fallback starts from the same inputs
        self.assertEqual(json.loads(self.run_main('--steps', 'walk', '--inputs', str(folder), '--plan', out='planned')[1])['inputs'], list(self.NEEDED_BY_WALK))

    def test_the_bundle_is_in_the_default_chain_now_its_parity_passed(self):
        self.assertEqual([name for name, spec in S.STEPS.items() if spec.held], [])
        plan = json.loads(self.run_main('--plan', key_file=self.root / 'x', environ={'TERRIER_DEMO_KEY': self.KEY})[1])
        self.assertEqual(plan['steps'], self.CHAIN)
        self.assertIn('bundle-out/*.zip', plan['files'])
        self.assertEqual(plan['inputs'], [])                       # bundle reads the shown table, the score store and the extend folder: all made by the chain

    def test_a_held_step_is_left_out_of_the_default_chain_and_refused_in_a_container_but_builds_on_windows(self):
        """The mechanism stays for the next step that fails its parity: held by a patched bundle step."""
        folder = self.root / 'district'
        (folder / 'extend').mkdir(parents=True)
        for name in ('area_scores_shown.csv', 'scores.sqlite3', 'extend/k.zip'):
            (folder / name).write_bytes(b'x')
        with mock.patch.dict(S.STEPS, {'bundle': S.STEPS['bundle']._replace(held='parity FAIL (D-177)')}):
            plan = json.loads(self.run_main('--plan', key_file=self.root / 'x', environ={'TERRIER_DEMO_KEY': self.KEY})[1])
            self.assertNotIn('bundle', plan['steps'])
            with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as stop:
                self.run_main('--steps', 'bundle', '--inputs', str(folder))
            self.assertEqual(stop.exception.code, 2)
            self.assertIn('held', err.getvalue())
            self.assertEqual(self.calls, [])
            code, text = self.run_main('--steps', 'bundle', '--inputs', str(folder), environ={'TERRIER_COMPUTE': 'windows'}, out='win')
            self.assertEqual((code, self.calls[-1][0], self.calls[-1][3]), (0, 'windows', ['bundle']))          # the Windows build is the accepted one

    def test_drop_stage_removes_the_folder_of_the_run_from_the_stage_volume_after_a_container_build_only(self):
        self.assertEqual(self.run_main(environ={'TERRIER_DEMO_KEY': self.KEY}, key_file=self.root / 'x')[0], 0)
        self.assertEqual(self.dropped, [])                                                    # off unless asked for: the stage keeps the run
        self.assertEqual(self.run_main('--drop-stage', environ={'TERRIER_DEMO_KEY': self.KEY}, key_file=self.root / 'x', out='dropped')[0], 0)
        self.assertEqual(len(self.dropped), 1)
        self.assertRegex(self.dropped[0], r'^P-\d{8}T\d{6}Z$')
        self.assertEqual(self.dropped[0], self.calls[-1][4])                                   # the run id the build used
        code, text = self.run_main('--drop-stage', environ={'TERRIER_COMPUTE': 'windows', 'TERRIER_DEMO_KEY': self.KEY}, key_file=self.root / 'x', out='win')
        self.assertEqual((code, len(self.dropped)), (0, 1))                                    # a Windows build has no stage
        code, text = self.run_main('--drop-stage', environ={'TERRIER_DEMO_KEY': self.KEY}, key_file=self.root / 'x', out='failed', write=False)
        self.assertEqual((code, len(self.dropped)), (1, 2))                                    # a build that left files out drops its stage too: the stores are voter-derived
        self.assertEqual(self.dropped[-1], self.calls[-1][4])
        with self.assertRaises(RuntimeError):                                                  # and so does one that raised
            self.run_main('--drop-stage', environ={'TERRIER_DEMO_KEY': self.KEY}, key_file=self.root / 'x', out='raised', fail=RuntimeError('docker died'))
        self.assertEqual((len(self.dropped), self.dropped[-1]), (3, self.calls[-1][4]))
        with self.assertRaises(RuntimeError):                                                  # without --drop-stage the stage stays, as before
            self.run_main(environ={'TERRIER_DEMO_KEY': self.KEY}, key_file=self.root / 'x', out='raised2', fail=RuntimeError('docker died'))
        self.assertEqual(len(self.dropped), 3)

    def test_a_given_salt_wins_and_a_run_with_no_salt_stops_before_anything_is_seeded(self):
        folder = self.district_folder(salt=False)
        with self.assertRaises(SystemExit) as stop:
            self.run_main('--steps', 'walk', '--inputs', str(folder), environ={'TERRIER_WALK_SALT': ''}, key_file=folder)
        self.assertIn('walk needs the district', str(stop.exception))
        self.assertEqual((self.calls, self.seeded), ([], []))
        self.assertEqual(self.run_main('--steps', 'walk', '--inputs', str(folder), environ={'TERRIER_WALK_SALT': 'ab' * 32}, key_file=folder)[0], 0)
        self.assertEqual(self.salts[-1], 'ab' * 32)
        self.assertEqual(self.run_main('--steps', 'roads', environ={'TERRIER_WALK_SALT': ''}, key_file=self.root / 'none', out='geo')[0], 0)
        self.assertFalse(self.salts[-1])                                                         # no walk step: no salt needed, none set

    def test_steps_that_read_stores_no_step_of_the_run_makes_stop_without_inputs_or_with_inputs_that_lack_them(self):
        folder = self.district_folder()
        (folder / 'street.sqlite3').unlink()
        with contextlib.redirect_stderr(io.StringIO()) as err:
            for argv, message in ((['--steps', 'walk'], '--inputs'), (['--steps', 'walk', '--inputs', str(folder)], 'has no street.sqlite3'),
                                  (['--steps', 'extend', '--inputs', str(folder)], 'has no ingest')):
                with self.assertRaises(SystemExit) as stop:
                    self.run_main(*argv, environ={'TERRIER_WALK_SALT': self.SALT})
                self.assertEqual(stop.exception.code, 2, argv)
                self.assertIn(message, err.getvalue())
        self.assertEqual((self.calls, self.seeded), ([], []))


# ---- the steps after the first five: walk, ingest, extend, bundle, density -------------------------------------------------

class NewStepTests(unittest.TestCase):
    """tools/build_step.py for the steps added to the chain: their command lines are the runners' (_build/run_remaining.ps1 R5 and
    _build/run_nowithhold.ps1), the files they leave are named, and what a run of some steps must be given is known."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / 'out'
        self.co = S.D.get('co-05')

    def run_recorded(self, step, environ=None, prepare=()):
        """S.run(step) with the tool replaced by a recorder that writes the step's files; (exit code, command, the env the tool got or None)."""
        shutil.rmtree(self.data, ignore_errors=True)
        for name, text in prepare:
            (self.data / name).parent.mkdir(parents=True, exist_ok=True)
            (self.data / name).write_text(text, encoding='utf-8')
        seen = []

        def fake_call(command, **kwargs):
            seen.append((command, kwargs.get('env')))
            for name in (S.STEPS[step].store, *S.STEPS[step].also):
                (self.data / name.replace('*', 'x')).parent.mkdir(parents=True, exist_ok=True)
                (self.data / name.replace('*', 'x')).write_text('x', encoding='utf-8')
            return 0

        with mock.patch.dict(os.environ, {'TERRIER_LOCAL_DIR': str(self.data), **(environ or {})}), \
                mock.patch.object(S.D, 'data_dir', return_value=self.data), mock.patch.object(S.subprocess, 'call', fake_call), \
                mock.patch.object(S, 'GlassBox'), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = S.run(step, 'co-05')
        return code, seen[0][0], seen[0][1]

    def test_the_new_steps_run_the_runners_scripts_in_the_geo_toolchain(self):
        for step, script, store in (('walk', 'tools/build_walk.py', 'walk.sqlite3'), ('ingest', 'tools/ingest.py', 'ingest/*.zip'),
                                    ('extend', 'tools/extend_geography.py', 'extend/*.zip'), ('bundle', 'tools/build_bundle_v4.py', 'bundle-out/*.zip'),
                                    ('density', 'tools/build_density.py', 'density.json')):
            self.assertEqual((S.STEPS[step].script, S.STEPS[step].store, S.STEPS[step].image), (script, store, 'geo'), step)
            self.assertTrue((REPO / script).is_file(), script)
        self.assertEqual(S.STEPS['bundle'].also, ('bundle-out/*.zip.sha256',))
        self.assertEqual(S.STEPS['bundle'].keep, ('bundle_receipt.json',))                  # the receipt leaves with the zip, but is never compared
        self.assertEqual(S.STEPS['ingest'].keep, ('ingest/v2/*',))                          # the folder extend reads leaves with the zip it was made into
        self.assertEqual(S.STEPS['contacts'].also, ('street.sqlite3',))                      # build_contacts adds an index to the street store
        self.assertEqual([name for name, spec in S.STEPS.items() if spec.walk_salt], ['walk'])

    def test_walk_and_density_run_their_tools_plain_and_walk_seeds_its_salt_only_when_given(self):
        salt = 'ab' * 32
        code, command, env = self.run_recorded('walk', environ={'TERRIER_WALK_SALT': salt})
        self.assertEqual((code, command, env), (0, [sys.executable, str(REPO / 'tools' / 'build_walk.py'), '--district', 'co-05'], None))
        self.assertEqual((self.data / '.walk-salt').read_bytes(), salt.encode('ascii'))
        self.assertFalse((self.root / 'out' / '.demo-key').exists())
        self.run_recorded('walk')
        self.assertFalse((self.data / '.walk-salt').exists())                              # no variable: the tool makes its own
        code, command, env = self.run_recorded('density')
        self.assertEqual(command, [sys.executable, str(REPO / 'tools' / 'build_density.py'), '--district', 'co-05'])

    def test_seed_walk_salt_writes_the_text_once_and_never_replaces_a_salt(self):
        S.seed_walk_salt(self.root, 'AB' * 32)
        self.assertEqual((self.root / '.walk-salt').read_bytes(), b'AB' * 32)
        S.seed_walk_salt(self.root, 'cd' * 32)
        self.assertEqual((self.root / '.walk-salt').read_bytes(), b'AB' * 32)
        for bad in ('ab' * 31, 'ab' * 33, '', 'zz' * 32):
            with self.assertRaises(SystemExit):
                S.seed_walk_salt(self.root / 'elsewhere', bad)
        self.assertFalse((self.root / 'elsewhere').exists())

    def test_ingest_extend_and_bundle_get_the_runners_arguments_and_the_environment_their_tools_read(self):
        csv, geography = self.co['source_csv'], self.co['geography']
        code, command, env = self.run_recorded('ingest')
        self.assertEqual(command, [sys.executable, str(REPO / 'tools' / 'ingest.py'), '--csv', str(csv), '--geography', str(geography),
                                   '--output', '%s/ingest/v2' % self.data, '--receipt', '%s/ingest/ingest_receipt.json' % self.data])
        self.assertEqual(env['CU_DISTRICT'], 'co-05')
        code, command, env = self.run_recorded('extend')
        self.assertEqual(command[2:], ['--csv', str(csv), '--base', '%s/ingest/v2' % self.data, '--geography', str(geography),
                                       '--output', '%s/extend/v3' % self.data])
        self.assertEqual((env['CU_DISTRICT'], env['PYTHONPATH']), ('co-05', str(REPO)))      # extend_geography.py imports `tools` from the repo
        code, command, env = self.run_recorded('bundle', prepare=[('extend/co-cd005-0123456789ab.zip', 'zip')])
        self.assertEqual(command[2:], ['--base', str(self.data / 'extend' / 'co-cd005-0123456789ab.zip'), '--out-dir', '%s/bundle-out' % self.data,
                                       '--receipt', '%s/bundle_receipt.json' % self.data])
        # the app's validator imports Django settings: a scratch data folder and a throwaway key, never a real .local or .secret
        self.assertEqual((env['CU_DISTRICT'], env['CU_DATA_DIR'], env['CU_SECRET_KEY']),
                         ('co-05', '%s/.cu-validate' % self.data, 'terrier-build-validate-only'))
        with self.assertRaises(SystemExit) as stop:
            self.run_recorded('bundle')                                                    # no extend output to build on
        self.assertIn('extend', str(stop.exception))
        with self.assertRaises(SystemExit) as stop:
            self.run_recorded('bundle', prepare=[('extend/a.zip', 'z'), ('extend/b.zip', 'z')])      # two zips: which is the base?
        self.assertIn('expected one', str(stop.exception))

    def test_a_name_with_a_star_stands_for_its_files_and_a_store_must_match_exactly_one(self):
        for name in ('a/1.zip', 'a/2.zip', 'a/1.zip.sha256', 'b.txt'):
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text('x', encoding='utf-8')
        self.assertEqual(S.expand(self.root, 'a/*.zip'), ['a/1.zip', 'a/2.zip'])
        self.assertEqual((S.expand(self.root, 'b.txt'), S.expand(self.root, 'c/*.zip')), (['b.txt'], []))
        self.assertTrue(S.written(self.root, 'a/*.zip'))
        self.assertFalse(S.written(self.root, 'a/*.zip', exactly_one=True))                 # two bundles: which one is the store?
        self.assertTrue(S.written(self.root, 'a/*.zip.sha256', exactly_one=True))
        self.assertFalse(S.written(self.root, 'c/*.zip'))
        (self.root / 'a' / '2.zip').write_text('', encoding='utf-8')
        self.assertFalse(S.written(self.root, 'a/*.zip'))                                  # an empty file is not a store

    def test_a_star_store_that_matches_two_files_is_not_completion(self):
        def fake_call(command, **kwargs):
            for name in ('ingest/k1.zip', 'ingest/k2.zip', 'ingest/ingest_receipt.json'):
                (self.data / name).parent.mkdir(parents=True, exist_ok=True)
                (self.data / name).write_text('x', encoding='utf-8')
            return 0

        with mock.patch.dict(os.environ, {'TERRIER_LOCAL_DIR': str(self.data)}), mock.patch.object(S.D, 'data_dir', return_value=self.data), \
                mock.patch.object(S.subprocess, 'call', fake_call), mock.patch.object(S, 'GlassBox'), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(S.run('ingest', 'co-05'), 1)

    def test_export_copies_every_file_a_star_name_matches_and_nothing_outside_the_stage(self):
        stage, out = self.root / 'stage', self.root / 'exported'
        for name in ('ingest/k1.zip', 'ingest/k1.json', 'density.json'):
            (stage / name).parent.mkdir(parents=True, exist_ok=True)
            (stage / name).write_text('x', encoding='utf-8')
        with mock.patch.dict(os.environ, {'TERRIER_LOCAL_DIR': str(stage)}), contextlib.redirect_stdout(io.StringIO()):
            S.export(out, ['ingest/*.zip', 'density.json'])
            self.assertEqual(sorted(json.loads((out / 'export_receipt.json').read_text(encoding='utf-8'))), ['density.json', 'ingest/k1.zip'])
            with self.assertRaises(SystemExit) as stop:
                S.export(out, ['bundle-out/*.zip'])                                       # a pattern that matches nothing is a missing file
            self.assertIn('nothing', str(stop.exception))
            for bad in ('../x/*.zip', '..'):
                with self.assertRaises(SystemExit):
                    S.export(out, [bad])

    def test_what_a_run_must_be_given_is_what_its_steps_read_and_none_of_them_makes(self):
        orr = S.D.get('or-05')
        self.assertEqual(S.step_needs(S.STEPS['walk'], self.co), ['roads.sqlite3', 'street.sqlite3', 'contacts.sqlite3', 'scores.sqlite3'])
        self.assertEqual(S.step_needs(S.STEPS['walk'], orr), ['roads.sqlite3', 'street.sqlite3', 'contacts.sqlite3'])    # people come from the CSV
        self.assertEqual((S.step_needs(S.STEPS['street'], self.co), S.step_needs(S.STEPS['street'], orr)), ([], ['.street-salt']))
        self.assertEqual((S.step_needs(S.STEPS['shown'], self.co), S.step_needs(S.STEPS['shown'], orr)), ([], ['rowfw']))
        self.assertEqual(S.inputs_needed(list(S.STEPS), self.co), [])                         # the whole chain needs nothing from outside
        self.assertEqual(S.inputs_needed(['roads', 'street', 'contacts', 'shown', 'scores'], self.co), [])
        self.assertEqual(S.inputs_needed(['contacts'], self.co), ['street.sqlite3'])
        self.assertEqual(S.inputs_needed(['walk', 'density'], self.co), ['roads.sqlite3', 'street.sqlite3', 'contacts.sqlite3', 'scores.sqlite3'])
        self.assertEqual(S.inputs_needed(['bundle'], self.co), ['area_scores_shown.csv', 'scores.sqlite3', 'extend'])
        self.assertEqual(S.inputs_needed(['ingest', 'extend', 'bundle'], self.co), ['area_scores_shown.csv', 'scores.sqlite3'])
        self.assertEqual(S.inputs_needed(['street', 'contacts'], orr), ['.street-salt'])
        self.assertEqual(S.inputs_needed(['shown', 'scores'], orr), ['rowfw', 'street.sqlite3', 'contacts.sqlite3'])


class ZipAndWalkCompareTests(unittest.TestCase):
    """compute_parity: a zip (byte for byte, else member by member with the variance left out), the walk store's `meta` table, the names
    a run lists, and the way inputs reach A1, A2 and the stage."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def zips(self, a1, a2, b, name='x.zip', stamps=((2026, 10, 1, 0, 0, 0), (2026, 10, 2, 0, 0, 0), (2026, 10, 3, 0, 0, 0))):
        for label, members, stamp in zip(('A1', 'A2', 'B'), (a1, a2, b), stamps):
            make_zip(self.root / label / name, members, stamp=stamp)
        return [self.root / label for label in ('A1', 'A2', 'B')]

    def test_zips_that_agree_byte_for_byte_pass_and_a_difference_in_b_fails(self):
        same = {'manifest.json': {'snapshot': 's'}, 'rows.json': [1, 2]}
        folders = self.zips(same, same, same, stamps=((2026, 10, 1, 0, 0, 0),) * 3)
        ok, detail = P.compare('x.zip', *folders)
        self.assertTrue(ok, detail)
        self.assertIn('byte-identical', detail)
        make_zip(folders[2] / 'x.zip', {**same, 'rows.json': [1, 3]})
        ok, detail = P.compare('x.zip', *folders)
        self.assertFalse(ok)
        self.assertIn('byte for byte', detail)

    def test_zips_stamped_by_their_writer_pass_when_only_the_stamped_fields_differ(self):
        rows = {'rows.json': [1, 2], 'geometry.json': {'a': [1] * 30}}
        folders = self.zips({**rows, 'manifest.json': {'created_at': 't1', 'snapshot': 's'}},
                            {**rows, 'manifest.json': {'created_at': 't2', 'snapshot': 's'}},
                            {**rows, 'manifest.json': {'created_at': 't3', 'snapshot': 's'}})
        ok, detail = P.compare('x.zip', *folders)
        self.assertTrue(ok, detail)
        self.assertEqual(detail, '2 members identical, 1 with run-to-run variance (left out): manifest.json[created_at]')
        # a fact of the manifest that differs in B, a member that differs in B, and a member B lacks all fail
        make_zip(folders[2] / 'x.zip', {**rows, 'manifest.json': {'created_at': 't3', 'snapshot': 'other'}}, stamp=(2026, 10, 3, 0, 0, 0))
        self.assertEqual(P.compare('x.zip', *folders), (False, 'B differs from the Windows build in member(s): manifest.json'))
        make_zip(folders[2] / 'x.zip', {**rows, 'rows.json': [1, 9], 'manifest.json': {'created_at': 't3', 'snapshot': 's'}}, stamp=(2026, 10, 3, 0, 0, 0))
        self.assertEqual(P.compare('x.zip', *folders), (False, 'B differs from the Windows build in member(s): rows.json'))
        make_zip(folders[2] / 'x.zip', {'rows.json': [1, 2], 'manifest.json': {'created_at': 't3', 'snapshot': 's'}}, stamp=(2026, 10, 3, 0, 0, 0))
        self.assertIn('names of its members', P.compare('x.zip', *folders)[1])

    def test_a_member_that_differs_between_the_windows_builds_and_is_not_a_json_object_makes_the_reference_unstable(self):
        folders = self.zips({'rows.json': [1, 2]}, {'rows.json': [1, 3]}, {'rows.json': [1, 2]})
        ok, detail = P.compare('x.zip', *folders)
        self.assertFalse(ok)
        self.assertIn('reference is not stable', detail)
        self.assertIn('rows.json', detail)

    def test_change_one_member_flips_one_bit_of_the_largest_member_but_never_the_manifest_and_the_comparison_sees_it(self):
        members = {'manifest.json': {'created_at': 't', 'snapshot': 's'}, 'rows.json': [1, 2], 'geometry.json': {'a': [1] * 200}}
        folders = self.zips(members, members, members)
        control = self.root / 'NC'
        control.mkdir()
        shutil.copyfile(folders[2] / 'x.zip', control / 'x.zip')
        before = zipfile.ZipFile(control / 'x.zip')
        original = {name: before.read(name) for name in before.namelist()}
        before.close()
        member = P.change_one_member(control / 'x.zip')
        self.assertEqual(member, 'geometry.json')
        with zipfile.ZipFile(control / 'x.zip') as after:
            self.assertEqual(after.namelist(), list(original))
            changed = [name for name in original if after.read(name) != original[name]]
            self.assertEqual(changed, ['geometry.json'])
            self.assertEqual(sum(bin(a ^ b).count('1') for a, b in zip(after.read('geometry.json'), original['geometry.json'])), 1)
        ok, detail = P.compare_zip('x.zip', folders[0], folders[1], control)
        self.assertFalse(ok)
        self.assertIn('geometry.json', detail)

    # the walk store: meta carries built_utc and the qa document with its timings and the Windows-only peak memory

    def walk_folders(self, b_people=5, b_qa=None):
        qa = lambda stamp, peak: {'rows': 5, 'timing': {'total': stamp}, 'peak_memory_mb': peak}      # noqa: E731
        for label in ('A1', 'A2', 'B'):
            (self.root / label / 'walk.sqlite3').unlink(missing_ok=True)
        make_walk_store(self.root / 'A1' / 'walk.sqlite3', 5, built='t1', qa=qa('s1', 1234))
        make_walk_store(self.root / 'A2' / 'walk.sqlite3', 5, built='t2', qa=qa('s2', 1234))          # the same peak twice by chance
        make_walk_store(self.root / 'B' / 'walk.sqlite3', b_people, built='t3', qa=b_qa if b_qa is not None else qa('s3', None))
        return [self.root / label for label in ('A1', 'A2', 'B')]

    def test_the_walk_meta_table_leaves_out_stamps_timings_and_the_peak_memory_and_nothing_beside_them(self):
        ok, detail = P.compare('walk.sqlite3', *self.walk_folders())
        self.assertTrue(ok, detail)
        self.assertIn('variance (left out): meta[built_utc, qa.peak_memory_mb, qa.timing]', detail)
        ok, detail = P.compare('walk.sqlite3', *self.walk_folders(b_people=6))              # a fact beside the stamps differs in B
        self.assertFalse(ok)
        self.assertIn('table(s): ', detail)
        self.assertEqual(P.differing_tables(detail), ['person', 'stop'])
        ok, detail = P.compare('walk.sqlite3', *self.walk_folders(b_qa={'rows': 4, 'timing': {'total': 's3'}, 'peak_memory_mb': None}))
        self.assertEqual((ok, detail), (False, 'B differs from the Windows build in table(s): meta[qa]'))

    def test_the_negative_controls_of_the_walk_store_skip_its_stamp_table_and_are_caught(self):
        folders = self.walk_folders()
        target = self.root / 'NC' / 'walk.sqlite3'
        target.parent.mkdir()
        shutil.copyfile(folders[2] / 'walk.sqlite3', target)
        skip = P.noise_tables('walk.sqlite3', folders[0], folders[1])
        self.assertEqual(skip, {'meta'})
        table = P.remove_one_row(target, skip)
        self.assertEqual(table, 'stop')                                                    # person is WITHOUT ROWID
        ok, detail = P.compare_store('walk.sqlite3', folders[0], folders[1], target.parent)
        self.assertTrue(P.control_detected(ok, detail, table))

    # names, inputs

    def test_store_names_lists_each_file_once_and_keep_adds_the_receipts(self):
        co, orr = S.D.get('co-05'), S.D.get('or-05')
        self.assertEqual(P.store_names(co, ['street', 'contacts']), ['street.sqlite3', 'contacts.sqlite3'])      # contacts changes street: one name
        self.assertEqual(P.store_names(co, ['bundle']), ['bundle-out/*.zip', 'bundle-out/*.zip.sha256'])
        self.assertEqual(P.store_names(co, ['bundle'], keep=True), ['bundle-out/*.zip', 'bundle-out/*.zip.sha256', 'bundle_receipt.json'])
        self.assertEqual(P.store_names(orr, ['walk'], extra=['x.csv']), ['or-05/walk.sqlite3', 'or-05/x.csv'])

    def test_expand_names_takes_a_star_name_from_the_folder_and_a_name_that_matches_nothing_is_an_error(self):
        make_zip(self.root / 'ingest' / 'k1.zip', {'a': 1})
        (self.root / 'density.json').write_text('{}', encoding='utf-8')
        self.assertEqual(P.expand_names(self.root, ['ingest/*.zip', 'density.json', 'ingest/*.zip']), ['ingest/k1.zip', 'density.json'])
        with self.assertRaises(SystemExit):
            P.expand_names(self.root, ['bundle-out/*.zip'])

    def test_copy_inputs_copies_files_and_folders_of_the_data_folder_and_stops_on_a_missing_one(self):
        source, target = self.root / 'source', self.root / 'target'
        (source / 'extend').mkdir(parents=True)
        (source / 'extend' / 'k.zip').write_bytes(b'zip')
        (source / 'scores.sqlite3').write_bytes(b'store')
        P.copy_inputs(target, S.D.get('co-05'), ['scores.sqlite3', 'extend'], source)
        self.assertEqual(((target / 'scores.sqlite3').read_bytes(), (target / 'extend' / 'k.zip').read_bytes()), (b'store', b'zip'))
        with self.assertRaises(SystemExit):
            P.copy_inputs(target, S.D.get('co-05'), ['street.sqlite3'], source)
        (source / 'or-05').mkdir()
        (source / 'or-05' / 'roads.sqlite3').write_bytes(b'r')
        P.copy_inputs(self.root / 'target2', S.D.get('or-05'), ['roads.sqlite3'], source)          # another district: its folder below the run's
        self.assertEqual((self.root / 'target2' / 'or-05' / 'roads.sqlite3').read_bytes(), b'r')

    def test_link_inputs_hard_links_files_where_the_disk_allows_it_and_copies_otherwise(self):
        source = self.root / 'source'
        source.mkdir()
        (source / 'scores.sqlite3').write_bytes(b'store')
        P.copy_inputs(self.root / 'a', S.D.get('co-05'), ['scores.sqlite3'], source, link=True)
        self.assertEqual(((self.root / 'a' / 'scores.sqlite3').read_bytes(), (source / 'scores.sqlite3').stat().st_nlink), (b'store', 2))
        with mock.patch.object(P.os, 'link', side_effect=OSError('another volume')):
            P.copy_inputs(self.root / 'b', S.D.get('co-05'), ['scores.sqlite3'], source, link=True)
        self.assertEqual(((self.root / 'b' / 'scores.sqlite3').read_bytes(), (self.root / 'b' / 'scores.sqlite3').stat().st_nlink), (b'store', 1))
        P.copy_inputs(self.root / 'c', S.D.get('co-05'), ['scores.sqlite3'], source)
        self.assertEqual((self.root / 'c' / 'scores.sqlite3').stat().st_nlink, 1)             # without `link`, always a copy

    def test_seed_stage_streams_the_inputs_as_a_tar_into_one_container_and_hands_them_to_the_build_user(self):
        source = self.root / 'source'
        (source / 'extend').mkdir(parents=True)
        (source / 'extend' / 'k.zip').write_bytes(b'zip')
        (source / 'scores.sqlite3').write_bytes(b'store' * 100)
        sent, launched, runs = io.BytesIO(), [], []

        class Receiver:
            def __init__(self, argv, **kwargs):
                launched.append(argv)
                self.stdin = type('Pipe', (), {'write': sent.write, 'flush': lambda s: None, 'close': lambda s: None, 'closed': False})()

            def wait(self):
                return 0

        def fake_run(argv, **kwargs):
            runs.append(argv)
            return subprocess.CompletedProcess(argv, 0)

        with mock.patch.object(P.subprocess, 'Popen', Receiver), mock.patch.object(P.subprocess, 'run', fake_run), \
                mock.patch.object(P, 'docker_exe', lambda: 'docker'):
            P.seed_stage({'geo': EXPLICIT}, REPO, self.root / 'out', 'B-1', S.D.get('co-05'), source, ['scores.sqlite3', 'extend'])
            P.seed_stage({'geo': EXPLICIT}, REPO, self.root / 'out', 'B-1', S.D.get('or-05'), source / 'or-05', [])
        (argv, _other) = launched
        self.assertEqual(argv[:2], ['docker', 'run'])
        self.assertIn('-i', argv)
        self.assertEqual(argv[argv.index('--network') + 1], 'none')
        self.assertEqual(argv[argv.index('--user') + 1], '0')
        self.assertIn(P.STAGE_VOLUME + ':/stage', argv)
        self.assertEqual(argv[argv.index('--entrypoint') + 1:argv.index('--entrypoint') + 3], ['sh', EXPLICIT])
        self.assertEqual(argv[-1], 'mkdir -p /stage/B-1 && tar xf - -C /stage/B-1 && chown -R 10001:10001 /stage/B-1')
        self.assertIn('/stage/B-1/or-05 && tar xf - -C /stage/B-1/or-05', _other[-1])        # another district: its folder below the run's
        with tarfile.open(fileobj=io.BytesIO(sent.getvalue())) as tar:
            self.assertEqual(sorted(m.name for m in tar.getmembers() if m.isfile())[:2], ['extend/k.zip', 'scores.sqlite3'])
        self.assertEqual([r[1] for r in runs], ['volume', 'volume'])                         # the volume is looked for, found (returncode 0)
        with self.assertRaises(SystemExit):
            P.seed_stage({'geo': EXPLICIT}, REPO, self.root / 'out', 'B-1', S.D.get('co-05'), source, ['street.sqlite3'])

    def test_drop_stage_runs_one_rm_on_the_folder_of_the_run_in_the_stage_volume_and_nothing_else(self):
        runs = []
        with mock.patch.object(P.subprocess, 'run', lambda argv, **kw: runs.append(argv) or subprocess.CompletedProcess(argv, 0)), \
                mock.patch.object(P, 'docker_exe', lambda: 'docker'):
            P.drop_stage({'geo': EXPLICIT}, 'P-20261013T120000Z')
            for bad in ('', 'P-1; rm -rf /', '../x', 'a b', '-rf'):
                with self.assertRaises(ValueError):
                    P.drop_stage({'geo': EXPLICIT}, bad)
        (argv,) = runs
        self.assertEqual(argv[:2], ['docker', 'run'])
        self.assertEqual(argv[argv.index('--network') + 1], 'none')
        self.assertIn(P.STAGE_VOLUME + ':/stage', argv)
        self.assertEqual(argv[-2:], ['-c', 'rm -rf /stage/P-20261013T120000Z'])

    def test_seed_stage_creates_the_stage_volume_through_compose_when_there_is_none(self):
        source = self.root / 'source'
        source.mkdir()
        (source / 'scores.sqlite3').write_bytes(b'store')
        composed = []

        class Receiver:
            def __init__(self, argv, **kwargs):
                self.stdin = io.BytesIO()
                self.stdin.close = lambda: None

            def wait(self):
                return 0

        with mock.patch.object(P.subprocess, 'Popen', Receiver), mock.patch.object(P, 'docker_exe', lambda: 'docker'), \
                mock.patch.object(P.subprocess, 'run', lambda argv, **kw: subprocess.CompletedProcess(argv, 1)), \
                mock.patch.object(P, 'compose', lambda *a, **k: composed.append(a[4:])):
            P.seed_stage({'score': SCORE_EXPLICIT}, REPO, self.root / 'out', 'B-1', S.D.get('co-05'), source, ['scores.sqlite3'])
        self.assertEqual(composed, [('run', '--rm', 'terrier-score', 'fingerprint')])        # compose makes it, so it carries compose's labels


if __name__ == '__main__':
    unittest.main()
