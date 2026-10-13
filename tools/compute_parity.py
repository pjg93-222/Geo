"""Parity of a containerised store build against the Windows build (COMPUTE.md; design sections 4 and 6).

    <venv python> tools/compute_parity.py --scratch <new folder> [--steps roads] [--district co-05] [--inputs-from <finished run's folder>]
                  [--image ghcr.io/pjg93-222/terrier-build@sha256:...] [--score-image ghcr.io/pjg93-222/terrier-build-score@sha256:...]
                  [--api] [--negative-control] [--skip-container] [--export-bind]

  Each step has a toolchain (tools/build_step.py STEPS): roads, street, contacts, walk, ingest, extend, bundle and density run in the geo
  image (terrier-build) and on Windows in the work venv (--python); shown and scores run in the score image (terrier-build-score) and on
  Windows in the system Python 3.12 (--score-python, $TERRIER_PY312), as Terrier's own runners do. Only the images the chosen steps need
  are resolved. A step reads stores that earlier steps leave (walk reads roads, street, contacts and the score store): run those steps
  in the same run, or judge the later steps from ONE set of inputs with --inputs-from (a finished run's A1: its stores are copied into
  A1, A2 and, as a tar stream into the stage volume, B; nothing is mounted). That also keeps a store's file hash (the walk store records
  the sha256 of roads.sqlite3) and a stamp (the bundle records the score store's built_utc) from being compared across two builds.
  An image is, in this order: --image / --score-image, $TERRIER_BUILD_IMAGE / $TERRIER_SCORE_IMAGE (an explicit build no signed release
  names yet: the first parity runs), else the one the laptop's verified current release manifest names (Melange ops/RELEASE.md,
  "Compute jobs follow the manifest"; `current.py image terrier-build` / `terrier-build-score`). None of the three: nothing runs.

  A1, A2  the Windows build of the steps, twice, into <scratch>/A1 and <scratch>/A2. What differs between them is the legitimate
          run-to-run variance (a stamp, a run id): those tables, and only those, are left out of the comparison with B
  B       the same steps in the container (compute.compose.yaml), built in the stage volume, exported through a named volume
          and `docker cp` to <scratch>/B (--export-bind: straight into the bind-mounted folder, for native-Linux hosts)
  Compared with co_baseline.store_digest per table (a logical comparison) for each SQLite store and byte for byte for every other
  file (the shown-areas CSV and manifest, --files: the bundle zip, density.json, ...); --api adds co_baseline's golden endpoint
  check on A1 and B. A `metadata` table that differs between A1 and A2 is not left out whole: per key (and per field of a JSON value,
  such as w1.seconds) only what varies is, the rest must match B. One throwaway demo key serves A1, A2 and B (scratch/demo_key.hex,
  build_step.seed_demo_key), or the demo names, their search index and the stored key would differ in every build.
  The shadow tables of an FTS5 table (demo_search_data, _idx, _docsize, _config, _content) are the physical layout of its index, which
  the SQLite library decides (D-177, ruling of 2026-10-13): when they are not exactly equal they must be equal after FTS5 'rebuild' by
  one library on scratch copies, and 400 MATCH queries must return the same rowids on the stores as built (fts_parity).
  Prints PASS or FAIL per artifact and exits 0 only when every line passes. Counts and names only; no row is printed.
  One heavy job at a time: A1, then A2, then B. Never point --scratch at a .local folder.
"""
import argparse
import datetime
import json
import math
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools import build_step as S            # noqa: E402
from tools import co_baseline as B           # noqa: E402
from tools import districts as D             # noqa: E402
from tools import hostpaths as H             # noqa: E402

IMAGE_NAMES = {'geo': 'terrier-build', 'score': 'terrier-build-score'}      # what the release manifest calls each build image
SERVICES = {'geo': 'terrier-build', 'score': 'terrier-score'}                 # the compose service that runs each
RELEASE_CURRENT = r'C:\Users\pgoff\OneDrive\Desktop\Melange Systems\ops\release_lib\current.py'      # $MELANGE_RELEASE_CURRENT moves it
COMPOSE_FILE = REPO / 'compute.compose.yaml'
EXPORT_BIND_FILE = REPO / 'compute.export-bind.compose.yaml'
STORE_META = ('schema_sha256', 'objects', 'user_version')
STAGE_VOLUME = 'terrier-compute_stage'       # compute.compose.yaml: project `terrier-compute`, volume `stage`
KV_TABLES = ('metadata', 'meta')             # the key/value tables of the stores: metadata (street, contacts, scores ...), meta (walk)
# What a store's key/value table records about the process that wrote it, not about the data: the walk store's qa document keeps the
# seconds of each phase and the peak working set (a Windows call, so None in the container). Always left out, named in the line.
MEASURED = {'meta': {'qa': ('timing', 'peak_memory_mb')}}


# ---- comparison (pure: two or three folders in, a verdict per artifact out) ------------------------------------------

def integrity_ok(path):
    try:
        with closing(B.open_readonly(path)) as connection:
            return connection.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
    except sqlite3.Error:
        return False


