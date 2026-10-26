# Terrier, hosted (one district per container, behind Cloudflare Access)

Local use is unchanged: nothing here applies unless `CU_DEPLOYMENT=hosted`. The Dockerfile bakes that in.

## The front door

Every request passes `server/research/access.py` first (the `AccessJwtMiddleware`, installed first by `server/settings.py`).
It uses `server/research/access_gate.py`, a byte-identical copy of the canonical gate (never edit it here; change the canonical
file and copy it over, compare sha256). The middleware hands the gate Django's `request.headers` (real header names) and `request.get_full_path()`, never `request.META`:
the gate denies any `HTTP_*` header name. A request needs a `Cf-Access-Jwt-Assertion` header carrying an RS256 token for this
district's Access application (`ACCESS_AUD`, `ACCESS_ISSUER`, signing keys in `ACCESS_JWKS_FILE`) and an allowed `Host`. Anything
else is `403 forbidden`. The exceptions are `GET /api/health` and `GET /version` from a loopback peer with no proxy headers (the
container's own probes). After the session layer runs, a signed-in account must be the person the token names (the token's email equals the account's
email, or its username when it has none, ignoring case); otherwise the session is ended and the answer is 403. Sign-in itself is
tied to the same identity: `POST /api/auth/login` answers 403 unless the username is the token's email (ignoring case), before the
failed-attempt counters are read or written, so nobody can lock another member of staff out by guessing at their account.

One audit line per request goes to the `cu.access` logger: time, app, user, method, route without its query, status. No query
string, no client address.

Environment (read at startup; a missing value stops the server):

| Variable | Meaning |
|---|---|
| `CU_DEPLOYMENT=hosted` | baked into the image |
| `CU_SECRET_KEY` | per district, 50+ random characters |
| `CU_ALLOWED_HOSTS`, `CU_TRUSTED_ORIGINS` | exact hostname(s) and the https origin; no wildcard |
| `CU_DISTRICT` | `co-05`, `or-05` or `ct-01` |
| `ACCESS_AUD`, `ACCESS_ISSUER`, `ACCESS_JWKS_FILE` | this district's Access application; the JWKS file is refused after 24 hours or when dated in the future |
| `ACCESS_ENFORCE=1` | baked into the image: `manage.py serve` (hosted) reads the gate's settings from the plain environment and exits without them, and refuses a public `CU_BIND` when the gate is not enforced (`assert_bind_consistent`) |
| `ACCESS_SERVICE_CNS` | optional service-token common names (they may reach `/api/health` and `/version` only) |
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
they are all 403, except the ten below, in two groups. Each is open to an account that is signed in, active, in the named Django
group, the person the token names, and under the rate limit (`server/research/field_access.py`; one per-user counter covers both
groups). The groups are independent: `field-manager` does not imply `field-staff`, and nobody is in either by default. Group
`field-staff`:

| Open to field staff | Used for |
|---|---|
| `GET /api/v3/walk/lists` | the saved lists to choose from (titles and counts) |
| `GET /api/v3/walk/lists/<key>` | a saved list: its runs, doors and voters, with each voter's latest result |
| `POST /api/v3/walk/dispositions` | recording door-knock results |

Group `field-manager` (building and saving lists on the server, where the saved lists live):

| Open to field managers | Used for |
|---|---|
| `GET /api/v3/walk/{options,streets,list,overview,plan}` | picking the area and streets, and building a list or street plan to look at |
| `POST /api/v3/walk/match` | matching an uploaded street list |
| `POST /api/v3/walk/lists` | saving a list (or a re-knock list) |

Each call writes an `AuditEvent` by route (`walk.build.<name>`; a saved list adds `walk.list.create` with its key): never a
parameter, title, name, address or phone. `/walk/scores` stays closed to everyone.

The walk-list PDF is built in the browser from the saved list, so it needs nothing else. Each opened field-staff read or write writes an
`AuditEvent` (who, route, list key, and for results the voter keys: never a name, address or phone). The PDF carries the signed-in
email and the UTC time on every page. Still closed to both groups: scoring (`/api/v3/walk/scores`), contacts and their export, the global
search, the workspace, people/person/household/street, the legacy `/api/{street,household,street-preload,contacts,person}`,
credit-quality, and the summary-table CSV download. The page hides the CSV download and the scores toggle when hosted.

Staff are invited inside the running container; the account name is their Access email address:

    python manage.py invite name@example.org --field-staff --origin https://co05.example.org
    python manage.py invite name@example.org --field-manager --origin https://co05.example.org     # may build and save lists; add --field-staff too to also walk them

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

