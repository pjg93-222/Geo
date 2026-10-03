# Walk List Handoff — 2026-10-02

## Status

- **`main` is fast-forwarded to `b672820`.** Commits, in order:
  - `e5bf03e` feat(walk): add Walk list tab for canvass walk lists
  - `7f80580` fix(walk): remove time estimates from walk-list output
  - `9df1158` docs(walk): add walk list handoff for 2026-10-02
  - `bf2356d` fix(walk): address Codex Astra frontend review findings
  - `43f9fd0` fix(walk): keep refused results instead of blocking the screen
  - `b672820` fix(walk): stable re-knock keys, save drift detection, phone layout
- Work branch: `claude/walk-list`. No remote; nothing pushed.
- Live app on 127.0.0.1:8767 was restarted at 22:13 on `b672820` and serves the **Walk list** tab (8 tabs total). The new backend is required: the new frontend sends `fingerprint` on save, which the old backend rejects.
- Everything is committed. See Open items 1 for what each fix commit changed.

## What the feature does

New tab **Walk list** (`/walk`) builds door-to-door canvass lists from the banked voter data:

- **Pick streets:** cascading area pickers (State Senate → State House → Precinct), a street search box, or **Upload CSV** of street names (`street` required; optional `side` even|odd|both, `city`, `zip`; ≤500 rows). Uploaded rows come back as ✓ matched, "Pick a place" (same name in several cities) or "No match · did you mean …".
- **Walk order:** shortest walking path. Own Dijkstra over a noded TIGER street network; each street side is walked as a run, doors in house-number order in the walking direction. Distance is true metres; the optimiser adds a small crossing allowance (15 m local / 60 m arterial). `ARTERIAL_FACTOR = 1.0` (pure shortest path, owner decision).
- **Every voter is placed:** geocode snap → fuzzy-name snap → nearest road → house-number interpolation → census-block centroid; generated streets/connectors fill TIGER gaps so the network is one component. Interpolated doors are marked "≈".
- **Printed list (PDF)** follows the owner's reference layout: Address | Name | Phone | Pri | Gen | Abs | Party | Age | Gender | Reg. Date | `S U O` | `NH MV`. One header per street side ("Precinct: … Street: … (Even)"). Page 1 has a full map overlay (streets by type, precinct outlines/labels, homes, walked doors, numbered route, scale bar, north arrow) and a short door script. **No time estimates** anywhere in the output.
  - Pri / Gen = primaries / generals voted out of eligible, last 6 even-year elections 2014–2024; Abs = mail ballots (vote codes starting M/A — validated: 88–92% of votes in 2020/22/24 generals). "/" = no eligible elections.
- **CSV export** with the same columns (tallies written "4 of 6", ISO dates, formula-injection guard).
- **Results saved locally:** S Supporter, U Undecided, O Opposed, NH Not home, MV Moved — autosaved per voter; **Not home again** creates a re-knock list (voters found by `voter_key`, so it survives rebuilds that change person ids).
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

API (all loopback-only, 403 in demo mode, `no-store`, strict parameter whitelist, CSRF on POST): GET `options`, `streets`, `list`, `overview`, `lists`, `lists/<key>`; POST `match`, `lists`, `dispositions` under `/api/v3/walk/`. Caps: 40 streets and 1,500 voters per list; 400 runs per routing group. Every built list carries `fingerprint` (sha256 of what it shows plus the voter key behind each person id); POST `lists` takes an optional `fingerprint` and answers `changed` (contract still `walk-list-1`: both fields are additive).

Dependencies added: `jspdf@4.2.1`, `jspdf-autotable@5.0.8` (pinned; lazy-loaded on Export; `npm audit` clean).

## Verification (at `b672820`)

