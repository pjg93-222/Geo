"""Untapped potential (Paul 2026-10-09): how much of each person is still unworked, summed over areas, and who is already worked.

For every person not at a door with a recorded result:
  s  candidate support on the absolute scale, support.sup_score / 100 (0.5 when the person has no support row)
  t  chance of voting: the mean of the five turnout-only scores (field_labels.TURNOUT_FIELDS) / 100, read from the stored
     evidence literals (not a percentile, not the exposure); else the person's general-election record (gen_voted /
     gen_elig of the walk store); else 0.5
  i  sphere of influence standing, person.soi_pct / 100 (0 without it)
  undecided = (1 - |2s - 1|) x t        supporter = s x (1 - t)
  potential = (wp x undecided + (1 - wp) x supporter) x (1 + wi x i)        field_labels.potential

Nothing is written: terms() decodes the score store once per file version, knocked() reads the saved walk lists' results,
and area_payload() adds the four sums per area (undecided, supporter and each times i) so the browser can apply the weights
without asking again. The weights are linear in these sums, so the map and the list move at once with the sliders.

  terms()            the per-person arrays, cached per (score store, walk store) file stamps
  knocked()          the doors that have a result in some saved list, cached per results version
  results_version()  changes whenever a result is saved or a list deleted
  parse_weights(q)   wp and wi from a query dict, the district's defaults when absent
  area_payload(l)    the body of GET /api/v3/untapped?level=l
"""
import math
import os
import re
import sqlite3
import threading
import zlib
from contextlib import closing
from functools import lru_cache
from pathlib import Path

import numpy as np
from django.conf import settings
from django.db.models import Count, Max

from tools import field_labels as FL
from tools import geography_levels as GL
from tools import scores_schema as S
from tools.geography_levels import area_label

from . import scoresdb as DB
from . import walk_store as W
from .models import Disposition
from .terrier_contacts import SourceStoreError
from .v3_http import BadRequest

SCHEMA = 'v3-untapped-1'
LEVELS = ('block', 'precinct', 'tract', 'zip', 'state_house', 'state_senate')
STATE_LEVELS = {'state_house': 'sh', 'state_senate': 'ss'}          # the walk store's columns (3-digit voter-file codes)
DEFAULT_WEIGHTS = (0.5, 0.5)                                         # (wp, wi) of a district whose entry names none
EVEN_ODDS = 0.5                                                      # t of a person with no turnout score and no eligible general
WEIGHT = re.compile(r'(0(\.\d{1,4})?|1(\.0{1,4})?)')
STATE_CODE = re.compile(r'\d{1,3}')
TURNOUT_FIDS = tuple(FL.BY_NAME[name]['fid'] for name in FL.TURNOUT_FIELDS)
CHUNK = 900
BATCH = 20000                                                         # evidence records decoded at a time

NO_SCORES = 'The local score store has not been built.'
UNREADABLE = 'The local score store is unreadable.'


# ---- the weights -------------------------------------------------------------------------------------------------

def default_weights():
    """(wp, wi) of this district (tools.districts _ROI 'potential'); the module defaults for a store served without one."""
    given = (settings.DISTRICT.get('roi') or {}).get('potential') or {}
    return float(given.get('wp', DEFAULT_WEIGHTS[0])), float(given.get('wi', DEFAULT_WEIGHTS[1]))


def parse_weights(q):
    """(wp, wi) from a query dict {name: text}: each 0 to 1 with at most four decimals, the district's default when absent."""
    out = []
    for name, default in zip(('wp', 'wi'), default_weights()):
        text = q.get(name)
        if text is None:
            out.append(default)
        elif WEIGHT.fullmatch(text):
            out.append(float(text))
        else:
            raise BadRequest('Invalid weight')
    return tuple(out)


# ---- the terms: s, t, i per person ----------------------------------------------------------------------------------

def _file_stamp(path):
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _stamp():
    """((score store path, mtime, size), (walk store mtime, size) or None): what every cache below is keyed on."""
    scores = DB.store_path()
    stamp = _file_stamp(scores)
    if stamp is None:
        raise SourceStoreError(NO_SCORES, 503)
    return (str(scores), *stamp), _file_stamp(Path(settings.DATA_DIR) / 'walk.sqlite3')


class Terms:
    """The per-person values of one version of the stores. Arrays are float64[n] in score-store order (index = pos - 1) and
    read-only; a Terms is replaced, never changed."""
    __slots__ = ('n', 's', 't', 'i', 'undecided', 'supporter', 't_source', 'stamp')

    def __init__(self, stamp, s, t, i, t_source):
        self.stamp, self.n, self.s, self.t, self.i, self.t_source = stamp, len(s), s, t, i, t_source
        self.undecided = (1 - np.abs(2 * s - 1)) * t
        self.supporter = s * (1 - t)
        for array in (self.s, self.t, self.i, self.undecided, self.supporter):
            array.flags.writeable = False


