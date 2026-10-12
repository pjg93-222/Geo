import os
from pathlib import Path

from tools import districts as D

BASE_DIR = Path(__file__).resolve().parent.parent
# One server process serves one district (CU_DISTRICT, default co-05): its data directory, cookies, snapshot keys and bounds.
DISTRICT = D.get(os.environ.get('CU_DISTRICT') or None)
LOCAL_ROOT = BASE_DIR / '.local'
DATA_DIR = Path(os.environ.get('CU_DATA_DIR', str(D.data_dir(DISTRICT, LOCAL_ROOT)))).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
# Hosted, the stores in CU_DATA_DIR are mounted read-only; the accounts database and the imported summary snapshots live in
# CU_STATE_DIR, its own writable volume (server/research/state.py). Unset (local): they are in DATA_DIR, as always.
STATE_DIR = Path(os.environ['CU_STATE_DIR']).resolve() if os.environ.get('CU_STATE_DIR') else None
if STATE_DIR is not None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
SECRET_KEY = os.environ.get('CU_SECRET_KEY')
if not SECRET_KEY:
    if os.environ.get('CU_DEPLOYMENT') == 'hosted':
        raise RuntimeError('Set CU_SECRET_KEY for hosted deployments.')
    key_file = DATA_DIR / '.secret'
    if not key_file.exists():
        import secrets
        key_file.write_text(secrets.token_urlsafe(64), encoding='utf-8')
    SECRET_KEY = key_file.read_text(encoding='utf-8')
DEBUG = False
ALLOWED_HOSTS = os.environ.get('CU_ALLOWED_HOSTS', '127.0.0.1,localhost,testserver').split(',')
ROOT_URLCONF = 'server.urls'
WSGI_APPLICATION = 'server.wsgi.application'
INSTALLED_APPS = ['django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions','django.contrib.messages','django.contrib.staticfiles','server.research']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware','server.research.demo.DemoGuardMiddleware','django.middleware.gzip.GZipMiddleware','django.middleware.http.ConditionalGetMiddleware','django.contrib.sessions.middleware.SessionMiddleware','django.middleware.common.CommonMiddleware','django.middleware.csrf.CsrfViewMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware','server.research.middleware.LocalAdministratorMiddleware','django.contrib.messages.middleware.MessageMiddleware','django.middleware.clickjacking.XFrameOptionsMiddleware']
DATABASES = {'default': {'ENGINE':'django.db.backends.sqlite3','NAME':str((STATE_DIR or DATA_DIR)/'accounts.sqlite3'),'OPTIONS':{'timeout':30}}}
PASSWORD_HASHERS = ['django.contrib.auth.hashers.Argon2PasswordHasher','django.contrib.auth.hashers.PBKDF2PasswordHasher']
AUTH_PASSWORD_VALIDATORS = [{'NAME':'django.contrib.auth.password_validation.MinimumLengthValidator','OPTIONS':{'min_length':12}},{'NAME':'django.contrib.auth.password_validation.CommonPasswordValidator'},{'NAME':'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'}]
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_TZ = True
TIME_ZONE = 'UTC'
# v3 preview (127.0.0.1:8766): cookies are per host, not per port, so a second local instance would overwrite 8765's
# session and CSRF cookies. CU_COOKIE_PREFIX (for example 'cu_v3_') gives this instance its own cookie names.
# Another district's instance adds its own tag, so two districts never share a session or CSRF cookie.
COOKIE_PREFIX = os.environ.get('CU_COOKIE_PREFIX', '') + ('' if DISTRICT['id'] == D.DEFAULT else DISTRICT['id'].replace('-', '') + '_')
SESSION_COOKIE_NAME = COOKIE_PREFIX + 'sessionid'
CSRF_COOKIE_NAME = COOKIE_PREFIX + 'csrftoken'
# CU_DEMO=1 forces demo mode for the whole instance (display-only pseudonymisation; cannot be switched off from the browser).
DEMO_MODE = os.environ.get('CU_DEMO') == '1'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Strict'
SESSION_COOKIE_AGE = 8*60*60
CSRF_COOKIE_SAMESITE = 'Strict'
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
HOSTED = os.environ.get('CU_DEPLOYMENT') == 'hosted'
LOCAL_ADMIN_ACCESS = not HOSTED and os.environ.get('CU_LOCAL_ADMIN', '1') == '1'
SESSION_COOKIE_SECURE = HOSTED
CSRF_COOKIE_SECURE = HOSTED
SECURE_SSL_REDIRECT = HOSTED and os.environ.get('CU_HTTPS_REDIRECT','1') == '1'
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https') if HOSTED else None
CSRF_TRUSTED_ORIGINS = [x for x in os.environ.get('CU_TRUSTED_ORIGINS','').split(',') if x]
DATA_UPLOAD_MAX_MEMORY_SIZE = 100*1024*1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2*1024*1024
STATIC_URL = '/static/'
LOGGING = {'version':1,'disable_existing_loggers':False,'handlers':{'console':{'class':'logging.StreamHandler'}},'root':{'handlers':['console'],'level':'INFO'}}

