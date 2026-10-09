"""Colorado (CO) baseline: the gate every later Terrier phase has to keep passing (multi-district build, Phase 0).

    <python> tools/co_baseline.py record [--port 8769] [--runs 2]    write tests/fixtures/co_baseline.json
    <python> tools/co_baseline.py check  [--port 8769]               recompute everything, diff against the fixture
    <python> tools/co_baseline.py capture-pre                        keep the score store's metadata before the unmask swap
    <python> tools/co_baseline.py make-stage --stage-dir D --scores S    verified copy of .local with the new score store S
    <python> tools/co_baseline.py check --intended unmask [--stage-dir D]    accept exactly "no area withheld" (below)

What it records (stdlib only; the server it starts needs the repo's own Python, see --python):

  stores     per table of contacts, scores, street, walk, roads and accounts (.local/*.sqlite3): row count and the
             sha256 of the rows dumped in a sorted, deterministic order (primary key, else every column), plus the
             sha256 of the schema. Never the raw file bytes. The stores are opened read-only.
  files      sha256 of .local/.walk-salt, of every file of the snapshots .local/snapshots/co-cd005-* (the derived/
             folder is a rebuildable cache and is listed apart; `check` passes a derived/ file the fixture does not
             list as INTENDED, the live server builds those on demand), and of the row-framework files the bundle is built
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

`check --intended unmask` (Paul 2026-10-08: "nothing should ever be withheld") passes only for that change: areas that were
withheld now carry the values of the shown-areas table (.local/area_scores_shown.csv, written by tools/shown_areas.py);
every standing is re-ranked over all areas and equals that table; every other field of an area that was already shown is
unchanged; the rest of the endpoints are byte-identical once the old and new snapshot keys and area-table shas are replaced
by placeholders; of the stores only the score metadata and the import rows differ, of the files only the new snapshot
folder. Anything else fails, named by JSON path or table, never by value. With --stage-dir the stores and files are read
from that copy (made by make-stage, then imported into), so the check can run before the live server is stopped.
"""
import argparse
import bisect
import collections
import copy
import csv
import datetime
import hashlib
import io
import json
import math
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
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
LOCAL = REPO / '.local'
FIXTURE = REPO / 'tests' / 'fixtures' / 'co_baseline.json'
BODY_DIR = LOCAL / 'baseline-co' / 'golden'
SHOWN = LOCAL / 'area_scores_shown.csv'                                      # tools/shown_areas.py --district co-05
PRE_FILE = LOCAL / 'baseline-co' / 'pre-unmask' / 'scores_metadata.json'     # capture-pre
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
    ('parties_registration_block', '/api/v3/parties/{snapshot}/registration/block.json'),
    ('parties_registration_precinct', '/api/v3/parties/{snapshot}/registration/precinct.json'),
    ('parties_registration_tract', '/api/v3/parties/{snapshot}/registration/tract.json'),
    ('parties_registration_zip', '/api/v3/parties/{snapshot}/registration/zip.json'),
    ('parties_residence_tract', '/api/v3/parties/{snapshot}/residence/tract.json'),
    ('parties_residence_zip', '/api/v3/parties/{snapshot}/residence/zip.json'),
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


def overview_party_split(golden, now):
    """O-10: the walk overview's d/r/u/o counts became {code: count}; D, R, U unchanged, the other codes (blank too) sum
    to o; geometry, span, street and doors unchanged; nothing else differs. Problems name JSON paths, never values."""
    a, b = json.loads(golden), json.loads(now)
    fs, gs = a.get('features'), b.get('features')
    if (set(a) != set(b) or a.get('type') != b.get('type') or not isinstance(fs, list) or not isinstance(gs, list)
            or len(fs) != len(gs)):
        return ['$.features']
    out = []
    for i, (fa, fb) in enumerate(zip(fs, gs)):
        pa, pb = fa.get('properties', {}), fb.get('properties', {})
        same = {k: v for k, v in pa.items() if k not in ('d', 'r', 'u', 'o')}
        parties = pb.get('parties')
        if (fa.get('geometry') != fb.get('geometry') or set(fa) != set(fb)
                or {k: v for k, v in pb.items() if k != 'parties'} != same or not isinstance(parties, dict)
                or any(not re.fullmatch(r'[A-Z]?', c) or not isinstance(n, int) or n < 1 for c, n in parties.items())
                or [parties.get(c, 0) for c in 'DRU'] != [pa['d'], pa['r'], pa['u']]
                or sum(n for c, n in parties.items() if c not in ('D', 'R', 'U')) != pa['o']):
            out.append('$.features[%d]' % i)
    return out[:12]


