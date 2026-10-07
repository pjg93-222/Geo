"""Colorado (CO) baseline: the gate every later Terrier phase has to keep passing (multi-district build, Phase 0).

    <python> tools/co_baseline.py record [--port 8769] [--runs 2]    write tests/fixtures/co_baseline.json
    <python> tools/co_baseline.py check  [--port 8769]               recompute everything, diff against the fixture

What it records (stdlib only; the server it starts needs the repo's own Python, see --python):

  stores     per table of contacts, scores, street, walk, roads and accounts (.local/*.sqlite3): row count and the
             sha256 of the rows dumped in a sorted, deterministic order (primary key, else every column), plus the
             sha256 of the schema. Never the raw file bytes. The stores are opened read-only.
  files      sha256 of .local/.walk-salt, of every file of the snapshots .local/snapshots/co-cd005-* (the derived/
             folder is a rebuildable cache and is listed apart), and of the row-framework files the bundle is built
             from (the three rowfw parquet files with their manifests, the frozen config/plan/criteria files, the area
             table and the suppressed-id list). The frozen three are checked against tools.area_layers.FROZEN_SHA256.
  endpoints  golden answers (status, content type, byte count, sha256 of the body) of a fixed list of GET endpoints,
             from a non-live server started from the current HEAD on a spare port. Never port 8767.

Personal data: the fixture holds hashes, counts, endpoint templates and normalisation notes only. The raw golden bodies
(names, phones) are kept under .local/baseline-co/golden/ (gitignored) and used only to say WHERE a mismatch is, as JSON
paths, never as values.

Write safety: reads of the real .local are read-only, and the store hashes are taken before and after the run and must
agree. The spare server runs on a COPY of .local (CU_DATA_DIR), because v3_cache writes derived/ files into a snapshot
folder on first use and the accounts store is a read-write Django database. The copy is verified byte for byte first.

Volatile fields (dates, ids that differ per request) are normalised, and ONLY those, through NORMALISE below; each is
written to the fixture's "normalised" list. `record` runs the endpoint pass twice, each against a freshly started server
process, and refuses to write the fixture when the two passes differ.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOCAL = REPO / '.local'
FIXTURE = REPO / 'tests' / 'fixtures' / 'co_baseline.json'
BODY_DIR = LOCAL / 'baseline-co' / 'golden'
ROWFW = REPO.parent / 'cu-geography' / '.local' / 'row-framework-20260930' / 'build' / 'rowfw-1.0.0'
SCHEMA = 'co-baseline-1'
LIVE_PORT = 8767
SPARE_PORTS = (8769, 8771)
STORES = ('contacts', 'scores', 'street', 'walk', 'roads', 'accounts')
COPIED_FILES = ('.walk-salt', '.secret', '.demo-key', 'credit-quality.json') + tuple(s + '.sqlite3' for s in STORES)
SNAPSHOT_DIR = re.compile(r'co-cd005-[a-f0-9]{12}')
PARTS = ('stores', 'files', 'api')
ROWFW_FILES = (
    'row_scores_rowfw-1.0.0.parquet', 'household_scores_rowfw-1.0.0.parquet', 'profiles_rowfw-1.0.0.parquet',
    'row_scores_rowfw-1.0.0.manifest.json', 'household_scores_rowfw-1.0.0.manifest.json',
    'profiles_rowfw-1.0.0.manifest.json',
    'framework_config.json', 'PLAN_rowfw-1.0.0.md', 'criteria_rowfw-1.0.0.json',
    'area_scores_rowfw-1.0.0.csv', 'summaries/area_suppressed_ids.csv')
FROZEN_FILES = {'framework_config_sha256': 'framework_config.json', 'plan_sha256': 'PLAN_rowfw-1.0.0.md',
                'criteria_sha256': 'criteria_rowfw-1.0.0.json'}

# Fixed inputs of the golden walk lists (street ids of walk.sqlite3 `street`; no personal data): Abrams Dr (one street,
# both sides); Artistic Cir even + Bunk House Ln odd + Oro Blanco Dr both, inside one precinct; Adams Dr + Allendale Dr +
# Ambassador Dr. Chosen from combo counts: 126 to 529 voters each, so every list builds well under the 1,500-voter cap.
WALK_A = 'streets=39:both'
WALK_B = 'streets=377:even,1236:odd,6435:both&precinct=5102221155'
WALK_C = 'streets=58:both,135:both,195:both'
BBOX = '-104.830,38.825,-104.800,38.850'        # central Colorado Springs, 0.03 x 0.025 degrees (the cap is 0.06)

# (id, GET path template, capture). Templates take {snapshot}, {person_id} and {household_id}, filled from earlier answers;
# the fixture keeps the template, so no personal id is written into it.
ENDPOINTS = (
    ('health_terrier', '/_terrier/health'),
    ('health_api', '/api/health'),
    ('meta', '/api/v3/meta'),
    ('method_snapshots', '/api/snapshots'),
    ('workspace', '/api/v3/workspace'),
    ('criteria_json', '/api/criteria'),
    ('criteria_csv', '/api/criteria?format=csv'),
    ('areas_registration_block', '/api/v3/areas/{snapshot}/registration/block.json'),
    ('areas_registration_precinct', '/api/v3/areas/{snapshot}/registration/precinct.json'),
    ('areas_registration_tract', '/api/v3/areas/{snapshot}/registration/tract.json'),
    ('areas_registration_zip', '/api/v3/areas/{snapshot}/registration/zip.json'),
    ('areas_residence_tract', '/api/v3/areas/{snapshot}/residence/tract.json'),
    ('areas_residence_zip', '/api/v3/areas/{snapshot}/residence/zip.json'),
    ('geometry_block_coarse', '/api/v3/geometry/{snapshot}/block/coarse.json'),
    ('geometry_block_fine', '/api/v3/geometry/{snapshot}/block/fine.json'),
    ('geometry_precinct_coarse', '/api/v3/geometry/{snapshot}/precinct/coarse.json'),
    ('geometry_precinct_fine', '/api/v3/geometry/{snapshot}/precinct/fine.json'),
    ('geometry_tract_coarse', '/api/v3/geometry/{snapshot}/tract/coarse.json'),
    ('geometry_tract_fine', '/api/v3/geometry/{snapshot}/tract/fine.json'),
    ('geometry_zip_coarse', '/api/v3/geometry/{snapshot}/zip/coarse.json'),
    ('geometry_zip_fine', '/api/v3/geometry/{snapshot}/zip/fine.json'),
    ('geometry_district', '/api/geometry?level=district'),
    ('people_first_page', '/api/v3/people'),
    ('people_filtered_page', '/api/v3/people?reg=D&grade=AB&sort=cua'),
    ('people_within_page', '/api/v3/people?reg=U&sort=within'),
    ('people_cursor_page', '/api/v3/people?after=50'),
    ('person_profile', '/api/v3/person?id={person_id}'),
    ('street_view', '/api/v3/street?basis=registration&bbox=' + BBOX + '&zoom=16'),
    ('household_popover', '/api/v3/household?id={household_id}&basis=registration'),
    ('roads_view', '/api/roads?bbox=-104.83,38.82,-104.80,38.85&zoom=14'),
    ('contacts_first_page', '/api/v3/contacts'),
    ('contacts_filtered_page', '/api/v3/contacts?q=Maple&channel=email'),
    ('contacts_export_csv', '/api/v3/contacts/export?q=Maple&channel=email'),
    ('summaries_count_json', '/api/summaries?basis=registration&level=zcta&metric=count&scale=raw'),
    ('summaries_index_csv', '/api/summaries?basis=registration&level=zcta&metric=area_participation&scale=raw&format=csv'),
    ('walk_options_precinct', '/api/v3/walk/options?level=precinct'),
    ('walk_options_senate', '/api/v3/walk/options?level=ss'),
    ('walk_streets_search', '/api/v3/walk/streets?q=Abrams'),
    ('walk_streets_precinct', '/api/v3/walk/streets?precinct=5102221155'),
    ('walk_overview_precinct', '/api/v3/walk/overview?precinct=5102221155'),
    ('walk_list_a', '/api/v3/walk/list?' + WALK_A),
    ('walk_list_b', '/api/v3/walk/list?' + WALK_B),
    ('walk_list_c', '/api/v3/walk/list?' + WALK_C),
)

# The only fields normalised (dropped before hashing a JSON body): {endpoint id prefix: [(dotted key path, why)]}.
# `record` fails when two passes differ, so a new volatile field is found there and added here, never guessed.
NORMALISE = {
    'walk_list_': [('generated_on', 'the day the list was built (date.today() in walk_views.plan_payload)')],
}


def log(message):
    print('[%s] %s' % (datetime.datetime.now().strftime('%H:%M:%S'), message), file=sys.stderr, flush=True)


# ---- hashing -------------------------------------------------------------------------------------------------------

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def open_readonly(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=30)
    connection.execute('PRAGMA query_only=ON')
    return connection


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def table_digest(connection, name, virtual=False):
    """{rows, columns, order, sha256} of one table. Rows are read in primary-key order (every column when an ordinary
    table has no primary key; rowid for a virtual FTS5/R-tree table, which cannot be indexed and is slow to sort) and
    hashed as repr(tuple), which keeps int 1, float 1.0, text and bytes apart."""
    info = connection.execute('PRAGMA table_info(%s)' % quote(name)).fetchall()
    columns = [row[1] for row in info]
    key = [row[1] for row in sorted((r for r in info if r[5]), key=lambda r: r[5])]
    if virtual:
        order, clause = 'rowid', 'rowid'
    elif key:
        order, clause = 'primary key', ', '.join(quote(c) for c in key)
    else:
        order, clause = 'all columns', ', '.join(str(i + 1) for i in range(len(columns)))
    digest, rows, batch = hashlib.sha256(), 0, []
    for row in connection.execute('SELECT * FROM %s ORDER BY %s' % (quote(name), clause)):
        batch.append(repr(row))
        rows += 1
        if len(batch) >= 5000:
            digest.update(('\n'.join(batch) + '\n').encode('utf-8', 'surrogatepass'))
            batch = []
    if batch:
        digest.update(('\n'.join(batch) + '\n').encode('utf-8', 'surrogatepass'))
    return {'rows': rows, 'columns': len(columns), 'order': order, 'sha256': digest.hexdigest()}


def store_digest(path):
    """{schema_sha256, objects, user_version, tables: {name: table_digest}} of one SQLite file, read-only."""
    connection = open_readonly(path)
    try:
        master = connection.execute(
            'SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name').fetchall()
        tables = {}
        for kind, name, _tbl, sql in master:
            if kind == 'table':
                virtual = (sql or '').upper().startswith('CREATE VIRTUAL')
                tables[name] = table_digest(connection, name, virtual)
                if virtual:
                    tables[name]['virtual'] = True
        return {'schema_sha256': sha256_text(repr(master)), 'objects': len(master),
                'user_version': connection.execute('PRAGMA user_version').fetchone()[0], 'tables': tables}
    finally:
        connection.close()


def store_hashes(data_dir):
    result = {}
    for name in STORES:
        started = time.time()
        result[name] = store_digest(Path(data_dir) / (name + '.sqlite3'))
        log('store %-9s %2d tables, %9d rows, %5.1fs' % (name, len(result[name]['tables']),
            sum(t['rows'] for t in result[name]['tables'].values()), time.time() - started))
    return result


def tree_hashes(root):
    """{posix path relative to root: sha256} of every file under root."""
    root = Path(root)
    return {path.relative_to(root).as_posix(): sha256_file(path)
            for path in sorted(p for p in root.rglob('*') if p.is_file())}


def file_hashes(data_dir, rowfw=ROWFW):
    data_dir = Path(data_dir)
    result = {'walk_salt': {'sha256': sha256_file(data_dir / '.walk-salt')}, 'snapshots': {}, 'rowfw': {}}
    snapshots = data_dir / 'snapshots'
    for folder in sorted(p for p in snapshots.iterdir() if p.is_dir() and SNAPSHOT_DIR.fullmatch(p.name)):
        files = tree_hashes(folder)
        result['snapshots'][folder.name] = {
            'core': {k: v for k, v in files.items() if not k.startswith('derived/')},
            'derived_cache': {k: v for k, v in files.items() if k.startswith('derived/')}}
    for name in ROWFW_FILES:
        result['rowfw'][name] = sha256_file(Path(rowfw) / name)
    return result


def frozen_check(rowfw_hashes):
    """Problems (a list of strings) with the frozen row-framework hashes against tools.area_layers.FROZEN_SHA256."""
    sys.path.insert(0, str(REPO))
    from tools import area_layers
    return ['rowfw %s is %s, tools/area_layers.py expects %s' % (name, rowfw_hashes[FROZEN_FILES[key]], expected)
            for key, expected in area_layers.FROZEN_SHA256.items()
            if rowfw_hashes[FROZEN_FILES[key]] != expected]


# ---- JSON helpers --------------------------------------------------------------------------------------------------

def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def drop_path(obj, dotted):
    parts = dotted.split('.')
    for part in parts[:-1]:
        if not isinstance(obj, dict) or part not in obj:
            return False
        obj = obj[part]
    if isinstance(obj, dict) and parts[-1] in obj:
        del obj[parts[-1]]
        return True
    return False


def volatile_fields(endpoint_id):
    return [item for prefix, items in NORMALISE.items() if endpoint_id.startswith(prefix) for item in items]


def body_digest(endpoint_id, body, content_type):
    """sha256 of the body; JSON bodies of an endpoint with volatile fields are parsed, stripped of exactly those fields
    and re-serialised canonically first. Returns (hex digest, list of fields actually removed)."""
    fields = volatile_fields(endpoint_id)
    if fields and 'json' in (content_type or ''):
        obj = json.loads(body.decode('utf-8'))
        removed = [path for path, _why in fields if drop_path(obj, path)]
        return hashlib.sha256(canonical(obj)).hexdigest(), removed
    return hashlib.sha256(body).hexdigest(), []


def json_paths_differing(left, right, limit=12):
    """Up to `limit` JSON paths where two bodies differ (never values)."""
    found = []

    def walk(a, b, path):
        if len(found) >= limit:
            return
        if type(a) is not type(b):
            found.append(path or '$')
        elif isinstance(a, dict):
            for key in sorted(set(a) | set(b)):
                if key not in a or key not in b:
                    found.append('%s.%s' % (path, key))
                else:
                    walk(a[key], b[key], '%s.%s' % (path, key))
        elif isinstance(a, list):
            if len(a) != len(b):
                found.append('%s[len %d vs %d]' % (path, len(a), len(b)))
            for index, (x, y) in enumerate(zip(a, b)):
                walk(x, y, '%s[%d]' % (path, index))
        elif a != b:
            found.append(path or '$')

    try:
        walk(json.loads(left.decode('utf-8')), json.loads(right.decode('utf-8')), '')
    except (ValueError, UnicodeDecodeError):
        return ['(not JSON: %d bytes vs %d bytes)' % (len(left), len(right))]
    return found[:limit]


def first_string(obj, pattern):
    """The first string anywhere in a parsed JSON value that matches the regular expression, else None."""
    if isinstance(obj, str):
        return obj if re.fullmatch(pattern, obj) else None
    items = obj.values() if isinstance(obj, dict) else obj if isinstance(obj, list) else ()
    for item in items:
        found = first_string(item, pattern)
        if found is not None:
            return found
    return None


# ---- ports, processes ----------------------------------------------------------------------------------------------

def listener_pid(port):
    """Process id listening on 127.0.0.1:port (read from `netstat`, which touches nothing), else None."""
    out = subprocess.run(['netstat', '-ano', '-p', 'TCP'], capture_output=True, text=True, check=False).stdout
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == 'TCP' and parts[3] == 'LISTENING' and parts[1].endswith(':%d' % port):
            return int(parts[4])
    return None


def choose_port(requested):
    if requested == LIVE_PORT:
        raise SystemExit('Refusing port %d: that is the LIVE server.' % LIVE_PORT)
    for port in ([requested] if requested else SPARE_PORTS):
        if listener_pid(port) is None:
            return port
        log('port %d is busy (pid %s)' % (port, listener_pid(port)))
    raise SystemExit('No spare port is free (%s).' % ', '.join(str(p) for p in ([requested] if requested else SPARE_PORTS)))


def server_python(requested):
    if requested:
        return str(requested)
    if os.environ.get('CU_PYTHON'):
        return os.environ['CU_PYTHON']
    venv = REPO.parent.parent / 'work' / '.venv' / 'Scripts' / 'python.exe'
    return str(venv) if venv.exists() else sys.executable


def make_copy(work_dir):
    """A verified copy of the files the server reads: .local without backups, walk-backups, pycache and server.pid."""
    folder = Path(tempfile.mkdtemp(prefix='co-baseline-', dir=str(work_dir)))
    started = time.time()
    for name in COPIED_FILES:
        source = LOCAL / name
        if source.exists():
            shutil.copyfile(source, folder / name)
    shutil.copytree(LOCAL / 'snapshots', folder / 'snapshots')
    for name in COPIED_FILES:
        source = LOCAL / name
        if source.exists() and sha256_file(source) != sha256_file(folder / name):
            raise SystemExit('The copy of %s differs from the original (changed while copying?). Run again.' % name)
    log('copied .local to %s in %.0fs, files verified byte for byte' % (folder, time.time() - started))
    return folder


def remove_copy(folder, work_dir):
    folder = Path(folder).resolve()
    if folder.name.startswith('co-baseline-') and folder.parent == Path(work_dir).resolve() and folder != LOCAL.resolve():
        shutil.rmtree(folder, ignore_errors=True)


class ServerFault(OSError):
    """An answer of 5xx. The golden answers are all 200, so this is the server's environment failing, not a changed
    answer (seen with work/.venv: 'DLL load failed while importing lib: The handle is invalid' inside a request)."""


class StartFailed(Exception):
    """The spare server process died before it answered (seen on the work/.venv launcher as exit 0xC000010A, no output)."""


STARTUP_RETRIES = []        # one line per retried start; written to the fixture notes


def start_with_retry(make_server, tries=3):
    """A started Server. A start that dies early is retried up to `tries` times in all, each retry noted."""
    for attempt in range(1, tries + 1):
        server = make_server(attempt)
        try:
            server.start()
            return server
        except StartFailed as problem:
            server.stop()
            note = 'spare server start %d of %d failed: %s' % (attempt, tries, problem)
            STARTUP_RETRIES.append(note)
            log(note)
            if attempt == tries:
                raise SystemExit(note)
            time.sleep(2.0)


class Server:
    """`manage.py serve` on a spare port against a data directory, from this checkout. Stops only its own process tree."""

    def __init__(self, python, port, data_dir, tag):
        self.python, self.port, self.data_dir, self.tag = python, port, Path(data_dir), tag
        self.process = None
        self.serving_pid = None

    def start(self, timeout=240):
        if self.port == LIVE_PORT:
            raise SystemExit('Refusing port %d: that is the LIVE server.' % LIVE_PORT)
        env = dict(os.environ, CU_DATA_DIR=str(self.data_dir), CU_COOKIE_PREFIX='cu_baseline_', CU_DEMO='0',
                   PYTHONPYCACHEPREFIX=str(self.data_dir / 'pycache'))
        for name in ('CU_PORT', 'CU_DEPLOYMENT', 'CU_LOCAL_ADMIN'):
            env.pop(name, None)
        out = open(self.data_dir.parent / ('server-%d-%s.out.log' % (self.port, self.tag)), 'wb')
        err = open(self.data_dir.parent / ('server-%d-%s.err.log' % (self.port, self.tag)), 'wb')
        flags = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0) | getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        self.process = subprocess.Popen([self.python, str(REPO / 'manage.py'), 'serve', '--port', str(self.port)],
                                        cwd=str(REPO), env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                        creationflags=flags)
        log('spare server started: pid %d, port %d, data %s' % (self.process.pid, self.port, self.data_dir))
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise StartFailed('exited early with code %s (%s); see %s' % (
                    self.process.returncode, hex(self.process.returncode & 0xFFFFFFFF), err.name))
            try:
                if fetch(self.port, '/_terrier/health')[0] == 200:
                    self.serving_pid = listener_pid(self.port)
                    log('spare server ready (serving pid %s)' % self.serving_pid)
                    return
            except OSError:
                pass
            time.sleep(1.0)
        raise SystemExit('The spare server was not ready after %ds; see %s' % (timeout, err.name))

    def stop(self):
        if self.process is None:
            return
        pid = self.process.pid
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True, check=False)
        else:
            self.process.terminate()
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.process.kill()
        for _ in range(30):
            if listener_pid(self.port) is None:
                break
            time.sleep(0.5)
        log('spare server stopped (pid %d, port %d now %s)' % (
            pid, self.port, 'free' if listener_pid(self.port) is None else 'STILL BUSY'))
        self.process = None


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch(port, path, timeout=900):
    """(status, content type, body bytes) of one GET on the spare port. Identity encoding, so bodies are not gzipped."""
    request = urllib.request.Request('http://127.0.0.1:%d%s' % (port, path), method='GET',
                                     headers={'Accept-Encoding': 'identity', 'Accept': '*/*'})
    try:
        with _OPENER.open(request, timeout=timeout) as answer:
            return answer.status, answer.headers.get('Content-Type', ''), answer.read()
    except urllib.error.HTTPError as problem:
        return problem.code, problem.headers.get('Content-Type', ''), problem.read()


# ---- the endpoint pass ---------------------------------------------------------------------------------------------

def endpoint_pass(port):
    """({id: entry}, {id: body bytes}) of every endpoint in ENDPOINTS, in order."""
    context, entries, bodies = {}, {}, {}
    for endpoint_id, template in ENDPOINTS:
        wanted = re.findall(r'\{(\w+)\}', template)
        if any(name not in context for name in wanted):
            entries[endpoint_id] = {'method': 'GET', 'path': template, 'skipped': 'its id was not found in an earlier answer'}
            log('  %-30s SKIPPED (no id from an earlier answer)' % endpoint_id)
            continue
        path = template.format(**context)
        started = time.time()
        status, content_type, body = fetch(port, path)
        digest, removed = body_digest(endpoint_id, body, content_type)
        entries[endpoint_id] = {'method': 'GET', 'path': template, 'status': status,
                                'content_type': content_type,
                                'bytes': len(body), 'sha256': digest}
        if removed:
            entries[endpoint_id]['normalised'] = removed
        bodies[endpoint_id] = body
        log('  %-30s %d %9d bytes %5.1fs' % (endpoint_id, status, len(body), time.time() - started))
        if status == 200 and 'json' in content_type:
            parsed = json.loads(body.decode('utf-8'))
            if endpoint_id == 'meta':
                context['snapshot'] = parsed['snapshot']
            elif endpoint_id == 'people_first_page':
                found = first_string(parsed, r'p_[0-9a-f]{64}')
                if found:
                    context['person_id'] = found
            elif endpoint_id == 'street_view':
                found = first_string(parsed, r'h_[0-9a-f]{64}')
                if found:
                    context['household_id'] = found
    faults = sorted(i for i, e in entries.items() if e.get('status', 0) >= 500)
    if faults:
        raise ServerFault('5xx from the spare server for %s' % ', '.join(faults))
    return entries, bodies, context.get('snapshot')


def can_keep_bodies(folder):
    result = subprocess.run(['git', 'check-ignore', '-q', str(folder)], cwd=str(REPO), check=False)
    return result.returncode == 0


def keep_bodies(bodies, folder):
    if not can_keep_bodies(folder):
        log('NOT keeping raw golden bodies: %s is not gitignored' % folder)
        return False
    folder.mkdir(parents=True, exist_ok=True)
    for endpoint_id, body in bodies.items():
        (folder / (endpoint_id + '.body')).write_bytes(body)
    log('raw golden bodies (personal data, gitignored) kept in %s' % folder)
    return True


# ---- comparison ----------------------------------------------------------------------------------------------------

def compare(expected, actual, path, out):
    """Readable differences between two parsed fixture sections, appended to `out`."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            if key not in actual:
                out.append('MISSING   %s.%s' % (path, key))
            elif key not in expected:
                out.append('NEW       %s.%s' % (path, key))
            else:
                compare(expected[key], actual[key], '%s.%s' % (path, key), out)
    elif expected != actual:
        out.append('DIFFERENT %s: recorded %s, now %s' % (path, short(expected), short(actual)))


