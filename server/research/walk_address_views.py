"""Resolve imported street names, registration doors and full imported walk lists.

Exact imported addresses never expand through this resolver. Unique normalized names
match directly; optional proximity uses other established upload locations to resolve
clear geographic choices, retaining uncertain or unsupported choices for review.
The snapshot binds street ids to the selected district and the verified local stores.
"""
import hashlib
import json
import math
import re
from collections import defaultdict
from difflib import SequenceMatcher

from django.conf import settings

from . import walk_names as N
from . import walk_store as W
from . import walk_views as V
from . import walk_location_evidence as L
from . import walk_record_evidence as R
from .street_views import response
from .v3_http import BadRequest, params

MAX_CANDIDATES = 20
MAX_ADDRESS_DOORS = 10000
FIELDS = {'street': 120, 'city': 60, 'state': 30, 'zip': 10}
MAX_ANCHORS, MAX_ANCHOR_STREETS = 500, 5000
MAX_GEO_POINTS, MAX_DISTANCE_CHECKS = 50000, 1000000
SEARCH_METRES, NEAR_METRES, CLEAR_MARGIN_METRES = 5000, 2000, 500
LOCAL_LINK_METRES, LOCAL_DIAMETER_METRES, MAX_LOCAL_GROUPS = 1500, 3000, 100


def snapshot(info):
    value = [settings.DISTRICT['id'], str(info.path), info.stamp, info.source_sha256]
    return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()


def zip_filter(value):
    if not isinstance(value, str) or (value and not re.fullmatch(r'\d{5}(?:-\d{4})?', value)):
        raise BadRequest('Invalid ZIP')
    return value[:5]


def input_row(row):
    if not isinstance(row, dict) or not set(row) <= set(FIELDS):
        raise BadRequest('Invalid street row')
    out = {}
    for field, limit in FIELDS.items():
        value = row.get(field, '')
        if not isinstance(value, str) or len(value) > limit or V.CONTROL.search(value):
            raise BadRequest('Invalid street field')
        out[field] = value.strip()
    out['zip'] = zip_filter(out['zip'])
    if not N.name_key(out['street']):
        raise BadRequest('Missing street')
    return out


def counts(store, ids, zip_code=''):
    """Exact door counts, including separate apartments; no person fields leave SQL."""
    out = {i: 0 for i in ids}
    for chunk in W._chunks(sorted(set(ids))):
        clause = ' AND substr(p.zip, 1, 5)=?' if zip_code else ''
        rows = store.conn.execute(
            'SELECT street_id, COUNT(*) FROM (SELECT p.street_id AS street_id FROM person p '
            'JOIN stop s ON s.id=p.stop_id WHERE p.street_id IN (%s)%s '
            "GROUP BY p.street_id, p.stop_id, COALESCE(p.unit, '')) GROUP BY street_id"
            % (','.join('?' * len(chunk)), clause), [*chunk, *([zip_code] if zip_code else [])])
        out.update(rows)
    return out


def zip_counts(store, codes):
    """One bounded query for all requested ZIPs, rather than a district scan per row."""
    if not codes:
        return {}
    return {(street, code): doors for street, code, doors in store.conn.execute(
        'SELECT street_id, zip, COUNT(*) FROM (SELECT p.street_id AS street_id, substr(p.zip, 1, 5) AS zip '
        'FROM person p JOIN stop s ON s.id=p.stop_id WHERE substr(p.zip, 1, 5) IN (%s) '
        "GROUP BY p.street_id, substr(p.zip, 1, 5), p.stop_id, COALESCE(p.unit, '')) GROUP BY street_id, zip"
        % ','.join('?' * len(codes)), sorted(codes))}


def plausible_name(text, candidate):
    """A nearby street still needs the same numeric/directional core and a close name."""
    def core(value):
        words = N.name_key(value).split()
        at = -2 if words and words[-1] in N.DIRECTIONS else -1
        if len(words) > abs(at) and words[at] in N.SUFFIXES:
            del words[at]
        return words
    a, b = core(text), core(candidate)
    fixed = lambda words: [w for w in words if any(c.isdigit() for c in w) or w in N.DIRECTIONS]
    return bool(a and b and fixed(a) == fixed(b) and SequenceMatcher(None, ' '.join(a), ' '.join(b)).ratio() >= .85)


