"""Multi-district server (Phase 2 Lane A): one process serves one district, chosen by CU_DISTRICT and read from
settings.DISTRICT. The district selector endpoint, the snapshot-key routes, the walk-store gate for a store built from
the source CSV, the walk window, the city spelling, the bounds and the projection zone. Synthetic data only."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from unittest import SkipTest, mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import resolve

from tools import build_walk as B
from tools import districts as D

from . import road_views, street_views, v3_area_views as v3a, v3_people_views as v3p
from . import walk_context as C
from . import walk_names as N
from . import walk_store as W
from . import walk_testkit as T
from . import walk_views
from .v3_http import BadRequest

LOOPBACK = dict(HTTP_HOST='localhost')
OR, CT, CO = D.get('or-05'), D.get('ct-01'), D.get('co-05')
PORTLAND = '-122.70,45.50,-122.67,45.53'          # 0.03 degrees square
COLORADO_SPRINGS = '-104.83,38.83,-104.80,38.86'


def touch(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(b'')


class DefaultDistrictTests(TestCase):
    def setUp(self):
        if os.environ.get('CU_DISTRICT'):
            raise SkipTest('CU_DISTRICT is set: these tests pin the default (co-05) process')

    def test_the_default_process_serves_colorado_and_its_snapshot_keys(self):
        from django.conf import settings
        self.assertEqual(settings.DISTRICT['id'], 'co-05')
        for kind in ('areas', 'geometry'):
            tail = ('registration/block.json' if kind == 'areas' else 'block/coarse.json')
            match = resolve('/api/v3/%s/co-cd005-0123456789ab/%s' % (kind, tail))
            self.assertIs(match.func, getattr(v3a, kind), kind)
            self.assertEqual(match.kwargs['snap'], 'co-cd005-0123456789ab')
            # another district's key (or a malformed one) falls through to the JSON catch-all, never the page shell
            other = Client(**LOOPBACK).get('/api/v3/%s/or-cd005-0123456789ab/%s' % (kind, tail))
            self.assertEqual(other.status_code, 404, kind)
            self.assertEqual(json.loads(other.content)['error'], 'Unknown v3 endpoint.')

    def test_the_districts_route_is_before_the_catch_all(self):
        self.assertIs(resolve('/api/v3/districts').func, v3a.districts)

    def test_the_cookie_names_are_unchanged_for_colorado(self):
        from django.conf import settings
        if os.environ.get('CU_COOKIE_PREFIX'):
            raise SkipTest('CU_COOKIE_PREFIX is set')
        self.assertEqual((settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME), ('sessionid', 'csrftoken'))


@override_settings(LOCAL_ADMIN_ACCESS=True, HOSTED=False, DEMO_MODE=False)
class DistrictListTests(TestCase):
    """GET /api/v3/districts against a throw-away local root."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.client = Client(**LOOPBACK)

    def build_co(self):
        touch(self.root / 'scores.sqlite3')
        touch(self.root / 'walk.sqlite3')
        (self.root / 'snapshots' / 'co-cd005-aaaaaaaaaaaa').mkdir(parents=True)
        touch(self.root / 'or-05' / 'walk.sqlite3')

    def listed(self, **settings_override):
        with override_settings(LOCAL_ROOT=self.root, DATA_DIR=self.root, **settings_override):
            response = self.client.get('/api/v3/districts')
        self.assertEqual(response.status_code, 200, response.content[:300])
        return response, json.loads(response.content)

    def test_colorado_is_ready_and_the_others_are_not_built(self):
        self.build_co()
        response, body = self.listed()
        self.assertEqual(body['schema'], 'v3-districts-1')
        self.assertEqual(body['current'], 'co-05')
        self.assertEqual([d['id'] for d in body['districts']], ['co-05', 'or-05', 'ct-01'])
        self.assertEqual({d['id']: d['ready'] for d in body['districts']}, {'co-05': True, 'or-05': False, 'ct-01': False})
        self.assertEqual({d['id']: d['port'] for d in body['districts']}, {'co-05': 8767, 'or-05': 8775, 'ct-01': 8777})
        first = body['districts'][0]
        self.assertEqual(set(first), {'id', 'label', 'short_label', 'state', 'port', 'ready', 'center', 'zoom'})
        self.assertEqual((first['label'], first['short_label'], first['state']), ('Colorado · District 05', 'District 5', 'CO'))
        self.assertEqual((first['center'], first['zoom']), ([-104.82, 38.86], 10))
        self.assertEqual(response['Cache-Control'], 'private, no-cache')

    def test_nothing_is_ready_in_an_empty_root_and_a_snapshot_alone_is_not_enough(self):
        _, body = self.listed()
        self.assertEqual([d['ready'] for d in body['districts']], [False, False, False])
        (self.root / 'snapshots' / 'co-cd005-aaaaaaaaaaaa').mkdir(parents=True)
        touch(self.root / 'walk.sqlite3')
        _, body = self.listed()
        self.assertFalse(body['districts'][0]['ready'])              # no score store
        touch(self.root / 'scores.sqlite3')
        _, body = self.listed()
        self.assertTrue(body['districts'][0]['ready'])
        (self.root / 'snapshots' / 'co-cd005-aaaaaaaaaaaa').rmdir()
        (self.root / 'snapshots' / 'or-cd005-bbbbbbbbbbbb').mkdir()  # another district's snapshot does not count
        _, body = self.listed()
        self.assertFalse(body['districts'][0]['ready'])

    def test_another_district_process_reads_its_own_data_dir_and_the_others_from_the_root(self):
        self.build_co()
        own = self.root / 'or-05'
        touch(own / 'scores.sqlite3')
        (own / 'snapshots' / 'or-cd005-bbbbbbbbbbbb').mkdir(parents=True)
        with override_settings(LOCAL_ROOT=self.root, DATA_DIR=own, DISTRICT=OR):
            body = json.loads(self.client.get('/api/v3/districts').content)
        self.assertEqual(body['current'], 'or-05')
        self.assertEqual({d['id']: d['ready'] for d in body['districts']}, {'co-05': True, 'or-05': True, 'ct-01': False})

    def test_it_is_a_signed_in_get_with_no_parameters_and_revalidates(self):
        self.build_co()
        response, _ = self.listed()
        with override_settings(LOCAL_ROOT=self.root, DATA_DIR=self.root):
            self.assertEqual(self.client.get('/api/v3/districts?x=1').status_code, 400)
            self.assertEqual(self.client.get('/api/v3/districts?demo=1').status_code, 400)
            self.assertEqual(self.client.post('/api/v3/districts').status_code, 405)
            self.assertEqual(Client(HTTP_HOST='localhost', REMOTE_ADDR='192.0.2.10').get('/api/v3/districts').status_code, 401)
            again = self.client.get('/api/v3/districts', HTTP_IF_NONE_MATCH=response['ETag'])
        self.assertEqual(again.status_code, 304)


