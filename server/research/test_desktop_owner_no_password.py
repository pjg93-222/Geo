"""The approved existing desktop owner is never asked for a password; recipients and hosted are unchanged.

Synthetic accounts only. Requests go through the real middleware stack (a WSGI handler) with the native boundary
marker inserted exactly as the launcher's token gate does; prepare_principal runs for real outside a test
transaction, with the launcher's verified-backup step (desktop_migrations, Melange tooling) replaced by a direct call.
"""
import hashlib
import json
import sys
import tempfile
import types
from http.cookies import SimpleCookie
from unittest import mock

from django.contrib.auth.models import User
from django.core.handlers.wsgi import WSGIHandler
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings

from .desktop_access import DesktopBoundary, MARKER_KEY, password_lock_disabled, request_principal
from .desktop_principal import Principal, prepare_principal
from .models import AuditEvent, DesktopPrincipalBinding, Invitation
from .views import create_invitation

PASSWORD = 'Synthetic-Recipient-Password-For-Tests-73!'
SERVER, DEVICE, OWNER_ID = ('11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222',
                            '33333333-3333-4333-8333-333333333333')
RECIPIENT_ID = '44444444-4444-4444-8444-444444444444'
PORT = 51234
DESKTOP = dict(HOSTED=False, DESKTOP_BOUNDARY=True, LOCAL_ADMIN_ACCESS=False, DISTRICT={'id': 'co-05'},
               ALLOWED_HOSTS=['127.0.0.1'])
DIRECT_MIGRATION = types.SimpleNamespace(migrate_sqlite=lambda name, state, action, close, verify=None: action())


def identity(principal_id):
    return 'recipient_' + principal_id.replace('-', '')


def owner_principal(user, district='co-05'):
    return Principal(SERVER, DEVICE, OWNER_ID, identity(OWNER_ID), 'admin', district, 'existing-owner', str(user.pk))


def recipient_principal(district='co-05'):
    return Principal(SERVER, DEVICE, RECIPIENT_ID, identity(RECIPIENT_ID), identity(RECIPIENT_ID), district, 'recipient', None)


def bind(principal, user):
    return DesktopPrincipalBinding.objects.create(
        server_id=principal.server_id, device_id=principal.device_id, principal_id=principal.principal_id,
        account_identity=principal.account_identity, local_username=principal.local_username,
        district_id=principal.district_id, mode=principal.mode, user=user)


