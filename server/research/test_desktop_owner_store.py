"""The owner credential store fails closed (M1), is private to this user (L2) and audits adoption (L3).

Synthetic accounts and a temporary owner directory only. prepare_principal runs for real; the native
launcher's verified-backup step (desktop_migrations, Melange tooling) is replaced by a direct call.
"""
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import TransactionTestCase, override_settings

from . import desktop_owner
from .desktop_owner import OwnerStoreUnavailable
from .desktop_principal import prepare_principal
from .models import AuditEvent, DesktopPrincipalBinding, Invitation
from .test_desktop_owner_password import PASSWORD, APP_SESSION, SERVER, DEVICE, OWNER_ID, foreign_credential, write_raw

DIRECT_MIGRATION = types.SimpleNamespace(migrate_sqlite=lambda name, state, action, close, verify=None: action())


@override_settings(HOSTED=False, DESKTOP_BOUNDARY=True, LOCAL_ADMIN_ACCESS=False, DISTRICT={'id': 'co-05'},
                   DESKTOP_OWNER_SESSION_KEY=APP_SESSION)
class PreparePrincipalStoreTests(TransactionTestCase):
    """prepare_principal outside a test transaction, as the launcher runs it."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='terrier-owner-store-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / 'terrier-owner'
        overrides = override_settings(DESKTOP_OWNER_ROOT=self.root)
        overrides.enable()
        self.addCleanup(overrides.disable)
        patch = mock.patch.dict(sys.modules, {'desktop_migrations': DIRECT_MIGRATION})
        patch.start()
        self.addCleanup(patch.stop)

    def owner(self, password=None):
        user = User.objects.create_user('admin', password=password, is_active=True, is_staff=True, is_superuser=True)
        from .desktop_principal import Principal
        principal = Principal(SERVER, DEVICE, OWNER_ID, 'recipient_' + OWNER_ID.replace('-', ''), 'admin', 'co-05',
                              'existing-owner', str(user.pk))
        DesktopPrincipalBinding.objects.create(server_id=SERVER, device_id=DEVICE, principal_id=OWNER_ID,
                                               account_identity=principal.account_identity, local_username='admin',
                                               district_id='co-05', mode='existing-owner', user=user)
        return user, principal

    def test_corrupt_or_foreign_credential_refuses_launch_and_creates_no_invitation(self):
        user, principal = self.owner()
        for text in ('{not json', foreign_credential(principal)):
            write_raw(self.root, text)
            with self.assertRaises(OwnerStoreUnavailable):
                prepare_principal(principal, self.root.parent)
            self.assertFalse(Invitation.objects.exists())
            self.assertFalse(AuditEvent.objects.exists())
            self.assertEqual((self.root / desktop_owner.CREDENTIAL).read_text(encoding='utf-8'), text)

    def test_missing_credential_invites_once_and_deletion_resets(self):
        user, principal = self.owner()
        self.assertTrue(prepare_principal(principal, self.root.parent).startswith('/#invite='))
        desktop_owner.set_credential(principal, make_password(PASSWORD))
        self.assertIsNone(prepare_principal(principal, self.root.parent))
        (self.root / desktop_owner.CREDENTIAL).unlink()
        self.assertTrue(prepare_principal(principal, self.root.parent).startswith('/#invite='))

    def test_adopting_an_existing_district_password_is_audited_once(self):
        user, principal = self.owner(password=PASSWORD)
        before = user.password
        self.assertIsNone(prepare_principal(principal, self.root.parent))
        self.assertEqual(desktop_owner.credential(principal), before)
        user.refresh_from_db()
        self.assertEqual(user.password, before)
        events = AuditEvent.objects.filter(action='account.desktop_owner_password_adopted')
        self.assertEqual([(e.actor, e.detail) for e in events], [('admin', {'district': 'co-05'})])
        self.assertFalse(Invitation.objects.exists())
        self.assertIsNone(prepare_principal(principal, self.root.parent))
        self.assertEqual(events.count(), 1)

    def test_failed_adoption_rolls_its_audit_event_back(self):
        user, principal = self.owner(password=PASSWORD)
        with mock.patch.object(desktop_owner, 'set_credential', side_effect=OwnerStoreUnavailable('synthetic')):
            with self.assertRaises(OwnerStoreUnavailable):
                prepare_principal(principal, self.root.parent)
        self.assertFalse(AuditEvent.objects.exists())
        self.assertIsNone(desktop_owner.credential(principal))