def split_differs(counts, people, d, r, u, o):
    """True unless counts ({code: people}) are whole numbers of at least 1 under single-capital (or blank) codes, total `people`,
    D, R and U equal the old shares d, r and u of them, and the other codes together (no code too) equal the old share o.
    The tolerance is the old shares' rounding to 8 decimals (v3_cache.SHARE_DECIMALS)."""
    if (not isinstance(counts, dict) or type(people) is not int or not all(isinstance(x, (int, float)) for x in (d, r, u, o))
            or any(not re.fullmatch(r'[A-Z]?', c) or type(n) is not int or n < 1 for c, n in counts.items())
            or sum(counts.values()) != people):
        return True
    minor = sum(n for c, n in counts.items() if c not in ('D', 'R', 'U'))
    return any(abs(got - share * people) > people * 2e-8 + 1e-6
               for got, share in ((counts.get('D', 0), d), (counts.get('R', 0), r), (counts.get('U', 0), u), (minor, o)))


def meta_party_split(golden, now):
    """O-10: /api/v3/meta's district_registration (shares d, r, u, o) became district_parties ({code: people}). D, R and U equal
    the old shares of the people, the other codes (no code too) sum to the old o, and every other key is unchanged. Problems
    name JSON paths, never values."""
    a, b = json.loads(golden), json.loads(now)
    old, parties = a.pop('district_registration', None), b.pop('district_parties', None)
    problems = [p if p.startswith(('$', '(')) else '$' + p for p in json_paths_differing(canonical(a), canonical(b))]
    if not isinstance(old, dict) or set(old) != {'d', 'r', 'u', 'o'}:
        problems.append('$.district_registration')
    elif split_differs(parties, a.get('people'), old['d'], old['r'], old['u'], old['o']):
        problems.append('$.district_parties')
    return problems


def parties_split(areas_golden, parties_now):
    """O-10: a level of /api/v3/parties against the OLD area table of the same level. Each area with registered people has one
    entry: its codes total its people, D, R and U equal the old lean shares of them and the other codes make up the old other.
    An area without people has no entry, and nothing else is in the answer. Problems name JSON paths, never values."""
    a, b = json.loads(areas_golden), json.loads(parties_now)
    if (not isinstance(b, dict) or set(b) != {'schema', 'snapshot', 'basis', 'level', 'ids', 'parties'} or b['schema'] != 'v3-parties-1'
            or [b[k] for k in ('snapshot', 'basis', 'level')] != [a.get(k) for k in ('snapshot', 'basis', 'level')]
            or not isinstance(b['ids'], list) or not isinstance(b['parties'], list) or len(b['ids']) != len(b['parties'])
            or b['ids'] != sorted(set(b['ids']))):
        return ['$']
    here, lean, out = dict(zip(b['ids'], b['parties'])), a['groups']['lean'], []
    if not set(here) <= set(a['ids']):
        out.append('$.ids')
    for i, ident in enumerate(a['ids']):
        if a['status'][i] != 'ok':
            if ident in here:
                out.append('$.areas[%d]' % i)
            continue
        d, r, u = lean['share_d'][i], lean['share_r'][i], lean['share_u'][i]
        if ident not in here or split_differs(here[ident], a['n'][i], d, r, u, None if None in (d, r, u) else 1 - d - r - u):
            out.append('$.areas[%d]' % i)
    return out[:12]


# Ruled differences from the Phase 0 fixture: {endpoint id: (ruling, verifier(golden bytes, new bytes) -> [problems])}.
# A listed endpoint passes only when its status and content type are unchanged and its verifier finds no problem
# (the golden body must be on disk); any other difference fails as before.
INTENDED = {'walk_overview_precinct': ('O-10 party codes as themselves', overview_party_split),
            'meta': ('O-10 party codes as themselves', meta_party_split)}
# Ruled new endpoints, until a fixture that records them exists: {endpoint id: (ruling, id of the old endpoint it is checked against,
# verifier(golden bytes of that endpoint, new bytes) -> [problems])}. One passes only with status 200 and a verifier that finds nothing.
ADDED = {'parties_%s_%s' % (basis, level): ('O-10 party codes as themselves', 'areas_%s_%s' % (basis, level), parties_split)
         for basis, level in (('registration', 'block'), ('registration', 'precinct'), ('registration', 'tract'),
                              ('registration', 'zip'), ('residence', 'tract'), ('residence', 'zip'))}


# Ruled: the live server writes a snapshot's derived/ memo files the first time someone browses a level, so a derived_cache file
# of a recorded snapshot that the fixture does not list is expected. One the fixture lists that changed or went missing still fails.
LAZY_CACHE = (re.compile(r'NEW +files\.snapshots\.%s\.derived_cache\.\S+' % SNAPSHOT_DIR.pattern),
              'derived cache built on demand by the live server')


def lazy_cache_intended(lines):
    """`lines` without the NEW derived_cache files of a recorded snapshot, each printed as INTENDED."""
    pattern, ruling = LAZY_CACHE
    for line in lines:
        if pattern.fullmatch(line):
            print('INTENDED %s (%s)' % (line.split(None, 1)[1], ruling))
    return [line for line in lines if not pattern.fullmatch(line)]


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


def file_hashes(data_dir, rowfw=ROWFW, shown=SHOWN):
    data_dir = Path(data_dir)
    result = {'walk_salt': {'sha256': sha256_file(data_dir / '.walk-salt')}, 'snapshots': {}, 'rowfw': {}}
    if Path(shown).exists():             # the shown-areas table is a pinned input from the unmask on; absent before it
        result['shown'] = {'sha256': sha256_file(shown)}
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


