"""Bounded local road context for aligning an imported map image.

Coordinates are evidence for a user-reviewed overlay, never proof that an OCR
label identifies a door. Road-only names remain separate from registered streets.
"""
import json
import math
import re
from collections import defaultdict

from django.conf import settings

from . import walk_address_views as A
from . import walk_image_alignment as G
from . import walk_names as N
from . import walk_store as W
from . import walk_views as V
from .street_views import response
from .v3_http import BadRequest, params

MAX_STREET_IDS, MAX_NAMES, MAX_CONTEXT_STREETS = 200, 100, 1000
MAX_ROADS, MAX_VERTICES, MAX_STOP_POINTS = 4000, 50000, 10000
MAX_BOUND_METRES = 50000


def coordinate(value):
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and all(type(n) in (float, int) and math.isfinite(n) for n in value)
            and -180 <= value[0] <= 180 and -90 < value[1] < 90)


def context_inputs(body):
    stamp = body.get('snapshot')
    if not isinstance(stamp, str) or not re.fullmatch(r'[a-f0-9]{64}', stamp):
        raise BadRequest('Invalid snapshot')
    ids, names = body.get('street_ids', []), body.get('street_names', [])
    if not isinstance(ids, list) or len(ids) > MAX_STREET_IDS:
        raise V.Refused('Use at most %d selected streets for an image overlay.' % MAX_STREET_IDS)
    if any(type(i) is not int or not 1 <= i <= 999999999 for i in ids):
        raise BadRequest('Invalid street id')
    if not isinstance(names, list) or len(names) > MAX_NAMES:
        raise V.Refused('Use at most %d image street labels per request.' % MAX_NAMES)
    normalized = {}
    for name in names:
        row = A.input_row({'street': name})
        if re.match(r'^\d+(?!\d)(?!st\b|nd\b|rd\b|th\b)(?:[A-Za-z]?\b|[-/])', row['street'], re.I):
            raise BadRequest('Image street labels must not contain house numbers')
        normalized.setdefault(N.name_key(row['street']), row['street'])
    if not ids and not normalized:
        raise BadRequest('Select streets or provide image street labels')
    bounds = body.get('bounds')
    if bounds is not None:
        if (not isinstance(bounds, list) or len(bounds) != 4
                or not coordinate(bounds[:2]) or not coordinate(bounds[2:])
                or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]):
            raise BadRequest('Invalid image bounds')
        west, south, east, north = bounds
        if (max(A.distance((west, south), (east, south)), A.distance((west, north), (east, north))) > MAX_BOUND_METRES
                or A.distance((west, south), (west, north)) > MAX_BOUND_METRES):
            raise BadRequest('Image bounds must be no wider or taller than 50 km')
    return stamp, sorted(set(ids)), normalized, bounds


def inside(point, bounds):
    return bounds is None or bounds[0] <= point[0] <= bounds[2] and bounds[1] <= point[1] <= bounds[3]


def intersects(line, bounds):
    """Rectangle intersection includes segments crossing with both endpoints outside."""
    if bounds is None:
        return True
    west, south, east, north = bounds
    for (x, y), (xx, yy) in zip(line, line[1:]):
        lo, hi = 0., 1.
        for p, q in ((x - xx, x - west), (xx - x, east - x), (y - yy, y - south), (yy - y, north - y)):
            if p == 0:
                if q < 0:
                    break
            elif p < 0:
                lo = max(lo, q / p)
            else:
                hi = min(hi, q / p)
            if lo > hi:
                break
        else:
            return True
    return False


