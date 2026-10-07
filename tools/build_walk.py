"""Build the walk-list store `walk.sqlite3` (schema walk-v1) from the source voter CSV and the local stores.

    <app venv python> tools/build_walk.py [--district ID] [--source CSV] [--data-dir DIR] [--out FILE] [--force]

CSV data row i is contacts.rowid i+1 and carries the person id every store uses (never recomputed: .street-salt is
gone). The row count and the sha256 of the file must agree with the contact, score and street stores; a mismatch is an
error (exit 1, --force does not override it). Per person the store keeps the walk address (street, stop, unit), the
area codes exactly as the file spells them, the vote-history tallies of the district's window (tools/districts.py;
Colorado's is server/research/walk_names.py) and an HMAC voter key (.walk-salt in the data directory, created on first
use, never printed). It holds no name, address text or birth date. Cities are canonicalised (CO SPGS / COLO SPRINGS ->
COLORADO SPRINGS) so one street is one row; dates are read in whichever of DATE_FORMATS the file uses (qa.dates names
the format, never a value). --district (default co-05) picks the config entry: UTM zone, primary dates, mail rule and
its gate, city column and spelling rules, precinct key. A district with person_fields 'csv' (OR, CT: no score store
yet) takes age, party, precinct and census block from the source CSV and records person_fields = csv in meta.
Everyone with a street gets a stop (a blank house number too: house '', parity NULL) and every stop a place on a
network that is one component (tools/walk_geo.py: virtual streets, connectors, tiers a-e and g).

Quality gates (all must hold, else the qa metrics are printed, any existing file stays and the exit code is 2;
--force replaces the file anyway and records it in qa):
  no stop without a map position, no stop without a span     largest road component = 100% of the network length
  side agreement >= 85%            G-abs: M*/A* >= 85% of recorded votes in each of 2020g, 2022g, 2024g (only where the
                                   district sets abs_gate; the shares are always reported in qa.g_abs)
Exit codes: 0 built and in place, 1 error, 2 gate failure. Output is aggregates only.
"""
import os

os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('OMP_NUM_THREADS', '1')

import argparse     # noqa: E402
import csv          # noqa: E402
import hashlib      # noqa: E402
import hmac         # noqa: E402
import json         # noqa: E402
import re           # noqa: E402
import secrets      # noqa: E402
import sqlite3      # noqa: E402
import sys          # noqa: E402
import tempfile     # noqa: E402
import time         # noqa: E402
import traceback    # noqa: E402
from collections import Counter  # noqa: E402
from contextlib import closing, contextmanager  # noqa: E402
from datetime import date, datetime, timezone  # noqa: E402
from functools import lru_cache  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.research import walk_names as N  # noqa: E402
from tools import districts as D  # noqa: E402
from tools import walk_geo as G  # noqa: E402

SCHEMA = 'walk-v1'
ENCODING = 'cp1252'
CHUNK_ROWS = 20000                      # the C parser tokenises all ~620 columns of a chunk: keep chunks small
BATCH_ROWS = 50000
P = 'svi_registration_'
GATES = {'unplaced_stops': 0, 'stops_without_span': 0, 'largest_component': 1.0, 'side_agreement': 0.85,
         'abs_share': N.ABS_GATE['min_share']}
CEILINGS = ('unplaced_stops', 'stops_without_span')      # gates that hold at or below their limit; the rest at or above
ELECTIONS = list(N.PRIMARIES) + list(N.GENERALS)          # column order of every vote array: primaries, then generals
N_PRI = len(N.PRIMARIES)
ELECTION_KEYS = np.array([int({**N.PRIMARIES, **N.GENERALS}[e].replace('-', '')) for e in ELECTIONS], dtype=np.int64)
VOTE_FIELDS = [N.VH_FIELD.format(e) for e in ELECTIONS]
TEXT = {'house': P + 'addressrange', 'predir': P + 'predirection', 'name': P + 'streetname', 'suffix': P + 'suffix',
        'postdir': P + 'postdirection', 'city': P + 'city', 'zip': P + 'zip', 'ss': P + 'state_senate_district',
        'sh': P + 'state_house_district', 'gender': 'sii_gender'}
PLACED_BY = ('geocode', 'fuzzy', 'nearest', 'interpolated')
TIERS = ('a', 'b', 'c', 'd', 'e', 'g')                   # tools/walk_geo.py
AREAS = ('block', 'precinct', 'zip', 'all')               # tier g centres, most local first
REPLACE_TRIES, REPLACE_WAIT_S = 20, 0.25                 # the app holds the store for one request at a time: wait it out

DDL = [
    'CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)',
    'CREATE TABLE street(id INTEGER PRIMARY KEY, key TEXT, city TEXT, zip TEXT, display TEXT, UNIQUE(key, city))',
    'CREATE TABLE stop(id INTEGER PRIMARY KEY, street_id INTEGER, house TEXT, house_int INTEGER, parity INTEGER, '
    'span_id INTEGER, pos_m REAL, side_lr INTEGER, placed_by TEXT, lon REAL, lat REAL)',
    'CREATE TABLE person(person_id TEXT PRIMARY KEY, household_id TEXT, stop_id INTEGER, street_id INTEGER, '
    'unit TEXT, ss TEXT, sh TEXT, precinct TEXT, city TEXT, zip TEXT, party TEXT, age INTEGER, gender TEXT, '
    'reg_date TEXT, pri_voted INTEGER, pri_elig INTEGER, gen_voted INTEGER, gen_elig INTEGER, abs_voted INTEGER, '
    'abs_elig INTEGER, voter_key TEXT) WITHOUT ROWID',
    'CREATE TABLE combo(ss TEXT, sh TEXT, precinct TEXT, street_id INTEGER, people INTEGER, doors INTEGER)',
]
INDEXES = [
    'CREATE INDEX person_street ON person(street_id)',
    'CREATE INDEX person_stop ON person(stop_id)',
    'CREATE INDEX person_area ON person(ss, sh, precinct)',
    'CREATE INDEX stop_street ON stop(street_id)',
]


class BuildError(RuntimeError):
    """A failed check. Messages carry counts and column names only, never values from the data."""