def make_copy(work_dir, source_dir=LOCAL, folder=None):
    """A verified copy of the files the server reads: .local without backups, walk-backups, pycache and server.pid.
    `folder` names the target (default: a new temp folder under work_dir)."""
    folder = Path(folder) if folder else Path(tempfile.mkdtemp(prefix='co-baseline-', dir=str(work_dir)))
    folder.mkdir(parents=True, exist_ok=True)
    started = time.time()
    for name in COPIED_FILES:
        source = source_dir / name
        if source.exists():
            shutil.copyfile(source, folder / name)
    shutil.copytree(source_dir / 'snapshots', folder / 'snapshots', dirs_exist_ok=True)
    for name in COPIED_FILES:
        source = source_dir / name
        if source.exists() and sha256_file(source) != sha256_file(folder / name):
            raise SystemExit('The copy of %s differs from the original (changed while copying?). Run again.' % name)
    log('copied %s to %s in %.0fs, files verified byte for byte' % (source_dir, folder, time.time() - started))
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


def compare_endpoints(expected, actual, bodies_now, golden_dir, out, intended=INTENDED, added=ADDED):
    for endpoint_id in sorted(set(expected) | set(actual)):
        before, now = expected.get(endpoint_id), actual.get(endpoint_id)
        if before is None and now is not None and endpoint_id in added:
            ruling, source, verify = added[endpoint_id]
            golden = golden_dir / (source + '.body')
            if now.get('status') != 200 or endpoint_id not in bodies_now or not golden.exists():
                out.append('NEW       endpoints.%s: ruled addition "%s" cannot be verified (needs status 200 and the golden body of %s)'
                           % (endpoint_id, ruling, source))
                continue
            found = verify(golden.read_bytes(), bodies_now[endpoint_id])
            if found:
                out.append('NEW       endpoints.%s: ruled addition "%s" not met at %s' % (endpoint_id, ruling, ', '.join(found)))
            else:
                print('INTENDED endpoints.%s (%s): verified against %s' % (endpoint_id, ruling, source))
            continue
        if before is None or now is None:
            out.append('%s     endpoints.%s' % ('NEW      ' if before is None else 'MISSING  ', endpoint_id))
            continue
        problems = []
        for key in ('status', 'content_type', 'sha256', 'skipped') + (() if 'normalised' in before else ('bytes',)):
            if before.get(key) != now.get(key):
                problems.append('%s %s -> %s' % (key, short(before.get(key)), short(now.get(key))))
        if not problems:
            continue
        golden = golden_dir / (endpoint_id + '.body')
        ruled = []
        if endpoint_id in intended and all(before.get(k) == now.get(k) for k in ('status', 'content_type', 'skipped')):
            ruling, verify = intended[endpoint_id]
            if endpoint_id not in bodies_now or not golden.exists():
                ruled = ['cannot verify a ruled difference without the golden body']
            else:
                found = verify(golden.read_bytes(), bodies_now[endpoint_id])
                if not found:
                    print('INTENDED endpoints.%s (%s): verified' % (endpoint_id, ruling))
                    continue
                ruled = ['ruled difference "%s" not met at %s' % (ruling, ', '.join(found))]
        line = 'DIFFERENT endpoints.%s: %s' % (endpoint_id, '; '.join(problems + ruled))
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


# ---- the unmask check (Paul 2026-10-08: nothing is withheld) -------------------------------------------------------------
# A verifier takes (golden bytes, new bytes, ctx) and returns problems as JSON paths, never values. `ctx` holds the old and
# new snapshot keys and area-table shas, the shown-areas table (the oracle) and counters.

AREA_CELLS = {'mean': 'eb_mean', 'se': 'eb_se', 'measured': 'measured_pct_mean', 'pct': 'area_pct', 'top10': 'top10_share_eb',
              'top10_se': 'top10_eb_se', 'top10_pct': 'top10_area_pct'}
LEAN_CELLS = {'mean': 'eb_mean', 'se': 'eb_se', 'measured': 'measured_pct_mean', 'share_d': 'share_reg_D',
              'share_r': 'share_reg_R', 'share_u': 'share_reg_U'}
STANDINGS = ('pct', 'top10_pct')               # the only cells of a shown area that may move: they are re-ranked over all areas
AREA_TOLERANCE = 1e-8                          # the wire rounds to 8 decimals (v3_cache._r)
META_MAY_CHANGE = ('area_table_sha256', 'suppressed_ids_sha256', 'area_table', 'built_utc', 'build_code_sha256')
ACCOUNT_TABLES = ('research_snapshot', 'research_auditevent', 'sqlite_sequence')      # what import_summary writes
MANIFEST_MASKED = (('snapshot',), ('created_at',), ('row_framework', 'area_table_sha256'), ('row_framework', 'area_indices_sha256'),
                   ('row_framework', 'base_snapshot'), ('threshold',), ('privacy',), ('measure_methodology',))
