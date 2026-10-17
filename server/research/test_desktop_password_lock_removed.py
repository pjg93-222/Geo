"""The removed `.desktop-password-lock-disabled` switch has no effect anywhere.

A flag file left in a district data directory by an earlier build is inert: the
approved, bound desktop owner without a password is never signed in by it.
"""
import importlib.util
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory, TestCase, override_settings

from .desktop_access import DesktopBoundary, DesktopPrincipalMiddleware, administrator_authorized
from .desktop_principal import Principal
from .models import DesktopPrincipalBinding
from .views import who

FLAG = '.desktop-password-lock-disabled'


@override_settings(HOSTED=False, DESKTOP_BOUNDARY=True, LOCAL_ADMIN_ACCESS=False,
                   DISTRICT={'id': 'co-05'}, ALLOWED_HOSTS=['127.0.0.1'])
class RemovedPasswordLockSwitchTests(TestCase):
    def setUp(self):
        self.data = tempfile.TemporaryDirectory(prefix='terrier-flag-')
        self.addCleanup(self.data.cleanup)
        Path(self.data.name, FLAG).write_text('Temporary password lock disabled (synthetic test)', encoding='utf-8')
        self.owner = User.objects.create_user('admin', is_active=True, is_staff=True, is_superuser=True)
        self.principal = Principal('11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222',
                                   '33333333-3333-4333-8333-333333333333', 'recipient_33333333333343338333333333333333',
                                   'admin', 'co-05', 'existing-owner', str(self.owner.pk))
        DesktopPrincipalBinding.objects.create(
            server_id=self.principal.server_id, device_id=self.principal.device_id,
            principal_id=self.principal.principal_id, account_identity=self.principal.account_identity,
            local_username='admin', district_id='co-05', mode='existing-owner', user=self.owner)

    def request(self, path='/api/auth/session'):
        request = RequestFactory().get(path, HTTP_HOST='127.0.0.1:51234', REMOTE_ADDR='127.0.0.1')
        request.user = AnonymousUser()
        request.session = {}
        DesktopBoundary('co-05', 51234, self.principal).authorize_environment(request.META)
        return request, DesktopPrincipalMiddleware(who)(request)

    def test_flag_in_data_directory_does_not_sign_in_the_passwordless_owner(self):
        # Even the old setting name, forced on, is read by nothing.
        with self.settings(DATA_DIR=Path(self.data.name), DESKTOP_PASSWORD_LOCK_DISABLED=True):
            request, response = self.request()
            value = json.loads(response.content)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(value['authenticated'])
            self.assertIsNone(value['username'])
            self.assertNotIn('password_lock_disabled', value)
            self.assertFalse(request.user.is_authenticated)
            self.assertFalse(administrator_authorized(request))
            _, protected = self.request('/api/snapshots')
            self.assertEqual(protected.status_code, 401)
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.has_usable_password())

    def test_settings_never_derive_anything_from_the_flag(self):
        environment = {'CU_DEPLOYMENT': 'desktop', 'CU_DATA_DIR': self.data.name, 'CU_SECRET_KEY': 'synthetic-test-only',
                       'CU_DISTRICT': 'co-05'}
        spec = importlib.util.spec_from_file_location('terrier_settings_probe', Path(settings.BASE_DIR) / 'server' / 'settings.py')
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, environment):
            os.environ.pop('CU_STATE_DIR', None)
            spec.loader.exec_module(module)
        self.assertTrue(module.DESKTOP_BOUNDARY)
        self.assertFalse(hasattr(module, 'DESKTOP_PASSWORD_LOCK_DISABLED'))
        source = (Path(settings.BASE_DIR) / 'server').rglob('*.py')
        self.assertEqual([p.name for p in source if FLAG in p.read_text(encoding='utf-8') and p.name != Path(__file__).name], [])
