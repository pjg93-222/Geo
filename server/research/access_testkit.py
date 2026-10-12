"""Cloudflare-Access-shaped tokens for tests, signed with an RSA key made when the first test asks for it.

Nothing is stored: no key, PEM, JWKS or token is ever in the tree. Pure standard library (the Terrier environment does not
have `cryptography`): the key comes from Miller-Rabin primes and the signature is RSASSA-PKCS1-v1_5 / SHA-256, built the way
RFC 8017 section 9.2 describes. access_gate verifies it the same way it verifies Cloudflare's, which is the check.
"""
import base64
import contextlib
import hashlib
import json
import os
import secrets
import tempfile
import time
from unittest import mock

from django.conf import settings
from django.test import Client, override_settings

from . import access_gate as gate

AUD = 'test-aud-tag'
ISS = 'https://example-team.cloudflareaccess.com'
KID = 'kid-test'
E = 65537
_SMALL = [p for p in range(3, 2000, 2) if all(p % q for q in range(3, int(p ** 0.5) + 1, 2))]
_key = []


def _probably_prime(n, rounds=16):
    if any(n % p == 0 for p in _SMALL):
        return n in _SMALL
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(rounds):
        x = pow(secrets.randbelow(n - 3) + 2, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _prime(bits):
    while True:
        # top two bits set so that p * q has exactly 2 * bits bits; odd; e must not divide p - 1
        candidate = secrets.randbits(bits) | (3 << (bits - 2)) | 1
        if (candidate - 1) % E and _probably_prime(candidate):
            return candidate


def key():
    """(n, d) of a 2048-bit RSA key, made once per process."""
    if not _key:
        p, q = _prime(1024), _prime(1024)
        while q == p:
            q = _prime(1024)
        _key.append((p * q, pow(E, -1, (p - 1) * (q - 1))))
    return _key[0]


def b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def _be(number):
    return number.to_bytes((number.bit_length() + 7) // 8, 'big')


def jwks(kid=KID):
    n, _ = key()
    return {'keys': [{'kty': 'RSA', 'kid': kid, 'use': 'sig', 'alg': 'RS256', 'n': b64u(_be(n)), 'e': b64u(_be(E))}]}


def write_jwks(path, kid=KID):
    with open(path, 'w', encoding='utf-8') as stream:
        json.dump(jwks(kid), stream)


def token(email='staff@example.test', *, aud=AUD, iss=ISS, kid=KID, exp=None, tamper=False, **claims):
    """A signed RS256 token; `tamper` flips one bit of the signature."""
    n, d = key()
    body = {'aud': [aud], 'iss': iss, 'email': email, 'exp': int(time.time()) + 3600 if exp is None else exp,
            'nbf': int(time.time()) - 10, **claims}
    signing = (b64u(json.dumps({'alg': 'RS256', 'kid': kid, 'typ': 'JWT'}).encode()) + '.'
               + b64u(json.dumps(body).encode()))
    size = (n.bit_length() + 7) // 8
    digest_info = gate._SHA256_PREFIX + hashlib.sha256(signing.encode('ascii')).digest()
    encoded = b'\x00\x01' + b'\xff' * (size - len(digest_info) - 3) + b'\x00' + digest_info
    signature = bytearray(pow(int.from_bytes(encoded, 'big'), d, n).to_bytes(size, 'big'))
    if tamper:
        signature[-1] ^= 1
    return signing + '.' + b64u(bytes(signature))


# ---- a hosted deployment, in process ----------------------------------------------------------------------------------

HOST = 'co05.example.test'
ACCESS_MIDDLEWARE = 'server.research.access.AccessJwtMiddleware'
LOCAL_MIDDLEWARE = 'server.research.middleware.LocalAdministratorMiddleware'


@contextlib.contextmanager
def hosted(**overrides):
    """What server/settings.py builds when CU_DEPLOYMENT=hosted: Access first, no local administrator, the gate's environment
    pointing at a JWKS file of the test key. Clients made inside it are hosted clients."""
    with tempfile.TemporaryDirectory() as tmp:
        certs = os.path.join(tmp, 'certs.json')
        write_jwks(certs)
        env = {'ACCESS_AUD': AUD, 'ACCESS_ISSUER': ISS, 'ACCESS_JWKS_FILE': certs, 'CU_ALLOWED_HOSTS': HOST + ',127.0.0.1',
               'ACCESS_SERVICE_CNS': 'svc-client.access', 'ACCESS_ENFORCE': '1'}      # the image bakes ACCESS_ENFORCE=1
        stack = [ACCESS_MIDDLEWARE] + [m for m in settings.MIDDLEWARE if m not in (ACCESS_MIDDLEWARE, LOCAL_MIDDLEWARE)]
        with mock.patch.dict(os.environ, env), override_settings(
                HOSTED=True, LOCAL_ADMIN_ACCESS=False, MIDDLEWARE=stack, ALLOWED_HOSTS=[HOST, '127.0.0.1', 'testserver'],
                **overrides):
            yield


def client(email='staff@example.test', *, login=None, **extra):
    """A client as Cloudflare Access would deliver it: the tunnel's address, this host, a token for `email` (or none when
    email is None); `login` is a user to sign in as."""
    headers = {'HTTP_HOST': HOST, 'REMOTE_ADDR': '172.18.0.5', **extra}
    if email is not None:
        headers['HTTP_CF_ACCESS_JWT_ASSERTION'] = token(email)
    made = Client(**headers)
    if login is not None:
        made.force_login(login)
    return made