@override_settings(**DESKTOP)
class OwnerWithoutPasswordTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('admin', is_active=True, is_staff=True, is_superuser=True)   # no usable password
        self.binding = bind(owner_principal(self.owner), self.owner)
        self.cookies = SimpleCookie()

    def request(self, path, method='GET', data=None, principal=None, boundary=True, **headers):
        principal = principal or owner_principal(self.owner, self.binding.district_id)
        environment = RequestFactory().generic(method, path, data=json.dumps(data or {}) if method == 'POST' else '',
                                               content_type='application/json', HTTP_HOST='127.0.0.1:%d' % PORT,
                                               REMOTE_ADDR='127.0.0.1', **headers).environ
        environment['HTTP_COOKIE'] = self.cookies.output(header='', sep=';').strip()
        if method == 'POST':
            environment['HTTP_X_CSRFTOKEN'] = self.cookies['csrftoken'].value if 'csrftoken' in self.cookies else ''
        if boundary:
            DesktopBoundary(principal.district_id, PORT, principal).authorize_environment(environment)
        captured = {}
        result = WSGIHandler()(environment, lambda status, response_headers, exc_info=None:
                               captured.update(status=int(status.split()[0]), headers=response_headers))
        body = b''.join(result)
        for name, value in captured['headers']:
            if name.lower() == 'set-cookie':
                self.cookies.load(value)
        return captured['status'], json.loads(body) if body.startswith(b'{') else None

    def session(self, **options):
        return self.request('/api/auth/session', **options)[1]

    def test_owner_without_any_password_is_signed_in_by_the_launch(self):
        value = self.session()
        self.assertEqual((value['authenticated'], value['username'], value['administrator']), (True, 'admin', True))
        self.assertIs(value['password_lock_disabled'], True)     # the client skips a fresh password after a Windows lock
        self.assertEqual(self.request('/api/snapshots')[0], 200)  # a protected route, no sign-in
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.has_usable_password())        # nothing about the account changed

    def test_an_existing_owner_password_is_kept_and_never_needed(self):
        self.owner.set_password('Synthetic-Old-Owner-Password-12!')
        self.owner.save()
        before = User.objects.get(pk=self.owner.pk).password
        self.assertTrue(self.session()['authenticated'])
        self.assertEqual(self.request('/api/snapshots')[0], 200)
        self.assertEqual(User.objects.get(pk=self.owner.pk).password, before)

    def test_every_district_of_the_app_opens_without_a_prompt(self):
        for district in ('or-05', 'ct-01'):
            self.binding.district_id = district
            self.binding.save(update_fields=['district_id'])
            with self.settings(DISTRICT={'id': district}):
                self.assertTrue(self.session()['authenticated'], district)
                self.assertEqual(self.request('/api/snapshots')[0], 200, district)

    def test_signing_out_or_a_district_session_cookie_never_changes_who_the_owner_is(self):
        self.session()
        self.assertEqual(self.request('/api/auth/logout', 'POST')[0], 200)   # what the client sends on a Windows lock
        self.assertTrue(self.session()['authenticated'])
        other = User.objects.create_user('someone-else', password=PASSWORD, is_staff=True)
        from django.test import Client
        client = Client()
        client.force_login(other)
        self.cookies['sessionid'] = client.cookies['sessionid'].value
        self.assertEqual(self.session()['username'], 'admin')

    def test_an_owner_mapping_that_no_longer_matches_is_refused(self):
        self.owner.is_staff = False
        self.owner.save()
        self.assertFalse(self.session()['authenticated'])
        self.assertEqual(self.request('/api/snapshots')[0], 401)
        self.owner.is_staff, self.owner.is_active = True, False
        self.owner.save()
        self.assertEqual(self.request('/api/snapshots')[0], 401)
        stranger = User.objects.create_user('stranger', is_staff=True)
        forged = Principal(SERVER, DEVICE, OWNER_ID, identity(OWNER_ID), 'admin', 'co-05', 'existing-owner', str(stranger.pk))
        self.owner.is_active = True
        self.owner.save()
        self.assertEqual(self.request('/api/snapshots', principal=forged)[0], 401)   # another local account ID

    def test_no_boundary_or_a_forged_marker_gets_nothing(self):
        self.assertEqual(self.request('/api/snapshots', boundary=False)[0], 403)
        self.assertEqual(self.request('/api/auth/session', boundary=False)[0], 403)
        # An HTTP header cannot stand in for the in-process marker.
        status, _ = self.request('/api/snapshots', boundary=False, HTTP_TERRIER_DESKTOP_BOUNDARY='1')
        self.assertEqual(status, 403)
        request = RequestFactory().get('/api/snapshots', HTTP_HOST='127.0.0.1:%d' % PORT, REMOTE_ADDR='127.0.0.1')
        request.META[MARKER_KEY] = (object(), 'co-05', 'http://127.0.0.1:%d' % PORT, owner_principal(self.owner))
        self.assertIsNone(request_principal(request))   # a lookalike issuer is not the token gate's

    def test_lan_proxy_and_rebinding_requests_are_refused_at_the_boundary(self):
        refused = [dict(REMOTE_ADDR='192.168.1.20'), dict(HTTP_HOST='evil.example:%d' % PORT),
                   dict(HTTP_HOST='localhost:%d' % PORT), dict(HTTP_X_FORWARDED_FOR='203.0.113.9'),
                   dict(HTTP_FORWARDED='for=203.0.113.9'), dict(HTTP_ORIGIN='http://evil.example'),
                   dict(HTTP_SEC_FETCH_SITE='cross-site')]
        for headers in refused:
            environment = RequestFactory().get('/api/snapshots', **{'HTTP_HOST': '127.0.0.1:%d' % PORT,
                                                                    'REMOTE_ADDR': '127.0.0.1', **headers}).environ
            self.assertFalse(DesktopBoundary('co-05', PORT, owner_principal(self.owner)).authorize_environment(environment), headers)
            self.assertNotIn(MARKER_KEY, environment)

    def test_recipients_still_sign_in_with_their_own_password(self):
        recipient = User.objects.create_user(identity(RECIPIENT_ID), password=PASSWORD, is_staff=True)
        bind(recipient_principal(), recipient)
        principal = recipient_principal()
        value = self.session(principal=principal)
        self.assertFalse(value['authenticated'])
        self.assertIs(value['password_lock_disabled'], False)
        self.assertEqual(self.request('/api/snapshots', principal=principal)[0], 401)
        self.assertEqual(self.request('/api/auth/login', 'POST', {'username': recipient.username, 'password': 'wrong-wrong-wrong'},
                                      principal=principal)[0], 401)
        status, value = self.request('/api/auth/login', 'POST', {'username': recipient.username, 'password': PASSWORD},
                                     principal=principal)
        self.assertEqual((status, value['authenticated'], value['password_lock_disabled']), (200, True, False))
        self.assertEqual(self.request('/api/snapshots', principal=principal)[0], 200)
        recipient.set_unusable_password()                 # a recipient without a password is never let in
        recipient.save()
        self.assertEqual(self.request('/api/snapshots', principal=principal)[0], 401)
        self.assertFalse(password_lock_disabled(principal))

    def test_no_invitation_can_set_or_change_the_owner_password(self):
        token = create_invitation('admin', administrator=True, reset=True).partition('=')[2]
        self.binding.invitation_hash = hashlib.sha256(token.encode()).hexdigest()
        self.binding.save(update_fields=['invitation_hash'])
        self.session()
        status, _ = self.request('/api/auth/accept', 'POST', {'token': token, 'password': 'Synthetic-New-Password-55!'})
        self.assertEqual(status, 403)
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.has_usable_password())

    def test_hosted_never_uses_the_desktop_owner(self):
        with self.settings(HOSTED=True, DESKTOP_BOUNDARY=False):
            self.assertFalse(password_lock_disabled(owner_principal(self.owner)))
            environment = RequestFactory().get('/', HTTP_HOST='127.0.0.1:%d' % PORT, REMOTE_ADDR='127.0.0.1').environ
            self.assertFalse(DesktopBoundary('co-05', PORT, owner_principal(self.owner)).authorize_environment(environment))
        with self.settings(HOSTED=True):        # even if both flags were somehow on, hosted wins
            self.assertFalse(password_lock_disabled(owner_principal(self.owner)))