SECURE_REDIRECT_EXEMPT = [r'^api/health$']

# ---- hosted: one district per container, behind Cloudflare Access (see HOSTED.md) ----------------------------------------
# CU_DISTRICT_URLS (JSON or "co-05=https://...,or-05=https://...") sends the district selector to each district's own hostname.
DISTRICT_URLS = D.district_urls()
# Person-level routes (walk lists, door-knock results) open to field staff in hosted mode only: per-user requests per minute.
FIELD_RATE_PER_MINUTE = int(os.environ.get('CU_FIELD_RATE_PER_MINUTE', '120'))
if HOSTED:
    LOCAL_ADMIN_MIDDLEWARE = 'server.research.middleware.LocalAdministratorMiddleware'
    # The passwordless local owner must be impossible here, not merely off: ask for it and the server does not start.
    if os.environ.get('CU_LOCAL_ADMIN') == '1':
        raise RuntimeError('CU_LOCAL_ADMIN=1 is refused in a hosted deployment (it grants an owner without a password).')
    # Cloudflare Access first (server/research/access.py), then the stack without the local-owner middleware.
    MIDDLEWARE = ['server.research.access.AccessJwtMiddleware'] + [m for m in MIDDLEWARE if m != LOCAL_ADMIN_MIDDLEWARE]
    if LOCAL_ADMIN_ACCESS or LOCAL_ADMIN_MIDDLEWARE in MIDDLEWARE or MIDDLEWARE[0] != 'server.research.access.AccessJwtMiddleware':
        raise RuntimeError('A hosted deployment must have Cloudflare Access first and no local administrator.')
    if not os.environ.get('CU_ALLOWED_HOSTS') or '*' in ALLOWED_HOSTS:
        raise RuntimeError('Set CU_ALLOWED_HOSTS to the exact hostname(s) of a hosted deployment (no wildcard).')
    ALLOWED_HOSTS = [*ALLOWED_HOSTS, *(h for h in ('127.0.0.1',) if h not in ALLOWED_HOSTS)]    # the container's own health probe
    # TLS ends at Cloudflare. Its edge adds X-Forwarded-Proto to the request it sends to the origin and cloudflared passes
    # request headers on (to be confirmed with a real request at stand-up), so SECURE_PROXY_SSL_HEADER (above) is what lets Django
    # see https. The origin is reachable only through the Access-checked tunnel, so a forged header gains nothing.
    # CU_HTTPS_REDIRECT=1 additionally redirects plain http at the origin; the image sets it to 0 (Cloudflare's Always Use HTTPS
    # does that at the edge) because a redirect loop is the failure if the header is ever missing.
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_REFERRER_POLICY = 'no-referrer'
    # W021 (HSTS preload) is the domain owner's decision at Cloudflare; W008 only when the redirect is deliberately off.
    SILENCED_SYSTEM_CHECKS = ['security.W021'] + ([] if SECURE_SSL_REDIRECT else ['security.W008'])
