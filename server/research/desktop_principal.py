"""Native-approved account binding. No HTTP value can create a principal."""
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import re
from uuid import UUID

from django.conf import settings
from django.db import connections, transaction
from django.utils import timezone


@dataclass(frozen=True)
class Principal:
    server_id: str
    device_id: str
    principal_id: str
    account_identity: str
    local_username: str
    district_id: str
    mode: str
    local_user_id: str | None


def parse_principal(value, district):
    fields = {'schemaVersion', 'serverId', 'deviceId', 'principalId', 'accountIdentity',
              'localAccountIdentity', 'localUserId', 'role', 'districtId', 'mode'}
    if not isinstance(value, dict) or set(value) != fields or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1:
        raise ValueError('Approved desktop principal is required')
    for field in ('serverId', 'deviceId', 'principalId'):
        if not isinstance(value[field], str) or str(UUID(value[field])) != value[field] or UUID(value[field]).int == 0:
            raise ValueError('Invalid desktop principal identity')
    expected = 'recipient_' + value['principalId'].replace('-', '')
    if (district not in {'co-05', 'or-05', 'ct-01'} or value['districtId'] != district
            or value['accountIdentity'] != expected or value['role'] != 'local_administrator'
            or value['mode'] not in {'recipient', 'existing-owner'}
            or value['localAccountIdentity'] != ('admin' if value['mode'] == 'existing-owner' else expected)):
        raise ValueError('Invalid desktop account scope')
    if value['mode'] == 'existing-owner':
        if not isinstance(value['localUserId'], str) or not re.fullmatch(r'[1-9][0-9]{0,18}', value['localUserId']):
            raise ValueError('Exact existing owner identity is required')
    elif value['localUserId'] is not None:
        raise ValueError('Recipients cannot choose a local account ID')
    return Principal(value['serverId'], value['deviceId'], value['principalId'], expected,
                     value['localAccountIdentity'], district, value['mode'], value['localUserId'])


def _matches(binding, principal):
    return bool(binding and str(binding.server_id) == principal.server_id
                and str(binding.device_id) == principal.device_id
                and str(binding.principal_id) == principal.principal_id
                and binding.account_identity == principal.account_identity
                and binding.local_username == principal.local_username
                and binding.district_id == principal.district_id and binding.mode == principal.mode)


def binding_for(principal, *, lock=False):
    from .models import DesktopPrincipalBinding
    if not isinstance(principal, Principal) or settings.HOSTED or principal.district_id != settings.DISTRICT['id']:
        return None
    records = DesktopPrincipalBinding.objects.select_for_update() if lock else DesktopPrincipalBinding.objects
    binding = records.filter(server_id=principal.server_id, principal_id=principal.principal_id).first()
    return binding if _matches(binding, principal) else None


