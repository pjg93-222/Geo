# Walk List Handoff — 2026-10-02

## Status

- **Merged to `main`** (fast-forward) at `7f80580`:
  - `e5bf03e` feat(walk): add Walk list tab for canvass walk lists
  - `7f80580` fix(walk): remove time estimates from walk-list output
- Work branch: `claude/walk-list`. No remote; nothing pushed.
- Live app on 127.0.0.1:8767 was restarted on the committed code and serves the new **Walk list** tab (8 tabs total).
- **In progress (uncommitted, separate lane):** fixes for 7 findings from the Codex Astra frontend review (see Open items 1).

## What the feature does

New tab **Walk list** (`/walk`) builds door-to-door canvass lists from the banked voter data:

- **Pick streets:** cascading area pickers (State Senate → State House → Precinct), a street search box, or **Upload CSV** of street names (`street` required; optional `side` even|odd|both, `city`, `zip`; ≤500 rows). Uploaded rows come back as ✓ matched, "Pick a place" (same name in several cities) or "No match · did you mean …".
- **Walk order:** shortest walking path. Own Dijkstra over a noded TIGER street network; each street side is walked as a run, doors in house-number order in the walking direction. Distance is true metres; the optimiser adds a small crossing allowance (15 m local / 60 m arterial). `ARTERIAL_FACTOR = 1.0` (pure shortest path, owner decision).
- **Every voter is placed:** geocode snap → fuzzy-name snap → nearest road → house-number interpolation → census-block centroid; generated streets/connectors fill TIGER gaps so the network is one component. Interpolated doors are marked "≈".
- **Printed list (PDF)** follows the owner's reference layout: Address | Name | Phone | Pri | Gen | Abs | Party | Age | Gender | Reg. Date | `S U O` | `NH MV`. One header per street side ("Precinct: … Street: … (Even)"). Page 1 has a full map overlay (streets by type, precinct outlines/labels, homes, walked doors, numbered route, scale bar, north arrow) and a short door script. **No time estimates** anywhere in the output.
  - Pri / Gen = primaries / generals voted out of eligible, last 6 even-year elections 2014–2024; Abs = mail ballots (vote codes starting M/A — validated: 88–92% of votes in 2020/22/24 generals). "/" = no eligible elections.
- **CSV export** with the same columns (tallies written "4 of 6", ISO dates, formula-injection guard).
- **Results saved locally:** S Supporter, U Undecided, O Opposed, NH Not home, MV Moved — autosaved per voter; **Not home again** creates a re-knock list.
- Raw data only — no scores, weights or alignment (owner decision).

## Data build (`.local/walk.sqlite3`)

```powershell
$env:OPENBLAS_NUM_THREADS=1; $env:OMP_NUM_THREADS=1
& ..\..\work\.venv\Scripts\python.exe tools\build_walk.py      # defaults: source CSV in Downloads, data dir .local
```

- Reads `C:\Users\pgoff\Downloads\statara_co_cd005_20260904.csv` plus the contacts/scores/street/roads stores. Person join = CSV row *i* ↔ `contacts.rowid` *i+1* (`.local/.street-salt` is missing, so ids are never recomputed).
- Quality gates (build refuses to replace the store if any fail): unplaced stops = 0, stops without a span = 0, largest network component = 100%, side agreement ≥ 0.85 (actual 0.9713), mail share ≥ 0.85 per general (0.884 / 0.920 / 0.899).
- Last real build: 207,198 stops, 0 of 491,308 voters unplaced; tiers a 191,805 · b 3,897 · c 545 · d 9,752 · e 386 · g 813; 1,082 generated streets; ~326 MB; ~2 min.
- `.local/.walk-salt` (new) keys `voter_key` so saved results survive rebuilds — **keep it**.
- Previous stores are kept in `.local/walk-backups/` (two copies, ~650 MB) — safe to delete.
- Rebuild after any voter-file refresh. The server opens the store per request, so no restart is needed for data.

