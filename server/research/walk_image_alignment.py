"""Bounded similarity fitting of map-label layout to actual named road lines.

Label centres need only be near their road: they are not surveyed control points.
Returned fits are estimates and never certify image georeferencing.
"""
import math

MAX_HYPOTHESES, MAX_CHECKS = 2000, 3000000
R = 6378137.


class FitLimit(Exception):
    pass


def mercator(point):
    lon, lat = point
    return R * math.radians(lon), R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def geographic(point):
    return math.degrees(point[0] / R), math.degrees(2 * math.atan(math.exp(point[1] / R)) - math.pi / 2)


def apply(fit, point):
    x, y = point
    return fit[0] * x + fit[1] * y + fit[2], fit[1] * x - fit[0] * y + fit[3]


def pair_fit(p, q, a, b):
    dx, dy, gx, gy = q[0] - p[0], q[1] - p[1], b[0] - a[0], b[1] - a[1]
    denom = dx * dx + dy * dy
    if denom < 1:
        return None
    aa, bb = (gx * dx - gy * dy) / denom, (gy * dx + gx * dy) / denom
    return aa, bb, a[0] - aa * p[0] - bb * p[1], a[1] - bb * p[0] + aa * p[1]


def refine(pairs):
    n = len(pairs)
    px, py, qx, qy = (sum(p[i] for p in pairs) / n for i in range(4))
    denom = sum((x - px) ** 2 + (y - py) ** 2 for x, y, _, _ in pairs)
    if denom < 1:
        return None
    aa = sum((x - px) * (xx - qx) - (y - py) * (yy - qy) for x, y, xx, yy in pairs) / denom
    bb = sum((y - py) * (xx - qx) + (x - px) * (yy - qy) for x, y, xx, yy in pairs) / denom
    return aa, bb, qx - aa * px - bb * py, qy - bb * px + aa * py


def samples(lines, count=7):
    values = sorted(set(p for line in lines for p in line))
    if len(values) <= count:
        return values
    return [values[round(i * (len(values) - 1) / (count - 1))] for i in range(count)]


