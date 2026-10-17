"""Untapped potential (Paul 2026-10-09): how much of each person is still unworked, summed over areas, and who is already worked.

For every person not at a door with a recorded result:
  s  candidate support on the absolute scale, support.sup_score / 100 (0.5 when the person has no support row)
  t  chance of voting: the mean of the five turnout-only scores (field_labels.TURNOUT_FIELDS) / 100, read from the stored
     evidence literals (not a percentile, not the exposure), lined up against the district's own general-election record
     (see fit_turnout); else the person's general-election record (gen_voted / gen_elig of the walk store); else 0.5
  i  sphere of influence standing, person.soi_pct / 100 (0 without it)
  undecided = (1 - |2s - 1|) x t        supporter = s x (1 - t)
  potential = (wp x undecided + (1 - wp) x supporter) x (1 + wi x i)        field_labels.potential

Nothing is written: terms() decodes the score store once per file version, knocked() reads the saved walk lists' results,
and area_payload() adds the four sums per area (undecided, supporter and each times i) so the browser can apply the weights
without asking again. The weights are linear in these sums, so the map and the list move at once with the sliders.

  terms()            the per-person arrays, cached per (score store, walk store) file stamps
  fit_turnout(...)   the monotone curve of raw t against people's own general-election record, per district
  knocked()          the doors that have a result in some saved list, cached per results version
  results_version()  changes whenever a result is saved or a list deleted
  parse_weights(q)   wp and wi from a query dict, the district's defaults when absent
  area_payload(l)    the body of GET /api/v3/untapped?level=l
"""
import itertools
import logging
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
FIT_MIN_PEOPLE = 1000                                                 # fewer people with a turnout score and an eligible general: t stays raw