def short(value):
    text = str(value)
    return text if len(text) <= 24 else text[:12] + '...' + text[-6:]


def compare_endpoints(expected, actual, bodies_now, golden_dir, out):
    for endpoint_id in sorted(set(expected) | set(actual)):
        before, now = expected.get(endpoint_id), actual.get(endpoint_id)
        if before is None or now is None:
            out.append('%s     endpoints.%s' % ('NEW      ' if before is None else 'MISSING  ', endpoint_id))
            continue
        problems = []
        for key in ('status', 'content_type', 'sha256', 'skipped') + (() if 'normalised' in before else ('bytes',)):
            if before.get(key) != now.get(key):
                problems.append('%s %s -> %s' % (key, short(before.get(key)), short(now.get(key))))
        if not problems:
            continue
        line = 'DIFFERENT endpoints.%s: %s' % (endpoint_id, '; '.join(problems))
        golden = golden_dir / (endpoint_id + '.body')
        if endpoint_id in bodies_now and golden.exists():
            where = json_paths_differing(golden.read_bytes(), bodies_now[endpoint_id])
            line += '\n            differing JSON paths: ' + (', '.join(where) if where else '(none; only the bytes differ)')
        out.append(line)


def normalised_list():
    return [{'endpoint_prefix': prefix, 'field': field, 'reason': why}
            for prefix, items in NORMALISE.items() for field, why in items]