def fit_layout(labels, roads, anchor_ids, width, height):
    """Return a consistent estimated pose, or review; roads are context GeoJSON features."""
    by_key, anchor_lines, linked_ids, touching, anchor_seeds = {}, {}, set(), {}, {}
    for road in roads:
        prop = road['properties']
        line = [mercator(p) for p in road['geometry']['coordinates']]
        by_key.setdefault(prop['name_key'], []).append((road['id'], line))
        for point in (road['geometry']['coordinates'][0], road['geometry']['coordinates'][-1]):
            touching.setdefault((prop['name_key'], tuple(round(v, 6) for v in point)), []).append(road['id'])
        if set(prop['street_ids']) & set(anchor_ids):
            linked_ids.add(road['id'])
            for anchor_id in set(prop['street_ids']) & set(anchor_ids):
                anchor_seeds.setdefault(anchor_id, set()).add(road['id'])
    # A reliable door anchors the connected named road, including its blocks
    # without registered doors; a map label can sit on any of those blocks.
    neighbors = {}
    for group in touching.values():
        for road_id in group:
            neighbors.setdefault(road_id, set()).update(group)
    pending = list(linked_ids)
    while pending:
        current = pending.pop()
        for road_id in neighbors.get(current, ()) - linked_ids:
            linked_ids.add(road_id)
            pending.append(road_id)
    for key, lines in by_key.items():
        chosen = [line for road_id, line in lines if road_id in linked_ids]
        if chosen:
            anchor_lines[key] = chosen
    anchor_components = []
    for seed in anchor_seeds.values():
        connected, pending = set(seed), list(seed)
        while pending:
            for road_id in neighbors.get(pending.pop(), ()) - connected:
                connected.add(road_id)
                pending.append(road_id)
        for key, lines in by_key.items():
            chosen = [(road_id, line) for road_id, line in lines if road_id in connected]
            if chosen:
                anchor_components.append((key, chosen))
    usable = [l for l in labels if l['key'] in by_key and l['confidence'] >= .45]
    anchors = [l for l in usable if l['key'] in anchor_lines]
    review = lambda why: {'status': 'review', 'reason': why}
    if not anchors:
        return review('No readable image label matches a trusted street with reliable local road geometry.')
    if len({l['key'] for l in usable}) < 4:
        return review('At least four distinct readable road labels are needed to check the surrounding layout.')
    known = [p for label in anchors for line in anchor_lines[label['key']] for p in line]
    west, south = min(p[0] for p in known) - 5000, min(p[1] for p in known) - 5000
    east, north = max(p[0] for p in known) + 5000, max(p[1] for p in known) + 5000
    by_key = {key: [(i, line) for i, line in lines
                    if max(p[0] for p in line) >= west and min(p[0] for p in line) <= east
                    and max(p[1] for p in line) >= south and min(p[1] for p in line) <= north]
              for key, lines in by_key.items()}
    diag = math.hypot(width, height)
    required_anchors = {l['key'] for l in anchors}
    checks = 0

    def nearest(point, lines):
        nonlocal checks
        best = (float('inf'), None, None)
        for road_id, line in lines:
            for a, b in zip(line, line[1:]):
                checks += 1
                if checks > MAX_CHECKS:
                    raise FitLimit()
                dx, dy = b[0] - a[0], b[1] - a[1]
                den = dx * dx + dy * dy
                t = max(0., min(1., ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / den)) if den else 0.
                q = a[0] + t * dx, a[1] + t * dy
                d = math.hypot(point[0] - q[0], point[1] - q[1])
                if d < best[0]:
                    best = d, q, road_id
        return best

    def score(fit):
        scale = math.hypot(fit[0], fit[1])
        if not 150 <= scale * diag <= 12000:
            return None
        tolerance = min(90., max(25., .035 * scale * diag))
        matched = []
        for label in usable:
            lines = [(-1, line) for line in anchor_lines[label['key']]] if label['key'] in anchor_lines else by_key[label['key']]
            d, q, road_id = nearest(apply(fit, label['point']), lines)
            if d <= tolerance:
                matched.append((label, d, q, road_id))
        distinct = len({l['key'] for l, _, _, _ in matched})
        if distinct < 4 or not required_anchors <= {l['key'] for l, _, _, _ in matched}:
            return None
        # Distinct trusted streets with the same name remain separate evidence.
        # A matching box near one town cannot silently satisfy the other town.
        for key, lines in anchor_components:
            relevant = [l for l in usable if l['key'] == key]
            if relevant and not any(nearest(apply(fit, l['point']), lines)[0] <= tolerance for l in relevant):
                return None
        xs, ys = [l['point'][0] for l, _, _, _ in matched], [l['point'][1] for l, _, _, _ in matched]
        coverage = (max(xs) - min(xs)) * (max(ys) - min(ys)) / (width * height)
        if coverage < .06 or (max(xs) - min(xs)) < width * .15 or (max(ys) - min(ys)) < height * .15:
            return None
        rms = math.sqrt(sum(d * d for _, d, _, _ in matched) / len(matched))
        return {'fit': fit, 'matched': matched, 'distinct': distinct, 'coverage': coverage, 'rms': rms,
                'score': (distinct, len(matched), -rms)}

    # One trusted road plus a second distinct label proposes scale and rotation.
    # Candidate points sample the real polylines, never town or street centroids.
    candidates, tested, seen = [], 0, set()
    try:
        for anchor in anchors[:4]:
            others = sorted((l for l in usable if l['key'] != anchor['key']),
                            key=lambda l: -math.dist(l['point'], anchor['point']))[:8]
            for q in samples(anchor_lines[anchor['key']], 7):
                for other in others:
                    if math.dist(anchor['point'], other['point']) < diag * .15:
                        continue
                    nearby = [line for _, line in by_key[other['key']]
                              if any(math.dist(q, p) <= 5000 for p in line)]
                    for qq in samples(nearby, 7):
                        fit = pair_fit(anchor['point'], other['point'], q, qq)
                        token = tuple(round(v, 2) for v in fit)
                        if token in seen:
                            continue
                        seen.add(token)
                        tested += 1
                        if tested > MAX_HYPOTHESES:
                            break
                        value = score(fit)
                        if value:
                            candidates.append(value)
                    if tested > MAX_HYPOTHESES:
                        break
                if tested > MAX_HYPOTHESES:
                    break
            if tested > MAX_HYPOTHESES:
                break
        if not candidates:
            return review('The readable labels do not establish a consistent surrounding road layout.')
        if tested > MAX_HYPOTHESES:
            return review('The bounded image layout search is incomplete. Use fewer labels or align this image manually.')
        candidates.sort(key=lambda c: c['score'], reverse=True)
        # Keep geographically different contenders even when many equivalent
        # seeds describe the best neighborhood. A top-N score cut alone can hide
        # an equally convincing second neighborhood.
        clusters = []
        for item in candidates:
            centre = apply(item['fit'], (width / 2, height / 2))
            if not any(math.dist(centre, c) <= 400 for c, _ in clusters):
                clusters.append((centre, item))
        if len(clusters) > 8:
            return review('Too many geographic layouts fit the image. Add a trusted street or align it manually.')
        shortlist = [item for _, item in clusters]
        shortlist.extend(item for item in candidates[:4] if not any(item is old for old in shortlist))
        refined = []
        for item in shortlist:
            for _ in range(6):
                pairs = [(*l['point'], *q) for l, _, q, _ in item['matched']]
                fit = refine(pairs)
                value = score(fit) if fit else None
                if not value or value['score'] < item['score']:
                    break
                item = value
            refined.append(item)
        refined.sort(key=lambda c: c['score'], reverse=True)
        best = refined[0]
        if best['distinct'] < max(4, math.ceil(len({l['key'] for l in usable}) * .65)):
            return review('Too few of the readable road names agree with one surrounding layout.')
        if best['rms'] > 55:
            return review('The image labels are too far from their expected roads for a reliable layout match.')
        centre = apply(best['fit'], (width / 2, height / 2))
        # Slightly different poses over the same neighborhood are still an estimate;
        # a similarly supported, geographically different layout remains ambiguous.
        for rival in refined[1:]:
            if (rival['distinct'] >= best['distinct'] - 1 and rival['rms'] <= best['rms'] + 20
                    and math.dist(centre, apply(rival['fit'], (width / 2, height / 2))) > 500):
                return review('More than one geographic layout fits the readable image labels.')
        aa, bb, cc, ff = best['fit']
        ground_errors = [d * math.cos(math.radians(geographic(q)[1])) for _, d, q, _ in best['matched']]
        fit = {'x': [aa, bb, cc], 'y': [bb, -aa, ff], 'projection': 'EPSG:3857',
               'rmsMeters': round(math.sqrt(sum(d*d for d in ground_errors) / len(ground_errors)), 2),
               'maxErrorMeters': round(max(ground_errors), 2),
               'coverage': round(best['coverage'], 4), 'verified': False,
               'reason': 'Estimated from readable road labels around trusted uploaded streets; review the overlay.'}
        corners = [geographic(apply(best['fit'], p)) for p in ((0, 0), (width, 0), (0, height), (width, height))]
        bounds = [min(p[0] for p in corners), min(p[1] for p in corners), max(p[0] for p in corners), max(p[1] for p in corners)]
        return {'status': 'matched', 'reason': fit['reason'], 'fit': fit, 'bounds': bounds,
                'matched': best['matched'], 'distinct_labels': best['distinct'], 'checks': checks,
                'alternatives': [r['matched'] for r in refined[1:]
                                 if r['distinct'] >= best['distinct'] and r['rms'] <= best['rms'] + 10],
                'alternative_fits': [r['fit'] for r in refined[1:]
                                     if r['distinct'] >= best['distinct'] and r['rms'] <= best['rms'] + 10],
                'hypotheses': min(tested, MAX_HYPOTHESES)}
    except FitLimit:
        return review('The bounded image layout check reached its limit. Use fewer clear labels or align this image manually.')
