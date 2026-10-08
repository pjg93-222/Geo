"""Walk list API (shapes: frontend/src/v3/api/walkTypes.ts, schema walk-list-1).

  GET  /api/v3/walk/options      districts and precincts to pick from, with people and doors
  GET  /api/v3/walk/streets      street search by typed text (at most 20) or the busiest streets of an area (at most 100)
  POST /api/v3/walk/match        an uploaded street list (at most 500 rows) matched against the streets
  GET  /api/v3/walk/list         a walk list built now: runs in walking order, doors, voters (at most 40 streets, 200 planned
                                 streets from the street plan as spans=12,15, 1,500 voters)
  GET  /api/v3/walk/overview     the blocks of an area with door and party counts, for map shading
  GET  /api/v3/walk/plan         street plan (candidate districts): the blocks of an area ranked by a preset's value per 100 m
  POST /api/v3/walk/scores       overall, participation and support standings of up to 1,500 listed people, for the printed list
  GET  /api/v3/walk/lists        the 50 newest saved lists
  POST /api/v3/walk/lists        freeze a list {title, query, fingerprint}, answered with `changed` when the list built to save
                                 differs from the one shown; or make a re-knock list {title, from, reknock: true}, answered
                                 with `missing`, the voters marked Not home that the voter data no longer has
  GET  /api/v3/walk/lists/<key>  a saved list as it was frozen, with each voter's latest result {code, on, list} refreshed
  POST /api/v3/walk/dispositions results of visits {list, on, entries}, appended; the newest entry of a person wins

Every endpoint is local only (loopback owner, no proxy headers, not hosted), answers 403 in demo mode (lists carry real
names and phones), is `no-store`, takes an exact list of query parameters and, for POST, a JSON object of at most 256 KB.
Django enforces CSRF on every POST. The voters of a saved list are fixed when it is saved: a result can only be recorded
for a person on its roster, and the roster's voter keys travel privately inside the stored payload.
"""
import hashlib
import json
import logging
import re
import secrets
import sqlite3
from bisect import bisect_left, bisect_right
from datetime import date, timedelta
from functools import lru_cache, wraps
from pathlib import Path

from django.conf import settings
from django.core.exceptions import SuspiciousOperation
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models.fields.json import KT
from django.utils import timezone

from tools import districts as D
from tools import field_labels as FL

from . import walk_context as C
from . import walk_names as N
from . import walk_route
from . import walk_store as W
from .models import Disposition, WalkList
from .street_views import local_denied, local_only, local_only_post, response
from .terrier_contacts import SourceStoreError
from .v3_http import BadRequest, clean, params

log = logging.getLogger(__name__)

MAX_STREETS = 40            # streets in one list
MAX_PICK_SPANS = 200        # planned streets (road spans) picked for one list
MAX_VOTERS = 1500           # voters in one list
MAX_ROWS = 500              # rows of an uploaded street list
MAX_ENTRIES = 200           # results in one POST
MAX_BODY = 256 * 1024       # bytes of a POST body
MAX_SPANS = 8000            # blocks in one overview
MAX_TEXT = 60               # characters of street search text
FOUND_WITH_TEXT, FOUND_WITHOUT_TEXT = 20, 100
MAX_SEGMENTS = 1200         # distinct (street, parity, precinct, block) in one list; plan_route's cost table grows with runs squared
MAX_GROUP_RUNS = 400        # runs in one group of nearby streets (walk_route): its deadhead table grows with their square
MAX_PLACES = 8              # candidates offered for one uploaded row
MATCH_MIN = 0.92            # similarity from which a lone reading is taken to be the street meant (an exact name always is)
PICK_MIN = 0.80             # readings this close are offered as a choice when two or more are about as good
NEAR_EQUAL = 0.05           # a street within this of the best reading is as likely as it
MAX_SUGGESTIONS = 3
LISTS_SHOWN = 50
CODES = ('S', 'U', 'O', 'NH', 'MV')
# an area id as the store holds it: at most 24 characters, but a CT precinct key is the code and the name (build_walk caps it at 64)
AREA = r'[0-9A-Za-z._-]{1,%d}' % (64 if settings.DISTRICT['precinct_key'] == 'code-name' else 24)
QUERY_KEYS = {'streets', 'spans', 'ss', 'sh', 'precinct'}
NO_CANDIDATE = 'No candidate is set for this district.'
SUPPORT_NOT_BUILT = 'Support is not built yet.'
SCORES_NOT_BUILT = 'Scores are not built yet.'
WEIGHT = r'\d{1,6}(\.\d{1,6})?'
KEY = re.compile(r'[0-9a-f]{10}')
PRIVATE = '_voter_keys'     # {person id: voter key}, kept in the stored payload and never sent out
NO_NUMBER = '(no number)'   # the address of a voter whose registration has no house number


