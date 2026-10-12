# Terrier, hosted (one district per container, behind Cloudflare Access)

Local use is unchanged: nothing here applies unless `CU_DEPLOYMENT=hosted`. The Dockerfile bakes that in.

## The front door

Every request passes `server/research/access.py` first (the `AccessJwtMiddleware`, installed first by `server/settings.py`).
It uses `server/research/access_gate.py`, a byte-identical copy of the canonical gate (never edit it here; change the canonical
file and copy it over, compare sha256). A request needs a `Cf-Access-Jwt-Assertion` header carrying an RS256 token for this
district's Access application (`ACCESS_AUD`, `ACCESS_ISSUER`, signing keys in `ACCESS_JWKS_FILE`) and an allowed `Host`. Anything
else is `403 forbidden`. The one exception is `GET /api/health` from a loopback peer with no proxy headers (the container's own
probe). After the session layer runs, a signed-in account must be the person the token names (the token's email equals the account's
email, or its username when it has none, ignoring case); otherwise the session is ended and the answer is 403.

One audit line per request goes to the `cu.access` logger: time, app, user, method, route without its query, status. No query
string, no client address.

Environment (read at startup; a missing value stops the server):

| Variable | Meaning |
|---|---|
| `CU_DEPLOYMENT=hosted` | baked into the image |
| `CU_SECRET_KEY` | per district, 50+ random characters |
| `CU_ALLOWED_HOSTS`, `CU_TRUSTED_ORIGINS` | exact hostname(s) and the https origin; no wildcard |
| `CU_DISTRICT` | `co-05`, `or-05` or `ct-01` |
| `ACCESS_AUD`, `ACCESS_ISSUER`, `ACCESS_JWKS_FILE` | this district's Access application; the JWKS file is refused after 24 hours |
| `ACCESS_SERVICE_CNS` | optional service-token common names (they may reach `/api/health` only) |
| `CU_BIND`, `PORT` | listen address (the image: `0.0.0.0:8080`; default everywhere else `127.0.0.1`). A non-loopback `CU_BIND` is refused unless hosted. |
| `CU_DATA_DIR` | the district's stores, mounted read-only |
| `CU_STATE_DIR` | the writable volume: `accounts.sqlite3` and the imported summary snapshots |
| `CU_BUNDLE` | the summaries ZIP to import at start (its sha256 in `<zip>.sha256` beside it) |
| `CU_DISTRICT_URLS` | `co-05=https://...,or-05=https://...` or a JSON object: where the header's district selector sends staff |
| `CU_FIELD_RATE_PER_MINUTE` | per-user requests a minute on the opened person-level routes (default 120) |
| `CU_HTTPS_REDIRECT` | the image sets `0`; see below |

Refused at startup when hosted: `CU_LOCAL_ADMIN=1` (the passwordless local owner), an empty or wildcard `CU_ALLOWED_HOSTS`, a missing
secret key. The local-owner middleware is not installed at all.

`python manage.py check --deploy --fail-level WARNING` is clean with those settings (HSTS one year with subdomains, no-referrer,
secure cookies). Two checks are silenced on purpose: `W021` (HSTS preload is the domain owner's decision) and `W008` (HTTPS redirect)
while `CU_HTTPS_REDIRECT=0`. TLS ends at Cloudflare, whose "Always Use HTTPS" does the redirect. Django sees https through
`X-Forwarded-Proto`; Cloudflare's edge adds it and cloudflared passes request headers on, but confirm with a real request at
stand-up before setting `CU_HTTPS_REDIRECT=1` (a missing header would make an endless redirect).

## Person-level routes

Every person-level route is `local_only`: the passwordless owner on the laptop and nobody else. Hosted there is no such owner, so
they are all 403, except the three the walk-list and door-knock flows need. Those are open to an account that is signed in, active,
in the Django group `field-staff`, the person the token names, and under the rate limit (`server/research/field_access.py`):

| Open to field staff | Used for |
|---|---|
| `GET /api/v3/walk/lists` | the saved lists to choose from (titles and counts) |
| `GET /api/v3/walk/lists/<key>` | a saved list: its runs, doors and voters, with each voter's latest result |
| `POST /api/v3/walk/dispositions` | recording door-knock results |

The walk-list PDF is built in the browser from the saved list, so it needs nothing else. Each opened read or write writes an
`AuditEvent` (who, route, list key, and for results the voter keys: never a name, address or phone). The PDF carries the signed-in
email and the UTC time on every page. Still closed: building, planning, matching, scoring and creating lists
(`/api/v3/walk/{options,streets,match,list,overview,plan,scores}` and `POST /walk/lists`), contacts and their export, the global
search, the workspace, people/person/household/street, the legacy `/api/{street,household,street-preload,contacts,person}`,
credit-quality, and the summary-table CSV download. The page hides the CSV download and the scores toggle when hosted.

Staff are invited inside the running container; the account name is their Access email address:

    python manage.py invite name@example.org --field-staff --origin https://co05.example.org

An invitation can only be accepted by the Access identity it names. District separation is by construction: each district is its own
container with its own accounts database and its own Access application, so district staff exist only in their own district.

## State, backups and the master copy of field results

The server's `accounts.sqlite3` holds users, saved walk lists and door-knock results, and is the master copy. It lives on the
`CU_STATE_DIR` volume, which snapshot pushes never touch. Back it up with `python manage.py backup <file>` (a consistent SQLite
copy); restore with `--restore` while the app is stopped. To seed field results already recorded on the laptop, move the walk lists and
results only (`dumpdata research.walklist research.disposition` there, `loaddata` here, before staff start), not the whole database: the
laptop's `accounts.sqlite3` also holds its passwordless local owner and local accounts. (This seeding has not been exercised yet.)

The stores in `CU_DATA_DIR` open read-only (`mode=ro`) and must be in `journal_mode=DELETE`; `tools/container_start.py` refuses to
start if one is in WAL mode.

## Voter keys and rebuilds (field results never orphan)

A result is stored with the voter key of the person. The key is `HMAC-SHA256(.walk-salt, voter id)` (`tools/build_walk.py`; a person
with no voter id in the file falls back to `person:<person id>`, the contact store's id, which changes only when that store is rebuilt
under a new street salt). It depends on the salt and the voter id, not on row order, so a rebuild of the stores with the same
`.walk-salt` gives the same keys. `server/research/test_hosted.py::FieldResultsSurviveRebuildTests` rebuilds a synthetic district with
its salt (same keys, every Not home voter found again) and then without it (new keys, the re-knock list refused with 409). The server
never computes a key: it reads `voter_key` from `walk.sqlite3`, so the salt does not need to be on the server.

The hazard: `read_or_create_salt` silently makes a new salt if `.walk-salt` is missing, which re-keys everyone and orphans every
recorded result (the test shows the re-knock list then refusing with 409). Keep `.walk-salt` in the laptop's backups, and never
rebuild a district's stores in a data directory that has lost it.

## Image, compose and release

`Dockerfile`: node build of the frontend, hashed Python dependencies from `requirements.linux.lock` (compile it from
`requirements.linux.in` with the command in that file), then `python:3.12-slim-bookworm` as uid 10001 with no login shell. No data is
baked in. `compose.yaml` is an example for three districts sharing one image (read-only root, `cap_drop: ALL`, no published ports, a
separate data volume, state volume and secrets file per district). `.github/workflows/release.yml` runs on a `v*` tag: tests, the image by
digest to `ghcr.io/pjg93-222/terrier-web`, an SBOM, and `digests.json`, attached to a draft release.
