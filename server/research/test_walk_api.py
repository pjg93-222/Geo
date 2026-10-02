"""Walk list API: access rules, parameters and caps, the shape and content of every endpoint against the hand-computed
world of walk_testkit, saved lists, results, re-knock, and the store checks. Synthetic data only: the walk store is built
by tools/build_walk.py from the testkit's invented voters."""
import hashlib
import json
import shutil
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings

from tools import build_walk as B

from . import walk_names as N
from . import walk_store as W
from . import walk_testkit as T
from . import walk_views as V
from .models import Disposition, WalkList
from .street_views import local_only, local_only_post, response

LOOPBACK = dict(HTTP_HOST='localhost')
PROXY_HEADERS = ('HTTP_FORWARDED', 'HTTP_X_FORWARDED_FOR', 'HTTP_X_FORWARDED_HOST', 'HTTP_X_FORWARDED_PROTO')
JSON = 'application/json'
CS, FOUNTAIN = 'COLORADO SPRINGS', 'FOUNTAIN'
CS_SHOWN, FOUNTAIN_SHOWN = 'Colorado Springs', 'Fountain'   # how the API spells them
PRECINCT_W, PRECINCT_E, PRECINCT_F = '5200021301', '5200021302', '5200021417'
NO_STORE = 'no-store, private'

LIST_KEYS = {'schema', 'generated_on', 'list', 'query', 'window', 'totals', 'bbox', 'runs', 'context'}   # context: walk_context.py
TOTAL_KEYS = {'streets', 'stops', 'doors', 'voters', 'walk_m', 'minutes'}
RUN_KEYS = {'stop', 'street_id', 'street', 'side', 'precinct', 'from_house', 'to_house', 'walk_m', 'lr', 'path',
            'approach', 'doors'}
DOOR_KEYS = {'seq', 'house', 'unit', 'placed_by', 'lon', 'lat', 'voters'}
VOTER_KEYS = {'person_id', 'name', 'phone', 'pri', 'gen', 'abs', 'party', 'age', 'gender', 'reg_date', 'last'}
STREET_KEYS = {'id', 'label', 'city', 'zip', 'doors', 'people', 'even', 'odd'}


def build_world(directory):
    """The testkit's stores plus the walk store built from them. The invented world sits under some of the builder's
    quality gates on purpose (tools/test_walk_build.py checks the gates); these tests are about the API, so the store is
    written whatever the gates say."""
    stores = T.write_minimal_stores(directory)
    result = B.build(stores.source_csv, stores.directory, force=True)
    if not result['replaced']:
        raise AssertionError('The synthetic walk store was not written.')
    return stores


def voters_of(payload):
    return [v for run in payload['runs'] for door in run['doors'] for v in door['voters']]


def doors_of(payload):
    return [door for run in payload['runs'] for door in run['doors']]


def digits(text):
    return ''.join(c for c in text if c.isdigit())


def file_hashes(directory):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(directory).glob('*.sqlite3'))}