def _number(literal):
    """A stored literal as a float, NaN when it is not a finite number."""
    try:
        value = float(literal)
    except (TypeError, ValueError):
        return math.nan
    return value if math.isfinite(value) else math.nan


def _turnout(conn, n, tables):
    """float64[n] of the mean of the five turnout literals / 100 (NaN: no usable literal), from the evidence blobs and the
    value dictionary. One pass over the blobs (about 3 s for 560,000 people)."""
    t = np.full(n, math.nan)
    if not {'evidence', 'value_dict'} <= tables:
        return t
    lookups = {fid: {} for fid in TURNOUT_FIDS}
    for fid, code, literal in conn.execute('SELECT fid, code, literal FROM value_dict WHERE fid IN (%s)'
                                           % ','.join(map(str, TURNOUT_FIDS))):
        lookups[fid][code] = _number(literal)
    tables_by_fid = []                           # one lookup array per field; the last slot is NaN and stands for "no such code"
    for fid in TURNOUT_FIDS:
        codes = lookups[fid]
        table = np.full(max(codes, default=-1) + 2, math.nan)
        for code, value in codes.items():
            table[code] = value
        tables_by_fid.append(table)
    raw = np.full((n, len(TURNOUT_FIDS)), 0xFFFF, dtype='<u2')
    fids = list(TURNOUT_FIDS)
    cursor = conn.execute('SELECT pos, blob FROM evidence')
    while True:
        batch = cursor.fetchmany(BATCH)
        if not batch:
            break
        joined = b''.join([zlib.decompress(blob) for _, blob in batch])
        if len(joined) != len(batch) * DB.EVIDENCE_BYTES:
            raise SourceStoreError('Unexpected evidence record size', 503)
        positions = np.fromiter((pos for pos, _ in batch), dtype=np.intp, count=len(batch))
        if positions.min() < 1 or positions.max() > n:
            raise SourceStoreError('The evidence positions are not 1 to n.', 503)
        # the first 40 little-endian u16 of each 440-byte record are the value codes
        raw[positions - 1] = np.frombuffer(joined, dtype='<u2').reshape(len(batch), DB.EVIDENCE_BYTES // 2)[:, fids]
    values = np.empty((n, len(TURNOUT_FIDS)))
    for k, table in enumerate(tables_by_fid):
        values[:, k] = table[np.minimum(raw[:, k].astype(np.intp), len(table) - 1)]
    present = ~np.isnan(values)
    counts = present.sum(axis=1)
    sums = np.where(present, values, 0.0).sum(axis=1)
    return np.where(counts > 0, sums / np.maximum(counts, 1) / 100.0, math.nan)


def _record(n, need, scores_path, walk_path):
    """(t from the general-election record, which people it covered): gen_voted / gen_elig where the person has an eligible
    general; the walk store is read only for the people in `need`."""
    t, found = np.full(n, math.nan), np.zeros(n, dtype=bool)
    if walk_path is None or not need.any():
        return t, found
    try:
        with closing(sqlite3.connect(walk_path.as_uri() + '?mode=ro', uri=True, timeout=3)) as conn:
            conn.execute('ATTACH DATABASE ? AS scores', (scores_path.as_uri() + '?mode=ro',))
            rows = conn.execute('SELECT sc.pos, p.gen_voted, p.gen_elig FROM person p JOIN scores.person sc '
                                'ON sc.person_id = p.person_id WHERE p.gen_elig > 0').fetchall()
    except sqlite3.Error:
        return t, found                    # no usable walk store: those people fall to even odds
    for pos, voted, eligible in rows:
        if need[pos - 1]:
            t[pos - 1], found[pos - 1] = min(1.0, max(0.0, voted / eligible)), True
    return t, found


def _build_terms(stamp):
    path = DB.store_path()
    try:
        with closing(DB._open(path)) as conn:
            conn.row_factory = None
            tables = {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            n, low, high = conn.execute('SELECT COUNT(*), MIN(pos), MAX(pos) FROM person').fetchone()
            if not n or low != 1 or high != n:
                raise SourceStoreError('The score store positions are not 1 to n.', 503)
            s = np.full(n, 0.5)
            if 'support' in tables:
                rows = np.array(conn.execute('SELECT pos, sup_score FROM support').fetchall(), dtype=np.float64).reshape(-1, 2)
                s[rows[:, 0].astype(np.intp) - 1] = np.clip(rows[:, 1] / 100.0, 0.0, 1.0)
            i = np.zeros(n)
            if 'soi_pct' in {row[1] for row in conn.execute('PRAGMA table_info(person)')}:
                soi = np.fromiter((math.nan if v is None else v for (v,) in conn.execute(
                    'SELECT soi_pct FROM person ORDER BY pos')), dtype=np.float64, count=n)
                i = np.clip(np.nan_to_num(soi / 100.0, nan=0.0), 0.0, 1.0)
            t = _turnout(conn, n, tables)
    except sqlite3.Error as problem:
        raise SourceStoreError(UNREADABLE, 503) from problem
    sai = ~np.isnan(t)
    t = np.clip(t, 0.0, 1.0)
    record_t, record = _record(n, ~sai, path, (Path(settings.DATA_DIR) / 'walk.sqlite3').resolve() if stamp[1] else None)
    t = np.where(sai, t, np.where(record, record_t, EVEN_ODDS))
    source = {'sai': int(sai.sum()), 'record': int(record.sum()), 'even': int((~sai & ~record).sum())}
    return Terms(stamp, s, t, i, source)


_terms_lock = threading.Lock()
_terms = None


def terms():
    """The Terms of the stores on disk (built on first use or after either file changes). SourceStoreError (503) when the
    score store is missing or unreadable."""
    global _terms
    stamp = _stamp()
    cached = _terms
    if cached is not None and cached.stamp == stamp:
        return cached
    with _terms_lock:
        cached = _terms
        if cached is not None and cached.stamp == stamp:
            return cached
        _terms = _build_terms(stamp)
        return _terms


# ---- results: which doors are already worked -----------------------------------------------------------------------

def results_version():
    """'<newest result id>-<results kept>': changes whenever a result is saved (a higher id) or a list is deleted (fewer)."""
    row = Disposition.objects.aggregate(top=Max('id'), n=Count('id'))
    return '%d-%d' % (row['top'] or 0, row['n'])


def standing_keys():
    """The voter keys with a result that stands: some list's newest entry for the voter has a code (a null code cleared it)."""
    state = {}
    for walk_list, key, code in Disposition.objects.order_by('id').values_list(
            'walk_list_id', 'voter_key', 'code').iterator(chunk_size=5000):
        state[(walk_list, key)] = code
    return {key for (_, key), code in state.items() if code is not None}


class Knocked:
    """The doors with a standing result and the people at them. `doors` is {(stop id, unit)} with '' for no unit; `pos` is
    bool[n] in score-store order, True for a person at one of those doors or a knocked voter with no stop; `people` counts
    them. `pos` is worked out on first use (it needs the score store). Replaced, never changed."""

    def __init__(self, version, stamp, doors, stopless):
        self.version, self.stamp, self.doors, self._stopless = version, stamp, doors, tuple(stopless)
        self._pos, self._lock = None, threading.Lock()

    @property
    def pos(self):
        if self._pos is None:
            with self._lock:
                if self._pos is None:
                    mask = np.zeros(terms().n, dtype=bool)
                    if self.doors or self._stopless:
                        self._mark(mask)
                    mask.flags.writeable = False
                    self._pos = mask
        return self._pos

    @property
    def people(self):
        return int(self.pos.sum())

    def _mark(self, mask):
        with W.walk_store() as store:
            if not store.attach_scores():
                raise SourceStoreError(NO_SCORES, 503)
            for chunk in W._chunks(sorted({stop for stop, _ in self.doors})):
                for pos, stop, unit in store.conn.execute(
                        "SELECT sc.pos, p.stop_id, IFNULL(p.unit, '') FROM person p JOIN scores.person sc "
                        "ON sc.person_id = p.person_id WHERE p.stop_id IN (%s)" % ','.join('?' * len(chunk)), chunk):
                    if (stop, unit) in self.doors:
                        mask[pos - 1] = True
            for chunk in W._chunks(self._stopless):
                for (pos,) in store.conn.execute(
                        'SELECT sc.pos FROM person p JOIN scores.person sc ON sc.person_id = p.person_id '
                        'WHERE p.person_id IN (%s)' % ','.join('?' * len(chunk)), chunk):
                    mask[pos - 1] = True


_knocked_lock = threading.Lock()
_knocked = None
_memo = (None, {})              # (walk stamp, {voter key: (stop id or None, unit, person id) or None})


def _doors_of(keys, walk_stamp):
    """[(stop id or None, unit, person id)] of the voters with these keys who are in the walk store now. A key is looked up
    once per walk store version. ponytail: one pass over the walk person table for each batch of new keys (about 1 s for
    560,000 people), because voter_key is not indexed; an index in tools/build_walk.py would make it a lookup (a rebuild)."""
    global _memo
    if _memo[0] != walk_stamp:
        _memo = (walk_stamp, {})
    memo = _memo[1]
    new = {key for key in keys if key not in memo}
    if new:
        with W.walk_store() as store:
            for key, stop, unit, person in store.conn.execute(
                    "SELECT voter_key, stop_id, IFNULL(unit, ''), person_id FROM person"):
                if key in new:
                    memo[key] = (stop, unit, person)
        for key in new:
            memo.setdefault(key, None)
    return [memo[key] for key in keys if memo[key] is not None]


def knocked():
    """The Knocked of the saved lists' results now, cached on (results version, store stamps). With no result, or no walk
    store, nobody is knocked."""
    global _knocked
    stamp = _stamp()
    cached = _knocked
    if cached is not None and cached.version == results_version() and cached.stamp == stamp:
        return cached
    with _knocked_lock:
        version = results_version()               # before the scan, so the cache is never older than its version
        cached = _knocked
        if cached is not None and cached.version == version and cached.stamp == stamp:
            return cached
        keys = standing_keys() if stamp[1] else set()
        found = _doors_of(keys, stamp[1]) if keys else []
        _knocked = Knocked(version, stamp, frozenset((stop, unit) for stop, unit, _ in found if stop is not None),
                           sorted(person for stop, _, person in found if stop is None))
        return _knocked


# ---- area sums ---------------------------------------------------------------------------------------------------

@lru_cache(maxsize=12)
def _area_codes(level, stamp):
    """(sorted area ids, int32[n] index of each person's area in them, -1 for none) of one level."""
    n = terms().n
    values = [''] * n
    if level in STATE_LEVELS:
        fips = settings.DISTRICT['state_fips']
        with W.walk_store() as store:
            if not store.attach_scores():
                raise SourceStoreError(NO_SCORES, 503)
            for pos, code in store.conn.execute('SELECT sc.pos, p.%s FROM person p JOIN scores.person sc '
                                                'ON sc.person_id = p.person_id' % STATE_LEVELS[level]):
                if code and STATE_CODE.fullmatch(code):
                    values[pos - 1] = fips + code.zfill(3)
    else:
        try:
            with closing(DB._open(DB.store_path())) as conn:
                conn.row_factory = None
                values = [v or '' for (v,) in conn.execute(
                    'SELECT %s FROM person ORDER BY pos' % S.AREA_LEVEL_OF[('registration', level)])]
        except sqlite3.Error as problem:
            raise SourceStoreError(UNREADABLE, 503) from problem
    ids, inverse = np.unique(np.array(values), return_inverse=True)
    first = 1 if len(ids) and ids[0] == '' else 0         # '' (no area) sorts first
    return [str(i) for i in ids[first:]], (inverse.reshape(-1) - first).astype(np.int32)


def _label(level, ident):
    if level in STATE_LEVELS:
        return GL.label(level, ident, settings.DISTRICT)
    return area_label(level, ident, settings.DISTRICT)


def area_payload(level):
    """The body of GET /api/v3/untapped (schema v3-untapped-1): per area, the people counted and left out (they live at a
    door with a result), and over the counted people the sums of undecided, supporter, undecided x i and supporter x i."""
    if level not in LEVELS:
        raise BadRequest('Invalid level')
    t, left = terms(), knocked()
    ids, codes = _area_codes(level, t.stamp)
    size = len(ids)
    out = left.pos
    counted, dropped = (codes >= 0) & ~out, (codes >= 0) & out

    def total(weights=None):
        return np.bincount(codes[counted], weights=None if weights is None else weights[counted], minlength=size)

    def rounded(array):
        return np.round(array, 6).tolist()

    return {
        'schema': SCHEMA, 'level': level, 'ids': ids, 'labels': [_label(level, i) for i in ids],
        'people': total().tolist(), 'knocked': np.bincount(codes[dropped], minlength=size).tolist(),
        'undecided': rounded(total(t.undecided)), 'supporter': rounded(total(t.supporter)),
        'undecided_i': rounded(total(t.undecided * t.i)), 'supporter_i': rounded(total(t.supporter * t.i)),
        'defaults': dict(zip(('wp', 'wi'), default_weights())), 't_source': t.t_source,
        'results': {'version': left.version, 'people_knocked': left.people}}


def reset():
    """Forget everything cached (tests, and after the stores are replaced)."""
    global _terms, _knocked, _memo
    with _terms_lock, _knocked_lock:
        _terms = _knocked = None
        _memo = (None, {})
    _area_codes.cache_clear()
