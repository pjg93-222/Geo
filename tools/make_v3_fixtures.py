"""Build the v3 frontend fixtures: exact-shape stand-ins for the /api/v3 aggregate endpoints.

Run with the shared work venv (shapely + numpy), never with system Python:

    work\\.venv\\Scripts\\python.exe tools\\make_v3_fixtures.py

Input  : the active snapshot's geometry.json (2020 block and ZCTA polygons, aggregate geometry only).
Output : frontend/public/fixtures/v3/{meta.json, geometry/<level>.<coarse|fine>.json, areas/<level>.json}

The numbers are SYNTHETIC (a smooth spatial field plus noise). They exist only so that the frontend can be built and
inspected before the real rowfw area table is served; they are not scores and never leave this copy.
No person-level data is read or written. Tract = exact dissolve of blocks by geoid[:11]; precinct = a synthetic
k-d partition of block centroids (the real precinct polygons come from build_area_geometry.py); ZIP = ZCTA polygon.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent.parent
SNAP = next(p for p in sorted((ROOT / '.local' / 'snapshots').iterdir()) if (p / 'geometry.json').is_file())
OUT = ROOT / 'frontend' / 'public' / 'fixtures' / 'v3'
RNG = np.random.default_rng(20261001)

COARSE, FINE = 0.00055, 0.00006


def r5(coords):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], 5), round(coords[1], 5)]
    return [r5(c) for c in coords]


def feature(ident, geom, tol, extra):
    g = geom.simplify(tol, preserve_topology=True) if tol else geom
    if g.is_empty:
        g = geom
    m = mapping(g)
    return {'type': 'Feature', 'properties': {'id': ident, **extra},
            'geometry': {'type': m['type'], 'coordinates': r5(m['coordinates'])}}


def label_point(geom):
    p = geom.representative_point()
    return round(p.x, 5), round(p.y, 5)


def kd_split(idx, xy, k):
    """Split point indices into exactly k spatially compact groups (k-d bisection, proportional)."""
    if k <= 1 or len(idx) <= 1:
        return [idx]
    pts = xy[idx]
    axis = 0 if np.ptp(pts[:, 0]) * math.cos(math.radians(38.8)) >= np.ptp(pts[:, 1]) else 1
    order = idx[np.argsort(pts[:, axis])]
    k1 = k // 2
    cut = int(round(len(order) * k1 / k))
    return kd_split(order[:cut], xy, k1) + kd_split(order[cut:], xy, k - k1)


# Smooth spatial fields: random Gaussian bumps over the study area.
BUMPS = []


def make_bumps(n, w, s, e, nn):
    out = []
    for _ in range(n):
        out.append((RNG.uniform(w, e), RNG.uniform(s, nn), RNG.uniform(0.012, 0.06), RNG.normal(0, 1)))
    return out


def field(bumps, x, y):
    v = np.zeros_like(x)
    for cx, cy, sig, amp in bumps:
        v += amp * np.exp(-(((x - cx) * 0.78) ** 2 + (y - cy) ** 2) / (2 * sig * sig))
    return v


def zscore(v):
    return (v - v.mean()) / (v.std() + 1e-9)


def midrank_pct(values):
    """Midrank percentile (0-100) among all the areas of the level."""
    vals = np.array(values, dtype=float)
    out = [None] * len(vals)
    pub = np.where(~np.isnan(vals))[0]
    srt = np.sort(vals[pub])
    for i in pub:
        lo = np.searchsorted(srt, vals[i], 'left')
        hi = np.searchsorted(srt, vals[i], 'right')
        out[i] = round(100.0 * ((lo + hi) / 2.0) / len(srt), 3)
    return out


def rnd(x, d=3):
    return None if x is None else round(float(x), d)


def main():
    OUT.joinpath('geometry').mkdir(parents=True, exist_ok=True)
    OUT.joinpath('areas').mkdir(parents=True, exist_ok=True)
    geo = json.loads((SNAP / 'geometry.json').read_text(encoding='utf-8'))
    district = json.loads((SNAP / 'district.geojson').read_text(encoding='utf-8'))
    dgeom = shape(district['features'][0]['geometry'])
    # Only public geographic context is copied; no person/score/contact store is opened.
    context = {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'properties': {}, 'geometry': f['geometry']} for f in district['features']]}
    (OUT / 'district.json').write_text(json.dumps(context, separators=(',', ':')), encoding='utf-8')
    roads = []
    with sqlite3.connect((ROOT / '.local' / 'roads.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        for ident, kind, raw in db.execute('SELECT id,kind,geometry FROM roads ORDER BY id'):
            g = shape(json.loads(raw)).simplify(0.00001, preserve_topology=True)
            roads.append({'type': 'Feature', 'id': ident, 'properties': {'kind': kind},
                          'bbox': list(g.bounds), 'geometry': mapping(g)})
    (OUT / 'roads.json').write_text(json.dumps({'type': 'FeatureCollection', 'features': roads,
        'attribution': 'U.S. Census TIGER/Line 2025 — public road geometry'}, separators=(',', ':')), encoding='utf-8')
    bids = sorted(geo['block'])
    blocks = {i: shape(geo['block'][i]['geometry']) for i in bids}
    cent = np.array([[blocks[i].representative_point().x, blocks[i].representative_point().y] for i in bids])
    print('blocks', len(bids), 'district bbox', [round(v, 4) for v in dgeom.bounds], file=sys.stderr)

    # ---- hierarchy ---------------------------------------------------------------------------------------------
    tract_of = {i: i[:11] for i in bids}
    groups = kd_split(np.arange(len(bids)), cent, 326)
    precinct_codes = []
    precinct_of = {}
    members_p = []
    for k, grp in enumerate(groups):
        code = '5' + f'{(k * 7) % 9000 + 1000:04d}' + '21' + f'{101 + k:03d}'
        precinct_codes.append(code)
        members_p.append([bids[j] for j in grp])
        for j in grp:
            precinct_of[bids[j]] = code
    tracts = sorted(set(tract_of.values()))
    tract_members = {t: [i for i in bids if tract_of[i] == t] for t in tracts}
    zips = sorted(geo['zcta'])
    zgeom = {z: shape(geo['zcta'][z]['geometry']) for z in zips}

    # ---- synthetic block values ---------------------------------------------------------------------------------
    w, s, e, n = dgeom.bounds
    f_main = zscore(field(make_bumps(46, w, s, e, n), cent[:, 0], cent[:, 1]))
    f_cua = zscore(0.7 * f_main + 0.7 * zscore(field(make_bumps(30, w, s, e, n), cent[:, 0], cent[:, 1])))
    f_part = zscore(0.7 * f_main + 0.7 * zscore(field(make_bumps(30, w, s, e, n), cent[:, 0], cent[:, 1])))
    f_soi = zscore(0.6 * f_main + 0.8 * zscore(field(make_bumps(30, w, s, e, n), cent[:, 0], cent[:, 1])))
    f_lean = field(make_bumps(24, w, s, e, n), cent[:, 0], cent[:, 1])  # Native synthetic signal: no cohort centering or variance normalization.
    f_meas = zscore(field(make_bumps(20, w, s, e, n), cent[:, 0], cent[:, 1]))
    nb = len(bids)
    area_km = np.array([blocks[i].area for i in bids])
    n_rows = np.clip(np.round(np.exp(RNG.normal(3.6, 1.05, nb)) * (0.55 + 3.0 * np.sqrt(area_km / np.median(area_km)) ** 0.5)), 1, 2600)
    def apportion(weights, total):
        units = (total - len(weights)) * weights / weights.sum()
        result = np.floor(units).astype(int) + 1
        order = np.argsort(-(units - np.floor(units)), kind='stable')
        result[order[:total - int(result.sum())]] += 1
        return result
    # Exact totals of fixtures.ts (45,000 seeded households); no real population totals.
    n_rows = apportion(n_rows, 109150)
    hh = apportion((n_rows - 1).astype(float), 45000)
    assert np.all(hh <= n_rows)

    def mean_vals(f, centre, spread, noise):
        return np.clip(centre + spread * f + RNG.normal(0, noise, nb), 3, 97)

    group_ids = ['ovr', 'cua', 'part', 'soi']
    mean_b = {
        'ovr': mean_vals(f_main, 52, 15, 4),
        'cua': mean_vals(f_cua, 50, 14, 5),
        'part': mean_vals(f_part, 54, 13, 5),
        'soi': mean_vals(f_soi, 46, 13, 5),
    }
    mean_b['ovr'] = .40 * mean_b['cua'] + .35 * mean_b['part'] + .25 * mean_b['soi']
    zfield = {'ovr': f_main, 'cua': f_cua, 'part': f_part, 'soi': f_soi}
    top_b = {g: np.clip(0.10 * np.exp(0.72 * zfield[g] + RNG.normal(0, 0.18, nb)), 0.004, 0.62) for g in group_ids}
    for g in group_ids:
        top_b[g] *= .1 / np.average(top_b[g], weights=n_rows)
    lean_b = np.clip(f_lean * 24 + RNG.normal(0, 7, nb), -100, 100)  # Fixed unit conversion and bounds only; no forced center or spread.
    meas_b = np.clip(66 + 9 * f_meas + RNG.normal(0, 4, nb), 25, 98)
    reg_u = np.clip(0.36 + 0.03 * f_meas + RNG.normal(0, 0.03, nb), 0.2, 0.55)
    reg_d = np.clip(0.30 + 0.035 * f_lean + RNG.normal(0, 0.03, nb), 0.1, 0.6)
    reg_r = np.clip(1 - reg_u - reg_d - 0.02 - RNG.uniform(0, 0.03, nb), 0.08, 0.7)

    def build_level(level, ids, labels, n_arr, hh_arr, mean, top, lean, meas, shares, parent, extra):
        """Columnar area payload. mean/top: dict group -> list. Every area has values."""
        cells = {}
        for g in group_ids:
            m = [float(v) for v in mean[g]]
            t = [float(v) for v in top[g]]
            se = [float(14.0 / math.sqrt(max(nr, 1)) + 0.4) for nr in n_arr]
            tse = [float(max(0.004, 0.55 * tv / math.sqrt(max(nr / 12.0, 1)))) for nr, tv in zip(n_arr, top[g])]
            cells[g] = {
                'mean': [rnd(v, 2) for v in m],
                'se': [rnd(v, 2) for v in se],
                'measured': [rnd(v, 1) for v in meas],
                'pct': midrank_pct(m),
                'top10': [rnd(v, 4) for v in t],
                'top10_se': [rnd(v, 4) for v in tse],
                'top10_pct': midrank_pct(t),
            }
        cells['lean'] = {
            'mean': [rnd(v, 2) for v in lean],
            'se': [rnd(25.0 / math.sqrt(max(nr, 1)) + 0.5, 2) for nr in n_arr],
            'measured': [rnd(v, 1) for v in meas],
            'share_d': [rnd(v, 4) for v in shares[0]],
            'share_r': [rnd(v, 4) for v in shares[1]],
            'share_u': [rnd(v, 4) for v in shares[2]],
        }
        payload = {
            'schema': 'v3-areas-1', 'snapshot': 'fixture-v3', 'basis': 'registration', 'level': level,
            'ids': ids, 'labels': labels,
            'status': ['ok'] * len(ids),
            'n': [int(x) for x in n_arr],
            'households': [int(x) for x in hh_arr],
            'parent': parent,
            'groups': cells,
        }
        payload.update(extra)
        return payload

    def lab_block(i):
        return 'Block ' + i[-4:]

    def lab_tract(t):
        code = t[5:11]
        head = str(int(code[:4]))
        tail = code[4:]
        return 'Tract ' + head + ('' if tail == '00' else '.' + tail)

    def agg(member_idx_lists):
        """Aggregate block arrays into parent areas (weights = n_rows)."""
        res = {'n': [], 'hh': [], 'mean': {g: [] for g in group_ids}, 'top': {g: [] for g in group_ids},
               'lean': [], 'meas': [], 'sd': [], 'sr': [], 'su': [], 'blocks': []}
        for idx in member_idx_lists:
            idx = np.array(idx)
            wt = n_rows[idx].astype(float)
            tot = wt.sum()
            res['n'].append(int(n_rows[idx].sum()))
            res['hh'].append(int(hh[idx].sum()))
            res['blocks'].append(int(len(idx)))
            for g in group_ids:
                res['mean'][g].append(float((mean_b[g][idx] * wt).sum() / tot))
                res['top'][g].append(float((top_b[g][idx] * wt).sum() / tot))
            res['lean'].append(float((lean_b[idx] * wt).sum() / tot))
            res['meas'].append(float((meas_b[idx] * wt).sum() / tot))
            res['sd'].append(float((reg_d[idx] * wt).sum() / tot))
            res['sr'].append(float((reg_r[idx] * wt).sum() / tot))
            res['su'].append(float((reg_u[idx] * wt).sum() / tot))
        return res

    # ---- geometry + areas, per level -----------------------------------------------------------------------------
    index_of = {i: k for k, i in enumerate(bids)}

    # blocks
    for variant, tol in (('coarse', COARSE), ('fine', FINE)):
        feats = []
        for i in bids:
            lx, ly = label_point(blocks[i])
            feats.append(feature(i, blocks[i], tol, {'lx': lx, 'ly': ly}))
        (OUT / 'geometry' / f'block.{variant}.json').write_text(json.dumps({'type': 'FeatureCollection', 'features': feats}, separators=(',', ':')), encoding='utf-8')
    block_payload = build_level(
        'block', bids, [lab_block(i) for i in bids], n_rows, hh, mean_b, top_b, lean_b, meas_b,
        (reg_d, reg_r, reg_u), [tract_of[i] for i in bids],
        {'precinct': [precinct_of[i] for i in bids], 'tract': [tract_of[i] for i in bids], 'blocks': [None] * nb})
    (OUT / 'areas' / 'block.json').write_text(json.dumps(block_payload, separators=(',', ':')), encoding='utf-8')
    print('block done', file=sys.stderr)

    def group_level(level, codes, member_lists, labeller, geom_of, modal_tract):
        a = agg([[index_of[b] for b in mem] for mem in member_lists])
        n_arr = np.array(a['n'])
        hh_arr = np.array(a['hh'])
        for variant, tol in (('coarse', COARSE), ('fine', FINE)):
            feats = []
            for c, mem in zip(codes, member_lists):
                g = geom_of(c, mem)
                lx, ly = label_point(g)
                feats.append(feature(c, g, tol * 1.6, {'lx': lx, 'ly': ly}))
            (OUT / 'geometry' / f'{level}.{variant}.json').write_text(json.dumps({'type': 'FeatureCollection', 'features': feats}, separators=(',', ':')), encoding='utf-8')
        payload = build_level(level, codes, [labeller(c) for c in codes], n_arr, hh_arr, a['mean'], a['top'], a['lean'], a['meas'],
                              (a['sd'], a['sr'], a['su']), [None] * len(codes),
                              {'tract': modal_tract, 'blocks': a['blocks'], 'precinct': [None] * len(codes)})
        (OUT / 'areas' / f'{level}.json').write_text(json.dumps(payload, separators=(',', ':')), encoding='utf-8')
        print(level, 'done', len(codes), file=sys.stderr)

    # tracts
    tract_geoms = {t: unary_union([blocks[b] for b in tract_members[t]]) for t in tracts}
    group_level('tract', tracts, [tract_members[t] for t in tracts], lab_tract, lambda c, mem: tract_geoms[c], [None] * len(tracts))

    # precincts (synthetic k-d partition)
    pgeom = {c: unary_union([blocks[b] for b in mem]) for c, mem in zip(precinct_codes, members_p)}

    def modal_tract_of(mem):
        counts = {}
        for b in mem:
            counts[tract_of[b]] = counts.get(tract_of[b], 0) + int(n_rows[index_of[b]])
        return max(counts, key=counts.get)

    group_level('precinct', precinct_codes, members_p, lambda c: 'Precinct ' + c[-3:], lambda c, mem: pgeom[c],
                [modal_tract_of(m) for m in members_p])

    # ZIP: members = blocks whose representative point lies in the ZCTA
    zip_members = {z: [] for z in zips}
    for k, i in enumerate(bids):
        from shapely.geometry import Point
        pt = Point(cent[k])
        for z in zips:
            if zgeom[z].contains(pt):
                zip_members[z].append(i)
                break
    zips_kept = [z for z in zips if zip_members[z]]
    group_level('zip', zips_kept, [zip_members[z] for z in zips_kept], lambda c: 'ZIP ' + c, lambda c, mem: zgeom[c],
                [modal_tract_of(zip_members[z]) for z in zips_kept])

    meta = {
        'schema': 'v3-meta-1', 'snapshot': 'fixture-v3', 'demo': True, 'source': 'fixture',
        'people': int(n_rows.sum()), 'households': int(hh.sum()),
        'district_label': 'Congressional District 5',
        'district_rate': {'ovr': 0.1, 'cua': 0.1, 'part': 0.1, 'soi': 0.1},
        'share_breaks': [0.05, 0.08, 0.12, 0.18], 'lean_cuts': [25, 75], 'measured_breaks': [20, 40, 60, 80],
        'household_breaks': [20, 40, 60, 80],
        'weights': {'cua': 40, 'part': 35, 'soi': 25},
        'levels': ['block', 'precinct', 'tract', 'zip'],
        'bbox': [round(v, 5) for v in dgeom.bounds],
        'small_area': {'min_n': 20, 'min_households': 10},
    }
    (OUT / 'meta.json').write_text(json.dumps(meta, separators=(',', ':')), encoding='utf-8')
    for p in sorted(OUT.rglob('*.json')):
        print(p.relative_to(OUT), p.stat().st_size, file=sys.stderr)


if __name__ == '__main__':
    main()