The hazard: a new salt re-keys everyone and orphans every recorded result (the test shows the re-knock list then refusing with 409).
`read_or_create_salt` makes one only for a district with no walk store or backup yet; if `.walk-salt` is missing beside `walk.sqlite3`
or `walk-backups/`, the build stops and says to restore it (`--new-salt` starts over on purpose). Keep `.walk-salt` in the laptop's backups.

## Image, compose and release

`Dockerfile`: node build of the frontend, hashed Python dependencies from `requirements.linux.lock` (CPython 3.12, linux x86_64,
wheels only; the command that made it is in its header, the inputs are in `requirements.linux.in`), then `python:3.12-slim-trixie` (Debian 13, D-181)
as uid 10001 with no login shell. Both base images are pinned by index digest (the same python and node digests as the platform
repo's `ops/images.lock`). `ARG GIT_SHA` and `ARG BUILD_TIME` (set by CI) become the image's `GIT_SHA` and `BUILD_TIME`
environment, which `access_gate.version_payload` reads. `GET /version` returns that payload (`app` `terrier-web`, `git_sha`, `build_time`,
`snapshot_id`: version information and nothing else) to the container's loopback probe, a user token or a listed service token, which
is what the platform's updater and `release status` read. No data is baked in. `compose.yaml` is an example for three districts sharing one image
(read-only root, `cap_drop: ALL`, no published ports, a separate data volume, state volume and secrets file per district); the
platform repo's `ops/compose.yaml` is the one the server runs. `.github/workflows/release.yml` runs on a `v*` tag, with every action
pinned to a commit SHA: tests, the image by digest to `ghcr.io/pjg93-222/terrier-web` (a check that the image holds no data files),
an SBOM, and `digests.json` (`{tag, commit, images: {"terrier-web": "<image>@sha256:..."}}`, the shape `ops/release.py` reads),
attached to a draft release. The same tag also builds the two build images (`COMPUTE.md`), and a `scan` job runs the platform's image
policy on all three by digest before the draft release is made (`tools/image_gate.py`, trivy pinned by sha256; D-181, D-182):
HIGH/CRITICAL vulnerabilities that have a fix, misconfigurations and secrets block; unfixed vulnerabilities and OS-package licences
(Debian GPL/LGPL) are printed as INFO lines and written to the `image-scan` artifact; pip/npm licences that are forbidden, restricted
or unknown block.

## Stand-up checklist and open rulings

* **Each district gets its own `ACCESS_AUD`.** Make a separate Cloudflare Access application per district (co-05, or-05, ct-01) and give
  each container the audience tag of its own application. A token is accepted only for the audience it was issued for
  (`test_a_token_for_another_districts_access_application_is_refused_on_its_audience`), so two districts that shared an application
  would also share their staff lists and a token from one would open the other. Never reuse one `ACCESS_AUD` across containers.
* **`AuditEvent` rows hold voter keys.** A door-knock result is audited with the voter keys it wrote (`HMAC-SHA256(.walk-salt, voter id)`,
  never a name, address or phone), and each saved list read with its list key. The keys are pseudonymous, not anonymous, so treat the
  accounts database and its backups as sensitive. Retention (ruled 2026-10-26): `AuditEvent` rows are kept for the election cycle plus 90
  days, then purged with `python manage.py purge_audit --cycle-end <YYYY-MM-DD> --yes`. It deletes the rows created on or before the
  end of the cycle-end day (UTC) and keeps later ones (the next cycle's); it refuses to run before cycle end + 90 days. Run it without
  `--yes` first for the counts. It touches no other table and records one `audit.purged` event (counts, no keys). The operator runs it;
  nothing schedules it.
* **No per-list assignment (closed).** A list is not assigned to anyone: any `field-staff` account in a district can open any saved list of that
  district and record results on it, and any `field-manager` can build and save lists. The district boundary is the only separation.
  Per-list assignment is closed: any staff account works every list of its district, per the one-application ruling.
* **Fixtures are not shipped.** `frontend/public/fixtures/v3` (made by `tools/make_v3_fixtures.py`, used only by the demo and test
  servers) holds synthetic numbers (fixed totals of 109,150 people and 45,000 households, a smooth random field) on real public
  geography: the Census block GEOIDs, polygons, roads and district outline of co-05. No person-level data is read to make it. The real
  block ids are the reason `.dockerignore` leaves the folder out of the build context, so the image carries none of it (and a
  CT or OR container would not carry Colorado's blocks).