log = logging.getLogger('cu.untapped')

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
    read-only; a Terms is replaced, never changed. t_fit is {'n', 'points', 'method'} of the calibration of t, None when t is
    the raw mean of the turnout scores."""
    __slots__ = ('n', 's', 't', 'i', 'undecided', 'supporter', 't_source', 't_fit', 'stamp')

    def __init__(self, stamp, s, t, i, t_source, t_fit=None):
        self.stamp, self.n, self.s, self.t, self.i, self.t_source = stamp, len(s), s, t, i, t_source
        self.t_fit = None if t_fit is None else dict(t_fit)
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


def _record(n, scores_path, walk_path):
    """(voted, eligible): float64[n] general elections each person voted in and was eligible for (the walk store's gen_voted
    and gen_elig; 0 and 0 without an eligible general, or without a usable walk store)."""
    voted, eligible = np.zeros(n), np.zeros(n)
    if walk_path is None:
        return voted, eligible
    try:
        with closing(sqlite3.connect(walk_path.as_uri() + '?mode=ro', uri=True, timeout=3)) as conn:
            conn.execute('ATTACH DATABASE ? AS scores', (scores_path.as_uri() + '?mode=ro',))
            rows = np.fromiter(itertools.chain.from_iterable(conn.execute(
                'SELECT sc.pos, IFNULL(p.gen_voted, 0), p.gen_elig FROM person p JOIN scores.person sc '
                'ON sc.person_id = p.person_id WHERE p.gen_elig > 0')), dtype=np.float64).reshape(-1, 3)
    except sqlite3.Error:
        return voted, eligible              # no usable walk store: those people fall to even odds
    at = rows[:, 0].astype(np.intp) - 1
    voted[at], eligible[at] = rows[:, 1], rows[:, 2]
    return voted, eligible


def _fit(x, voted, elig):
    """((centres, values), None), or (None, why) when the raw t is left as it is: fewer than FIT_MIN_PEOPLE people, a raw t or
    a record that is not finite, or fewer than two blocks once the record is made non-decreasing in raw t."""
    x, voted, elig = (np.asarray(v, dtype=np.float64) for v in (x, voted, elig))
    if len(x) < FIT_MIN_PEOPLE:
        return None, 'fewer than %d people with a turnout score and an eligible general: %d' % (FIT_MIN_PEOPLE, len(x))
    if not (np.isfinite(x).all() and np.isfinite(voted).all() and np.isfinite(elig).all()):
        return None, 'a turnout score or a record is not finite'
    voted = np.clip(voted, 0.0, elig)
    distinct, inverse = np.unique(x, return_inverse=True)
    votes, trials = np.bincount(inverse.reshape(-1), voted), np.bincount(inverse.reshape(-1), elig)
    blocks = []                      # pool adjacent violators: [votes, trials, trials x raw t], the rate of each above the last
    for k in np.flatnonzero(trials > 0):
        blocks.append([votes[k], trials[k], trials[k] * distinct[k]])
        while len(blocks) > 1 and blocks[-2][0] * blocks[-1][1] >= blocks[-1][0] * blocks[-2][1]:    # rate before >= rate after
            last = blocks.pop()
            blocks[-1] = [blocks[-1][i] + last[i] for i in range(3)]
    if len(blocks) < 2:
        return None, 'fewer than 2 blocks: the record does not rise with the turnout scores (%d)' % len(blocks)
    votes, trials, weighted = np.array(blocks).T
    return (weighted / trials, votes / trials), None


def fit_turnout(x, voted, elig):
    """(centres, values): the curve that lines the raw t x of each person up against their general-election record (`voted` of
    `elig` generals) without ever turning the order round. The people are summed by distinct raw t, the record rate of each
    is made non-decreasing in raw t by weighted pool-adjacent-violators (weights `elig`), and each pooled block is a point:
    its centre is the mean raw t weighted by `elig`, its value the votes over the generals of the block. Centres and values
    strictly increase. t = np.interp(raw t, centres, values): strictly increasing between the first and last centre, flat
    outside them. None when the raw t is better left as it is (see _fit): fewer than 1000 people, a value that is not
    finite, or fewer than two blocks."""
    return _fit(x, voted, elig)[0]


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
    raw = np.clip(t, 0.0, 1.0)
    voted, eligible = _record(n, path, (Path(settings.DATA_DIR) / 'walk.sqlite3').resolve() if stamp[1] else None)
    record = ~sai & (eligible > 0)
    t = np.where(sai, raw, np.where(record, np.clip(voted / np.maximum(eligible, 1.0), 0.0, 1.0), EVEN_ODDS))
    source = {'sai': int(sai.sum()), 'record': int(record.sum()), 'even': int((~sai & ~record).sum())}
    t, t_fit = _calibrate(t, raw, sai, voted, eligible)
    return Terms(stamp, s, t, i, source, t_fit)


def _calibrate(t, raw, sai, voted, eligible):
    """(t, t_fit): t with every person who has a raw t (`sai`) replaced by the district's curve of it (fit_turnout against the
    general-election record of the people who also had an eligible general); the same t and None when the fit is refused."""
    fit_people = sai & (eligible >= 1.0)
    curve, why = _fit(raw[fit_people], voted[fit_people], eligible[fit_people])
    if curve is None:
        log.info('chance of voting not calibrated (%s)', why)
        return t, None
    calibrated = np.interp(raw, *curve)
    log.info('chance of voting calibrated against the general-election record: n=%d, %d points, mean %.4f -> %.4f '
             '(record %.4f)', fit_people.sum(), len(curve[0]), raw[fit_people].mean(), calibrated[fit_people].mean(),
             np.clip(voted[fit_people], 0.0, eligible[fit_people]).sum() / eligible[fit_people].sum())
    return np.where(sai, calibrated, t), {'n': int(fit_people.sum()), 'points': len(curve[0]), 'method': 'monotone'}


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

def _stored_precinct_codes(stamp, n):
    """Reuse the score store's verified person-list column instead of rescanning its wide person table.

    Older stores without list columns keep the SQL path. Only the same file version as the turnout terms may supply
    codes. The writer's factorize contract is sorted nonempty IDs, integer codes, and -1 for no area. A malformed or
    incomplete derived column falls back to the authoritative person rows.
    """
    try:
        info = DB.info()
    except DB.StoreError:
        return None
    if (str(info.path), *info.stamp) != stamp[0] or info.n != n:
        return None
    ids, codes = info.dicts.get('a_reg_precinct'), info.columns.get('a_reg_precinct')
    if (not isinstance(ids, list) or not all(isinstance(ident, str) and ident for ident in ids)
            or ids != sorted(set(ids)) or not isinstance(codes, np.ndarray) or codes.shape != (n,)
            or codes.dtype.kind not in 'iu'):
        return None
    used = np.unique(codes)
    present = used[used >= 0]
    if ((len(used) and used[0] < -1) or not np.array_equal(present, np.arange(len(ids)))):
        return None
    return list(ids), codes.astype(np.int32, copy=False)


@lru_cache(maxsize=12)
def _area_codes(level, stamp):
    """(sorted area ids, int32[n] index of each person's area in them, -1 for none) of one level."""
    n = terms().n
    if level == 'precinct':
        stored = _stored_precinct_codes(stamp, n)
        if stored is not None:
            return stored
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
    door with a result), and over the counted people the sums of undecided, supporter, undecided x i and supporter x i. Also
    where t came from (t_source) and how it was lined up against the general-election record ({n, points, method}, or null)."""
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
        'defaults': dict(zip(('wp', 'wi'), default_weights())), 't_source': t.t_source, 't_calibration': t.t_fit,
        'results': {'version': left.version, 'people_knocked': left.people}}


def reset():
    """Forget everything cached (tests, and after the stores are replaced)."""
    global _terms, _knocked, _memo
    with _terms_lock, _knocked_lock:
        _terms = _knocked = None
        _memo = (None, {})
    _area_codes.cache_clear()
