"""Read-only access to `scores.sqlite3` for the v3 endpoints.

One connection per server thread (opened read-only, re-opened only when the file changes), the in-memory list columns
loaded once per store version, and lazy ATTACH of contacts.sqlite3 (real names) and street.sqlite3 (coordinates).

Demo mode never attaches the contacts layer: a response built in demo mode cannot contain a real name because the
connection that builds it has no path to one.
"""
import json
import os
import re
import sqlite3
import threading
import zlib
from pathlib import Path

import numpy as np
from django.conf import settings

from tools import field_labels as FL
from tools import scores_schema as S
from tools.demo_names import demo_name

from .terrier_contacts import SourceStoreError

EVIDENCE_LAYOUT = (('values', '<u2', 0), ('status', 'u1', 80), ('p', '<f4', 120), ('exposure', '<f4', 280))
EVIDENCE_BYTES = 440
OUTLOOK_FIELDS = ('sci_ct_political_outlk_vconserv', 'sci_ct_political_outlk_swconserv',
                  'sci_ct_political_outlk_mid', 'sci_ct_political_outlk_swliberal',
                  'sci_ct_political_outlk_vliberal')
OUTLOOK_FIDS = tuple(FL.BY_NAME[name]['fid'] for name in OUTLOOK_FIELDS)
OUTLOOK_LABELS = ('Very conservative', 'Somewhat conservative', 'Middle outlook', 'Somewhat liberal', 'Very liberal')
OUTLOOK_UNKNOWN, OUTLOOK_MULTIPLE = -3, 3


class StoreError(Exception):
    """The score store cannot serve this request (maps to 503)."""
    status = 503


class SourceMismatch(StoreError):
    """The store, the contact registry and the street directory were not built from one source file (409)."""
    status = 409


class NotInStore(StoreError):
    status = 404


_load_lock = threading.Lock()
_local = threading.local()
_info = None


def store_path():
    return (Path(settings.DATA_DIR) / 'scores.sqlite3').resolve()


def number(value):
    """Float32 values are stored as doubles; print them at 8 significant digits (round-trips a float32)."""
    if value is None:
        return None
    return float('%.8g' % value)


