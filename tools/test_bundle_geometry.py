"""Synthetic checks for the canonical ring start of tools/build_bundle_v4.py (Paul 2026-10-13: every ring is written from the same vertex,
so the bundle's geometry.json is byte-identical on Windows and in the container). No real data, no Django.

    <venv python> -m unittest tools.test_bundle_geometry          (from the V3 folder)
"""
import copy
import io
import random
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from shapely.geometry import MultiPolygon, Polygon

from tools import build_bundle_v4 as B4


def rotated(ring, by):
    """The closed `ring` written from its vertex number `by` (what another GEOS would have written)."""
    body = ring[:-1]
    body = body[by:] + body[:by]
    return body + [body[0]]


def signed_area(ring):
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:])) / 2


def level(kind, coordinates, ident='1'):
    """A one-feature geometry.json as the builder writes it."""
    return {'tract': {ident: {'id': ident, 'geometry': {'type': kind, 'coordinates': coordinates}, 'bbox': [0.0, 0.0, 9.0, 9.0]}}}


def serialized(geometry):
    return B4.dumps(B4.canonical_geometry(geometry)).encode('utf-8')


SQUARE_CCW = [[1.0, 1.0], [5.0, 1.0], [5.0, 5.0], [1.0, 5.0], [1.0, 1.0]]
SQUARE_CW = [[1.0, 1.0], [1.0, 5.0], [5.0, 5.0], [5.0, 1.0], [1.0, 1.0]]
SHAPE = [[3.5, 2.0], [4.0, 4.5], [2.0, 6.0], [-1.25, 4.0], [0.5, 0.125], [2.5, -1.0], [3.5, 2.0]]     # an irregular exterior (counter-clockwise)
HOLE = [[1.0, 2.0], [1.0, 3.0], [2.0, 3.0], [2.0, 2.0], [1.0, 2.0]]                                  # clockwise, inside SHAPE


class StartRingTests(unittest.TestCase):
    def test_every_start_gives_the_same_ring(self):
        want = B4.start_ring(SHAPE)
        for by in range(len(SHAPE) - 1):
            self.assertEqual(B4.start_ring(rotated(SHAPE, by)), want, by)

    def test_the_ring_starts_and_ends_at_its_smallest_x_then_y(self):
        ring = B4.start_ring(SHAPE)
        self.assertEqual(ring[0], [-1.25, 4.0])
        self.assertEqual(ring[0], ring[-1])
        self.assertEqual(ring[0], min(ring[:-1]))
        tie = [[2.0, 7.0], [2.0, 1.0], [6.0, 1.0], [6.0, 7.0], [2.0, 7.0]]                          # two vertices share the smallest x: the smaller y wins
        self.assertEqual(B4.start_ring(tie)[0], [2.0, 1.0])

    def test_orientation_and_the_cycle_are_kept(self):
        for ring in (SQUARE_CCW, SQUARE_CW, SHAPE, HOLE):
            for by in range(len(ring) - 1):
                made = B4.start_ring(rotated(ring, by))
                self.assertEqual(signed_area(made), signed_area(ring))
                self.assertEqual(len(made), len(ring))
                self.assertIn(made[:-1], [rotated(ring, i)[:-1] for i in range(len(ring) - 1)])        # a rotation, never a reversal or a re-order
        self.assertGreater(signed_area(B4.start_ring(SQUARE_CCW)), 0)
        self.assertLess(signed_area(B4.start_ring(SQUARE_CW)), 0)

    def test_a_vertex_visited_twice_at_the_smallest_point_does_not_make_the_start_depend_on_the_input(self):
        pinch = [[0.0, 0.0], [2.0, 1.0], [1.0, 3.0], [0.0, 0.0], [-2.0, 1.0], [-1.0, -3.0], [0.0, 0.0]]        # (0, 0) twice, and (-2, 1) is the smallest
        low_twice = [[-3.0, 0.0], [1.0, 2.0], [0.0, 5.0], [-3.0, 0.0], [-1.0, 4.0], [-2.0, 6.0], [-3.0, 0.0]]       # the smallest point itself twice
        self.assertEqual(low_twice[0], low_twice[3])
        self.assertEqual(min(low_twice[:-1]), low_twice[0])
        for ring in (pinch, low_twice):
            want = B4.start_ring(ring)
            for by in range(len(ring) - 1):
                self.assertEqual(B4.start_ring(rotated(ring, by)), want, by)

    def test_the_input_is_not_changed(self):
        ring = rotated(SHAPE, 2)
        before = copy.deepcopy(ring)
        B4.start_ring(ring)
        self.assertEqual(ring, before)