class Refused(Exception):
    """A request the walk list will not serve, said plainly to the walker: HTTP status and message."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def guarded(function):
    """Demo mode answers 403 (the lists carry real names); refusals and store problems become JSON errors."""
    @wraps(function)
    def wrapped(request, *args, **kwargs):
        if settings.DEMO_MODE or request.GET.get('demo') == '1':
            return response({'error': 'Walk lists are unavailable in demo mode.'}, 403)
        try:
            return function(request, *args, **kwargs)
        except Refused as problem:
            return response({'error': str(problem)}, problem.status)
        except BadRequest:
            return response({'error': 'Invalid request.'}, 400)
        except SourceStoreError as problem:
            return response({'error': str(problem)}, problem.status)
        except DatabaseError:
            return response({'error': 'The saved lists are unavailable. If the walk list was just added, run manage.py migrate.'}, 503)
        except (sqlite3.Error, OSError):
            return response({'error': 'The local walk list data is unavailable.'}, 503)
        except SuspiciousOperation:
            return response({'error': 'Invalid request.'}, 400)
        except Exception:      # a fault of ours: say so in JSON and leave the traceback in the log, never "bad input"
            log.exception('walk list: %s failed', function.__name__)
            return response({'error': 'The walk list could not be built. The server log has the details.'}, 500)
    return wrapped


def read(function):
    return local_only(guarded(function))


def write(function):
    return local_only_post(guarded(function))


# ---- request parsing -----------------------------------------------------------------------------------------------

def json_body(request, allowed):
    """The POST body as a dict whose keys are all in `allowed`."""
    if request.content_type != 'application/json':
        raise Refused('Send the request as application/json.')
    try:
        size = int(request.META.get('CONTENT_LENGTH') or 0)
    except ValueError:
        size = 0
    raw = request.body if size <= MAX_BODY else b''
    if size > MAX_BODY or len(raw) > MAX_BODY:
        raise Refused('That request is larger than 256 KB.')
    try:
        value = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise Refused('The request is not valid JSON.') from None
    if not isinstance(value, dict) or not set(value) <= set(allowed):
        raise BadRequest('Expected a JSON object with known fields')
    return value


def area_of(values):
    """{'ss': ..., 'sh': ..., 'precinct': ...} for the area codes present in `values`."""
    return {column: clean(values[column], AREA) for column in W.AREA_COLUMNS if column in values}


def parse_picks(text):
    """[(street id, 'even'|'odd'|'both')] from "12:even,15:both"; a street named twice takes both sides."""
    sides = {}
    for part in clean(text, r'[0-9a-z:,]{1,2000}').split(','):
        found = re.fullmatch(r'(\d{1,9}):(even|odd|both)', part)
        if not found:
            raise BadRequest('Invalid street pick')
        street, side = int(found[1]), found[2]
        sides[street] = side if sides.get(street, side) == side else 'both'
    if len(sides) > MAX_STREETS:
        raise Refused('A walk list holds up to %d streets.' % MAX_STREETS)
    return list(sides.items())


def parse_spans(text):
    """The road-span ids of "12,15" (planned streets from the street plan): sorted, each once."""
    parts = clean(text, r'[0-9,]{1,2000}').split(',')
    if not all(re.fullmatch(r'\d{1,9}', part) for part in parts):
        raise BadRequest('Invalid span pick')
    spans = sorted({int(part) for part in parts})
    if len(spans) > MAX_PICK_SPANS:
        raise Refused('A walk list holds up to %d planned streets.' % MAX_PICK_SPANS)
    return spans


def list_query(values):
    """(picks, spans, area, normalised query) of a list request: the GET parameters, or the query of a list to save."""
    area = area_of(values)
    picks = parse_picks(values['streets']) if 'streets' in values else []
    spans = parse_spans(values['spans']) if 'spans' in values else []
    if not picks and not spans and not area:
        raise Refused('Pick an area or add a street first.')
    query = {'streets': ','.join('%d:%s' % pick for pick in picks)} if picks else {}
    if spans:
        query['spans'] = ','.join(map(str, spans))
    return picks, spans, area, {**query, **area}


CONTROL = re.compile(r'[\x00-\x1f\x7f-\x9f]')


def clean_title(value):
    if not isinstance(value, str) or CONTROL.search(value):
        raise BadRequest('Invalid title')
    value = value.strip()
    if not 1 <= len(value) <= 120:
        raise Refused('Give the list a title of 1 to 120 characters.')
    return value


def local_iso(moment):
    return moment.astimezone().isoformat(timespec='seconds')


def find_list(key):
    if not isinstance(key, str) or not KEY.fullmatch(key):
        raise Refused('That saved list was not found.', 404)
    saved = WalkList.objects.filter(key=key).first()
    if saved is None:
        raise Refused('That saved list was not found.', 404)
    return saved


# ---- options and streets -------------------------------------------------------------------------------------------

def short(code):
    return str(int(code)) if code.isdigit() else code


@read
def options(request):
    q = params(request, {'level', 'ss', 'sh'}, {'level'})
    level = clean(q['level'], r'ss|sh|precinct')
    area = area_of(q)
    with W.walk_store() as store:
        rows = store.options(level, area)
    name = {'ss': lambda code: 'SD ' + short(code), 'sh': lambda code: 'HD ' + short(code), 'precinct': str}[level]
    return response({'level': level, 'options': [
        {'id': code, 'label': name(code), 'people': people, 'doors': doors} for code, people, doors in rows]})


def street_json(street, count):
    return {'id': street.id, 'label': street.display, 'city': N.display_city(street.city), 'zip': street.zip,
            'doors': count['doors'], 'people': count['people'], 'even': count['even'], 'odd': count['odd']}


@read
def streets(request):
    q = params(request, {'q', 'ss', 'sh', 'precinct'})
    text = q.get('q', '').strip()
    if len(text) > MAX_TEXT:
        raise Refused('Street search takes up to %d characters.' % MAX_TEXT)
    area = area_of(q)
    with W.walk_store() as store:
        directory = store.directory()
        doors = store.street_doors(area)
        allowed = doors if area else None
        if N.name_key(text):
            found = directory.search(text, FOUND_WITH_TEXT, allowed, doors)
        else:
            busiest = sorted((i for i in doors if i in directory.by_id), key=lambda i: (
                -doors[i], directory.by_id[i].display, directory.by_id[i].city, i))
            found = [directory.by_id[i] for i in busiest[:FOUND_WITHOUT_TEXT]]
        counts = store.street_counts([street.id for street in found], area)
    return response({'streets': [street_json(street, counts[street.id]) for street in found]})


# ---- uploaded street lists -----------------------------------------------------------------------------------------

def city_key(text):
    """The spelling the store uses for a city (tools/districts.py canonical_city): in Colorado 'Colo Spgs.' is 'COLORADO SPRINGS'."""
    return D.canonical_city(text, settings.DISTRICT)


def match_row(row):
    """(street text, side, city, zip) of one uploaded row; every field a short string."""
    if not isinstance(row, dict) or not set(row) <= {'street', 'side', 'city', 'zip'}:
        raise BadRequest('Invalid row')
    limits = {'street': 120, 'side': 40, 'city': 60, 'zip': 20}
    values = {}
    for field, limit in limits.items():
        value = row.get(field, '')
        if not isinstance(value, str) or len(value) > limit:
            raise BadRequest('Invalid row field')
        values[field] = value.strip()
    return values['street'], N.parse_side(values['side']), values['city'], values['zip']


def narrow(candidates, city, zip_code):
    """Candidates the city and zip columns point at; when they name none of them the columns are no reason to drop one."""
    if len(candidates) > 1 and city:
        same = [s for s in candidates if city_key(s.city) == city_key(city)]
        candidates = same or candidates
    zip5 = re.sub(r'\D', '', zip_code)[:5]
    if len(candidates) > 1 and zip5:
        same = [s for s in candidates if s.zip[:5] == zip5]
        candidates = same or candidates
    return candidates


def decide(hits, city, zip_code, doors):
    """(candidate streets, suggested labels) for the ranked hits of one row. An exact name is the street (or, in several
    cities, a choice the city and zip columns may settle). Otherwise the readings within NEAR_EQUAL of the best one, if it
    is at least PICK_MIN, are a choice when there are two or more, and the street meant only when it is alone and at
    least MATCH_MIN. Anything weaker is no match and comes with up to three labels that come close."""
    if not hits:
        return [], []
    best = hits[0][0]
    near = [street for score, street in hits if score >= best - NEAR_EQUAL] if best >= PICK_MIN else []
    near = narrow(near, city, zip_code)
    if near and (best >= 1.0 or len(near) > 1 or best >= MATCH_MIN):
        return sorted(near, key=lambda s: (-doors.get(s.id, 0), s.display, s.city, s.id))[:MAX_PLACES], []
    labels = []
    for _, street in hits:
        if street.display not in labels:
            labels.append(street.display)
    return [], labels[:MAX_SUGGESTIONS]


@write
def match(request):
    params(request, set())
    body = json_body(request, {'rows', 'ss', 'sh', 'precinct'})
    rows = body.get('rows')
    if not isinstance(rows, list):
        raise BadRequest('rows must be a list')
    if len(rows) > MAX_ROWS:
        raise Refused('A street list can have up to %d rows.' % MAX_ROWS)
    area = area_of(body)
    inputs = [match_row(row) for row in rows]
    with W.walk_store() as store:
        directory = store.directory()
        doors = store.street_doors(area)
        allowed = doors if area else None
        decided = [decide(directory.lookup(text, allowed), city, zip_code, doors) for text, _, city, zip_code in inputs]
        counts = store.street_counts([s.id for candidates, _ in decided for s in candidates], area)
    out = []
    for number, ((text, side, _, _), (candidates, suggestions)) in enumerate(zip(inputs, decided), 1):
        out.append({'row': number, 'input': text, 'side': side,
                    'status': 'none' if not candidates else 'matched' if len(candidates) == 1 else 'pick',
                    'candidates': [street_json(s, counts[s.id]) for s in candidates], 'suggestions': suggestions})
    return response({'rows': out})


# ---- building a list -----------------------------------------------------------------------------------------------

def unit_key(unit):
    """Natural order: 'Apt 2' before 'Apt 10'; no unit first."""
    return [int(part) if i % 2 else part.lower() for i, part in enumerate(re.split(r'(\d+)', unit))]


def street_label(names, street_id):
    street = names.get(street_id)
    return street.display if street else ''


def route_stops(rows, names):
    """({route id: rows}, [stop records for plan_route]). Precinct is a property of the person, so a stop whose people
    are registered in several precincts becomes one route stop per precinct, each with its own id (1..n in stop order)."""
    groups = {}
    for row in rows:
        groups.setdefault((row['stop_id'], row['precinct'] or ''), []).append(row)
    members, stops = {}, []
    for number, ((_, precinct), group) in enumerate(sorted(groups.items()), 1):
        first = group[0]
        members[number] = group
        stops.append({'id': number, 'street_id': first['street_id'], 'street': street_label(names, first['street_id']),
                      'parity': first['parity'], 'precinct': precinct, 'span_id': first['span_id'],
                      'pos_m': first['pos_m'], 'house_int': first['house_int'], 'house': first['house'],
                      'side_lr': first['side_lr'], 'lon': first['lon'], 'lat': first['lat']})
    return members, stops


def house_label(house):
    return house or NO_NUMBER


def tally(voted, eligible):
    return {'voted': int(voted or 0), 'eligible': int(eligible or 0)}


def voter_json(row, contacts):
    name, phone = contacts.get(row['person_id'], ('', ''))
    return {'person_id': row['person_id'], 'name': name, 'phone': phone,
            'pri': tally(row['pri_voted'], row['pri_elig']), 'gen': tally(row['gen_voted'], row['gen_elig']),
            'abs': tally(row['abs_voted'], row['abs_elig']), 'party': row['party'] or '', 'age': row['age'],
            'gender': row['gender'] or '', 'reg_date': row['reg_date'] or '', 'last': None}


def place_doors(doors, path):
    """A point for every door of a run: its stop's own, else the nearest door before (then after) it, else the path."""
    for order in (doors, doors[::-1]):
        known = None
        for door in order:
            if door['lon'] is None:
                if known:
                    door['lon'], door['lat'] = known
            else:
                known = (door['lon'], door['lat'])
    for door in doors:
        if door['lon'] is None and path:
            door['lon'], door['lat'] = path[0]