class StoreInfo:
    """Everything that is constant for one version of the store file."""

    def __init__(self, path, stamp, connection):
        self.path, self.stamp = path, stamp
        raw = dict(connection.execute('SELECT key, value FROM metadata').fetchall())
        missing = [key for key in S.REQUIRED_METADATA if key not in raw]
        if missing:
            raise StoreError('The score store lacks metadata: ' + ', '.join(missing))
        self.meta = {key: json.loads(value) for key, value in raw.items()}
        if self.meta['schema'] != S.SCHEMA or self.meta['blob_version'] != S.BLOB_VERSION:
            raise StoreError('Unsupported score store schema')
        if self.meta['w1'].get('status') not in S.W1_ACCEPTED:
            raise StoreError('The person crosswalk has not been verified')
        self.n = int(self.meta['n_people'])
        self.catalog = self.meta['field_catalog']
        self.points = np.array([f['points'] for f in self.catalog], dtype=np.float64)
        self.points50 = np.array([f['points_code50'] for f in self.catalog], dtype=np.float64)
        self.demo_key = bytes.fromhex(self.meta['demo_key'])
        self.status_codes = self.meta['status_codes']
        self.value_dict = {}
        for fid, code, literal in connection.execute('SELECT fid, code, literal FROM value_dict ORDER BY fid, code'):
            self.value_dict.setdefault(fid, {})[code] = literal
        self.columns = {}
        for name, dtype, n, blob in connection.execute('SELECT name, dtype, n, data FROM list_column'):
            array = np.frombuffer(blob, dtype=np.dtype(dtype), count=n)
            array.flags.writeable = False
            self.columns[name] = array
        self.dicts = {name: json.loads(ids) for name, ids in connection.execute('SELECT name, ids FROM list_dict')}
        self.area_codes = {name: {ident: code for code, ident in enumerate(ids)} for name, ids in self.dicts.items()}
        for name, array in self.columns.items():
            if len(array) != self.n:
                raise StoreError('List column %s does not match the person count' % name)
        # candidate district only: the support table (sup_score, sup_pct, ranks) and the ord_sup list column, written together
        self.support = 'support' in self.meta
        if self.support and 'ord_sup' not in self.columns:
            raise StoreError('The score store has candidate support but no support order')
        self.sources_checked = False
        self._balanced = {}
        self._balanced_lock = threading.Lock()
        self._outlooks = None
        self._outlook_lock = threading.Lock()
        self._push = None
        self._push_lock = threading.Lock()
        self._parties = None

    def party_counts(self):
        """{party code: people} over the whole district, '' = no code; counted once per version of the store. Display only (O-10):
        the registration groups behind the scores are unchanged."""
        if self._parties is None:
            conn = _open(self.path)
            try:
                rows = conn.execute("SELECT COALESCE(party, ''), COUNT(*) FROM person GROUP BY 1 ORDER BY 1").fetchall()
            finally:
                conn.close()
            self._parties = {code: int(n) for code, n in rows}
        return self._parties

    def terms(self):
        """untapped.terms() (support score, chance of voting, influence standing of every person), as a StoreError when it
        cannot be worked out or does not cover this store's people."""
        from . import untapped
        try:
            terms = untapped.terms()
        except SourceStoreError as problem:
            raise StoreError(str(problem)) from problem
        if terms.n != self.n:
            raise StoreError('The turnout terms do not cover every person')
        return terms

    def push(self):
        """(push 0..100 per person in pos order, positions from the highest push down with ties by position, the push
        values ascending) of a candidate district: 100 x field_labels.potential of the terms at the district's default
        weights, worked out on first use, so the store needs no rebuild. Worked out again when the terms are (a store
        file changed)."""
        from . import untapped
        terms = self.terms()
        cached = self._push
        if cached is None or cached[0] is not terms:
            with self._push_lock:
                cached = self._push
                if cached is None or cached[0] is not terms:
                    value = 100.0 * FL.potential(terms.s, terms.t, terms.i, *untapped.default_weights())
                    order = np.lexsort((np.arange(len(value)), -value)).astype(np.int32)
                    ascending = np.sort(value)
                    for array in (value, order, ascending):
                        array.flags.writeable = False
                    self._push = cached = (terms, (value, order, ascending))
        return cached[1]

    def outlook_codes(self):
        """One cached category per stored evidence row; never derive views from the lean score.

        Both observed and stored model-filled values participate. Empty and conflicting flags are retained
        as separate states, rather than forced into a midpoint or a registration-consistent response.
        """
        if self._outlooks is None:
            with self._outlook_lock:
                if self._outlooks is None:
                    conn = _open(self.path)
                    try:
                        codes = np.full(self.n, OUTLOOK_UNKNOWN, dtype=np.int8)
                        for row in conn.execute('SELECT pos, blob FROM evidence ORDER BY pos'):
                            values = decode_evidence(row['blob'])['values']
                            codes[row['pos'] - 1] = outlook_code(self, values)
                    finally:
                        conn.close()
                    codes.flags.writeable = False
                    self._outlooks = codes
        return self._outlooks

    def balanced_mask(self, k=100):
        """Boolean mask of the balanced top-K view: each registration group's rows up to its Hamilton seats, ties
        included (a row stays when its group competition rank is within the group's seats). K = 100 by default."""
        mask = self._balanced.get(k)
        if mask is None:
            with self._balanced_lock:
                mask = self._balanced.get(k)
                if mask is None:
                    seats = self.seats(k)
                    limit = np.array([seats[g] for g in FL.REG_GROUPS], dtype=np.int64)
                    mask = self.columns['reg_rank'] <= limit[self.columns['reg_group']]
                    mask.flags.writeable = False
                    self._balanced[k] = mask
        return mask

    def seats(self, k):
        balanced = self.meta['balanced']['k'].get(str(k))
        if balanced is None:
            from tools.scores_writer import hamilton
            counts = [self.meta['reg_group_counts'][g] for g in FL.REG_GROUPS]
            seats = hamilton(int(k), counts)
            return dict(zip(FL.REG_GROUPS, seats))
        return balanced['K_g']


def _open(path, extra_pragmas=True):
    uri = path.as_uri() + '?mode=ro'
    connection = sqlite3.connect(uri, uri=True, timeout=3, check_same_thread=True)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    if extra_pragmas:
        connection.execute('PRAGMA mmap_size=268435456')
        connection.execute('PRAGMA cache_size=-32768')
        connection.execute('PRAGMA temp_store=MEMORY')
    return connection


def _stamp(path):
    try:
        stat = os.stat(path)
    except OSError as error:
        raise StoreError('The local score store has not been built.') from error
    return (stat.st_mtime_ns, stat.st_size)


def info():
    """The cached StoreInfo for the current file version (loads it on first use or after the file changes)."""
    global _info
    path = store_path()
    stamp = _stamp(path)
    current = _info
    if current is not None and current.path == path and current.stamp == stamp:
        return current
    with _load_lock:
        current = _info
        if current is not None and current.path == path and current.stamp == stamp:
            return current
        connection = _open(path, extra_pragmas=False)
        try:
            _info = StoreInfo(path, stamp, connection)
        except sqlite3.Error as error:
            raise StoreError('The local score store is unreadable.') from error
        finally:
            connection.close()
        return _info


def party_counts_by_area(column):
    """{area id: {party code: people}} for one area column of the person table ('' = no code). Display only (O-10)."""
    by_area = {}
    for area, code, n in connection().execute(
            "SELECT %s, COALESCE(party, ''), COUNT(*) FROM person WHERE %s IS NOT NULL AND %s <> '' GROUP BY 1, 2 ORDER BY 1, 2" % ((column,) * 3)):
        by_area.setdefault(area, {})[code] = int(n)
    return by_area