def find_candidates(row, directory, by_zip, scopes, proximity=False):
    text, city, state, code = (row[k] for k in FIELDS)
    result = {'input': text, 'status': 'none', 'candidates': [], 'truncated': False}
    # Ordinal street names (1st Ave) are valid. A house-number prefix is an exact
    # address, even when a caller accidentally sends it to the street endpoint.
    if re.match(r'^\d+(?!\d)(?!st\b|nd\b|rd\b|th\b)(?:[A-Za-z]?\b|[-/])', text, re.I):
        return {**result, 'reason': 'This contains a house number. Keep it as an exact address.'}, [], False
    if state and state.casefold() not in (settings.DISTRICT['state'].casefold(), settings.DISTRICT['state_name'].casefold()):
        return {**result, 'reason': 'This state is outside the selected district.'}, [], False
    scope = (V.city_key(city), code)
    if scope not in scopes:
        scopes[scope] = {s.id for s in directory.streets
                         if (not city or V.city_key(s.city) == scope[0]) and (not code or (s.id, code) in by_zip)}
    allowed = scopes[scope]
    exact = [s for s in directory.by_key.get(N.name_key(text), ()) if s.id in allowed]
    # One extra candidate proves truncation; locality is applied before this cap.
    found = exact[:MAX_CANDIDATES + 1] or directory.search(text, MAX_CANDIDATES + 1, allowed)
    result['truncated'] = len(found) > MAX_CANDIDATES
    if proximity and not exact:
        alternatives = sorted(directory.near(N.name_key(text).split(), allowed), key=lambda s: (s.display, s.city, s.id))
        found = list({s.id: s for s in [*found, *alternatives] if plausible_name(text, s.display)}.values())[:MAX_CANDIDATES + 1]
        result['truncated'] |= len(found) > MAX_CANDIDATES
    return result, found, bool(exact)


def resolved(row, found, exact, result, door_counts):
    code = row['zip']
    # Filtering out zero-door or implausible entries cannot prove that unseen
    # candidates beyond the search cap are absent.
    result['truncated'] = result.get('truncated', False) or len(found) > MAX_CANDIDATES
    found = [s for s in found if door_counts.get((s.id, code), 0)]
    if not found:
        return {**result, 'reason': 'More matches exist. Add a city or ZIP code to narrow the street.' if result['truncated'] else result.get('reason', 'No matching registered doors in the selected district and supplied locality.')}
    result['status'] = 'matched' if len(found) == 1 and exact and not result['truncated'] and N.name_key(found[0].display) == N.name_key(row['street']) else 'pick'
    result['candidates'] = [
        {'id': s.id, 'label': s.display, 'city': N.display_city(s.city), 'state': settings.DISTRICT['state'],
         'zip': code or s.zip, 'zip_filter': code, 'doors': door_counts[s.id, code]}
        for s in found[:MAX_CANDIDATES]]
    if result['truncated']:
        result['reason'] = 'More matches exist. Add a city or ZIP code to narrow the street.'
    elif not exact:
        result['reason'] = 'Choose a suggested street to confirm this spelling.'
    return result


def resolve_rows(rows, directory, store, proximity=False, anchors=(), selected_streets=()):
    by_zip = zip_counts(store, {r['zip'] for r in rows if r['zip']})
    cached, scopes, prepared = {}, {}, []
    for row in rows:
        key = tuple(row.values())
        if key not in cached:
            cached[key] = find_candidates(row, directory, by_zip, scopes, proximity)
        prepared.append(cached[key])
    # Resolve all non-ZIP candidate counts together, so a 500-row file does not
    # issue 500 household GROUP BY queries. ZIP counts were obtained in one pass.
    ids = {s.id for row, (_, found, _) in zip(rows, prepared) if not row['zip'] for s in found}
    door_counts = {**by_zip, **{(street, ''): doors for street, doors in counts(store, ids).items()}}
    results = [resolved(row, found, exact, dict(result), door_counts)
               for row, (result, found, exact) in zip(rows, prepared)]
    for result in results:
        result['method'] = 'exact' if result['status'] == 'matched' else 'review'
        if result['status'] == 'matched':
            result['selected_id'] = result['candidates'][0]['id']
    if proximity:
        infer_proximity(rows, results, directory, store, anchors, selected_streets)
    return results


def anchor_inputs(values):
    if not isinstance(values, list) or len(values) > MAX_ANCHORS:
        raise V.Refused('Use at most %d exact address anchors.' % MAX_ANCHORS)
    out = []
    for value in values:
        if not isinstance(value, dict) or not set(value) <= {*FIELDS, 'house', 'unit'}:
            raise BadRequest('Invalid anchor')
        row = input_row({k: v for k, v in value.items() if k in FIELDS})
        for key, limit in (('house', 30), ('unit', 80)):
            text = value.get(key, '')
            if not isinstance(text, str) or len(text) > limit or V.CONTROL.search(text):
                raise BadRequest('Invalid anchor field')
            row[key] = text.strip()
        if not row['house']:
            raise BadRequest('An address anchor needs its exact house number')
        out.append(row)
    return out


def unit_key(value):
    return re.sub(r'[\s.#]', '', re.sub(r'^(?:apartment|apt\.?|unit|suite|ste\.?)\s*', '', value, flags=re.I)).upper()