def peak_memory_mb():
    """Peak working set of this process in MB (Windows; None where the call is unavailable)."""
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD), ('peak', ctypes.c_size_t),
                        ('working', ctypes.c_size_t), ('peak_paged', ctypes.c_size_t), ('paged', ctypes.c_size_t),
                        ('peak_nonpaged', ctypes.c_size_t), ('nonpaged', ctypes.c_size_t),
                        ('pagefile', ctypes.c_size_t), ('peak_pagefile', ctypes.c_size_t)]
        kernel, psapi, counters = ctypes.windll.kernel32, ctypes.windll.psapi, Counters()
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        counters.cb = ctypes.sizeof(counters)
        return round(counters.peak / 2 ** 20) if psapi.GetProcessMemoryInfo(
            kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb) else None
    except (AttributeError, OSError, ImportError):
        return None


class Clock:
    def __init__(self, log=None):
        self.log, self.marks, self.began = log, {}, time.time()

    @contextmanager
    def phase(self, name):
        began = time.time()
        yield
        self.marks[name] = round(time.time() - began, 1)
        if self.log:
            self.log('%-12s %7.1fs   peak memory %s MB' % (name, self.marks[name], peak_memory_mb()))

    def total(self):
        return round(time.time() - self.began, 1)


# ---- text normalisers (applied once per distinct value, never per row) -----------------------------------------------
def canonical_city(text, district=None):
    """Upper case, punctuation dropped, the district's postal abbreviations spelled out (tools/districts.py): in
    Colorado 'CO SPGS', 'COLO SPRINGS' and 'Colorado Spgs.' are all 'COLORADO SPRINGS'; 'GRN MTN FLS' is 'GREEN
    MOUNTAIN FALLS'."""
    return D.canonical_city(text, district)


def unit_label(text):
    """'APT 4' -> 'Apt 4', '# 12B' -> '# 12B': all-letter words are capitalised, any other word stays upper case."""
    return ' '.join(w.capitalize() if w.isalpha() else w.upper() for w in str(text).split())


def house_label(text):
    return ' '.join(str(text).upper().split())


class Vocab:
    """Strings -> dense int32 codes that stay valid across chunks; `norm` runs once per distinct value."""

    def __init__(self, norm=str.strip):
        self.norm, self.index, self.values, self.raw = norm, {}, [], set()

    def encode(self, column):
        codes, uniques = pd.factorize(column.fillna('').to_numpy(dtype=object))
        self.raw.update(uniques.tolist())
        table = np.empty(len(uniques), dtype=np.int32)
        for j, value in enumerate(uniques):
            value = self.norm(value)
            code = self.index.get(value)
            if code is None:
                code = self.index[value] = len(self.values)
                self.values.append(value)
            table[j] = code
        return table[codes]

    def array(self):
        return np.array(self.values, dtype=object)


# ---- dates -----------------------------------------------------------------------------------------------------------
DATE_FORMATS = ('%Y-%m-%d', '%m/%d/%Y', '%Y%m%d', '%Y/%m/%d', '%m-%d-%Y', '%d/%m/%Y', '%d-%m-%Y', '%m%d%Y',
                '%d-%b-%Y', '%b %d, %Y', '%B %d, %Y', '%m/%d/%y', '%m-%d-%y', '%d-%b-%y')
DATE_YEARS = (1880, 2100)               # anything outside is treated as not a date
DETECT_ROWS = 20000
_TIME_TAIL = r'^(\S+)[T ]\d{1,2}:\d{2}.*$'


def date_keys(text, fmt):
    """yyyymmdd ints for stripped, non-blank strings; 0 where the text is no date in `fmt` ('mixed': pandas' per-value
    parser) or the year is implausible."""
    stamps = pd.to_datetime(text, format=fmt, errors='coerce')
    year, month, day = (getattr(stamps.dt, part).fillna(0).to_numpy(np.int64) for part in ('year', 'month', 'day'))
    if '%y' in fmt:                                       # a two-digit year never means the future
        year = np.where(year > date.today().year, year - 100, year)
    ok = stamps.notna().to_numpy() & (year >= DATE_YEARS[0]) & (year <= DATE_YEARS[1])
    return np.where(ok, year * 10000 + month * 100 + day, 0)


class DateColumn:
    """One date column across chunks. The format is the one of DATE_FORMATS that parses most of the first chunk with
    values (earlier entries win ties, so month-first beats day-first when nothing tells them apart); later values that
    fail try the other formats and, last, pandas' per-value parser. Values that still fail are counted, stored as 0."""

    def __init__(self, name):
        self.name, self.format = name, None
        self.counts = {'rows': 0, 'blank': 0, 'unparseable': 0}
        self.rescued = Counter()                          # format -> values it parsed although it is not the main one

    def _detect(self, text):
        best, rate = None, 0.0
        sample = text.iloc[:DETECT_ROWS]
        for fmt in DATE_FORMATS:
            share = float((date_keys(sample, fmt) > 0).mean())
            if share > rate:
                best, rate = fmt, share
        return best

    def parse(self, column):
        text = column.str.strip().str.replace(_TIME_TAIL, r'\1', regex=True)
        present = (text != '').to_numpy()
        keys = np.zeros(len(text), dtype=np.int64)
        self.counts['rows'] += len(text)
        self.counts['blank'] += int((~present).sum())
        if present.any():
            text = text[present]
            if self.format is None:
                self.format = self._detect(text)
            got = date_keys(text, self.format) if self.format else np.zeros(len(text), dtype=np.int64)
            for fmt in (*[f for f in DATE_FORMATS if f != self.format], 'mixed'):
                bad = np.flatnonzero(got == 0)
                if not len(bad):
                    break
                try:
                    found = date_keys(text.iloc[bad], fmt)
                except (ValueError, TypeError, OverflowError):
                    continue
                got[bad] = found
                if (found > 0).any():
                    self.rescued[fmt] += int((found > 0).sum())
            keys[present] = got
            self.counts['unparseable'] += int((got == 0).sum())
        return keys.astype(np.int32)

    def report(self):
        return {'format': self.format, **self.counts, 'parsed_by_other_formats': dict(self.rescued)}


