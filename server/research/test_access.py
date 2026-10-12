"""Hosted deployment, the front door: the Cloudflare Access middleware, the settings a hosted deployment must have, the bind
address, the sign-in throttle's key and the district selector's links. Synthetic accounts only; tokens are signed at run time
with a throwaway key (access_testkit.py)."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from tools import districts as D

from . import access
from . import access_testkit as K
from .models import LoginAttempt
from .views import client_address

SESSION_KEYS = {'authenticated', 'username', 'administrator', 'local_admin_access', 'csrf'}


def user(name='staff@example.test', **extra):
    return User.objects.create_user(name, password='correct horse battery staple', **extra)


class AccessMiddlewareTests(TestCase):
    def setUp(self):
        context = K.hosted()
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def test_a_request_without_a_token_gets_nothing(self):
        for path in ('/', '/api/auth/session', '/api/v3/meta', '/api/snapshots', '/assets/index.js', '/api/health/'):
            response = K.client(None).get(path)
            self.assertEqual((response.status_code, response.content), (403, b'forbidden\n'), path)
            self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(K.client(None).post('/api/auth/login', '{}', content_type='application/json').status_code, 403)

    def test_a_valid_token_reaches_the_app_and_names_the_visitor(self):
        response = K.client('Staff@Example.test').get('/api/auth/session')
        self.assertEqual(response.status_code, 200)
        session = response.json()
        self.assertEqual((session['hosted'], session['email'], session['authenticated']), (True, 'Staff@Example.test', False))
        self.assertFalse(session['local_admin_access'])

    def test_a_token_that_is_wrong_in_any_way_is_refused(self):
        wrong = {'audience': K.token(aud='another-application'), 'issuer': K.token(iss='https://other.cloudflareaccess.com'),
                 'expired': K.token(exp=1), 'forged signature': K.token(tamper=True), 'unknown key': K.token(kid='not-listed'),
                 'garbage': 'a.b.c', 'empty': ''}
        for why, bad in wrong.items():
            made = K.client(None, HTTP_CF_ACCESS_JWT_ASSERTION=bad)
            self.assertEqual(made.get('/api/auth/session').status_code, 403, why)
        self.assertEqual(K.client('staff@example.test', HTTP_HOST='evil.example.test').get('/api/auth/session').status_code, 403)

    def test_the_cookie_is_never_read(self):
        made = K.client(None, HTTP_COOKIE='CF_Authorization=' + K.token())
        self.assertEqual(made.get('/api/auth/session').status_code, 403)

    def test_a_signed_in_account_must_be_the_person_the_token_names(self):
        account = user('Staff@Example.test')                    # case does not matter
        mine = K.client('staff@EXAMPLE.test', login=account)
        self.assertEqual(mine.get('/api/snapshots').status_code, 200)
        self.assertTrue(mine.get('/api/auth/session').json()['authenticated'])
        # the same session cookie carried to another Access identity: refused, and the session is over on the server
        sid = mine.cookies[settings.SESSION_COOKIE_NAME].value
        other = K.client('other@example.test')
        other.cookies[settings.SESSION_COOKIE_NAME] = sid
        self.assertTrue(Session.objects.filter(session_key=sid).exists())
        self.assertEqual(other.get('/api/snapshots').status_code, 403)
        self.assertFalse(Session.objects.filter(session_key=sid).exists())
        back = K.client('staff@example.test')
        back.cookies[settings.SESSION_COOKIE_NAME] = sid
        self.assertEqual(back.get('/api/snapshots').status_code, 401)         # the rightful owner is signed out too

    def test_an_account_with_an_email_is_matched_on_the_email(self):
        account = user('someone', email='Real.Name@example.test')
        self.assertEqual(K.client('real.name@example.test', login=account).get('/api/snapshots').status_code, 200)
        self.assertEqual(K.client('someone', login=account).get('/api/snapshots').status_code, 403)

    def test_an_identity_with_no_account_has_no_data(self):
        self.assertEqual(K.client('nobody@example.test').get('/api/snapshots').status_code, 401)

    def test_a_service_token_reaches_the_health_route_only(self):
        service = K.token(email=None, common_name='svc-client.access')
        for path, status in (('/api/health', 200), ('/api/auth/session', 403), ('/api/snapshots', 403)):
            made = K.client(None, HTTP_CF_ACCESS_JWT_ASSERTION=service)
            self.assertEqual(made.get(path).status_code, status, path)
        stranger = K.token(email=None, common_name='someone-else.access')
        self.assertEqual(K.client(None, HTTP_CF_ACCESS_JWT_ASSERTION=stranger).get('/api/health').status_code, 403)

    def test_only_api_health_is_open_to_a_loopback_probe(self):
        probe = dict(HTTP_HOST='127.0.0.1:8080', REMOTE_ADDR='127.0.0.1')
        self.assertEqual(Client(**probe).get('/api/health').json()['status'], 'ok')
        for path in ('/api/auth/session', '/api/v3/meta', '/', '/healthz', '/version', '/api/health/x'):
            self.assertEqual(Client(**probe).get(path).status_code, 403, path)
        for header in ('HTTP_CF_CONNECTING_IP', 'HTTP_X_FORWARDED_FOR', 'HTTP_X_REAL_IP', 'HTTP_FORWARDED'):
            self.assertEqual(Client(**probe, **{header: '203.0.113.9'}).get('/api/health').status_code, 403, header)
        self.assertEqual(Client(HTTP_HOST='127.0.0.1:8080', REMOTE_ADDR='172.18.0.5').get('/api/health').status_code, 403)
        self.assertEqual(Client(HTTP_HOST='evil.example.test', REMOTE_ADDR='127.0.0.1').get('/api/health').status_code, 403)

    def test_one_audit_line_per_request_without_the_query_or_the_address(self):
        with self.assertLogs('cu.access', 'INFO') as seen:
            K.client('staff@example.test', REMOTE_ADDR='203.0.113.77').get('/api/auth/session?probe=needle')
            K.client(None, REMOTE_ADDR='203.0.113.78').get('/api/v3/meta?probe=needle')
        self.assertEqual(len(seen.records), 2)
        allowed, refused = (json.loads(r.getMessage()) for r in seen.records)
        self.assertEqual((allowed['user'], allowed['route'], allowed['status'], allowed['app']),
                         ('staff@example.test', '/api/auth/session', 200, 'terrier-' + settings.DISTRICT['id']))
        self.assertEqual((refused['user'], refused['route'], refused['status'], refused['reason']),
                         ('-', '/api/v3/meta', 403, 'no token'))
        for line in (r.getMessage() for r in seen.records):
            self.assertNotIn('needle', line)
            self.assertNotIn('203.0.113', line)


class AccessConfigTests(SimpleTestCase):
    GOOD = {'ACCESS_AUD': 'aud', 'ACCESS_ISSUER': 'https://t.cloudflareaccess.com', 'ACCESS_JWKS_FILE': '/access/jwks.json',
            'CU_ALLOWED_HOSTS': 'co05.example.test,127.0.0.1'}

    def test_the_gate_is_always_enforced_and_takes_the_hosts_of_the_app(self):
        cfg = access.load_config(self.GOOD)
        self.assertTrue(cfg.enforced)
        self.assertEqual(cfg.bind_host, '127.0.0.1')              # enforced all the same
        self.assertIn('co05.example.test', cfg.allowed_hosts)
        self.assertEqual(access.load_config({**self.GOOD, 'ALLOWED_HOSTS': 'other.example.test'}).allowed_hosts - {
            '127.0.0.1', 'localhost', '::1', '[::1]'}, {'other.example.test'})

    def test_a_missing_or_malformed_setting_stops_the_server(self):
        for name in ('ACCESS_AUD', 'ACCESS_ISSUER', 'ACCESS_JWKS_FILE'):
            with self.assertRaises(ImproperlyConfigured, msg=name):
                access.load_config({k: v for k, v in self.GOOD.items() if k != name})
        with self.assertRaises(ImproperlyConfigured):
            access.load_config({**self.GOOD, 'CU_ALLOWED_HOSTS': '*'})
        self.assertTrue(access.load_config({**self.GOOD, 'ACCESS_ENFORCE': '0'}).enforced)    # nothing can switch it off

    def test_the_middleware_will_not_start_unconfigured(self):
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(ImproperlyConfigured):
            access.AccessJwtMiddleware(lambda request: None)



class LocalUnchangedTests(TestCase):
    def test_a_local_session_looks_exactly_as_before(self):
        with override_settings(HOSTED=False, LOCAL_ADMIN_ACCESS=True):
            session = Client(HTTP_HOST='localhost').get('/api/auth/session').json()
        self.assertEqual(set(session), SESSION_KEYS)

    def test_a_local_account_can_still_be_named_without_an_email(self):
        with override_settings(HOSTED=False):
            from .views import create_invitation
            create_invitation('plainname')


class HostedSettingsTests(SimpleTestCase):
    """server/settings.py as a hosted process sees it: a fresh interpreter per case, because settings are read once."""
    ROOT = Path(settings.BASE_DIR)

    def run_django(self, *args, hosted=True, **env):
        with tempfile.TemporaryDirectory() as tmp:
            base = {k: v for k, v in os.environ.items() if not k.startswith(('CU_', 'ACCESS_', 'DJANGO_'))}
            base.update(PYTHONDONTWRITEBYTECODE='1', CU_DATA_DIR=tmp + '/data')
            if hosted:
                base.update(CU_STATE_DIR=tmp + '/state', CU_DEPLOYMENT='hosted', CU_SECRET_KEY='k' * 20 + 'abcdefghij' * 4 + '0123456789' * 2,
                            CU_ALLOWED_HOSTS='co05.example.test,127.0.0.1', CU_TRUSTED_ORIGINS='https://co05.example.test',
                            CU_HTTPS_REDIRECT='0')
            base.update(env)
            return subprocess.run([sys.executable, 'manage.py', *args], cwd=self.ROOT, env=base, capture_output=True, text=True,
                                  timeout=240)

    PROBE = ('import json, django; django.setup(); from django.conf import settings as s; '
             'print(json.dumps({"hosted": s.HOSTED, "admin": s.LOCAL_ADMIN_ACCESS, "mw": s.MIDDLEWARE, "hsts": '
             's.SECURE_HSTS_SECONDS, "state": str(s.STATE_DIR), "db": s.DATABASES["default"]["NAME"], "referrer": '
             's.SECURE_REFERRER_POLICY, "cookies": [s.SESSION_COOKIE_SECURE, s.CSRF_COOKIE_SECURE]}))')

    def probe(self, **kw):
        done = self.run_django('shell', '-c', self.PROBE, **kw)
        self.assertEqual(done.returncode, 0, done.stderr[-600:])
        return json.loads(done.stdout.strip().splitlines()[-1])

    def test_hosted_has_access_first_no_local_administrator_and_its_own_state_volume(self):
        found = self.probe()
        self.assertEqual((found['hosted'], found['admin']), (True, False))
        self.assertEqual(found['mw'][0], K.ACCESS_MIDDLEWARE)
        self.assertNotIn(K.LOCAL_MIDDLEWARE, found['mw'])
        self.assertEqual((found['hsts'], found['referrer'], found['cookies']), (31536000, 'no-referrer', [True, True]))
        self.assertTrue(found['db'].replace('\\', '/').endswith('/state/accounts.sqlite3'))

    def test_a_hosted_start_that_asks_for_the_local_owner_fails(self):
        done = self.run_django('shell', '-c', self.PROBE, CU_LOCAL_ADMIN='1')
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('CU_LOCAL_ADMIN=1 is refused', done.stderr)

    def test_a_hosted_start_needs_exact_hostnames(self):
        for hosts in ('', '*', 'co05.example.test,*'):
            done = self.run_django('shell', '-c', self.PROBE, CU_ALLOWED_HOSTS=hosts)
            self.assertNotEqual(done.returncode, 0, hosts)
            self.assertIn('CU_ALLOWED_HOSTS', done.stderr)

    def test_a_hosted_start_needs_a_secret_key(self):
        done = self.run_django('shell', '-c', self.PROBE, CU_SECRET_KEY='')
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('CU_SECRET_KEY', done.stderr)

    def test_a_local_start_is_the_local_stack(self):
        found = self.probe(hosted=False)
        self.assertEqual((found['hosted'], found['admin'], found['hsts'], found['state']), (False, True, 0, 'None'))
        self.assertIn(K.LOCAL_MIDDLEWARE, found['mw'])
        self.assertNotIn(K.ACCESS_MIDDLEWARE, found['mw'])
        self.assertTrue(found['db'].replace('\\', '/').endswith('/data/accounts.sqlite3'))

    def test_the_deploy_check_is_clean_in_hosted_mode(self):
        for redirect in ('0', '1'):
            done = self.run_django('check', '--deploy', '--fail-level', 'WARNING', CU_HTTPS_REDIRECT=redirect)
            self.assertEqual(done.returncode, 0, (redirect, done.stdout[-800:], done.stderr[-800:]))

    def test_the_deploy_check_is_not_vacuous(self):
        done = self.run_django('check', '--deploy', '--fail-level', 'WARNING', CU_SECRET_KEY='short')
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('security.W009', done.stdout + done.stderr)


class ServeBindTests(SimpleTestCase):
    def serve(self, **env):
        with mock.patch.dict(os.environ, env), mock.patch('waitress.serve') as served, \
                mock.patch('server.research.management.commands.serve.enable_fault_log'), \
                mock.patch('faulthandler.enable'):
            call_command('serve', '--no-warm', '--port', '8999')
        return served

    def test_the_default_bind_is_loopback(self):
        with mock.patch.dict(os.environ):
            os.environ.pop('CU_BIND', None)
            served = self.serve()
        self.assertEqual(served.call_args.kwargs['host'], '127.0.0.1')

    def test_a_non_loopback_bind_is_refused_unless_hosted(self):
        with override_settings(HOSTED=False), self.assertRaises(CommandError) as raised:
            self.serve(CU_BIND='0.0.0.0')
        self.assertIn('CU_BIND=0.0.0.0', str(raised.exception))
        for bind in ('192.0.2.1', '::'):
            with override_settings(HOSTED=False), self.assertRaises(CommandError):
                self.serve(CU_BIND=bind)
        with override_settings(HOSTED=False):
            self.assertEqual(self.serve(CU_BIND='::1').call_args.kwargs['host'], '::1')

    def test_hosted_binds_where_it_is_told(self):
        with override_settings(HOSTED=True):
            self.assertEqual(self.serve(CU_BIND='0.0.0.0').call_args.kwargs['host'], '0.0.0.0')


class ThrottleKeyTests(TestCase):
    def request(self, **meta):
        return RequestFactory().get('/', **meta)

    def test_hosted_signins_are_keyed_on_the_visitor_not_the_tunnel(self):
        with override_settings(HOSTED=True):
            seen = self.request(REMOTE_ADDR='172.18.0.5', HTTP_CF_CONNECTING_IP='198.51.100.7')
            seen.access_email = 'staff@example.test'
            self.assertEqual(client_address(seen), '198.51.100.7')
            seen.META['HTTP_CF_CONNECTING_IP'] = 'not an address'
            self.assertEqual(client_address(seen), '172.18.0.5')
            seen.META.pop('HTTP_CF_CONNECTING_IP')
            self.assertEqual(client_address(seen), '172.18.0.5')
            unchecked = self.request(REMOTE_ADDR='172.18.0.5', HTTP_CF_CONNECTING_IP='198.51.100.7')   # never passed the gate
            self.assertEqual(client_address(unchecked), '172.18.0.5')

    def test_local_signins_are_keyed_on_the_peer_whatever_the_header_says(self):
        with override_settings(HOSTED=False):
            seen = self.request(REMOTE_ADDR='127.0.0.1', HTTP_CF_CONNECTING_IP='198.51.100.7')
            seen.access_email = 'staff@example.test'
            self.assertEqual(client_address(seen), '127.0.0.1')

    def test_one_visitors_failures_do_not_lock_out_another_behind_the_same_tunnel(self):
        import hashlib
        with K.hosted():
            blocked = hashlib.sha256(b'ip:198.51.100.7').hexdigest()
            LoginAttempt.objects.create(key=blocked, failures=8, window_start=timezone.now())

            def attempt(ip):
                made = K.client('staff@example.test', HTTP_CF_CONNECTING_IP=ip)
                return made.post('/api/auth/login', json.dumps({'username': 'x' + ip, 'password': 'wrong'}),
                                 content_type='application/json').status_code
            self.assertEqual(attempt('198.51.100.7'), 429)
            self.assertEqual(attempt('198.51.100.8'), 401)


class DistrictLinkTests(TestCase):
    def test_the_address_list_reads_json_or_pairs(self):
        want = {'co-05': 'https://co05.example.test/', 'or-05': 'https://or05.example.test'}
        self.assertEqual(D.district_urls({'CU_DISTRICT_URLS': json.dumps(want)}), want)
        self.assertEqual(D.district_urls({'CU_DISTRICT_URLS': 'co-05=https://co05.example.test/, or-05=https://or05.example.test'}), want)
        self.assertEqual(D.district_urls({}), {})
        self.assertEqual(D.district_urls({'CU_DISTRICT_URLS': '  '}), {})

    def test_a_bad_list_is_an_error_not_a_wrong_link(self):
        for bad in ('{"co-05": 5}', '[1]', '{not json', 'co-05', 'xx-99=https://a.example.test',
                    'co-05=javascript:alert(1)', 'co-05=ftp://a.example.test', 'co-05=https://user:pw@a.example.test',
                    'co-05=https://a.example.test/?x=1', 'co-05=https://a.example.test/#x', 'co-05=/relative', 'co-05='):
            with self.assertRaises(ValueError, msg=bad):
                D.district_urls({'CU_DISTRICT_URLS': bad})

    def listed(self):
        with override_settings(LOCAL_ADMIN_ACCESS=True, HOSTED=False):
            return Client(HTTP_HOST='localhost').get('/api/v3/districts').json()['districts']

    def test_unset_the_selector_is_what_it_always_was(self):
        with override_settings(DISTRICT_URLS={}):
            rows = self.listed()
        self.assertEqual({r['id'] for r in rows}, set(D.DISTRICTS))
        self.assertTrue(all('url' not in r for r in rows))

    def test_set_the_selector_links_to_the_listed_districts_only(self):
        urls = {'or-05': 'https://or05.example.test/', 'ct-01': 'https://ct01.example.test/'}
        with override_settings(DISTRICT_URLS=urls):
            rows = {r['id']: r for r in self.listed()}
        current = settings.DISTRICT['id']
        self.assertEqual(set(rows), {current, *urls})
        for ident, url in urls.items():
            self.assertEqual((rows[ident]['url'], rows[ident]['ready']), (url, True))
        self.assertNotIn('url', rows[current])