SUMMARY_RANKED = ('index',)                        # the percentile moves for every row: more areas have values
SUMMARY_COMPOSITE = ('value', 'component_coverage', 'components_used', 'components_total')      # an index of indices is re-derived too
SUMMARY_META = ('snapshot', 'threshold', 'coverage', 'measure_methodology')


class Found(list):
    """Problems as JSON paths, at most a dozen."""

    def add(self, path):
        if len(self) < 12 and path not in self:
            self.append(path)


def near(a, b, tolerance=AREA_TOLERANCE):
    return (a is None and b is None) or (a is not None and b is not None and abs(a - b) <= tolerance)


def read_shown(path):
    """{(basis, level, area_id): {group: {column: float or None}}} of the shown-areas table; ids stay text ('nan' is an id)."""
    shown = {}
    with open(path, newline='', encoding='utf-8') as stream:
        for row in csv.DictReader(stream):
            cells = {c: (float(v) if v else None) for c, v in row.items() if c not in ('basis', 'level', 'area_id', 'group', 'parent_id')}
            shown.setdefault((row['basis'], row['level'], row['area_id']), {})[row['group']] = cells
    return shown


def normalised(body, ctx):
    """The body as text with the old and new snapshot keys and area-table shas replaced by placeholders."""
    text = body.decode('utf-8', 'replace')
    for key in (ctx.old_key, ctx.new_key):
        text = text.replace(key, '@SNAPSHOT@')
    for sha in (ctx.old_sha, ctx.shown_sha):
        text = text.replace(sha, '@AREA_TABLE@')
    return text.encode('utf-8')


def verify_keys_only(golden, now, ctx):
    """Every endpoint the unmask does not touch: byte-identical once the keys and shas are replaced."""
    a, b = normalised(golden, ctx), normalised(now, ctx)
    return [] if a == b else json_paths_differing(a, b) or ['$ (only the bytes differ)']


def verify_areas(golden, now, ctx):
    """areas_*: an area that was shown keeps every cell but its two standings; a withheld area now carries the shown table's
    values (`ok`) or, with no registered people, is `empty` with n 0 and no values; every standing equals the shown table."""
    old, new, found = json.loads(golden), json.loads(now), Found()
    basis, level, ids = old['basis'], old['level'], old['ids']
    if set(old) != set(new) or set(old['groups']) != set(new['groups']):
        return ['$']
    if new['snapshot'] != ctx.new_key:
        found.add('$.snapshot')
    for key in ('schema', 'basis', 'level', 'ids', 'labels', 'parent', 'neighbours'):
        if old.get(key) != new.get(key):
            found.add('$.' + key)
    series = {k: new[k] for k in ('status', 'n', 'households', 'precinct', 'tract', 'blocks')}
    series.update({'groups.%s.%s' % (g, c): v for g, cells in new['groups'].items() for c, v in cells.items()})
    for name, values in series.items():
        if len(values) != len(ids):
            found.add('$.%s[len]' % name)
    if found:
        return list(found)
    for i, ident in enumerate(ids):
        was, state, shown = old['status'][i], new['status'][i], ctx.shown.get((basis, level, ident))
        cells = [(g, name, column, new['groups'][g][name][i], old['groups'][g][name][i])
                 for g in new['groups'] for name, column in (LEAN_CELLS if g == 'lean' else AREA_CELLS).items()]
        if state == 'ok' and shown is not None and 'ovr' in shown and was in ('ok', 'withheld'):
            if was == 'withheld':
                ctx.stats['newly_shown'] += 1
            ovr = shown['ovr']
            for key, column in (('n', 'n_rows'), ('households', 'n_households')):
                if new[key][i] is None or float(new[key][i]) != ovr[column] or (was == 'ok' and new[key][i] != old[key][i]):
                    found.add('$.%s[%d]' % (key, i))
            for key in ('precinct', 'tract', 'blocks'):
                if (new[key][i] != old[key][i]) if was == 'ok' else not isinstance(new[key][i], (str, int, type(None))):
                    found.add('$.%s[%d]' % (key, i))
            for g, name, column, value, before in cells:
                if shown.get(g) is None or not near(value, shown[g].get(column)):
                    found.add('$.groups.%s.%s[%d]' % (g, name, i))
                elif was == 'ok' and value != before:
                    if name in STANDINGS:
                        ctx.stats['moved'] += 1
                    else:
                        found.add('$.groups.%s.%s[%d]' % (g, name, i))
        elif state == 'empty' and was == 'withheld' and shown is None:
            ctx.stats['empty'] += 1
            if (new['n'][i], new['households'][i]) != (0, 0) or any(new[k][i] is not None for k in ('precinct', 'tract', 'blocks')) \
                    or any(value is not None for _g, _n, _c, value, _b in cells):
                found.add('$.status[%d]' % i)
        else:
            found.add('$.status[%d]' % i)
    present = set(ids)
    if any(b == basis and lv == level and a not in present and a.isdigit() for (b, lv, a) in ctx.shown):
        found.add('$.ids')                      # an area of the shown table (not the id-less pseudo-area) has no row
    return list(found)


