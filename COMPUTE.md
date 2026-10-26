# Terrier store builds in a container (compute lane 1)

D-173 / D-177: Terrier's store builds run in a container and are proven content-identical to the Windows build before they
replace it. Design: `COMPUTE_CONTAINERS_DESIGN.md` (sections 1.1, 2.1, 4, 6). This file is the runbook. HOSTED.md is the serve side.

**Status (2026-10-13):** Docker Desktop 4.95.0 (WSL2 backend, engine 29.8.2) is installed. The full CO-05 chain `roads,street,contacts,shown,scores`
has run twice on Windows (A1, A2) and once in the containers (B), on LOCAL builds of both images from e60d68e (`terrier-build@sha256:62d17090...`,
`terrier-build-score@sha256:9f0c4fe5...`): the CI images of release v2026.10.12.8 are private in GHCR and an anonymous pull is refused
(`unauthorized`), so their digests are not yet compared with the local ones. `roads`, `street`, `contacts`, `shown` (CSV and manifest, byte for
byte, sha256 `a0e96d47...` = the live table) PASS. `scores` is exact in every table (person, evidence, household, street_household, demo_name,
metadata apart from `built_utc` and `w1.seconds`, ... : no float differs, 0 ULP) except the two FTS5 shadow tables `demo_search_data` and
`demo_search_idx`, which hold the physical layout of the demo-name search index and differ because the SQLite libraries differ. Under the D-177 rule
of 2026-10-13 (section 9: shadow tables are compared by their rebuilt form and by 400 MATCH queries) the finished run was judged again, without a
rebuild: **all six artifacts PASS, every negative control is detected** (section 9). The full chain is green, so the build is cut over (D-188):
`tools/compute_build.py` builds in the containers by default and `TERRIER_COMPUTE=windows` is the one fallback (section 8); no production build
has been run through it yet. The export goes through a named volume by default (section 5); `--export-bind` opts out.