- Frontend `test:unit` 124/124; `tsc --noEmit` clean; `check-no-eval.mjs --require` OK; `build` + `compress-demo.mjs` OK.
- Django `server.research --parallel`: 296 OK.
- Walk unittests (`tools.test_walk_geo tools.test_walk_build server.research.test_walk_route server.research.test_walk_testkit`): 119 OK. `test_forty_street_grid_speed` failed once (1.03 s against a 1.0 s limit) while the machine was busy with builds and servers, then passed 3/3 run alone; the routing code is unchanged. Treat it as timing-sensitive under load.
- `verify-walk-list.mjs`: 20/20 checks, 0 failures, 0 page errors, on a fresh synthetic world (1,500 people). This includes the 375x812 phone-card check.
- Manual UI check at desktop and 375 px: the generate / mark / auto-save flow, the "not saved" flag and alert, contrast 6.5–8:1, and the screen-reader description of "not saved" all work. The app renders its light theme even when the OS is dark, so dark-mode contrast is not a concern.
- Earlier, at `7f80580`: `verify-actual-data.mjs` 9/9; `tools/Test-TerrierDesktop.ps1` 26/26; `tools.test_build_scores` 15 OK, `tools.test_measures` 16 OK (not re-run since).
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

1. **Done — Codex Astra review r3 findings fixed** (`bf2356d`): unsent results survive leaving the screen (page-level shelf, retry on return); results from other lists show as dated history while this list's own results come back selected (API `last` now carries `list`); initial CSV matching is race-safe; visit date is captured when marked; precinct labels avoid enclave holes; focus returns to Save; the PDF collision test runs all fixtures. Verified at `bf2356d`: Django 290 OK, walk unittests 119 OK, frontend unit 105/105, build, no-eval, `verify-walk-list.mjs` 14/14 on a synthetic world. With the refusal fix below: frontend unit 115/115, `tsc` clean, build + compress-demo + no-eval OK, Django 290 OK, `verify-walk-list.mjs` 17/17 (new checks: a result survives leaving the tab with its visit day; a late file match changes nothing; a refused result stays flagged and named and does not block). Known limitation: `last` is the latest result across all lists, so an older result on this list that another list superseded shows as that other list's history.
   - **Fixed (`43f9fd0`) — a refused result batch no longer leaves the screen stuck.** Before, any failed batch went back to the outbox, so a batch the server refuses every time blocked opening another list until reload (and the page-level shelf carried it across visits). Now only the walk API refusing in its own JSON words counts as a refusal: 400 or 404 on a results request, 400 on saving the list (`refusalOf`, `ApiError.answered` in `api/client.ts`). An HTML body, a failed session/CSRF fetch, 403, 408, 409, 410, 413, 422, 429 and 5xx stay retryable and still block with Retry. A refused 400 batch is split in halves to find the refused results, and the split stops when both halves are refused (a batch refused throughout costs 3 requests). A 404 (list gone) or a refused list save refuses the whole batch unsplit. Refused results leave the outbox, so loading is never blocked by them, but **a walker's mark is never erased**: it stays on screen as their code, drawn hollow/dashed and labelled "S (not saved)", stays in the CSV as `S (not saved)`, and comes back flagged if the list is reopened in the same page load. Choosing the code again re-queues it, and the flag clears once it is saved. A rail note adds up refusals across batches, days and lists until dismissed, naming each list, each door with its code, and the server's reasons.
   - **Fixed (`b672820`) — re-knock selects by `voter_key`.** Root cause: `reknock()` collected the parent list's NH results by `person_id` and looked them up with `voters_by_id` (`walk_views.py:543,547` at `43f9fd0`); person ids come from the contact stores' salt, so after those are rebuilt under a new salt the lookup found nobody (409 "no longer in the walk list data"). Now the NH results carry the `voter_key` every `Disposition` row already stores (no migration) and `walk_store.voters_by_key` finds them. When one voter key sits on several rows (a voter id twice in the file) the person the result was recorded for is kept, else the rows at the door it was recorded at (from the parent list's frozen payload); a key on one row is that voter wherever they live now; anything else is left out. The response carries `missing` (NH voters the data no longer has; all missing is still a 409) and the page says "N voters marked Not home are no longer in the voter data." Otherwise the client only sends the parent key. The lookup scans `person` (no index on `voter_key`): about 0.2 s for a few voters and 0.5 s for 1,500 on a 491k-row synthetic table.
   - **Fixed (`b672820`) — saving a list never quietly saves a different one.** Root cause: POST `lists` rebuilt the list from its query and froze that (`walk_views.py:568-573` at `43f9fd0`), while the page kept showing its own copy and took only the new key (`WalkScreen.tsx:329`). Now every built list carries a `fingerprint`; the page sends it on save, and when the list built to save differs the server still saves it (201, no new status, nothing to retry) and answers `changed: true`. The page shows the saved list with the note "The voter data was updated since this list was shown; showing the saved version." When person ids changed but the voters did not (contact stores rebuilt under a new salt), each voter shown is found on the saved list by street, house, unit and normalised name (`movedIds` in `lib/walk.ts`; voter keys never reach the page) and the waiting results, the request already on its way, the marks on screen, the "not saved" flags and the refused marks all move to the new id. Two people alike at one door, a changed name or a voter who is gone are not guessed: such a mark is refused on the page, never sent, and named in the refusal note with its door and code (so a batch is not split and good results are not refused with it). The note's headline then reads "N results were not saved:" instead of "The server refused…", with the reason line kept. A page from before this change sends no fingerprint and gets the old behaviour (`changed: false`).
   - **Fixed (`b672820`) — phones.** At 640 px and below each voter row is a card (`styles/walk.css`, last block): address, name, a wrapping line of labelled details (phone, Pri, Gen, Abs, Party, Age, Gender, Reg.), then the five result buttons across the full width, 44 px tall. Nothing scrolls sideways at 375 px; desktop and the PDF are unchanged. The table keeps explicit roles (`table`, `rowgroup`, `row`, `columnheader`, `cell`) so it is still a table to screen readers once laid out as cards (Safari drops native table semantics otherwise). A refused mark's result group is described by its "S (not saved)" text (`aria-describedby`). Typing one letter in the street search says "Type at least 2 letters".
   - Verified: Django `server.research --parallel` 296 OK; walk unittests 119 OK; frontend unit 124/124; `tsc` clean; build + compress-demo + no-eval OK; `verify-walk-list.mjs` 20/20 on a fresh synthetic world on 8769. New checks: a save answered `changed` swaps in the saved list with the note, saves the other marks and never sends the dropped voter's mark (headline "1 result was not saved:"); a save answered with new person ids carries both marks over under the new ids, none refused; a re-knock answered `missing: 1` says so; at 375x812 every voter is a card with its name and all five 44 px buttons in view and the table roles kept; a refused mark's group is described by its flag; one typed letter shows the hint.
2. Final-review leftovers:
   - Refused marks and the refusal note live in page memory only: a full reload drops them (the unload guard does not cover them, since nothing is left to send). The CSV is the walker's fallback copy.
   - On phones the refusal note sits in the rail at the top of the page, far above the cards. The per-card "! X (not saved)" flag is the visible cue at the door. Consider repeating a short note near the list or making it sticky.
   - `fingerprint` is optional on save so pages loaded before `b672820` keep working. Making it required needs a contract bump (`walk-list-1` → next); owner's call.
   - The API still returns a `minutes` figure in the list totals (`walk_views.py`, `MINUTES_PER_DOOR`). No output shows it, per the no-time-estimates rule, so it could be dropped from the payload.
7. **Dispersed lists route slowly.** Re-measured on real data (`plan_payload`, wall clock, caps respected, measured while other tests ran, so approximate; 0 violations): typical (one precinct) p50 0.14 s / p95 0.81 s, 27 cases; clustered (one House district) p50 0.26 s / p95 1.26 s, 19 cases; dispersed (several House districts) p50 3.4 s / p95 5.0 s / max 5.7 s, only 5 cases within the voter cap. The time goes on the distance between groups, not the run count (14 streets / 37 runs took 5.7 s). Not fixed; next step: profile the A* between groups in `walk_route.py`.
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

## Testing hygiene

- Start synthetic servers with `export CU_DATA_DIR=... CU_DEMO=0` in the same shell as the server. One server started without the variable served the real `.local` data. `verify-walk-list.mjs` refuses to run against a data set that large (its guard caught it).
- On Windows two servers can bind the same port and the older one takes the connections. Check `netstat` before starting, and stop every server you start (subagent-started servers have been left orphaned more than once).
- Never use port 8767 for tests.