def verify_meta(golden, now, ctx):
    """meta: `floors` is now `small_area` (same 20 and 10), the suppressed-id sha is gone, the area-table sha is the shown
    table's, the concordance count is `without_polygon`; nothing else."""
    old, new = json.loads(golden), json.loads(now)
    old['small_area'] = old.pop('floors', None)
    old['row_framework'].pop('suppressed_ids_sha256', None)
    old['row_framework']['area_table_sha256'] = ctx.shown_sha
    concordance = old['geometry_method']['concordance']
    concordance['without_polygon'] = concordance.pop('withheld_without_polygon', None)
    old['snapshot'] = ctx.new_key
    return json_paths_differing(canonical(old), canonical(new))


def verify_profile(golden, now, ctx):
    """person_profile: the block and precinct rows lose `withheld` and gain n and households; pct is the shown table's top-10
    standing, top10 stays when the area was shown and is the shown table's when it was not; the rest is untouched."""
    old, new = json.loads(golden), json.loads(now)
    context_old, context_new = old.pop('area_context'), new.pop('area_context')
    found = Found(json_paths_differing(canonical(old), canonical(new)))
    if set(context_old) != set(context_new):
        found.add('$.area_context')
    for level in sorted(set(context_old) & set(context_new)):
        was, ref, path = context_old[level], context_new[level], '$.area_context.' + level
        shown = ctx.shown.get(('registration', level, was['id']), {}).get('ovr')
        if set(ref) != {'level', 'id', 'label', 'pct', 'top10', 'n', 'households'} or any(ref[k] != was[k] for k in ('level', 'id', 'label')):
            found.add(path)
        elif was['id'] == '':
            if (ref['pct'], ref['top10']) != (None, None):
                found.add(path + '.pct')
        elif shown is None:
            found.add(path + '.id')
        else:
            for key, value in (('pct', shown['top10_area_pct']), ('top10', shown['top10_share_eb'])):
                if key == 'top10' and not was['withheld']:
                    value = was['top10']                     # a shown area keeps its share
                if ref[key] is None or value is None or not math.isclose(ref[key], value, rel_tol=1e-7, abs_tol=1e-9):
                    found.add('%s.%s' % (path, key))
            if (ref['n'], ref['households']) != (shown['n_rows'], shown['n_households']):
                found.add(path + '.n')
    return list(found)


def verify_workspace(golden, now, ctx):
    """workspace: the snapshot key, the store's build time and the areas ledger text (which no longer mentions withholding)."""
    old, new, found = json.loads(golden), json.loads(now), Found()
    ledger = next((x for x in new.get('layers', []) if x.get('key') == 'areas'), {})
    if new.get('snapshot') != ctx.new_key:
        found.add('$.snapshot')
    if ctx.new_built is not None and new.get('builtAt') != ctx.new_built:
        found.add('$.builtAt')
    if not isinstance(ledger.get('detail'), str) or 'ithheld' in ledger['detail']:
        found.add('$.layers[areas].detail')
    for body in (old, new):
        body.pop('snapshot', None)
        body.pop('builtAt', None)
        for item in body.get('layers', []):
            if item.get('key') == 'areas':
                item.pop('detail', None)
    return list(found) + json_paths_differing(canonical(old), canonical(new))


def mask_manifest(manifest):
    for path in MANIFEST_MASKED:
        node = manifest
        for part in path[:-1]:
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, dict):
            node[path[-1]] = '@'
    return manifest


def verify_snapshots(golden, now, ctx):
    """method_snapshots: old entries unchanged apart from `active`; one new active entry whose manifest is the old active one's
    without the withheld-area keys (and with the shown table's sha), nothing else beyond the masked build fields."""
    old, new, found = json.loads(golden)['snapshots'], json.loads(now)['snapshots'], Found()
    by_old, by_new = {s['key']: s for s in old}, {s['key']: s for s in new}
    if ctx.new_key in by_old or set(by_new) != set(by_old) | {ctx.new_key}:
        return ['$.snapshots']
    if [k for k, s in by_new.items() if s['active']] != [ctx.new_key]:
        found.add('$.snapshots[active]')
    for index, (key, was) in enumerate(by_old.items()):
        if canonical({**was, 'active': None}) != canonical({**by_new[key], 'active': None}):
            found.add('$.snapshots[%d]' % index)
    want = copy.deepcopy(next(s for s in old if s['active'])['manifest'])
    got = copy.deepcopy(by_new[ctx.new_key]['manifest'])
    want.pop('area_floors', None)
    for key in ('suppressed_ids_sha256', 'withheld_set_sha256', 'd1_audit'):
        want['row_framework'].pop(key, None)
    concordance = want['geometry_method']['concordance']
    concordance['without_polygon'] = concordance.pop('withheld_without_polygon', None)
    framework = got.get('row_framework', {})
    if (got.get('snapshot') != ctx.new_key or framework.get('area_table_sha256') != ctx.shown_sha
            or not str(got.get('created_at', '')).startswith('Built from the score store written ')
            or not re.fullmatch(r'[0-9a-f]{64}', str(framework.get('area_indices_sha256')))):
        found.add('$.snapshots[new].manifest')
    return list(found) + json_paths_differing(canonical(mask_manifest(want)), canonical(mask_manifest(got)))