def git_head():
    result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=str(REPO), capture_output=True, text=True, check=False)
    return result.stdout.strip()


# ---- main flow -----------------------------------------------------------------------------------------------------

def run(mode, args):
    parts = [p for p in args.parts.split(',') if p]
    if any(p not in PARTS for p in parts):
        raise SystemExit('--parts takes a comma list of %s' % ', '.join(PARTS))
    runs = args.runs if mode == 'record' else 1
    work_dir = Path(args.work_dir or tempfile.gettempdir()).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    live_before = listener_pid(LIVE_PORT)
    log('live server on %d: pid %s (never contacted)' % (LIVE_PORT, live_before))
    problems, current = [], {'schema': SCHEMA}

    if 'stores' in parts:
        log('hashing the stores (before)')
        current['stores'] = store_hashes(LOCAL)
    if 'files' in parts:
        log('hashing files')
        current['files'] = file_hashes(LOCAL)
        problems += frozen_check(current['files']['rowfw'])

    bodies = {}
    spare = None
    if 'api' in parts:
        spare = choose_port(args.port)
        python = server_python(args.python)
        log('server python: %s; spare port: %d' % (python, spare))
        data_dir = Path(args.data_dir) if args.data_dir else make_copy(work_dir)
        passes = []
        try:
            for number in range(1, runs + 1):
                for attempt in range(1, 4):         # a server that dies mid-pass is started again, up to 3 passes in all
                    server = None
                    try:
                        server = start_with_retry(lambda tries: Server(python, spare, data_dir, 'run%d-pass%d-try%d' % (number, attempt, tries)))
                        log('endpoint pass %d of %d (attempt %d)' % (number, runs, attempt))
                        passes.append((endpoint_pass(spare), server.process.pid, server.serving_pid))
                        break
                    except OSError as problem:      # connection refused / reset (the server died) or a 5xx (ServerFault)
                        note = 'pass %d attempt %d lost its server: %s' % (number, attempt, problem)
                        STARTUP_RETRIES.append(note)
                        log(note)
                        if attempt == 3:
                            raise SystemExit(note)
                    finally:
                        if server is not None:
                            server.stop()
        finally:
            if not args.data_dir and not args.keep_copy:
                remove_copy(data_dir, work_dir)
        (first, first_bodies, snapshot), _pid, _serving = passes[0]
        current['snapshot'] = snapshot
        current['endpoints'] = first
        bodies = first_bodies
        current['spare_server'] = {'port': spare, 'pids': [{'launcher': p, 'serving': s} for _, p, s in passes]}
        for index in range(1, len(passes)):
            (other, other_bodies, _snap), _p, _s = passes[index]
            same = True
            for endpoint_id in sorted(set(first) | set(other)):
                if first.get(endpoint_id) != other.get(endpoint_id):
                    same = False
                    where = json_paths_differing(first_bodies[endpoint_id], other_bodies[endpoint_id]) \
                        if endpoint_id in first_bodies and endpoint_id in other_bodies else ['(missing)']
                    problems.append('pass 1 and pass %d differ at endpoints.%s: %s' % (
                        index + 1, endpoint_id, ', '.join(where)))
            log('pass 1 and pass %d identical: %s' % (index + 1, 'yes' if same else 'NO'))

    if 'stores' in parts:
        log('hashing the stores (after)')
        after = store_hashes(LOCAL)
        changed = []
        compare(current['stores'], after, 'stores', changed)
        if changed:
            problems += ['the real stores changed during the run: ' + line for line in changed]
        else:
            log('real stores unchanged by the run')
    live_after = listener_pid(LIVE_PORT)
    if live_after != live_before:
        problems.append('the live server on %d changed pid: %s -> %s' % (LIVE_PORT, live_before, live_after))
    log('live server on %d: pid %s (unchanged: %s)' % (LIVE_PORT, live_after, 'yes' if live_after == live_before else 'NO'))

    if mode == 'record':
        if problems:
            report(problems, 'record refused; no fixture written')
            return 1
        fixture = {**current, 'recorded_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
                   'repo_head': git_head(), 'normalised': normalised_list(),
                   'notes': ['Hashes, counts and templates only; raw bodies stay under .local/baseline-co/golden (gitignored).',
                             'accounts.sqlite3 is read-write user state (saved walk lists, results): a legitimate edit shows as a diff.',
                             'derived_cache files of a snapshot are rebuilt by the server on first use.',
                             'spare-server start retries: ' + ('; '.join(STARTUP_RETRIES) or 'none needed'),
                             'The walk-list PDF is drawn in the browser (frontend/src/v3/lib/walkPdf.ts); the server has no PDF '
                             'endpoint, so the walk_list_* bodies (its only input) are recorded, not PDF bytes.']}
        target = Path(args.out) if args.out else FIXTURE
        if len(parts) < len(PARTS) and not args.out:
            raise SystemExit('The fixture is only written when all parts are recorded; got --parts %s' % args.parts)
        if bodies:
            keep_bodies(bodies, BODY_DIR)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(fixture, indent=1, sort_keys=True) + '\n', encoding='utf-8')
        log('wrote %s' % target)
        summarise(fixture)
        return 0

    expected = json.loads(FIXTURE.read_text(encoding='utf-8'))
    for part, key in (('stores', 'stores'), ('files', 'files')):
        if part in parts:
            compare(expected.get(key), current.get(key), key, problems)
    if 'api' in parts:
        compare({'snapshot': expected.get('snapshot')}, {'snapshot': current.get('snapshot')}, '', problems)
        compare_endpoints(expected.get('endpoints', {}), current['endpoints'], bodies, BODY_DIR, problems)
    if problems:
        report(problems, 'CHECK FAILED')
        return 1
    summarise(current)
    print('CHECK PASSED: %s identical to %s' % (', '.join(parts), FIXTURE.name))
    return 0