def bounds(runs):
    xs, ys = [], []
    for run in runs:
        for lon, lat in (*run['path'], *run['approach']):
            xs.append(lon)
            ys.append(lat)
        for door in run['doors']:
            xs.append(door['lon'])
            ys.append(door['lat'])
    return [round(min(xs), 6), round(min(ys), 6), round(max(xs), 6), round(max(ys), 6)] if xs else [0.0, 0.0, 0.0, 0.0]


def plan_payload(store, rows, query):
    """(payload, {person id: voter key}) for the people in `rows`: their stops as runs in walking order, each stop's
    people as doors (stop, unit) and voters by name, numbered 1..n over the whole list. `last` is left empty."""
    contacts = store.contacts(row['person_id'] for row in rows)
    at_stop, stops = route_stops(rows, store.directory().by_id)
    if len({(s['street_id'], s['parity'], s['precinct'], s['span_id']) for s in stops}) > MAX_SEGMENTS:
        raise Refused('This selection splits into too many separate street segments. Narrow the area or streets.')
    try:
        plan = walk_route.plan_route(store.conn, stops, max_runs=MAX_GROUP_RUNS)
    except walk_route.RouteTooLarge:
        raise Refused('This selection splits into too many separate street segments. Narrow the area or streets.') from None
    runs, seq = [], 0
    for number, run in enumerate(plan.runs, 1):
        doors = []
        for stop_id in run.stop_ids:
            by_unit = {}
            for member in at_stop[stop_id]:
                by_unit.setdefault(member['unit'] or '', []).append(member)
            for unit in sorted(by_unit, key=unit_key):
                seq += 1
                first = by_unit[unit][0]
                voters = sorted((voter_json(m, contacts) for m in by_unit[unit]), key=lambda v: (v['name'].casefold(), v['person_id']))
                doors.append({'seq': seq, 'house': house_label(first['house']), 'unit': unit, 'placed_by': first['placed_by'] or 'interpolated',
                              'lon': first['lon'], 'lat': first['lat'], 'voters': voters})
        place_doors(doors, run.path)
        runs.append({'stop': number, 'street_id': run.street_id, 'street': run.street, 'side': run.side,
                     'precinct': run.precinct, 'from_house': house_label(run.from_house), 'to_house': house_label(run.to_house),
                     'walk_m': run.walk_m, 'lr': run.lr, 'path': run.path, 'approach': run.approach, 'doors': doors})
    anchor = next(((d['lon'], d['lat']) for r in runs for d in r['doors'] if d['lon'] is not None), (0.0, 0.0))
    for run in runs:
        for door in run['doors']:
            if door['lon'] is None:
                door['lon'], door['lat'] = anchor
    n_doors = sum(len(run['doors']) for run in runs)
    payload = {
        'schema': 'walk-list-1', 'generated_on': date.today().isoformat(),
        'list': {'key': None, 'title': '', 'kind': 'standard', 'created_at': None}, 'query': query,
        'window': json.loads(store.info.meta['window']) if 'window' in store.info.meta else dict(N.WINDOW),
        'totals': {'streets': len({run['street_id'] for run in runs}), 'stops': len(runs), 'doors': n_doors,
                   'voters': len(rows), 'walk_m': plan.total_m},
        'bbox': bounds(runs), 'runs': runs}
    keys = {row['person_id']: row['voter_key'] for row in rows}
    payload['fingerprint'] = fingerprint(payload, keys)
    return payload, keys