def summary_rows(body):
    text = body.decode('utf-8')
    if text.lstrip().startswith('{'):
        data = json.loads(text)
        return data['meta'], data['rows']
    return None, list(csv.DictReader(io.StringIO(text)))


def verify_summaries(golden, now, ctx):
    """summaries_* (the old summary layer; its small-count suppression is gone too): same rows in the same order; a row that
    was ok keeps count, share, coverage, valid_count, field_coverage and a raw measure's value to the byte (only the percentile
    `index` moves, and for a composite index also its derived value and component counts); a suppressed row is now ok with a
    count; nothing is suppressed."""
    (old_meta, old_rows), (new_meta, new_rows), found = summary_rows(golden), summary_rows(now), Found()
    ident = lambda r: r.get('id', r.get('geographic_id'))
    if [ident(r) for r in old_rows] != [ident(r) for r in new_rows]:
        return ['$.rows']
    if old_meta is not None:
        for key in set(old_meta) | set(new_meta):
            if key not in SUMMARY_META and old_meta.get(key) != new_meta.get(key):
                found.add('$.meta.' + key)
        if new_meta.get('snapshot') != ctx.new_key:
            found.add('$.meta.snapshot')
    composite = bool(new_meta and new_meta.get('index_method')) or any(r.get('components_used') not in (None, '') for r in new_rows)
    ranked = SUMMARY_RANKED + (SUMMARY_COMPOSITE if composite else ())
    for i, (before, row) in enumerate(zip(old_rows, new_rows)):
        if row.get('status') != 'ok' or row.get('measure_status', 'ok') != 'ok':
            found.add('$.rows[%d].status' % i)
        elif before.get('status') == 'ok':
            if any(before.get(k) != row.get(k) for k in set(before) | set(row) if k not in ranked + ('snapshot',)):
                found.add('$.rows[%d]' % i)
            if 'snapshot' in row and row['snapshot'] != ctx.new_key:
                found.add('$.rows[%d].snapshot' % i)
        elif row.get('count') in (None, ''):
            found.add('$.rows[%d].count' % i)
        else:
            ctx.stats['summary_rows'] += 1
    if new_meta is not None and new_meta.get('metric') == 'count' and not found:
        values = sorted(r['value'] for r in new_rows)
        for i, row in enumerate(new_rows):
            lo, hi = bisect.bisect_left(values, row['value']), bisect.bisect_right(values, row['value'])
            if row['value'] != row['count'] or not math.isclose(row['index'], 100 * (lo + (hi - lo) / 2) / len(values), rel_tol=1e-9, abs_tol=1e-9):
                found.add('$.rows[%d].index' % i)
    return list(found)


def guarded(verify, ctx):
    def run_one(golden, now):
        try:
            return verify(golden, now, ctx)
        except (KeyError, TypeError, ValueError, AttributeError, IndexError, StopIteration) as problem:
            return ['$ (unexpected shape: %s)' % type(problem).__name__]
    return run_one


def unmask_rules(ctx):
    """{endpoint id: (ruling, verifier)} for every endpoint: the special ones, else byte-identical after the keys are replaced."""
    special = {'meta': verify_meta, 'person_profile': verify_profile, 'workspace': verify_workspace, 'method_snapshots': verify_snapshots}
    rules = {}
    for endpoint_id, _template in ENDPOINTS:
        verify = special.get(endpoint_id) or (verify_areas if endpoint_id.startswith('areas_') else
                                             verify_summaries if endpoint_id.startswith('summaries_') else verify_keys_only)
        rules[endpoint_id] = ('nothing withheld, Paul 2026-10-08', guarded(verify, ctx))
    return rules


def unmask_unexplained(lines, ctx):
    """The generic difference lines (snapshot, files, stores) that the unmask does not explain."""
    allowed = {('DIFFERENT', '.snapshot'), ('NEW', 'files.shown'), ('NEW', 'files.snapshots.' + ctx.new_key)}
    # sqlite_stat1 is SQLite's own query-planner statistics, regenerated whenever the score store is rebuilt; not data.
    allowed |= {('DIFFERENT', 'stores.scores.tables.%s.%s' % (t, k)) for t in ('metadata', 'sqlite_stat1') for k in ('rows', 'sha256')}
    allowed |= {('DIFFERENT', 'stores.accounts.tables.%s.%s' % (t, k)) for t in ACCOUNT_TABLES for k in ('rows', 'sha256')}
    return [line for line in lines if tuple(line.split(':')[0].split()[:2]) not in allowed]


def rows_digest(rows):
    """table_digest's hash for a small table already in primary-key order (one batch)."""
    return hashlib.sha256(('\n'.join(repr(tuple(r)) for r in rows) + '\n').encode('utf-8', 'surrogatepass')).hexdigest()