def iso_dates(keys):
    return ['%04d-%02d-%02d' % (k // 10000, k // 100 % 100, k % 100) if k else None for k in keys.tolist()]


# ---- votes -----------------------------------------------------------------------------------------------------------
def vote_flags(column, prefixes=N.ABS_PREFIXES):
    """(voted, mail) boolean arrays of one vote-history column: blank = did not vote; a code counts as a mail ballot
    when its first letter is in `prefixes` (None: every recorded vote counts, as in Oregon)."""
    codes, uniques = pd.factorize(column.fillna('').to_numpy(dtype=object))
    texts = [u.strip().upper() for u in uniques]
    voted = np.array([t != '' for t in texts], dtype=bool)
    mail = voted if prefixes is None else np.array([t != '' and t[:1] in prefixes for t in texts], dtype=bool)
    return voted[codes], mail[codes]


def history(voted, mail, reg, dob, keys=ELECTION_KEYS):
    """Per-person tallies. eligible(election) = voted in it, or registered on/before its day (`keys`: yyyymmdd of each
    election, the district's calendar) and 18 by then."""
    registered = (reg[:, None] > 0) & (reg[:, None] <= keys)
    adult = (dob[:, None] > 0) & (dob[:, None].astype(np.int64) + 180000 <= keys)
    eligible = voted | (registered & adult)
    pri, gen = slice(0, N_PRI), slice(N_PRI, None)
    return {'pri_voted': voted[:, pri].sum(1), 'pri_elig': eligible[:, pri].sum(1),
            'gen_voted': voted[:, gen].sum(1), 'gen_elig': eligible[:, gen].sum(1),
            'abs_voted': (voted & mail).sum(1), 'abs_elig': eligible.sum(1)}


# ---- stores and source -----------------------------------------------------------------------------------------------
def _uri(path):
    return Path(path).resolve().as_uri() + '?mode=ro'


def _stored(connection, schema, key):
    row = connection.execute('SELECT value FROM %smetadata WHERE key=?' % schema, (key,)).fetchone()
    return json.loads(row[0]) if row else None


def sha256_of(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_or_create_salt(path):
    try:
        with path.open('xb') as stream:
            stream.write(secrets.token_hex(32).encode('ascii'))
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except FileExistsError:
        pass
    salt = path.read_bytes().strip()
    if len(salt) < 32:
        raise BuildError('The walk salt file holds fewer than 32 bytes')
    return salt


def missing_columns(path, names):
    with Path(path).open(encoding=ENCODING, newline='') as stream:
        header = next(csv.reader(stream), [])
    return [c for c in names if c not in header]


def read_person_fields(path, count, d):
    """(age with -1 for blank, party, precinct key, census block) of every CSV row, in row order, from one chunked pass
    over the file: what a district without a score store (person_fields 'csv') has instead of scores.person. The party
    is the raw code, stripped; the precinct key is districts.precinct_key (county / name columns read only when the
    key needs them), checked against the district's pattern."""
    code, county, name = P + 'precinct_code', P + 'county', P + 'precinct_name'
    area = {'code': (code, None, None), 'county-code': (code, county, None),
            'code-name': (code, None, name)}[d['precinct_key']]          # the columns of precinct_key(code, county, name)
    names = ['sii_age', 'svi_party_registration', *(c for c in area if c), 'svi_registration_censusid']
    missing = missing_columns(path, names)
    if missing:
        raise BuildError('Source columns missing: ' + ', '.join(missing))
    key = lru_cache(maxsize=None)(lambda *parts: D.precinct_key(d, *parts))
    age, party, precinct, block = [], [], [], []
    with pd.read_csv(path, usecols=names, dtype=str, encoding=ENCODING, na_filter=False, chunksize=CHUNK_ROWS) as reader:
        for chunk in reader:
            years = pd.to_numeric(chunk['sii_age'].str.strip(), errors='coerce').fillna(-1)
            age.append(years.to_numpy(dtype=np.int32))
            party.extend(chunk['svi_party_registration'].str.strip().tolist())
            blank = [''] * len(chunk)
            precinct.extend(key(*parts) for parts in zip(*(chunk[c].tolist() if c else blank for c in area)))
            block.extend(chunk['svi_registration_censusid'].str.strip().tolist())
    if len(party) != count:
        raise BuildError('Source has %d rows, the contacts store %d' % (len(party), count))
    wrong = sum(1 for k in set(precinct) if k and (len(k) > 64 or not re.fullmatch(d['precinct_pattern'], k)))
    if wrong:
        raise BuildError('%d precinct keys are longer than 64 characters or do not match the %s pattern'
                         % (wrong, d['id']))
    return np.concatenate(age) if age else np.array([], dtype=np.int32), party, precinct, block


def load_people(data_dir, checksum, source=None, district=None):
    """Contact ids in rowid order, the person fields of each (age, party, precinct, census block; from the score store,
    with block '' where it has no such column, or from `source` when the district's person_fields is 'csv', see
    read_person_fields) and the registration household (id, lon, lat) of each person that has one. Every store must
    have been built from the CSV with this sha256."""
    d = D.get(district)
    from_csv = d['person_fields'] == 'csv'
    contacts = sqlite3.connect(_uri(data_dir / 'contacts.sqlite3'), uri=True)
    try:
        if not from_csv:
            contacts.execute('ATTACH DATABASE ? AS sc', (_uri(data_dir / 'scores.sqlite3'),))
        contacts.execute('ATTACH DATABASE ? AS st', (_uri(data_dir / 'street.sqlite3'),))
        for label, recorded in (('contacts', _stored(contacts, '', 'source_sha256')),
                                *(() if from_csv else (('scores', _stored(contacts, 'sc.', 'contacts_source_sha256')),)),
                                ('street', _stored(contacts, 'st.', 'source_sha256'))):
            if recorded != checksum:
                raise BuildError('The %s store was built from a different source file (sha256 differs)' % label)
        count, low, high = contacts.execute('SELECT count(*), min(rowid), max(rowid) FROM contacts').fetchone()
        if not count or low != 1 or high != count:
            raise BuildError('contacts rowids are not 1..N (%d rows)' % count)
        if from_csv:
            ids = [r[0] for r in contacts.execute('SELECT id FROM contacts ORDER BY rowid')]
            age, party, precinct, block = read_person_fields(source, count, d)
        else:
            ids, found, age, party, precinct, block = [], 0, [], [], [], []
            has_block = any(r[1] == 'reg_block' for r in contacts.execute('PRAGMA sc.table_info(person)'))
            for person_id, has_score, years, group, area, census in contacts.execute(
                    'SELECT c.id, p.person_id IS NOT NULL, p.age, p.party, p.reg_precinct, %s FROM contacts c '
                    'LEFT JOIN sc.person p ON p.person_id = c.id ORDER BY c.rowid'
                    % ('p.reg_block' if has_block else "''")):
                ids.append(person_id)
                found += has_score
                age.append(-1 if years is None else years)
                party.append(group or '')
                precinct.append(area or '')
                block.append(census or '')
            if found != count:
                raise BuildError('%d contacts have no row in the score store' % (count - found))
        household = np.full(count, None, dtype=object)
        lon, lat = np.full(count, np.nan), np.full(count, np.nan)
        for rowid, household_id, x, y in contacts.execute(
                "SELECT c.rowid, h.id, h.longitude, h.latitude FROM contacts c JOIN st.people sp ON sp.id = c.id "
                "JOIN st.households h ON h.id = sp.household_id AND h.basis = 'registration' ORDER BY c.rowid"):
            household[rowid - 1], lon[rowid - 1], lat[rowid - 1] = household_id, x, y
    finally:
        contacts.close()
    parties, precincts, blocks = Vocab(), Vocab(), Vocab()
    handle, handles = pd.factorize(household)               # household ids as codes; the 491k-slot string array goes
    return SimpleNamespace(
        ids=ids, age=np.array(age, dtype=np.int32), party=parties, precinct=precincts, block=blocks,
        party_code=parties.encode(pd.Series(party)), precinct_code=precincts.encode(pd.Series(precinct)),
        block_code=blocks.encode(pd.Series(block)), household_code=handle,
        household_values=np.asarray(handles, dtype=object), lon=lon, lat=lat)


def read_source(path, ids, salt, district=None):
    """One pass over the CSV (usecols only, chunked): street/area text as vocabulary codes, dates as yyyymmdd ints,
    vote flags, voter keys. The city comes from the district's city column and spelling rules. Returns an attribute
    bag; raises BuildError when the row count differs from `ids`."""
    d = D.get(district)
    text = {**TEXT, 'city': d['city_field']}
    needed = list(dict.fromkeys(['svi_voter_id', 'sii_dob', P + 'date', P + 'suitename', P + 'suiterange',
                                 *text.values(), *VOTE_FIELDS]))
    missing = missing_columns(path, needed)
    if missing:
        raise BuildError('Source columns missing: ' + ', '.join(missing))
    city = lru_cache(maxsize=None)(lambda value: D.canonical_city(value, d))
    norms = {'house': house_label, 'city': city, 'unit': unit_label}
    vocab = {name: Vocab(norms.get(name, str.strip)) for name in (*text, 'unit')}
    codes = {name: [] for name in vocab}
    dates = {'dob': DateColumn('sii_dob'), 'reg': DateColumn(P + 'date')}
    keys = {'dob': [], 'reg': []}
    voted, mail, digests = [], [], []
    recorded = np.zeros((2, len(ELECTIONS)), dtype=np.int64)
    blank_ids = rows = 0
    reader = pd.read_csv(path, usecols=needed, dtype=str, encoding=ENCODING, na_filter=False, chunksize=CHUNK_ROWS)
    with reader:
        for chunk in reader:
            size = len(chunk)
            if rows + size > len(ids):
                raise BuildError('The source has more rows than the contacts store (%d)' % len(ids))
            for name, field in text.items():
                codes[name].append(vocab[name].encode(chunk[field]))
            codes['unit'].append(vocab['unit'].encode(chunk[P + 'suitename'] + ' ' + chunk[P + 'suiterange']))
            keys['dob'].append(dates['dob'].parse(chunk['sii_dob']))
            keys['reg'].append(dates['reg'].parse(chunk[P + 'date']))
            v, m = np.empty((size, len(ELECTIONS)), dtype=bool), np.empty((size, len(ELECTIONS)), dtype=bool)
            for j, field in enumerate(VOTE_FIELDS):
                v[:, j], m[:, j] = vote_flags(chunk[field], d['abs_prefixes'])
            voted.append(v)
            mail.append(m)
            recorded[0] += v.sum(0)
            recorded[1] += m.sum(0)
            for k, voter in enumerate(chunk['svi_voter_id'].tolist()):
                voter = voter.strip()
                blank_ids += not voter
                message = voter.encode('utf-8') if voter else b'person:' + ids[rows + k].encode('utf-8')
                digests.append(hmac.digest(salt, message, 'sha256'))
            rows += size
    if rows != len(ids):
        raise BuildError('Source has %d rows, the contacts store %d' % (rows, len(ids)))
    blob = b''.join(digests)
    del digests
    return SimpleNamespace(
        rows=rows, vocab=vocab, codes={name: np.concatenate(parts) for name, parts in codes.items()},
        dob=np.concatenate(keys['dob']), reg=np.concatenate(keys['reg']), dates=dates,
        voted=np.concatenate(voted), mail=np.concatenate(mail), recorded=recorded, voter_blob=blob,
        blank_voter_ids=blank_ids, duplicate_voter_keys=rows - len(np.unique(np.frombuffer(blob, dtype='S32'))))


# ---- streets, stops ----------------------------------------------------------------------------------------------------
def make_streets(src):
    """Street rows unique per (key, city): id by sorted (key, city), display from the commonest spelling, zip the
    commonest non-blank one. Returns (rows (id, key, city, zip, display), street id per person; 0: no street name)."""
    v, c = src.vocab, src.codes
    packed = c['name'].astype(np.int64)
    for k in ('predir', 'suffix', 'postdir', 'city'):
        packed = packed * len(v[k].values) + c[k]
    _, first, inverse, counts = np.unique(packed, return_index=True, return_inverse=True, return_counts=True)
    groups, labelled = {}, []
    for j, row in enumerate(first.tolist()):
        parts = tuple(v[k].values[c[k][row]] for k in ('predir', 'name', 'suffix', 'postdir'))
        key = N.street_key(*parts)
        city = v['city'].values[c['city'][row]]
        labelled.append((key, city) if key else None)
        if key:
            shown = groups.setdefault((key, city), {})
            label = N.display_street(*parts)
            shown[label] = shown.get(label, 0) + int(counts[j])
    ordered = sorted(groups)
    index = {pair: i + 1 for i, pair in enumerate(ordered)}
    street_of_combo = np.array([index[p] if p else 0 for p in labelled], dtype=np.int32)
    street_row = street_of_combo[inverse]
    blank_zip = np.array([z == '' for z in v['zip'].values] or [True])
    zips = pd.DataFrame({'street': street_row, 'zip': c['zip']})
    zips = zips[(zips.street > 0) & ~blank_zip[zips.zip.to_numpy()]]
    zips = zips.groupby(['street', 'zip']).size().reset_index(name='n')
    zips['text'] = v['zip'].array()[zips.zip.to_numpy()] if len(zips) else []
    best = zips.sort_values(['street', 'n', 'text'], ascending=[True, False, True]).drop_duplicates('street')
    zip_of = dict(zip(best.street.tolist(), best.text.tolist()))
    rows = []
    for (key, city), i in index.items():
        label = min(groups[(key, city)].items(), key=lambda kv: (-kv[1], kv[0]))[0]
        rows.append((i, key, city, zip_of.get(i, ''), label))
    return rows, street_row


def make_stops(src, street_row, household_code, lon, lat):
    """Stops = distinct (street, house) of everyone with a street, a blank house number included (house '', parity
    NULL, after the numbered ones); ids by (street, house number, house text). location = median of the member
    households' coordinates (NaN when none has any). Returns the per-person stop id (0: none) and per-stop arrays."""
    values = src.vocab['house'].values
    rows = np.flatnonzero(street_row > 0)
    size = max(len(values), 1)
    pair = street_row[rows].astype(np.int64) * size + src.codes['house'][rows]
    unique, first, inverse = np.unique(pair, return_index=True, return_inverse=True)
    street_of, house_of = (unique // size).tolist(), (unique % size).tolist()
    numbers = [N.house_int(h) for h in values]
    order = sorted(range(len(unique)), key=lambda i: (street_of[i], numbers[house_of[i]] is None,
                                                      numbers[house_of[i]] or 0, values[house_of[i]]))
    stop_of_unique = np.empty(len(unique), dtype=np.int64)
    stop_of_unique[order] = np.arange(1, len(unique) + 1)
    stop_row = np.zeros(src.rows, dtype=np.int32)
    stop_row[rows] = stop_of_unique[inverse]
    order = np.array(order, dtype=np.int64)
    houses = [values[h] for h in np.array(house_of, dtype=np.int64)[order].tolist()]
    stop_street = np.array(street_of, dtype=np.int64)[order]
    member = rows[first[order]]                                   # a person of each stop, for its zip
    located = np.flatnonzero((stop_row > 0) & ~np.isnan(lon))
    pairs = pd.DataFrame({'stop': stop_row[located], 'hh': household_code[located], 'lon': lon[located],
                          'lat': lat[located]})
    median = pairs.drop_duplicates(['stop', 'hh']).groupby('stop')[['lon', 'lat']].median()
    s_lon, s_lat = np.full(len(unique), np.nan), np.full(len(unique), np.nan)
    at = median.index.to_numpy() - 1                              # stop ids are 1..n
    s_lon[at], s_lat[at] = median['lon'].to_numpy(), median['lat'].to_numpy()
    return SimpleNamespace(
        row=stop_row, count=len(unique), street=stop_street, house=houses, house_int=[N.house_int(h) for h in houses],
        zip=src.vocab['zip'].array()[src.codes['zip'][member]] if len(member) else np.array([], dtype=object),
        lon=s_lon, lat=s_lat)


def area_points(src, people, stops):
    """Tier g centres (district UTM metres) per stop and their basis: the mean of the geocoded registration households
    of the census block most common among the stop's people, else of its precinct, else of its ZIP, else of all of
    them (ties between equally common areas: the first code). Returns (xy array stops x 2, [basis per stop])."""
    located = ~np.isnan(people.lon) & (people.household_code >= 0)
    x, y = G.to_metres(people.lon[located], people.lat[located]) if located.any() else ([], [])
    levels = {'block': (people.block_code, people.block), 'precinct': (people.precinct_code, people.precinct),
              'zip': (src.codes['zip'], src.vocab['zip'])}
    homes = pd.DataFrame({'hh': people.household_code[located], 'x': x, 'y': y,
                          **{k: codes[located] for k, (codes, _) in levels.items()}}).drop_duplicates('hh')
    member = stops.row > 0
    frame = pd.DataFrame({'stop': stops.row[member] - 1, **{k: codes[member] for k, (codes, _) in levels.items()}})
    xy, basis, todo = np.full((stops.count, 2), np.nan), [None] * stops.count, np.ones(stops.count, dtype=bool)
    for level, (_, vocab) in levels.items():
        blank = vocab.index.get('', -1)
        centre = homes[homes[level] != blank].groupby(level)[['x', 'y']].mean()
        votes = frame[frame[level] != blank].groupby(['stop', level]).size().reset_index(name='n')
        top = votes.sort_values(['stop', 'n', level], ascending=[True, False, True]).drop_duplicates('stop')
        top = top[todo[top.stop.to_numpy()] & top[level].isin(centre.index).to_numpy()]
        at = top.stop.to_numpy()
        xy[at] = centre.loc[top[level].to_numpy(), ['x', 'y']].to_numpy()
        for i in at.tolist():
            basis[i] = level
        todo[at] = False
    if todo.any() and len(homes):
        xy[todo] = homes[['x', 'y']].mean().to_numpy()
        for i in np.flatnonzero(todo).tolist():
            basis[i] = 'all'
    return xy, basis


FAR_BINS = [150, 250, 500, 1000, 2000, 1e12]
FAR_LABELS = ['150-250', '250-500', '500-1000', '1000-2000', '2000+']


def place_stops(out, roads_path, stops, street_rows, areas=None, log=None):
    """Network into `out`: TIGER, then virtual streets for the voter streets it lacks and connectors that make it one
    component; then place every stop (tiers a-c and e by geocode, d by house number, g at its area centre from
    `areas`, see area_points) and settle sides. Returns (placements, network stats with the TIGER-only ones under
    'tiger', agreement, {area basis: tier g stops})."""
    with closing(sqlite3.connect(_uri(roads_path), uri=True)) as roads:
        tiger = G.build_network(out, roads.execute('SELECT id, name, kind, geometry FROM roads'))
    if log:
        log('roads        %d nodes, %d edges, %d spans, %d components' % (tiger['nodes'], tiger['edges'], tiger['spans'],
                                                                          tiger['components']))
    here = ~np.isnan(stops.lon)
    x, y = np.full(stops.count, np.nan), np.full(stops.count, np.nan)
    if here.any():
        x[here], y[here] = G.to_metres(stops.lon[here], stops.lat[here])
    key_of = {i: (key, city) for i, key, city, _, _ in street_rows}
    rows = []
    for i in range(stops.count):
        key, city = key_of[int(stops.street[i])]
        number = stops.house_int[i]
        rows.append({'id': i + 1, 'key': key, 'city': city, 'zip': stops.zip[i], 'house_int': number,
                     'parity': None if number is None else number % 2,
                     'x': float(x[i]) if here[i] else None, 'y': float(y[i]) if here[i] else None})
    network = G.augment_network(out, G.virtual_streets(G.load_network(out), rows))
    if log:
        log('virtual      %d streets in %d pieces, %d street connectors, %d component connectors' % (
            network['virtual_streets'], network['virtual_pieces'], network['street_connectors'],
            network['component_connectors']))
    net = G.load_network(out)
    placements = G.interpolate(net, rows, G.snap_stops(net, rows))
    targets, basis = {}, Counter()
    for i, p in enumerate(placements):
        if p.span_id is None and areas is not None and np.isfinite(areas[0][i]).all():
            targets[i] = tuple(areas[0][i].tolist())
            basis[areas[1][i]] += 1
    placements = G.place_near(net, rows, placements, targets)
    even_left, agreement = G.assign_sides(net, placements)
    out.executemany('UPDATE road_span SET even_left=? WHERE id=?', [(int(v), int(k)) for k, v in even_left.items()])
    return placements, {**network, 'tiger': tiger}, agreement, dict(basis)


def stop_rows(stops, placements):
    for i, p in enumerate(placements):
        snapped = p.placed_by != 'interpolated'
        lon, lat = (stops.lon[i], stops.lat[i]) if snapped else (p.lon, p.lat)
        number = stops.house_int[i]
        yield (i + 1, int(stops.street[i]), stops.house[i], number, None if number is None else number % 2, p.span_id,
               p.pos_m, p.side_lr, p.placed_by, None if lon is None else round(float(lon), 6),
               None if lat is None else round(float(lat), 6))


# ---- people and combos ------------------------------------------------------------------------------------------------
def person_rows(ctx):
    """Person rows in id order (the primary key is random: sorted inserts keep the WITHOUT ROWID tree compact)."""
    src, people, stops, tally = ctx.src, ctx.people, ctx.stops, ctx.tally
    order = sorted(range(src.rows), key=people.ids.__getitem__)
    ids = np.array(people.ids, dtype=object)
    arrays = {name: src.vocab[name].array() for name in src.vocab}
    party, precinct = ctx.party.array(), ctx.precinct.array()
    hh = ctx.household_values if len(ctx.household_values) else np.array([None], dtype=object)
    for lo in range(0, len(order), BATCH_ROWS):
        idx = np.array(order[lo:lo + BATCH_ROWS], dtype=np.int64)
        stop = stops.row[idx]
        street = np.where(stop > 0, ctx.street_row[idx], 0)
        handle = ctx.household_code[idx]
        age = people.age[idx]
        columns = [
            ids[idx].tolist(),
            np.where(handle >= 0, hh[handle], None).tolist(),
            np.where(stop > 0, stop, None).tolist(),
            np.where(stop > 0, street, None).tolist(),
            arrays['unit'][src.codes['unit'][idx]].tolist(),
            arrays['ss'][src.codes['ss'][idx]].tolist(),
            arrays['sh'][src.codes['sh'][idx]].tolist(),
            precinct[ctx.precinct_code[idx]].tolist(),
            arrays['city'][src.codes['city'][idx]].tolist(),
            arrays['zip'][src.codes['zip'][idx]].tolist(),
            party[ctx.party_code[idx]].tolist(),
            np.where(age >= 0, age, None).tolist(),
            arrays['gender'][src.codes['gender'][idx]].tolist(),
            [ctx.reg_iso[i] for i in idx.tolist()],
            *[tally[name][idx].tolist() for name in ('pri_voted', 'pri_elig', 'gen_voted', 'gen_elig', 'abs_voted',
                                                     'abs_elig')],
            [src.voter_blob[i * 32:(i + 1) * 32].hex() for i in idx.tolist()],
        ]
        yield from zip(*columns)


def combo_rows(ctx):
    src, stop = ctx.src, ctx.stops.row
    rows = np.flatnonzero(stop > 0)
    frame = pd.DataFrame({'ss': src.codes['ss'][rows], 'sh': src.codes['sh'][rows], 'pr': ctx.precinct_code[rows],
                          'st': ctx.street_row[rows], 'stop': stop[rows], 'unit': src.codes['unit'][rows]})
    area = ['ss', 'sh', 'pr', 'st']
    table = pd.concat([frame.groupby(area).size().rename('people'),
                       frame.drop_duplicates(area + ['stop', 'unit']).groupby(area).size().rename('doors')],
                      axis=1).reset_index()
    if not len(table):
        return []
    table['ss'] = src.vocab['ss'].array()[table['ss'].to_numpy()]
    table['sh'] = src.vocab['sh'].array()[table['sh'].to_numpy()]
    table['pr'] = ctx.precinct.array()[table['pr'].to_numpy()]
    table = table.sort_values(['ss', 'sh', 'pr', 'st'])
    return [(a, b, c, int(d), int(e), int(f)) for a, b, c, d, e, f in
            zip(table.ss, table.sh, table.pr, table.st, table.people, table.doors)]


# ---- gates -------------------------------------------------------------------------------------------------------------
def judge(raw, thresholds):
    """{gate: {value, min | max, pass}} from the unrounded numbers (CEILINGS hold at or below their limit, the rest at
    or above); a gate without a value fails. No abs_share_* gates where thresholds['abs_share'] is None."""
    gates = {name: raw[name] for name in ('unplaced_stops', 'stops_without_span', 'largest_component', 'side_agreement')}
    if thresholds['abs_share'] is not None:
        gates.update({'abs_share_' + year: share for year, share in raw['abs_share'].items()})
    out = {}
    for name, value in gates.items():
        limit = thresholds['abs_share' if name.startswith('abs_share_') else name]
        if name in CEILINGS:
            out[name] = {'value': value, 'max': limit, 'pass': value is not None and value <= limit}
        else:
            out[name] = {'value': None if value is None else round(float(value), 4), 'min': limit,
                         'pass': value is not None and value >= limit}
    return out


def backup_existing(database):
    """Copy the file about to be replaced into walk-backups/ without ever overwriting an earlier backup."""
    folder = database.parent / 'walk-backups'
    folder.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = folder / ('walk-' + stamp + '-' + secrets.token_hex(4) + '.sqlite3')
    with backup.open('xb'):
        pass
    try:
        with closing(sqlite3.connect(_uri(database), uri=True)) as source, closing(sqlite3.connect(backup)) as copy:
            source.backup(copy)
    except Exception:
        backup.unlink(missing_ok=True)
        raise
    return backup


# ---- build -------------------------------------------------------------------------------------------------------------
def default_data_dir(d):
    """co-05: CU_DATA_DIR or .local, as always; any other district: its own folder under .local."""
    return Path((d['id'] == D.DEFAULT and os.environ.get('CU_DATA_DIR')) or D.data_dir(d))


def build(source=None, data_dir=None, out=None, *, force=False, gates=None, log=None, district=None):
    """Build the store; see the module docstring. `source` defaults to the district's CSV (`district`: an id or a config
    dict, default co-05). `gates` overrides GATES entries (tests only); a district without abs_gate has no abs_share
    gates whatever `gates` says. Returns a dict: passed, failed (gate names), replaced, forced, qa, out, backup (path of
    the copy of the replaced file), bytes."""
    d = D.get(district)
    G.EPSG = d['utm_epsg']
    if list(d['primaries']) != list(N.PRIMARIES):
        raise BuildError('The district primaries must be keyed %s (the vote columns are the same in every state)'
                         % ', '.join(N.PRIMARIES))
    source = Path(source or d['source_csv'])
    data_dir = Path(data_dir or default_data_dir(d))
    out = Path(out) if out else data_dir / 'walk.sqlite3'
    thresholds = {**GATES, **(gates or {})}
    if d['abs_gate'] is None:
        thresholds['abs_share'] = None
    out.parent.mkdir(parents=True, exist_ok=True)
    lock = out.with_suffix('.build.lock')
    try:
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except FileExistsError:
        raise BuildError('%s exists: another walk build is running, or remove it if none is' % lock.name) from None
    temporary = None
    clock = Clock(log)
    try:
        for name in ('contacts', *(('scores',) if d['person_fields'] == 'scores' else ()), 'street', 'roads'):
            if not (data_dir / (name + '.sqlite3')).is_file():
                raise BuildError('%s.sqlite3 is missing from the data directory' % name)
        if not source.is_file():
            raise BuildError('The source CSV does not exist')
        original = source.stat()
        with clock.phase('checksums'):
            checksum, roads_sha = sha256_of(source), sha256_of(data_dir / 'roads.sqlite3')
        with clock.phase('stores'):
            people = load_people(data_dir, checksum, source, d)
        with clock.phase('read_csv'):
            src = read_source(source, people.ids, read_or_create_salt(data_dir / '.walk-salt'), d)
        latest = source.stat()
        if (original.st_size, original.st_mtime_ns) != (latest.st_size, latest.st_mtime_ns):
            raise BuildError('The source changed during the build')

        with clock.phase('streets'):
            tally = history(src.voted, src.mail, src.reg, src.dob, np.array(D.election_keys(d), dtype=np.int64))
            street_rows, street_row = make_streets(src)
            stops = make_stops(src, street_row, people.household_code, people.lon, people.lat)
            areas = area_points(src, people, stops)
        fd, filename = tempfile.mkstemp(prefix='.walk-build-', suffix='.sqlite3', dir=out.parent)
        os.close(fd)
        temporary = Path(filename)
        with closing(sqlite3.connect(temporary)) as db:
            db.execute('PRAGMA journal_mode=MEMORY')
            db.execute('PRAGMA synchronous=OFF')
            with clock.phase('network'):
                for sql in DDL:
                    db.execute(sql)
                placements, network, agreement, basis = place_stops(db, data_dir / 'roads.sqlite3', stops, street_rows,
                                                                    areas, log)
            with clock.phase('write'):
                db.executemany('INSERT INTO street VALUES (?,?,?,?,?)', street_rows)
                db.executemany('INSERT INTO stop VALUES (?,?,?,?,?,?,?,?,?,?,?)', stop_rows(stops, placements))
                ctx = SimpleNamespace(
                    src=src, people=people, stops=stops, tally=tally, street_row=street_row, party=people.party,
                    precinct=people.precinct, party_code=people.party_code, precinct_code=people.precinct_code,
                    household_code=people.household_code, household_values=people.household_values,
                    reg_iso=iso_dates(src.reg), district=d)
                db.executemany('INSERT INTO person VALUES (%s)' % ','.join('?' * 21), person_rows(ctx))
                db.executemany('INSERT INTO combo VALUES (?,?,?,?,?,?)', combo_rows(ctx))
                for sql in INDEXES:
                    db.execute(sql)
                db.commit()

            qa, raw = quality(src, people, stops, placements, street_rows, network, agreement, basis, ctx)
            qa['gates'] = judge(raw, thresholds)
            failed = sorted(name for name, gate in qa['gates'].items() if not gate['pass'])
            passed = not failed
            qa['forced'] = bool(force and not passed)
            qa['timing'] = {**clock.marks, 'total': clock.total()}
            qa['peak_memory_mb'] = peak_memory_mb()
            meta = [('schema', SCHEMA), ('source_sha256', checksum), ('roads_sha256', roads_sha),
                    ('built_utc', datetime.now(timezone.utc).isoformat()), ('n_people', str(src.rows)),
                    ('n_stops', str(stops.count)), ('abs_rule', d['abs_rule']), ('window', json.dumps(D.window(d))),
                    ('qa', json.dumps(qa, sort_keys=True))]
            if d['person_fields'] == 'csv':
                meta.append(('person_fields', 'csv'))
            db.executemany('INSERT INTO meta VALUES (?,?)', meta)
            db.commit()
            with clock.phase('integrity'):
                if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise BuildError('Walk database integrity check failed')

        result = {'passed': passed, 'replaced': False, 'forced': qa['forced'], 'qa': qa, 'out': out, 'backup': None,
                  'failed': failed, 'bytes': temporary.stat().st_size}
        if passed or force:
            if out.exists():
                result['backup'] = backup_existing(out)
            for attempt in range(REPLACE_TRIES):
                try:
                    os.replace(temporary, out)
                    break
                except PermissionError:
                    if attempt + 1 == REPLACE_TRIES:
                        raise BuildError('%s is in use (is the app holding it open?); the old file was kept'
                                         % out.name) from None
                    time.sleep(REPLACE_WAIT_S)
            temporary = None
            result['replaced'] = True
        return result
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def quality(src, people, stops, placements, street_rows, network, agreement, basis, ctx):
    """The qa document (aggregates only) and the unrounded numbers the gates are judged on."""
    by = Counter(p.placed_by for p in placements)
    tiers = Counter(p.tier for p in placements)
    located = int((~np.isnan(stops.lon)).sum())
    snapped = tiers['a'] + tiers['b'] + tiers['c']
    lacks_span = np.array([p.span_id is None for p in placements], dtype=bool)
    no_span = int(lacks_span.sum())
    unplaced = sum(1 for i, p in enumerate(placements)              # no map position: no geocode and no tier d/g spot
                   if p.lon is None and np.isnan(stops.lon[i]))
    far = dict(zip(FAR_LABELS, np.histogram([p.dist_m for p in placements if p.tier == 'e'], bins=FAR_BINS)[0].tolist()))
    per_stop = np.bincount(stops.row[stops.row > 0], minlength=stops.count + 1)[1:]
    g_abs, shares = {}, {}
    for year in N.ABS_GATE['elections']:
        j = ELECTIONS.index(year)
        voted, mail = int(src.recorded[0][j]), int(src.recorded[1][j])
        shares[year] = mail / voted if voted else None
        g_abs[year] = {'recorded': voted, 'mail': mail, 'share': None if shares[year] is None else round(shares[year], 4)}
    in_stop = ctx.stops.row > 0
    doors = len(set(zip(ctx.stops.row[in_stop].tolist(), src.codes['unit'][in_stop].tolist())))
    placement = snapped / located if located else None
    whole = network['components'] == 1                            # a rounded share of 1.0 is not enough
    raw = {'unplaced_stops': unplaced, 'stops_without_span': no_span,
           'largest_component': 1.0 if whole else min(network['largest_component_share'], 0.9999),
           'side_agreement': agreement, 'abs_share': shares}
    qa = {
        'rows': src.rows, 'people': src.rows, 'streets': len(street_rows), 'stops': stops.count, 'doors': doors,
        'households': int(len(ctx.household_values)),
        'people_without_stop': int((~in_stop).sum()), 'people_without_location': int(np.isnan(people.lon).sum()),
        'people_in_stops_without_span': int(per_stop[lacks_span].sum()),
        'stop_counts': {'by_placed_by': {t: by.get(t, 0) for t in PLACED_BY},
                        'by_tier': {t: tiers.get(t, 0) for t in TIERS}, 'located': located, 'snapped': snapped,
                        'snapped_share': None if placement is None else round(placement, 4),
                        'unplaced': unplaced, 'without_span': no_span, 'tier_e_road_m': far,
                        'tier_g_area': {a: basis.get(a, 0) for a in AREAS},
                        'without_house_number': sum(h is None for h in stops.house_int)},
        'network': network, 'side_agreement': agreement, 'g_abs': g_abs,
        'dates': {d.name: d.report() for d in src.dates.values()},
        'cities': {'in_file': len(src.vocab['city'].raw), 'canonical': len(src.vocab['city'].values)},
        'voter_ids_blank': src.blank_voter_ids, 'voter_keys_duplicated': src.duplicate_voter_keys,
        'window': D.window(ctx.district),
    }
    return qa, raw


def safe_trace(error):
    """Exception type and call sites only: messages of library errors can echo the data being parsed."""
    frames = ['%s:%d in %s' % (Path(f.filename).name, f.lineno, f.name)
              for f in traceback.extract_tb(error.__traceback__)]
    return '%s at %s' % (type(error).__name__, ' <- '.join(reversed(frames[-4:])))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--district', default=D.DEFAULT, choices=list(D.DISTRICTS), help='default co-05')
    parser.add_argument('--source', type=Path, default=None, help="default: the district's source CSV")
    parser.add_argument('--data-dir', type=Path, default=None,
                        help="default: .local for co-05 (CU_DATA_DIR if set), else the district's folder under .local")
    parser.add_argument('--out', type=Path, default=None, help='default: <data-dir>/walk.sqlite3')
    parser.add_argument('--force', action='store_true', help='replace the store although a quality gate failed')
    args = parser.parse_args(argv)
    d = D.get(args.district)
    source = args.source
    if source is None:       # the district's file must be the one its config names; an explicit --source is trusted
        source = Path(d['source_csv'])
        if source.is_file() and sha256_of(source) != d['sha256']:
            print('error: %s has a different sha256 than the %s source in tools/districts.py' % (source.name, d['id']),
                  file=sys.stderr)
            return 1
    try:
        result = build(source, args.data_dir, args.out, force=args.force, district=d,
                       log=lambda text: print(text, flush=True))
    except BuildError as error:
        print('error: %s' % error, file=sys.stderr)
        return 1
    except Exception as error:      # noqa: BLE001
        if os.environ.get('WALK_BUILD_TRACEBACK'):
            raise
        print('error: ' + safe_trace(error), file=sys.stderr)
        return 1
    print(json.dumps(result['qa'], indent=1, sort_keys=True), flush=True)
    if not result['replaced']:
        print('walk store NOT replaced; failed gates: ' + ', '.join(result['failed']), flush=True)
        return 2
    note = ' (forced past: ' + ', '.join(result['failed']) + ')' if result['forced'] else ''
    note += '; previous file backed up' if result['backup'] else ''
    print('walk store written: %.1f MB%s' % (result['bytes'] / 1e6, note), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