def exact_anchor_match(store, anchor, street_ids):
    """Resolve identity before checking coordinates, so unlocated twins still cause ambiguity."""
    matches = set()
    for chunk in W._chunks(street_ids):
        code = anchor['zip']
        clause = ' AND substr(p.zip,1,5)=?' if code else ''
        rows = store.conn.execute(
            "SELECT DISTINCT p.street_id,p.stop_id,COALESCE(p.unit,'') FROM person p JOIN stop s ON s.id=p.stop_id "
            'WHERE p.street_id IN (%s) AND UPPER(s.house)=?%s' % (','.join('?' * len(chunk)), clause),
            [*chunk, anchor['house'].upper(), *([code] if code else [])])
        for street, stop, unit in rows:
            if unit_key(unit) == unit_key(anchor['unit']):
                matches.add((street, stop, unit_key(unit)))
                if len(matches) > 1:
                    return None
    return next(iter(matches)) if len(matches) == 1 else None


def geo_points(store, ids):
    """Original registration geocodes only; interpolated/area-centre placements cannot be evidence.

    Only direct or name-fuzzy road placements are accepted. Nearest-road placements
    are conservatively excluded as that category also includes area-centre placement.
    """
    out = defaultdict(list)
    total = 0
    for chunk in W._chunks(sorted(ids)):
        found = store.conn.execute(
            "SELECT DISTINCT s.id, p.street_id, s.house, COALESCE(p.unit,''), COALESCE(p.zip,''), s.lon, s.lat "
            'FROM person p JOIN stop s ON s.id=p.stop_id WHERE p.street_id IN (%s) '
            "AND s.placed_by IN ('geocode','fuzzy') AND s.lon IS NOT NULL AND s.lat IS NOT NULL "
            'ORDER BY p.street_id,s.id LIMIT ?' % ','.join('?' * len(chunk)), [*chunk, MAX_GEO_POINTS + 1 - total]).fetchall()
        total += len(found)
        if total > MAX_GEO_POINTS:
            return None
        for stop, street, house, unit, code, lon, lat in found:
            if math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 < lat < 90 and (lon or lat):
                out[street].append((stop, house or '', unit, code, lon, lat))
    return out


