# Terrier store builds in a container (compute lane 1)

D-173 / D-177: Terrier's store builds run in a container and are proven content-identical to the Windows build before they
replace it. Design: `COMPUTE_CONTAINERS_DESIGN.md` (sections 1.1, 2.1, 4, 6). This file is the runbook. HOSTED.md is the serve side.

**Status (2026-10-12, evening):** Docker Desktop 4.95.0 (WSL2 backend, engine 29.8.2) is installed. `roads`, `street` and `contacts` (CO-05)
have run in the container and passed parity against the Windows build, on a LOCAL build of both images from e60d68e: the CI images of
release v2026.10.12.8 are private in GHCR and an anonymous pull is refused (`unauthorized`), so their digests are not yet compared
with the local ones. The export goes through a named volume by default (section 5); `--export-bind` opts out. Section 5 lists what the other steps need.

## 1. What is where

| File | Job |
|---|---|
| `tools/hostpaths.py` | every machine path a build tool used to hard-code, as an environment variable (section 2). Defaults are today's Windows paths, so the laptop builds as before |
| `tools/build_step.py` | `run <step> --district d` / `export <out> <files>` / `fingerprint`. The container entrypoint, and also what the Windows side runs, so both sides take the same code path |
| `Dockerfile.build` | the toolchain, no code and no data: target `geo` (CPython 3.12.14, = `work\.venv`; `python:3.12.14-slim-trixie`) and target `score` (CPython 3.12.10, = system Python; still `python:3.12.10-slim-bookworm`, because the registry has no 3.12.10 trixie image); both apply Debian's security updates at build, hashed locks `requirements.build.lock` / `requirements.score.lock` |
| `compute.compose.yaml` | services `terrier-build` (geo image) and `terrier-score` (score image, `TERRIER_SCORE_IMAGE`; the same mounts, shared through one YAML anchor): network none, read-only root, uid 10001, inputs read-only, writes only the `stage` volume; and `terrier-export` (copies stores from the `stage` volume into a second named volume, `terrier-export-<run id>`, with an integrity check; the harness `docker cp`s them to the scratch folder and removes it) |
| `compute.export-bind.compose.yaml` | opt-out override (`--export-bind`): `terrier-export` writes `/out` into the bind-mounted scratch folder instead of the volume. Native-Linux hosts only |
| `tools/compute_parity.py` | the parity run: Windows twice (A1, A2), container once (B), compare, PASS/FAIL per artifact. Each step has a toolchain: roads, street, contacts run in the geo image and, on Windows, the work venv (`--python`); shown, scores in the score image and, on Windows, the system Python 3.12 (`--score-python`, `$TERRIER_PY312`). An image is `--image` / `--score-image`, else `$TERRIER_BUILD_IMAGE` / `$TERRIER_SCORE_IMAGE`, else the verified current release manifest (section 3, step 4) |
| `.github/workflows/release.yml` job `build-image` | on a `v<YYYY>.<MM>.<DD>.<n>` tag: builds both targets, pushes `ghcr.io/pjg93-222/terrier-build` (geo) and `terrier-build-score` by digest, SBOMs, `terrier-build.digests.json` on the draft release. Job `draft-release` folds both digests into the release's `digests.json`, which the platform's `ops/release.py` reads, so the signed manifest carries them |
| `.github/workflows/release.yml` job `scan` + `tools/image_gate.py` | trivy on `terrier-web`, `terrier-build` and `terrier-build-score` by digest, the platform's image policy (D-181, D-182: fixable HIGH/CRITICAL vulnerabilities, misconfigurations, secrets and pip/npm licences that are forbidden, restricted or unknown block; unfixed vulnerabilities and OS-package licences are reported). The draft release waits for it. Tests: `tools/test_image_gate.py` |
| `tools/test_compute.py` | the tests (also in the release workflow's test job) |

Steps wired in `tools/build_step.py`, in the order Terrier's Windows runner (`_build/run_nowithhold.ps1`) runs them, each one row of
`STEPS`: `roads`, `street`, `contacts` (toolchain `geo`), `shown` (`tools/shown_areas.py --district d`; writes `area_scores_shown.csv`
and its `.manifest.json`) and `scores` (`tools/build_scores_v3.py --district d --receipt <data dir>/scores_receipt.json`; writes
`scores.sqlite3`; the receipt would otherwise go into the repo's `_build`, which is read-only in a container) (toolchain `score`).
`shown` and `scores` read the earlier stores (street, contacts, the shown table) from the same folder, so run them after those.

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
file one bit flipped in the middle. Each must make the comparison FAIL (the line says "detected"). A store is compared exactly (the
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
  varies is; every other key must match B, and the line names what was left out. Still to add as rows in `STEPS` (each reads earlier stores from the same folder): `walk` (needs `.walk-salt` copied into the stage first), `density`, and the bundle (`build_bundle_v4.py` writes
  to `--out-dir` and `--receipt`, both outside `TERRIER_LOCAL_DIR`, and imports Django settings: set `CU_DATA_DIR` and `CU_SECRET_KEY` to
  scratch values). Live sizes: scores 551 MB, walk 311 MB. Compare the bundle with `--files data/<key>.zip`.
- `--api` runs `co_baseline.py check --parts api --data-dir <folder>` on A1 and B (a spare server per folder, minutes each). It needs a
  complete data folder (snapshots, accounts), so it only makes sense after the full set exists in every folder.
- Salts and keys (`.street-salt`, `.walk-salt`, `.demo-key`, `.secret`) are copied, never regenerated: for CO the street salt is the
  read-only mount; for OR/CT `build_step.py` stops when it is missing. The Census downloads (T1, T9) stay on Windows.
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

- Run under Docker so far: the `geo` image built locally from e60d68e and `roads`, `street`, `contacts` of CO-05 (parity PASS). Not
  yet run: the `score` image on a real step, `walk`, `shown`, `scores`, `density`, the bundle and `--api`. The CI job itself has run
  (v2026.10.12.8) but its images were not pulled; compare their digests with a local build only after the registry login that is Paul's.
- `tools/luna_district` (OR/CT phase 3, T6) still calls `ctypes.windll` without a POSIX branch (`common.available_ram_gb`,
  `peak_working_set_gb`); the seams are in, the container does not run it. `build_walk.py` and `build_scores_v3.py` already degrade.
- The images' Debian SQLite (the `geo` image is Debian 13, the `score` image Debian 12 with 3.40.1) differs from the host's (3.53.1 in
  `work\.venv`, 3.49.1 system): store bytes can never be identical, which is why the gate is logical. A table-level variance mask is coarse: a stamp inside a table that also holds real
  values (for example `scores.metadata`) hides the whole table; refine with a column mask once a Windows A1/A2 pair shows what varies.
- The score image's package set is the design's list (numpy, pandas, pyarrow, scipy, joblib); if `shown_areas` or the rowfw children
  import anything else, add it to `requirements.score.in` and regenerate the lock.
- `current.py` (the manifest reader) is Melange's `ops/release_lib/current.py`. Until a signed release names `terrier-build` (the Geo workflow
  must run on a `v*` tag with the folded `digests.json`) and that script is in the Melange tree at the default path, the harness stops unless
  `--image` or `$TERRIER_BUILD_IMAGE` names a digest.
