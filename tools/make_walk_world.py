"""Build a complete synthetic Terrier data directory for the walk-list browser checks. Nothing here is real.

    <app venv python> tools/make_walk_world.py <out_dir> [--force]

<out_dir> becomes a data directory the real app boots on with CU_DEMO=0 (point CU_DATA_DIR at it, never at .local):
  contacts / scores / street .sqlite3, source.csv    server.research.v3_testkit.build_world: 1,500 invented people, as the
  and the cu-summary-v4 bundle (imported, active)    Django tests use them
  roads.sqlite3, walk.sqlite3                        server.research.walk_testkit's hand case (38 voters, 27 doors, 5 streets,
                                                     3 precincts): tools/build_walk.py runs on the testkit's own minimal
                                                     stores in a temporary directory, then its person ids are re-keyed onto
                                                     contacts rows 1..38 of the world, whose contact fields (name, emails,
                                                     phones, addresses, search index) take the testkit's invented values, so
                                                     names and phones resolve through the world's contacts store. Scores and
                                                     street rows of those 38 keep the fixture's values (walk.person carries
                                                     its own age and party)
  accounts.sqlite3, .secret                          migrations applied, local administrator created by one loopback API call
The invented walk world sits under two plan gates on purpose (see RELAXED in tools/test_walk_build.py); the build relaxes
exactly those two, as the tests do. Two runs give the same directory byte for byte except accounts.sqlite3 and .secret (the
build times of scores.sqlite3 and walk.sqlite3 and the walk build's timings are fixed); the summary prints data file sha256s.
An existing non-empty <out_dir> is refused unless it holds this tool's marker file and --force is given. Output: counts only.

Serve it (8768 is for synthetic worlds; 8767 is the real app and is never touched):
    CU_DATA_DIR=<out_dir> CU_DEMO=0 CU_COOKIE_PREFIX=cu_walk_ <venv python> manage.py serve --port 8768
The accounts database is migrated as of the build: when a migration is added later (the saved walk lists and results are
Django models), run manage.py migrate with the same CU_DATA_DIR, or rebuild with --force.
"""
import os

os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('OMP_NUM_THREADS', '1')

import argparse     # noqa: E402
import hashlib      # noqa: E402
import json         # noqa: E402
import logging      # noqa: E402
import re           # noqa: E402
import shutil       # noqa: E402
import sqlite3      # noqa: E402
import sys          # noqa: E402
import tempfile     # noqa: E402
from contextlib import closing  # noqa: E402
from pathlib import Path        # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SCHEMA = 'walk-world-1'
MARKER = '.walk-world.json'
BUILT_UTC = '2026-10-01T00:00:00+00:00'
SALT = hashlib.sha256(b'cu-geography-v3 walk world salt').hexdigest().encode('ascii')     # .walk-salt: 64 hex characters
RELAXED = {'largest_component': 0.85, 'abs_share': 0.65}
DATA_FILES = ('contacts.sqlite3', 'scores.sqlite3', 'street.sqlite3', 'roads.sqlite3', 'walk.sqlite3')
CONTACT_COLUMNS = ('name', 'email', 'email_match', 'phone', 'wireless', 'wireless_match', 'registration', 'residential')


class WorldError(RuntimeError):
    """A failed step; messages carry counts and file names only."""