def key_values(path, table='metadata'):
    """{key: value} of a store's key/value table (`table`: columns key, value); None when it is not a plain key/value table."""
    try:
        with closing(B.open_readonly(path)) as connection:
            if [row[1] for row in connection.execute('PRAGMA table_info(%s)' % B.quote(table))] != ['key', 'value']:
                return None
            return dict(connection.execute('SELECT key, value FROM %s' % B.quote(table)))
    except sqlite3.Error:
        return None


def json_object(text):
    try:
        found = json.loads(text)
    except (TypeError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def metadata_variance(a1, a2, b, measured=None):
    """(left_out, mismatched) for the key/value dicts of A1, A2 and B. What differs between the two Windows builds is the
    run-to-run variance and is left out: a plain value (built_utc), or, for a JSON object, only the fields that differ (w1.seconds,
    not the verification result beside it). `measured` {key: fields}: fields of a JSON value that measure the writing process and are
    left out whatever A1 and A2 say. Any other difference between A1 and B is a mismatch."""
    left_out, mismatched = [], []
    for key in sorted(set(a1) | set(a2) | set(b)):
        x, y, z = a1.get(key), a2.get(key), b.get(key)
        fields = (measured or {}).get(key)
        if fields and None not in (x, y, z):
            fx, fy, fz = json_object(x), json_object(y), json_object(z)
            if None not in (fx, fy, fz):
                varying = sorted({f for f in set(fx) | set(fy) if fx.get(f) != fy.get(f)} | {f for f in fields if f in fx or f in fy or f in fz})
                left_out += ['%s.%s' % (key, f) for f in varying]
                if {f: v for f, v in fx.items() if f not in varying} != {f: v for f, v in fz.items() if f not in varying}:
                    mismatched.append(key)
                continue
        if x == y:
            if x != z:
                mismatched.append(key)
            continue
        if None in (x, y, z):
            mismatched.append(key)
            continue
        fx, fy, fz = json_object(x), json_object(y), json_object(z)
        if fx is None or fy is None:
            left_out.append(key)
            continue
        varying = sorted(f for f in set(fx) | set(fy) if fx.get(f) != fy.get(f))
        left_out += ['%s.%s' % (key, f) for f in varying]
        if fz is None or {f: v for f, v in fx.items() if f not in varying} != {f: v for f, v in fz.items() if f not in varying}:
            mismatched.append(key)
    return left_out, mismatched


FTS_SHADOWS = ('_data', '_idx', '_docsize', '_config', '_content')       # the shadow tables of an FTS5 table
MATCH_QUERIES = 400


def fts5_tables(path, rebuildable=False):
    """{FTS5 table: [its shadow tables]} of a store, read from its schema. `rebuildable` leaves out the contentless ones (content=''):
    they keep no text to rebuild the index from, so only an exact comparison of their shadow tables is possible."""
    with closing(B.open_readonly(path)) as connection:
        master = connection.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'").fetchall()
    names = {name for name, _sql in master}
    return {name: [name + s for s in FTS_SHADOWS if name + s in names] for name, sql in master
            if re.search(r'\bUSING\s+fts5\b', sql or '', re.IGNORECASE)
            and not (rebuildable and re.search(r'''\bcontent\s*=\s*(''|"")''', sql, re.IGNORECASE))}


def rebuilt_shadows(label, path, fts, shadows, work):
    """Digests of `shadows` in a scratch copy of the store (in `work`) after FTS5 'rebuild' by this process's SQLite library."""
    copy = Path(work) / (label + '.sqlite3')
    shutil.copyfile(path, copy)
    with closing(sqlite3.connect(copy)) as connection, connection:
        connection.execute("INSERT INTO %s(%s) VALUES('rebuild')" % (B.quote(fts), B.quote(fts)))
    with closing(B.open_readonly(copy)) as connection:
        return {t: B.table_digest(connection, t) for t in shadows}


def query_words(path, fts, count=MATCH_QUERIES):
    """The words the MATCH queries ask for: tokens of one store's indexed text, sorted, shuffled with a fixed seed, `count` of them."""
    words = set()
    with closing(B.open_readonly(path)) as connection:
        for row in connection.execute('SELECT * FROM %s' % B.quote(fts)):
            words.update(w for w in re.findall(r'\w+', ' '.join(str(v) for v in row if v is not None).lower()) if len(w) > 2)
    words = sorted(words)
    random.Random(7).shuffle(words)
    return words[:count]


def match_answers(path, fts, words):
    """(rows, sha256 of the rowids) of `fts MATCH "<word>"` for each word, on the store as it is (not rebuilt)."""
    with closing(B.open_readonly(path)) as connection:
        found = [connection.execute('SELECT rowid FROM %s WHERE %s MATCH ? ORDER BY rowid' % (B.quote(fts), B.quote(fts)),
                                    ('"%s"' % w,)).fetchall() for w in words]
    return [(len(rows), B.sha256_text(repr(rows))) for rows in found]


def fts_parity(paths, fts, shadows):
    """(ok, detail) for the FTS5 table `fts` in the stores `paths` {label: path}, 'A1' among them: (i) its shadow tables are equal after
    FTS5 'rebuild' by one library on scratch copies, and (ii) MATCH_QUERIES MATCH queries return the same rowids on the stores as built.
    A contentless table cannot be rebuilt, so it is not comparable this way and fails (its shadow tables are exactly equal, or they are not)."""
    if fts not in fts5_tables(paths['A1'], rebuildable=True):
        return False, ('FTS5 %s is contentless: it cannot be rebuilt, so its shadow tables must be exactly equal and they are not, '
                       'in table(s): %s' % (fts, ', '.join(shadows)))
    with tempfile.TemporaryDirectory(prefix='fts5-rebuild-', ignore_cleanup_errors=True) as work:
        rebuilt = {label: rebuilt_shadows(label, path, fts, shadows, work) for label, path in paths.items()}
    words = query_words(paths['A1'], fts)
    answers = {label: match_answers(path, fts, words) for label, path in paths.items()}
    differ = sorted(t for t in shadows if any(rebuilt[label][t] != rebuilt['A1'][t] for label in paths))
    same = sum(all(answers[label][i] == answers['A1'][i] for label in paths) for i in range(len(words)))
    if not differ and same == len(words):
        return True, 'FTS5 %s: shadow tables equal after rebuild (SQLite %s), %d of %d MATCH queries identical' % (
            fts, sqlite3.sqlite_version, same, len(words))
    return False, 'FTS5 %s is not equivalent: (i) rebuilt shadow tables %s, (ii) %d of %d MATCH queries identical, in table(s): %s' % (
        fts, 'differ' if differ else 'equal', same, len(words), ', '.join(differ or shadows))


def change_one_indexed_name(path):
    """Negative control for FTS5: add a word to one indexed value (the last row of the first FTS5 table) and bring the index in line with
    it, so the stored text and the index, as built and as rebuilt, differ from the Windows build. Returns (FTS5 table, table holding the
    text, column)."""
    fts = next(iter(fts5_tables(path, rebuildable=True)), None)
    if fts is None:
        raise ValueError('no FTS5 table with content in %s' % path)
    with closing(sqlite3.connect(path)) as connection, connection:
        sql = connection.execute('SELECT sql FROM sqlite_master WHERE name = ?', (fts,)).fetchone()[0]
        column = connection.execute('PRAGMA table_info(%s)' % B.quote(fts)).fetchone()[1]
        external = re.search(r'content\s*=\s*[\'"]?(\w+)', sql, re.IGNORECASE)
        text = external.group(1) if external else fts
        connection.execute("UPDATE %s SET %s = coalesce(%s, '') || ' zzqx' WHERE rowid = (SELECT max(rowid) FROM %s)" % (
            B.quote(text), B.quote(column), B.quote(column), B.quote(text)))
        if external:
            connection.execute("INSERT INTO %s(%s) VALUES('rebuild')" % (B.quote(fts), B.quote(fts)))
    return fts, text, column


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
    fts = fts5_tables(paths['B'])                 # the schema is the same in all three (schema_sha256 above)
    names = (set(tables['A1']) | set(tables['A2']) | set(tables['B'])) - {t for shadows in fts.values() for t in shadows}
    noise = sorted(t for t in names if tables['A1'].get(t) != tables['A2'].get(t))
    differ = sorted(t for t in names if t not in noise and tables['A1'].get(t) != tables['B'].get(t))
    if differ:
        return False, 'B differs from the Windows build in table(s): ' + ', '.join(differ)
    left_out = [t for t in noise if t not in KV_TABLES]
    for kv in KV_TABLES:
        if kv in noise:                           # refine the mask: the stamps vary, the facts beside them must match
            pairs = [key_values(paths[label], kv) for label in ('A1', 'A2', 'B')]
            if None in pairs:
                left_out.append(kv)
            else:
                keys, mismatched = metadata_variance(*pairs, measured=MEASURED.get(kv))
                if mismatched:
                    return False, 'B differs from the Windows build in table(s): %s[%s]' % (kv, ', '.join(mismatched))
                left_out.append('%s[%s]' % (kv, ', '.join(keys)))
    notes = []
    for table, shadows in fts.items():            # physical index structures: exact when they can be, else by content (fts_parity)
        raw = {label: [tables[label][t] for t in shadows] for label in tables}
        if raw['A1'] == raw['B']:
            notes.append('FTS5 %s: shadow tables exactly equal' % table)
            continue
        ok, detail = fts_parity({label: paths[label] for label in ('A1', 'B') + (('A2',) if raw['A2'] != raw['A1'] else ())}, table, shadows)
        if not ok:
            return False, 'B differs from the Windows build: ' + detail
        notes.append(detail)
    rows = sum(t['rows'] for t in tables['B'].values())
    return True, '; '.join(['%d tables, %s rows; run-to-run variance (left out): %s' % (len(tables['B']), '{:,}'.format(rows),
                                                                                       ', '.join(left_out) or 'none')] + notes)


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


def compare_zip(name, a1, a2, b):
    """(ok, detail) for a zip: byte for byte when the two Windows builds agree on the bytes (the bundle: its writer stamps every member
    the same). When they do not (ingest and extend stamp `created_at` into manifest.json and the members with the time of writing),
    member by member: the same names, the same bytes, and for a member that differs between A1 and A2 only its JSON fields that
    differ are left out: every other field must equal B. A member that differs and is not a JSON object makes the reference unstable."""
    paths = {label: Path(folder) / name for label, folder in (('A1', a1), ('A2', a2), ('B', b))}
    missing = [label for label, path in paths.items() if not path.is_file()]
    if missing:
        return False, 'missing in ' + ', '.join(missing)
    shas = {label: B.sha256_file(path) for label, path in paths.items()}
    if shas['A1'] == shas['A2']:
        if shas['A1'] != shas['B']:
            return False, 'B differs from the Windows build byte for byte'
        return True, 'byte-identical, sha256 %s' % shas['B'][:12]
    try:
        with zipfile.ZipFile(paths['A1']) as z1, zipfile.ZipFile(paths['A2']) as z2, zipfile.ZipFile(paths['B']) as zb:
            if sorted(z1.namelist()) != sorted(z2.namelist()):
                return False, 'the two Windows builds differ in the names of their members: the reference is not stable'
            if sorted(z1.namelist()) != sorted(zb.namelist()):
                return False, 'B differs from the Windows build in the names of its members'
            left_out, mismatched, same = [], [], 0
            for member in sorted(z1.namelist()):
                x, y, z = z1.read(member), z2.read(member), zb.read(member)
                if x == y:
                    if x == z:
                        same += 1
                    else:
                        mismatched.append(member)
                    continue
                fx, fy, fz = json_object(x), json_object(y), json_object(z)
                if fx is None or fy is None:
                    return False, 'the two Windows builds differ in %s, which is not a JSON object: the reference is not stable' % member
                varying = sorted(f for f in set(fx) | set(fy) if fx.get(f) != fy.get(f))
                left_out.append('%s[%s]' % (member, ', '.join(varying)))
                if fz is None or {f: v for f, v in fx.items() if f not in varying} != {f: v for f, v in fz.items() if f not in varying}:
                    mismatched.append(member)
    except (zipfile.BadZipFile, OSError) as error:
        return False, 'the two Windows builds differ byte for byte and a zip cannot be read (%s): the reference is not stable' % type(error).__name__
    if mismatched:
        return False, 'B differs from the Windows build in member(s): ' + ', '.join(mismatched)
    return True, '%d members identical, %d with run-to-run variance (left out): %s' % (same, len(left_out), ', '.join(left_out))


def compare(name, a1, a2, b):
    if name.endswith('.sqlite3'):
        return compare_store(name, a1, a2, b)
    return (compare_zip if name.endswith('.zip') else compare_file)(name, a1, a2, b)


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


def step_value(value):
    """(the value changed by its smallest step, what that step is): a REAL by one ULP, an INTEGER by 1, a TEXT or BLOB in its last bit."""
    if isinstance(value, float):
        stepped = math.nextafter(value, math.inf)
        return (stepped if stepped != value else math.nextafter(value, -math.inf)), 'REAL by one ULP'
    if isinstance(value, int):
        return value ^ 1, 'INTEGER by 1'
    if isinstance(value, str):
        return (value[:-1] + chr(ord(value[-1]) ^ 1) if value else 'x'), 'TEXT in its last bit'
    return (value[:-1] + bytes([value[-1] ^ 1]) if value else b'\x00'), 'BLOB in its last bit'


def perturb_one_value(path, skip=()):
    """Negative control: change ONE stored value by its smallest step (step_value) in a copy of a store. Takes the last row of the first
    ordinary table (not in `skip`, nor virtual, nor a shadow of one) that has a non-key column; a REAL cell by preference, else an
    INTEGER, TEXT or BLOB one. Returns (table, column, what)."""
    tables = B.store_digest(path)['tables']
    virtual = [name for name, digest in tables.items() if digest.get('virtual')]
    candidates = []
    with closing(sqlite3.connect(path)) as connection:
        for table, digest in sorted(tables.items()):
            if not digest['rows'] or table in skip or digest.get('virtual') or any(table.startswith(v + '_') for v in virtual):
                continue
            try:
                keys = {row[1] for row in connection.execute('PRAGMA table_info(%s)' % B.quote(table)) if row[5]}
                cursor = connection.execute('SELECT rowid, * FROM %s WHERE rowid = (SELECT max(rowid) FROM %s)' % (B.quote(table), B.quote(table)))
                row = cursor.fetchone()
            except sqlite3.Error:                                   # WITHOUT ROWID: no rowid to find the row by
                continue
            names = [c[0] for c in cursor.description][1:]
            for column, value in zip(names, row[1:]):
                if column not in keys and value is not None:
                    rank = 0 if isinstance(value, float) else 1 if isinstance(value, int) else 2 if isinstance(value, str) else 3
                    candidates.append((rank, len(candidates), table, column, row[0], value))
    for _rank, _order, table, column, rowid, value in sorted(candidates):
        stepped, what = step_value(value)
        with closing(sqlite3.connect(path)) as connection, connection:
            try:
                connection.execute('UPDATE %s SET %s = ? WHERE rowid = ?' % (B.quote(table), B.quote(column)), (stepped, rowid))
                return table, column, what
            except sqlite3.Error:
                continue
    raise ValueError('no value to change in %s' % path)


def differing_tables(detail):
    """The tables a failed compare_store detail names ('... in table(s): a, metadata[k1, k2], b'); [] for any other failure."""
    if 'table(s): ' not in detail:
        return []
    listed = re.sub(r'\[[^\]]*\]', '', detail.split('table(s): ', 1)[1])
    return [name.strip() for name in listed.split(',') if name.strip()]


def control_detected(ok, detail, table, already=()):
    """A negative control counts as detected only when the comparison fails BECAUSE of the table that was changed: it names that table,
    and that table did not differ before (`already`). A store that fails for another reason must not make a control look caught."""
    return not ok and table in differing_tables(detail) and table not in already


def flip_one_byte(path):
    """Negative control for any other file: change one byte in the middle of it (one bit). Returns the offset."""
    data = bytearray(Path(path).read_bytes())
    at = len(data) // 2
    data[at] ^= 0x01
    Path(path).write_bytes(bytes(data))
    return at


def change_one_member(path):
    """Negative control for a zip: flip one bit in the middle of its largest member other than manifest.json (whose stamps the
    comparison may leave out) and write the zip again with the same names, order and stamps. Returns the member."""
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        data = {info.filename: archive.read(info.filename) for info in infos}
    member = max((info.filename for info in infos if info.filename != 'manifest.json'), key=lambda n: len(data[n]))
    blob = bytearray(data[member])
    blob[len(blob) // 2] ^= 0x01
    data[member] = bytes(blob)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for info in infos:
            fresh = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            fresh.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(fresh, data[info.filename])
    return member


# ---- the two builds ---------------------------------------------------------------------------------------------------

def windows_env(image, out=None):
    """The environment of a Windows build step. The score steps run in the interpreter's own world, as the documented command line of
    shown_areas.py and build_scores_v3.py has it (`env -u PYTHONPATH PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 <python>`)."""
    env = {k: v for k, v in os.environ.items() if k not in ('CU_DATA_DIR', 'CU_DISTRICT', 'CU_SECRET_KEY', 'CU_STATE_DIR', 'TERRIER_LOCAL_DIR')}
    if out is not None:
        env['TERRIER_LOCAL_DIR'] = str(out)
    if image == 'score':
        env.pop('PYTHONPATH', None)
        env.update(PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
    return env


def windows_build(pythons, repo, district, steps, out):
    """Each step in its own interpreter: pythons = {'geo': work venv, 'score': system Python 3.12}."""
    for step in steps:
        image = S.STEPS[step].image
        subprocess.run([pythons[image], str(repo / 'tools' / 'build_step.py'), 'run', step, '--district', district],
                       cwd=str(repo), env=windows_env(image, out), check=True)


def windows_fingerprint(pythons, repo, images):
    return {image: json.loads(subprocess.run([pythons[image], str(repo / 'tools' / 'build_step.py'), 'fingerprint'], cwd=str(repo),
                                             env=windows_env(image), check=True, capture_output=True, text=True).stdout)
            for image in images}


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


def resolve_images(toolchains, explicit, fail):
    """({toolchain: image}, {toolchain: 'explicit' | 'manifest'}) for `toolchains`: the digest in `explicit` ({toolchain: ref or None}) when
    there is one, else the verified release manifest's. `fail(message)` stops on an image that is not pinned by digest."""
    images, sources = {}, {}
    for toolchain in toolchains:
        images[toolchain], sources[toolchain] = ((explicit[toolchain], 'explicit') if explicit[toolchain]
                                                 else (manifest_image(IMAGE_NAMES[toolchain]), 'manifest'))
        if '@sha256:' not in images[toolchain]:
            fail('the %s image (%s) must be pinned by digest: ghcr.io/pjg93-222/%s@sha256:...' % (
                toolchain, '--image or $TERRIER_BUILD_IMAGE' if toolchain == 'geo' else '--score-image or $TERRIER_SCORE_IMAGE',
                IMAGE_NAMES[toolchain]))
    return images, sources


def store_names(district, steps, extra=(), keep=False):
    """The files a run of `steps` leaves in the data folder, relative to the folder that holds the stores (co-05: the folder itself):
    each step's store and `also`, plus `extra`, and its `keep` files (receipts: exported, never compared) for `keep`. A name with `*`
    stands for the files it matches (expand_names); a file two steps name (street, changed by contacts) is listed once."""
    sub = D.data_dir(district, Path('.'))
    found = [f for s in steps for f in (S.STEPS[s].store, *S.STEPS[s].also, *(S.STEPS[s].keep if keep else ()))] + list(extra)
    return list(dict.fromkeys((sub / f).as_posix() for f in found))


def expand_names(folder, names):
    """`names` with each `*` name replaced by the files it matches in `folder`; a name that matches nothing is an error."""
    found = []
    for name in names:
        matches = S.expand(folder, name)
        if not matches:
            raise SystemExit('compute_parity: %s matches nothing in %s' % (name, folder))
        found += matches
    return list(dict.fromkeys(found))


def link_or_copy(source, target):
    """A hard link to `source` where the file system allows one (one disk copy for A1 and A2), else a copy."""
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


def copy_inputs(folder, district, names, source, link=False):
    """Windows side of --inputs-from: `names` (files or folders of the data folder) copied from the finished run in `source` into `folder`.
    `link`: hard links instead of copies. Only for steps that never write what they read (walk, ingest, extend, bundle, density open their
    inputs read-only); contacts changes street.sqlite3 in place, which would change the source too."""
    origin, data = D.data_dir(district, Path(source)), D.data_dir(district, Path(folder))
    data.mkdir(parents=True, exist_ok=True)
    for name in names:
        if not (origin / name).exists():
            raise SystemExit('compute_parity: --inputs-from has no %s' % name)
        if (origin / name).is_dir():
            shutil.copytree(origin / name, data / name, **({'copy_function': link_or_copy} if link else {}))
        else:
            (link_or_copy if link else shutil.copyfile)(origin / name, data / name)


def docker_exe():
    docker = shutil.which('docker')
    if not docker:
        raise SystemExit('compute_parity: docker is not on PATH (Docker Desktop is not installed or not running)')
    return docker


def export_image(images):
    """The image that exports (stdlib only, so either does): the geo image when the run has one."""
    return images.get('geo') or images['score']


def compose(images, repo, out, run_id, *args, capture=False, files=()):
    """`images`: {'geo': ref, 'score': ref}, the toolchains the run needs. Compose parses every service, so both variables are set; the one
    the run does not need is given the other image (its service is never started)."""
    env = {**os.environ, 'TERRIER_BUILD_IMAGE': images.get('geo') or images['score'],
           'TERRIER_SCORE_IMAGE': images.get('score') or images['geo'], 'HOST_REPO': str(repo), 'HOST_OUT': str(out), 'RUN_ID': run_id}
    overrides = [part for f in files for part in ('-f', str(f))]
    return subprocess.run([docker_exe(), 'compose', '-f', str(COMPOSE_FILE), *overrides, '--profile', 'terrier', *args], env=env,
                          check=True, capture_output=capture, text=True).stdout


def export_via_volume(images, repo, out, run_id, names):
    """The export with /out in a named volume (compute.compose.yaml), then `docker cp` into `out`. The volume is created here
    and given to uid 10001 (a new volume is root's), and is removed again, also when the export fails."""
    docker, volume, holder, image = docker_exe(), 'terrier-export-' + run_id, 'terrier-export-cp-' + run_id, export_image(images)

    def run(*argv):
        subprocess.run([docker, *argv], check=True, stdout=subprocess.DEVNULL)

    run('volume', 'create', volume)
    try:
        run('run', '--rm', '--pull', 'never', '--network', 'none', '--user', '0', '-v', volume + ':/out', '--entrypoint', 'chown',
            image, '10001:10001', '/out')
        compose(images, repo, out, run_id, 'run', '--rm', 'terrier-export', 'export', '/out', *names)
        run('create', '--name', holder, '--pull', 'never', '--network', 'none', '-v', volume + ':/out:ro', image)
        try:
            run('cp', holder + ':/out/.', str(out))
        finally:
            run('rm', '-v', holder)
    finally:
        run('volume', 'rm', volume)


def seed_stage(images, repo, out, run_id, district, source, names):
    """Copy `names` (files or folders of a data folder) from the host folder `source` into this run's folder of the stage volume, for the
    steps that read stores no step of the run makes. They cross as a tar stream on the stdin of one container (`docker cp` into a volume
    ran at 1 MB/s on this machine, the stream at 5): nothing is mounted, `source` is only read (it may be a live .local), and the copies
    are given to the build user (uid 10001) before anything builds."""
    docker, image = docker_exe(), export_image(images)
    target = '/stage/' + run_id + ('/' + district['data_dir'] if district['data_dir'] else '')
    origin = D.data_dir(district, Path(source))
    for name in names:
        if not (origin / name).exists():
            raise SystemExit('compute_parity: the inputs folder has no %s' % name)
    if subprocess.run([docker, 'volume', 'inspect', STAGE_VOLUME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        compose(images, repo, out, run_id, 'run', '--rm', SERVICES['geo' if 'geo' in images else 'score'], 'fingerprint', capture=True)
    script = 'mkdir -p %s && tar xf - -C %s && chown -R 10001:10001 /stage/%s' % (target, target, run_id)
    receiver = subprocess.Popen([docker, 'run', '-i', '--rm', '--pull', 'never', '--network', 'none', '--user', '0', '-v',
                                 STAGE_VOLUME + ':/stage', '--entrypoint', 'sh', image, '-c', script],
                                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    try:
        with tarfile.open(fileobj=receiver.stdin, mode='w|') as archive:
            for name in names:
                archive.add(str(origin / name), arcname=name)
    finally:
        receiver.stdin.close()
        code = receiver.wait()
    if code:
        raise subprocess.CalledProcessError(code, 'docker run -i (the stage seeding)')


def drop_stage(images, run_id):
    """Remove this run's folder from the stage volume: the stores in it are voter-derived and the export already holds them. Only
    the folder of this run id; the volume and any other run stay."""
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*', run_id):
        raise ValueError('not a run id: %r' % run_id)
    subprocess.run([docker_exe(), 'run', '--rm', '--pull', 'never', '--network', 'none', '--user', '0', '-v', STAGE_VOLUME + ':/stage',
                    '--entrypoint', 'sh', export_image(images), '-c', 'rm -rf /stage/' + run_id], check=True, stdout=subprocess.DEVNULL)


def container_build(images, repo, district, steps, out, run_id, names, export_bind=False):
    """Each step in the service of its toolchain (SERVICES), all into the same stage volume, then the export."""
    for step in steps:
        compose(images, repo, out, run_id, 'run', '--rm', SERVICES[S.STEPS[step].image], 'run', step, '--district', district)
    if export_bind:
        compose(images, repo, out, run_id, 'run', '--rm', 'terrier-export', 'export', '/out', *names, files=[EXPORT_BIND_FILE])
    else:
        export_via_volume(images, repo, out, run_id, names)


def container_fingerprint(images, repo, out, run_id):
    return {image: json.loads(compose(images, repo, out, run_id, 'run', '--rm', SERVICES[image], 'fingerprint',
                                      capture=True).splitlines()[-1]) for image in images}


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
    parser.add_argument('--steps', default='roads', help='comma list of %s, in build order (default roads, the smoke)' % ', '.join(S.STEPS))
    parser.add_argument('--files', default='', help='comma list of other files to compare byte for byte (relative to the data folder)')
    parser.add_argument('--link-inputs', action='store_true',
                        help='with --inputs-from: hard-link the inputs into A1 and A2 instead of copying them (disk space); refused for a step that '
                             'writes a store it reads (contacts)')
    parser.add_argument('--inputs-from', type=Path, default=None,
                        help='a finished run\'s folder (for example an earlier A1): the stores the chosen steps read and none of them makes are '
                             'copied from it into A1, A2 and the stage of B, so the steps are judged from one set of inputs')
    parser.add_argument('--python', default=os.environ.get('CU_PYTHON') or str(H.WORK_PY),
                        help='the Windows interpreter of the geo steps (roads, street, contacts)')
    parser.add_argument('--score-python', default=str(H.PY312),
                        help='the Windows interpreter of the score steps (shown, scores): the system Python 3.12 ($TERRIER_PY312)')
    parser.add_argument('--image', default=os.environ.get('TERRIER_BUILD_IMAGE'),
                        help='terrier-build image, pinned by digest (default: $TERRIER_BUILD_IMAGE, else the verified release manifest)')
    parser.add_argument('--score-image', default=os.environ.get('TERRIER_SCORE_IMAGE'),
                        help='terrier-build-score image, pinned by digest (default: $TERRIER_SCORE_IMAGE, else the verified release manifest)')
    parser.add_argument('--repo', type=Path, default=REPO, help='the tree both builds run (default: this one)')
    parser.add_argument('--api', action='store_true', help='also run co_baseline\'s golden endpoint check on A1 and B (co-05, full set)')
    parser.add_argument('--negative-control', action='store_true', help='also prove the comparison fails when one row is removed')
    parser.add_argument('--skip-container', action='store_true', help='Windows only: learn the run-to-run variance (A1 vs A2)')
    parser.add_argument('--reuse-windows', action='store_true', help='keep an existing <scratch>/A1 and A2 instead of building them again')
    parser.add_argument('--reuse-container', action='store_true',
                        help='with --reuse-windows: keep an existing <scratch>/B too and only judge the three again (a changed comparison rule); '
                             'images, if given, are only recorded and used for the fingerprint')
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
    if args.reuse_container and (not args.reuse_windows or args.skip_container or not (scratch / 'B').is_dir()):
        parser.error('--reuse-container needs --reuse-windows, an existing <scratch>/B, and no --skip-container')
    build_container = not args.skip_container and not args.reuse_container
    toolchains = [t for t in IMAGE_NAMES if any(S.STEPS[s].image == t for s in steps)]          # geo first
    pythons = {'geo': args.python, 'score': args.score_python}
    images, sources = {}, {}
    if not args.skip_container:
        explicit = {'geo': args.image, 'score': args.score_image}
        # a judgement of finished builds needs no image: only the ones that are given are recorded
        images, sources = resolve_images([t for t in toolchains if explicit[t] or not args.reuse_container], explicit, parser.error)
    d = D.get(args.district)
    needed = S.inputs_needed(steps, d)
    if needed and not args.inputs_from:
        parser.error('%s read %s, which no step of this run makes: run the steps that make it too, or pass --inputs-from <a finished '
                     'run\'s folder>' % (', '.join(s for s in steps if S.step_needs(S.STEPS[s], d)), ', '.join(needed)))
    if args.inputs_from and not needed:
        parser.error('--inputs-from: these steps read nothing that they do not make themselves')
    if args.link_inputs and (not args.inputs_from or 'contacts' in steps):
        parser.error('--link-inputs needs --inputs-from and a run without contacts (which changes street.sqlite3 in place)')
    names = store_names(d, steps, [f for f in args.files.split(',') if f])
    folders = {label: scratch / label for label in ('A1', 'A2', 'B')}
    run_id = 'B-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    report = {'district': d['id'], 'steps': steps, 'images': images, 'image_sources': sources,
              'python': {t: pythons[t] for t in toolchains}, 'results': []}

    if not os.environ.get('TERRIER_DEMO_KEY'):       # one throwaway demo key for A1, A2 and B; kept in the scratch folder for --reuse-windows
        scratch.mkdir(parents=True, exist_ok=True)
        key_file = scratch / 'demo_key.hex'
        if not key_file.exists():
            key_file.write_text(os.urandom(32).hex(), encoding='utf-8')
        os.environ['TERRIER_DEMO_KEY'] = key_file.read_text(encoding='utf-8').strip()

    if any(S.STEPS[s].walk_salt for s in steps) and not os.environ.get('TERRIER_WALK_SALT'):      # likewise one throwaway walk salt
        scratch.mkdir(parents=True, exist_ok=True)
        salt_file = scratch / 'walk_salt.hex'
        if not salt_file.exists():
            salt_file.write_text(os.urandom(32).hex(), encoding='utf-8')
        os.environ['TERRIER_WALK_SALT'] = salt_file.read_text(encoding='utf-8').strip()
    report['inputs_from'] = str(args.inputs_from) if args.inputs_from else None
    report['inputs'] = needed

    for label in ('A1', 'A2'):
        if args.reuse_windows and folders[label].is_dir():
            print('%s: reusing %s' % (label, folders[label]), flush=True)
            continue
        print('%s: Windows build of %s' % (label, ', '.join(steps)), flush=True)
        folders[label].mkdir(parents=True, exist_ok=True)
        if needed:
            print('%s: inputs %s %s from %s' % (label, ', '.join(needed), 'hard-linked' if args.link_inputs else 'copied', args.inputs_from), flush=True)
            copy_inputs(folders[label], d, needed, args.inputs_from, link=args.link_inputs)
        windows_build(pythons, args.repo, d['id'], steps, folders[label])
    names = expand_names(folders['A1'], names)                       # a `*` name (a bundle's file name is its snapshot key) as A1 made it
    report['fingerprint_windows'] = windows_fingerprint(pythons, args.repo, toolchains)
    if args.reuse_container:
        print('B: reusing %s' % folders['B'], flush=True)
        if len(images) == len(toolchains):
            report['fingerprint_container'] = container_fingerprint(images, args.repo, folders['B'], run_id)
    elif build_container:
        print('B: container build of %s (stage volume %s)' % (', '.join(steps), run_id), flush=True)
        folders['B'].mkdir(parents=True, exist_ok=True)
        if needed:
            print('B: inputs %s copied from %s into the stage' % (', '.join(needed), args.inputs_from), flush=True)
            seed_stage(images, args.repo, folders['B'], run_id, d, args.inputs_from, needed)
        container_build(images, args.repo, d['id'], steps, folders['B'], run_id, names, export_bind=args.export_bind)
        report['fingerprint_container'] = container_fingerprint(images, args.repo, folders['B'], run_id)

    def verdict(name, ok, detail):
        print('%-5s %-34s %s' % ('PASS' if ok else 'FAIL', name, detail), flush=True)
        report['results'].append({'artifact': name, 'ok': ok, 'detail': detail})

    before, passed = {}, {}                  # artifact -> the tables that already differ (what a negative control must not rely on); -> did it pass
    for name in names:
        if args.skip_container:
            ok, detail = compare(name, folders['A1'], folders['A2'], folders['A2'])
            detail = '(Windows only) ' + detail
        else:
            ok, detail = compare(name, folders['A1'], folders['A2'], folders['B'])
        before[name], passed[name] = differing_tables(detail), ok
        verdict(name, ok, detail)
        note = baseline_note(name, folders['A1']) if name.endswith('.sqlite3') and d['id'] == D.DEFAULT else None
        if note:
            print('      %-34s %s' % ('', note), flush=True)
    if not args.skip_container and args.negative_control:
        for name in names:
            control = scratch / 'NC'
            target = control / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(folders['B'] / name, target)
            if name.endswith('.sqlite3'):
                skip = noise_tables(name, folders['A1'], folders['A2'])
                table = remove_one_row(target, skip)
                found = control_detected(*compare_store(name, folders['A1'], folders['A2'], control), table, before[name])
                verdict('negative control ' + name, found, 'one row removed from %s: %s' % (table, 'detected' if found else 'NOT detected'))
                shutil.copyfile(folders['B'] / name, target)
                table, column, step = perturb_one_value(target, skip)
                found = control_detected(*compare_store(name, folders['A1'], folders['A2'], control), table, before[name])
                verdict('negative control ' + name + ' (value)', found, 'one %s.%s value changed (%s): %s' % (
                    table, column, step, 'detected' if found else 'NOT detected'))
                fts = fts5_tables(folders['B'] / name, rebuildable=True)
                if fts:                                  # the FTS5 comparison itself must catch a changed indexed name
                    shutil.copyfile(folders['B'] / name, target)
                    index, text, column = change_one_indexed_name(target)
                    found = not fts_parity({'A1': folders['A1'] / name, 'B': target}, index, fts[index])[0]
                    verdict('negative control ' + name + ' (indexed name)', found, 'one %s.%s indexed value changed, index kept in line: %s' % (
                        text, column, 'detected' if found else 'NOT detected'))
            elif not passed[name]:                   # a file that already differs would make any control look caught
                print('      negative control %s: not run, the comparison itself already fails' % name, flush=True)
            elif name.endswith('.zip'):
                what, ok = 'one bit of %s changed' % change_one_member(target), compare_zip(name, folders['A1'], folders['A2'], control)[0]
                verdict('negative control ' + name, not ok, '%s: %s' % (what, 'detected' if not ok else 'NOT detected'))
            else:
                what, ok = 'one byte changed at offset %d' % flip_one_byte(target), compare_file(name, folders['A1'], folders['A2'], control)[0]
                verdict('negative control ' + name, not ok, '%s: %s' % (what, 'detected' if not ok else 'NOT detected'))
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