def capture_pre():
    """Keep the live score store's metadata before the swap; refuses unless it is exactly the recorded state."""
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    connection = open_readonly(LOCAL / 'scores.sqlite3')
    try:
        digest = table_digest(connection, 'metadata')
        rows = [list(r) for r in connection.execute('SELECT key, value FROM metadata ORDER BY key')]
    finally:
        connection.close()
    if digest != fixture['stores']['scores']['tables']['metadata']:
        raise SystemExit('The live score store is not the recorded one; nothing kept.')
    if not can_keep_bodies(PRE_FILE.parent):
        raise SystemExit('%s is not gitignored; nothing kept.' % PRE_FILE.parent)
    PRE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PRE_FILE.write_text(json.dumps({'schema': 'pre-unmask-1', 'digest': digest, 'rows': rows}), encoding='utf-8')
    log('kept %d metadata rows of the live score store in %s' % (len(rows), PRE_FILE))


def old_metadata(fixture):
    """{key: value} of the score store's metadata as recorded: from the capture-pre dump, else the live store while it is
    still the recorded one. Fails when neither is."""
    wanted = fixture['stores']['scores']['tables']['metadata']
    if PRE_FILE.exists():
        dump = json.loads(PRE_FILE.read_text(encoding='utf-8'))
        if dump['digest'] != wanted or rows_digest(dump['rows']) != wanted['sha256']:
            raise SystemExit('The pre-unmask metadata dump is not the recorded state.')
        return {k: v for k, v in dump['rows']}
    connection = open_readonly(LOCAL / 'scores.sqlite3')
    try:
        if table_digest(connection, 'metadata') != wanted:
            raise SystemExit('No pre-unmask metadata: run capture-pre while the live score store is still the recorded one.')
        return dict(connection.execute('SELECT key, value FROM metadata').fetchall())
    finally:
        connection.close()


def metadata_problems(old, new, ctx):
    """Score metadata: only the area-table keys, the build time and code sha change; `w1` keeps all but its timing."""
    def untimed(value):
        parsed = json.loads(value)
        parsed.pop('seconds', None)
        return parsed
    found = ['scores.metadata.%s changed' % k for k in sorted(set(old) | set(new)) if k not in META_MAY_CHANGE and
             (untimed(old[k]) != untimed(new[k]) if k == 'w1' and k in old and k in new else old.get(k) != new.get(k))]
    if 'suppressed_ids_sha256' in new:
        found.append('scores.metadata.suppressed_ids_sha256 is still there')
    table = json.loads(new.get('area_table', 'null')) or {}
    if json.loads(new.get('area_table_sha256', 'null')) != ctx.shown_sha or table.get('area_table_sha256') != ctx.shown_sha:
        found.append('scores.metadata.area_table_sha256 is not the shown table')
    if {k: v.get('areas') for k, v in table.get('levels', {}).items()} != ctx.level_areas:
        found.append('scores.metadata.area_table.levels differ from the shown table')
    manifest = SHOWN.with_suffix('.manifest.json')
    if manifest.exists() and table.get('manifest_sha256') != sha256_file(manifest):
        found.append('scores.metadata.area_table.manifest_sha256 is not the shown table\'s manifest')
    return found


def unmask_context(fixture, current, bodies, data_dir, golden_dir=BODY_DIR, shown_path=SHOWN):
    """Everything the unmask verifiers need; facts that already fail are collected in ctx.problems."""
    if not Path(shown_path).exists():
        raise SystemExit('%s is missing: run tools/shown_areas.py --district co-05 first.' % shown_path)
    ctx = SimpleNamespace(old_key=fixture['snapshot'], new_key=current['snapshot'], shown_sha=sha256_file(shown_path),
                          old_sha=fixture['files']['rowfw']['area_scores_rowfw-1.0.0.csv'], shown=read_shown(shown_path),
                          stats=collections.Counter(), problems=[], new_built=None)
    ctx.level_areas = dict(collections.Counter('%s/%s' % k[:2] for k, groups in ctx.shown.items() if 'ovr' in groups))
    if ctx.new_key == ctx.old_key:
        ctx.problems.append('the snapshot key did not change')
    if json.loads(bodies['meta'])['row_framework']['area_table_sha256'] != ctx.shown_sha:
        ctx.problems.append('the new meta does not name the shown table')
    for endpoint_id, entry in fixture['endpoints'].items():       # a stale golden body would make every verdict meaningless
        path = Path(golden_dir) / (endpoint_id + '.body')
        if path.exists() and 'sha256' in entry and body_digest(endpoint_id, path.read_bytes(), entry['content_type'])[0] != entry['sha256']:
            ctx.problems.append('golden body %s is not the recorded state' % endpoint_id)
    new = store_metadata(Path(data_dir) / 'scores.sqlite3')
    ctx.new_built = json.loads(new['built_utc'])
    ctx.problems += metadata_problems(old_metadata(fixture), new, ctx)
    return ctx


def store_metadata(path):
    connection = open_readonly(path)
    try:
        return dict(connection.execute('SELECT key, value FROM metadata').fetchall())
    finally:
        connection.close()


