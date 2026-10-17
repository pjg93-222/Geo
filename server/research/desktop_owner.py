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
import tempfile
import time
from pathlib import Path

from django.conf import settings
from django.contrib.auth.hashers import check_password, identify_hasher

COOKIE = 'desktop_terrier_owner'
CREDENTIAL = 'owner-credential.json'
SESSION = 'owner-session.json'


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
    directory = root(principal)
    if directory is None:
        return None
    try:
        path = directory / name
        if path.is_symlink() or path.stat().st_size > 4096:
            return None
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if (not isinstance(value, dict) or set(value) != {'schemaVersion', 'binding', *fields}
            or value['schemaVersion'] != 1 or value['binding'] != _binding(principal)):
        return None
    return value


def _write(principal, name, fields):
    directory = root(principal)
    if directory is None:
        raise ValueError('Desktop owner storage is unavailable')
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix='.' + name + '.', suffix='.tmp')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as file:
            json.dump({'schemaVersion': 1, 'binding': _binding(principal), **fields}, file)
            file.flush()
            os.fsync(file.fileno())
        _retry(lambda: os.replace(temporary, directory / name))
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def credential(principal):
    """The owner's one password hash, or None until the one-time setup."""
    value = _read(principal, CREDENTIAL, {'password'})
    if not value or not isinstance(value['password'], str):
        return None
    try:
        identify_hasher(value['password'])
    except ValueError:
        return None
    return value['password']


def set_credential(principal, encoded):
    identify_hasher(encoded)   # a Django password hash; plaintext raises
    _write(principal, CREDENTIAL, {'password': encoded})


def check(principal, password):
    encoded = credential(principal)
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
    value = _read(principal, SESSION, {'token', 'app', 'expiresAt'})
    return bool(key and isinstance(token, str) and value and credential(principal)
                and isinstance(value['token'], str) and isinstance(value['app'], str)
                and type(value['expiresAt']) is int and time.time() < value['expiresAt']
                and hmac.compare_digest(value['app'], _digest(key))
                and hmac.compare_digest(value['token'], _digest(token)))


def end_session(principal, response):
    directory = root(principal)
    if directory is not None:
        _retry(lambda: (directory / SESSION).unlink(missing_ok=True))
    response.delete_cookie(COOKIE, path='/', samesite='Strict')