def context(store, selected, names, viewport=None):
    directory = store.directory()
    if any(i not in directory.by_id for i in selected):
        raise V.Refused('A selected street is not in this district. Resolve the import again.', 409)
    names = dict(names)
    for i in selected:
        street = directory.by_id[i]
        names.setdefault(street.key, street.display)
    ids = set(selected)
    ids.update(s.id for key in names for s in directory.by_key.get(key, ()))
    if len(ids) > MAX_CONTEXT_STREETS:
        raise V.Refused('Too many street locations match these labels. Align fewer image labels at a time.')

    # The same name may occur in several towns. Return every real road with that
    # exact normalized name, preserving alternatives instead of picking a town.
    road_rows = []
    for chunk in W._chunks(sorted(names)):
        road_rows.extend(store.conn.execute(
            'SELECT id,name_key,a,b,coords FROM road_span WHERE virtual=0 AND name_key IN (%s) '
            'ORDER BY id LIMIT ?' % ','.join('?' * len(chunk)),
            [*chunk, MAX_ROADS + 1 - len(road_rows)]).fetchall())
        if len(road_rows) > MAX_ROADS:
            raise V.Refused('The image overlay has too many road segments. Align fewer labels at a time.')

    points, linked, point_count = defaultdict(set), defaultdict(set), 0
    for chunk in W._chunks(sorted(ids)):
        rows = store.conn.execute(
            'SELECT DISTINCT street_id,span_id,lon,lat FROM stop WHERE street_id IN (%s) '
            "AND placed_by IN ('geocode','fuzzy') AND lon IS NOT NULL AND lat IS NOT NULL "
            'ORDER BY street_id,span_id,lon,lat LIMIT ?' % ','.join('?' * len(chunk)),
            [*chunk, MAX_STOP_POINTS + 1 - point_count]).fetchall()
        point_count += len(rows)
        if point_count > MAX_STOP_POINTS:
            raise V.Refused('The image overlay has too many recorded locations. Align fewer labels at a time.')
        for street, span, lon, lat in rows:
            if coordinate((lon, lat)) and (lon or lat) and inside((lon, lat), viewport):
                points[street].add((lon, lat))
                if span is not None:
                    linked[span].add(street)

    features, junctions, used_names, vertices = [], {}, set(), 0
    bounds = [180., 90., -180., -90.]
    for span, key, a, b, raw in road_rows:
        try:
            line = json.loads(raw)
        except (TypeError, ValueError):
            raise V.Refused('The local road geometry is invalid. Rebuild the district walk data.', 409)
        if not isinstance(line, list) or len(line) < 2 or not all(coordinate(p) for p in line):
            raise V.Refused('The local road geometry is invalid. Rebuild the district walk data.', 409)
        if not intersects(line, viewport):
            continue
        vertices += len(line)
        if vertices > MAX_VERTICES:
            raise V.Refused('The image overlay has too many road coordinates. Align fewer labels at a time.')
        used_names.add(key)
        for lon, lat in line:
            bounds = [min(bounds[0], lon), min(bounds[1], lat), max(bounds[2], lon), max(bounds[3], lat)]
        features.append({'type': 'Feature', 'id': span,
                         'geometry': {'type': 'LineString', 'coordinates': line},
                         'properties': {'name': names[key], 'name_key': key,
                                        'street_ids': sorted(linked[span]), 'source': 'local-road-network'}})
        for node, point in ((a, line[0]), (b, line[-1])):
            if not inside(point, viewport):
                continue
            value = junctions.setdefault(node, {'id': node, 'lon': point[0], 'lat': point[1], 'labels': set()})
            value['labels'].add(names[key])
    streets = []
    for i in sorted(ids):
        if viewport is not None and not points[i]:
            continue
        s = directory.by_id[i]
        streets.append({'id': i, 'label': s.display, 'city': N.display_city(s.city),
                        'state': settings.DISTRICT['state'], 'zip': s.zip,
                        'reliable_points': [list(p) for p in sorted(points[i])]})
    return {'roads': {'type': 'FeatureCollection', 'features': features},
            'junctions': [{**j, 'labels': sorted(j['labels'])} for _, j in sorted(junctions.items())],
            'streets': streets, 'bounds': viewport if viewport is not None else bounds if features else None,
            'unmatched_names': [names[k] for k in sorted(names) if k not in used_names],
            'road_only_names': [names[k] for k in sorted(used_names) if not directory.by_key.get(k)]}


@V.manager_write
def image_context(request):
    params(request, set())
    body = V.json_body(request, {'snapshot', 'street_ids', 'street_names', 'bounds'})
    stamp, ids, names, bounds = context_inputs(body)
    with W.walk_store() as store:
        if stamp != A.snapshot(store.info):
            raise V.Refused('The district data changed. Resolve the import again.', 409)
        result = context(store, ids, names, bounds)
    if stamp != A.snapshot(W.verify()):
        raise V.Refused('The district data changed. Resolve the import again.', 409)
    return response({'district': settings.DISTRICT['id'], 'snapshot': stamp, **result})