def report(problems, headline):
    print('%s: %d difference(s)' % (headline, len(problems)))
    for line in problems:
        print('  ' + line)


def summarise(data):
    stores = data.get('stores', {})
    if stores:
        print('stores: %d, tables hashed: %d, rows hashed: %s' % (
            len(stores), sum(len(s['tables']) for s in stores.values()),
            '{:,}'.format(sum(t['rows'] for s in stores.values() for t in s['tables'].values()))))
    files = data.get('files', {})
    if files:
        print('files: walk-salt 1, snapshots %d (%d files), rowfw %d' % (
            len(files['snapshots']), sum(len(v['core']) + len(v['derived_cache']) for v in files['snapshots'].values()),
            len(files['rowfw'])))
    if 'endpoints' in data:
        statuses = {}
        for entry in data['endpoints'].values():
            statuses[entry.get('status', 'skipped')] = statuses.get(entry.get('status', 'skipped'), 0) + 1
        print('endpoints: %d, by status: %s' % (len(data['endpoints']), statuses))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('mode', choices=('record', 'check'))
    parser.add_argument('--port', type=int, default=0, help='spare port (default: 8769, else 8771); never 8767')
    parser.add_argument('--runs', type=int, default=2, help='record: endpoint passes, each on a fresh server (default 2)')
    parser.add_argument('--parts', default=','.join(PARTS), help='comma list of stores,files,api (check only; record needs all)')
    parser.add_argument('--python', help='interpreter that runs the spare server (default: $CU_PYTHON, the work/.venv, this one)')
    parser.add_argument('--work-dir', help='where the verified copy of .local and the server logs go (default: system temp)')
    parser.add_argument('--data-dir', help='run the spare server on this existing data directory instead of a fresh copy')
    parser.add_argument('--keep-copy', action='store_true', help='do not delete the copy of .local afterwards')
    parser.add_argument('--out', help='record: write the result here instead of tests/fixtures/co_baseline.json '
                                      '(allows a partial --parts record; never the committed fixture)')
    args = parser.parse_args(argv)
    return run(args.mode, args)


if __name__ == '__main__':
    sys.exit(main())
