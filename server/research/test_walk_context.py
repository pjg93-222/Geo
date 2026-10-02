"""Walk list map context (walk_context.build_context): the WalkContext shape, road kinds and labels, padding, caps,
precinct outlines from a snapshot and repeatability. Synthetic data only: the walk store is built by tools/build_walk.py
from walk_testkit's invented world in a temporary directory; the snapshot is a few hand-drawn polygons."""
import json
import math
import shutil
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

import numpy as np
from django.test import TestCase, override_settings

from tools import build_walk as B

from . import v3_cache
from . import walk_context as C
from . import walk_store as W
from . import walk_testkit as T
from .models import Snapshot
from .walk_route import DEG_M

NORTH = (-310, -240, 340, 240)          # local metres (x0, y0, x1, y1): the neighbourhood, without the Fountain Pine St
WORLD = (-320, -3220, 770, 470)         # everything
LABELS = {'CEDAR HTS DR': 'Cedar Heights Dr', 'JUNIPER LN': 'Juniper Ln', 'PNE ST': 'Pine St',
          'RIDGEVIEW CT': 'Ridgeview Ct', 'ACADEMY BLVD': 'Academy Blvd', '': ''}
KEY = 'co-cd005-0123456789ab'
PRECINCT_W, PRECINCT_E, PRECINCT_F = '5200021301', '5200021302', '5200021417'


def box(x0, y0, x1, y1):
    return [*T.lonlat(x0, y0), *T.lonlat(x1, y1)]


def ring(x0, y0, x1, y1):
    return [list(T.lonlat(x, y)) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0))]


def close(a, b, within=1.1e-6):
    return all(abs(x - y) <= within for x, y in zip(a, b))


class WalkContextTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temporary = tempfile.TemporaryDirectory()
        stores = T.write_minimal_stores(Path(cls.temporary.name) / 'world')
        if not B.build(stores.source_csv, stores.directory, force=True)['replaced']:
            raise AssertionError('The synthetic walk store was not written.')
        cls.directory = stores.directory
        cls.override = override_settings(DATA_DIR=cls.directory)
        cls.override.enable()
        W.reset()

    @classmethod
    def tearDownClass(cls):
        W.reset()
        cls.override.disable()
        cls.temporary.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.db = sqlite3.connect((self.directory / 'walk.sqlite3').as_uri() + '?mode=ro', uri=True)
        self.addCleanup(self.db.close)
        self.addCleanup(self.forget)
        self.forget()

    def forget(self):
        C.reset()
        v3_cache.forget()
        shutil.rmtree(self.directory / 'snapshots', ignore_errors=True)

    def spans(self):
        """{span id: (name_key, kind, virtual, len_m, [[lon, lat], ...])} straight from the store."""
        return {r[0]: (r[1] or '', r[2], r[3], r[4], json.loads(r[5])) for r in self.db.execute(
            'SELECT id, name_key, kind, virtual, len_m, coords FROM road_span')}

    def source(self, road, spans):
        """The id of the block a road line was drawn from: the one with the same two ends."""
        found = [sid for sid, s in spans.items() if close(s[4][0], road['c'][0]) and close(s[4][-1], road['c'][-1])]
        self.assertEqual(len(found), 1, road)
        return found[0]

    def assert_point(self, point, bbox=None):
        self.assertEqual(len(point), 2)
        for value in point:
            self.assertIsInstance(value, float)
            self.assertEqual(round(value, 6), value)
        if bbox is not None:
            self.assertTrue(bbox[0] <= point[0] <= bbox[2] and bbox[1] <= point[1] <= bbox[3], point)

    def snapshot(self, geometry, schema='cu-summary-v4'):
        folder = self.directory / 'snapshots' / KEY
        folder.mkdir(parents=True)
        if geometry is not None:
            (folder / 'geometry.json').write_text(json.dumps({'precinct': geometry}), encoding='utf-8')
        Snapshot.objects.create(key=KEY, manifest={'schema': schema}, active=True)

    # ---- shape and roads -------------------------------------------------------------------------------------------
    def test_shape_matches_walk_context(self):
        raw = box(*NORTH)
        context = C.build_context(self.db, raw)
        self.assertEqual(set(context), {'bbox', 'roads', 'precincts', 'homes'})
        self.assertEqual(json.loads(json.dumps(context, allow_nan=False)), context)
        bbox = context['bbox']
        self.assertEqual(len(bbox), 4)
        for value in bbox:
            self.assertEqual(round(value, 6), value)
        self.assertTrue(bbox[0] < raw[0] and bbox[1] < raw[1] and bbox[2] > raw[2] and bbox[3] > raw[3])
        self.assertTrue(context['roads'])
        for road in context['roads']:
            self.assertEqual(set(road), {'k', 'n', 'c'})
            self.assertIn(road['k'], ('S1200', 'S1400', 'V'))
            self.assertIsInstance(road['n'], str)
            self.assertGreaterEqual(len(road['c']), 2)
            for point in road['c']:
                self.assert_point(point)
        self.assertEqual(context['precincts'], [])               # this world has no snapshot: no outlines, no error
        homes = context['homes']
        for point in homes:
            self.assert_point(point, bbox)
        self.assertEqual(homes, sorted(homes))
        west, south, east, north = bbox
        want = {(round(lon, 6), round(lat, 6)) for lon, lat in self.db.execute(
            'SELECT lon, lat FROM stop WHERE lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?', (west, east, south, north))}
        self.assertEqual(len(homes), len(want))                 # one point per position
        self.assertEqual({tuple(p) for p in homes}, want)
        self.assertEqual(len(want), 24)                         # the 26 stops less the two on the Fountain Pine St

    def test_roads_are_the_blocks_with_their_kind_label_and_simplified_line(self):
        spans = self.spans()
        roads = C.build_context(self.db, box(*WORLD))['roads']
        self.assertEqual(sorted(self.source(road, spans) for road in roads), sorted(spans))
        for road in roads:
            key, kind, virtual, _, line = spans[self.source(road, spans)]
            self.assertEqual(road['k'], 'V' if virtual else kind)
            self.assertEqual(road['n'], LABELS[key])                         # the voters' label, not 'Pne St'
            if key:
                self.assertEqual(len(road['c']), 2, key)                      # every named block here is (nearly) straight
            else:
                self.assertEqual(len(road['c']), len(line))                   # the bent connector keeps its shape
                for got, want in zip(road['c'], line):
                    self.assertTrue(close(got, want), (got, want))
        self.assertEqual(sorted({r['k'] for r in roads}), ['S1200', 'S1400', 'V'])
        self.assertEqual(max(len(s[4]) for s in spans.values() if s[0]), 4)    # Cedar Heights Dr east of Pine St: 4 -> 2
        self.assertEqual([len(r['c']) for r in roads if r['k'] == 'S1400' and not r['n']], [5])

    def test_virtual_blocks_are_marked_v(self):
        spans = self.spans()
        virtual = [sid for sid, s in spans.items() if s[2]]
        self.assertEqual(len(virtual), 1)                                      # the connector to the Fountain Pine St
        roads = C.build_context(self.db, box(*WORLD))['roads']
        marked = [r for r in roads if r['k'] == 'V']
        self.assertEqual([(self.source(r, spans), r['n']) for r in marked], [(virtual[0], '')])
        older = sqlite3.connect(':memory:')                                   # a store written before the virtual column
        self.addCleanup(older.close)
        self.db.backup(older)
        older.execute('ALTER TABLE road_span DROP COLUMN virtual')
        kinds = sorted(r['k'] for r in C.build_context(older, box(*WORLD))['roads'])
        self.assertEqual(kinds, sorted(s[1] for s in spans.values()))

    def test_padding_reaches_a_road_just_outside_the_bbox(self):
        raw = box(-90, 105, -10, 115)                    # a strip of Ridgeview Ct; Pine St runs 110 m east of it
        names = lambda **options: {r['n'] for r in C.build_context(self.db, raw, **options)['roads']}
        self.assertEqual(names(pad_m=0), {'Ridgeview Ct'})
        padded = names()
        self.assertTrue({'Ridgeview Ct', 'Pine St', 'Cedar Heights Dr', 'Juniper Ln'} <= padded, padded)
        self.assertNotIn('Academy Blvd', padded)         # 340 m east: beyond 300 m
        self.assertIn('Academy Blvd', names(pad_m=400))
        bbox = C.build_context(self.db, raw)['bbox']
        self.assertAlmostEqual((raw[0] - bbox[0]) * DEG_M * math.cos(math.radians((raw[1] + raw[3]) / 2)), 300, delta=0.5)
        self.assertAlmostEqual((bbox[3] - raw[3]) * DEG_M, 300, delta=0.5)
        with self.assertRaises(ValueError):
            C.build_context(self.db, [raw[2], raw[1], raw[0], raw[3]])

    def test_caps_drop_the_shortest_unnamed_roads_first_and_thin_homes(self):
        spans = self.spans()
        named = sorted((sid for sid, s in spans.items() if s[0]), key=lambda sid: (-spans[sid][3], sid))
        unnamed = sorted((sid for sid, s in spans.items() if not s[0]), key=lambda sid: (-spans[sid][3], sid))
        self.assertEqual(len(unnamed), 2)
        whole = box(*WORLD)
        kept = lambda: {self.source(r, spans) for r in C.build_context(self.db, whole)['roads']}
        with mock.patch.object(C, 'MAX_ROADS', len(named) + 1):
            self.assertEqual(kept(), set(named) | {unnamed[0]})                # the longer unnamed one stays
        with mock.patch.object(C, 'MAX_ROADS', len(named)):
            self.assertEqual(kept(), set(named))
        with mock.patch.object(C, 'MAX_ROADS', 3):
            self.assertEqual(kept(), set(named[:3]))                           # then the shortest named ones go
        homes = C.build_context(self.db, whole)['homes']
        self.assertEqual(len(homes), 26)
        with mock.patch.object(C, 'MAX_HOMES', 5):
            thinned = C.build_context(self.db, whole)['homes']
            self.assertEqual(C.build_context(self.db, whole)['homes'], thinned)
        self.assertEqual(len(thinned), 5)
        self.assertEqual(thinned, sorted(thinned))
        self.assertTrue({tuple(p) for p in thinned} <= {tuple(p) for p in homes})
        grid = np.round(np.array([[-104.8 + i * 1e-4, 38.85 + j * 1e-4] for i in range(40) for j in range(40)]), 6)
        picked = C.thin(C._keys(grid), 160)
        self.assertEqual(picked.tolist(), sorted(set(picked.tolist())))
        self.assertEqual(len(picked), 160)
        self.assertEqual(C.thin(C._keys(grid), 160).tolist(), picked.tolist())
        columns = np.bincount(picked // 40, minlength=40)                      # spread over the grid, not one side of it
        self.assertGreater(min(columns[:20].sum(), columns[20:].sum()), 40)

    # ---- precincts -------------------------------------------------------------------------------------------------
    def test_precinct_outlines_come_from_the_active_snapshot(self):
        west = {'type': 'Polygon', 'coordinates': [ring(-400, -300, 10, 300), ring(-250, -50, -150, 50)]}
        east = {'type': 'MultiPolygon', 'coordinates': [[ring(10, -300, 400, -10)], [ring(10, 10, 400, 300)]]}
        far = {'type': 'Polygon', 'coordinates': [ring(400, -3300, 800, -3100)]}
        self.snapshot({PRECINCT_E: {'geometry': east}, PRECINCT_W: {'geometry': west}, PRECINCT_F: {'geometry': far}})
        found = C.build_context(self.db, box(*NORTH))['precincts']
        self.assertEqual([(p['id'], p['label']) for p in found], [(PRECINCT_W, PRECINCT_W), (PRECINCT_E, PRECINCT_E)])
        self.assertEqual(set(found[0]), {'id', 'label', 'rings'})
        self.assertEqual([len(p['rings']) for p in found], [2, 2])         # outer ring and hole; two parts
        for precinct, source in zip(found, (west['coordinates'], [r for part in east['coordinates'] for r in part])):
            for got, want in zip(precinct['rings'], source):
                self.assertEqual(len(got), len(want))
                self.assertEqual(got[0], got[-1])
                for point, corner in zip(got, want):
                    self.assert_point(point)
                    self.assertTrue(close(point, corner, 1e-5), (point, corner))   # the app's layer keeps 5 decimals
        self.assertEqual(json.loads(json.dumps(found)), found)
        everything = C.build_context(self.db, box(*WORLD))['precincts']
        self.assertEqual([p['id'] for p in everything], [PRECINCT_W, PRECINCT_E, PRECINCT_F])
        C.reset()
        self.assertEqual(C.build_context(self.db, box(*NORTH), data_dir=self.directory)['precincts'], found)
        Snapshot.objects.update(active=False)
        self.assertEqual(C.build_context(self.db, box(*NORTH))['precincts'], [])

    def test_no_outlines_without_a_v4_snapshot_or_readable_geometry(self):
        self.snapshot({PRECINCT_W: {'geometry': {'type': 'Polygon', 'coordinates': [ring(-400, -300, 10, 300)]}}},
                      schema='cu-summary-v3')
        self.assertEqual(C.build_context(self.db, box(*NORTH))['precincts'], [])
        Snapshot.objects.all().delete()
        shutil.rmtree(self.directory / 'snapshots')
        self.snapshot(None)                                                 # active, but without its geometry file
        with self.assertLogs('server.research.walk_context', 'WARNING'):
            context = C.build_context(self.db, box(*NORTH))
        self.assertEqual(context['precincts'], [])
        self.assertTrue(context['roads'] and context['homes'])

    # ---- repeatability and attach ---------------------------------------------------------------------------------
    def test_output_is_deterministic(self):
        self.snapshot({PRECINCT_W: {'geometry': {'type': 'Polygon', 'coordinates': [ring(-400, -300, 10, 300)]}}})
        first = json.dumps(C.build_context(self.db, box(*WORLD)))
        C.reset()
        v3_cache.forget()
        other = sqlite3.connect((self.directory / 'walk.sqlite3').as_uri() + '?mode=ro', uri=True)
        self.addCleanup(other.close)
        other.row_factory = sqlite3.Row                                     # as walk_store opens it
        self.assertEqual(json.dumps(C.build_context(other, box(*WORLD))), first)
        self.assertEqual(json.dumps(C.build_context(self.db, box(*WORLD))), first)

    def test_attach_sets_context_and_leaves_it_out_without_a_walk_store(self):
        raw = box(*NORTH)
        payload = {'bbox': raw, 'runs': []}
        self.assertIs(C.attach(payload), payload)
        self.assertEqual(payload['context'], C.build_context(self.db, raw))
        self.assertEqual(C.attach({'bbox': raw}, self.db)['context'], payload['context'])
        with tempfile.TemporaryDirectory() as empty, override_settings(DATA_DIR=Path(empty)):
            W.reset()
            try:
                with self.assertLogs('server.research.walk_context', 'INFO'):
                    self.assertEqual(C.attach({'bbox': raw}), {'bbox': raw})
            finally:
                W.reset()