def fingerprint(payload, keys):
    """A digest of what a list shows (all of it but the day it was built and its saved details) and of the voter behind
    each person id: two builds with the same fingerprint are the same list. Taken before `last` is attached."""
    shown = {name: value for name, value in payload.items() if name not in ('generated_on', 'list', 'fingerprint')}
    return hashlib.sha256(json.dumps([shown, keys], sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def standard_payload(store, picks, spans, area, query):
    directory = store.directory()
    if any(street not in directory.by_id for street, _ in picks):
        raise Refused('One of those streets is not in the walk list data. Search for it again.')
    rows = store.voters(picks, area, MAX_VOTERS + 1, spans)
    if len(rows) > MAX_VOTERS:
        raise Refused('That is %s voters, and a walk list holds up to %s. Choose a smaller area or fewer streets.'
                      % ('{:,}'.format(store.count_voters(picks, area, spans)), '{:,}'.format(MAX_VOTERS)))
    if not rows:
        raise Refused('No voters live on those streets in this area.')
    return plan_payload(store, rows, query)


def latest_codes(voter_keys):
    """{voter key: {code, on, list}}: for each voter the newest result that stands, and the key of the list it was recorded
    on. A list's current result for a person is its newest entry (a null code cleared it); across lists the newest of those
    that are not cleared is the latest. Newest is the last one saved (the row id), not the clock's word: results are
    appended and never edited."""
    keys = sorted(set(voter_keys))
    latest = {}
    for start in range(0, len(keys), 500):
        state = {}
        entries = Disposition.objects.filter(voter_key__in=keys[start:start + 500]).order_by('id')
        for walk_list, list_key, key, code, day, ident in entries.values_list(
                'walk_list_id', 'walk_list__key', 'voter_key', 'code', 'contacted_on', 'id'):
            state[(walk_list, key)] = (ident, code, day, list_key)
        for (_, key), (order, code, day, list_key) in state.items():
            if code is not None and (key not in latest or order > latest[key][0]):
                latest[key] = (order, code, day, list_key)
    return {key: {'code': code, 'on': day.isoformat(), 'list': list_key} for key, (_, code, day, list_key) in latest.items()}


def attach_last(payload, keys):
    last = latest_codes(keys.values())
    for run in payload['runs']:
        for door in run['doors']:
            for voter in door['voters']:
                voter['last'] = last.get(keys.get(voter['person_id']))


@read
def walk_list(request):
    picks, spans, area, query = list_query(params(request, QUERY_KEYS))
    with W.walk_store() as store:
        payload, keys = standard_payload(store, picks, spans, area, query)
        C.attach(payload, store.conn)                  # map context: computed for each response, never stored
    attach_last(payload, keys)
    return response(payload)


# ---- the blocks of an area -----------------------------------------------------------------------------------------

@read
def overview(request):
    area = area_of(params(request, W.AREA_COLUMNS))
    if not area:
        raise Refused('Choose a district or precinct first.')
    with W.walk_store() as store:
        names = store.directory().by_id
        spans = {}
        for row in store.span_stats(area):
            span = spans.setdefault(row['span_id'], {'doors': 0, 'label': None})
            span['doors'] += row['doors'] or 0
            label = (-row['doors'], street_label(names, row['street_id']))
            if span['label'] is None or label < span['label']:
                span['label'] = label
        if len(spans) > MAX_SPANS:
            raise Refused('That area has %s blocks; choose a house district or a precinct to draw them.' % '{:,}'.format(len(spans)))
        lines = store.span_lines(spans)
        parties = store.span_parties(area)
    features = []
    for span_id in sorted(spans):
        coordinates = json.loads(lines.get(span_id) or '[]')
        if len(coordinates) < 2:
            continue
        span = spans[span_id]
        features.append({'type': 'Feature', 'geometry': {'type': 'LineString', 'coordinates': coordinates},
                         'properties': {'span': span_id, 'street': span['label'][1], 'doors': span['doors'],
                                        'parties': parties.get(span_id, {})}})
    return response({'type': 'FeatureCollection', 'features': features})


# ---- the street plan and the printed scores ------------------------------------------------------------------------

SUMS = ('doors', 'people', 'scored', 'push', 'mixed', 'turnout', 'identify', 's_sum', 's_n', 'p_sum', 'p_n')
MEASURE = {'value': 'value_per_100m', 'pop_density': 'pop_density', 'voter_density': 'voter_density'}   # weight -> plan column


@lru_cache(maxsize=2)
def _block_density(path, mtime_ns, size):
    block = json.loads(Path(path).read_text(encoding='utf-8'))['levels']['block']
    return {ident: value for ident, value in zip(block['ids'], block['pop_density']) if value is not None}


def block_density():
    """{census block id: people per km2} from density.json, or None when it is not built."""
    path = Path(settings.DATA_DIR) / 'density.json'
    try:
        stat = path.stat()
    except OSError:
        return None
    return _block_density(str(path), stat.st_mtime_ns, stat.st_size)


def percentile(values):
    """{key: midrank standing 0..1} among the keys whose value is not None; equal values share the middle of their ranks."""
    ordered = sorted(v for v in values.values() if v is not None)
    return {key: (bisect_left(ordered, v) + bisect_right(ordered, v)) / (2 * len(ordered))
            for key, v in values.items() if v is not None}


def fold_plan(store, rows, blocks):
    """{span id: sums over its streets, len_m, street label, commonest block} from Store.span_plan rows. The label is the
    street with most doors on the block, then the first by name (as the overview)."""
    names = store.directory().by_id
    spans, best = {}, {}
    for row in rows:
        span = spans.setdefault(row['span_id'], {**dict.fromkeys(SUMS, 0), 'len_m': row['len_m'], 'block': blocks.get(row['span_id'])})
        for key in SUMS:
            span[key] += row[key] or 0
        label = (-row['doors'], street_label(names, row['street_id']))
        if row['span_id'] not in best or label < best[row['span_id']]:
            best[row['span_id']] = label
    for span_id, span in spans.items():
        span['street'] = best[span_id][1]
    return spans


def rank_plan(spans, preset, weights, roi, pop_density):
    """(plan rows best first, {'spans', 'people'} not ranked) for the blocks in `spans` (fold_plan). A block with fewer than
    roi['min_doors'] doors is not ranked. Its value is the preset's sum over its people, per 100 m of its length (floored
    at roi['min_length_m']). The score blends the 0..1 standings of value per 100 m, population density (the commonest
    census block's, from `pop_density` {block id: per km2} or None) and voter density (people per 100 m) with `weights`
    (summing to 1); a block with no value on a weighted measure stands lowest on it. Ties go to the higher value per
    100 m, then the lower span id."""
    rows, skipped = [], {'spans': 0, 'people': 0}
    for span_id, s in spans.items():
        if s['doors'] < roi['min_doors']:
            skipped['spans'] += 1
            skipped['people'] += s['people']
            continue
        length = max(s['len_m'] or 0.0, roi['min_length_m'])
        rows.append({'span': span_id, 'street': s['street'], 'doors': s['doors'], 'people': s['people'], 'len_m': length,
                     's_mean': s['s_sum'] / s['s_n'] / 100.0 if s['s_n'] else None,
                     'p_mean': s['p_sum'] / s['p_n'] / 100.0 if s['p_n'] else None,
                     'pop_density': pop_density.get(s['block']) if pop_density else None,
                     'voter_density': s['people'] / (length / 100.0), 'value': s[preset],
                     'value_per_100m': s[preset] / (length / 100.0)})
    standing = {key: percentile({r['span']: r[column] for r in rows}) for key, column in MEASURE.items() if weights[key]}
    for r in rows:
        r['score'] = round(sum(weights[key] * standing[key].get(r['span'], 0.0) for key in standing), 9)
    rows.sort(key=lambda r: (-r['score'], -r['value_per_100m'], r['span']))
    for rank, r in enumerate(rows, 1):
        r['rank'] = rank
        for key, digits in (('len_m', 1), ('s_mean', 4), ('p_mean', 4), ('voter_density', 3), ('value', 4), ('value_per_100m', 4), ('score', 4)):
            r[key] = None if r[key] is None else round(r[key], digits)
    return rows, skipped


@read
def plan(request):
    roi = settings.DISTRICT['roi']
    if roi is None:
        raise Refused(NO_CANDIDATE, 404)
    q = params(request, {*W.AREA_COLUMNS, 'preset', 'w_value', 'w_pop', 'w_voter'})
    area = area_of(q)
    if not area:
        raise Refused('Choose a district or precinct first.')
    preset = clean(q.get('preset', roi['preset']), r'[a-z]{1,16}')
    if preset not in roi['presets']:
        raise Refused('That street plan is not offered.')
    given = {'value': q.get('w_value'), 'pop_density': q.get('w_pop'), 'voter_density': q.get('w_voter')}
    if all(text is None for text in given.values()):
        weights = dict(roi['weights'])
    else:                                          # the weights sent are the weights: one left out counts for nothing
        weights = {key: 0.0 if text is None else float(clean(text, WEIGHT)) for key, text in given.items()}
    total = sum(weights.values())
    if total <= 0:
        raise Refused('Give at least one weight above zero.')
    weights = {key: weight / total for key, weight in weights.items()}
    density = block_density()
    if weights['pop_density'] and density is None:
        raise Refused('Density is not built yet.', 503)
    with W.walk_store() as store:
        if not (store.attach_scores() and store.support):
            raise Refused(SUPPORT_NOT_BUILT, 503)
        rows, blocks = store.span_plan(area)
        spans = fold_plan(store, rows, blocks)
    if len(spans) > MAX_SPANS:
        raise Refused('That area has %s blocks; choose a house district or a precinct to rank them.' % '{:,}'.format(len(spans)))
    ranked, skipped = rank_plan(spans, preset, weights, roi, density)
    return response({'schema': 'walk-plan-1', 'preset': preset, 'weights': {key: round(w, 6) for key, w in weights.items()},
                     'p': roi['p'], 'race_mode': settings.DISTRICT['race_mode'], 'spans': ranked, 'not_ranked': skipped,
                     'matched': {'people': sum(s['people'] for s in spans.values()), 'scored': sum(s['scored'] for s in spans.values())}})


@write
def voter_scores(request):
    params(request, set())
    ids = json_body(request, {'ids'}).get('ids')
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_VOTERS:
        raise Refused('Send between 1 and %s voter ids.' % '{:,}'.format(MAX_VOTERS))
    wanted = {clean(ident, r'[0-9A-Za-z_-]{1,80}') for ident in ids}
    with W.walk_store() as store:
        if not store.attach_scores():
            raise Refused(SCORES_NOT_BUILT, 503)
        found = store.scores(wanted)
    candidate = bool(settings.DISTRICT['candidate'])
    return response({'scores': {ident: {'ovr': FL.display_round(ovr), 'part': FL.display_round(part),
                                        **({'sup': FL.display_round(sup), 'push': FL.display_round(FL.push(sup, part))}
                                           if candidate and sup is not None and part is not None else {})}
                                for ident, (ovr, part, sup) in found.items()}})


# ---- saved lists and results ---------------------------------------------------------------------------------------

def freeze(payload, keys, title, kind, parent, info):
    """Store the payload under a new short key, with the list's own details in it; returns the payload to send."""
    created = timezone.now()
    for _ in range(8):
        key = secrets.token_hex(5)
        payload['list'] = {'key': key, 'title': title, 'kind': kind, 'created_at': local_iso(created)}
        try:
            with transaction.atomic():
                WalkList.objects.create(key=key, title=title, kind=kind, parent=parent, query=payload['query'],
                                        payload={**payload, PRIVATE: keys}, source_sha256=info.source_sha256,
                                        created_at=created)
            return payload
        except IntegrityError:
            continue
    raise Refused('The list could not be saved. Try again.', 503)


def current_codes(walk_list):
    """{person id: (voter key, code or None)}: the newest entry of each person on the list."""
    state = {}
    for person, key, code in Disposition.objects.filter(walk_list=walk_list).order_by('id').values_list('person_id', 'voter_key', 'code'):
        state[person] = (key, code)
    return state


def places_of(payload):
    """{person id: (street, house, unit)}: the door each voter of a frozen list was shown at."""
    return {voter['person_id']: (run['street'], door['house'], door['unit'])
            for run in payload.get('runs', ()) for door in run['doors'] for voter in door['voters']}


def not_home(rows, again, places, names):
    """The rows of the voters in `again` ({voter key: person ids it was recorded for}), found by voter key: person ids are
    the contact stores' and change when those are rebuilt under a new salt. A key on several rows (one voter id twice in
    the file) keeps the people the result was recorded for, else the rows at the door it was recorded at (`places`); a
    key on one row is that voter wherever they live now. Anything else is unknown and left out."""
    found = {}
    for row in rows:
        found.setdefault(row['voter_key'], []).append(row)
    out = []
    for key, group in found.items():
        mine = [r for r in group if r['person_id'] in again[key]]
        if not mine and len(group) > 1:
            at = {places.get(person) for person in again[key]}
            mine = [r for r in group if (street_label(names, r['street_id']), house_label(r['house']), r['unit'] or '') in at]
        out.extend(mine or (group if len(group) == 1 else []))
    return out


def reknock(title, parent_key):
    parent = find_list(parent_key)
    again = {}
    for person, (key, code) in current_codes(parent).items():
        if code == 'NH':
            again.setdefault(key, set()).add(person)
    if not again:
        raise Refused('Nobody on that list is marked Not home.')
    with W.walk_store() as store:
        rows = not_home(store.voters_by_key(again), again, places_of(parent.payload), store.directory().by_id)
        if not rows:
            raise Refused('Those voters are no longer in the walk list data.', 409)
        payload, keys = plan_payload(store, rows, {'from': parent.key, 'reknock': '1'})
        info = store.info
    attach_last(payload, keys)
    return freeze(payload, keys, title, 'reknock', parent, info), len(again) - len({row['voter_key'] for row in rows})


@write
def create_list(request):
    params(request, set())
    body = json_body(request, {'title', 'query', 'fingerprint', 'from', 'reknock'})
    title = clean_title(body.get('title'))
    if 'from' in body or 'reknock' in body:
        if set(body) != {'title', 'from', 'reknock'} or body['reknock'] is not True:
            raise BadRequest('A re-knock list needs title, from and reknock: true')
        payload, missing = reknock(title, body['from'])
        return response({**C.attach(payload), 'missing': missing}, 201)     # voters marked Not home the data no longer has
    query = body.get('query')
    if set(body) - {'fingerprint'} != {'title', 'query'} or not isinstance(query, dict) or not set(query) <= QUERY_KEYS:
        raise BadRequest('A list needs a title and a query')
    if 'fingerprint' not in body:       # only a plain save needs it: a re-knock list above takes none (and refuses one)
        raise Refused('This page is out of date. Reload it, then save the list again.')
    shown = clean(body['fingerprint'], r'[0-9a-f]{64}')
    picks, spans, area, query = list_query(query)
    with W.walk_store() as store:
        payload, keys = standard_payload(store, picks, spans, area, query)
        info = store.info
    attach_last(payload, keys)
    saved = C.attach(freeze(payload, keys, title, 'standard', None, info))
    # The list is built again to be saved. If the voter data changed since the walker was shown it, the list saved is the
    # new one, never quietly: `changed` tells the page to show the list it gets back instead of the one it had.
    return response({**saved, 'changed': shown != saved['fingerprint']}, 201)


@read
def list_lists(request):
    params(request, set())
    newest = list(WalkList.objects.order_by('-created_at', '-id').values_list('id', flat=True)[:LISTS_SHOWN])
    rows = {row['id']: row for row in WalkList.objects.filter(id__in=newest)       # totals out of the payload: only for these
            .annotate(doors=KT('payload__totals__doors'), voters=KT('payload__totals__voters'))
            .values('id', 'key', 'title', 'kind', 'created_at', 'doors', 'voters')}
    return response({'lists': [
        {'key': row['key'], 'title': row['title'], 'kind': row['kind'], 'created_at': local_iso(row['created_at']),
         'doors': int(row['doors'] or 0), 'voters': int(row['voters'] or 0)} for row in (rows[i] for i in newest)]})


def lists(request):
    if request.method == 'POST':
        return create_list(request)
    if request.method == 'GET':
        return list_lists(request)
    denied = local_denied(request)
    if denied is not None:
        return denied
    result = response({'error': 'GET or POST is required.'}, 405)
    result['Allow'] = 'GET, POST'
    return result


@read
def saved_list(request, key):
    params(request, set())
    payload = dict(find_list(key).payload)
    attach_last(payload, payload.pop(PRIVATE, {}) or {})
    return response(C.attach(payload))


@write
def dispositions(request):
    params(request, set())
    body = json_body(request, {'list', 'on', 'entries'})
    saved = find_list(body.get('list'))
    try:
        day = date.fromisoformat(clean(body.get('on'), r'\d{4}-\d{2}-\d{2}'))
    except ValueError:
        raise BadRequest('Invalid date') from None
    if not date(2000, 1, 1) <= day <= date.today() + timedelta(days=1):    # a day of slack for a browser clock in another zone
        raise Refused('The visit date must be a real day, not in the future.')
    entries = body.get('entries')
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_ENTRIES:
        raise Refused('Send between 1 and %d results at a time.' % MAX_ENTRIES)
    roster = saved.payload.get(PRIVATE) or {}
    made = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'person_id', 'code'}:
            raise BadRequest('Invalid entry')
        person, code = entry['person_id'], entry['code']
        if not isinstance(person, str) or person not in roster:
            raise Refused('Some of those voters are not on this list.')
        if code is not None and code not in CODES:
            raise Refused('Results are S, U, O, NH or MV; send null to clear one.')
        made.append(Disposition(walk_list=saved, person_id=person, voter_key=roster[person], code=code, contacted_on=day))
    with transaction.atomic():
        Disposition.objects.bulk_create(made)
    return response({'saved': len(made)})