def reset():
    """Close this thread's connection and forget the cached store (tests, and after the file is replaced)."""
    global _info
    state = getattr(_local, 'state', None)
    if state is not None:
        try:
            state['connection'].close()
        except sqlite3.Error:
            pass
        _local.state = None
    _info = None


def connection(*, contacts=False, street=False):
    """Thread-local read-only connection; contacts/street are attached only when asked for."""
    current = info()
    state = getattr(_local, 'state', None)
    if state is None or state['stamp'] != current.stamp or state['path'] != current.path:
        if state is not None:
            try:
                state['connection'].close()
            except sqlite3.Error:
                pass
        try:
            conn = _open(current.path)
        except sqlite3.Error as error:
            raise StoreError('The local score store is unreadable.') from error
        state = {'connection': conn, 'stamp': current.stamp, 'path': current.path, 'attached': set()}
        _local.state = state
    conn = state['connection']
    for wanted, name in ((contacts, 'contacts'), (street, 'street')):
        if wanted and name not in state['attached']:
            _attach(conn, name, current)
            state['attached'].add(name)
        elif not wanted and name in state['attached']:
            # a request that did not ask for a layer never has it: a demo response has no path to a real name even
            # when this thread served a real-mode request a moment ago
            conn.execute('DETACH DATABASE %s' % name)
            state['attached'].discard(name)
    return conn


def _attach(conn, name, current):
    path = (Path(settings.DATA_DIR) / (name + '.sqlite3')).resolve()
    if not path.exists():
        raise StoreError('The local %s layer is unavailable.' % name)
    conn.execute('ATTACH DATABASE ? AS %s' % name, (path.as_uri() + '?mode=ro',))
    row = conn.execute('SELECT value FROM %s.metadata WHERE key=?' % name,
                       ('source_sha256',)).fetchone()
    if row is None or json.loads(row[0]) != current.meta['contacts_source_sha256']:
        conn.execute('DETACH DATABASE %s' % name)
        raise SourceMismatch('The score store and the %s layer use different source files.' % name)


# ---- demo mode and names ------------------------------------------------------------------------------------------

def is_demo(request):
    """CU_DEMO=1 forces demo for the whole instance; ?demo=1 is display-only and per request."""
    return bool(settings.DEMO_MODE) or request.GET.get('demo') == '1'


_SUFFIXES = {'jr', 'sr', 'ii', 'iii', 'iv'}


def title_name(raw):
    """The contact layer stores names in upper case: lower-case, capitalise after start/space/hyphen/apostrophe."""
    if not raw:
        return ''
    parts = re.split(r"([ \-'])", raw.strip().lower())
    out = []
    for part in parts:
        if part in (' ', '-', "'") or not part:
            out.append(part)
        elif part in _SUFFIXES:
            out.append(part.upper())
        elif part.startswith('mc') and len(part) > 2:
            out.append('Mc' + part[2].upper() + part[3:])
        else:
            out.append(part[0].upper() + part[1:])
    return ''.join(out)


def names(conn, current, rows, demo):
    """{person_id: display name}. `rows` need person_id and hh. Real names come only from the attached contacts layer."""
    if demo:
        return {row['person_id']: demo_name(current.demo_key, row['person_id'], row['hh']) for row in rows}
    ids = [row['person_id'] for row in rows]
    if not ids:
        return {}
    marks = ','.join('?' * len(ids))
    found = conn.execute('SELECT id, name FROM contacts.contacts WHERE id IN (%s)' % marks, ids).fetchall()
    return {row['id']: title_name(row['name']) for row in found}


# ---- evidence ---------------------------------------------------------------------------------------------------

def decode_evidence(blob):
    raw = zlib.decompress(blob)
    if len(raw) != EVIDENCE_BYTES:
        raise StoreError('Unexpected evidence record size')
    return {name: np.frombuffer(raw, dtype=dtype, count=S.N_FIELDS, offset=offset)
            for name, dtype, offset in EVIDENCE_LAYOUT}


def outlook_code(current, values):
    selected = [index for index, fid in enumerate(OUTLOOK_FIDS)
                if current.value_dict.get(fid, {}).get(int(values[fid])) in ('1', '1.0')]
    return selected[0] - 2 if len(selected) == 1 else OUTLOOK_MULTIPLE if selected else OUTLOOK_UNKNOWN


def outlook_fields(code):
    if code == OUTLOOK_MULTIPLE:
        return 'U', 'Multiple responses'
    if code == OUTLOOK_UNKNOWN:
        return 'U', 'Unknown'
    return int(code), OUTLOOK_LABELS[int(code) + 2]


def contributions(current, exposure, code50):
    points = current.points50 if code50 else current.points
    return np.nan_to_num(exposure.astype(np.float64), nan=0.0) * points


def fmt_rank(rank, tie_n):
    return {'rank': int(rank), 'tie': bool(tie_n and tie_n > 1)}
