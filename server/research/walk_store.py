"""Read-only access to the walk-list store `walk.sqlite3` for the /api/v3/walk/ endpoints.

Names and phones come from `contacts.sqlite3`, attached read-only to the same connection; the walk store itself holds no
name, address text or birth date. A connection lives for one request and is closed with it (scoresdb keeps one per
thread): a handle held between requests would make the build tool's os.replace fail on Windows. What is derived from
the file (the checked metadata, the street directory) is cached once per file version instead.

walk_store() is the gate. The file must exist and carry schema walk-v1, and its source_sha256 must be the one the
contact, score and street stores record: the checks of terrier_contacts.verified_stores, except that a synthetic
crosswalk ('fixture', as scoresdb accepts) passes too.

A store built from the source CSV (meta person_fields = csv, the OR and CT districts, which have no score store yet)
is checked against the contact and street stores only: it has no scores.sqlite3 to attach.
"""
import re
import sqlite3
import threading
from collections import defaultdict
from contextlib import closing, contextmanager
from pathlib import Path

from django.conf import settings

from tools import scores_schema as S

from . import walk_names as N
from .scoresdb import title_name
from .terrier_contacts import SourceStoreError, metadata

SCHEMA = 'walk-v1'
NOT_BUILT = "Walk list data isn't built yet. Run tools/build_walk.py."
UNREADABLE = 'The walk list data is unreadable. Run tools/build_walk.py.'
SOURCES = ('walk', 'contacts', 'scores', 'street')
AREA_COLUMNS = ('ss', 'sh', 'precinct')
SUGGEST_MIN = 0.6               # a street this similar to the typed one is close enough to offer
CHUNK = 900                     # ids per IN (...)

_lock = threading.Lock()
_checked = None                 # ((data dir, stamps of the four files), Info)
_directory = None               # ((walk file, stamp), Directory)


def reset():
    """Forget what is cached (tests, and after the stores are replaced)."""
    global _checked, _directory
    with _lock:
        _checked = _directory = None


def _uri(path):
    return path.as_uri() + '?mode=ro'


def _chunks(items, size=CHUNK):
    items = list(items)
    for start in range(0, len(items), size):
        yield items[start:start + size]


def phone_digits(wireless, phone):
    """The wireless number if it has digits, else the landline; digits only ('' when there is none)."""
    for value in (wireless, phone):
        digits = ''.join(c for c in str(value or '') if c.isdigit())
        if digits:
            return digits
    return ''


class Info:
    def __init__(self, data_dir, stamp, meta):
        self.stamp, self.meta = stamp, meta
        self.path = data_dir / 'walk.sqlite3'
        self.contacts_path = data_dir / 'contacts.sqlite3'
        self.source_sha256 = meta['source_sha256']


def verify():
    """The checked Info of the stores on disk; raises SourceStoreError (503 not built or unreadable, 409 stores disagree)."""
    global _checked
    directory = Path(settings.DATA_DIR).resolve()
    walk = directory / 'walk.sqlite3'
    if not walk.is_file():
        raise SourceStoreError(NOT_BUILT, 503)
    names = [name for name in SOURCES if name != 'scores' or (directory / 'scores.sqlite3').is_file()]
    try:
        stamp = tuple((stat.st_mtime_ns, stat.st_size) for stat in (
            (directory / (name + '.sqlite3')).stat() for name in names))
    except OSError as error:
        raise SourceStoreError('The local source stores are unavailable.', 503) from error
    cached = _checked
    if cached is not None and cached[0] == (directory, stamp):
        return cached[1]
    with _lock:
        cached = _checked
        if cached is not None and cached[0] == (directory, stamp):
            return cached[1]
        info = _read(directory, stamp)
        _checked = ((directory, stamp), info)
        return info


