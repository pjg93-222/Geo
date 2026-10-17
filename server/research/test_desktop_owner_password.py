"""One owner password, set once, and one sign-in per app session for every district (D2).

Synthetic accounts, a temporary desktop-level owner directory and the real middleware stack
through a WSGI handler, with the native boundary marker inserted as the launcher's token gate does.
"""
import json
import os
import subprocess
import tempfile
import unittest
from http.cookies import SimpleCookie
from pathlib import Path
from unittest import mock

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core.handlers.wsgi import WSGIHandler
from django.test import RequestFactory, TestCase, override_settings

from . import desktop_owner
from .desktop_access import DesktopBoundary
from .desktop_owner import OwnerStoreUnavailable
from .desktop_principal import Principal
from .models import AuditEvent, DesktopPrincipalBinding, Invitation, LoginAttempt
from .views import create_invitation

PASSWORD = 'Synthetic-Owner-Password-For-Tests-91!'
APP_SESSION = 'a' * 43
SERVER, DEVICE, OWNER_ID = ('11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222',
                            '33333333-3333-4333-8333-333333333333')
RECIPIENT_ID = '44444444-4444-4444-8444-444444444444'


def write_raw(root, text):
    root.mkdir(parents=True, exist_ok=True)
    (root / desktop_owner.CREDENTIAL).write_text(text, encoding='utf-8')


def foreign_credential(principal):
    return json.dumps({'schemaVersion': 1, 'binding': {'serverId': principal.server_id, 'deviceId': principal.device_id,
                       'principalId': '55555555-5555-4555-8555-555555555555', 'localUsername': 'admin'},
                       'password': make_password('Another-Owner-Password-For-Tests-1!')})


class DesktopOwnerTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='terrier-owner-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / 'terrier-owner'
        self.overrides = override_settings(HOSTED=False, DESKTOP_BOUNDARY=True, LOCAL_ADMIN_ACCESS=False,
                                           DISTRICT={'id': 'co-05'}, ALLOWED_HOSTS=['127.0.0.1'],
                                           DESKTOP_OWNER_ROOT=self.root, DESKTOP_OWNER_SESSION_KEY=APP_SESSION)
        self.overrides.enable()
        self.addCleanup(self.overrides.disable)
        self.owner = User.objects.create_user('admin', is_active=True, is_staff=True, is_superuser=True)   # no usable password
        self.binding = DesktopPrincipalBinding.objects.create(
            server_id=SERVER, device_id=DEVICE, principal_id=OWNER_ID, account_identity=self.identity(OWNER_ID),
            local_username='admin', district_id='co-05', mode='existing-owner', user=self.owner)
        self.cookies = SimpleCookie()

    @staticmethod
    def identity(principal_id):
        return 'recipient_' + principal_id.replace('-', '')

    def principal(self, district='co-05', principal_id=OWNER_ID, mode='existing-owner'):
        username = 'admin' if mode == 'existing-owner' else self.identity(principal_id)
        local_id = str(self.owner.pk) if mode == 'existing-owner' else None
        return Principal(SERVER, DEVICE, principal_id, self.identity(principal_id), username, district, mode, local_id)

    def move_to(self, district):
        """A second district backend of the same app session: its own bound account record, same owner directory."""
        self.binding.district_id = district
        self.binding.save(update_fields=['district_id'])
        return override_settings(DISTRICT={'id': district})

    def request(self, path, method='GET', data=None, principal=None):
        principal = principal or self.principal(self.binding.district_id)
        environment = RequestFactory().generic(method, path, data=json.dumps(data or {}) if method == 'POST' else '',
                                               content_type='application/json', HTTP_HOST='127.0.0.1:51234',
                                               REMOTE_ADDR='127.0.0.1').environ
        environment['HTTP_COOKIE'] = self.cookies.output(header='', sep=';').strip()
        if method == 'POST':
            environment['HTTP_X_CSRFTOKEN'] = self.cookies['csrftoken'].value if 'csrftoken' in self.cookies else ''
        DesktopBoundary(principal.district_id, 51234, principal).authorize_environment(environment)
        captured = {}
        result = WSGIHandler()(environment, lambda status, headers, exc_info=None: captured.update(status=int(status.split()[0]), headers=headers))
        body = b''.join(result)
        for name, value in captured['headers']:
            if name.lower() == 'set-cookie':
                self.cookies.load(value)
        for name in [key for key, morsel in self.cookies.items() if str(morsel['max-age']) == '0' or morsel.value == '']:
            del self.cookies[name]
        return captured['status'], json.loads(body) if body.startswith(b'{') else None

    def csrf(self, principal=None):
        self.request('/api/auth/session', principal=principal)

    def setup_password(self, password=PASSWORD):
        path = create_invitation('admin', administrator=True, reset=True)
        token = path.partition('=')[2]
        import hashlib
        self.binding.invitation_hash = hashlib.sha256(token.encode()).hexdigest()
        self.binding.save(update_fields=['invitation_hash'])
        self.csrf()
        return self.request('/api/auth/accept', 'POST', {'token': token, 'password': password})

    def sign_in(self, password=PASSWORD, principal=None):
        self.csrf(principal)
        return self.request('/api/auth/login', 'POST', {'username': 'admin', 'password': password}, principal=principal)

    def session(self, principal=None):
        return self.request('/api/auth/session', principal=principal)[1]

    def test_one_time_setup_sets_one_hashed_password_and_signs_in(self):
        before = self.owner.password
        status, value = self.setup_password()
        self.assertEqual(status, 200, value)
        self.assertTrue(value['authenticated'])
        self.assertEqual(value['username'], 'admin')
        stored = json.loads((self.root / desktop_owner.CREDENTIAL).read_text(encoding='utf-8'))
        self.assertTrue(stored['password'].startswith(('argon2$', 'pbkdf2_')))
        self.assertNotIn(PASSWORD, (self.root / desktop_owner.CREDENTIAL).read_text(encoding='utf-8'))
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.password, before)          # the district account is unchanged
        self.assertFalse(self.owner.has_usable_password())
        self.binding.refresh_from_db()
        self.assertEqual(self.binding.invitation_hash, '')
        self.assertTrue(AuditEvent.objects.filter(action='account.desktop_owner_password').exists())
        # Set once: a second, stale setup link cannot replace it.
        status, value = self.setup_password('Another-Synthetic-Password-55!')
        self.assertEqual(status, 400)
        self.assertTrue(desktop_owner.check(self.principal(), PASSWORD))

    def test_one_sign_in_opens_every_district_of_the_app_session(self):
        self.setup_password()
        self.cookies = SimpleCookie()                          # a new app launch: nothing carried over
        self.assertFalse(self.session()['authenticated'])
        status, value = self.sign_in()
        self.assertEqual((status, value['authenticated']), (200, True))
        for district in ('or-05', 'ct-01'):
            with self.move_to(district):
                value = self.session()
                self.assertTrue(value['authenticated'], district)
                self.assertEqual(self.request('/api/snapshots')[0], 200, district)   # no second password prompt

    def test_wrong_password_signs_in_nowhere(self):
        self.setup_password()
        self.cookies = SimpleCookie()
        record = (self.root / desktop_owner.SESSION).read_text(encoding='utf-8')
        status, value = self.sign_in('Wrong-Synthetic-Password-00!')
        self.assertEqual(status, 401)
        self.assertNotIn(desktop_owner.COOKIE, self.cookies)
        self.assertEqual((self.root / desktop_owner.SESSION).read_text(encoding='utf-8'), record)   # not replaced
        self.assertEqual(sorted(LoginAttempt.objects.values_list('failures', flat=True)), [1, 1])
        with self.move_to('or-05'):
            self.assertFalse(self.session()['authenticated'])
            self.assertEqual(self.request('/api/snapshots')[0], 401)

    def test_no_password_set_means_no_sign_in(self):
        self.assertEqual(self.sign_in()[0], 401)
        self.assertFalse(self.session()['authenticated'])

    def test_sign_out_on_a_screen_lock_ends_the_sign_in_in_every_district(self):
        self.setup_password()
        cookie = self.cookies[desktop_owner.COOKIE].value
        with self.move_to('or-05'):
            self.csrf()
            self.assertEqual(self.request('/api/auth/logout', 'POST')[0], 200)   # what the client sends on a lock
        self.assertFalse((self.root / desktop_owner.SESSION).exists())
        self.cookies[desktop_owner.COOKIE] = cookie                                # even a replayed cookie
        for district in ('co-05', 'or-05', 'ct-01'):
            with self.move_to(district):
                self.assertFalse(self.session()['authenticated'], district)
        with self.move_to('ct-01'):
            self.assertTrue(self.sign_in()[1]['authenticated'])                   # a fresh password reopens them

    def test_a_new_app_session_signs_in_again(self):
        self.setup_password()
        with self.settings(DESKTOP_OWNER_SESSION_KEY='b' * 43):
            self.assertFalse(self.session()['authenticated'])
        with self.settings(DESKTOP_OWNER_SESSION_KEY=''):
            self.assertFalse(self.session()['authenticated'])

    def test_district_session_cookie_never_signs_in_the_owner(self):
        self.owner.set_password(PASSWORD)
        self.owner.save()
        from django.test import Client
        client = Client()
        client.force_login(self.owner)                          # a stale district session for 'admin'
        self.cookies['sessionid'] = client.cookies['sessionid'].value
        self.assertFalse(self.session()['authenticated'])
        self.assertEqual(self.request('/api/snapshots')[0], 401)

    def test_owner_sign_in_needs_the_desktop_store(self):
        self.setup_password()
        self.cookies = SimpleCookie()
        with self.settings(DESKTOP_OWNER_ROOT=None):
            self.assertEqual(self.sign_in()[0], 401)

    def test_recipients_keep_their_district_password_and_never_use_the_owner_sign_in(self):
        self.setup_password()                                   # an owner sign-in is live in this app session
        recipient = User.objects.create_user(self.identity(RECIPIENT_ID), password=PASSWORD, is_staff=True)
        DesktopPrincipalBinding.objects.create(
            server_id=SERVER, device_id=DEVICE, principal_id=RECIPIENT_ID, account_identity=recipient.username,
            local_username=recipient.username, district_id='co-05', mode='recipient', user=recipient)
        principal = self.principal(principal_id=RECIPIENT_ID, mode='recipient')
        self.assertFalse(self.session(principal)['authenticated'])          # the owner cookie is not theirs
        self.csrf(principal)
        status, value = self.request('/api/auth/login', 'POST', {'username': recipient.username, 'password': 'wrong-wrong-wrong'},
                                     principal=principal)
        self.assertEqual(status, 401)
        self.csrf(principal)
        status, value = self.request('/api/auth/login', 'POST', {'username': recipient.username, 'password': PASSWORD},
                                     principal=principal)
        self.assertEqual((status, value['authenticated'], value['username']), (200, True, recipient.username))
        self.assertIsNone(desktop_owner.credential(principal))

    def test_hosted_and_unbounded_requests_never_reach_the_owner_store(self):
        with self.settings(HOSTED=True):
            self.assertIsNone(desktop_owner.root(self.principal()))
        with self.settings(DESKTOP_BOUNDARY=False):
            self.assertIsNone(desktop_owner.root(self.principal()))
        self.assertIsNone(desktop_owner.root(self.principal(principal_id=RECIPIENT_ID, mode='recipient')))
        with self.assertRaises(ValueError):
            desktop_owner.set_credential(self.principal(), PASSWORD)          # plaintext is refused

    def test_unreadable_or_foreign_credential_fails_sign_in_closed(self):
        for text in ('{not json', foreign_credential(self.principal()), json.dumps({'schemaVersion': 1})):
            write_raw(self.root, text)
            self.cookies = SimpleCookie()
            self.assertEqual(self.sign_in()[0], 401, text)
            self.assertFalse(self.session()['authenticated'])
            with self.assertRaises(OwnerStoreUnavailable):
                desktop_owner.credential(self.principal())
            self.assertEqual((self.root / desktop_owner.CREDENTIAL).read_text(encoding='utf-8'), text)

    def test_pending_owner_invitation_is_refused_and_the_file_is_unchanged(self):
        self.setup_password()
        before = (self.root / desktop_owner.CREDENTIAL).read_bytes()
        status, _ = self.setup_password('Another-Synthetic-Password-55!')
        self.assertEqual(status, 400)
        self.assertEqual((self.root / desktop_owner.CREDENTIAL).read_bytes(), before)
        self.assertEqual(Invitation.objects.filter(used_at=None).count(), 1)     # the second invitation stays unspent
        # A damaged credential: the invitation is refused too and nothing is written.
        write_raw(self.root, '{not json')
        status, _ = self.setup_password('Another-Synthetic-Password-55!')
        self.assertEqual(status, 400)
        self.assertEqual((self.root / desktop_owner.CREDENTIAL).read_text(encoding='utf-8'), '{not json')

    def test_a_second_set_credential_raises_and_never_replaces(self):
        desktop_owner.set_credential(self.principal(), make_password(PASSWORD))
        before = (self.root / desktop_owner.CREDENTIAL).read_bytes()
        with self.assertRaises(OwnerStoreUnavailable):
            desktop_owner.set_credential(self.principal(), make_password('Another-Synthetic-Password-55!'))
        self.assertEqual((self.root / desktop_owner.CREDENTIAL).read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), [desktop_owner.CREDENTIAL])   # no temp file left
        self.assertTrue(desktop_owner.check(self.principal(), PASSWORD))

    def test_deleting_the_credential_is_the_deliberate_reset(self):
        desktop_owner.set_credential(self.principal(), make_password(PASSWORD))
        (self.root / desktop_owner.CREDENTIAL).unlink()
        self.assertIsNone(desktop_owner.credential(self.principal()))
        self.assertEqual(self.setup_password('Another-Synthetic-Password-55!')[0], 200)

    @staticmethod
    def windows_sid():
        whoami = os.path.join(os.environ['SystemRoot'], 'System32', 'whoami.exe')
        return subprocess.run([whoami, '/user', '/fo', 'csv', '/nh'], capture_output=True, text=True, check=True).stdout.strip().split(',')[-1].strip('"')

    @unittest.skipUnless(os.name == 'nt', 'Windows ACL')
    def test_owner_directory_is_restricted_to_this_user_and_system(self):
        desktop_owner.set_credential(self.principal(), make_password(PASSWORD))
        sid = self.windows_sid()
        flags, aces = desktop_owner._dacl(self.root)
        self.assertIn('P', flags)
        self.assertEqual(sorted((ace[0], ace[2], ace[5]) for ace in aces), sorted([('A', 'FA', sid), ('A', 'FA', 'SY')]))
        _, inherited = desktop_owner._dacl(self.root / desktop_owner.CREDENTIAL)
        self.assertEqual(sorted(ace[5] for ace in inherited), sorted([sid, 'SY']))
        self.assertTrue(all('ID' in ace[1] for ace in inherited))

    @unittest.skipUnless(os.name == 'nt', 'Windows ACL')
    def test_a_mandatory_integrity_label_is_not_an_access_grant(self):
        # An elevated process (a hosted CI runner) labels what it creates; the label is in the SACL, not the DACL.
        self.root.mkdir(parents=True)
        icacls = os.path.join(os.environ['SystemRoot'], 'System32', 'icacls.exe')
        subprocess.run([icacls, str(self.root), '/setintegritylevel', '(OI)(CI)low'], capture_output=True, check=True)
        desktop_owner.set_credential(self.principal(), make_password(PASSWORD))
        self.assertTrue(desktop_owner.check(self.principal(), PASSWORD))

    @unittest.skipUnless(os.name == 'nt', 'Windows ACL')
    def test_another_explicit_grant_fails_closed(self):
        self.root.mkdir(parents=True)
        icacls = os.path.join(os.environ['SystemRoot'], 'System32', 'icacls.exe')
        subprocess.run([icacls, str(self.root), '/grant', '*S-1-1-0:(OI)(CI)R'], capture_output=True, check=True)   # Everyone
        with self.assertRaises(OwnerStoreUnavailable):
            desktop_owner.set_credential(self.principal(), make_password(PASSWORD))
        self.assertFalse((self.root / desktop_owner.CREDENTIAL).exists())

    def test_restriction_failure_writes_nothing(self):
        with mock.patch.object(desktop_owner.subprocess, 'run', side_effect=subprocess.CalledProcessError(5, 'icacls')), \
                mock.patch.object(desktop_owner.os, 'name', 'nt'):
            with self.assertRaises(OwnerStoreUnavailable):
                desktop_owner.set_credential(self.principal(), make_password(PASSWORD))
        self.assertFalse((self.root / desktop_owner.CREDENTIAL).exists())