def sha256_of(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def prepare(out, force):
    """Create <out> (empty) and its marker; clear a world this tool made before when asked to."""
    local = (ROOT / '.local').resolve()
    if out == local or local in out.parents:
        raise WorldError('%s is the real data directory; pick another one' % out)
    if out.exists() and not out.is_dir():
        raise WorldError('%s is not a directory' % out)
    if out.is_dir() and any(out.iterdir()):
        if not (force and (out / MARKER).is_file()):
            raise WorldError('%s is not empty (--force rebuilds a directory this tool made)' % out)
        try:
            for child in out.iterdir():
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        except OSError as error:
            raise WorldError('Could not clear %s (%s); is a server still running on it?' % (out, type(error).__name__)) from None
    out.mkdir(parents=True, exist_ok=True)
    (out / MARKER).write_text(json.dumps({'schema': SCHEMA}), encoding='utf-8')


def boot(out):
    """Settings read CU_DATA_DIR at import: set it (and the real mode) before Django starts."""
    os.environ.update(CU_DATA_DIR=str(out), CU_DEMO='0', CU_LOCAL_ADMIN='1', DJANGO_SETTINGS_MODULE='server.settings')
    os.environ.pop('CU_DEPLOYMENT', None)
    import django
    django.setup()
    logging.getLogger().setLevel(logging.WARNING)


def digits(text):
    return ''.join(c for c in text if c.isdigit())


def update_contacts(path, identities):
    """Give the contacts rows in `identities` ({world id: (name, email, email_match, phone, wireless, wireless_match,
    registration, residential)}) the testkit's values, rebuild the search index the way tools/build_contacts.py fills it and
    keep the stored totals true."""
    with closing(sqlite3.connect(path)) as db, db:
        db.executemany('UPDATE contacts SET %s WHERE id=?' % ', '.join(c + '=?' for c in CONTACT_COLUMNS),
                       [(*values, ident) for ident, values in identities.items()])
        db.execute('DROP TABLE search')
        db.execute("CREATE VIRTUAL TABLE search USING fts5(name,email,phone,address,content='')")
        db.executemany('INSERT INTO search(rowid,name,email,phone,address) VALUES (?,?,?,?,?)',
                       [(rowid, name, email, digits(phone) + ' ' + digits(wireless), registration + ' ' + residential)
                        for rowid, name, email, phone, wireless, registration, residential in db.execute(
                            'SELECT rowid,name,email,phone,wireless,registration,residential FROM contacts ORDER BY rowid')])
        records, with_email, with_phone, with_wireless = db.execute(
            "SELECT count(*), sum(email<>''), sum(phone<>''), sum(wireless<>'') FROM contacts").fetchone()
        db.execute("UPDATE metadata SET value=? WHERE key='totals'", (json.dumps(
            {'records': records, 'with_email': with_email, 'with_phone': with_phone, 'with_wireless': with_wireless}),))


def make_walk(out, world, work):
    """walk.sqlite3 and roads.sqlite3 into `out`, re-keyed onto contacts rows 1..38. Returns the summary numbers."""
    from server.research import walk_testkit as T
    from tools import build_walk as B

    stores = T.write_minimal_stores(work)
    (work / '.walk-salt').write_bytes(SALT)
    result = B.build(stores.source_csv, work, gates=RELAXED)
    if not (result['passed'] and result['replaced']):
        raise WorldError('The walk build failed its gates: ' + ', '.join(result['failed']))

    keys = stores.expected['keys']                              # {'p01': testkit person id, ...}
    contacts, street = out / 'contacts.sqlite3', out / 'street.sqlite3'
    with closing(sqlite3.connect(contacts)) as db:
        taken = [r[0] for r in db.execute('SELECT id FROM contacts ORDER BY rowid LIMIT ?', (len(keys),))]
    if len(taken) < len(keys):
        raise WorldError('The world has %d contacts, the walk case needs %d' % (len(taken), len(keys)))
    rekey = {keys[k]: taken[int(k[1:]) - 1] for k in sorted(keys)}
    with closing(sqlite3.connect(stores.contacts_path)) as db:
        identities = {rekey[ident]: (name, *rest) for ident, name, *rest in db.execute(
            'SELECT id, %s FROM contacts' % ', '.join(CONTACT_COLUMNS))}
    update_contacts(contacts, identities)
    with closing(sqlite3.connect(out / 'scores.sqlite3')) as db, db:       # the one value of the fixture that varies per run
        db.execute("UPDATE metadata SET value=? WHERE key='built_utc'", (json.dumps(BUILT_UTC),))

    shutil.copyfile(work / 'roads.sqlite3', out / 'roads.sqlite3')
    shutil.copyfile(work / '.walk-salt', out / '.walk-salt')
    walk = out / 'walk.sqlite3'
    shutil.copyfile(work / 'walk.sqlite3', walk)
    household = {}                      # the registration household each re-keyed person has in the world's street store
    with closing(sqlite3.connect(street)) as db:
        for ident in rekey.values():
            found = db.execute("SELECT p.household_id FROM people p JOIN households h ON h.id=p.household_id "
                               "AND h.basis='registration' WHERE p.id=?", (ident,)).fetchone()
            household[ident] = found[0] if found else None
    with closing(sqlite3.connect(walk)) as db, db:
        db.executemany('UPDATE person SET person_id=?, household_id=? WHERE person_id=?',
                       [(new, household[new], old) for old, new in rekey.items()])
        qa = json.loads(db.execute("SELECT value FROM meta WHERE key='qa'").fetchone()[0])
        qa['timing'] = dict.fromkeys(qa.get('timing', {}), 0)
        qa['peak_memory_mb'] = None
        db.executemany('UPDATE meta SET value=? WHERE key=?', [
            (world.fixture.source_sha, 'source_sha256'), (BUILT_UTC, 'built_utc'),
            (json.dumps(qa, sort_keys=True), 'qa')])
        if sha256_of(out / 'roads.sqlite3') != db.execute("SELECT value FROM meta WHERE key='roads_sha256'").fetchone()[0]:
            raise WorldError('roads.sqlite3 differs from the file the walk store was built on')
    return {'qa': qa, 'rekeyed': len(rekey)}


def check_stores(out):
    """Every walk voter is a named contact with a score row, and the walk and contact stores name one source file."""
    with closing(sqlite3.connect(out / 'walk.sqlite3')) as db:
        ids = [r[0] for r in db.execute('SELECT person_id FROM person ORDER BY person_id')]
        walk_source = db.execute("SELECT value FROM meta WHERE key='source_sha256'").fetchone()[0]
        areas = db.execute('SELECT count(DISTINCT ss), count(DISTINCT sh), count(DISTINCT precinct) FROM person').fetchone()
    marks = ','.join('?' * len(ids))
    with closing(sqlite3.connect(out / 'contacts.sqlite3')) as db:
        named = db.execute("SELECT count(*), sum(phone<>'' OR wireless<>'') FROM contacts WHERE name<>'' AND id IN (%s)"
                           % marks, ids).fetchone()
        contact_source = json.loads(db.execute("SELECT value FROM metadata WHERE key='source_sha256'").fetchone()[0])
    with closing(sqlite3.connect(out / 'scores.sqlite3')) as db:
        scored = db.execute('SELECT count(*) FROM person WHERE person_id IN (%s)' % marks, ids).fetchone()[0]
    if not (named[0] == scored == len(ids) > 0):
        raise WorldError('Not every walk voter is a named contact with a score row (%d walk, %d named, %d scored)'
                         % (len(ids), named[0], scored))
    if walk_source != contact_source:
        raise WorldError('walk.sqlite3 and contacts.sqlite3 name different source files')
    return {'voters': len(ids), 'with_phone': named[1], 'areas': areas}


def probe():
    """Open the world as the app does, in this process: the first loopback API call creates the local administrator."""
    from django.contrib.auth.models import User
    from django.test import Client
    from server.research import scoresdb

    scoresdb.reset()
    client = Client(HTTP_HOST='localhost')
    status = {}
    logging.disable(logging.CRITICAL)           # django.request logs every 4xx/5xx; the statuses below say it
    try:
        for name, url in (('session', '/api/auth/session'), ('meta', '/api/v3/meta'),
                          ('people', '/api/v3/people?basis=registration'), ('shell', '/'),
                          ('walk options', '/api/v3/walk/options?level=ss')):
            try:
                status[name] = client.get(url).status_code
            except Exception as error:      # noqa: BLE001 - the walk routes may be mid-build; the rest must work
                status[name] = type(error).__name__
    finally:
        logging.disable(logging.NOTSET)
    broken = [n for n in ('session', 'meta', 'people') if status[n] != 200]
    if broken:
        raise WorldError('The app did not open the world: ' + ', '.join('%s %s' % (n, status[n]) for n in broken))
    if not User.objects.filter(username='admin', is_active=True, is_staff=True, is_superuser=True).exists():
        raise WorldError('The local administrator was not created')
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('out_dir', type=Path, help='an empty (or new) directory; never R/.local')
    parser.add_argument('--force', action='store_true', help='rebuild a directory this tool made before (clears it)')
    args = parser.parse_args(argv)
    out = args.out_dir.resolve()
    try:
        prepare(out, args.force)
        boot(out)
        from django.core.management import call_command
        from server.research import bundles
        from server.research.v3_testkit import build_world

        call_command('migrate', interactive=False, verbosity=0)
        world = build_world(out)
        with tempfile.TemporaryDirectory(prefix='walk-world-') as work:
            walk = make_walk(out, world, Path(work))
        snapshot = bundles.import_bundle(world.zip_path, expected_sha256=world.zip_sha256)
        checked = check_stores(out)
        status = probe()
    except WorldError as error:
        print('error: %s' % error, file=sys.stderr)
        return 1
    qa = walk['qa']
    shas = {name: sha256_of(out / name)[:12] for name in (*DATA_FILES, world.zip_path.name)}
    (out / MARKER).write_text(json.dumps({'schema': SCHEMA, 'snapshot': snapshot.key, 'sha256': shas}), encoding='utf-8')
    print('walk world: %s' % out)
    print('  base     %d people in contacts / scores / street, snapshot %s (bundle sha256 %s)' % (
        world.fixture.n, snapshot.key, world.zip_sha256[:12]))
    print('  walk     %d voters = contacts rows 1..%d (%d with a phone), %d doors, %d stops, %d streets; %d senate, '
          '%d house, %d precinct codes' % (checked['voters'], walk['rekeyed'], checked['with_phone'], qa['doors'],
                                           qa['stops'], qa['streets'], *checked['areas']))
    print('           placed by %s; side agreement %.2f; gates relaxed to %s' % (
        ', '.join('%s %d' % kv for kv in qa['stop_counts']['by_placed_by'].items() if kv[1]), qa['side_agreement'],
        ', '.join('%s>=%s' % kv for kv in RELAXED.items())))
    print('  app      http: %s' % ', '.join('%s %s' % kv for kv in status.items()))
    print('  sha256   ' + ', '.join('%s %s' % (re.sub(r'\.sqlite3$', '', n), s) for n, s in shas.items()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
