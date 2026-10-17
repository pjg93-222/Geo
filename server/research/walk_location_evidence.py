"""Reviewed public address evidence, applied without changing the source stores.

Parcel representative points locate an address parcel, not an entrance. They can
repair omitted import placements only with an exact address/unit/locality match,
an included reliable neighboring street, and a nearby real same-named road.
This module never fetches network data or interpolates an unconfirmed house.
"""
import hashlib
import json
import math
import re
from collections import defaultdict
from pathlib import Path

from . import walk_names as N

EVIDENCE_DIR = Path(__file__).with_name('location_evidence')
MAX_FILES, MAX_BYTES, MAX_ENTRIES = 16, 2_000_000, 2000
MAX_SPANS, MAX_VERTICES, MAX_CHECKS = 2000, 20000, 100000
ANCHOR_METRES, ROAD_METRES = 1500, 100
PARCEL_WARNING = 'Municipal parcel representative point; entrance position is not verified.'


def text(value):
    return ' '.join(str(value or '').strip().upper().split())


def point(lon, lat):
    return (type(lon) in (int, float) and type(lat) in (int, float)
            and math.isfinite(lon) and math.isfinite(lat)
            and -180 <= lon <= 180 and -90 < lat < 90 and bool(lon or lat))


def metres(a, b):
    k = math.cos(math.radians((a[1] + b[1]) / 2))
    return 111195 * math.hypot((a[0] - b[0]) * k, a[1] - b[1])


def source_valid(source):
    return (isinstance(source, dict) and isinstance(source.get('url'), str)
            and source['url'].startswith('https://') and len(source['url']) <= 4000
            and re.fullmatch(r'[a-f0-9]{64}', source.get('sha256', ''))
            and isinstance(source.get('retrieved_at'), str) and len(source['retrieved_at']) <= 50)