@override_settings(LOCAL_ADMIN_ACCESS=True, HOSTED=False, DEMO_MODE=False)
class CsvBuiltWalkStoreTests(TestCase):
    """A walk store with meta person_fields = csv has no score store; every other check stays."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.stores = T.write_minimal_stores(Path(cls.temporary.name) / 'world')
        if not B.build(cls.stores.source_csv, cls.stores.directory, force=True)['replaced']:
            raise AssertionError('The synthetic walk store was not written.')
        W.reset()

    @classmethod
    def tearDownClass(cls):
        W.reset()
        cls.temporary.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.client = Client(**LOOPBACK)

    def copy_world(self, name, scores=True, csv=False):
        copy = Path(self.temporary.name) / name
        shutil.copytree(self.stores.directory, copy)
        if not scores:
            (copy / 'scores.sqlite3').unlink()
        if csv:
            self.edit(copy / 'walk.sqlite3', "INSERT INTO meta VALUES ('person_fields', 'csv')")
        return copy

    @staticmethod
    def edit(path, sql, *args):
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(sql, args)

    def get(self, directory, url):
        with override_settings(DATA_DIR=directory):
            return self.client.get(url)

    def test_a_csv_built_store_is_served_without_scores(self):
        copy = self.copy_world('csv', scores=False, csv=True)
        response = self.get(copy, '/api/v3/walk/options?level=precinct')
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertTrue(json.loads(response.content))
        listing = self.get(copy, '/api/v3/walk/list?ss=010')
        self.assertEqual(listing.status_code, 200, listing.content[:300])
        self.assertGreater(json.loads(listing.content)['totals']['voters'], 0)
        self.assertFalse((copy / 'scores.sqlite3').exists())          # a missing store is never created

    def test_the_same_world_without_the_csv_marker_is_a_503(self):
        copy = self.copy_world('no-marker', scores=False)
        response = self.get(copy, '/api/v3/walk/options?level=precinct')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.content)['error'], 'The local source stores are unavailable.')
        self.assertFalse((copy / 'scores.sqlite3').exists())

    def test_a_store_with_scores_and_no_marker_is_checked_as_before(self):
        copy = self.copy_world('with-scores')
        self.assertEqual(self.get(copy, '/api/v3/walk/options?level=precinct').status_code, 200)
        self.edit(copy / 'scores.sqlite3', "UPDATE metadata SET value=? WHERE key='w1'", json.dumps({'status': 'pending'}))
        self.assertEqual(self.get(copy, '/api/v3/walk/options?level=precinct').status_code, 409)

    def test_the_csv_marker_skips_only_the_score_checks(self):
        copy = self.copy_world('csv-scores-ignored', csv=True)      # scores.sqlite3 is there but not consulted
        self.edit(copy / 'scores.sqlite3', "UPDATE metadata SET value=? WHERE key='w1'", json.dumps({'status': 'pending'}))
        self.assertEqual(self.get(copy, '/api/v3/walk/options?level=precinct').status_code, 200)
        for store, key in (('contacts', 'source_sha256'), ('street', 'source_sha256')):
            other = self.copy_world('csv-other-' + store, scores=False, csv=True)
            self.edit(other / (store + '.sqlite3'), 'UPDATE metadata SET value=? WHERE key=?', json.dumps('c' * 64), key)
            response = self.get(other, '/api/v3/walk/options?level=precinct')
            self.assertEqual(response.status_code, 409, store)
            self.assertIn('different source', json.loads(response.content)['error'])
        other = self.copy_world('csv-other-walk', scores=False, csv=True)
        self.edit(other / 'walk.sqlite3', "UPDATE meta SET value=? WHERE key='source_sha256'", 'c' * 64)
        self.assertEqual(self.get(other, '/api/v3/walk/options?level=precinct').status_code, 409)
        schema = self.copy_world('csv-street-schema', scores=False, csv=True)
        self.edit(schema / 'street.sqlite3', "UPDATE metadata SET value=? WHERE key='schema_version'", json.dumps('0'))
        self.assertEqual(self.get(schema, '/api/v3/walk/options?level=precinct').status_code, 503)

    def test_the_walk_window_is_the_one_the_store_records(self):
        untouched = self.get(self.stores.directory, '/api/v3/walk/list?ss=010')
        self.assertEqual(untouched.status_code, 200, untouched.content[:300])
        self.assertEqual(json.loads(untouched.content)['window'], N.WINDOW)
        copy = self.copy_world('window')
        window = D.window(OR)
        self.assertNotEqual(window, N.WINDOW)
        self.edit(copy / 'walk.sqlite3', "UPDATE meta SET value=? WHERE key='window'", json.dumps(window))
        recorded = self.get(copy, '/api/v3/walk/list?ss=010')
        self.assertEqual(recorded.status_code, 200, recorded.content[:300])
        self.assertEqual(json.loads(recorded.content)['window'], window)
        self.assertEqual(json.loads(recorded.content)['window']['primaries'], list(N.PRIMARIES))
        # a store with no window row (built before the row existed) gets the Colorado contract window
        self.edit(copy / 'walk.sqlite3', "DELETE FROM meta WHERE key='window'")
        self.assertEqual(json.loads(self.get(copy, '/api/v3/walk/list?ss=010').content)['window'], N.WINDOW)

    def test_colorado_keeps_its_24_character_area_ids(self):
        if os.environ.get('CU_DISTRICT'):
            raise SkipTest('CU_DISTRICT is set')
        self.assertNotEqual(self.get(self.stores.directory, '/api/v3/walk/overview?precinct=' + 'x' * 24).status_code, 400)
        self.assertEqual(self.get(self.stores.directory, '/api/v3/walk/overview?precinct=' + 'x' * 25).status_code, 400)


@override_settings(LOCAL_ADMIN_ACCESS=True, HOSTED=False, DEMO_MODE=False)
class DistrictSettingTests(SimpleTestCase):
    def test_city_spelling_follows_the_district(self):
        with override_settings(DISTRICT=CT):
            self.assertEqual(walk_views.city_key('Town of Berlin'), 'BERLIN')
            self.assertEqual(walk_views.city_key('WINCHESTER CNTR'), 'WINCHESTER CENTER')
        with override_settings(DISTRICT=OR):
            self.assertEqual(walk_views.city_key('Mt. Angel'), 'MOUNT ANGEL')
        for text in ('Colo Spgs.', 'CO SPGS', 'colorado springs', 'GRN MTN FLS', 'Fountain'):
            self.assertEqual(walk_views.city_key(text), B.canonical_city(text), text)
        self.assertEqual(walk_views.city_key('Colo Spgs.'), 'COLORADO SPRINGS')

    def test_street_viewport_is_the_districts_box(self):
        factory = RequestFactory()

        def viewport(box):
            return street_views.viewport(factory.get('/', {'bbox': box, 'zoom': '16'}))
        with override_settings(DISTRICT=OR):
            self.assertEqual(viewport(PORTLAND), (-122.70, 45.50, -122.67, 45.53))
            with self.assertRaises(ValueError):
                viewport(COLORADO_SPRINGS)
        self.assertEqual(viewport(COLORADO_SPRINGS), (-104.83, 38.83, -104.80, 38.86))
        with self.assertRaises(ValueError):
            viewport(PORTLAND)

    def test_people_extent_message_names_the_state(self):
        with override_settings(DISTRICT=OR):
            self.assertEqual(v3p.parse_bbox(PORTLAND), (-122.70, 45.50, -122.67, 45.53))
            with self.assertRaisesMessage(BadRequest, 'Extent outside Oregon'):
                v3p.parse_bbox(COLORADO_SPRINGS)
        with override_settings(DISTRICT=CT):
            with self.assertRaisesMessage(BadRequest, 'Extent outside Connecticut'):
                v3p.parse_bbox(PORTLAND)
        with self.assertRaisesMessage(BadRequest, 'Extent outside Colorado'):
            v3p.parse_bbox(PORTLAND)

    def test_road_attribution_for_colorado_is_unchanged(self):
        self.assertEqual(road_views.ATTRIBUTION, 'U.S. Census TIGER/Line 2025 · El Paso County roads')

    def test_snapshot_regexes_and_bundle_checks_use_the_districts_prefix(self):
        from . import bundle_v4, v3_cache
        for regex in (bundle_v4.SNAPSHOT_RE, v3_cache.SNAPSHOT_RE):
            self.assertTrue(regex.fullmatch('co-cd005-0123456789ab'))
            self.assertFalse(regex.fullmatch('or-cd005-0123456789ab'))
            self.assertFalse(regex.fullmatch('co-cd005-0123456789a'))

    def test_the_projection_zone_is_the_districts(self):
        lines = [[[-122.6800, 45.5200], [-122.6795, 45.5203], [-122.6790, 45.5200]]]
        from tools import walk_geo
        for district, zone in ((OR, 26910), (CT, 26918), (CO, 26913)):
            seen = []
            real_metres, real_lonlat = walk_geo.to_metres, walk_geo.to_lonlat
            with override_settings(DISTRICT=district), \
                    mock.patch.object(walk_geo, 'to_metres', side_effect=lambda *a, f=real_metres: (seen.append(a[2]), f(*a))[1]), \
                    mock.patch.object(walk_geo, 'to_lonlat', side_effect=lambda *a, f=real_lonlat: (seen.append(a[2]), f(*a))[1]):
                out = C.simplified(lines, 0.0)
            self.assertEqual(seen, [zone, zone], district['id'])
            self.assertEqual(out, [[list(p) for p in lines[0]]], district['id'])          # tolerance 0: the round trip is the line


class DistrictProcessTests(SimpleTestCase):
    """CU_DISTRICT picks the district when the process starts: each case is a fresh interpreter."""
    PROBE = '''
import json, os
os.environ['DJANGO_SETTINGS_MODULE'] = 'server.settings'
import django
django.setup()
from django.conf import settings
from django.urls import resolve
from tools import districts as D
from server.research import walk_views
tail = {'areas': 'registration/block.json', 'geometry': 'block/coarse.json'}
print(json.dumps({
    'district': settings.DISTRICT['id'],
    'cookies': [settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME],
    'data_dir': os.path.relpath(D.data_dir(settings.DISTRICT, settings.LOCAL_ROOT), settings.BASE_DIR),
    'area_max': walk_views.AREA.split(',')[1].rstrip('}'),
    'routes': {key + '-' + prefix: resolve('/api/v3/%s/%s0123456789ab/%s' % (key, prefix, tail[key])).func.__name__
               for key in tail for prefix in ('co-cd005-', 'or-cd005-', 'ct-cd001-')
               if resolve('/api/v3/%s/%s0123456789ab/%s' % (key, prefix, tail[key])).func.__name__ != '<lambda>'},
}))
'''

    def probe(self, district):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as data:
            env = {k: v for k, v in os.environ.items() if k not in ('CU_DISTRICT', 'CU_COOKIE_PREFIX', 'CU_DATA_DIR')}
            env['CU_DATA_DIR'] = data
            if district:
                env['CU_DISTRICT'] = district
            done = subprocess.run([sys.executable, '-c', self.PROBE], cwd=root, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr[-600:])
        return json.loads(done.stdout.strip().splitlines()[-1])

    def test_each_district_has_its_own_cookies_data_dir_snapshot_keys_and_area_ids(self):
        co, orr, ct = self.probe(None), self.probe('or-05'), self.probe('ct-01')
        self.assertEqual((co['district'], orr['district'], ct['district']), ('co-05', 'or-05', 'ct-01'))
        self.assertEqual(co['cookies'], ['sessionid', 'csrftoken'])
        self.assertEqual(orr['cookies'], ['or05_sessionid', 'or05_csrftoken'])
        self.assertEqual(ct['cookies'], ['ct01_sessionid', 'ct01_csrftoken'])
        self.assertEqual((co['data_dir'], orr['data_dir'], ct['data_dir']), ('.local', os.path.join('.local', 'or-05'), os.path.join('.local', 'ct-01')))
        self.assertEqual(co['routes'], {'areas-co-cd005-': 'areas', 'geometry-co-cd005-': 'geometry'})
        self.assertEqual(orr['routes'], {'areas-or-cd005-': 'areas', 'geometry-or-cd005-': 'geometry'})
        self.assertEqual(ct['routes'], {'areas-ct-cd001-': 'areas', 'geometry-ct-cd001-': 'geometry'})
        self.assertEqual((co['area_max'], orr['area_max'], ct['area_max']), ('24', '24', '64'))

    def test_an_unknown_district_stops_the_process(self):
        root = Path(__file__).resolve().parents[2]
        env = {k: v for k, v in os.environ.items() if k not in ('CU_DISTRICT', 'CU_DATA_DIR')}
        env.update(CU_DISTRICT='xx-01', CU_DATA_DIR=tempfile.gettempdir(), DJANGO_SETTINGS_MODULE='server.settings')
        done = subprocess.run([sys.executable, '-c', 'import django; django.setup()'], cwd=root, env=env, capture_output=True,
                              text=True, timeout=120)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('Unknown district', done.stderr)


class StartGuardTests(SimpleTestCase):
    def test_serve_refuses_a_data_dir_built_from_another_source_file(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            with closing(sqlite3.connect(Path(folder) / 'street.sqlite3')) as db, db:
                db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)')
                db.execute('INSERT INTO metadata VALUES (?, ?)', ('source_sha256', json.dumps(CO['sha256'])))
            with override_settings(DISTRICT=OR, DATA_DIR=Path(folder)), \
                    mock.patch.dict(os.environ, {'CU_DISTRICT': 'or-05'}), \
                    mock.patch('waitress.serve', side_effect=AssertionError('the server started')):
                with self.assertRaisesMessage(CommandError, 'another source file than or-05'):
                    call_command('serve', '--no-warm')