## Accounts database

- Migration `research.0002_walklist_disposition` is applied to `.local/accounts.sqlite3` (backup: `.local/backups/accounts-20261002T203226Z.sqlite3`).
- `Start-TerrierServer.ps1` does **not** run migrations. On a fresh data folder run `manage.py migrate` once (the walk API returns a 503 naming the fix if tables are missing).
- Saved lists store their frozen payload (names/phones) in `accounts.sqlite3`; there is no delete path (owner accepted).

## Code map

| Area | Files |
|---|---|
| Names, window rules | `server/research/walk_names.py` |
| Routing (Dijkstra, clustering ≤1.5 km groups, A* between groups) | `server/research/walk_route.py` |
| Store access, API, map overlay | `server/research/walk_store.py`, `walk_views.py`, `walk_context.py`, `server/urls.py` |
| Saved lists / results | `server/research/models.py` (WalkList, Disposition), `migrations/0002_walklist_disposition.py` |
| Loopback gate refactor | `server/research/street_views.py` (`local_denied`, `local_only_post`; existing behaviour unchanged) |
| Build tools | `tools/walk_geo.py`, `tools/build_walk.py`, `tools/make_walk_world.py` |
| Frontend | `frontend/src/v3/components/WalkScreen.tsx`, `WalkMap.tsx`; `api/walk.ts`, `api/walkTypes.ts` (contract `walk-list-1`), `api/client.ts` (`postJson` + CSRF); `lib/walk.ts`, `walkScript.ts`, `walkPdf.ts`, `walkGeo.ts`, `download.ts`; `styles/walk.css` |
| Tests | `server/research/test_walk_*.py`, `tools/test_walk_geo.py`, `tools/test_walk_build.py`, `frontend/tests/v3/walk.test.ts`, `frontend/scripts/verify-walk-list.mjs`, `frontend/scripts/check-no-eval.mjs` |

API (all loopback-only, 403 in demo mode, `no-store`, strict parameter whitelist, CSRF on POST): GET `options`, `streets`, `list`, `overview`, `lists`, `lists/<key>`; POST `match`, `lists`, `dispositions` under `/api/v3/walk/`. Caps: 40 streets and 1,500 voters per list; 400 runs per routing group.

Dependencies added: `jspdf@4.2.1`, `jspdf-autotable@5.0.8` (pinned; lazy-loaded on Export; `npm audit` clean).

## Verification (at `7f80580`)

- Django `server.research --parallel`: 289 OK. Walk unittests (`tools.test_walk_geo tools.test_walk_build server.research.test_walk_route server.research.test_walk_testkit`): 119 OK. `tools.test_build_scores` 15 OK, `tools.test_measures` 16 OK.
- Frontend `test:unit` 91/91; `build` OK; `check-no-eval.mjs --require` OK; `compress-demo.mjs` OK.
- `verify-actual-data.mjs` 9/9; `tools/Test-TerrierDesktop.ps1` 26/26.
- Real-data routing smoke: 73 cases, 0 violations; typical list p95 ≈ 0.26 s.
- Reviews: Opus backend (security clean), Sonnet frontend, Codex Astra (`gpt-6-astra`, xhigh) frontend r2 + r3.
- Known pre-existing failure: `frontend/scripts/verify-restored-navigation.mjs` asserts the old label "Self-described affiliation" (renamed in `fe311da`) and expects demo mode with zero API calls — not caused by this work.

## Owner decisions on record

- Keep it simple: raw data only; shortest-path routing; no time estimates; Even/Odd separate lists for printed packets.
- Standing rules: the owner wants the copy-ban test and the "no bulk export" rule removed, but agent edits to those were blocked by the safety classifier. They remain in place; the owner can edit `frontend/tests/v3/copy.test.ts` (banned-word list ~line 8) and the comment in `server/research/v3_people_views.py` (~lines 9–12) personally. UI copy currently avoids the banned words.
- Counsel check on voter-file use: waived by the owner.