def resolve_inputs(body):
    stamp, ids, _, _ = context_inputs({'snapshot': body.get('snapshot'), 'street_ids': body.get('street_ids')})
    if not ids or len(body['street_ids']) > 40:
        raise BadRequest('Use between 1 and 40 trusted anchor streets')
    image, labels = body.get('image'), body.get('labels')
    if not isinstance(image, dict) or set(image) != {'width', 'height'}:
        raise BadRequest('Invalid image dimensions')
    if any(type(n) not in (int, float) or not math.isfinite(n) or not 16 <= n <= 16000 for n in image.values()):
        raise BadRequest('Invalid image dimensions')
    if not isinstance(labels, list) or not 1 <= len(labels) <= 80:
        raise BadRequest('Use between 1 and 80 readable image road labels')
    out = []
    for index, label in enumerate(labels):
        if not isinstance(label, dict) or set(label) != {'text', 'x', 'y', 'width', 'height', 'confidence'}:
            raise BadRequest('Invalid image label')
        text = A.input_row({'street': label['text']})['street']
        x, y, width, height, confidence = (label[k] for k in ('x', 'y', 'width', 'height', 'confidence'))
        if any(type(n) not in (int, float) or not math.isfinite(n) for n in (x, y, width, height, confidence)):
            raise BadRequest('Invalid image label coordinates')
        if (x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > image['width'] + 1
                or y + height > image['height'] + 1 or not 0 <= confidence <= 100):
            raise BadRequest('Image labels must remain inside the image')
        out.append({'row': index + 1, 'text': text, 'key': N.name_key(text),
                    'point': (x + width / 2, y + height / 2), 'confidence': confidence / 100})
    requests = body.get('requests', [])
    if not isinstance(requests, list) or len(requests) > 100:
        raise BadRequest('Use at most 100 surrounding street requests')
    return stamp, ids, image, out, [A.input_row(row) for row in requests]