**Status (2026-10-13, later):** `walk`, `ingest`, `extend` and `density` of CO-05 pass parity from one set of inputs and are in `tools/build_step.py`, `tools/compute_build.py` and the runners; `bundle` first failed
(the container's `geometry.json` differed from the Windows build, and D-177 keeps the bundle zip byte-identical), was held (`Step.held`), and passes now that `tools/build_bundle_v4.py` writes a canonical form (section 10): it is in the
default chain and in the runner. `import_summary` and the steps that start servers stay on the host.
`--inputs` (build) and `--inputs-from` (parity) supply the stores a run of some steps reads; the untracked runners call the build and fall back to Windows with `TERRIER_COMPUTE=windows`. Only CO-05 was run.

## 1. What is where

| File | Job |
|---|---|
| `tools/hostpaths.py` | every machine path a build tool used to hard-code, as an environment variable (section 2). Defaults are today's Windows paths, so the laptop builds as before |
| `tools/build_step.py` | `run <step> --district d` / `export <out> <files>` (a name with `*` is every file it matches) / `fingerprint`. The container entrypoint, and also what the Windows side runs, so both sides take the same code path. `STEPS` holds the ten steps of the chain (section 10) |
| `Dockerfile.build` | the toolchain, no code and no data: target `geo` (CPython 3.12.14, = `work\.venv`; `python:3.12.14-slim-trixie`) and target `score` (CPython 3.12.10, = system Python; still `python:3.12.10-slim-bookworm`, because the registry has no 3.12.10 trixie image); both apply Debian's security updates at build, hashed locks `requirements.build.lock` / `requirements.score.lock` |
| `compute.compose.yaml` | services `terrier-build` (geo image) and `terrier-score` (score image, `TERRIER_SCORE_IMAGE`; the same mounts, shared through one YAML anchor): network none, read-only root, uid 10001, inputs read-only, writes only the `stage` volume; and `terrier-export` (copies stores from the `stage` volume into a second named volume, `terrier-export-<run id>`, with an integrity check; the harness `docker cp`s them to the scratch folder and removes it) |
| `compute.export-bind.compose.yaml` | opt-out override (`--export-bind`): `terrier-export` writes `/out` into the bind-mounted scratch folder instead of the volume. Native-Linux hosts only |
| `tools/compute_parity.py` | the parity run: Windows twice (A1, A2), container once (B), compare, PASS/FAIL per artifact. Each step has a toolchain: roads, street, contacts, walk, ingest, extend, bundle, density run in the geo image and, on Windows, the work venv (`--python`); shown, scores in the score image and, on Windows, the system Python 3.12 (`--score-python`, `$TERRIER_PY312`). An image is `--image` / `--score-image`, else `$TERRIER_BUILD_IMAGE` / `$TERRIER_SCORE_IMAGE`, else the verified current release manifest (section 3, step 4). `--inputs-from <a finished run's folder>` judges steps that read earlier stores from one set of inputs (section 10) |
| `tools/compute_build.py` | the production build (section 8, D-188): the steps of one district into a new folder, in the containers by default, on Windows when `TERRIER_COMPUTE=windows`; `--inputs <data folder>` supplies the stores a run of some steps reads (section 10). Images from `$TERRIER_BUILD_IMAGE` / `$TERRIER_SCORE_IMAGE`, else the verified current release manifest |
| `.github/workflows/release.yml` job `build-image` | on a `v<YYYY>.<MM>.<DD>.<n>` tag: builds both targets, pushes `ghcr.io/pjg93-222/terrier-build` (geo) and `terrier-build-score` by digest, SBOMs, `terrier-build.digests.json` on the draft release. Job `draft-release` folds both digests into the release's `digests.json`, which the platform's `ops/release.py` reads, so the signed manifest carries them |
| `.github/workflows/release.yml` job `scan` + `tools/image_gate.py` | trivy on `terrier-web`, `terrier-build` and `terrier-build-score` by digest, the platform's image policy (D-181, D-182: fixable HIGH/CRITICAL vulnerabilities, misconfigurations, secrets and pip/npm licences that are forbidden, restricted or unknown block; unfixed vulnerabilities and OS-package licences are reported). The draft release waits for it. Tests: `tools/test_image_gate.py` |
| `tools/test_compute.py` | the tests (also in the release workflow's test job) |

Steps wired in `tools/build_step.py`, in the order of the build chain, each one row of `STEPS`: `roads`, `street`, `contacts` (toolchain `geo`), `shown` (`tools/shown_areas.py --district d`; writes `area_scores_shown.csv`
and its `.manifest.json`) and `scores` (`tools/build_scores_v3.py --district d --receipt <data dir>/scores_receipt.json`; writes
`scores.sqlite3`; the receipt would otherwise go into the repo's `_build`, which is read-only in a container) (toolchain `score`), then `walk`, `ingest`, `extend`, `bundle` and `density` (toolchain `geo`; section 10).
`shown` and `scores` read the earlier stores (street, contacts, the shown table) from the same folder, so run them after those. The untracked runners (`_build/run_remaining.ps1`, `run_fast.ps1`, `run_nowithhold.ps1`) call
`tools/compute_build.py` for these steps since 2026-10-13, except the bundle, and run the old Windows lines when `TERRIER_COMPUTE=windows` (section 10).

## 2. Path seams (`tools/hostpaths.py`)

| Variable | Is | Windows default (unchanged) | In the container |
|---|---|---|---|
| `TERRIER_SOURCES_DIR` | vendor CSVs (`statara_co_*.csv`; `terrier-sources\` for OR/CT) | `C:\Users\pgoff\Downloads` | `/sources` |
| `TERRIER_GEOGRAPHY_DIR` | Census geography (`roads.zip`, `blocks.gpkg`, `or-05\`, `ct-01\`) | `<work>\geography` | `/geography` |
| `TERRIER_SIBLING_DIR` | the `cu-geography` repo: `.local\.street-salt`, `.local\row-framework-20260930`, Luna | `<codex>\outputs\cu-geography` | `/sibling` |
| `TERRIER_LOCAL_DIR` | where a build writes (co-05: the folder itself; other districts: their folder below it) | `<this repo>\.local` | `/stage/<run id>` |
| `TERRIER_WORK_DIR` | the Codex work folder | `<codex>\work` | unused |
| `TERRIER_WORK_PY`, `TERRIER_PY312` | interpreters Luna phase 3 launches | work venv, `Python312` | unused (Luna is not containerised yet) |
| `TERRIER_LUNA_SCRATCH` | text Luna's original scripts carry, which `luna_district/rowfw.py` patches | the Colorado scratch folder | unused |

`<codex>` is `C:\Users\pgoff\Documents\Codex\2026-09-27\we-are-going-to-have-an`, a junction to `C:\Users\pgoff\Documents\Terrier`.
Docker cannot follow it, so `compute.compose.yaml` mounts the real folders. `build_step.py` refuses to run without
`TERRIER_LOCAL_DIR`: a build never falls back to the repo's live `.local`. `tools/test_compute.py` proves the defaults are the old
literals (byte for byte on Windows) and that each variable moves what it names.

## 3. One-time setup (Paul; admin)

1. Install Docker Desktop (WSL2 backend): `wsl --install`, reboot, install Docker Desktop. `%UserProfile%\.wslconfig`:
   `[wsl2]` `memory=16GB` `processors=8` `swap=8GB`. Put the Docker disk image on C: (the only drive) and cap it (64 GB proposed).
2. Tag the Geo repo `v2026.10.12.1` (or the next number) so the `release` workflow builds the images. **Outward step: Paul pushes the tag.**
   Both images appear in GHCR by digest; `terrier-build.digests.json` and the SBOMs are attached to the draft release.
3. First CI run: the build is untested (no Docker was available when this was written). If `build-image` fails, the log names the
   step: a lock that will not install, a base digest, or the import check. Fix, tag again.
4. Get the digest (a tag is refused by the harness, by design). Once a signed release carries the build images, you do not: the
   harness asks the Melange platform's `ops\release_lib\current.py image terrier-build` (the laptop's verified current manifest;
   `$env:MELANGE_RELEASE_CURRENT` moves the script; anything but a verified digest stops the run, with no fallback to a hand-edited
   value). `terrier-build-score` resolves the same way for the score steps. The one override is an
   explicit `--image` / `$TERRIER_BUILD_IMAGE` (and `--score-image` / `$TERRIER_SCORE_IMAGE`), for a build no signed release names yet (the first parity runs):

```powershell
python 'C:\Users\pgoff\OneDrive\Desktop\Melange Systems\ops\release_lib\current.py' image terrier-build   # the digest the harness uses by default
gh release download v2026.10.12.1 -R pjg93-222/Geo -p terrier-build.digests.json -D $env:TEMP
$env:TERRIER_BUILD_IMAGE = (Get-Content $env:TEMP\terrier-build.digests.json | ConvertFrom-Json).images.'terrier-build'
docker pull $env:TERRIER_BUILD_IMAGE
docker compose -f C:\Users\pgoff\worktrees\dep-terrier-build\compute.compose.yaml config -q    # the YAML was only parsed, never run through compose
```

## 4. Run the smoke (roads.sqlite3, CO-05)

Only when the machine is idle: no Mentat rebuild or other heavy job, and about 14 GB of RAM free
(`(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB`). One heavy job at a time. Use a NEW scratch folder under
`C:\Users\pgoff\terrier-parity` (not OneDrive, not a `.local`).

```powershell
Set-Location C:\Users\pgoff\worktrees\dep-terrier-build     # until this branch is merged, run it from here; the inputs are absolute paths
$py = 'C:\Users\pgoff\Documents\Terrier\work\.venv\Scripts\python.exe'

# 4a. no Docker needed: the Windows build twice, to see the legitimate variance (A1 vs A2)
& $py tools\compute_parity.py --scratch C:\Users\pgoff\terrier-parity\smoke-win --steps roads --skip-container

# 4b. the real thing: A1, A2, then the container (B), plus a negative control
& $py tools\compute_parity.py --scratch C:\Users\pgoff\terrier-parity\smoke-1 --steps roads --negative-control
```

Expected (4b): `A1: Windows build of roads`, `A2: ...`, `B: container build of roads (stage volume B-<utc>)`, then

```
PASS  roads.sqlite3   5 tables, 77,930 rows; run-to-run variance (left out): none
                      equals the recorded co_baseline fixture: yes
PASS  negative control roads.sqlite3   one row removed from roads: detected
PASS  negative control roads.sqlite3 (value)   one roads.name value changed (TEXT in its last bit): detected
PARITY PASS: 3 artifact check(s), 0 failed; report ...\smoke-1\parity_report.json
```

The negative controls work on copies of B in the scratch folder, never on a live store: per SQLite store one row removed and, separately,
one stored value changed by its smallest step (a REAL cell by one ULP, else an INTEGER by 1, a TEXT or BLOB in its last bit); per other
file one bit flipped in the middle; for a store with an FTS5 table that keeps its text (`demo_search`), a third: one name gets a word added with the
index kept in line, and the FTS5 comparison itself (section 9) must fail. Each must make the comparison FAIL (the line says "detected").
`--reuse-windows --reuse-container` judges a finished run's A1, A2 and B again, building nothing (for a changed comparison rule). A store is compared exactly (the
digest hashes `repr` of every cell, which round-trips a float), so a one-ULP change is seen: the harness has no ULP tolerance, and a
float difference of D-177's "a few ULP" would show as a FAIL naming the table, to be judged by hand.

`parity_report.json` also holds the interpreter and library versions of both sides (`build_step.py fingerprint`): record them with
the result. A FAIL line names the table (never a row value). If A1 and A2 differ in a table, that table is the variance and is left
out of the A-vs-B comparison; the line says so, so a human can judge whether the table deserves a column-level mask.

**Cost (smoke):** images about 0.5 GB each [inferred], pulled once; roads store 13 MB per copy (live: 13.3 MB), so scratch is
under 100 MB; time: seconds to a few minutes (the pull dominates) [inferred; the Windows status logs have no roads timing].

## 5. The full CO-05 set (next step, not wired yet)

The pass bar for the lane: the five SQLite stores logically equal under `co_baseline.store_digest`, the bundle zip byte-identical,
the golden endpoint hashes matching (`tests/fixtures/co_baseline.json` holds 49 endpoint entries, all status 200; the brief says 48,
so the harness reports the check's own verdict, not a count).

- `street` and `contacts` are wired: `--steps roads,street,contacts`. Live sizes: street 713 MB, contacts 207 MB. Windows times
  (from `_build/run_remaining.status.jsonl`, CT; CO runs about 1.1x): street 7 min, contacts 3 min; the design estimates 7-10 and 5-7.
  Three runs (A1, A2, B) plus the export is about 45-60 minutes and about 4 GB of disk (3 x 0.94 GB scratch + the stage volume).
  **The export goes through a named volume, by default.** `terrier-export` writes `/out` into a volume (`terrier-export-<run id>`, ext4
  inside the Docker VM); the harness creates it, gives it to uid 10001, runs the export, `docker cp`s the files into the scratch folder and
  removes the holder container (`rm -v`) and the volume, also when the export fails. A bind-mounted Windows scratch folder is the slow way:
  the `PRAGMA integrity_check` that `tools/copy_db.py` runs on the copy reads the 731 MB street store page by page over Docker Desktop's
  9p file share (4 KB reads at 0.34-0.78 ms; over 40 minutes with no progress output, the process sits in `p9_client_rpc`) against 31
  seconds on a volume. `--export-bind` (adds `compute.export-bind.compose.yaml`) restores the bind export; use it only where the folder is
  on a native file system (a Linux host). Measured 2026-10-12 (machine loaded by other jobs): street + contacts, A1 ~11 min,
  A2 ~9 min, B build ~21 min, export 31 s, comparison with both negative controls ~17 min (street alone: 6 min to digest three copies).
  Measured 2026-10-13 for the full chain (Glass Box `build step finished`, seconds; A1 / A2 / B): roads 10 / 9 / 11, street 223 / 328 / 708,
  contacts 96 / 138 / 1250 (B ran while other jobs loaded the machine), shown 31 / 22 / 28, scores 261 / 128 / 170. A1 + A2 took 39 min with
  the comparison of the two; B 36 min to the export. The comparison of B (integrity check and digest of three copies per store) took about
  90 min on a loaded machine: scores alone one hour.
- `shown` and `scores` are wired (`--steps roads,street,contacts,shown,scores`). Windows A runs use the system Python 3.12 (`--score-python`,
  `$TERRIER_PY312`; the same one `_build/run_nowithhold.ps1` calls `$PY312`), isolated as the tools' own docstrings say
  (`PYTHONPATH` unset, `PYTHONNOUSERSITE=1`, `PYTHONDONTWRITEBYTECODE=1`). Compose parses every service, so both image variables are always
  set; a run that needs one toolchain gives the other variable the same image (that service never starts), and the export uses the geo image
  when the run has one. Three seams make the two sides comparable, none of them changes a value: `shown_areas.py` reads the district's own cache
  (`D.rowfw_cache`, hostpaths) instead of the Windows path the frozen config names, and writes its manifest with LF (`scores` records that
  file's sha256 in `metadata.area_table`); `build_step.py` seeds `.demo-key` from `$TERRIER_DEMO_KEY` when the scores step starts (the tool
  otherwise makes a random key per folder, and the demo names, their FTS index and `metadata.demo_key` then differ in every build, A1 vs A2
  as much as A vs B); the harness makes one throwaway key per scratch folder (`demo_key.hex`, reused by `--reuse-windows`). A `metadata`
  table that differs between A1 and A2 is no longer left out whole: per key, and per field of a JSON value (`w1.seconds`), only what
  varies is; every other key must match B, and the line names what was left out. `walk`, `ingest`, `extend`, `bundle` and `density` are wired as well (section 10: the bundle's `--out-dir` and `--receipt` go into the stage, its Django validator gets a scratch `CU_DATA_DIR` and a throwaway
  `CU_SECRET_KEY`, the walk store's `.walk-salt` comes from `$TERRIER_WALK_SALT`). Live sizes: scores 551 MB, walk 311 MB.
- `--api` runs `co_baseline.py check --parts api --data-dir <folder>` on A1 and B (a spare server per folder, minutes each). It needs a
  complete data folder (snapshots, accounts), so it only makes sense after the full set exists in every folder.
- Salts and keys (`.street-salt`, `.walk-salt`, `.demo-key`, `.secret`) are copied, never regenerated: for CO the street salt is the
  read-only mount; for OR/CT `build_step.py` stops when it is missing, and `build_street.py` run by hand refuses to make a new one beside an existing street store, backup or contacts store (restore the salt, or pass `--new-salt`; co-05's is refused either way). The Census downloads (T1, T9) stay on Windows.
- Windows reference: the live `.local` CO stores are not touched. The A runs rebuild from the same inputs into scratch. If the roads
  line says `equals the recorded co_baseline fixture: no`, the inputs or tools moved since 2026-10-09; the A1/A2/B comparison still stands.

## 6. Cleanup and rules

- `docker compose -f compute.compose.yaml -p terrier-compute down -v` removes the stage volume (the stores inside are voter-derived).
  Scratch folders are removed only on Paul's word. A killed parity run can leave its export volume and holder behind:
  `docker rm -v terrier-export-cp-B-<utc>` and `docker volume rm terrier-export-B-<utc>` (`docker volume ls` shows them).
- Never mount or edit the live `C:\Users\pgoff\Documents\Terrier\outputs\cu-geography-v3\.local`. The compose file has no mount for it,
  and a test fails if `cu-geography-v3` appears in it.
- No network in a build container (`network_mode: none`). Images run by digest only; the harness refuses a tag.
- Glass Box: `build_step.py run` emits start and finish events; the events folder is the one writable host mount of a build.

## 7. Known gaps

- FTS5 shadow tables: settled by the D-177 rule of 2026-10-13, section 9. A contentless FTS5 table (`contacts.sqlite3`'s `search`) cannot be rebuilt, so its shadow
  tables must still be exactly equal; they were in the finished run.
- Run under Docker so far: both images built locally from e60d68e and `roads`, `street`, `contacts`, `shown`, `scores` of CO-05 (parity
  above), then `walk`, `ingest`, `extend`, `bundle` and `density` of CO-05 (section 10; the bundle fails). Not yet run: `--api`, and any step for OR-05 or CT-01 (their row framework and street salt
  would be taken from `.local` by `--inputs`, which only CO-05 has been used with). The CI job itself has run
  (v2026.10.12.8) but its images were not pulled; compare their digests with a local build only after the registry login that is Paul's.
- `tools/luna_district` (OR/CT phase 3, T6) still calls `ctypes.windll` without a POSIX branch (`common.available_ram_gb`,
  `peak_working_set_gb`); the seams are in, the container does not run it. `build_walk.py` and `build_scores_v3.py` already degrade.
- The images' Debian SQLite (the `geo` image is Debian 13, the `score` image Debian 12 with 3.40.1) differs from the host's (3.53.1 in
  `work\.venv`, 3.49.1 system): store bytes can never be identical, which is why the gate is logical. Any other table that differs between A1 and A2 is still left out whole (the A1/A2 pair of the full chain showed
  only `street.metadata[created_at_utc]` and `scores.metadata[built_utc, w1.seconds]`, which the key-level mask now names).
- The score image's package set is the design's list (numpy, pandas, pyarrow, scipy, joblib); if `shown_areas` or the rowfw children
  import anything else, add it to `requirements.score.in` and regenerate the lock.
- `current.py` (the manifest reader) is Melange's `ops/release_lib/current.py`. Until a signed release names `terrier-build` (the Geo workflow
  must run on a `v*` tag with the folded `digests.json`) and that script is in the Melange tree at the default path, the harness stops unless
  `--image` or `$TERRIER_BUILD_IMAGE` names a digest.

## 8. The production build (cut-over, D-188)

D-188: switch when the parity run is green, keep a one-flag Windows fallback. The full CO-05 chain is green under the FTS5 rule of D-177 (section 9),
so `tools/compute_build.py` is the build of the five stores of a district, and it runs in the containers unless told otherwise.

**What it is.** One command builds the steps of a district (default: all of section 10, and a step marked `held` would be left out; the first five are `roads`, `street`, `contacts`, `shown` and `scores`), in that order, into a NEW folder (`--out`; never a
`.local` folder, and it must be empty). In the container mode each step runs in the compose service of its toolchain (`terrier-build`, the geo image:
roads, street, contacts; `terrier-score`, the score image: shown, scores), all in one stage volume, and the stores leave the volume through the named export
volume and `docker cp` (section 5), the harness's own `container_build`. The files are the same six that the parity run compares: `roads.sqlite3`,
`street.sqlite3`, `contacts.sqlite3`, `area_scores_shown.csv`, `area_scores_shown.manifest.json`, `scores.sqlite3` (co-05: in `--out` itself; another
district: in its folder below it). `compute_build_receipt.json` beside them names the mode, the images and where each came from, the run id and the
interpreter and library versions of the side that built. The tool does not put anything live: Paul's swap (`_build\swap_co_live.ps1` for Colorado) does.

**The switch.** `TERRIER_COMPUTE` unset, empty or `container`: the containers. `TERRIER_COMPUTE=windows`: the fallback, the same steps on the laptop with the
work venv (geo steps) and the system Python 3.12 (score steps), exactly the way the A runs of the parity run build them (`build_step.py run`, into the same
`--out`). Any other value stops before anything runs. There is no other switch: no flag, no per-step choice. The images are not read in `windows` mode.

**Images.** `$TERRIER_BUILD_IMAGE` and `$TERRIER_SCORE_IMAGE`, each pinned by digest (a tag is refused), while no signed release names the build images; when a variable
is empty, the digest the laptop's verified current release manifest names (Melange `ops\release_lib\current.py image terrier-build` and
`terrier-build-score`; no verified answer stops the run, there is no guess). Until the manifest names them, the values are the local builds of e60d68e that the
parity run used. The environment variable is the only place they are set; nothing here sets one for the machine or the user.

**The demo key.** `scores` derives every demo name, their search index and `metadata.demo_key` from the district's `.demo-key`, and makes a random one when there
is none, which would change every name. So `compute_build.py` starts the build from `$TERRIER_DEMO_KEY` (64 hex digits) or else the district's own `.demo-key` in
its data folder (`<repo>\.local\.demo-key` for Colorado; read, never printed or written to a log or the receipt; it reaches the build container as an environment
variable, as the throwaway key of the parity run did), and stops when it has neither. In `windows` mode
the key is also written to `--out\.demo-key` (build_step.seed_demo_key); treat that folder like `.local`.

**Run it** (PowerShell, from the checkout that holds the district's `.local`; Docker Desktop running; the machine idle, as for the smoke in section 4):

```powershell
Set-Location C:\Users\pgoff\Documents\Terrier\outputs\cu-geography-v3
$py = 'C:\Users\pgoff\Documents\Terrier\work\.venv\Scripts\python.exe'
$env:TERRIER_BUILD_IMAGE = 'ghcr.io/pjg93-222/terrier-build@sha256:62d170907fadbd1cb6535795dc333ffe4f98cbdbb6398d76f7602f89666e40c1'
$env:TERRIER_SCORE_IMAGE = 'ghcr.io/pjg93-222/terrier-build-score@sha256:9f0c4fe5cde759ac474c80dcd5bbdb84b17731052fb7b4ed3eda779684195b55'
& $py tools\compute_build.py --district co-05 --out C:\Users\pgoff\terrier-builds\co-05-<date> --plan     # prints the mode, the service and image of each step; starts nothing
& $py tools\compute_build.py --district co-05 --out C:\Users\pgoff\terrier-builds\co-05-<date>           # roads to scores: 36 minutes to the export when measured, on a loaded machine
```

**Fall back to Windows** (one variable, the same command line):

```powershell
$env:TERRIER_COMPUTE = 'windows'
& $py tools\compute_build.py --district co-05 --out C:\Users\pgoff\terrier-builds\co-05-<date>-win
Remove-Item Env:TERRIER_COMPUTE        # back to the containers
```

The stage volume keeps the run's stores (voter-derived) until `docker compose -f compute.compose.yaml -p terrier-compute down -v` (section 6); the export volume is removed by the tool.
Run the chain from `roads`: a later step reads the stores of the earlier ones from the same stage volume, so `--steps shown,scores` alone finds no `street.sqlite3`
in a container (it is not seeded from `.local`, which is never mounted). A one-step Windows build is still `build_step.py run <step>` with `TERRIER_LOCAL_DIR` set.

**The runners** are in the ignored `_build\` folder (`.gitignore`: `_build/`): none is tracked in git. Since 2026-10-13 `run_remaining.ps1`, `run_fast.ps1` and `run_nowithhold.ps1` call `tools/compute_build.py` for the
steps of section 10 (the bundle too, since its parity passed) and run their old Windows lines unchanged when `TERRIER_COMPUTE=windows`; the copies from before the change are `run_remaining.ps1.bak-20261013`, `run_fast.ps1.bak-20261013` and
`run_nowithhold.ps1.bak-20261013` beside them (`run_nowithhold.ps1.bak-20261013b` is the copy from before the bundle line was added). A run of one of them without the variable is a container build.

## 9. The FTS5 rule and the full-chain verdict (D-177, 2026-10-13)

**The rule** (Claude, 2026-10-13, under D-177's content-level equivalence; Paul may overrule). The shadow tables of an FTS5 table (`<fts>_data`, `_idx`, `_docsize`,
`_config`, `_content`) are physical index structures, not data: the SQLite library that writes them decides their layout. `compute_parity.compare_store` still compares every
other table exactly. When the shadow tables of an FTS5 table are not exactly equal in A1 and B (and everything else already matches), `fts_parity` compares them two ways, and
both must hold: (i) rebuild-canonical form: on scratch copies of both sides (system temp, removed again), `INSERT INTO <fts>(<fts>) VALUES('rebuild')` with ONE SQLite
library (the harness interpreter's), then the shadow tables must be exactly equal; (ii) identical MATCH results on the stores as built: 400 queries, the same for every side
(the words of more than two letters in A1's indexed text, sorted, shuffled with seed 7, the first 400, each asked as a quoted phrase), the same rowids. If A2's shadow tables
differ from A1's, A2 is rebuilt and compared too. A contentless table (`content=''`, `contacts.sqlite3`'s `search`) cannot be rebuilt: its shadow tables must be exactly equal or
the store fails. Nothing else is relaxed: a table that only looks like a shadow (`thing_data`) stays exact, and so does the virtual table's own rows.

**Negative control.** `change_one_indexed_name` adds a word to one indexed name in a copy of B and brings the index in line with it (external content: `rebuild`; own content:
the UPDATE does it). The FTS5 comparison itself must fail, not only the exact comparison of `demo_name`. On the real stores it does, in (i): `demo_search_data` and
`demo_search_docsize` differ after the rebuild. (ii) alone would not catch it (the 400 sampled words do not include the new one: 400 of 400 identical), which is why (i) is the rule's
main test and (ii) the guard against an index that disagrees with its text. The unit tests also build an index that is stale against its text (equal after a rebuild, caught by
(ii)).

**Evidence** (the finished full-chain run of 2026-10-13, judged again without rebuilding anything: `compute_parity.py --reuse-windows --reuse-container --negative-control`
at cba5e34, report `parity_report.json` in the run's scratch folder; the images are the local builds above, `terrier-build` fingerprint python 3.12.14 / SQLite 3.46.1,
`terrier-build-score` python 3.12.10 / SQLite 3.40.1, Windows 3.53.1 / 3.49.1):

| Shadow table of `demo_search` | rows | A1 = A2 | A1 = B |
|---|---|---|---|
| `demo_search_data` | 901 | yes (sha256 801885c7...) | no (B: 96028988...) |
| `demo_search_idx` | 1,026 | yes (74cfcf55...) | no (B: 44fd9f4d...) |
| `demo_search_docsize` | 491,308 | yes | yes (4ec835e6...) |
| `demo_search_config` | 1 | yes | yes |
| `demo_search` (the table's own rows, read through `demo_name`) | 491,308 | yes | yes (b457ea94...) |

After `rebuild` by SQLite 3.53.1 on copies of A1 and B the shadow tables are equal (9 s for the whole comparison), and 400 of 400 MATCH queries return the same rowids. `contacts.sqlite3`
has a contentless FTS5 table, `search`; its shadow tables are exactly equal in A1 and B, so no rebuild is needed.

**Verdict: PARITY PASS, 17 of 17.** `roads`, `street`, `contacts`, `area_scores_shown.csv`, `area_scores_shown.manifest.json`, `scores.sqlite3` pass; the negative controls are all
detected: row removed and one value changed by its smallest step in roads, street, contacts and scores (a REAL cell by one ULP in street and scores), one bit flipped in the shown
table and its manifest, and one changed indexed name in scores. `street.sqlite3` and `scores.sqlite3` do not equal the recorded `co_baseline` fixture (inputs or tools moved since
2026-10-09; non-gating, as before); the A1/A2/B comparison stands. Left out as run-to-run variance, named: `street.metadata[created_at_utc]`, `scores.metadata[built_utc, w1.seconds]`.

## 10. Walk, ingest, extend, bundle, density, and the runners (D-188, 2026-10-13)

The five steps of section 9 were joined by five more: `walk` (`tools/build_walk.py`), `ingest` (`tools/ingest.py`), `extend` (`tools/extend_geography.py`), `bundle` (`tools/build_bundle_v4.py`) and `density`
(`tools/build_density.py`). They run in the geo image and, on Windows, in the work venv (`$PY`), the interpreter the runners give them.

**Which steps run in a container.** Runner lines are those of the `.bak-20261013` copies of the runners, which are the files before this change.

| Step | Runner line | Writes | Reads | Class |
|---|---|---|---|---|
| roads, street, contacts | `run_remaining.ps1` 160-162, `run_fast.ps1` 207-209 (R2-R4) | `roads.sqlite3`, `street.sqlite3`, `contacts.sqlite3` (contacts also adds an index to street) in `.local\<district>` | geography, source CSV, street salt | (a), section 9 |
| walk | `run_remaining.ps1` 163, `run_fast.ps1` 210 (R5); `tools/build_walk.py` 729, 754, 815 | `walk.sqlite3`; makes `.walk-salt` only for a district with no walk store or backup yet (a missing salt beside `walk.sqlite3` or `walk-backups/` stops the build; `--new-salt` starts over on purpose) | roads, street, contacts (and the score store for co-05), CSV, `.walk-salt` | (a) |
| shown, scores | `run_nowithhold.ps1` 181-182, 209-214 | `area_scores_shown.csv`, `scores.sqlite3` (co-05: `scores.unmask.sqlite3`, a side file) | row framework, street, contacts | (a), section 9 |
| ingest | `run_nowithhold.ps1` 183, 215; `tools/ingest.py` | the v2 folder and its zip; used to write `ingest_receipt.json` into the (read-only) geography folder | CSV, geography | (a), after a `--receipt` seam |
| extend | `run_nowithhold.ps1` 183, 216; `tools/extend_geography.py` | the v3 folder and its zip | CSV, the v2 folder, geography, `hierarchy\*.zip` | (a): it downloads only a `hierarchy` zip that is missing (co-05; `tools/prepare_geography.py` 20-22 returns at once for a file that exists) |
| bundle | `run_nowithhold.ps1` 185, 217-224; `tools/build_bundle_v4.py` 597-610, 673 | `data\<key>.zip` and `.sha256` in the checkout's `data\` (inert until imported), and a receipt | the v3 zip, the shown table, the score store, the row framework, blocks; validates with Django settings and touches no database | (a), after a canonical form in the builder (below): parity PASS, no longer held |
| density | `run_nowithhold.ps1` 187, 232-236; `tools/build_density.py` 80, 104-106 | `density.json` (co-05: `density.unmask.json`) | `blocks.zip`, the score store | (a) |
| import | `run_nowithhold.ps1` 186, 226-231, 239-244; `server/research/management/commands/import_summary.py`, `server/research/bundles.py` 225-254 | the snapshot folder and the district's `accounts.sqlite3` rows, in the server's data dir | the bundle zip | (b): stays on the host |
| prepare_geography (R1) | `run_remaining.ps1` 159, `run_fast.ps1` 206; `tools/prepare_geography.py` 20-31 | Census downloads | the network | (c): stays on the host |
| R6, R7, pincheck, smoke, gate, capture-pre, stage, stage-import, Luna phase 3 | | start servers on spare ports, run the golden endpoint gate, write the server's data | | (b)/(c)/not containerised: unchanged |

**The parity run** (CO-05, 2026-10-13; images the local builds of e60d68e of section 8; scratch `terrier_parity3\full`; HEAD of this change). The five steps were judged from one set of inputs (below), A1 and A2 on Windows with the work venv,
B in the geo container with the volume export. Verdicts (`compare_store`/`compare_zip`/`compare_file`; no FTS5 table is among these artifacts, so the FTS5 rule of section 9 did not come into play):

| Artifact | Verdict |
|---|---|
| `walk.sqlite3` | PASS: 12 tables, 1,051,323 rows equal in A1, A2 and B; left out as run-to-run variance, named: `meta[built_utc, qa.peak_memory_mb, qa.timing]` |
| `ingest/<key>.zip` | PASS: 4 of its 5 members byte-identical, `manifest.json` equal except `created_at` (A1 and A2 differ in it too) |
| `ingest/ingest_receipt.json` | PASS: byte-identical once the receipt is written with LF on Windows too (it was CRLF there: every value was equal, 35 of 35) |
| `extend/<key>.zip` | PASS: same as ingest |
| `density.json` | PASS: byte-identical, sha256 `20147ce2...` |
| `bundle-out/<key>.zip` and its `.sha256` | **FAIL** in this run: B differs from A1 byte for byte; A1 and A2 are byte-identical. **PASS** after the canonical form below (second run, further down) |

Negative controls (one per step, on a copy of B): a row removed and a REAL cell moved one ULP in the walk store; one bit flipped in the largest member of the ingest and extend zips; one byte in `density.json` and the receipt: all detected. The bundle's control was not
run in this run once its comparison failed (it would look caught anyway); it ran in the second run.

**Why the bundle was held, and the canonical form that released it (Paul 2026-10-13: the map bundle must be byte-identical on Windows and in the container, by writing every ring from the same start, "Always start at the same point").** In the
container's bundle only `geometry.json` differed, and in two features of it: one of the 179 tracts and one of the 326 precincts, polygons that `dissolve` (`unary_union`) makes from their blocks. Both are the same polygon (`equals` is true, the
symmetric difference has area 0). GEOS is 3.13.1 on both sides; the likeliest cause is the order a Windows and a Linux build of GEOS take inside the cascaded union (not verified). D-177 keeps the bundle zip byte-identical, and a float tolerance would
not cover a different ring order, so `tools/build_bundle_v4.py` now writes a canonical form (`canonical_geometry`, applied to every ring of `geometry.json` just before it is serialized: the base bundle's blocks and ZCTAs too, so the file is canonical
whole): **canonical ring start** (each ring, exterior and holes, each polygon of a multipolygon, from its smallest (x, y) after the 6-decimal rounding; orientation, the closing point, precision and key order unchanged; a vertex the ring visits twice at
that point takes the start whose rotated ring sorts first); **holes in the order of their start vertex** (the exterior stays first; the order of the polygons of a multipolygon is not touched); `write_zip` (the zip with `create_system` 3 on every platform).
Two things beside the ring start were found by the re-run and are needed for the same byte-identity: the first fix still failed because the same tract and precinct came with the same holes in another order (the first diagnosis, a different start
vertex, was incomplete), and the zip's own headers differed (`ZipInfo.create_system` is 0 on Windows and 3 elsewhere and is written into the archive, so the zip would differ with every member equal); and the `.sha256` file is written with LF on
Windows too (it was CRLF there, as the ingest receipt was). Tests: `tools/test_bundle_geometry.py`.

**The second parity run (bundle only, 2026-10-13).** The proof standard of that run was the lighter one (Paul): ONE Windows reference build (A1) and the container build (B), compared exactly, no mask (`compute_parity.py --single-reference`; the bundle has
no stamp: A1 and A2 of the first run were byte-identical). `compute_parity.py --steps bundle --inputs-from <folder> --link-inputs --single-reference --negative-control`, the geo image of section 8 (`terrier-build@sha256:62d17090...`), inputs: the shown table and
score store of the first run's A1 and its `extend` zip (so both builds start from one base), scratch `terrier_parity4\full2`. Verdict: `bundle-out/<key>.zip` PASS, byte-identical in A1 and B (24,891,270 bytes, sha256 `7c478a71d1de...`);
its `.sha256` PASS, byte-identical. Negative controls: one bit of `rows.json` changed in a copy of B, one byte of the `.sha256` changed, and one tract coordinate nudged by 1e-6 in `geometry.json` (rebuilt zip): all three FAIL the comparison, as they must.
Against the bundle the first run's Windows build made (before the change): 9,668 of 9,668 features are equal as shapes (`equals`), all 18,632 rings are closed and start at their smallest vertex, the holes of all 10,008 polygons are in start order, and
`manifest.json`, `rows.json`, `adjacency.json`, `area_indices.json`, `streets.geojson` and `district.geojson` are byte-identical to the old bundle's. The A1 and B copies were deleted after the comparison (voter-derived stores); only `parity_report.json` is kept.
The new bytes are a one-time change of the served file's bytes (`geometry.json`, the zip, its `.sha256`), not of its shapes; nothing was rebuilt for production and no snapshot was imported. Only CO-05 was run: another district's bundle uses the same code.

**The volatile list (D-242, Paul 2026-10-18).** `--single-reference` is also the standard for stores: ONE Windows run (A1; A2 is A1) and the container run (B), compared exactly, except that what a store records
about the build itself is left out from a fixed, reviewed list, `tools/compute_volatile.json`, instead of a measured A1/A2 variance. Each entry names a store, its key/value table, the key, a JSON pointer into the value (an empty pointer is the whole value; `*` stands for exactly one segment),
the `path:line` of the statement that writes it and a `needle` that line contains, a `kind` and, for a pointer, `dumps` (how the writer dumps the stored value). The six entries are the names that sections 9 and 10 record as left out in the full-chain
and walk runs of 2026-10-13: `street.metadata[created_at_utc]` (`build_street.py:250`); `scores.metadata[built_utc]` (`scores_writer.py:325`); `scores.metadata[w1]` field `/seconds` (`build_scores_v3.py:330`); `walk.meta[built_utc]` (`build_walk.py:797`);
`walk.meta[qa]` fields `/timing/*` (`build_walk.py:794`: the phase names must match, the seconds are left out) and `/peak_memory_mb` (`:795`; a number on Windows, null in the container, so this one entry has `or_null`). The rules: only a key/value table of a SQLite store a build step writes
can be listed (the loader refuses a zip, the shown table, `density.json`, a receipt, a wildcard key or a pointer of nothing but wildcards); a value is left out only when both sides have the same JSON type (a bare ISO text is `text`, a JSON string is `string`) and look like their
kind (`clock`: an ISO-8601 time; `duration` and `measured`: a number of at least 0); a key missing on one side, a key or field the list does not name, a shape change (another key set, list length or container against leaf) and any difference in the rest of a JSON value still fail. The rest of a value is
compared as the writer's own text: both raw cells must equal their own re-dump in the entry's `dumps` format, and a cell with a duplicate key or NaN/Infinity is plain text that no pointer names, so number spelling, spacing, escapes and key order beside a left-out field cannot hide a difference.
A store without an entry (roads, contacts) is exact; `compare_zip` and `compare_file` never read the list, so the bundle zip and its `.sha256` stay byte-identical. The run stops before anything builds unless the list is committed and clean (`git status --porcelain` empty for the file, and its raw bytes
hashing to HEAD's blob), every entry's `needle` is still on its cited line in the tree being built (an edited writer sends its entry back to review), and that tree is clean; `--skip-container` is refused with it. The report (`parity_report.json`, key `volatile_list`, and `repo_head`, the HEAD of the tree built) and the console record the
list's sha256, its HEAD blob and that commit. The negative controls skip the listed key/value tables, as the two-run mode skips its variance tables, and each store with entries gets one more: in a copy of B a field beside a listed one is changed (`w1.matches`, `qa.rows`) or, for a store whose entries
are whole values (street), an unlisted key is added, and the comparison must fail naming that table. Not covered: the `created_at` stamps in the `manifest.json` of the ingest and extend zips
(`ingest.py:150`, `extend_geography.py:97`), which a single-reference run would still call a byte difference. Two-run mode is unchanged. Tests: `tools/test_compute.py` (`VolatileListTests`, `ReviewedListTests`, `RunTests`).

**How a step that reads earlier stores gets them.** A container sees no `.local` (section 6). A run of some steps reads what the steps before them left; `tools/build_step.py` names that per step (`needs`: `walk` needs roads, street,
contacts and, for co-05, the score store; `extend` needs the `ingest` folder; `bundle` needs the shown table, the score store and the `extend` folder; for another district also its street salt and its `rowfw` folder). The steps of
one run find them in the stage. For the rest, `tools/compute_build.py --inputs <data folder>` copies exactly the names the chosen steps need and no step of the run makes: they cross as a tar stream on the stdin of one container
(`tools/compute_parity.py seed_stage`; `docker cp` into a volume ran at 1 MB/s here, the stream at 5), the folder is only read, and the copies are given to the build user. The Windows fallback copies the same names into `--out`.
`compute_parity.py --inputs-from <a finished run's folder>` does it for A1, A2 and B, so the later steps are judged from one set of inputs (here the stores of the full-chain run of section 9); `--link-inputs` hard-links them into A1 and A2 (disk) and is
refused for `contacts`, which changes street.sqlite3 in place. Judging from one set of inputs also keeps two file-level facts out of the comparison: the walk store records the sha256 of `roads.sqlite3` (a SQLite file differs between the Windows and the
container library) and the bundle records the score store's `built_utc`. `--drop-stage` removes the run's folder from the stage volume once its files are in `--out` (the runners use it; the stores in the stage are voter-derived).

**The salt.** `walk` keys every voter with `.walk-salt`: `compute_build.py` takes `$TERRIER_WALK_SALT` (64 hex digits) or else the district's own `.walk-salt` (in `--inputs`, else its data folder) and stops without one (a new salt would re-key every
voter); it reaches the container as an environment variable (`compute.compose.yaml`), as the demo key does. A parity run makes one throwaway salt per scratch folder (`walk_salt.hex`).

**What the comparison does for these artifacts** (`tools/compute_parity.py`): the walk store is compared like the other stores, except that its `meta` table is a key/value table with the same field-level mask as `metadata`, and the
`timing` and `peak_memory_mb` fields of its `qa` document (seconds per phase; a Windows-only call) are always left out and named in the line. A zip whose two Windows builds agree byte for byte (the bundle) must be byte-identical
in B; a zip they stamp differently (ingest and extend write `created_at` and the time of writing) is compared member by member, the differing JSON fields of a member left out and named, every other byte and field equal. `density.json`, the receipt of ingest
and the `.sha256` of the bundle are compared byte for byte (no line-ending allowance: `ingest.py` now writes its receipt with LF, as `shown_areas.py` does its manifest).

**The runners** (untracked; the copies from before are `run_remaining.ps1.bak-20261013`, `run_fast.ps1.bak-20261013`, `run_nowithhold.ps1.bak-20261013`). `run_remaining.ps1` and `run_fast.ps1` run roads, street, contacts and walk (R2-R5) through
`tools\compute_build.py --district d --steps <step> --inputs .local --out _build\compute-out\<id>-<utc> --drop-stage`; `run_nowithhold.ps1` does the same for shown, scores, ingest, extend, bundle and density, and leaves import, pincheck, smoke and
the Colorado gate as they were (`run_nowithhold.ps1.bak-20261013b`: the file with the bundle still on Windows). A build writes a new folder and the runner installs its files where the Windows step left them: a file it replaces is moved aside as `<name>.replaced-<utc>`, never deleted (an `ingest` or `extend` folder of an earlier
run is moved aside whole); the Colorado side files stay side files (`scores.unmask.sqlite3`, `density.unmask.json`, hard-linked into a small inputs folder for the density step; the Colorado bundle step gets the new score store, the shown table and the extend zip its mark names the same way, and its zip, `.sha256` and receipt are installed in `data\` and `_build\nowithhold\bundle_receipt_<district>.json`, where the Windows step leaves them). `TERRIER_COMPUTE=windows` runs the old Windows lines, unchanged
(the dry-run plan of each runner is identical to the original's); anything but `container` or `windows` stops. A runner started by `launch_run_*.ps1` has none of a shell's variables, so `TERRIER_COMPUTE` is read from the process, then
the user's, then the machine's environment (`setx TERRIER_COMPUTE windows` is the fallback for a detached run). Images: the environment, else the two local builds of section 8. Do not mix the two modes within one district's ingest, extend, bundle
chain: the container's `ingest` and `extend` leave their folders in `.local\[<district>\]ingest` and `extend`, the Windows ones in `_build\nowithhold`. Only CO-05 has been through the parity run: the OR-05 and CT-01 paths (their street salt, their
`rowfw` folder, their own stores taken from `.local\<district>`) use the same code but have not been run, and no production build has been run through the changed runners; a sandbox test with stubs for the build and a fake tree ran the container path of every step these runners changed, the Windows path of `run_fast.ps1`, and compared the dry-run plans of all three in Windows mode with the originals' (identical); the bundle line of `run_nowithhold.ps1` had the same kind of test (a stub build that checks the inputs it is given: the new score store and not the live one, the shown table, the extend zip of the mark; the zip installed in `data\`, the file it replaced moved aside, the mark naming the installed zip; the dry-run plans of `-Part CO` and `-Part Districts` in Windows mode identical to the `.bak-20261013b` file's).