def unmask_final(ctx):
    print('INTENDED unmask: %d areas newly shown, %d without registered people (empty), %d standings moved in shown areas, '
          '%d summary rows shown' % (ctx.stats['newly_shown'], ctx.stats['empty'], ctx.stats['moved'], ctx.stats['summary_rows']))
    return ctx.problems + ([] if ctx.stats['newly_shown'] else ['no previously withheld area carries a value'])


def make_stage(args):
    """A verified copy of the live .local with the new score store in place of the old one (import_summary comes next)."""
    stage = Path(args.stage_dir).resolve()
    if not args.scores or (stage.exists() and any(stage.iterdir())):
        raise SystemExit('make-stage needs --scores and an empty or new --stage-dir')
    make_copy(stage.parent, LOCAL, stage)
    shutil.copyfile(args.scores, stage / 'scores.sqlite3')
    print('staged %s; scores.sqlite3 sha256 %s' % (stage, sha256_file(stage / 'scores.sqlite3')))


# ---- main flow -----------------------------------------------------------------------------------------------------

def run(mode, args):
    parts = [p for p in args.parts.split(',') if p]
    if any(p not in PARTS for p in parts):
        raise SystemExit('--parts takes a comma list of %s' % ', '.join(PARTS))
    runs = args.runs if mode == 'record' else 1
    if (args.intended or args.stage_dir) and (mode != 'check' or not args.intended or set(parts) != set(PARTS)):
        raise SystemExit('--intended and --stage-dir go together, with check and all parts')
    data = Path(args.stage_dir).resolve() if args.stage_dir else LOCAL      # the stores, files and server data to check
    work_dir = Path(args.work_dir or tempfile.gettempdir()).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    live_before = listener_pid(LIVE_PORT)
    log('live server on %d: pid %s (never contacted)' % (LIVE_PORT, live_before))
    problems, current = [], {'schema': SCHEMA}

    if 'stores' in parts:
        log('hashing the stores (before)')
        current['stores'] = store_hashes(data)
    if 'files' in parts:
        log('hashing files')
        current['files'] = file_hashes(data)
        problems += frozen_check(current['files']['rowfw'])

    bodies = {}
    spare = None
    if 'api' in parts:
        spare = choose_port(args.port)
        python = server_python(args.python)
        log('server python: %s; spare port: %d' % (python, spare))
        data_dir = Path(args.data_dir) if args.data_dir else make_copy(work_dir, data)
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
        after = store_hashes(data)
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
    ctx, rules, lines = None, INTENDED, []
    if args.intended:
        ctx = unmask_context(expected, current, bodies, data)
        rules = {**unmask_rules(ctx), **INTENDED}              # O-10 keeps its own verifier
    for part, key in (('stores', 'stores'), ('files', 'files')):
        if part in parts:
            compare(expected.get(key), current.get(key), key, lines)
    if 'api' in parts:
        compare({'snapshot': expected.get('snapshot')}, {'snapshot': current.get('snapshot')}, '', lines)
        compare_endpoints(expected.get('endpoints', {}), current['endpoints'], bodies, BODY_DIR, lines, rules)
    lines = lazy_cache_intended(lines)
    if ctx:
        lines = unmask_unexplained(lines, ctx) + unmask_final(ctx)
    problems += lines
    if problems:
        report(problems, 'CHECK FAILED')
        return 1
    summarise(current)
    print('CHECK PASSED: %s %s %s' % (', '.join(parts), 'as ruled (nothing withheld) against' if ctx else 'identical to', FIXTURE.name))
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
    parser.add_argument('mode', choices=('record', 'check', 'capture-pre', 'make-stage'))
    parser.add_argument('--port', type=int, default=0, help='spare port (default: 8769, else 8771); never 8767')
    parser.add_argument('--runs', type=int, default=2, help='record: endpoint passes, each on a fresh server (default 2)')
    parser.add_argument('--parts', default=','.join(PARTS), help='comma list of stores,files,api (check only; record needs all)')
    parser.add_argument('--python', help='interpreter that runs the spare server (default: $CU_PYTHON, the work/.venv, this one)')
    parser.add_argument('--work-dir', help='where the verified copy of .local and the server logs go (default: system temp)')
    parser.add_argument('--data-dir', help='run the spare server on this existing data directory instead of a fresh copy')
    parser.add_argument('--keep-copy', action='store_true', help='do not delete the copy of .local afterwards')
    parser.add_argument('--out', help='record: write the result here instead of tests/fixtures/co_baseline.json '
                                      '(allows a partial --parts record; never the committed fixture)')
    parser.add_argument('--intended', choices=('unmask',), help='check: accept exactly the ruled change "no area withheld"')
    parser.add_argument('--stage-dir', help='check --intended / make-stage: the copy of .local to check (stores, files, server data)')
    parser.add_argument('--scores', help='make-stage: the new score store to put into the stage')
    args = parser.parse_args(argv)
    if args.mode == 'capture-pre':
        return capture_pre()
    if args.mode == 'make-stage':
        return make_stage(args)
    return run(args.mode, args)


if __name__ == '__main__':
    sys.exit(main())