def validate_entry(entry, confirmed):
    if not isinstance(entry, dict) or not source_valid(entry.get('source')):
        raise ValueError('Invalid evidence source')
    address = entry.get('address', {})
    if not isinstance(address, dict):
        raise ValueError('Invalid evidence address object')
    for field in ('house', 'street', 'unit', 'city', 'state'):
        value = address.get(field)
        if not isinstance(value, str) or len(value) > 120 or any(ord(c) < 32 for c in value):
            raise ValueError('Invalid evidence address')
        if field != 'unit' and not value.strip():
            raise ValueError('Incomplete evidence address')
    if address.get('zip') is not None and not re.fullmatch(r'\d{5}', address.get('zip', '')):
        raise ValueError('Invalid evidence ZIP')
    aliases = entry.get('aliases', [])
    if not isinstance(aliases, list) or len(aliases) > 5 or any(not isinstance(a, str) or len(a) > 120 for a in aliases):
        raise ValueError('Invalid evidence aliases')
    if confirmed:
        location, context = entry.get('location', {}), entry.get('context', {})
        if not isinstance(location, dict) or not isinstance(context, dict):
            raise ValueError('Invalid evidence location or context object')
        if location.get('kind') != 'municipal_parcel' or not point(location.get('lon'), location.get('lat')):
            raise ValueError('Invalid evidence location')
        names = context.get('street_names', [])
        if not isinstance(names, list) or not 1 <= len(names) <= 20 or any(not isinstance(n, str) or not N.name_key(n) or len(n) > 120 for n in names):
            raise ValueError('Missing evidence neighborhood')
        polygon = entry.get('polygon', {})
        if not isinstance(polygon, dict):
            raise ValueError('Invalid evidence polygon object')
        rings = polygon.get('rings', [])
        if not isinstance(rings, list) or not rings or any(not isinstance(r, list) for r in rings) or sum(len(r) for r in rings) > MAX_VERTICES:
            raise ValueError('Invalid evidence parcel')
        inside = False
        x, y = location['lon'], location['lat']
        for ring in rings:
            if not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError('Invalid evidence parcel ring')
            for a, b in zip(ring, ring[1:]):
                if len(a) != 2 or len(b) != 2 or not point(*a) or not point(*b):
                    raise ValueError('Invalid parcel vertex')
                if (a[1] > y) != (b[1] > y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:
                    inside = not inside
        if not inside:
            raise ValueError('Representative point is outside parcel')
    elif not isinstance(entry.get('reason'), str) or len(entry['reason']) > 1000:
        raise ValueError('Invalid unconfirmed evidence reason')


def load_evidence(district, directory=None):
    """Small reviewed app assets only; malformed/oversized assets fail closed."""
    confirmed, unconfirmed, warnings = [], [], []
    directory = Path(directory) if directory is not None else EVIDENCE_DIR
    paths = sorted(directory.glob('*.json')) if directory.is_dir() else []
    if len(paths) > MAX_FILES:
        return [], [], ['Reviewed location evidence exceeded its file limit; no corrections applied.']
    size = 0
    for path in paths:
        try:
            size += path.stat().st_size
            if size > MAX_BYTES:
                raise ValueError('Evidence size limit')
            raw = path.read_bytes()
            if len(raw) > MAX_BYTES:
                raise ValueError('Evidence size changed while reading')
            bundle = json.loads(raw)
            if not isinstance(bundle, dict) or bundle.get('schema') != 1:
                raise ValueError('Invalid evidence schema')
            if bundle.get('district') != district:
                continue
            entries, missing = bundle.get('entries', []), bundle.get('unconfirmed', [])
            if not isinstance(entries, list) or not isinstance(missing, list) or len(entries)+len(missing)+len(confirmed)+len(unconfirmed) > MAX_ENTRIES:
                raise ValueError('Evidence entry limit')
            for entry in entries:
                validate_entry(entry, True)
            for entry in missing:
                validate_entry(entry, False)
            stamp = hashlib.sha256(raw).hexdigest()
            confirmed.extend({**e, 'bundle_sha256': stamp} for e in entries)
            unconfirmed.extend({**e, 'bundle_sha256': stamp} for e in missing)
        except (OSError, ValueError, TypeError, KeyError):
            # Atomic across all bundles: an incomplete evidence set cannot break a tie.
            return [], [], ['Reviewed location evidence could not be validated; no corrections applied.']
    return confirmed, unconfirmed, warnings


def address_key(house, street, unit, city, state):
    return text(house), N.name_key(street), text(unit), text(city), text(state)


def evidence_index(entries):
    index = defaultdict(list)
    for entry in entries:
        a = entry['address']
        for street in dict.fromkeys(N.name_key(name) for name in [a['street'], *entry.get('aliases', [])]):
            index[address_key(a['house'], street, a['unit'], a['city'], a['state'])].append(entry)
    return index


def project(location, span, budget):
    """Separate route projection, with position scaled exactly as walk_route.Span."""
    span_id, name, length, raw = span
    if not isinstance(raw, str) or len(raw) > MAX_VERTICES*70:
        raise ValueError('Evidence road geometry size limit')
    pts = json.loads(raw or '[]')
    if not 2 <= len(pts) <= MAX_VERTICES or any(len(p) != 2 or not point(*p) for p in pts):
        return None
    k = math.cos(math.radians(pts[0][1]))
    cumulative = [0.0]
    for a, b in zip(pts, pts[1:]):
        cumulative.append(cumulative[-1] + math.hypot((b[0]-a[0])*k, b[1]-a[1]))
    total = cumulative[-1]
    if not total or not math.isfinite(length) or length <= 0:
        return None
    best = None
    for i, (a, b) in enumerate(zip(pts, pts[1:])):
        budget[0] += 1
        if budget[0] > MAX_CHECKS:
            raise ValueError('Evidence geometry budget exceeded')
        dx, dy = (b[0]-a[0])*k, b[1]-a[1]
        denominator = dx*dx+dy*dy
        t = max(0, min(1, (((location[0]-a[0])*k)*dx+(location[1]-a[1])*dy)/denominator)) if denominator else 0
        xy = (a[0]+t*(b[0]-a[0]), a[1]+t*(b[1]-a[1]))
        distance = metres(location, xy)
        value = {'lon': xy[0], 'lat': xy[1], 'span_id': span_id,
                 'pos_m': (cumulative[i]+t*(cumulative[i+1]-cumulative[i]))/total*length,
                 'distance_m': round(distance, 2), 'road_name': name}
        if best is None or distance < best[0]:
            best = distance, value
    return best


def repair_locations(store, rows, omissions, district, state, *, evidence=None):
    """Return copied rows, unresolved omissions, parcel corrections and loader warnings.

    Every source person remains present. Evidence is never an anchor for another
    correction, and existing usable geocodes are not candidates for replacement.
    """
    confirmed, missing, warnings = evidence if evidence is not None else load_evidence(district)
    if not omissions or not (confirmed or missing):
        return rows, omissions, [], warnings
    directory = store.directory().by_id
    indexes = evidence_index(confirmed), evidence_index(missing)
    people = {row['person_id']: row for row in rows}
    anchors = {}
    for row in rows:
        if row['placed_by'] in ('geocode', 'fuzzy') and point(row['lon'], row['lat']):
            street = directory[row['street_id']]
            anchors.setdefault((street.key, text(street.city)), {})[row['stop_id']] = (row['lon'], row['lat'])
    span_cache, corrections, remaining, replacements, budget = {}, [], [], {}, [0]
    try:
        for omission in omissions:
            group = [people[p] for p in omission['person_ids']]
            first = group[0]
            street = directory[first['street_id']]
            key = address_key(first['house'], street.key, first['unit'], street.city, state)
            # Postal ZIP is retained from the voter file; unknown evidence ZIP makes no claim about it.
            eligible = lambda entry: entry['address']['zip'] is None or all(str(r['zip'] or '')[:5] == entry['address']['zip'] for r in group)
            candidates = [e for e in indexes[0].get(key, []) if eligible(e)]
            found = []
            for entry in candidates:
                location = (entry['location']['lon'], entry['location']['lat'])
                nearby = []
                for name in entry['context']['street_names']:
                    road_key = N.name_key(name)
                    if road_key == street.key:
                        continue
                    for xy in anchors.get((road_key, text(street.city)), {}).values():
                        budget[0] += 1
                        if budget[0] > MAX_CHECKS:
                            raise ValueError('Evidence anchor budget exceeded')
                        distance = metres(location, xy)
                        if distance <= ANCHOR_METRES:
                            nearby.append({'street': name, 'distance_m': round(distance, 1)})
                if not nearby:
                    continue
                road_keys = tuple(sorted({street.key, N.name_key(entry['address']['street']), *(N.name_key(n) for n in entry.get('aliases', []))}))
                if road_keys not in span_cache:
                    spans = store.conn.execute('SELECT id,name_key,len_m,coords FROM road_span WHERE virtual=0 AND name_key IN (%s) ORDER BY id LIMIT ?'
                                               % ','.join('?'*len(road_keys)), [*road_keys, MAX_SPANS+1]).fetchall()
                    if len(spans) > MAX_SPANS:
                        raise ValueError('Evidence road limit exceeded')
                    span_cache[road_keys] = spans
                choices = [value for span in span_cache[road_keys] if (value := project(location, span, budget)) is not None]
                choices.sort(key=lambda value: (value[0], value[1]['span_id']))
                if choices and choices[0][0] <= ROAD_METRES:
                    found.append((entry, choices[0][1], min(nearby, key=lambda a: (a['distance_m'], a['street']))))
            if len(found) == 1:
                entry, projection, anchor = found[0]
                audit = {**omission, 'reason': PARCEL_WARNING, 'codes': [*omission.get('codes', []), 'parcel_representative_point'],
                         'location': entry['location'],
                         'route_projection': projection, 'source': entry['source'], 'address_match': entry['address'],
                         'context': {**entry['context'], 'anchor': anchor}, 'bundle_sha256': entry.get('bundle_sha256', '')}
                corrections.append(audit)
                for row in group:
                    replacements[row['person_id']] = {**row, 'lon': entry['location']['lon'], 'lat': entry['location']['lat'],
                        'span_id': projection['span_id'], 'pos_m': projection['pos_m'], 'side_lr': None, 'placed_by': 'parcel'}
            else:
                notices = [e for e in indexes[1].get(key, []) if eligible(e)]
                item = dict(omission)
                if notices:
                    item['reason'] += ' ' + ' '.join(dict.fromkeys(e['reason'] for e in notices))
                    item['reviewed_evidence'] = notices
                    # A same-number parcel on another named street is recorded, never used to rename the source address.
                    elsewhere = any(isinstance(e.get('alternate_street_check'), dict) and e['alternate_street_check'].get('matching_address')
                                    for e in notices)
                    item['codes'] = [*omission.get('codes', []), 'no_confirming_parcel', *(['conflicting_parcel_elsewhere'] if elsewhere else [])]
                elif candidates:
                    item['reason'] += ' Reviewed parcel evidence lacks a unique supported match with an included reliable neighboring street and nearby same-named road.'
                    item['codes'] = [*omission.get('codes', []), 'no_unique_parcel_match']
                remaining.append(item)
    except (ValueError, TypeError, KeyError):
        return rows, omissions, [], [*warnings, 'Reviewed location evidence reached its geometry validation limit; no corrections applied.']
    return [replacements.get(row['person_id'], row) for row in rows], remaining, corrections, warnings