class CanonicalGeometryTests(unittest.TestCase):
    def test_the_same_polygon_from_different_starts_serializes_to_identical_bytes(self):
        reference = serialized(level('Polygon', [SHAPE]))
        for by in range(len(SHAPE) - 1):
            self.assertEqual(serialized(level('Polygon', [rotated(SHAPE, by)])), reference, by)

    def test_holes_are_rotated_too_and_their_orientation_is_kept(self):
        reference = serialized(level('Polygon', [SHAPE, HOLE]))
        for outer in range(len(SHAPE) - 1):
            for inner in range(len(HOLE) - 1):
                self.assertEqual(serialized(level('Polygon', [rotated(SHAPE, outer), rotated(HOLE, inner)])), reference, (outer, inner))
        done = B4.canonical_geometry(level('Polygon', [rotated(SHAPE, 3), rotated(HOLE, 2)]))['tract']['1']['geometry']['coordinates']
        self.assertEqual(done[1][0], min(HOLE[:-1]))
        self.assertGreater(signed_area(done[0]), 0)
        self.assertLess(signed_area(done[1]), 0)
        self.assertEqual(len(done), 2)                                                             # the exterior stays first, the hole second

    def test_the_holes_come_in_the_order_of_their_start_vertex_whatever_order_they_were_given(self):
        """Parity 2026-10-13: a dissolved polygon came with the same holes in another order on the other platform."""
        far = [[3.0, 3.0], [3.0, 3.5], [3.5, 3.5], [3.5, 3.0], [3.0, 3.0]]
        near = [[0.0, 2.0], [0.0, 2.5], [0.5, 2.5], [0.5, 2.0], [0.0, 2.0]]
        reference = serialized(level('Polygon', [SHAPE, near, HOLE, far]))
        for holes in ([near, HOLE, far], [far, HOLE, near], [HOLE, far, near], [far, near, HOLE]):
            for by in range(4):
                moved = [rotated(SHAPE, by + 1)] + [rotated(h, by) for h in holes]
                self.assertEqual(serialized(level('Polygon', moved)), reference, (by, holes))
        done = B4.canonical_geometry(level('Polygon', [rotated(SHAPE, 2), far, near, HOLE]))['tract']['1']['geometry']['coordinates']
        self.assertEqual([ring[0] for ring in done], [[-1.25, 4.0], [0.0, 2.0], [1.0, 2.0], [3.0, 3.0]])      # the exterior first, then the holes by start vertex
        self.assertEqual([signed_area(ring) < 0 for ring in done[1:]], [True, True, True])                   # their orientation is kept
        self.assertEqual(B4.canonical_polygon([]), [])

    def test_the_order_of_the_polygons_of_a_multipolygon_is_not_touched(self):
        low, high = [SHAPE], [[[x + 10.0, y] for x, y in SHAPE]]                                   # two polygons, each a list of rings
        for parts in ([low, high], [high, low]):
            done = B4.canonical_geometry(level('MultiPolygon', [[rotated(p[0], 2)] for p in parts]))['tract']['1']['geometry']['coordinates']
            self.assertEqual([polygon[0][0] for polygon in done], [B4.start_ring(p[0])[0] for p in parts])      # the first one stays the first

    def test_every_polygon_of_a_multipolygon_is_rotated_and_their_order_is_kept(self):
        second = [[x + 10.0, y] for x, y in SHAPE]
        parts = [[SHAPE, HOLE], [second]]
        reference = serialized(level('MultiPolygon', parts))
        for by in range(len(SHAPE) - 1):
            moved = [[rotated(SHAPE, by), rotated(HOLE, by % 4)], [rotated(second, (by + 3) % 6)]]
            self.assertEqual(serialized(level('MultiPolygon', moved)), reference, by)
        done = B4.canonical_geometry(level('MultiPolygon', parts))['tract']['1']['geometry']['coordinates']
        self.assertEqual([len(polygon) for polygon in done], [2, 1])
        self.assertEqual(done[1][0][0], [8.75, 4.0])                                               # the second polygon, from its own smallest vertex

    def test_only_the_start_moves(self):
        geometry = level('Polygon', [rotated(SHAPE, 4), rotated(HOLE, 1)])
        geometry['block'] = {'b': {'id': 'b', 'geometry': {'type': 'Polygon', 'coordinates': [rotated(SQUARE_CW, 2)]}, 'bbox': [1.0, 1.0, 5.0, 5.0]}}
        before = copy.deepcopy(geometry)
        done = B4.canonical_geometry(geometry)
        self.assertEqual(geometry, before)                                                         # the input is not changed
        self.assertEqual(list(done), ['tract', 'block'])
        feature = done['tract']['1']
        self.assertEqual(list(feature), ['id', 'geometry', 'bbox'])
        self.assertEqual(list(feature['geometry']), ['type', 'coordinates'])
        self.assertEqual(feature['bbox'], [0.0, 0.0, 9.0, 9.0])
        self.assertEqual(sorted(map(tuple, feature['geometry']['coordinates'][0][:-1])),
                         sorted(map(tuple, before['tract']['1']['geometry']['coordinates'][0][:-1])))      # the same vertices
        self.assertEqual(done['block']['b']['geometry']['coordinates'], [B4.start_ring(SQUARE_CW)])

    def test_a_nudged_coordinate_still_changes_the_bytes(self):
        reference = serialized(level('Polygon', [SHAPE]))
        nudged = copy.deepcopy(SHAPE)
        nudged[2][0] += 0.000001
        self.assertNotEqual(serialized(level('Polygon', [nudged])), reference)

    def test_features_from_the_builder_are_the_same_whatever_the_ring_started_from(self):
        """feature() takes shapely's rings as they come; two polygons with the same shape and another first vertex end in the same bytes."""
        a = Polygon(SHAPE[:-1], [HOLE[:-1]])
        b = Polygon(rotated(SHAPE, 4)[:-1], [rotated(HOLE, 2)[:-1]])
        self.assertTrue(a.equals(b))
        self.assertNotEqual(list(a.exterior.coords), list(b.exterior.coords))
        self.assertEqual(serialized({'tract': {'1': B4.feature('1', a)}}), serialized({'tract': {'1': B4.feature('1', b)}}))
        multi = MultiPolygon([a, Polygon([[20, 0], [24, 0], [24, 4], [20, 4]])])
        flipped = MultiPolygon([b, Polygon([[24, 4], [20, 4], [20, 0], [24, 0]])])
        self.assertEqual(serialized({'precinct': {'1': B4.feature('1', multi)}}), serialized({'precinct': {'1': B4.feature('1', flipped)}}))

    def test_the_zip_is_the_same_bytes_whatever_the_platform_says_it_is(self):
        """ZipInfo.create_system is 0 on Windows and 3 elsewhere and is written into the archive: the zip must not depend on it."""
        members = {'manifest.json': b'{"a":1}', 'geometry.json': b'{"b":[1,2,3]}' * 50, 'rows.json': b'[]'}
        made = {}
        for platform in ('win32', 'linux'):
            with tempfile.TemporaryDirectory() as folder, mock.patch.object(zipfile.sys, 'platform', platform):
                path = Path(folder) / 'k.zip'
                B4.write_zip(path, members)
                made[platform] = path.read_bytes()
        self.assertEqual(made['win32'], made['linux'])
        with zipfile.ZipFile(io.BytesIO(made['win32'])) as archive:
            self.assertEqual(archive.namelist(), ['geometry.json', 'manifest.json', 'rows.json'])
            self.assertEqual({info.create_system for info in archive.infolist()}, {3})
            self.assertEqual(archive.read('geometry.json'), members['geometry.json'])

    def test_random_polygons_any_start(self):
        rng = random.Random(20261013)
        for _ in range(200):
            n = rng.randrange(3, 12)
            ring = [[round(rng.uniform(-9, 9), 6), round(rng.uniform(-9, 9), 6)] for _ in range(n)]
            ring.append(ring[0])
            holes = [[[round(x + 0.5, 6), round(y + 0.5, 6)] for x, y in rotated(ring, 0)]]
            want = serialized(level('Polygon', [ring] + holes))
            for _ in range(5):
                by = rng.randrange(n)
                moved = [rotated(ring, by)] + [rotated(h, rng.randrange(n)) for h in holes]
                self.assertEqual(serialized(level('Polygon', moved)), want)
                made = B4.start_ring(moved[0])
                self.assertEqual(made[0], min(made[:-1]))


if __name__ == '__main__':
    unittest.main()
