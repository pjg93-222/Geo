"""Demo-mode display positions for street markers.

A rooftop position identifies a place. In demo mode the street endpoint never sends one: each household is drawn at a
deterministic point inside its own block polygon, chosen from HMAC(demo_key, "h:" + household_code + counter), so the
same household always lands on the same spot, the spot lies in the right block, and it is not the household's address.
"""
import hashlib
import hmac
import json
from functools import lru_cache

from . import scoresdb as DB
from . import v3_cache

TRIES = 24


def inside_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def inside_polygon(x, y, polygon):
    """polygon = [exterior, hole, ...] as GeoJSON coordinate rings."""
    if not inside_ring(x, y, polygon[0]):
        return False
    return not any(inside_ring(x, y, hole) for hole in polygon[1:])


@lru_cache(maxsize=2)
def block_shapes(key):
    """{block id: (minx, miny, maxx, maxy, polygons, label x, label y)} from the fine block geometry."""
    collection = json.loads(v3_cache.geometry_blob(key, 'block', 'fine').body)
    shapes = {}
    for feature in collection['features']:
        geometry = feature['geometry']
        polygons = [geometry['coordinates']] if geometry['type'] == 'Polygon' else geometry['coordinates']
        xs = [p[0] for polygon in polygons for p in polygon[0]]
        ys = [p[1] for polygon in polygons for p in polygon[0]]
        shapes[feature['properties']['id']] = (min(xs), min(ys), max(xs), max(ys), polygons,
                                               feature['properties']['lx'], feature['properties']['ly'])
    return shapes


def point_in_block(key, shape, household_code):
    minx, miny, maxx, maxy, polygons, lx, ly = shape
    for attempt in range(TRIES):
        digest = hmac.new(key, ('h:%s:%d' % (household_code, attempt)).encode('ascii'), hashlib.sha256).digest()
        fx = int.from_bytes(digest[:8], 'big') / 2 ** 64
        fy = int.from_bytes(digest[8:16], 'big') / 2 ** 64
        x, y = minx + fx * (maxx - minx), miny + fy * (maxy - miny)
        if any(inside_polygon(x, y, polygon) for polygon in polygons):
            return x, y
    return lx, ly


def displaced(demo_key, rows):
    """[(lon, lat)] for street rows (each with `hh` and `block`); never the household's real position."""
    from .v3_area_views import v4_snapshot
    found = v4_snapshot()
    if found is None:
        raise DB.StoreError('The area layers have not been imported.')
    shapes = block_shapes(found[0])
    out = []
    for row in rows:
        shape = shapes.get(row['block'])
        # a household whose block has no polygon is left out of the demo view rather than drawn at its address
        out.append(point_in_block(demo_key, shape, row['hh']) if shape is not None else None)
    return out