def footprint_distance(line, fit, image):
    """Ground distance from a road polyline to the estimated, rotated image rectangle."""
    aa, bb, cc = fit['x']
    ff = fit['y'][2]
    den = aa * aa + bb * bb
    pixel_line = []
    for point in line:
        x, y = G.mercator(point)
        pixel_line.append(((aa * (x - cc) + bb * (y - ff)) / den,
                           (bb * (x - cc) - aa * (y - ff)) / den))
    width, height = image['width'], image['height']
    if intersects(pixel_line, [0, 0, width, height]):
        return 0.
    best = min(math.hypot(x - max(0., min(width, x)), y - max(0., min(height, y))) for x, y in pixel_line)
    for a, b in zip(pixel_line, pixel_line[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = dx * dx + dy * dy
        for x, y in ((0, 0), (width, 0), (0, height), (width, height)):
            t = max(0., min(1., ((x - a[0]) * dx + (y - a[1]) * dy) / length)) if length else 0.
            best = min(best, math.hypot(x - a[0] - t * dx, y - a[1] - t * dy))
    return best * math.sqrt(den) * math.cos(math.radians(line[0][1]))


def surrounding_resolutions(store, requests, roads, fit, image):
    directory = store.directory()
    by_key, touching, neighbors = defaultdict(list), defaultdict(list), defaultdict(set)
    road_by_id = {r['id']: r for r in roads}
    for road in roads:
        key, line = road['properties']['name_key'], road['geometry']['coordinates']
        by_key[key].append(road['id'])
        for point in (line[0], line[-1]):
            touching[key, tuple(round(v, 6) for v in point)].append(road['id'])
    for spans in touching.values():
        for span in spans:
            neighbors[span].update(spans)
    associations = defaultdict(dict)
    for chunk in W._chunks(sorted(road_by_id)):
        for span, street, reliable, total in store.conn.execute(
                'SELECT s.span_id,s.street_id,SUM(s.placed_by IN (\'geocode\',\'fuzzy\')),COUNT(*) '
                'FROM stop s JOIN street t ON t.id=s.street_id JOIN road_span r ON r.id=s.span_id '
                'WHERE s.span_id IN (%s) AND t.key=r.name_key GROUP BY s.span_id,s.street_id'
                % ','.join('?' * len(chunk)), chunk):
            associations[span][street] = reliable, total
    components = defaultdict(list)
    for key, spans in by_key.items():
        unseen = set(spans)
        while unseen:
            queue, group, proof = [min(unseen)], set(), defaultdict(lambda: [0, 0])
            while queue:
                span = queue.pop()
                if span in group:
                    continue
                group.add(span)
                unseen.discard(span)
                for street, (reliable, total) in associations[span].items():
                    proof[street][0] += reliable
                    proof[street][1] += total
                queue.extend(neighbors[span] - group)
            distance = min(footprint_distance(road_by_id[span]['geometry']['coordinates'], fit, image) for span in group)
            components[key].append((distance, group, proof))
    by_zip = A.zip_counts(store, {r['zip'] for r in requests if r['zip']})
    out = []
    for index, row in enumerate(requests, 1):
        key = N.name_key(row['street'])
        item = {'row': index, 'request_row': index, 'origin': 'request', 'text': row['street'],
                'method': 'image-surroundings', 'status': 'review'}
        allowed = {s.id for s in directory.by_key.get(key, ())
                   if (not row['city'] or V.city_key(s.city) == V.city_key(row['city']))
                   and (not row['zip'] or (s.id, row['zip']) in by_zip)}
        valid_state = not row['state'] or row['state'].casefold() in (settings.DISTRICT['state'].casefold(), settings.DISTRICT['state_name'].casefold())
        ranked = sorted(components.get(key, ()), key=lambda c: c[0])
        if not valid_state or (not allowed and (row['city'] or row['zip'])):
            item['reason'] = 'The supplied locality has no matching registered street in this district.'
        elif not ranked or ranked[0][0] > 1000:
            item['reason'] = 'No same-name road is within 1 km of the fitted image footprint.'
        else:
            distance, group, proof = ranked[0]
            choices = allowed & set(proof)
            strong = {s for s in choices if proof[s][0] or proof[s][1] >= 2}
            accounted = set().union(*(set(p) for _, _, p in ranked))
            rivals = [d for d, _, p in ranked[1:] if not choices or (allowed & set(p)) != choices]
            item.update(distance_m=round(distance), road_ids=sorted(group))
            if allowed - accounted:
                item['reason'] = 'A registered alternative has no same-name road association, so it cannot be ruled out.'
            elif rivals and (rivals[0] - distance < 500 or rivals[0] < 3 * distance):
                item['reason'] = 'Multiple same-name roads are too close to the fitted image footprint to distinguish.'
            elif len(choices) == 1 and strong == choices:
                street = directory.by_id[next(iter(choices))]
                item.update(status='matched', street_id=street.id, city=N.display_city(street.city),
                            reason='This recorded street has the clearly nearest same-name road to the fitted image footprint.')
            elif not choices:
                item.update(status='road_only', road_only=True,
                            reason='The nearby road is visible in the local map, but no recorded street doors are associated with it.')
            else:
                item['reason'] = 'The nearby road does not uniquely establish a recorded street locality.'
        out.append(item)
    return out


def image_resolutions(store, labels, matched, roads, trusted):
    """Associate a fitted named road component with recorded streets of that name.

    A same-name stored road association can narrow an interpolated street, but
    never makes its estimated house coordinates into geocoded anchor evidence.
    """
    directory = store.directory()
    by_id = {r['id']: r for r in roads}
    joins, neighbors = defaultdict(list), defaultdict(set)
    for road in roads:
        key, line = road['properties']['name_key'], road['geometry']['coordinates']
        for point in (line[0], line[-1]):
            joins[key, tuple(round(v, 6) for v in point)].append(road['id'])
    for linked in joins.values():
        for span in linked:
            neighbors[span].update(linked)
    associations, accounted = defaultdict(set), defaultdict(set)
    for chunk in W._chunks(sorted(by_id)):
        rows = store.conn.execute(
            'SELECT s.span_id,s.street_id,SUM(s.placed_by IN (\'geocode\',\'fuzzy\')),COUNT(*) '
            'FROM stop s JOIN street t ON t.id=s.street_id JOIN road_span r ON r.id=s.span_id '
            'WHERE s.span_id IN (%s) AND t.key=r.name_key GROUP BY s.span_id,s.street_id'
            % ','.join('?' * len(chunk)), chunk)
        for span, street, reliable, total in rows:
            accounted[directory.by_id[street].key].add(street)
            if reliable or total >= 2:
                associations[span].add(street)
    matched_rows = {l['row']: (l, d, q, span) for l, d, q, span in matched}
    result = []
    for label in labels:
        item = {'row': label['row'], 'origin': 'label', 'method': 'image-layout', 'text': label['text'], 'status': 'review',
                'reason': 'This label did not have enough geographic layout support.'}
        if label['row'] not in matched_rows:
            result.append(item)
            continue
        _, distance, location, span = matched_rows[label['row']]
        choices = {i for i in trusted if directory.by_id[i].key == label['key']} if span == -1 else set()
        component, queue = set(), [span] if span in by_id else []
        while queue:
            current = queue.pop()
            if current in component:
                continue
            component.add(current)
            choices.update(associations[current])
            queue.extend(neighbors[current] - component)
        item.update(distance_m=round(distance * math.cos(math.radians(G.geographic(location)[1]))), road_ids=sorted(component))
        alternatives = {s.id for s in directory.by_key.get(label['key'], ())}
        if span != -1 and alternatives - accounted[label['key']]:
            item['reason'] = 'A registered alternative has no same-name road association, so it cannot be ruled out.'
        elif len(choices) == 1:
            street = directory.by_id[next(iter(choices))]
            item.update(status='matched', street_id=street.id, city=N.display_city(street.city),
                        reason='The image layout fits this recorded street and its surrounding named roads.')
        elif not directory.by_key.get(label['key']):
            item.update(status='road_only', road_only=True,
                        reason='This road fits the image layout, but no registered doors are recorded under this street name.')
        elif len(choices) > 1:
            item['reason'] = 'The fitted road crosses multiple recorded street localities. Choose the intended locality.'
        else:
            item['reason'] = 'The fitted road has no trustworthy recorded street association. Review the locality.'
        result.append(item)
    return result


@V.manager_write
def image_resolve(request):
    params(request, set())
    body = V.json_body(request, {'snapshot', 'street_ids', 'image', 'labels', 'requests'})
    stamp, ids, image, labels, requests = resolve_inputs(body)
    with W.walk_store() as store:
        if stamp != A.snapshot(store.info):
            raise V.Refused('The district data changed. Resolve the import again.', 409)
        names = {N.name_key(r['street']): r['street'] for r in requests}
        names.update({l['key']: l['text'] for l in labels})
        data = context(store, ids, names)
        fitted = G.fit_layout(labels, data['roads']['features'], ids, image['width'], image['height'])
        matched = fitted.pop('matched', [])
        alternatives = fitted.pop('alternatives', [])
        alternative_fits = fitted.pop('alternative_fits', [])
        resolutions = image_resolutions(store, labels, matched, data['roads']['features'], ids) if matched else []
        for alternative in alternatives:
            rival = {r['row']: r for r in image_resolutions(store, labels, alternative, data['roads']['features'], ids)}
            for item in resolutions:
                other = rival[item['row']]
                if item.get('street_id') and other.get('street_id') != item['street_id']:
                    item.pop('street_id', None)
                    item.pop('city', None)
                    item.update(status='review', reason='Other similarly fitting image layouts do not confirm this street locality.')
        if matched and requests:
            surroundings = surrounding_resolutions(store, requests, data['roads']['features'], fitted['fit'], image)
            for aa, bb, cc, ff in alternative_fits:
                rival_fit = {'x': [aa, bb, cc], 'y': [bb, -aa, ff]}
                rivals = surrounding_resolutions(store, requests, data['roads']['features'], rival_fit, image)
                for item, rival in zip(surroundings, rivals):
                    if item.get('street_id') and rival.get('street_id') != item['street_id']:
                        item.pop('street_id', None)
                        item.pop('city', None)
                        item.update(status='review', reason='Other similarly fitting image footprints do not confirm this street locality.')
            resolutions.extend(surroundings)
    if stamp != A.snapshot(W.verify()):
        raise V.Refused('The district data changed. Resolve the import again.', 409)
    return response({'district': settings.DISTRICT['id'], 'snapshot': stamp, 'method': 'image-layout',
                     'bounds': None, **fitted, 'resolutions': resolutions})