## Rush deliverable (2026-10-02)

- Source sheet: `C:\Users\pgoff\Downloads\Walklist Streets.xlsx` (94 rows). 80 exact matches + 6 corrected spellings (verified online, ZIP 80922): Lessburg → Leesburg Rd (D), Mismark → Bismark Rd (D), Mule Shore → Muleshoe Dr (C), Boot Hll → Boothill Dr (C), River Mist Point → River Mist Grv (same street, B), River Bay Grove → Riverbay Grv (A).
- 8 sheet roads have no addressed homes (through-roads; houses are on side streets): Apache Tear Dr, Leeward Way, Lagoon Dr, Sea Coral Vw, Gulfstream Dr, Springbrook Dr, Bankside Dr, Roping Dr. Adding their side streets was offered, not done.
- Output folder `C:\Users\pgoff\Downloads\Walk Lists\` (`Even\`, `Odd\`, `Summary.csv`, `Notes.txt`) and `Walk Lists.zip` (1.4 MB, for Teams). 87 streets, 5,265 voters.
- Saved in Terrier (Walk list tab):

| List | Even key (doors/voters) | Odd key (doors/voters) |
|---|---|---|
| A 5101521412 | `b942adf324` (291/546) | `195f83d12a` (313/665) |
| B 5101521413 | `faf14c468c` (249/516) | `ccef66e206` (264/578) |
| C 5101521416 | `85f00c36e9` (281/582) | `98dd6124ef` (305/647) |
| D 5101521419 | `b0d93e7210` (227/479) | `d72a5dd98c` (219/459) |
| E 5111521414 | `43b04ff1ab` (197/409) | `e1599f2bf3` (192/384) |

## Open items

1. **Codex Astra review r3 findings (fix lane running, uncommitted):** HIGH — switching tabs while a result save fails can lose that result; MEDIUM — prior-list results shown as current marks; initial CSV match can land in another list's picker; retried saves record the retry date; precinct label can fall inside an enclave; LOW — focus after Save targets a disabled button; a PDF test skips its stress fixtures. Commit and fast-forward `main` once green.
2. Final-review leftovers: re-measure dispersed-street routing p95 on real data; re-knock selects by person id (weaker across rebuilds than `voter_key`); saving a list regenerates it server-side (can differ from screen if the store was rebuilt meanwhile); a refused result batch leaves the screen stuck until reload.
3. Separate task (own session): contact-sheet CSV export guard only checks the first character for formula injection (`terrier_contacts.py csv_value`).
4. Codex tooling: the Codex MCP server is older than the bundled CLI, so `gpt-6-astra` works only via `codex exec` (MCP returns a 400 "requires a newer version").
5. GateGuard friction: optional `GATEGUARD_EXEMPT_GLOBS` in `~/.claude/settings.json` for this repo and scratch paths (owner's call).
6. Turf splitting into packets (e.g. ~100 doors per volunteer) was removed for simplicity; large lists (e.g. list A) need manual splitting by route stop.

## Useful commands

```powershell
npm --prefix frontend run build; node frontend/scripts/compress-demo.mjs        # frontend changes: rebuild, reload page
./Stop-TerrierServer.ps1; ./Start-Terrier.ps1 -NoBrowser                         # Python changes: restart
& ..\..\work\.venv\Scripts\python.exe manage.py test server.research --parallel
npm --prefix frontend run test:unit
node frontend/scripts/check-no-eval.mjs --require
# Synthetic browser check (never on 8767):
& ..\..\work\.venv\Scripts\python.exe tools\make_walk_world.py <empty-dir>
$env:CU_DATA_DIR='<empty-dir>'; $env:CU_DEMO='0'; & ..\..\work\.venv\Scripts\python.exe manage.py serve --port 8768
$env:TERRIER_URL='http://127.0.0.1:8768'; node frontend/scripts/verify-walk-list.mjs
```
