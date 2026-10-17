"""Temporary local owner access retains the signed desktop boundary and account identity."""
import json
from dataclasses import replace

from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory, TestCase, override_settings

from .desktop_access import DesktopBoundary, DesktopPrincipalMiddleware
from .desktop_principal import Principal
from .models import DesktopPrincipalBinding
from .views import who


@override_settings(HOSTED=False, DESKTOP_BOUNDARY=True, DESKTOP_PASSWORD_LOCK_DISABLED=True,
                   LOCAL_ADMIN_ACCESS=False, DISTRICT={'id': 'co-05'}, ALLOWED_HOSTS=['127.0.0.1'])
class DesktopPasswordLockTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('admin', is_active=True, is_staff=True, is_superuser=True)
        self.principal = Principal('11111111-1111-4111-8111-111111111111',
                                   '22222222-2222-4222-8222-222222222222',
                                   '33333333-3333-4333-8333-333333333333',
                                   'recipient_33333333333343338333333333333333',
                                   'admin', 'co-05', 'existing-owner', str(self.owner.pk))
        self.binding = DesktopPrincipalBinding.objects.create(
            server_id=self.principal.server_id, device_id=self.principal.device_id,
            principal_id=self.principal.principal_id, account_identity=self.principal.account_identity,
            local_username='admin', district_id='co-05', mode='existing-owner', user=self.owner)

    def request(self, principal=None, trusted=True, **headers):
        request = RequestFactory().get('/api/auth/session', HTTP_HOST='127.0.0.1:51234',
                                       REMOTE_ADDR='127.0.0.1', **headers)
        request.user = AnonymousUser()
        request.session = {}
        if trusted:
            DesktopBoundary('co-05', 51234, principal or self.principal).authorize_environment(request.META)
        response = DesktopPrincipalMiddleware(who)(request)
        return request, response, json.loads(response.content)

    def test_exact_approved_owner_gets_request_only_access_without_changing_password(self):
        password = self.owner.password
        request, response, value = self.request()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(value['authenticated'])
        self.assertTrue(value['password_lock_disabled'])
        self.assertEqual(value['username'], 'admin')
        self.assertEqual(request.session, {})
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.password, password)
        self.assertFalse(self.owner.has_usable_password())
        self.assertEqual(User.objects.count(), 1)

    @override_settings(DESKTOP_PASSWORD_LOCK_DISABLED=False)
    def test_removing_switch_restores_signin(self):
        _, _, value = self.request()
        self.assertFalse(value['authenticated'])
        self.assertFalse(value['password_lock_disabled'])

    def test_untrusted_or_forwarded_request_cannot_use_switch(self):
        for headers in ({}, {'HTTP_X_FORWARDED_FOR': '127.0.0.1'}):
            _, response, _ = self.request(trusted=bool(headers), **headers)
            self.assertEqual(response.status_code, 403)

    def test_recipient_cannot_use_owner_switch(self):
        principal = replace(self.principal, mode='recipient', local_user_id=None)
        self.binding.mode = 'recipient'
        self.binding.save(update_fields=['mode'])
        _, _, value = self.request(principal=principal)
        self.assertFalse(value['authenticated'])

    def test_changed_account_identity_cannot_use_switch(self):
        _, _, value = self.request(principal=replace(self.principal, local_user_id=str(self.owner.pk + 1)))
        self.assertFalse(value['authenticated'])

    def test_disabled_owner_cannot_use_switch(self):
        self.owner.is_active = False
        self.owner.save(update_fields=['is_active'])
        _, _, value = self.request()
        self.assertFalse(value['authenticated'])

    @override_settings(HOSTED=True)
    def test_hosted_never_uses_switch(self):
        _, _, value = self.request()
        self.assertFalse(value['authenticated'])
        self.assertNotIn('password_lock_disabled', value)
