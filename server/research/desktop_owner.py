"""One owner password and one sign-in for every district of a native desktop installation.

Only the explicitly migrated existing owner (principal mode 'existing-owner') uses this. It has a single password
for co-05, or-05 and ct-01: a Django password hash, never plaintext, kept in the desktop-level directory that the
native launcher names (CU_DESKTOP_OWNER_ROOT), outside every district's data directory. No district account or
password is changed. Signing in from any district writes one sign-in record there and sets one HttpOnly cookie for
the loopback host, so the other district backends of the same app session accept it without asking again.

The record is bound to the approved principal and to this app session's launcher key (CU_DESKTOP_OWNER_SESSION, a
fresh random value per app process), so every app launch signs in again. Signing out from any district ends the
sign-in everywhere; the native client signs out on a Windows lock or sign-out. Recipients, hosted deployments and
requests outside the native boundary never reach this module.
"""
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import tempfile
import time
from pathlib import Path

from django.conf import settings
from django.contrib.auth.hashers import check_password, identify_hasher

COOKIE = 'desktop_terrier_owner'
CREDENTIAL = 'owner-credential.json'
SESSION = 'owner-session.json'


class OwnerStoreUnavailable(ValueError):
    """The owner store exists but cannot be trusted. Never read as 'no password set yet': launch and sign-in fail closed."""


def root(principal):
    if (settings.HOSTED or not getattr(settings, 'DESKTOP_BOUNDARY', False) or principal is None
            or principal.mode != 'existing-owner' or not getattr(settings, 'DESKTOP_OWNER_ROOT', None)):
        return None
    return Path(settings.DESKTOP_OWNER_ROOT)


def _binding(principal):
    return {'serverId': principal.server_id, 'deviceId': principal.device_id,
            'principalId': principal.principal_id, 'localUsername': principal.local_username}


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _retry(action):
    # ponytail: a concurrent reader on Windows can briefly block replace/unlink; three short retries, then fail closed.
    for attempt in range(3):
        try:
            return action()
        except PermissionError:
            if attempt == 2:
                raise
            time.sleep(0.05)


def _read(principal, name, fields):
    """The record, or None ONLY when the file does not exist. Anything else present but unusable raises."""
    directory = root(principal)
    if directory is None:
        return None
    path = directory / name
    try:
        if path.is_symlink():
            raise OwnerStoreUnavailable(name + ' is a link')
        if path.stat().st_size > 4096:
            raise OwnerStoreUnavailable(name + ' is too large')
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except OwnerStoreUnavailable:
        raise
    except (OSError, ValueError) as error:
        raise OwnerStoreUnavailable(name + ' cannot be read') from error
    if (not isinstance(value, dict) or set(value) != {'schemaVersion', 'binding', *fields}
            or value['schemaVersion'] != 1):
        raise OwnerStoreUnavailable(name + ' has an unexpected schema')
    if value['binding'] != _binding(principal):
        raise OwnerStoreUnavailable(name + ' belongs to another approved owner')
    return value


def _system(name):
    return os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', name)


def _secure(directory):
    """Create the owner directory if needed and restrict it to this user and SYSTEM; fail closed."""
    directory.mkdir(parents=True, exist_ok=True)
    try:
        if os.name != 'nt':
            directory.chmod(0o700)
            return
        run = lambda *args: subprocess.run(args, capture_output=True, text=True, timeout=30, check=True).stdout
        sid = run(_system('whoami.exe'), '/user', '/fo', 'csv', '/nh').strip().split(',')[-1].strip('"')
        if not sid.startswith('S-1-'):
            raise OwnerStoreUnavailable('The current Windows account could not be identified')
        # This directory only: drop inherited entries, grant full control to this user and SYSTEM (inherited by its files).
        run(_system('icacls.exe'), str(directory), '/inheritance:r', '/grant:r', f'*{sid}:(OI)(CI)F', '*S-1-5-18:(OI)(CI)F')
        entries = [line for line in run(_system('icacls.exe'), str(directory)).splitlines() if ':(' in line]
        if len(entries) != 2 or any('(I)' in line for line in entries):
            raise OwnerStoreUnavailable('The desktop owner directory still grants access to others')
    except (OSError, subprocess.SubprocessError) as error:
        raise OwnerStoreUnavailable('The desktop owner directory could not be restricted to this user') from error


def _temporary(principal, name, fields):
    directory = root(principal)
    if directory is None:
        raise OwnerStoreUnavailable('Desktop owner storage is unavailable')
    _secure(directory)
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix='.' + name + '.', suffix='.tmp')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as file:
            json.dump({'schemaVersion': 1, 'binding': _binding(principal), **fields}, file)
            file.flush()
            os.fsync(file.fileno())
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return directory, Path(temporary)


def _write(principal, name, fields):
    directory, temporary = _temporary(principal, name, fields)
    try:
        _retry(lambda: os.replace(temporary, directory / name))
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def credential(principal):
    """The owner's one password hash; None ONLY while no credential file exists (the one-time setup, or a deliberate reset)."""
    value = _read(principal, CREDENTIAL, {'password'})
    if value is None:
        return None
    try:
        if not isinstance(value['password'], str):
            raise ValueError
        identify_hasher(value['password'])
    except ValueError as error:
        raise OwnerStoreUnavailable(CREDENTIAL + ' does not hold a password hash') from error
    return value['password']


def set_credential(principal, encoded):
    """Publish the one password exclusively: a hard link fails if a credential already exists. Never replaced."""
    identify_hasher(encoded)   # a Django password hash; plaintext raises
    directory, temporary = _temporary(principal, CREDENTIAL, {'password': encoded})
    try:
        os.link(temporary, directory / CREDENTIAL)
    except FileExistsError as error:
        raise OwnerStoreUnavailable('The owner password is already set') from error
    finally:
        temporary.unlink(missing_ok=True)


def check(principal, password):
    try:
        encoded = credential(principal)
    except OwnerStoreUnavailable:
        return False
    return bool(encoded and isinstance(password, str) and check_password(password, encoded))


def _app_key():
    key = getattr(settings, 'DESKTOP_OWNER_SESSION_KEY', '')
    return key if isinstance(key, str) and len(key) >= 32 else None


def start_session(principal):
    """After a verified owner password: replace the one sign-in record and return the cookie value."""
    key = _app_key()
    if key is None or credential(principal) is None:
        raise ValueError('Desktop owner sign-in is unavailable')
    token = secrets.token_urlsafe(32)
    _write(principal, SESSION, {'token': _digest(token), 'app': _digest(key),
                                'expiresAt': int(time.time()) + settings.SESSION_COOKIE_AGE})
    return token


def set_cookie(response, token):
    response.set_cookie(COOKIE, token, max_age=settings.SESSION_COOKIE_AGE, httponly=True, samesite='Strict', path='/')


def session_valid(request, principal):
    key, token = _app_key(), request.COOKIES.get(COOKIE)
    try:
        value = _read(principal, SESSION, {'token', 'app', 'expiresAt'})
        if value and not credential(principal):
            return False
    except OwnerStoreUnavailable:
        return False
    return bool(key and isinstance(token, str) and value
                and isinstance(value['token'], str) and isinstance(value['app'], str)
                and type(value['expiresAt']) is int and time.time() < value['expiresAt']
                and hmac.compare_digest(value['app'], _digest(key))
                and hmac.compare_digest(value['token'], _digest(token)))


def end_session(principal, response):
    directory = root(principal)
    if directory is not None:
        _retry(lambda: (directory / SESSION).unlink(missing_ok=True))
    response.delete_cookie(COOKIE, path='/', samesite='Strict')