def prepare_principal(principal, state_root):
    """Bind a fresh approved recipient or explicitly migrated owner before serving.

    This runs only after verified schema backup/migration. A new binding/invitation
    gets its own verified backup; no account, password or role is created here.
    The existing owner has one desktop password for every district (desktop_owner.py):
    it is invited to choose it only while none is set, the one-time setup.
    """
    from django.contrib.auth.models import User
    from .models import DesktopPrincipalBinding, Invitation, AuditEvent
    from .views import create_invitation
    from . import desktop_owner
    from desktop_migrations import migrate_sqlite
    if not isinstance(principal, Principal) or settings.HOSTED or not settings.DESKTOP_BOUNDARY or principal.district_id != settings.DISTRICT['id']:
        raise ValueError('Approved desktop principal is required')
    if principal.mode == 'existing-owner' and desktop_owner.root(principal) is None:
        raise ValueError('The desktop owner password store is required')
    connection = connections['default']
    if connection.vendor != 'sqlite' or connection.in_atomic_block:
        raise ValueError('Desktop principal setup requires its independent local database')

    def inspect():
        binding = binding_for(principal)
        user = User.objects.filter(username=principal.local_username).first()
        if binding:
            if binding.user_id and (not user or user.pk != binding.user_id):
                raise ValueError('Approved account identity changed')
            if not binding.user_id and user:
                raise ValueError('An unrelated account already uses the approved name')
        elif (DesktopPrincipalBinding.objects.filter(account_identity=principal.account_identity).exists()
              or DesktopPrincipalBinding.objects.filter(local_username=principal.local_username).exists()):
            raise ValueError('Approved account mapping conflicts with existing state')
        if principal.mode == 'recipient' and user and not binding:
            raise ValueError('An unrelated account already uses the approved name')
        if principal.mode == 'existing-owner':
            if not user or str(user.pk) != principal.local_user_id or not user.is_active or not user.is_staff:
                raise ValueError('The approved existing owner is unavailable')
            if (desktop_owner.credential(principal) is None and not user.has_usable_password()
                    and (User.objects.count() != 1 or not user.is_superuser)):
                raise ValueError('First-password setup refuses ambiguous existing accounts')
        if user and (not user.is_active or not user.is_staff):
            raise ValueError('The approved account is disabled or lacks its existing role')
        return binding, user

    def needs_password(user):
        if principal.mode == 'existing-owner':
            return desktop_owner.credential(principal) is None
        return not user or not user.has_usable_password()

    existing, user = inspect()
    if principal.mode == 'existing-owner' and desktop_owner.credential(principal) is None and user.has_usable_password():
        # An owner who already has a password here keeps it, as the one password for every district.
        desktop_owner.set_credential(principal, user.password)
    if existing and user and not needs_password(user):
        return None
    result = {'launchPath': None}

    def issue():
        with connection.cursor() as cursor:
            cursor.execute('BEGIN IMMEDIATE')
            try:
                binding, user = inspect()
                if not binding:
                    binding = DesktopPrincipalBinding.objects.create(
                        server_id=principal.server_id, device_id=principal.device_id,
                        principal_id=principal.principal_id, account_identity=principal.account_identity,
                        local_username=principal.local_username, district_id=principal.district_id,
                        mode=principal.mode, user=user)
                if needs_password(user):
                    path = create_invitation(principal.local_username, administrator=True, reset=user is not None)
                    token = path.partition('=')[2]
                    digest = hashlib.sha256(token.encode()).hexdigest()
                    Invitation.objects.filter(token_hash=digest).update(expires_at=timezone.now()+timedelta(minutes=15))
                    binding.invitation_hash = digest
                    binding.save(update_fields=['invitation_hash'])
                    AuditEvent.objects.create(actor=principal.local_username, action='account.desktop_activation_invited', detail={})
                    # Fragments never enter HTTP requests, access logs or referrers.
                    result['launchPath'] = '/#invite=' + token
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    migrate_sqlite(connection.settings_dict['NAME'], state_root, issue, connections.close_all)
    return result['launchPath']


def accept_bound_invitation(principal, invitation, user):
    """Called inside the ordinary password acceptance transaction, before writes."""
    binding = binding_for(principal, lock=True)
    if not binding or binding.invitation_hash != invitation.token_hash or binding.local_username != invitation.username:
        return None
    if binding.user_id is not None:
        if not user or user.pk != binding.user_id or not invitation.reset:
            return None
    elif user or invitation.reset or not invitation.administrator or invitation.field_staff or invitation.field_manager:
        return None
    return binding


def bind_accepted_user(binding, user):
    if not transaction.get_connection().in_atomic_block:
        raise ValueError('Account binding requires the password transaction')
    if (user.username != binding.local_username or not user.is_active or not user.is_staff
            or not user.has_usable_password() or binding.user_id not in (None, user.pk)):
        raise ValueError('Approved account identity differs')
    binding.user = user
    binding.invitation_hash = ''
    binding.save(update_fields=['user', 'invitation_hash'])