def distance(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 12742000 * math.asin(min(1, math.sqrt(max(0, h))))


def proximity_review(results, reason):
    for result in results:
        if result['status'] == 'pick':
            result['proximity'] = {'distance_m': None, 'runner_up_distance_m': None, 'anchor_count': 0,
                                   'confidence': 'review', 'reason': reason}


def infer_proximity(rows, results, directory, store, anchors, selected_streets):
    # Collect evidence once before any inference. Inferred winners never become anchors.
    street_anchors = set(selected_streets)
    street_anchors.update((r['candidates'][0]['id'], r['candidates'][0]['zip_filter'])
                          for r in results if r['method'] == 'exact')
    anchor_options = []
    ids = {c['id'] for r in results for c in r['candidates']} | {i for i, _ in street_anchors}
    for anchor in anchors:
        if anchor['state'] and anchor['state'].casefold() not in (settings.DISTRICT['state'].casefold(), settings.DISTRICT['state_name'].casefold()):
            anchor_options.append((anchor, []))
            continue
        found = [s.id for s in directory.by_key.get(N.name_key(anchor['street']), ())
                 if not anchor['city'] or V.city_key(s.city) == V.city_key(anchor['city'])]
        ids.update(found)
        anchor_options.append((anchor, found))
    points = geo_points(store, ids)
    if points is None:
        proximity_review(results, 'Too many locations for a bounded proximity check. Narrow the upload or review these choices.')
        return
    sources = {}
    for street, code in sorted(street_anchors):
        places = {(p[4], p[5]) for p in points.get(street, ()) if not code or p[3][:5] == code}
        if places:
            sources[('street', street, code)] = places
    checked_anchors = {}
    for anchor, options in anchor_options:
        cache_key = (*anchor.values(), *options)
        if cache_key not in checked_anchors:
            checked_anchors[cache_key] = exact_anchor_match(store, anchor, options)
        key = checked_anchors[cache_key]
        if key:
            places = {(p[4], p[5]) for p in points.get(key[0], ()) if p[0] == key[1] and unit_key(p[2]) == key[2]
                      and (not anchor['zip'] or p[3][:5] == anchor['zip'])}
            if places:
                sources[('address', *key)] = places
    if not sources:
        proximity_review(results, 'No other exact or confirmed upload entries have reliable coordinates. Choose a street or add an exact address.')
        return
    # Earth-centred 3-D cells cannot miss a point within SEARCH_METRES: chord length
    # is at most surface distance, so every coordinate differs by at most one cell.
    # The index only finds neighbors; final distances are haversine metres.
    def cell(point):
        lon, lat = map(math.radians, point)
        return tuple(math.floor(value * 6371000 / SEARCH_METRES) for value in (
            math.cos(lat)*math.cos(lon), math.cos(lat)*math.sin(lon), math.sin(lat)))
    grid = defaultdict(list)
    for source, places in sources.items():
        for place in sorted(places):
            grid[cell(place)].append((source, place))
    checks, decisions = 0, []
    for row, result in zip(rows, results):
        if result['status'] != 'pick':
            continue
        if result['truncated']:
            decisions.append((result, None, 'More candidate streets exist. Narrow the name or locality before proximity can choose.'))
            continue
        ranked, missing = [], False
        for candidate in result['candidates']:
            places = {(p[4], p[5]) for p in points.get(candidate['id'], ())
                      if not candidate['zip_filter'] or p[3][:5] == candidate['zip_filter']}
            if not places:
                missing = True
                continue
            best, closest, nearby = float('inf'), None, set()
            for place in sorted(places):
                x, y, z = cell(place)
                for dx in range(-1, 2):
                    for dy in range(-1, 2):
                        for dz in range(-1, 2):
                            for source, anchor_place in grid.get((x+dx, y+dy, z+dz), ()):
                                checks += 1
                                if checks > MAX_DISTANCE_CHECKS:
                                    proximity_review(results, 'The bounded proximity check reached its location limit. Review these choices or narrow the upload.')
                                    return
                                metres = distance(place, anchor_place)
                                if metres <= NEAR_METRES:
                                    nearby.add(source)
                                if metres <= SEARCH_METRES and metres < best:
                                    best, closest = metres, anchor_place
            ranked.append((best, candidate['id'], closest, len(nearby)))
        ranked.sort(key=lambda value: (value[0], value[1]))
        if missing:
            decisions.append((result, ranked, 'At least one candidate lacks reliable coordinates, so proximity cannot safely rule it out.'))
            continue
        if not ranked or ranked[0][0] > NEAR_METRES:
            decisions.append((result, ranked, 'No candidate is within 2 km of another resolved upload location.'))
            continue
        best = ranked[0]
        rivals = ranked[1:]
        if any(r[0] <= NEAR_METRES and distance(best[2], r[2]) > NEAR_METRES for r in rivals):
            decisions.append((result, ranked, 'Different uploaded neighborhoods support different candidates. Choose which neighborhood this street belongs to.'))
        elif rivals and (rivals[0][0] - best[0] < CLEAR_MARGIN_METRES or rivals[0][0] < 3 * best[0]):
            decisions.append((result, ranked, 'The nearest candidates are too close to distinguish reliably.'))
        elif not plausible_name(row['street'], next(c['label'] for c in result['candidates'] if c['id'] == best[1])):
            decisions.append((result, ranked, 'The nearby candidate does not sufficiently match the supplied street name.'))
        else:
            decisions.append((result, ranked, None))
    for result, ranked, reason in decisions:
        best = ranked[0] if ranked else None
        runner = ranked[1] if ranked and len(ranked) > 1 else None
        evidence = {'distance_m': round(best[0]) if best and math.isfinite(best[0]) else None,
                    'runner_up_distance_m': round(runner[0]) if runner and math.isfinite(runner[0]) else None,
                    'anchor_count': best[3] if best else 0, 'confidence': 'review' if reason else 'clear',
                    'reason': reason or 'Selected by physical proximity to other resolved locations in this upload.'}
        if runner and not math.isfinite(runner[0]):
            evidence['runner_up_beyond_m'] = SEARCH_METRES
        result['proximity'] = evidence
        if not reason:
            result.update(status='matched', method='proximity', selected_id=best[1], reason=evidence['reason'])


def resolve_row(row, directory, store):
    return resolve_rows([row], directory, store)[0]


@V.manager_write
def address_match(request):
    params(request, set())
    body = V.json_body(request, {'rows', 'proximity', 'anchors', 'selected_streets', 'snapshot'})
    rows = body.get('rows')
    if not isinstance(rows, list) or not rows or len(rows) > V.MAX_ROWS:
        raise V.Refused('Send between 1 and %d street rows.' % V.MAX_ROWS)
    inputs = [input_row(row) for row in rows]
    proximity = body.get('proximity', False)
    if type(proximity) is not bool:
        raise BadRequest('Invalid proximity flag')
    anchors = anchor_inputs(body.get('anchors', []))
    selected_values = body.get('selected_streets', [])
    if not isinstance(selected_values, list):
        raise BadRequest('Invalid selected streets')
    selected = selections(selected_values, MAX_ANCHOR_STREETS) if selected_values else []
    with W.walk_store() as store:
        stamp = snapshot(store.info)
        if selected and body.get('snapshot') != stamp:
            raise V.Refused('The district data changed. Resolve the street list again.', 409)
        if any(street not in store.directory().by_id for street, _ in selected):
            raise V.Refused('An anchor street is not in the selected district.', 409)
        out = [{'row': number, **result} for number, result in enumerate(
            resolve_rows(inputs, store.directory(), store, proximity, anchors, selected), 1)]
    if stamp != snapshot(W.verify()):
        raise V.Refused('The district data changed. Resolve the street list again.', 409)
    return response({'district': settings.DISTRICT['id'], 'snapshot': stamp, 'rows': out})


def selections(values, limit=V.MAX_STREETS):
    if not isinstance(values, list) or not 1 <= len(values) <= limit:
        raise V.Refused('Select between 1 and %d streets per request.' % limit)
    out = []
    for item in values:
        if isinstance(item, dict):
            if not set(item) <= {'id', 'zip'}:
                raise BadRequest('Invalid selection')
            street, code = item.get('id'), zip_filter(item.get('zip', ''))
        else:
            street, code = item, ''
        if type(street) is not int or not 1 <= street <= 999999999:
            raise BadRequest('Invalid street id')
        if (street, code) not in out:
            out.append((street, code))
    return out


def door_rows(store, street, code, room):
    clause = ' AND substr(p.zip, 1, 5)=?' if code else ''
    return store.conn.execute(
        "SELECT p.stop_id, COALESCE(p.unit, '') AS unit, s.house, s.house_int, "
        "COUNT(DISTINCT NULLIF(p.zip, '')) AS zip_count, MIN(NULLIF(p.zip, '')) AS zip "
        'FROM person p JOIN stop s ON s.id=p.stop_id WHERE p.street_id=?%s '
        "GROUP BY p.stop_id, COALESCE(p.unit, '') "
        'ORDER BY s.house_int IS NULL, s.house_int, s.house, unit LIMIT ?' % clause,
        [street, *([code] if code else []), room]).fetchall()


@V.manager_write
def address_doors(request):
    params(request, set())
    body = V.json_body(request, {'snapshot', 'streets'})
    stamp = body.get('snapshot')
    if not isinstance(stamp, str) or not re.fullmatch(r'[a-f0-9]{64}', stamp):
        raise BadRequest('Invalid snapshot')
    picks = selections(body.get('streets'))
    out = []
    with W.walk_store() as store:
        if stamp != snapshot(store.info):
            raise V.Refused('The district data changed. Resolve the street list again.', 409)
        directory = store.directory()
        for street_id, code in picks:
            street = directory.by_id.get(street_id)
            if street is None:
                raise V.Refused('A selected street is not in this district. Resolve the street list again.', 409)
            rows = door_rows(store, street_id, code, MAX_ADDRESS_DOORS + 1 - len(out))
            if not rows:
                raise V.Refused('A selected street has no registered doors in the supplied ZIP code.', 409)
            for row in rows:
                house, unit = row['house'] or '', row['unit']
                zip_code = (row['zip'] or '') if row['zip_count'] <= 1 else ''
                warnings = []
                if not house:
                    warnings.append('No house number recorded.')
                if row['zip_count'] > 1:
                    warnings.append('Multiple ZIP codes recorded for this door; left blank.')
                city, state = N.display_city(street.city), settings.DISTRICT['state']
                unit_label = unit if re.match(r'^(?:apt|apartment|unit|suite|ste|lot|bldg|building|floor|fl)\b|^#', unit, re.I) else '# ' + unit if unit else ''
                line = ' '.join(part for part in (house, street.display, unit_label) if part)
                address = ', '.join(part for part in (line, city, ' '.join(part for part in (state, zip_code) if part)) if part)
                out.append({'street_id': street_id, 'house': house, 'street': street.display, 'unit': unit,
                            'city': city, 'state': state, 'zip': zip_code, 'zip_filter': code,
                            'address': address, 'warnings': warnings})
            if len(out) > MAX_ADDRESS_DOORS:
                raise V.Refused('These streets exceed %d doors. Request fewer streets at a time.' % MAX_ADDRESS_DOORS)
    if stamp != snapshot(W.verify()):
        raise V.Refused('The district data changed. Resolve the street list again.', 409)
    return response({'district': settings.DISTRICT['id'], 'snapshot': stamp, 'addresses': out})


def selected_voters(store, picks):
    """Read each exact street/ZIP selection, retaining membership and a bounded union.

    Overlapping selections share people in the route but keep independent membership
    so a client can reproduce repeated source requests without broadening their ZIPs.
    Every query and the combined union must fit; no partial list is returned.
    """
    rows, selected = {}, []
    for street, code in picks:
        if street not in store.directory().by_id:
            raise V.Refused('A selected street is not in this district. Resolve the street list again.', 409)
        clause = ' AND substr(p.zip, 1, 5)=?' if code else ''
        found = store.conn.execute(
            W.VOTER_SQL.replace('SELECT ', 'SELECT p.zip AS zip, ', 1)
            + ' WHERE p.street_id=?' + clause + ' ORDER BY p.person_id LIMIT ?',
            [street, *([code] if code else []), V.MAX_VOTERS + 1]).fetchall()
        if not found:
            raise V.Refused('A selected street has no registered doors in the supplied ZIP code.', 409)
        rows.update((row['person_id'], row) for row in found)
        if len(rows) > V.MAX_VOTERS:
            raise V.Refused('These selections exceed %s voters. Build fewer streets at a time; no doors have been omitted.'
                            % format(V.MAX_VOTERS, ','))
        selected.append({'id': street, 'zip': code, 'person_ids': [row['person_id'] for row in found]})
    return [rows[key] for key in sorted(rows)], selected


def attach_door_locality(payload, rows, directory):
    """Use only the included registration rows for imported-door address fields.

    A street's summary ZIP is never a substitute for a door's actual records.
    A mixed-ZIP union stays conservatively blank even if a client later filters it.
    """
    people, reasons = {row['person_id']: row for row in rows}, {}
    for run in payload['runs']:
        street = directory.by_id[run['street_id']]
        city, state = N.display_city(street.city), settings.DISTRICT['state']
        for door in run['doors']:
            members = [people[voter['person_id']] for voter in door['voters']]
            house, unit = members[0]['house'] or '', door['unit']
            codes = {member['zip'] for member in members if member['zip']}
            code = next(iter(codes)) if len(codes) == 1 else ''
            warnings, reasons[door['seq']] = [], []
            if not house:
                warnings.append('No house number recorded.')
                reasons[door['seq']].append('no_house_number')
            if len(codes) > 1:
                warnings.append('Multiple ZIP codes recorded for this door; left blank.')
                reasons[door['seq']].append('mixed_zip')
            unit_label = unit if re.match(r'^(?:apt|apartment|unit|suite|ste|lot|bldg|building|floor|fl)\b|^#', unit, re.I) else '# ' + unit if unit else ''
            line = ' '.join(part for part in (house, street.display, unit_label) if part)
            address = ', '.join(part for part in (line, city, ' '.join(part for part in (state, code) if part)) if part)
            door.update(city=city, state=state, zip=code, address=address, warnings=warnings)
    return reasons


def import_locations(store, rows):
    """Keep original coordinates as evidence; remove unsupported map placements only.

    A geocode is not invalid merely because it is in another neighborhood. Inferred
    points need a house number and the selected street's named road, otherwise the
    entire household remains printable without an invented replacement location.
    """
    spans = {}
    for chunk in W._chunks(sorted({r['span_id'] for r in rows if r['span_id'] is not None})):
        spans.update(store.conn.execute('SELECT id, name_key FROM road_span WHERE id IN (%s)'
                                        % ','.join('?' * len(chunk)), chunk))
    directory = store.directory()
    clean, omitted = [], {}
    for original in rows:
        row = dict(original)
        lon, lat, placed = row['lon'], row['lat'], row['placed_by'] or ''
        road = spans.get(row['span_id'])
        finite = (isinstance(lon, (int, float)) and isinstance(lat, (int, float))
                  and math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180
                  and -90 < lat < 90 and bool(lon or lat))
        inferred = placed in ('nearest', 'interpolated')
        reason = code = None
        if not finite:
            if lon is not None or lat is not None or row['span_id'] is not None:
                reason, code = 'Missing or invalid source coordinates; map placement omitted.', 'missing_coordinates'
        elif inferred and not str(row['house'] or '').strip():
            reason, code = 'An inferred location has no house number; map placement omitted.', 'inferred_no_house'
        elif inferred and (road is None or N.name_key(road) != directory.by_id[row['street_id']].key):
            reason, code = 'The inferred source location is on a different or unknown named road; map placement omitted.', 'inferred_off_road'
        elif placed not in ('geocode', 'fuzzy', 'nearest', 'interpolated'):
            reason, code = 'The source location has no supported placement provenance; map placement omitted.', 'unsupported_provenance'
        if reason or not finite:
            row.update(lon=None, lat=None, span_id=None, pos_m=None, placed_by='unlocated')
        if reason:
            key = (row['stop_id'], row['unit'] or '', row['precinct'] or '')
            if key not in omitted:
                safe = lambda value: value if value is None or (isinstance(value, (int, float)) and math.isfinite(value)) else None
                omitted[key] = {'street_id': row['street_id'], 'street': directory.by_id[row['street_id']].display,
                                'house': row['house'] or '', 'unit': row['unit'] or '', 'precinct': row['precinct'] or '',
                                'person_ids': [], 'original': {'lon': safe(lon), 'lat': safe(lat), 'placed_by': placed,
                                                              'span_id': original['span_id'], 'road_name': road},
                                'reason': reason, 'codes': [code]}
            omitted[key]['person_ids'].append(row['person_id'])
        clean.append(row)
    return clean, list(omitted.values())


def local_import_groups(rows):
    """Bounded, deterministic clusters of actual stop points, with no centroid guess.

    Within-cell points are at most 1.5 km apart. Adjacent cells join only through an
    actual pair of close stops, and a 3 km extent bound prevents long chains from
    turning back into one district-wide map. Unlocated rows inherit only a print
    group, never a location. Every person and every physical stop stays together.
    """
    stops = {}
    for row in rows:
        stops.setdefault(row['stop_id'], row)
    points, cells = {}, defaultdict(list)
    width = LOCAL_LINK_METRES / math.sqrt(3)
    for stop, row in sorted(stops.items()):
        if row['lon'] is None or row['lat'] is None:
            continue
        lon, lat = math.radians(row['lon']), math.radians(row['lat'])
        point = (6371000 * math.cos(lat) * math.cos(lon), 6371000 * math.cos(lat) * math.sin(lon), 6371000 * math.sin(lat))
        points[stop] = point
        cells[tuple(math.floor(value / width) for value in point)].append(stop)
    parent, boxes = {}, {}
    for key, members in cells.items():
        first = members[0]
        for stop in members:
            parent[stop] = first
        boxes[first] = ([min(points[s][i] for s in members) for i in range(3)],
                        [max(points[s][i] for s in members) for i in range(3)])
    def find(stop):
        while parent[stop] != stop:
            parent[stop] = parent[parent[stop]]
            stop = parent[stop]
        return stop
    checks = 0
    for key in sorted(cells):
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                for dz in range(-2, 3):
                    other = (key[0]+dx, key[1]+dy, key[2]+dz)
                    if other <= key or other not in cells:
                        continue
                    a, b = find(cells[key][0]), find(cells[other][0])
                    if a == b:
                        continue
                    lo = [min(boxes[a][0][i], boxes[b][0][i]) for i in range(3)]
                    hi = [max(boxes[a][1][i], boxes[b][1][i]) for i in range(3)]
                    if sum((hi[i]-lo[i])**2 for i in range(3)) > LOCAL_DIAMETER_METRES**2:
                        continue
                    close = False
                    for first in cells[key]:
                        for second in cells[other]:
                            checks += 1
                            if checks > MAX_DISTANCE_CHECKS:
                                raise V.Refused('Local map grouping reached its bounded location limit. Request fewer streets; no records have been omitted.')
                            if math.dist(points[first], points[second]) <= LOCAL_LINK_METRES:
                                close = True
                                break
                        if close:
                            break
                    if close:
                        root, child = min(a, b), max(a, b)
                        parent[child], boxes[root] = root, (lo, hi)
    assignment = {stop: find(stop) for stop in points}
    sizes, streets = defaultdict(int), defaultdict(lambda: defaultdict(int))
    for stop, group in assignment.items():
        sizes[group] += 1
        streets[stops[stop]['street_id']][group] += 1
    primary = min(sizes, key=lambda group: (-sizes[group], group)) if sizes else 0
    for stop, row in sorted(stops.items()):
        if stop not in assignment:
            nearby = streets[row['street_id']]
            assignment[stop] = min(nearby, key=lambda group: (-nearby[group], group)) if nearby else primary
    groups = defaultdict(list)
    for row in rows:
        groups[assignment[row['stop_id']]].append(row)
    if len(groups) > MAX_LOCAL_GROUPS:
        raise V.Refused('These selections need more than %d local maps. Request fewer streets; no records have been omitted.' % MAX_LOCAL_GROUPS)
    return [groups[group] for group in sorted(groups, key=lambda group: (-len(groups[group]), group))]


SOURCE_PLACEMENTS = ('geocode', 'fuzzy', 'nearest', 'interpolated')
# A vendor geocode is a source claim, not a verified address point; unknown provenance stays unlocated.
PRECISION = {'parcel': 'parcel', 'interpolated': 'interpolated', 'geocode': 'source_point',
             'fuzzy': 'source_point', 'nearest': 'source_point'}


def door_evidence(door, items, proof, locality, sources):
    """Typed per-door evidence (version 1) from codes attached where each reason was created."""
    if proof:
        recovery = proof.get('registration_recovery')
        status = 'recovered_number' if recovery else 'independently_confirmed'
        codes = proof.get('codes', [])
    else:
        codes = [code for item in items for code in item.get('codes', [])]
        status = ('conflicting' if 'conflicting_parcel_elsewhere' in codes else 'unresolved') if items else 'source_exact'
        recovery = None
    precision = PRECISION.get(door['placed_by'], 'unlocated')
    source = next((sources[v['person_id']] for v in door['voters'] if v['person_id'] in sources), None)
    return {'version': 1, 'address_status': status, 'location_precision': precision,
            'decision': 'accepted' if status == 'source_exact' and precision in ('source_point', 'interpolated') else 'review',
            'reason_codes': list(dict.fromkeys([*codes, *locality])),
            'source_placement': source if source in SOURCE_PLACEMENTS else None,
            'original_house': recovery['registration']['house'] if recovery else None}


def build_import_payload(store, rows, query, omissions=(), corrections=(), sources=None):
    """`sources` maps person id to the store's placed_by before import/repair changes (default: `rows`)."""
    payload, keys = V.plan_payload(store, rows, query, preserve_unlocated=True)
    locality = attach_door_locality(payload, rows, store.directory())
    sources = sources if sources is not None else {row['person_id']: row['placed_by'] for row in rows}
    reasons = {person: item['reason'] for item in omissions for person in item['person_ids']}
    omitted = {person: item for item in omissions for person in item['person_ids']}
    evidence = {person: item for item in corrections for person in item['person_ids']}
    for run in payload['runs']:
        for door in run['doors']:
            for reason in dict.fromkeys(reasons[voter['person_id']] for voter in door['voters'] if voter['person_id'] in reasons):
                door['warnings'].append(reason + ' Original coordinates retained in provenance.')
            proofs = [evidence[voter['person_id']] for voter in door['voters'] if voter['person_id'] in evidence]
            if proofs:
                door['location_evidence'] = proofs[0]
                door['warnings'].append(L.PARCEL_WARNING + ' Original coordinates retained in provenance.')
                if proofs[0].get('registration_recovery'):
                    door['warnings'].append(R.RECOVERY_WARNING)
            items = [omitted[v['person_id']] for v in door['voters'] if v['person_id'] in omitted]
            door['evidence'] = door_evidence(door, items, proofs[0] if proofs else None, locality[door['seq']], sources)
    payload['fingerprint'] = V.fingerprint(payload, keys)
    if any(payload['bbox']):
        V.C.attach(payload, store.conn)
    return payload, keys


@V.manager_write
def address_list(request):
    """Full walk-list-1 output for reviewed imports; exact address rows stay separate.

    Uses the same people, route, map context and visit history as the normal walk
    list, while retaining strict snapshot/ZIP scope and honest missing coordinates.
    """
    params(request, set())
    body = V.json_body(request, {'snapshot', 'streets', 'split_sides', 'local_groups'})
    stamp = body.get('snapshot')
    if not isinstance(stamp, str) or not re.fullmatch(r'[a-f0-9]{64}', stamp):
        raise BadRequest('Invalid snapshot')
    split_sides = body.get('split_sides')
    if type(split_sides) is not bool:
        raise BadRequest('Invalid split sides flag')
    local_groups = body.get('local_groups', False)
    if type(local_groups) is not bool:
        raise BadRequest('Invalid local groups flag')
    picks = selections(body.get('streets'))
    with W.walk_store() as store:
        if stamp != snapshot(store.info):
            raise V.Refused('The district data changed. Resolve the street list again.', 409)
        rows, selected = selected_voters(store, picks)
        query = {'source': 'address-import', 'snapshot': stamp, 'split_sides': '1' if split_sides else '0',
                 'streets': json.dumps([{'id': street, 'zip': code} for street, code in picks], separators=(',', ':'))}
        if local_groups:
            sources = {row['person_id']: row['placed_by'] for row in rows}
            rows, omissions = import_locations(store, rows)
            original_rows = rows
            rows, recoveries, recovery_warnings = R.prepare(store, rows, settings.DISTRICT['id'], settings.DISTRICT['state'])
            rows, omissions, corrections, evidence_warnings = L.repair_locations(
                store, rows, omissions, settings.DISTRICT['id'], settings.DISTRICT['state'])
            rows = R.finish(original_rows, rows, corrections, recoveries)
            groups = []
            histories = []
            for number, members in enumerate(local_import_groups(rows), 1):
                ids = {row['person_id'] for row in members}
                selections_here = [{**selection, 'person_ids': [person for person in selection['person_ids'] if person in ids]}
                                   for selection in selected if ids.intersection(selection['person_ids'])]
                payload, keys = build_import_payload(store, members, query, omissions, corrections, sources)
                groups.append({'key': 'local-%d' % number, 'payload': payload, 'selections': selections_here,
                               'reason': 'Local source coordinates; unlocated rows remain printable without a map location.'})
                histories.append((payload, keys))
            warnings = list(dict.fromkeys('%s: %s' % (item['street'], item['reason']) for item in [*omissions, *corrections]))
            warnings.extend(evidence_warnings)
            warnings.extend(recovery_warnings)
            if len(groups) > 1:
                warnings.append('Selected addresses span separate neighborhoods and have been divided into %d local maps.' % len(groups))
            result = {'groups': groups, 'warnings': warnings, 'map_placement_omissions': omissions,
                      'map_placement_corrections': corrections}
        else:
            payload, keys = build_import_payload(store, rows, query)
            histories = [(payload, keys)]
            result = {'payload': payload, 'selections': selected}
    for payload, keys in histories:
        V.attach_last(payload, keys)
    if stamp != snapshot(W.verify()):
        raise V.Refused('The district data changed. Resolve the street list again.', 409)
    return response({'district': settings.DISTRICT['id'], 'snapshot': stamp, **result})