def _read(directory, stamp):
    paths = {name: directory / (name + '.sqlite3') for name in SOURCES}
    try:
        with closing(sqlite3.connect(_uri(paths['walk']), uri=True, timeout=3)) as walk:
            meta = dict(walk.execute('SELECT key, value FROM meta').fetchall())
    except sqlite3.Error as error:
        raise SourceStoreError(UNREADABLE, 503) from error
    if meta.get('schema') != SCHEMA:
        raise SourceStoreError('The walk list data uses an unsupported schema. Run tools/build_walk.py.', 503)
    checksum = meta.get('source_sha256')
    if not isinstance(checksum, str) or not re.fullmatch(r'[a-f0-9]{64}', checksum):
        raise SourceStoreError(UNREADABLE, 503)
    csv_built = meta.get('person_fields') == 'csv'
    attached = ('street',) if csv_built else ('scores', 'street')
    try:
        with closing(sqlite3.connect(_uri(paths['contacts']), uri=True, timeout=3)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA query_only=ON')
            for name in attached:
                connection.execute('ATTACH DATABASE ? AS ' + name, (_uri(paths[name]),))
            recorded = {name: metadata(connection, 'main' if name == 'contacts' else name)
                        for name in ('contacts', *attached)}
    except sqlite3.Error as error:
        raise SourceStoreError('The local source stores are unavailable.', 503) from error
    if (recorded['contacts'].get('schema') != 'contacts-v1'
            or (not csv_built and recorded['scores'].get('schema') != 'scores-v1')
            or recorded['street'].get('schema_version') != '1'):
        raise SourceStoreError('The local source stores use an unsupported schema.')
    if not csv_built and recorded['scores'].get('w1', {}).get('status') not in S.W1_ACCEPTED:
        raise SourceStoreError('The person crosswalk has not been verified against the source file.', 409)
    if not (checksum == recorded['contacts'].get('source_sha256') == recorded['street'].get('source_sha256')
            and (csv_built or checksum == recorded['scores'].get('contacts_source_sha256'))):
        raise SourceStoreError('The walk list data was built from a different source file than the contact stores. '
                               'Run tools/build_walk.py.', 409)
    return Info(directory, stamp, meta)


@contextmanager
def walk_store():
    """A Store over a read-only connection to walk.sqlite3 with contacts attached; closed when the block ends."""
    info = verify()
    connection = sqlite3.connect(_uri(info.path), uri=True, timeout=3)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA mmap_size=268435456')
        connection.execute('PRAGMA cache_size=-32768')
        connection.execute('PRAGMA temp_store=MEMORY')
        connection.execute('ATTACH DATABASE ? AS contacts', (_uri(info.contacts_path),))
        yield Store(connection, info)
    finally:
        connection.close()


# ---- streets: one directory per file version, searched in memory ---------------------------------------------------

def _base(tokens):
    """The words that name the street: without leading and trailing directionals and the final street type."""
    lo, hi = 0, len(tokens)
    while hi - lo > 1 and tokens[lo] in N.DIRECTIONS:
        lo += 1
    while hi - lo > 1 and tokens[hi - 1] in N.DIRECTIONS:
        hi -= 1
    if hi - lo > 1 and tokens[hi - 1] in N.SUFFIXES:
        hi -= 1
    return tokens[lo:hi]


def _within(query, pool, exact=False):
    """True when every query word is (or, unless exact, starts) a different word of pool."""
    free = list(pool)
    for word in query:
        for i, other in enumerate(free):
            if other == word or (not exact and other.startswith(word)):
                del free[i]
                break
        else:
            return False
    return True


class Street:
    __slots__ = ('id', 'key', 'city', 'zip', 'display', 'tokens', 'base', 'words')

    def __init__(self, row):
        self.id, self.key, self.city, self.zip, self.display = row[0], row[1] or '', row[2] or '', row[3] or '', row[4] or ''
        self.tokens = tuple(self.key.split())
        self.base = tuple(_base(self.tokens))
        self.words = tuple(re.findall(r'[a-z0-9]+', self.display.lower()))


def _tier(street, key, tokens, words):
    """How well the typed text reads as this street: 1 the whole name, then the name starts with it, all its words
    are in the name, they start words of the name (a half-typed word); 0 otherwise."""
    if street.key == key:
        return 1.0
    if street.key.startswith(key + ' '):
        return 0.99
    if _within(tokens, street.tokens, True):
        return 0.97
    if _within(tokens, street.tokens) or _within(words, street.words):
        return 0.93
    return 0.0


class Directory:
    """Every street of the store with the lookups the street search and the CSV match need."""

    def __init__(self, rows):
        self.streets = [Street(row) for row in rows]
        self.by_id = {street.id: street for street in self.streets}
        self.by_key = defaultdict(list)
        self.by_base = defaultdict(list)
        grams = defaultdict(set)
        for street in self.streets:
            self.by_key[street.key].append(street)
            for token in set(street.base):
                self.by_base[token].append(street)
        for token in self.by_base:
            if len(token) >= 4 and not any(c.isdigit() for c in token):
                for i in range(len(token) - 2):
                    grams[token[i:i + 3]].add(token)
        self._grams, self._similar = grams, {}

    def similar(self, token):
        """Other words of the directory that read as a misspelling of `token` (the token rule of walk_names.similarity)."""
        found = self._similar.get(token)
        if found is None:
            found = ()
            if len(token) >= 4 and not any(c.isdigit() for c in token):
                near = {u for i in range(len(token) - 2) for u in self._grams.get(token[i:i + 3], ())}
                found = tuple(sorted(u for u in near if u != token and 0.6 <= len(u) / len(token) <= 1 / 0.6
                                     and N.similarity(token, u) > 0))
            if len(self._similar) < 50000:
                self._similar[token] = found
        return found

    def near(self, tokens, allowed=None):
        """Streets that share a naming word (or a close spelling of one) with `tokens`; a street type alone is no reason."""
        out = {}
        for token in _base(tokens):
            for word in (token, *self.similar(token)):
                for street in self.by_base.get(word, ()):
                    if allowed is None or street.id in allowed:
                        out[street.id] = street
        return list(out.values())

    def search(self, text, limit, allowed=None, doors=None):
        """Up to `limit` streets for typed text, best first, then the one with more doors. `allowed`: street ids."""
        key = N.name_key(text)
        if not key:
            return []
        tokens, words = key.split(), re.findall(r'[a-z0-9]+', text.lower())
        pool = self.streets if allowed is None else [self.by_id[i] for i in allowed if i in self.by_id]
        scored = {}
        for street in pool:
            score = _tier(street, key, tokens, words)
            if score:
                scored[street.id] = score
        if len(scored) < limit:               # fewer good readings than room: add close spellings
            for street in self.near(tokens, allowed):
                if street.id not in scored:
                    score = N.similarity(key, street.key)
                    if score >= SUGGEST_MIN:
                        scored[street.id] = min(score, 0.9)
        doors = doors or {}
        order = sorted(scored, key=lambda i: (-scored[i], -doors.get(i, 0), self.by_id[i].display, self.by_id[i].city, i))
        return [self.by_id[i] for i in order[:limit]]

    def lookup(self, text, allowed=None):
        """[(similarity, Street)], best first, for a street written out in full (an uploaded row): the streets with
        exactly that name, else those that read like it (similarity >= SUGGEST_MIN)."""
        key = N.name_key(text)
        if not key:
            return []
        exact = [street for street in self.by_key.get(key, ()) if allowed is None or street.id in allowed]
        if exact:
            return [(1.0, street) for street in exact]
        hits = []
        for street in self.near(key.split(), allowed):
            score = N.similarity(key, street.key)
            if score >= SUGGEST_MIN:
                hits.append((score, street))
        hits.sort(key=lambda hit: (-hit[0], hit[1].display, hit[1].city, hit[1].id))
        return hits


# ---- queries -------------------------------------------------------------------------------------------------------

def _area(area, alias=''):
    """(' AND ss=? ...', args) for the area codes given."""
    prefix = alias + '.' if alias else ''
    given = [column for column in AREA_COLUMNS if area.get(column)]
    return ''.join(' AND %s%s=?' % (prefix, column) for column in given), [area[column] for column in given]


VOTER_SQL = ('SELECT p.person_id, p.stop_id, p.unit, p.precinct, p.party, p.age, p.gender, p.reg_date, p.pri_voted, '
             'p.pri_elig, p.gen_voted, p.gen_elig, p.abs_voted, p.abs_elig, p.voter_key, p.street_id, s.house, '
             's.house_int, s.parity, s.span_id, s.pos_m, s.side_lr, s.placed_by, s.lon, s.lat '
             'FROM person p JOIN stop s ON s.id = p.stop_id')
PARITY = {'even': ' AND s.parity=0', 'odd': ' AND s.parity=1', 'both': ''}


class Store:
    def __init__(self, connection, info):
        self.conn, self.info = connection, info

    def directory(self):
        global _directory
        key = (self.info.path, self.info.stamp[0])
        cached = _directory
        if cached is not None and cached[0] == key:
            return cached[1]
        with _lock:
            cached = _directory
            if cached is not None and cached[0] == key:
                return cached[1]
            built = Directory(self.conn.execute('SELECT id, key, city, zip, display FROM street ORDER BY id').fetchall())
            _directory = (key, built)
            return built

    def options(self, level, area):
        """[(code, people, doors)] of the districts or precincts at `level` inside `area`, by code."""
        column = {'ss': 'ss', 'sh': 'sh', 'precinct': 'precinct'}[level]
        clause, args = _area(area)
        return [(row[0], row[1], row[2]) for row in self.conn.execute(
            "SELECT %s, SUM(people), SUM(doors) FROM combo WHERE %s<>''%s GROUP BY %s ORDER BY %s"
            % (column, column, clause, column, column), args)]

    def street_doors(self, area):
        """{street id: doors} of the streets that have people in `area` (every street when no area is given)."""
        clause, args = _area(area)
        return {row[0]: row[1] for row in self.conn.execute(
            'SELECT street_id, SUM(doors) FROM combo WHERE 1=1%s GROUP BY street_id ORDER BY street_id' % clause, args)}

    def street_counts(self, ids, area):
        """{street id: {people, doors, even, odd}} inside `area`; a door is a (stop, unit) pair."""
        clause, args = _area(area, 'p')
        out = {i: {'people': 0, 'doors': 0, 'even': 0, 'odd': 0} for i in ids}
        for chunk in _chunks(sorted(set(ids))):
            for street, parity, doors, people in self.conn.execute(
                    'SELECT street_id, parity, COUNT(*), SUM(n) FROM (SELECT p.street_id AS street_id, s.parity AS '
                    'parity, COUNT(*) AS n FROM person p JOIN stop s ON s.id = p.stop_id WHERE p.street_id IN (%s)%s '
                    'GROUP BY p.street_id, p.stop_id, p.unit) GROUP BY street_id, parity' % (','.join('?' * len(chunk)), clause),
                    [*chunk, *args]):
                count = out[street]
                count['doors'] += doors
                count['people'] += people
                if parity == 0:
                    count['even'] += doors
                elif parity == 1:
                    count['odd'] += doors
        return out

    def voters(self, picks, area, limit):
        """Rows of the people at the picked street sides ([(street id, 'even'|'odd'|'both')]) or, with no picks, of
        everyone in `area` who has a stop: at most `limit`."""
        clause, args = _area(area, 'p')
        if not picks:
            return self.conn.execute(VOTER_SQL + ' WHERE 1=1' + clause + ' LIMIT ?', [*args, limit]).fetchall()
        rows = []
        for street, side in picks:
            rows.extend(self.conn.execute(VOTER_SQL + ' WHERE p.street_id=?' + PARITY[side] + clause + ' LIMIT ?',
                                          [street, *args, limit - len(rows)]))
            if len(rows) >= limit:
                break
        return rows

    def count_voters(self, picks, area):
        clause, args = _area(area, 'p')
        if not picks:
            plain, plain_args = _area(area)
            return self.conn.execute('SELECT COALESCE(SUM(people), 0) FROM combo WHERE 1=1' + plain, plain_args).fetchone()[0]
        return sum(self.conn.execute(
            'SELECT COUNT(*) FROM person p JOIN stop s ON s.id = p.stop_id WHERE p.street_id=?' + PARITY[side] + clause,
            [street, *args]).fetchone()[0] for street, side in picks)

    def voters_by_key(self, keys):
        """Rows of the people with these voter keys (.walk-salt HMACs, the same across rebuilds; person ids need not be)."""
        rows = []
        for chunk in _chunks(sorted(set(keys))):
            rows.extend(self.conn.execute(VOTER_SQL + ' WHERE p.voter_key IN (%s)' % ','.join('?' * len(chunk)), chunk))
        return rows

    def contacts(self, ids):
        """{person id: (display name, phone digits)} from the contact layer."""
        out = {}
        for chunk in _chunks(sorted(set(ids))):
            for row in self.conn.execute('SELECT id, name, phone, wireless FROM contacts.contacts WHERE id IN (%s)'
                                         % ','.join('?' * len(chunk)), chunk):
                out[row['id']] = (title_name(row['name']), phone_digits(row['wireless'], row['phone']))
        return out

    def span_stats(self, area):
        """Rows (span_id, street_id, doors, d, r, u, o) of the placed blocks that have people in `area`."""
        clause, args = _area(area, 'p')
        return self.conn.execute(
            "SELECT s.span_id, p.street_id, COUNT(DISTINCT p.stop_id || ':' || IFNULL(p.unit, '')) AS doors, "
            "SUM(p.party='D') AS d, SUM(p.party='R') AS r, SUM(p.party='U') AS u, "
            "SUM(COALESCE(p.party,'') NOT IN ('D','R','U')) AS o FROM person p JOIN stop s ON s.id = p.stop_id "
            "WHERE s.span_id IS NOT NULL%s GROUP BY s.span_id, p.street_id" % clause, args).fetchall()

    def span_lines(self, ids):
        """{span id: coords JSON text}."""
        out = {}
        for chunk in _chunks(sorted(set(ids))):
            out.update(self.conn.execute('SELECT id, coords FROM road_span WHERE id IN (%s)' % ','.join('?' * len(chunk)), chunk))
        return out