@override_settings(**DESKTOP)
class PrepareOwnerTests(TransactionTestCase):
    """prepare_principal outside a test transaction, as the launcher runs it."""

    def setUp(self):
        patch = mock.patch.dict(sys.modules, {'desktop_migrations': DIRECT_MIGRATION})
        patch.start()
        self.addCleanup(patch.stop)
        state = tempfile.TemporaryDirectory(prefix='terrier-owner-state-')
        self.addCleanup(state.cleanup)
        self.state = state.name

    def test_owner_launch_binds_without_an_invitation_even_beside_other_accounts(self):
        owner = User.objects.create_user('admin', is_active=True, is_staff=True)          # no password, not superuser
        User.objects.create_user(identity(RECIPIENT_ID), password=PASSWORD, is_staff=True)  # a recipient already exists
        principal = owner_principal(owner)
        self.assertIsNone(prepare_principal(principal, self.state))                         # no '/#invite=' launch path
        binding = DesktopPrincipalBinding.objects.get(principal_id=OWNER_ID)
        self.assertEqual((binding.user_id, binding.invitation_hash), (owner.pk, ''))
        self.assertFalse(Invitation.objects.exists())
        self.assertFalse(AuditEvent.objects.exists())
        self.assertIsNone(prepare_principal(principal, self.state))                         # and on every later launch
        owner.refresh_from_db()
        self.assertFalse(owner.has_usable_password())

    def test_owner_launch_still_refuses_an_unavailable_account(self):
        owner = User.objects.create_user('admin', is_active=False, is_staff=True)
        with self.assertRaises(ValueError):
            prepare_principal(owner_principal(owner), self.state)
        other = User.objects.create_user('other', is_active=True, is_staff=True)
        with self.assertRaises(ValueError):
            prepare_principal(Principal(SERVER, DEVICE, OWNER_ID, identity(OWNER_ID), 'admin', 'co-05', 'existing-owner',
                                        str(other.pk)), self.state)
        self.assertFalse(DesktopPrincipalBinding.objects.exists())

    def test_a_new_recipient_is_still_invited_to_choose_a_password(self):
        path = prepare_principal(recipient_principal(), self.state)
        self.assertTrue(path.startswith('/#invite='))
        self.assertTrue(DesktopPrincipalBinding.objects.get(principal_id=RECIPIENT_ID).invitation_hash)
