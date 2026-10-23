"""Exercise owner access using the packaged Python, source and native token gate.

Uses synthetic accounts in a disposable database; never opens an installed profile.
The packaged Python omits django.test, so this check uses only the WSGI interface.
"""
import argparse
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
from wsgiref.util import setup_testing_defaults


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def probe(runtime, scratch):
    sys.path[:0] = [str(runtime / 'sources'), str(runtime / 'bootstrap')]
    for name in list(os.environ):
        if name.startswith(('CU_', 'ACCESS_', 'DESKTOP_', 'TERRIER_')):
            os.environ.pop(name)
    os.environ.update(DJANGO_SETTINGS_MODULE='server.settings', CU_DEPLOYMENT='desktop', CU_LOCAL_ADMIN='0',
                      CU_DATA_DIR=str(scratch / 'data'), CU_STATE_DIR=str(scratch / 'state'),
                      CU_DISTRICT='co-05', CU_SECRET_KEY='synthetic-packaged-owner-check-only',
                      CU_ALLOWED_HOSTS='127.0.0.1')
    import django
    django.setup()
    from django.core.management import call_command
    from django.core.wsgi import get_wsgi_application
    from django.contrib.auth.models import User
    from django.db import connections
    from server.research.desktop_access import DesktopBoundary
    from server.research.desktop_principal import parse_principal, prepare_principal
    from server.research.models import DesktopPrincipalBinding, Invitation
    from desktop_backend import GuardedWSGI

    call_command('migrate', interactive=False, verbosity=0)
    owner = User.objects.create_user('admin', is_staff=True)
    other = User.objects.create_user('recipient_' + '4' * 32, is_staff=True)
    original = (owner.password, other.password)
    common = dict(schemaVersion=1, serverId='11111111-1111-4111-8111-111111111111',
                  deviceId='22222222-2222-4222-8222-222222222222', districtId='co-05', role='local_administrator')
    principal = parse_principal(dict(common, principalId='33333333-3333-3333-3333-333333333333',
        accountIdentity='recipient_' + '3' * 32, localAccountIdentity='admin', localUserId=str(owner.pk),
        mode='existing-owner'), 'co-05')
    try:
        require(prepare_principal(principal, scratch / 'state') is None, 'Owner launch still asks to set a password')
        require(not Invitation.objects.exists(), 'Owner launch created a password invitation')
        token = secrets.token_urlsafe(32)
        boundary = DesktopBoundary('co-05', 51234, principal)
        application = GuardedWSGI(get_wsgi_application(), token, 'terrier', desktop_boundary=boundary)

        def request(path, **changes):
            environment = {}
            setup_testing_defaults(environment)
            environment.update(REQUEST_METHOD='GET', PATH_INFO=path, HTTP_HOST='127.0.0.1:51234',
                               REMOTE_ADDR='127.0.0.1', HTTP_X_DESKTOP_TOKEN=token,
                               SERVER_NAME='127.0.0.1', SERVER_PORT='51234',
                               **{'wsgi.input': io.BytesIO(b'')})
            environment.update(changes)
            captured = {}
            result = application(environment, lambda status, headers, exc_info=None:
                                 captured.update(status=int(status.split()[0])))
            try:
                body = b''.join(result)
            finally:
                if hasattr(result, 'close'):
                    result.close()
            payload = json.loads(body) if captured['status'] == 200 else None
            return captured['status'], payload

        for _ in range(2):  # no cookies or password, including a fresh subsequent request
            status, session = request('/api/auth/session')
            require(status == 200 and session.get('authenticated') is True
                    and session.get('administrator') is True and session.get('username') == 'admin'
                    and session.get('password_lock_disabled') is True, 'Packaged owner still requires password sign-in')
            require(request('/api/accounts')[0] == 200, 'Packaged owner cannot open a protected route')
        for headers in ({'HTTP_X_DESKTOP_TOKEN': 'wrong-token'}, {'REMOTE_ADDR': '192.0.2.5'},
                        {'HTTP_HOST': 'evil.example:51234'}, {'HTTP_ORIGIN': 'https://evil.example'},
                        {'HTTP_X_FORWARDED_FOR': '127.0.0.1'}):
            require(request('/api/auth/session', **headers)[0] == 403, 'Native boundary accepted an untrusted request')

        recipient = parse_principal(dict(common, principalId='44444444-4444-4444-4444-444444444444',
            accountIdentity=other.username, localAccountIdentity=other.username, localUserId=None, mode='recipient'), 'co-05')
        DesktopPrincipalBinding.objects.create(server_id=recipient.server_id, device_id=recipient.device_id,
            principal_id=recipient.principal_id, account_identity=recipient.account_identity,
            local_username=recipient.local_username, district_id='co-05', mode='recipient', user=other)
        boundary.principal = recipient
        status, session = request('/api/auth/session')
        require(status == 200 and session.get('authenticated') is False, 'Recipient inherited owner access')
        require(request('/api/accounts')[0] == 401, 'Recipient bypassed sign-in')
        owner.refresh_from_db()
        other.refresh_from_db()
        require((owner.password, other.password) == original, 'Verification changed an account password')
        return dict(ownerPasswordless=True, noOwnerInvitation=True, recipientRequiresSignin=True,
                    untrustedRequestsRefused=True, accountPasswordsUnchanged=True)
    finally:
        connections.close_all()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--probe', type=Path, help=argparse.SUPPRESS)
    options = parser.parse_args()
    runtime = options.runtime.resolve()
    if options.probe:
        print(json.dumps(probe(runtime, options.probe.resolve())))
        return
    with tempfile.TemporaryDirectory(prefix='terrier-packaged-owner-') as scratch:
        environment = {k: v for k, v in os.environ.items() if not k.startswith(('CU_', 'ACCESS_', 'DESKTOP_', 'TERRIER_'))}
        result = subprocess.run([str(runtime / 'python/python.exe'), '-E', '-s', '-B', str(Path(__file__).resolve()),
                                 '--runtime', str(runtime), '--probe', scratch],
                                env=environment, text=True, capture_output=True, timeout=120,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        require(result.returncode == 0, 'Packaged owner verification failed:\n' + result.stdout + result.stderr)
        report = json.loads(result.stdout)
        if options.output:
            options.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