@override_settings(LOCAL_ADMIN_ACCESS=True, HOSTED=False, DEMO_MODE=False)
class WalkCase(TestCase):
    """A synthetic walk world in a temporary data directory, built once per class."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.stores = build_world(Path(cls.temporary.name) / 'world')
        cls.expected = cls.stores.expected
        cls.override = override_settings(DATA_DIR=cls.stores.directory)
        cls.override.enable()
        W.reset()

    @classmethod
    def tearDownClass(cls):
        W.reset()
        cls.override.disable()
        cls.temporary.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.client = Client(**LOOPBACK)

    # ---- helpers ---------------------------------------------------------------------------------------------
    def get(self, url, client=None, **extra):
        return (client or self.client).get(url, **extra)

    def json(self, url, status=200, client=None):
        response = self.get(url, client)
        self.assertEqual(response.status_code, status, response.content[:300])
        return json.loads(response.content)

    def post(self, url, data, client=None, **extra):
        return (client or self.client).post(url, data=json.dumps(data), content_type=JSON, **extra)

    def posted(self, url, data, status=200):
        response = self.post(url, data)
        self.assertEqual(response.status_code, status, response.content[:300])
        return json.loads(response.content)

    def error(self, response, status):
        self.assertEqual(response.status_code, status, response.content[:300])
        self.assertTrue(json.loads(response.content)['error'])
        return json.loads(response.content)['error']

    def street_id(self, label, city=CS):
        with closing(sqlite3.connect(self.stores.directory / 'walk.sqlite3')) as db:
            return db.execute('SELECT id FROM street WHERE display=? AND city=?', (label, city)).fetchone()[0]

    def people_on(self, label, city=None, parity=None, precinct=None):
        doors = [d for d in self.expected['doors'].values() if d['street'] == label
                 and (city is None or d['city'] == city) and (parity is None or d['parity'] == parity)
                 and (precinct is None or d['precinct'] == precinct)]
        return {pid for d in doors for pid in d['people']}

    def make_list(self, query, title='Test list', status=201):
        return self.posted('/api/v3/walk/lists', {'title': title, 'query': query}, status)

    def cedar_juniper(self):
        return '%d:both,%d:both' % (self.street_id('Cedar Heights Dr'), self.street_id('Juniper Ln'))


# ---- access, headers, parameters -----------------------------------------------------------------------------------

class WalkAccessTests(WalkCase):
    def endpoints(self):
        """(method, url, body) for every route, each valid when the rules allow it."""
        key = self.make_list({'streets': '%d:both' % self.street_id('Ridgeview Ct')})['list']['key']
        person = voters_of(self.json('/api/v3/walk/lists/' + key))[0]['person_id']
        return [('get', '/api/v3/walk/options?level=ss', None), ('get', '/api/v3/walk/streets?q=ced', None),
                ('get', '/api/v3/walk/list?streets=%d:both' % self.street_id('Ridgeview Ct'), None),
                ('get', '/api/v3/walk/overview?ss=010', None), ('get', '/api/v3/walk/lists', None),
                ('get', '/api/v3/walk/lists/' + key, None),
                ('post', '/api/v3/walk/match', {'rows': [{'street': 'Pine St'}]}),
                ('post', '/api/v3/walk/lists', {'title': 'Again', 'query': {'ss': '010'}}),
                ('post', '/api/v3/walk/dispositions', {'list': key, 'on': '2026-10-01',
                                                       'entries': [{'person_id': person, 'code': 'S'}]})]

    def call(self, client, method, url, body):
        if method == 'get':
            return client.get(url)
        return client.post(url, data=json.dumps(body), content_type=JSON)

    def test_every_route_answers_the_local_owner(self):
        for method, url, body in self.endpoints():
            self.assertIn(self.call(self.client, method, url, body).status_code, (200, 201), url)

    def test_only_a_direct_loopback_owner_gets_in(self):
        routes = self.endpoints()
        far = Client(HTTP_HOST='localhost', REMOTE_ADDR='192.0.2.10')
        for method, url, body in routes:
            self.assertEqual(self.call(far, method, url, body).status_code, 403, url)
            for header in PROXY_HEADERS:
                self.assertEqual(self.call(Client(HTTP_HOST='localhost', **{header: '127.0.0.1'}), method, url, body).status_code, 403, (header, url))
            with override_settings(HOSTED=True):
                self.assertEqual(self.call(self.client, method, url, body).status_code, 403, url)
            with override_settings(LOCAL_ADMIN_ACCESS=False):
                self.assertEqual(self.call(self.client, method, url, body).status_code, 403, url)
            self.assertEqual(self.call(Client(HTTP_HOST='testserver'), method, url, body).status_code, 403, url)      # a host that is not loopback

    def test_demo_mode_refuses_everything(self):
        routes = self.endpoints()
        with override_settings(DEMO_MODE=True):
            for method, url, body in routes:
                response = self.call(self.client, method, url, body)
                self.assertEqual(response.status_code, 403, url)
                self.assertEqual(response['X-CU-Demo'], '1')
        for method, url, body in routes:
            marked = url + ('&' if '?' in url else '?') + 'demo=1'
            self.assertEqual(self.call(self.client, method, marked, body).status_code, 403, marked)

    def test_wrong_methods_are_405_with_allow(self):
        for method, url, body in self.endpoints():
            if method == 'get':
                allow, others = 'GET', ('post', 'put', 'patch', 'delete', 'head')
            else:
                allow, others = 'POST', ('get', 'put', 'patch', 'delete', 'head')
            if url.endswith('/api/v3/walk/lists'):
                continue            # answers both; checked below
            for other in others:
                response = getattr(self.client, other)(url)
                self.assertEqual(response.status_code, 405, (other, url))
                self.assertEqual(response['Allow'], allow)
        for other in ('put', 'patch', 'delete', 'head'):
            response = getattr(self.client, other)('/api/v3/walk/lists')
            self.assertEqual((response.status_code, response['Allow']), (405, 'GET, POST'))
        self.assertEqual(Client(HTTP_HOST='localhost', REMOTE_ADDR='192.0.2.10').put('/api/v3/walk/lists').status_code, 403)

    def test_unknown_walk_paths_stay_json_404(self):
        for url in ('/api/v3/walk/nothing', '/api/v3/walk/lists/', '/api/v3/walk', '/api/v3/walk/options/'):
            response = self.get(url)
            self.assertEqual(response.status_code, 404, url)
            self.assertEqual(json.loads(response.content), {'error': 'Unknown v3 endpoint.'})

    def test_responses_are_no_store_json(self):
        for method, url, body in self.endpoints():
            response = self.call(self.client, method, url, body)
            self.assertEqual(response['Cache-Control'], NO_STORE, url)
            self.assertEqual(response['Pragma'], 'no-cache')
            self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
            self.assertTrue(response['Content-Type'].startswith('application/json'))
            self.assertFalse(response.has_header('ETag'))
        for url in ('/api/v3/walk/options', '/api/v3/walk/lists/0123456789', '/api/v3/walk/overview'):
            self.assertEqual(self.get(url)['Cache-Control'], NO_STORE, url)
        self.assertEqual(self.get('/api/v3/walk/options?level=ss', Client(HTTP_HOST='localhost', REMOTE_ADDR='192.0.2.10'))['Cache-Control'], NO_STORE)

    def test_posts_need_the_csrf_token(self):
        client = Client(HTTP_HOST='localhost', enforce_csrf_checks=True)
        key = self.make_list({'streets': '%d:both' % self.street_id('Ridgeview Ct')})['list']['key']
        person = voters_of(self.json('/api/v3/walk/lists/' + key))[0]['person_id']
        bodies = [('/api/v3/walk/match', {'rows': [{'street': 'Pine St'}]}, 200),
                  ('/api/v3/walk/lists', {'title': 'Csrf', 'query': {'ss': '011'}}, 201),
                  ('/api/v3/walk/dispositions', {'list': key, 'on': '2026-10-01', 'entries': [{'person_id': person, 'code': 'U'}]}, 200)]
        before = (WalkList.objects.count(), Disposition.objects.count())
        for url, body, _ in bodies:
            self.assertEqual(self.post(url, body, client).status_code, 403, url)
            self.assertEqual(self.post(url, body, client, HTTP_X_CSRFTOKEN='wrong').status_code, 403, url)
        self.assertEqual((WalkList.objects.count(), Disposition.objects.count()), before)
        token = json.loads(client.get('/api/auth/session').content)['csrf']
        for url, body, status in bodies:
            self.assertEqual(self.post(url, body, client, HTTP_X_CSRFTOKEN=token).status_code, status, url)

    def test_query_parameters_are_an_exact_list(self):
        key = self.make_list({'ss': '011'})['list']['key']
        ridge = '%d:both' % self.street_id('Ridgeview Ct')
        for url in ('/api/v3/walk/options?level=ss&extra=1', '/api/v3/walk/options', '/api/v3/walk/options?level=ss&level=sh',
                    '/api/v3/walk/options?level=zip', '/api/v3/walk/options?level=ss&ss=', '/api/v3/walk/options?level=sh&ss=0%2010',
                    '/api/v3/walk/streets?extra=1', '/api/v3/walk/streets?q=a&q=b', '/api/v3/walk/streets?ss=a/b',
                    '/api/v3/walk/streets?precinct=' + 'x' * 25,
                    '/api/v3/walk/list', '/api/v3/walk/list?streets=' + ridge + '&extra=1', '/api/v3/walk/list?streets=12',
                    '/api/v3/walk/list?streets=12:left', '/api/v3/walk/list?streets=a:even', '/api/v3/walk/list?streets=',
                    '/api/v3/walk/list?streets=%s&streets=%s' % (ridge, ridge), '/api/v3/walk/list?ss=010&ss=010',
                    '/api/v3/walk/overview', '/api/v3/walk/overview?ss=010&x=1', '/api/v3/walk/overview?street=1',
                    '/api/v3/walk/lists?x=1', '/api/v3/walk/lists/%s?x=1' % key):
            self.assertEqual(self.get(url).status_code, 400, url)
        for url in ('/api/v3/walk/match?x=1', '/api/v3/walk/lists?x=1', '/api/v3/walk/dispositions?x=1'):
            self.assertEqual(self.post(url, {}).status_code, 400, url)

    def test_post_bodies_are_validated(self):
        key = self.make_list({'ss': '011'})['list']['key']
        person = voters_of(self.json('/api/v3/walk/lists/' + key))[0]['person_id']
        bad = {
            '/api/v3/walk/match': [{}, {'rows': 'Pine St'}, {'rows': [{'street': 5}]}, {'rows': [{'street': 'x', 'extra': 'y'}]},
                                   {'rows': ['Pine St']}, {'rows': [{'street': 'x' * 121}]}, {'rows': [], 'bogus': 1},
                                   {'rows': [], 'ss': 10}, {'rows': [], 'ss': 'a b'}, {'rows': [{'street': 'x', 'zip': 'z' * 21}]}],
            '/api/v3/walk/lists': [{}, {'title': 'x'}, {'query': {'ss': '010'}}, {'title': '', 'query': {'ss': '010'}},
                                   {'title': ' ', 'query': {'ss': '010'}}, {'title': 'x' * 121, 'query': {'ss': '010'}},
                                   {'title': 5, 'query': {'ss': '010'}}, {'title': 'x', 'query': 'ss=010'},
                                   {'title': 'x', 'query': {'ss': '010', 'bogus': '1'}}, {'title': 'x', 'query': {'ss': 10}},
                                   {'title': 'x', 'query': {'streets': '1:left'}}, {'title': 'x', 'query': {}},
                                   {'title': 'x', 'query': {'ss': '010'}, 'extra': 1},
                                   {'title': 'x', 'from': key}, {'title': 'x', 'from': key, 'reknock': 'yes'},
                                   {'title': 'x', 'from': 5, 'reknock': True}, {'title': 'x', 'from': key, 'reknock': True, 'query': {}},
                                   {'title': 'bad\x00title', 'query': {'ss': '010'}}],
            '/api/v3/walk/dispositions': [{}, {'list': key}, {'list': key, 'on': '2026-10-01'},
                                          {'list': key, 'on': 'yesterday', 'entries': [{'person_id': person, 'code': 'S'}]},
                                          {'list': key, 'on': '2026-13-45', 'entries': [{'person_id': person, 'code': 'S'}]},
                                          {'list': key, 'on': '2026-10-01', 'entries': 'S'},
                                          {'list': key, 'on': '2026-10-01', 'entries': []},
                                          {'list': key, 'on': '2026-10-01', 'entries': ['S']},
                                          {'list': key, 'on': '2026-10-01', 'entries': [{'person_id': person}]},
                                          {'list': key, 'on': '2026-10-01', 'entries': [{'person_id': person, 'code': 'S', 'x': 1}]},
                                          {'list': key, 'on': '2026-10-01', 'entries': [{'person_id': person, 'code': 'X'}]},
                                          {'list': key, 'on': '2026-10-01', 'entries': [{'person_id': person, 'code': 5}]},
                                          {'list': key, 'on': '2026-10-01', 'entries': [{'person_id': 5, 'code': 'S'}]},
                                          {'list': 5, 'on': '2026-10-01', 'entries': [{'person_id': person, 'code': 'S'}]},
                                          {'list': key, 'on': '2026-10-01', 'entries': [{'person_id': person, 'code': 'S'}], 'x': 1}],
        }
        before = (WalkList.objects.count(), Disposition.objects.count())
        for url, bodies in bad.items():
            for body in bodies:
                response = self.post(url, body)
                self.assertIn(response.status_code, (400, 404), (url, body))
                self.assertTrue(json.loads(response.content)['error'])
        for url in bad:
            for raw in (b'', b'not json', b'[]', b'"x"', b'null', b'\xff\xfe'):
                response = self.client.post(url, data=raw, content_type=JSON)
                self.assertEqual(response.status_code, 400, (url, raw))
            self.assertEqual(self.client.post(url, data='a=b', content_type='application/x-www-form-urlencoded').status_code, 400)
            self.assertEqual(self.client.post(url, data={'a': 'b'}).status_code, 400)       # multipart
        self.assertEqual((WalkList.objects.count(), Disposition.objects.count()), before)

    def test_a_body_nested_too_deeply_is_a_400_not_a_crash(self):
        for url in ('/api/v3/walk/match', '/api/v3/walk/lists', '/api/v3/walk/dispositions'):
            for raw in (b'[' * 20000, b'{"rows":' * 5000 + b'1' + b'}' * 5000):
                self.assertEqual(self.client.post(url, data=raw, content_type=JSON).status_code, 400, url)

    def test_a_fault_of_ours_is_a_logged_json_500_not_a_bad_request(self):
        ridge = '/api/v3/walk/list?streets=%d:both' % self.street_id('Ridgeview Ct')
        with mock.patch.object(V.walk_route, 'plan_route', side_effect=KeyError('x')), self.assertLogs(V.log, 'ERROR') as logged:
            response = self.get(ridge)
        self.assertEqual(response.status_code, 500)
        self.assertIn('server log', json.loads(response.content)['error'])
        self.assertEqual(response['Cache-Control'], NO_STORE)
        self.assertTrue(any('walk_list failed' in line for line in logged.output))
        with mock.patch.object(V, 'plan_payload', side_effect=ZeroDivisionError), self.assertLogs(V.log, 'ERROR'):
            self.assertEqual(self.post('/api/v3/walk/lists', {'title': 'x', 'query': {'ss': '011'}}).status_code, 500)
        self.assertEqual(self.get(ridge).status_code, 200)

    def test_a_body_over_256_kb_is_refused(self):
        rows = [{'street': 'Cedar Heights Dr', 'city': 'x' * 50, 'zip': '80919'}] * 3000
        body = json.dumps({'rows': rows})
        self.assertGreater(len(body), V.MAX_BODY)
        self.assertEqual(self.client.post('/api/v3/walk/match', data=body, content_type=JSON).status_code, 400)
        small = json.dumps({'rows': rows[:20]})
        self.assertEqual(self.client.post('/api/v3/walk/match', data=small, content_type=JSON).status_code, 200)

    def test_requests_leave_the_stores_untouched(self):
        before = file_hashes(self.stores.directory)
        self.assertEqual(len(before), 5)
        for method, url, body in self.endpoints():
            self.call(self.client, method, url, body)
        self.assertEqual(file_hashes(self.stores.directory), before)

    def test_the_shared_gate_decorators(self):
        factory = RequestFactory()

        def request(method='post', remote='127.0.0.1', **extra):
            made = getattr(factory, method)('/x', REMOTE_ADDR=remote, **extra)
            made.user = SimpleNamespace(is_authenticated=True)
            made.local_admin_access = True
            return made

        @local_only_post
        def posting(request, key='none'):
            return response({'key': key})

        @local_only_post
        def failing(request):
            raise sqlite3.OperationalError('x') if request.GET.get('db') else ValueError('x')

        self.assertEqual(json.loads(posting(request(), key='k').content), {'key': 'k'})
        self.assertEqual(posting(request('post', '192.0.2.4')).status_code, 403)
        self.assertEqual(posting(request('post', HTTP_X_FORWARDED_FOR='127.0.0.1')).status_code, 403)
        answer = posting(request('get'))
        self.assertEqual((answer.status_code, answer['Allow']), (405, 'POST'))
        self.assertEqual(failing(request()).status_code, 400)
        self.assertEqual(failing(request('post', QUERY_STRING='db=1')).status_code, 503)
        gone = request()
        gone.user.is_authenticated = False
        self.assertEqual(posting(gone).status_code, 403)

        @local_only
        def reading(request, key='none'):
            return response({'key': key})

        self.assertEqual(json.loads(reading(request('get'), key='k').content), {'key': 'k'})
        self.assertEqual(reading(request('post')).status_code, 405)


# ---- options, streets, match ---------------------------------------------------------------------------------------

class WalkSearchTests(WalkCase):
    def combo_totals(self, **where):
        rows = [c for c in self.expected['combos'] if all(c[k] == v for k, v in where.items())]
        return sum(c['people'] for c in rows), sum(c['doors'] for c in rows)

    def option_rows(self, url):
        payload = self.json(url)
        self.assertEqual(set(payload), {'level', 'options'})
        for option in payload['options']:
            self.assertEqual(set(option), {'id', 'label', 'people', 'doors'})
        return payload

    def test_options_cascade_with_labels_and_combo_counts(self):
        top = self.option_rows('/api/v3/walk/options?level=ss')
        self.assertEqual(top['level'], 'ss')
        self.assertEqual([(o['id'], o['label']) for o in top['options']], [('010', 'SD 10'), ('011', 'SD 11')])
        for option in top['options']:
            self.assertEqual((option['people'], option['doors']), self.combo_totals(ss=option['id']))
        houses = self.option_rows('/api/v3/walk/options?level=sh&ss=010')
        self.assertEqual([(o['id'], o['label']) for o in houses['options']], [('018', 'HD 18'), ('019', 'HD 19')])
        for option in houses['options']:
            self.assertEqual((option['people'], option['doors']), self.combo_totals(sh=option['id']))
        self.assertEqual([o['id'] for o in self.option_rows('/api/v3/walk/options?level=sh')['options']], ['018', '019', '021'])
        precincts = self.option_rows('/api/v3/walk/options?level=precinct&ss=010&sh=019')
        self.assertEqual([(o['id'], o['label']) for o in precincts['options']], [(PRECINCT_E, PRECINCT_E)])
        self.assertEqual((precincts['options'][0]['people'], precincts['options'][0]['doors']), self.combo_totals(precinct=PRECINCT_E))
        self.assertEqual([o['id'] for o in self.option_rows('/api/v3/walk/options?level=precinct&ss=010')['options']], [PRECINCT_W, PRECINCT_E])
        self.assertEqual(self.option_rows('/api/v3/walk/options?level=precinct&ss=011&sh=018')['options'], [])
        self.assertEqual(sum(o['people'] for o in top['options']), self.expected['counts']['people'])

    def test_street_search_ranks_and_counts(self):
        found = self.json('/api/v3/walk/streets?q=ced')['streets']
        self.assertEqual(len(found), 1)
        cedar = next(s for s in self.expected['streets'] if s['street'] == 'Cedar Heights Dr')
        self.assertEqual(found[0], {'id': self.street_id('Cedar Heights Dr'), 'label': 'Cedar Heights Dr', 'city': CS_SHOWN,
                                    'zip': '80919', 'doors': cedar['doors'], 'people': cedar['people'],
                                    'even': cedar['doors_even'], 'odd': cedar['doors_odd']})
        for text in ('cedar', 'Cedar Heights', 'cedar hei', 'heights', 'Cedar Heights Drive', 'N CEDAR HEIGHTS DR', 'cedar  heights'):
            self.assertEqual([s['label'] for s in self.json('/api/v3/walk/streets?q=' + text.replace(' ', '%20'))['streets']],
                             ['Cedar Heights Dr'], text)
        pines = self.json('/api/v3/walk/streets?q=pine')['streets']
        self.assertEqual([(s['label'], s['city']) for s in pines], [('Pine St', CS_SHOWN), ('Pine St', FOUNTAIN_SHOWN)])
        self.assertEqual(self.json('/api/v3/walk/streets?q=zzyzx')['streets'], [])
        self.assertEqual(self.json('/api/v3/walk/streets?q=%20%20')['streets'][0]['label'], 'Cedar Heights Dr')
        for street in self.json('/api/v3/walk/streets?q=ju')['streets']:
            self.assertEqual(set(street), STREET_KEYS)

    def test_street_search_without_text_lists_the_busiest_streets_of_the_area(self):
        everything = self.json('/api/v3/walk/streets')['streets']
        self.assertEqual([(s['label'], s['city']) for s in everything], [
            ('Cedar Heights Dr', CS_SHOWN), ('Ridgeview Ct', CS_SHOWN), ('Juniper Ln', CS_SHOWN), ('Pine St', CS_SHOWN), ('Pine St', FOUNTAIN_SHOWN)])
        self.assertEqual([s['doors'] for s in everything], [16, 4, 3, 2, 2])
        in_f = self.json('/api/v3/walk/streets?precinct=' + PRECINCT_F)['streets']
        self.assertEqual([(s['label'], s['city'], s['doors'], s['people']) for s in in_f], [('Pine St', FOUNTAIN_SHOWN, 2, 4)])
        in_e = self.json('/api/v3/walk/streets?ss=010&sh=019&precinct=%s' % PRECINCT_E)['streets']
        self.assertEqual([(s['label'], s['city'], s['doors'], s['people']) for s in in_e], [
            ('Cedar Heights Dr', CS_SHOWN, 6, 7), ('Pine St', CS_SHOWN, 2, 3)])
        pine = self.json('/api/v3/walk/streets?q=pine&precinct=' + PRECINCT_E)['streets']
        self.assertEqual([(s['city'], s['doors'], s['even'], s['odd']) for s in pine], [(CS_SHOWN, 2, 1, 1)])
        self.assertEqual(self.json('/api/v3/walk/streets?q=ridgeview&precinct=' + PRECINCT_E)['streets'], [])

    def test_street_search_counts_even_odd_and_doors_with_units(self):
        by_street = {(s['street'], s['city']): s for s in self.expected['streets']}
        for street in self.json('/api/v3/walk/streets')['streets']:
            want = by_street[(street['label'], street['city'].upper())]
            self.assertEqual((street['doors'], street['people'], street['even'], street['odd']),
                             (want['doors'], want['people'], want['doors_even'], want['doors_odd']))
        self.assertEqual(self.expected['doors']['d12']['stop'], self.expected['doors']['d13']['stop'])    # one stop, two doors

    def test_street_search_text_is_capped(self):
        self.assertEqual(self.get('/api/v3/walk/streets?q=' + 'a' * 60).status_code, 200)
        self.error(self.get('/api/v3/walk/streets?q=' + 'a' * 61), 400)

    def test_street_search_limits(self):
        self.assertEqual(len(self.json('/api/v3/walk/streets?q=pine')['streets']), 2)
        with mock.patch.object(V, 'FOUND_WITH_TEXT', 1), mock.patch.object(V, 'FOUND_WITHOUT_TEXT', 3):
            self.assertEqual(len(self.json('/api/v3/walk/streets?q=pine')['streets']), 1)
            self.assertEqual(len(self.json('/api/v3/walk/streets')['streets']), 3)

    def match(self, rows, **area):
        payload = self.posted('/api/v3/walk/match', {'rows': rows, **area})
        self.assertEqual(set(payload), {'rows'})
        self.assertEqual(len(payload['rows']), len(rows))
        for number, row in enumerate(payload['rows'], 1):
            self.assertEqual(set(row), {'row', 'input', 'side', 'status', 'candidates', 'suggestions'})
            self.assertEqual(row['row'], number)
            for street in row['candidates']:
                self.assertEqual(set(street), STREET_KEYS)
        return payload['rows']

    def test_match_statuses(self):
        rows = self.match([
            {'street': 'Cedar Heights Drive'}, {'street': 'Pine Street', 'side': 'evens'}, {'street': 'Pine St', 'city': 'Fountain'},
            {'street': 'Cedar Dr'}, {'street': 'Zzyzx Rd'}, {'street': 'Cedr Heights Dr', 'side': 'Odd'},
            {'street': 'Ridgeview Court', 'side': 'both'}, {'street': ''}, {'street': ' Juniper   LANE ', 'side': 'e'},
            {'street': 'Pine St', 'zip': '80920'}, {'street': 'Pine St', 'city': 'Monument'}, {'street': 'Ridgeview'},
            {'street': 'Cedar Heights'}])
        cedar, pines, fountain = rows[0], rows[1], rows[2]
        self.assertEqual((cedar['status'], cedar['input'], cedar['side'], cedar['suggestions']), ('matched', 'Cedar Heights Drive', 'both', []))
        self.assertEqual([c['label'] for c in cedar['candidates']], ['Cedar Heights Dr'])
        self.assertEqual((pines['status'], pines['side']), ('pick', 'even'))
        self.assertEqual([(c['city'], c['zip']) for c in pines['candidates']], [(CS_SHOWN, '80920'), (FOUNTAIN_SHOWN, '80817')])
        self.assertEqual((fountain['status'], [c['city'] for c in fountain['candidates']]), ('matched', [FOUNTAIN_SHOWN]))
        self.assertEqual((rows[3]['status'], rows[3]['candidates'], rows[3]['suggestions']), ('none', [], ['Cedar Heights Dr']))
        self.assertEqual((rows[4]['status'], rows[4]['candidates'], rows[4]['suggestions']), ('none', [], []))
        self.assertEqual((rows[5]['status'], rows[5]['side'], rows[5]['candidates'][0]['label']), ('matched', 'odd', 'Cedar Heights Dr'))
        self.assertEqual((rows[6]['status'], rows[6]['candidates'][0]['label']), ('matched', 'Ridgeview Ct'))
        self.assertEqual((rows[7]['status'], rows[7]['input']), ('none', ''))
        self.assertEqual((rows[8]['status'], rows[8]['input'], rows[8]['side']), ('matched', 'Juniper   LANE', 'even'))
        self.assertEqual((rows[9]['status'], rows[9]['candidates'][0]['zip']), ('matched', '80920'))
        self.assertEqual((rows[10]['status'], len(rows[10]['candidates'])), ('pick', 2))      # a city the street is not in settles nothing
        self.assertEqual((rows[11]['status'], rows[11]['candidates'], rows[11]['suggestions']), ('none', [], ['Ridgeview Ct']))   # a lone 0.89 reading is not taken
        self.assertEqual((rows[12]['status'], rows[12]['candidates'][0]['label']), ('matched', 'Cedar Heights Dr'))       # 0.93 is
        self.assertEqual(self.match([]), [])

    def test_match_candidates_carry_counts(self):
        cedar = next(s for s in self.expected['streets'] if s['street'] == 'Cedar Heights Dr')
        row = self.match([{'street': 'cedar heights dr', 'side': 'even'}])[0]
        self.assertEqual({k: row['candidates'][0][k] for k in ('doors', 'people', 'even', 'odd')},
                         {'doors': cedar['doors'], 'people': cedar['people'], 'even': cedar['doors_even'], 'odd': cedar['doors_odd']})

    def test_match_is_restricted_to_the_area(self):
        row = self.match([{'street': 'Pine St'}], precinct=PRECINCT_F)[0]
        self.assertEqual((row['status'], [(c['city'], c['doors']) for c in row['candidates']]), ('matched', [(FOUNTAIN_SHOWN, 2)]))
        row = self.match([{'street': 'Cedar Heights Dr'}], ss='011')[0]
        self.assertEqual((row['status'], row['candidates'], row['suggestions']), ('none', [], []))
        row = self.match([{'street': 'Cedar Heights Dr'}], ss='010', sh='019')[0]
        self.assertEqual((row['status'], row['candidates'][0]['people']), ('matched', 7))

    def test_match_takes_at_most_500_rows(self):
        rows = [{'street': 'Pine St'}] * 500
        self.assertEqual(len(self.match(rows)), 500)
        self.error(self.post('/api/v3/walk/match', {'rows': rows + [{'street': 'Pine St'}]}), 400)


class DecideTests(SimpleTestCase):
    """The rule that turns ranked readings of an uploaded street into matched, pick or none."""

    @staticmethod
    def street(ident, display, city='COLORADO SPRINGS', zip_code='80903'):
        return W.Street((ident, N.name_key(display), city, zip_code, display))

    def setUp(self):
        self.a, self.b = self.street(1, 'N Main St'), self.street(2, 'S Main St')
        self.c = self.street(3, 'Main Ave', 'FOUNTAIN', '80817')
        self.doors = {1: 5, 2: 9, 3: 1}

    def decide(self, hits, city='', zip_code=''):
        return V.decide(hits, city, zip_code, self.doors)

    def test_a_single_clear_winner_and_nothing_less_is_matched(self):
        self.assertEqual(self.decide([(0.95, self.a)]), ([self.a], []))
        self.assertEqual(self.decide([(0.92, self.a), (0.80, self.c)]), ([self.a], []))
        self.assertEqual(self.decide([(0.91, self.a)]), ([], ['N Main St']))
        self.assertEqual(self.decide([(0.87, self.a), (0.80, self.c)]), ([], ['N Main St', 'Main Ave']))

    def test_readings_within_five_hundredths_of_the_best_are_a_choice(self):
        picked, labels = self.decide([(0.94, self.a), (0.93, self.b), (0.80, self.c)])
        self.assertEqual(([s.id for s in picked], labels), ([2, 1], []))          # more doors first
        self.assertEqual(self.decide([(0.94, self.a), (0.889, self.b)]), ([self.a], []))
        self.assertEqual(len(self.decide([(0.94, self.a), (0.8901, self.b)])[0]), 2)
        self.assertEqual([s.id for s in self.decide([(0.87, self.a), (0.86, self.b)])[0]], [2, 1])
        self.assertEqual([s.id for s in self.decide([(0.81, self.a), (0.80, self.c)])[0]], [1, 3])

    def test_weak_readings_are_only_suggestions(self):
        self.assertEqual(self.decide([(0.79, self.a), (0.78, self.b)]), ([], ['N Main St', 'S Main St']))
        self.assertEqual(self.decide([(0.7, self.a), (0.65, self.b), (0.61, self.c), (0.6, self.a)]),
                         ([], ['N Main St', 'S Main St', 'Main Ave']))
        self.assertEqual(self.decide([]), ([], []))

    def test_an_exact_name_in_several_cities_is_settled_by_the_city_and_zip_columns(self):
        east, north = self.street(4, 'Pine St', 'FOUNTAIN', '80817'), self.street(5, 'Pine St', 'COLORADO SPRINGS', '80920')
        both = [(1.0, east), (1.0, north)]
        self.assertEqual(self.decide(both)[0], [north, east])                    # equal doors: by city
        self.assertEqual(self.decide(both, city='Fountain')[0], [east])
        self.assertEqual(self.decide(both, city='Colo. Spgs')[0], [north])
        self.assertEqual(self.decide(both, zip_code='80920-1234')[0], [north])
        self.assertEqual(len(self.decide(both, city='Monument', zip_code='80000')[0]), 2)
        self.assertEqual(self.decide(both, city='Fountain', zip_code='80920')[0], [east])      # the city wins; zip only breaks ties

    def test_a_pick_offers_at_most_eight_places(self):
        streets = [self.street(10 + n, 'Pine St', 'CITY %d' % n) for n in range(12)]
        self.assertEqual(len(self.decide([(1.0, s) for s in streets])[0]), V.MAX_PLACES)

    def test_city_spellings_are_the_builders(self):
        samples = ['CO SPGS', 'Colo. Springs', 'colorado springs', 'Colorado Spgs', 'GRN MTN FLS', "Manitou Spg's", 'Ft Carson',
                   'Fountain', '  Monument ', 'Co Springs', 'COLO SPRINGS CO']
        for text in samples:
            self.assertEqual(V.city_key(text), B.canonical_city(text), text)


class ListHelperTests(SimpleTestCase):
    def test_units_sort_naturally_with_no_unit_first(self):
        units = ['Unit 3B', 'Apt 10', '', 'Apt A', 'Apt 2', '#12', 'apt 9']
        self.assertEqual(sorted(units, key=V.unit_key), ['', '#12', 'Apt 2', 'apt 9', 'Apt 10', 'Apt A', 'Unit 3B'])

    def test_an_address_without_a_number_is_labelled(self):
        self.assertEqual((V.house_label('1112'), V.house_label(''), V.house_label(None)), ('1112', '(no number)', '(no number)'))

    def test_picks_merge_and_are_capped(self):
        self.assertEqual(V.parse_picks('3:even,5:both,3:odd,5:even'), [(3, 'both'), (5, 'both')])
        self.assertEqual(V.parse_picks('7:odd,2:even'), [(7, 'odd'), (2, 'even')])
        with self.assertRaises(V.Refused):
            V.parse_picks(','.join('%d:both' % n for n in range(1, V.MAX_STREETS + 2)))
        self.assertEqual(len(V.parse_picks(','.join('%d:both' % n for n in range(1, V.MAX_STREETS + 1)))), V.MAX_STREETS)


# ---- the list itself -----------------------------------------------------------------------------------------------

class WalkListTests(WalkCase):
    def list(self, streets='', **area):
        query = '&'.join(['streets=' + streets] if streets else []) + ''.join('&%s=%s' % kv for kv in area.items())
        return self.json('/api/v3/walk/list?' + query.lstrip('&'))

    def match(self, rows):
        return self.posted('/api/v3/walk/match', {'rows': rows})['rows']

    def check_shape(self, payload):
        self.assertEqual(set(payload), LIST_KEYS)
        self.assertEqual(payload['schema'], 'walk-list-1')
        self.assertEqual(payload['generated_on'], date.today().isoformat())
        self.assertEqual(set(payload['list']), {'key', 'title', 'kind', 'created_at'})
        self.assertEqual(payload['window'], {'primaries': list(N.PRIMARIES), 'generals': list(N.GENERALS), 'abs_rule': 'Abs = mail ballots'})
        self.assertEqual(set(payload['totals']), TOTAL_KEYS)
        self.assertEqual(len(payload['bbox']), 4)
        for run in payload['runs']:
            self.assertEqual(set(run), RUN_KEYS)
            self.assertIn(run['side'], ('even', 'odd'))
            self.assertIn(run['lr'], (1, -1, 0))
            for point in run['path'] + run['approach']:
                self.assertEqual(len(point), 2)
            for door in run['doors']:
                self.assertEqual(set(door), DOOR_KEYS)
                self.assertIn(door['placed_by'], ('geocode', 'fuzzy', 'nearest', 'interpolated'))
                for voter in door['voters']:
                    self.assertEqual(set(voter), VOTER_KEYS)
                    for tally in ('pri', 'gen', 'abs'):
                        self.assertEqual(set(voter[tally]), {'voted', 'eligible'})

    def test_shape_totals_and_numbering(self):
        payload = self.list(self.cedar_juniper())
        self.check_shape(payload)
        self.assertEqual(payload['list'], {'key': None, 'title': '', 'kind': 'standard', 'created_at': None})
        self.assertEqual(payload['query'], {'streets': self.cedar_juniper()})
        runs, doors, voters = payload['runs'], doors_of(payload), voters_of(payload)
        self.assertEqual([run['stop'] for run in runs], list(range(1, len(runs) + 1)))
        self.assertEqual([door['seq'] for door in doors], list(range(1, len(doors) + 1)))
        self.assertEqual(runs[0]['approach'], [])
        self.assertTrue(all(run['approach'] for run in runs[1:]))
        totals = payload['totals']
        self.assertEqual((totals['streets'], totals['stops'], totals['doors'], totals['voters']), (2, len(runs), len(doors), len(voters)))
        self.assertEqual((len(doors), len(voters)), (19, 26))
        self.assertGreater(totals['walk_m'], sum(run['walk_m'] for run in runs))       # the walks between the runs come on top
        self.assertEqual(totals['minutes'], totals['doors'] * 2 + totals['walk_m'] / 80)
        west, south, east, north = payload['bbox']
        for lon, lat in [(d['lon'], d['lat']) for d in doors] + [p for run in runs for p in run['path'] + run['approach']]:
            self.assertTrue(west <= lon <= east and south <= lat <= north, (lon, lat, payload['bbox']))

    def test_every_voter_of_the_streets_is_present_once_with_the_stored_facts(self):
        payload = self.list(self.cedar_juniper())
        want = self.people_on('Cedar Heights Dr') | self.people_on('Juniper Ln')
        voters = voters_of(payload)
        self.assertEqual(len(voters), len(want))
        self.assertEqual({v['person_id'] for v in voters}, want)
        for voter in voters:
            truth = self.expected['people'][voter['person_id']]
            self.assertEqual(voter['name'], truth['name'])
            self.assertEqual(voter['phone'], digits(truth['wireless']) or digits(truth['phone']))
            for tally in ('pri', 'gen', 'abs'):
                self.assertEqual(voter[tally], truth[tally], (truth['name'], tally))
            self.assertEqual((voter['party'], voter['age'], voter['gender'], voter['reg_date']),
                             (truth['party'], truth['age'], truth['gender'], truth['reg_date']))
            self.assertIsNone(voter['last'])
        self.assertTrue(any(v['phone'] for v in voters) and any(not v['phone'] for v in voters))

    def test_doors_are_stop_and_unit_groups_with_voters_by_name(self):
        payload = self.list('%d:both' % self.street_id('Cedar Heights Dr'))
        by_address = {(d['house'], d['unit']): d for d in doors_of(payload)}
        self.assertEqual(len(by_address), 16)
        self.assertEqual({(h, u) for h, u in by_address if h == '1285'}, {('1285', 'Apt A'), ('1285', 'Apt B')})
        door = by_address[('1132', '')]
        self.assertEqual([v['name'] for v in door['voters']], ['Elena Muñoz', 'José Muñoz', 'Tobias R Halvorsen'])
        self.assertEqual(by_address[('1136', '')]['placed_by'], 'interpolated')
        self.assertEqual(by_address[('1112', '')]['placed_by'], 'geocode')
        self.assertEqual(by_address[('1350A', '')]['house'], '1350A')
        for key, truth in self.expected['doors'].items():
            if truth['street'] != 'Cedar Heights Dr':
                continue
            door = by_address[(truth['house'], 'Apt ' + truth['unit'] if truth['unit'] else '')]
            self.assertEqual({v['person_id'] for v in door['voters']}, set(truth['people']), key)
            self.assertEqual(door['placed_by'], truth['placed_by'], key)
        for door in by_address.values():
            names = [v['name'].casefold() for v in door['voters']]
            self.assertEqual(names, sorted(names))

    def test_the_interpolated_door_sits_between_its_neighbours(self):
        doors = {d['house']: d for d in doors_of(self.list('%d:even' % self.street_id('Cedar Heights Dr')))}
        lost, before, after = doors['1136'], doors['1132'], doors['1152']
        self.assertEqual(lost['placed_by'], 'interpolated')
        low, high = sorted((before['lon'], after['lon']))          # along the street; the point itself is on the centreline
        self.assertTrue(low <= lost['lon'] <= high)
        self.assertIn(lost['voters'][0]['person_id'], {p for p in self.expected['doors']['d03']['people']})

    def test_sides_and_areas_narrow_the_voters(self):
        cedar = self.street_id('Cedar Heights Dr')
        for side, parity in (('even', 'even'), ('odd', 'odd')):
            voters = {v['person_id'] for v in voters_of(self.list('%d:%s' % (cedar, side)))}
            self.assertEqual(voters, self.people_on('Cedar Heights Dr', parity=parity))
            for run in self.list('%d:%s' % (cedar, side))['runs']:
                self.assertEqual(run['side'], side)
        merged = self.list('%d:even,%d:odd' % (cedar, cedar))
        self.assertEqual(merged['query'], {'streets': '%d:both' % cedar})
        self.assertEqual({v['person_id'] for v in voters_of(merged)}, self.people_on('Cedar Heights Dr'))
        in_e = self.list('%d:both' % cedar, precinct=PRECINCT_E)
        self.assertEqual({v['person_id'] for v in voters_of(in_e)}, self.people_on('Cedar Heights Dr', precinct=PRECINCT_E))
        self.assertEqual(in_e['query'], {'streets': '%d:both' % cedar, 'precinct': PRECINCT_E})
        self.assertEqual({r['precinct'] for r in in_e['runs']}, {PRECINCT_E})
        self.error(self.get('/api/v3/walk/list?streets=%d:both&ss=011' % cedar), 400)

    def test_an_area_alone_lists_everyone_in_it(self):
        payload = self.list(ss='011')
        self.assertEqual({v['person_id'] for v in voters_of(payload)}, self.people_on('Pine St', city=FOUNTAIN))
        self.assertEqual(payload['query'], {'ss': '011'})
        both = self.list(sh='019', precinct=PRECINCT_E)
        self.assertEqual({v['person_id'] for v in voters_of(both)},
                         self.people_on('Cedar Heights Dr', precinct=PRECINCT_E) | self.people_on('Pine St', city=CS, precinct=PRECINCT_E))

    def test_runs_are_one_street_side_in_one_precinct(self):
        payload = self.list('%d:both' % self.street_id('Cedar Heights Dr'))
        want = [r for r in self.expected['runs'] if r['street'] == 'Cedar Heights Dr']
        got = [(r['side'], r['precinct'], sorted(d['house'] + (' ' + d['unit'] if d['unit'] else '') for d in r['doors'])) for r in payload['runs']]
        self.assertEqual(len(got), len(want))
        for run in want:
            doors = sorted(self.expected['doors'][d]['house'] + (' Apt ' + self.expected['doors'][d]['unit'] if self.expected['doors'][d]['unit'] else '') for d in run['doors'])
            self.assertIn((run['parity'], run['precinct'], doors), got)
        for run in payload['runs']:
            numbers = [int(''.join(c for c in d['house'] if c.isdigit())) for d in run['doors']]
            self.assertEqual(numbers, sorted(numbers) if numbers[0] <= numbers[-1] else sorted(numbers, reverse=True))
            self.assertEqual((run['from_house'], run['to_house']), (run['doors'][0]['house'], run['doors'][-1]['house']))

    def test_the_order_is_deterministic(self):
        first = self.list(self.cedar_juniper() + ',%d:both' % self.street_id('Ridgeview Ct'))
        for _ in range(3):
            self.assertEqual(self.list(self.cedar_juniper() + ',%d:both' % self.street_id('Ridgeview Ct')), first)
        again = self.list('%d:both,%s' % (self.street_id('Ridgeview Ct'), self.cedar_juniper()))
        self.assertEqual({v['person_id'] for v in voters_of(again)}, {v['person_id'] for v in voters_of(first)})
        self.assertEqual(len(doors_of(again)), len(doors_of(first)))

    def test_a_stop_of_two_precincts_becomes_two_runs(self):
        copy = Path(self.temporary.name) / 'split'
        shutil.copytree(self.stores.directory, copy)
        mover = next(pid for pid, p in self.expected['people'].items() if p['key'] == 'p05')       # Tobias R Halvorsen at 1132
        with closing(sqlite3.connect(copy / 'walk.sqlite3')) as db, db:
            db.execute('UPDATE person SET precinct=? WHERE person_id=?', (PRECINCT_E, mover))
        with override_settings(DATA_DIR=copy):
            payload = self.list('%d:even' % self.street_id('Cedar Heights Dr'))
        self.assertEqual({v['person_id'] for v in voters_of(payload)}, self.people_on('Cedar Heights Dr', parity='even'))
        houses = [(run['precinct'], [v['person_id'] for v in door['voters']]) for run in payload['runs'] for door in run['doors'] if door['house'] == '1132']
        self.assertEqual(len(houses), 2)
        by_precinct = dict(houses)
        self.assertEqual(by_precinct[PRECINCT_E], [mover])
        self.assertEqual(len(by_precinct[PRECINCT_W]), 2)
        self.assertEqual([door['seq'] for door in doors_of(payload)], list(range(1, len(doors_of(payload)) + 1)))

    def test_stops_without_a_number_are_listed_only_for_both_sides(self):
        copy = self.edited_world('nullparity', "UPDATE stop SET parity=NULL, house_int=NULL, house='' WHERE street_id=? AND house='1119'",
                                 self.street_id('Cedar Heights Dr'))
        cedar = self.street_id('Cedar Heights Dr')
        with override_settings(DATA_DIR=copy):
            everything = self.list('%d:both' % cedar)
            even, odd = self.list('%d:even' % cedar), self.list('%d:odd' % cedar)
            streets = self.json('/api/v3/walk/streets?q=cedar')['streets'][0]
            area = self.list(precinct=PRECINCT_W)
        lone = self.expected['doors']['d05']['people']
        self.assertTrue(set(lone) <= {v['person_id'] for v in voters_of(everything)})
        self.assertTrue(set(lone) <= {v['person_id'] for v in voters_of(area)})
        self.assertFalse(set(lone) & {v['person_id'] for v in voters_of(even) + voters_of(odd)})
        self.assertEqual(len(voters_of(even)) + len(voters_of(odd)), len(voters_of(everything)) - len(lone))
        self.assertEqual((streets['doors'], streets['even'] + streets['odd']), (16, 15))
        for payload in (everything, area):
            unnumbered = [door for door in doors_of(payload) if door['house'] == V.NO_NUMBER]
            self.assertEqual([[v['person_id'] for v in door['voters']] for door in unnumbered], [lone])
            self.assertEqual(unnumbered[0]['unit'], '')
            self.assertTrue(all(door['house'] for door in doors_of(payload)))
            for run in payload['runs']:
                self.assertEqual((run['from_house'], run['to_house']), (run['doors'][0]['house'], run['doors'][-1]['house']))

    def test_a_street_of_unnumbered_stops_reads_no_number_in_its_run_headers(self):
        fountain = self.street_id('Pine St', FOUNTAIN)
        copy = self.edited_world('nonumbers', "UPDATE stop SET parity=NULL, house_int=NULL, house='' WHERE street_id=?", fountain)
        with override_settings(DATA_DIR=copy):
            payload = self.list('%d:both' % fountain)
            for side in ('even', 'odd'):          # no number, no side
                self.assertIn('No voters', self.error(self.get('/api/v3/walk/list?streets=%d:%s' % (fountain, side)), 400))
        self.assertEqual({v['person_id'] for v in voters_of(payload)}, self.people_on('Pine St', city=FOUNTAIN))
        self.assertEqual({door['house'] for door in doors_of(payload)}, {V.NO_NUMBER})
        for run in payload['runs']:
            self.assertEqual((run['from_house'], run['to_house']), (V.NO_NUMBER, V.NO_NUMBER))

    def test_people_without_a_stop_are_left_out_without_error(self):
        mover = next(iter(self.expected['doors']['d04']['people']))
        copy = self.edited_world('nostop', 'UPDATE person SET stop_id=NULL, street_id=NULL WHERE person_id=?', mover)
        cedar = self.street_id('Cedar Heights Dr')
        with override_settings(DATA_DIR=copy):
            by_street = self.list('%d:both' % cedar)
            by_area = self.list(precinct=PRECINCT_W)
            streets = self.json('/api/v3/walk/streets?q=cedar')['streets'][0]
            self.assertEqual(self.get('/api/v3/walk/overview?precinct=' + PRECINCT_W).status_code, 200)
            self.assertEqual(self.match([{'street': 'Cedar Heights Dr'}])[0]['status'], 'matched')
        self.assertEqual({v['person_id'] for v in voters_of(by_street)}, self.people_on('Cedar Heights Dr') - {mover})
        self.assertNotIn(mover, {v['person_id'] for v in voters_of(by_area)})
        self.assertEqual(streets['people'], len(self.people_on('Cedar Heights Dr')) - 1)

    def edited_world(self, name, sql, *args):
        copy = Path(self.temporary.name) / name
        shutil.copytree(self.stores.directory, copy)
        with closing(sqlite3.connect(copy / 'walk.sqlite3')) as db, db:
            db.execute(sql, args)
        return copy

    def test_a_door_without_coordinates_borrows_its_neighbours(self):
        copy = self.edited_world('nocoords', "UPDATE stop SET lon=NULL, lat=NULL WHERE house='1132'")
        with override_settings(DATA_DIR=copy):
            payload = self.list('%d:even' % self.street_id('Cedar Heights Dr'))
        self.assertEqual({v['person_id'] for v in voters_of(payload)}, self.people_on('Cedar Heights Dr', parity='even'))
        for run in payload['runs']:
            for at, door in enumerate(run['doors']):
                self.assertTrue(isinstance(door['lon'], float) and isinstance(door['lat'], float), door['house'])
                if door['house'] == '1132':
                    near = [(d['lon'], d['lat']) for d in run['doors'][max(0, at - 1):at + 2] if d['house'] != '1132']
                    self.assertIn((door['lon'], door['lat']), near)
        west, south, east, north = payload['bbox']
        self.assertTrue(all(west <= d['lon'] <= east and south <= d['lat'] <= north for d in doors_of(payload)))

    def test_a_stop_off_every_block_is_listed_without_a_path(self):
        copy = self.edited_world('noblock', "UPDATE stop SET span_id=NULL, pos_m=NULL, side_lr=0 WHERE house='1119'")
        with override_settings(DATA_DIR=copy):
            payload = self.list('%d:odd' % self.street_id('Cedar Heights Dr'))
        self.assertEqual({v['person_id'] for v in voters_of(payload)}, self.people_on('Cedar Heights Dr', parity='odd'))
        loose = [run for run in payload['runs'] if not run['path']]
        self.assertEqual([[d['house'] for d in run['doors']] for run in loose], [['1119']])
        self.assertEqual((loose[0]['walk_m'], loose[0]['lr'], loose[0]['approach']), (0.0, 0, []))
        self.assertEqual([d['seq'] for d in doors_of(payload)], list(range(1, len(doors_of(payload)) + 1)))

    def test_caps(self):
        cedar = self.street_id('Cedar Heights Dr')
        self.error(self.get('/api/v3/walk/list?streets=' + ','.join('%d:both' % n for n in range(1, 42))), 400)
        with mock.patch.object(V, 'MAX_STREETS', 5):          # the world has five streets
            self.assertEqual(self.get('/api/v3/walk/list?streets=' + ','.join('%d:both' % n for n in range(1, 6))).status_code, 200)
            self.assertEqual(self.get('/api/v3/walk/list?streets=' + ','.join('%d:even,%d:odd' % (n, n) for n in range(1, 6))).status_code, 200)
            self.error(self.get('/api/v3/walk/list?streets=' + ','.join('%d:both' % n for n in range(1, 7))), 400)
        with mock.patch.object(V, 'MAX_VOTERS', 10):
            message = self.error(self.get('/api/v3/walk/list?streets=%d:both' % cedar), 400)
            self.assertIn('23 voters', message)
            self.assertIn('10', message)
            self.assertIn('34 voters', self.error(self.get('/api/v3/walk/list?ss=010'), 400))
            self.assertEqual(self.get('/api/v3/walk/list?streets=%d:both&precinct=%s' % (self.street_id('Juniper Ln'), PRECINCT_W)).status_code, 200)
            self.error(self.post('/api/v3/walk/lists', {'title': 'Too big', 'query': {'streets': '%d:both' % cedar}}), 400)
        self.assertEqual(WalkList.objects.count(), 0)

    def test_empty_and_unknown_selections_are_said_plainly(self):
        self.assertIn('Pick an area', self.error(self.get('/api/v3/walk/list'), 400))
        self.assertIn('No voters', self.error(self.get('/api/v3/walk/list?ss=010&sh=021'), 400))
        self.assertIn('No voters', self.error(self.get('/api/v3/walk/list?streets=%d:both&ss=011' % self.street_id('Juniper Ln')), 400))
        self.assertIn('not in the walk list data', self.error(self.get('/api/v3/walk/list?streets=99999:both'), 400))

    def test_too_many_street_segments_are_refused_before_planning(self):
        with mock.patch.object(V, 'MAX_SEGMENTS', 3), mock.patch.object(V.walk_route, 'plan_route') as plan:
            message = self.error(self.get('/api/v3/walk/list?streets=' + self.cedar_juniper()), 400)
            self.assertEqual(message, 'This selection splits into too many separate street segments. Narrow the area or streets.')
            plan.assert_not_called()
        self.assertEqual(self.get('/api/v3/walk/list?streets=' + self.cedar_juniper()).status_code, 200)

    def test_too_many_runs_in_one_group_are_refused_before_ordering(self):
        with mock.patch.object(V, 'MAX_GROUP_RUNS', 1):
            self.assertIn('too many separate street segments', self.error(self.get('/api/v3/walk/list?streets=' + self.cedar_juniper()), 400))
        self.assertEqual(self.get('/api/v3/walk/list?streets=' + self.cedar_juniper()).status_code, 200)

    def test_overview_blocks_with_door_and_party_counts(self):
        payload = self.json('/api/v3/walk/overview?precinct=' + PRECINCT_W)
        self.assertEqual(payload['type'], 'FeatureCollection')
        people = [p for p in self.expected['people'].values() if p['precinct'] == PRECINCT_W]
        doors = [d for d in self.expected['doors'].values() if d['precinct'] == PRECINCT_W]
        spans = set()
        counts = {'d': 0, 'r': 0, 'u': 0, 'o': 0}
        total_doors = 0
        for feature in payload['features']:
            self.assertEqual(feature['type'], 'Feature')
            self.assertEqual(feature['geometry']['type'], 'LineString')
            self.assertGreaterEqual(len(feature['geometry']['coordinates']), 2)
            props = feature['properties']
            self.assertEqual(set(props), {'span', 'street', 'doors', 'd', 'r', 'u', 'o'})
            self.assertNotIn(props['span'], spans)
            spans.add(props['span'])
            total_doors += props['doors']
            for party in counts:
                counts[party] += props[party]
        self.assertEqual(total_doors, len(doors))
        want = {'d': 0, 'r': 0, 'u': 0, 'o': 0}
        for person in people:
            want[person['party'].lower() if person['party'] in 'DRU' else 'o'] += 1
        self.assertEqual(counts, want)
        self.assertEqual({f['properties']['street'] for f in payload['features']}, {'Cedar Heights Dr', 'Juniper Ln', 'Ridgeview Ct'})
        self.assertEqual(sum(sum(f['properties'][k] for k in 'dru') + f['properties']['o'] for f in payload['features']), len(people))

    def test_overview_needs_an_area_and_has_a_cap(self):
        self.error(self.get('/api/v3/walk/overview'), 400)
        self.assertEqual(self.json('/api/v3/walk/overview?ss=011')['features'][0]['properties']['street'], 'Pine St')
        self.assertEqual(self.json('/api/v3/walk/overview?ss=011&sh=018')['features'], [])
        with mock.patch.object(V, 'MAX_SPANS', 2):
            self.assertIn('blocks', self.error(self.get('/api/v3/walk/overview?ss=010'), 400))
            self.assertEqual(self.get('/api/v3/walk/overview?ss=011').status_code, 200)


# ---- saved lists, results, re-knock --------------------------------------------------------------------------------

class WalkSavedListTests(WalkCase):
    def saved(self, streets=None, title='Cedar and Juniper'):
        return self.make_list({'streets': streets or self.cedar_juniper()}, title)

    def results(self, key, entries, day='2026-10-01'):
        return self.posted('/api/v3/walk/dispositions', {'list': key, 'on': day, 'entries': [{'person_id': p, 'code': c} for p, c in entries]})

    def last_by_person(self, key):
        return {v['person_id']: v['last'] for v in voters_of(self.json('/api/v3/walk/lists/' + key))}

    def test_a_list_is_frozen_with_its_details(self):
        built = self.json('/api/v3/walk/list?streets=' + self.cedar_juniper())
        saved = self.saved()
        self.assertEqual(saved['list']['title'], 'Cedar and Juniper')
        self.assertEqual(saved['list']['kind'], 'standard')
        self.assertRegex(saved['list']['key'], r'^[0-9a-f]{10}$')
        self.assertEqual(datetime.fromisoformat(saved['list']['created_at']).date(), date.today())
        self.assertEqual({k: v for k, v in saved.items() if k != 'list'}, {k: v for k, v in built.items() if k != 'list'})
        row = WalkList.objects.get(key=saved['list']['key'])
        self.assertEqual((row.title, row.kind, row.parent, row.query), ('Cedar and Juniper', 'standard', None, {'streets': self.cedar_juniper()}))
        self.assertEqual(row.source_sha256, self.stores.source_sha256)
        self.assertEqual(self.json('/api/v3/walk/lists/' + saved['list']['key']), saved)
        self.assertTrue(saved['list']['key'] in {l['key'] for l in self.json('/api/v3/walk/lists')['lists']})

    def test_map_context_goes_out_with_each_list_and_is_never_stored(self):
        built = self.json('/api/v3/walk/list?streets=' + self.cedar_juniper())
        self.assertEqual(set(built['context']), {'bbox', 'roads', 'precincts', 'homes'})
        saved = self.saved()
        self.assertIn('context', saved)
        self.assertNotIn('context', WalkList.objects.get(key=saved['list']['key']).payload)
        self.assertIn('context', self.json('/api/v3/walk/lists/' + saved['list']['key']))

    def test_the_voter_keys_stay_private(self):
        saved = self.saved()
        key = saved['list']['key']
        voter_keys = set(WalkList.objects.get(key=key).payload[V.PRIVATE].values())
        self.assertEqual(len(voter_keys), 26)
        self.results(key, [(voters_of(saved)[0]['person_id'], 'S')])
        for url in ('/api/v3/walk/lists/' + key, '/api/v3/walk/lists'):
            text = self.get(url).content.decode()
            self.assertNotIn(V.PRIVATE, text)
            self.assertFalse(any(k in text for k in voter_keys))
        self.assertFalse(any(k in json.dumps(saved) for k in voter_keys))

    def test_saved_list_index_shape_and_order(self):
        first = self.saved('%d:both' % self.street_id('Ridgeview Ct'), 'Ridgeview')
        second = self.saved()
        index = self.json('/api/v3/walk/lists')
        self.assertEqual(set(index), {'lists'})
        self.assertEqual([l['key'] for l in index['lists']], [second['list']['key'], first['list']['key']])
        for item, want in zip(index['lists'], (second, first)):
            self.assertEqual(set(item), {'key', 'title', 'kind', 'created_at', 'doors', 'voters'})
            self.assertEqual((item['title'], item['kind'], item['doors'], item['voters']),
                             (want['list']['title'], 'standard', want['totals']['doors'], want['totals']['voters']))
            self.assertEqual(item['created_at'], want['list']['created_at'])

    def test_only_the_newest_fifty_are_listed(self):
        for number in range(52):
            WalkList.objects.create(key='%010x' % number, title='List %d' % number, payload={'totals': {'doors': number, 'voters': 2 * number}}, source_sha256='a' * 64)
        index = self.json('/api/v3/walk/lists')['lists']
        self.assertEqual(len(index), 50)
        self.assertEqual([l['title'] for l in index][:3], ['List 51', 'List 50', 'List 49'])
        self.assertEqual((index[0]['doors'], index[0]['voters']), (51, 102))

    def test_titles_and_queries_are_validated_and_unknown_lists_are_404(self):
        self.assertEqual(self.make_list({'ss': '011'}, ' Trimmed ')['list']['title'], 'Trimmed')
        self.assertEqual(self.make_list({'ss': '011'}, 'x' * 120)['list']['title'], 'x' * 120)
        self.assertEqual(self.make_list({'streets': '1:even,1:odd', 'ss': '010'})['query'], {'streets': '1:both', 'ss': '010'})
        for key in ('0123456789', 'zzzzzzzzzz', 'short', 'A' * 10):
            self.error(self.get('/api/v3/walk/lists/' + key), 404)
        self.error(self.post('/api/v3/walk/lists', {'title': 'x', 'from': '0123456789', 'reknock': True}), 404)
        self.error(self.post('/api/v3/walk/dispositions', {'list': '0123456789', 'on': '2026-10-01', 'entries': [{'person_id': 'p', 'code': 'S'}]}), 404)

    def test_results_are_for_people_on_the_roster_only(self):
        saved = self.saved('%d:both' % self.street_id('Ridgeview Ct'))
        key = saved['list']['key']
        inside = voters_of(saved)[0]['person_id']
        outside = next(iter(self.people_on('Pine St', city=FOUNTAIN)))
        before = Disposition.objects.count()
        self.assertIn('not on this list', self.error(self.post('/api/v3/walk/dispositions', {
            'list': key, 'on': '2026-10-01', 'entries': [{'person_id': inside, 'code': 'S'}, {'person_id': outside, 'code': 'S'}]}), 400))
        self.assertIn('not on this list', self.error(self.post('/api/v3/walk/dispositions', {
            'list': key, 'on': '2026-10-01', 'entries': [{'person_id': 'p_' + '0' * 64, 'code': None}]}), 400))
        self.assertEqual(Disposition.objects.count(), before)          # nothing of a refused request is kept
        self.assertEqual(self.results(key, [(inside, 'S')]), {'saved': 1})
        self.assertEqual(Disposition.objects.count(), before + 1)

    def test_codes_dates_and_batch_size(self):
        saved = self.saved('%d:both' % self.street_id('Ridgeview Ct'))
        key, person = saved['list']['key'], voters_of(saved)[0]['person_id']
        for code in ('S', 'U', 'O', 'NH', 'MV', None):
            self.assertEqual(self.results(key, [(person, code)]), {'saved': 1})
        for code in ('X', 'nh', 'S ', '', 'Moved'):
            self.error(self.post('/api/v3/walk/dispositions', {'list': key, 'on': '2026-10-01', 'entries': [{'person_id': person, 'code': code}]}), 400)
        entries = [{'person_id': person, 'code': 'S'}] * 200
        self.assertEqual(self.posted('/api/v3/walk/dispositions', {'list': key, 'on': '2026-10-01', 'entries': entries}), {'saved': 200})
        self.error(self.post('/api/v3/walk/dispositions', {'list': key, 'on': '2026-10-01', 'entries': entries + entries[:1]}), 400)
        row = Disposition.objects.filter(walk_list__key=key).latest('id')
        self.assertEqual((row.person_id, row.code, row.contacted_on), (person, 'S', date(2026, 10, 1)))
        self.assertEqual(row.voter_key, WalkList.objects.get(key=key).payload[V.PRIVATE][person])
        self.assertIsNotNone(row.recorded_at)

    def test_a_visit_day_in_the_future_or_long_past_is_refused(self):
        saved = self.saved('%d:both' % self.street_id('Ridgeview Ct'))
        key, person = saved['list']['key'], voters_of(saved)[0]['person_id']
        for day in ((date.today() + timedelta(days=2)).isoformat(), '9999-12-31', '1999-12-31'):
            self.assertIn('future', self.error(self.post('/api/v3/walk/dispositions', {'list': key, 'on': day, 'entries': [{'person_id': person, 'code': 'S'}]}), 400))
        for day in (date.today().isoformat(), (date.today() + timedelta(days=1)).isoformat(), '2000-01-01'):
            self.assertEqual(self.results(key, [(person, 'S')], day), {'saved': 1})

    def test_the_newest_result_is_the_last_one_saved_whatever_the_clock_says(self):
        saved = self.saved('%d:both' % self.street_id('Ridgeview Ct'))
        key, person = saved['list']['key'], voters_of(saved)[0]['person_id']
        self.results(key, [(person, 'S')], '2026-10-01')
        self.results(key, [(person, 'U')], '2026-10-02')
        Disposition.objects.filter(code='U').update(recorded_at=datetime(2020, 1, 1, tzinfo=timezone.utc))      # a clock set back
        self.assertEqual(self.last_by_person(key)[person], {'code': 'U', 'on': '2026-10-02'})
        self.results(key, [(person, 'NH')], '2026-10-03')
        again = self.posted('/api/v3/walk/lists', {'title': 'Again', 'from': key, 'reknock': True}, 201)
        self.assertEqual([v['person_id'] for v in voters_of(again)], [person])

    def test_results_are_appended_the_newest_wins_and_null_clears(self):
        saved = self.saved()
        key = saved['list']['key']
        a, b, c = (v['person_id'] for v in voters_of(saved)[:3])
        self.results(key, [(a, 'NH'), (b, 'U')], '2026-09-30')
        self.results(key, [(a, 'S')], '2026-10-01')
        self.results(key, [(c, 'O'), (c, 'MV')], '2026-10-01')                  # later entry of one request wins
        last = self.last_by_person(key)
        self.assertEqual(last[a], {'code': 'S', 'on': '2026-10-01'})
        self.assertEqual(last[b], {'code': 'U', 'on': '2026-09-30'})
        self.assertEqual(last[c], {'code': 'MV', 'on': '2026-10-01'})
        self.assertEqual(Disposition.objects.filter(walk_list__key=key, person_id=a).count(), 2)       # append-only
        self.results(key, [(a, None)], '2026-10-02')
        self.assertIsNone(self.last_by_person(key)[a])
        self.assertEqual(Disposition.objects.filter(walk_list__key=key, person_id=a).count(), 3)
        self.assertEqual(sum(1 for v in self.last_by_person(key).values() if v), 2)
        self.results(key, [(a, 'U')], '2026-10-03')
        self.assertEqual(self.last_by_person(key)[a], {'code': 'U', 'on': '2026-10-03'})

    def test_last_is_the_latest_result_across_lists_and_a_clear_hides_only_its_own(self):
        first = self.saved('%d:both' % self.street_id('Ridgeview Ct'), 'First')
        second = self.saved('%d:both,%d:both' % (self.street_id('Ridgeview Ct'), self.street_id('Juniper Ln')), 'Second')
        person = voters_of(first)[0]['person_id']
        self.assertEqual(self.last_by_person(second['list']['key'])[person], None)
        self.results(first['list']['key'], [(person, 'NH')], '2026-09-20')
        self.assertEqual(self.last_by_person(second['list']['key'])[person], {'code': 'NH', 'on': '2026-09-20'})
        self.results(second['list']['key'], [(person, 'S')], '2026-09-25')
        for key in (first['list']['key'], second['list']['key']):
            self.assertEqual(self.last_by_person(key)[person], {'code': 'S', 'on': '2026-09-25'})
        self.results(second['list']['key'], [(person, None)], '2026-09-26')            # cleared on the second list
        for key in (first['list']['key'], second['list']['key']):
            self.assertEqual(self.last_by_person(key)[person], {'code': 'NH', 'on': '2026-09-20'})
        self.results(first['list']['key'], [(person, None)], '2026-09-27')
        self.assertIsNone(self.last_by_person(second['list']['key'])[person])
        built = self.json('/api/v3/walk/list?streets=%d:both' % self.street_id('Ridgeview Ct'))
        self.assertTrue(all(v['last'] is None for v in voters_of(built)))
        self.results(first['list']['key'], [(person, 'U')], '2026-09-28')
        built = self.json('/api/v3/walk/list?streets=%d:both' % self.street_id('Ridgeview Ct'))
        self.assertEqual({v['person_id']: v['last'] for v in voters_of(built)}[person], {'code': 'U', 'on': '2026-09-28'})

    def test_reknock_lists_the_voters_whose_latest_result_is_not_home(self):
        saved = self.saved()
        key = saved['list']['key']
        people = [v['person_id'] for v in voters_of(saved)]
        nh, later_s, cleared, other, still = people[:5]
        self.results(key, [(nh, 'NH'), (later_s, 'NH'), (cleared, 'NH'), (other, 'U'), (still, 'NH')], '2026-09-30')
        self.results(key, [(later_s, 'S'), (cleared, None), (still, 'NH')], '2026-10-01')
        again = self.posted('/api/v3/walk/lists', {'title': 'Not home again', 'from': key, 'reknock': True}, 201)
        self.check_payload(again)
        self.assertEqual({v['person_id'] for v in voters_of(again)}, {nh, still})
        self.assertEqual((again['list']['kind'], again['list']['title']), ('reknock', 'Not home again'))
        self.assertNotEqual(again['list']['key'], key)
        self.assertEqual(again['query'], {'from': key, 'reknock': '1'})          # not the parent's streets or area
        self.assertEqual(WalkList.objects.get(key=again['list']['key']).query, {'from': key, 'reknock': '1'})
        self.assertEqual(again['totals']['voters'], 2)
        self.assertEqual(again['totals']['doors'], len(doors_of(again)))
        self.assertEqual([d['seq'] for d in doors_of(again)], list(range(1, len(doors_of(again)) + 1)))
        self.assertEqual({v['person_id']: v['last'] for v in voters_of(again)}[nh], {'code': 'NH', 'on': '2026-09-30'})
        row = WalkList.objects.get(key=again['list']['key'])
        self.assertEqual((row.kind, row.parent.key), ('reknock', key))
        self.assertEqual(self.json('/api/v3/walk/lists/' + again['list']['key'])['list'], again['list'])
        index = {l['key']: l for l in self.json('/api/v3/walk/lists')['lists']}
        self.assertEqual((index[again['list']['key']]['kind'], index[again['list']['key']]['voters']), ('reknock', 2))
        # results of the new list are its own, and it can be re-knocked in turn
        sub = again['list']['key']
        self.results(sub, [(nh, 'NH')], '2026-10-02')
        third = self.posted('/api/v3/walk/lists', {'title': 'Third try', 'from': sub, 'reknock': True}, 201)
        self.assertEqual({v['person_id'] for v in voters_of(third)}, {nh})
        self.assertIn('Not home', self.error(self.post('/api/v3/walk/lists', {'title': 'None', 'from': third['list']['key'], 'reknock': True}), 400))

    def check_payload(self, payload):
        self.assertEqual(set(payload), LIST_KEYS)
        self.assertEqual(set(payload['list']), {'key', 'title', 'kind', 'created_at'})
        for run in payload['runs']:
            self.assertEqual(set(run), RUN_KEYS)
            for door in run['doors']:
                self.assertEqual(set(door), DOOR_KEYS)
                for voter in door['voters']:
                    self.assertEqual(set(voter), VOTER_KEYS)

    def test_voters_the_store_no_longer_has_cannot_be_re_knocked(self):
        saved = self.saved('%d:both' % self.street_id('Ridgeview Ct'))
        key = saved['list']['key']
        people = [v['person_id'] for v in voters_of(saved)]
        self.results(key, [(person, 'NH') for person in people[:2]])
        copy = Path(self.temporary.name) / 'moved-away'
        shutil.copytree(self.stores.directory, copy)
        with closing(sqlite3.connect(copy / 'walk.sqlite3')) as db, db:
            db.execute('DELETE FROM person WHERE person_id IN (?, ?)', people[:2])
        with override_settings(DATA_DIR=copy):
            self.assertIn('no longer', self.error(self.post('/api/v3/walk/lists', {'title': 'x', 'from': key, 'reknock': True}), 409))
        self.assertEqual(WalkList.objects.count(), 1)

    def test_results_of_many_voters_are_looked_up_in_chunks(self):
        parent = WalkList.objects.create(key='f' * 10, title='Many', payload={}, source_sha256='a' * 64)
        keys = ['%064x' % n for n in range(1100)]
        Disposition.objects.bulk_create([Disposition(walk_list=parent, person_id='p%d' % n, voter_key=key, code='NH' if n % 2 else 'S',
                                                     contacted_on=date(2026, 10, 1)) for n, key in enumerate(keys)])
        found = V.latest_codes(keys + keys[:5])
        self.assertEqual(len(found), 1100)
        self.assertEqual((found[keys[0]]['code'], found[keys[1]]['code'], found[keys[1099]]['code']), ('S', 'NH', 'NH'))
        self.assertEqual(V.latest_codes([]), {})

    def test_an_accounts_database_without_the_tables_is_a_503_that_names_the_fix(self):
        from django.db.utils import OperationalError
        with mock.patch.object(V.WalkList.objects, 'order_by', side_effect=OperationalError('no such table: research_walklist')):
            self.assertIn('manage.py migrate', self.error(self.get('/api/v3/walk/lists'), 503))
        with mock.patch.object(V, 'latest_codes', side_effect=OperationalError('no such table: research_disposition')):
            self.assertIn('manage.py migrate', self.error(self.get('/api/v3/walk/list?streets=%d:both' % self.street_id('Ridgeview Ct')), 503))
        self.assertEqual(self.get('/api/v3/walk/lists').status_code, 200)

    def test_nothing_to_re_knock_is_said_plainly(self):
        saved = self.saved('%d:both' % self.street_id('Ridgeview Ct'))
        key = saved['list']['key']
        self.assertIn('Not home', self.error(self.post('/api/v3/walk/lists', {'title': 'x', 'from': key, 'reknock': True}), 400))
        person = voters_of(saved)[0]['person_id']
        self.results(key, [(person, 'S')])
        self.assertIn('Not home', self.error(self.post('/api/v3/walk/lists', {'title': 'x', 'from': key, 'reknock': True}), 400))
        self.assertEqual(WalkList.objects.count(), 1)

    def test_saved_lists_and_results_do_not_need_the_walk_store(self):
        saved = self.saved('%d:both' % self.street_id('Ridgeview Ct'))
        key, person = saved['list']['key'], voters_of(saved)[0]['person_id']
        empty = Path(self.temporary.name) / 'no-stores'
        empty.mkdir()
        with override_settings(DATA_DIR=empty):
            self.assertEqual(self.json('/api/v3/walk/lists/' + key)['list'], saved['list'])
            self.assertEqual(self.results(key, [(person, 'NH')]), {'saved': 1})
            self.assertEqual(self.json('/api/v3/walk/lists')['lists'][0]['key'], key)
            self.assertEqual(self.last_by_person(key)[person]['code'], 'NH')
            self.assertEqual(self.get('/api/v3/walk/options?level=ss').status_code, 503)
            self.assertEqual(self.post('/api/v3/walk/lists', {'title': 'x', 'from': key, 'reknock': True}).status_code, 503)


# ---- the store checks ----------------------------------------------------------------------------------------------

class WalkStoreChecks(WalkCase):
    def copy_world(self, name):
        copy = Path(self.temporary.name) / name
        shutil.copytree(self.stores.directory, copy)
        return copy

    def edit(self, path, sql, *args):
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(sql, args)

    def endpoints(self):
        return ['/api/v3/walk/options?level=ss', '/api/v3/walk/streets?q=ced', '/api/v3/walk/list?ss=011',
                '/api/v3/walk/overview?ss=011']

    def test_a_store_that_was_not_built_is_a_503_with_the_command(self):
        empty = Path(self.temporary.name) / 'unbuilt'
        shutil.copytree(self.stores.directory, empty, ignore=shutil.ignore_patterns('walk.sqlite3'))
        with override_settings(DATA_DIR=empty):
            for url in self.endpoints():
                self.assertEqual(self.error(self.get(url), 503), "Walk list data isn't built yet. Run tools/build_walk.py.", url)
            self.assertEqual(self.error(self.post('/api/v3/walk/match', {'rows': [{'street': 'x'}]}), 503),
                             "Walk list data isn't built yet. Run tools/build_walk.py.")
            self.assertEqual(self.error(self.post('/api/v3/walk/lists', {'title': 'x', 'query': {'ss': '010'}}), 503),
                             "Walk list data isn't built yet. Run tools/build_walk.py.")
            self.assertEqual(self.get('/api/v3/walk/lists').status_code, 200)
        self.assertFalse((empty / 'walk.sqlite3').exists())                     # a missing store is never created

    def test_the_other_stores_must_be_there(self):
        copy = self.copy_world('no-contacts')
        (copy / 'contacts.sqlite3').unlink()
        with override_settings(DATA_DIR=copy):
            for url in self.endpoints():
                self.assertEqual(self.get(url).status_code, 503, url)
        self.assertFalse((copy / 'contacts.sqlite3').exists())

    def test_a_different_schema_is_a_503(self):
        copy = self.copy_world('schema')
        self.edit(copy / 'walk.sqlite3', "UPDATE meta SET value='walk-v0' WHERE key='schema'")
        with override_settings(DATA_DIR=copy):
            self.assertIn('unsupported schema', self.error(self.get('/api/v3/walk/options?level=ss'), 503))
        self.edit(copy / 'walk.sqlite3', "UPDATE meta SET value='walk-v1' WHERE key='schema'")
        with override_settings(DATA_DIR=copy):
            self.assertEqual(self.get('/api/v3/walk/options?level=ss').status_code, 200)       # a new file version is re-read
        self.edit(copy / 'walk.sqlite3', 'DROP TABLE meta')
        with override_settings(DATA_DIR=copy):
            self.assertIn('unreadable', self.error(self.get('/api/v3/walk/options?level=ss'), 503))

    def test_stores_from_different_sources_are_a_409(self):
        for store, table, key, quoted in (('walk', 'meta', 'source_sha256', False), ('contacts', 'metadata', 'source_sha256', True),
                                          ('street', 'metadata', 'source_sha256', True), ('scores', 'metadata', 'contacts_source_sha256', True)):
            copy = self.copy_world('source-' + store)
            other = 'c' * 64
            self.edit(copy / (store + '.sqlite3'), 'UPDATE %s SET value=? WHERE key=?' % table, json.dumps(other) if quoted else other, key)
            with override_settings(DATA_DIR=copy):
                for url in self.endpoints():
                    self.assertIn('different source', self.error(self.get(url), 409), (store, url))
                self.assertIn('different source', self.error(self.post('/api/v3/walk/lists', {'title': 'x', 'query': {'ss': '010'}}), 409))

    def test_the_crosswalk_must_be_verified_or_a_fixture(self):
        copy = self.copy_world('crosswalk')
        self.edit(copy / 'scores.sqlite3', "UPDATE metadata SET value=? WHERE key='w1'", json.dumps({'status': 'pending'}))
        with override_settings(DATA_DIR=copy):
            self.assertIn('crosswalk', self.error(self.get('/api/v3/walk/options?level=ss'), 409))
        self.edit(copy / 'scores.sqlite3', "UPDATE metadata SET value=? WHERE key='w1'", json.dumps({'status': 'verified'}))
        with override_settings(DATA_DIR=copy):
            self.assertEqual(self.get('/api/v3/walk/options?level=ss').status_code, 200)

    def test_a_replaced_store_is_picked_up(self):
        copy = self.copy_world('replaced')
        with override_settings(DATA_DIR=copy):
            before = self.json('/api/v3/walk/streets?q=ridgeview')['streets']
            self.assertEqual(len(before), 1)
            self.edit(copy / 'walk.sqlite3', "UPDATE street SET display='Ridgeview Court', key=? WHERE display='Ridgeview Ct'", N.name_key('Ridgeview Court'))
            after = self.json('/api/v3/walk/streets?q=ridgeview')['streets']
            self.assertEqual([s['label'] for s in after], ['Ridgeview Court'])

    def test_the_store_is_opened_read_only_and_closed(self):
        copy = self.copy_world('closed')
        with override_settings(DATA_DIR=copy):
            self.assertEqual(self.get('/api/v3/walk/options?level=ss').status_code, 200)
            self.assertEqual(self.get('/api/v3/walk/list?ss=010').status_code, 200)
        walk_file = copy / 'walk.sqlite3'
        walk_file.rename(copy / 'walk.old')             # fails on Windows while a handle is still open
        (copy / 'walk.old').rename(walk_file)
        with override_settings(DATA_DIR=copy):
            with W.walk_store() as store:
                with self.assertRaises(sqlite3.OperationalError):
                    store.conn.execute('DELETE FROM street')
                with self.assertRaises(sqlite3.OperationalError):
                    store.conn.execute('UPDATE contacts.contacts SET name=name')


# ---- the in-memory street directory --------------------------------------------------------------------------------

class DirectoryTests(SimpleTestCase):
    def directory(self, *labels):
        rows = []
        for number, label in enumerate(labels, 1):
            city = 'FOUNTAIN' if label.endswith('*') else 'COLORADO SPRINGS'
            label = label.rstrip('*')
            rows.append((number, N.name_key(label), city, '80903', label))
        return W.Directory(rows)

    def names(self, found):
        return [s.display for s in found]

    def test_typed_text_ranks_whole_name_then_start_then_words_then_half_words(self):
        d = self.directory('Cedarwood Dr', 'N Cedar St', 'Cedar Heights Dr', 'Cedar Ln', 'Cedar', 'Old Cedar Rd', 'Elm St')
        doors = {i: n for i, n in zip(range(1, 8), (50, 40, 30, 20, 10, 5, 99))}
        found = d.search('cedar', 20, None, doors)
        self.assertEqual(self.names(found), ['Cedar', 'Cedar Heights Dr', 'Cedar Ln', 'N Cedar St', 'Old Cedar Rd', 'Cedarwood Dr'])
        self.assertEqual(self.names(d.search('cedar', 2, None, doors)), ['Cedar', 'Cedar Heights Dr'])
        self.assertEqual(self.names(d.search('Cedar Heights Drive', 20, None, doors))[0], 'Cedar Heights Dr')
        self.assertEqual(self.names(d.search('heig', 20, None, doors)), ['Cedar Heights Dr'])
        self.assertEqual(self.names(d.search('cedar l', 20, None, doors))[:1], ['Cedar Ln'])
        self.assertEqual(self.names(d.search('', 20, None, doors)), [])
        self.assertEqual(self.names(d.search('...', 20, None, doors)), [])

    def test_ordinals_and_abbreviations_match_either_way(self):
        d = self.directory('1st St', 'Mt Herman Rd', 'St Charles Ave', 'Pikes Peak Ave')
        self.assertEqual(self.names(d.search('first', 20)), ['1st St'])
        self.assertEqual(self.names(d.search('mount herman', 20)), ['Mt Herman Rd'])
        self.assertEqual(self.names(d.search('saint charles', 20)), ['St Charles Ave'])
        self.assertEqual(self.names(d.search('pikes', 20)), ['Pikes Peak Ave'])

    def test_a_misspelling_finds_the_street_when_the_whole_name_is_close(self):
        d = self.directory('Cheyenne Mountain Blvd', 'Cheyenne Blvd', 'Chestnut St')
        self.assertEqual(self.names(d.search('Cheyene Mountain Boulevard', 20)), ['Cheyenne Mountain Blvd', 'Cheyenne Blvd'])
        self.assertEqual(d.lookup('Cheyene Mountain Blvd')[0][1].display, 'Cheyenne Mountain Blvd')

    def test_lookup_is_exact_first_and_ranked_by_similarity(self):
        d = self.directory('N Main St', 'S Main St', 'Main Ave', 'Main St', 'Maine Ct')
        exact = d.lookup('main street')
        self.assertEqual([(score, s.display) for score, s in exact], [(1.0, 'Main St')])
        loose = d.lookup('Main')
        self.assertEqual([s.display for _, s in loose[:2]], ['Main Ave', 'Main St'])
        self.assertEqual(loose[0][0], loose[1][0])                          # as close to one as to the other
        self.assertEqual(d.lookup('Zzyzx'), [])
        self.assertEqual(d.lookup(''), [])
        in_area = d.lookup('Main St', {1, 2})
        self.assertEqual(sorted(s.display for _, s in in_area), ['N Main St', 'S Main St'])

    def test_a_type_or_direction_alone_is_no_reason_to_offer_a_street(self):
        d = self.directory('Oak St', 'Elm St', 'Pine St')
        self.assertEqual(d.lookup('Birch St'), [])
        self.assertEqual(d.lookup('N'), [])

    def test_500_rows_against_10000_streets_take_a_moment(self):
        import random
        rng = random.Random(3)
        words = sorted({''.join(rng.choice('abcdefghilmnoprstuvw') for _ in range(rng.randint(4, 9))) for _ in range(6000)})
        rows, seen = [], set()
        for number in range(1, 10001):
            label = '%s %s' % (rng.choice(words).capitalize(), rng.choice(('St', 'Ave', 'Dr', 'Ln', 'Ct', 'Way', 'Rd')))
            key = N.name_key(label)
            if (key, number % 5) in seen:
                continue
            seen.add((key, number % 5))
            rows.append((number, key, 'CITY %d' % (number % 5), '80903', label))
        directory = W.Directory(rows)
        doors = {r[0]: 1 for r in rows}
        started = time.perf_counter()
        for row in rng.sample(rows, 500):
            name, kind = row[4].rsplit(' ', 1)
            typed = '%s %s' % (name.lower(), {'St': 'Street', 'Ave': 'Avenue', 'Dr': 'Drive'}.get(kind, kind)) if rng.random() < 0.5 else name[:-1] + ' ' + kind
            V.decide(directory.lookup(typed), '', '', doors)
        self.assertLess(time.perf_counter() - started, 10)
