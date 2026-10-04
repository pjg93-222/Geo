# Walk List Handoff — 2026-10-02

## Status

- **`main` is fast-forwarded to `f7d9272`** (plus this handoff update). Commits, in order:
  - `e5bf03e` feat(walk): add Walk list tab for canvass walk lists
  - `7f80580` fix(walk): remove time estimates from walk-list output
  - `9df1158` docs(walk): add walk list handoff for 2026-10-02
  - `bf2356d` fix(walk): address Codex Astra frontend review findings
  - `43f9fd0` fix(walk): keep refused results instead of blocking the screen
  - `b672820` fix(walk): stable re-knock keys, save drift detection, phone layout
  - `a6f68cf` docs(walk): update handoff for b672820 and the live restart
  - `290eddf` fix(walk): show a bottom bar on phones while results are not saved
  - `fc671d4` fix(walk): drop the unused minutes figure from list totals
  - `0650b33` docs(walk): record the 2026-10-03 live restart on fc671d4
  - `fde78d4` fix(walk): require the shown list's fingerprint on save
  - `6612c77` docs(walk): record the 2026-10-03 live restart on fde78d4
  - `f7d9272` feat(server): crash logging and an auto-restart supervisor
- Work branch: `claude/walk-list`. No remote; nothing pushed. Everything is committed. See Open items 1 for what each fix commit changed.
- Live app on 127.0.0.1:8767 was restarted on 2026-10-03 at 21:15 on `f7d9272` and is **supervised**: server PID 25184 (launcher 73988), supervisor PID 19412 (`_build/terrier-supervisor-pid.json`; its log says "watching server pid 25184"). `_build/terrier-faults.log` has its start header, and the previous err log was rotated to `terrier-server.err.20261003T211515.log`. It serves the **Walk list** tab (8 tabs total), requires `fingerprint` on a plain list save and no longer sends `minutes`. A tab older than the 2026-10-02 22:13 restart gets "This page is out of date. Reload it, then save the list again." with its marks kept and flagged "not saved". Stop it with `./Stop-TerrierServer.ps1`, which stops the supervisor first.
- Earlier restarts: 2026-10-03 17:40 on `fde78d4` (PID 82300, unsupervised); 17:07 on `fc671d4` (PID 55544, which crashed at 17:25 with a thread-pool handle fault seen across Python processes on this machine, not specific to Terrier; see Open items 8); 2026-10-02 22:13 on `b672820`.

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
| Frontend | `frontend/src/v3/components/WalkScreen.tsx`, `WalkMap.tsx`; `api/walk.ts`, `api/walkTypes.ts` (contract `walk-list-1`), `api/client.ts` (`postJson` + CSRF); `lib/walk.ts`, `walkRefused.ts` (refused marks and note in `sessionStorage`), `walkScript.ts`, `walkPdf.ts`, `walkGeo.ts`, `download.ts`; `styles/walk.css` |
| Tests | `server/research/test_walk_*.py`, `tools/test_walk_geo.py`, `tools/test_walk_build.py`, `frontend/tests/v3/walk.test.ts`, `walkRefused.test.ts`, `frontend/scripts/verify-walk-list.mjs`, `frontend/scripts/check-no-eval.mjs` |

API (all loopback-only, 403 in demo mode, `no-store`, strict parameter whitelist, CSRF on POST): GET `options`, `streets`, `list`, `overview`, `lists`, `lists/<key>`; POST `match`, `lists`, `dispositions` under `/api/v3/walk/`. Caps: 40 streets and 1,500 voters per list; 400 runs per routing group. Every built list carries `fingerprint` (sha256 of what it shows plus the voter key behind each person id); POST `lists` requires `fingerprint` on a plain save (400 "This page is out of date. Reload it, then save the list again." without it; a re-knock body takes none and refuses one) and answers `changed` (contract still `walk-list-1`: both fields are additive).

Dependencies added: `jspdf@4.2.1`, `jspdf-autotable@5.0.8` (pinned; lazy-loaded on Export; `npm audit` clean).

## Verification (at `b672820`)

- Latest run, `fingerprint` required on save (on top of `0650b33`): frontend `test:unit` 126/126; `tsc` clean; no-eval OK; build + compress-demo OK; Django `server.research --parallel` 298 OK; walk unittests 119 OK; `verify-walk-list.mjs` 21/21, 0 failures, 0 page errors (1,500-person synthetic world, port 8769; new check: a direct save without `fingerprint` answers 400 with the reload message and saves nothing).
- Earlier run, the `minutes` removal on `290eddf`: frontend `test:unit` 125/125; Django 296 OK; walk unittests 119 OK; `verify-walk-list.mjs` 20/20. The list below is the earlier run at `b672820`.
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
   - **Fixed (`b672820`) — saving a list never quietly saves a different one.** Root cause: POST `lists` rebuilt the list from its query and froze that (`walk_views.py:568-573` at `43f9fd0`), while the page kept showing its own copy and took only the new key (`WalkScreen.tsx:329`). Now every built list carries a `fingerprint`; the page sends it on save, and when the list built to save differs the server still saves it (201, no new status, nothing to retry) and answers `changed: true`. The page shows the saved list with the note "The voter data was updated since this list was shown; showing the saved version." When person ids changed but the voters did not (contact stores rebuilt under a new salt), each voter shown is found on the saved list by street, house, unit and normalised name (`movedIds` in `lib/walk.ts`; voter keys never reach the page) and the waiting results, the request already on its way, the marks on screen, the "not saved" flags and the refused marks all move to the new id. Two people alike at one door, a changed name or a voter who is gone are not guessed: such a mark is refused on the page, never sent, and named in the refusal note with its door and code (so a batch is not split and good results are not refused with it). The note's headline then reads "N results were not saved:" instead of "The server refused…", with the reason line kept. (Until `fingerprint` was made required, a page from before this change that sent none got `changed: false`.)
   - **Fixed (`b672820`) — phones.** At 640 px and below each voter row is a card (`styles/walk.css`, last block): address, name, a wrapping line of labelled details (phone, Pri, Gen, Abs, Party, Age, Gender, Reg.), then the five result buttons across the full width, 44 px tall. Nothing scrolls sideways at 375 px; desktop and the PDF are unchanged. The table keeps explicit roles (`table`, `rowgroup`, `row`, `columnheader`, `cell`) so it is still a table to screen readers once laid out as cards (Safari drops native table semantics otherwise). A refused mark's result group is described by its "S (not saved)" text (`aria-describedby`). Typing one letter in the street search says "Type at least 2 letters".
   - **Fixed (after `290eddf`) — the list totals no longer carry `minutes`.** The figure was never shown (no-time-estimates rule); removed from `walk_views.py` with `MINUTES_PER_DOOR` / `METRES_PER_MINUTE` (nothing else used them), from `WalkTotals`, and from the `verify-walk-list.mjs` report. Contract stays `walk-list-1` (removing an unused field breaks no client). Lists saved earlier keep `minutes` in their frozen payload (not migrated); the page does no runtime shape check, so it opens them as before (unit-tested: `totalsLine`, CSV and PDF ignore it). The fingerprint hashes the payload, so fingerprints change once: a tab loaded before the server restart that then saves gets `changed: true` and the "voter data was updated" note one time. Verified: Django 296 OK; walk unittests 119 OK; frontend unit 125/125; `tsc` clean; build + compress-demo + no-eval OK; `verify-walk-list.mjs` 20/20 on a fresh synthetic world on 8769. New Django assertions: `minutes` absent from built, saved and re-knock totals and from the saved row's frozen payload.
   - Verified: Django `server.research --parallel` 296 OK; walk unittests 119 OK; frontend unit 124/124; `tsc` clean; build + compress-demo + no-eval OK; `verify-walk-list.mjs` 20/20 on a fresh synthetic world on 8769. New checks: a save answered `changed` swaps in the saved list with the note, saves the other marks and never sends the dropped voter's mark (headline "1 result was not saved:"); a save answered with new person ids carries both marks over under the new ids, none refused; a re-knock answered `missing: 1` says so; at 375x812 every voter is a card with its name and all five 44 px buttons in view and the table roles kept; a refused mark's group is described by its flag; one typed letter shows the hint.
2. Final-review leftovers:
   - Refused marks and the refusal note live in page memory only: a full reload drops them (the unload guard does not cover them, since nothing is left to send). The CSV is the walker's fallback copy. **Fixed:** both survive a reload of the tab. They are kept in `sessionStorage` under one versioned key, `terrier.walk.refused.v1`, as two separate parts: `marks` (the existing `RefusedMarks`: `listIdentity` (`key:<key>` or `query:<sorted query>`) → person id → code, `null` for a refused clearing) and `note` (the existing `RefusalNote`: list titles, door labels with their codes, reasons, and the `local` flag). Nothing else is stored, and nothing goes to `localStorage` or the address. `lib/walkRefused.ts` owns it: `refusedStore(storage)` restores once (in `WalkScreen.tsx`, when the module loads) and writes through on every change: `hold` (a refusal: `keepRefused` + `addRefusal`), `adopt` (a list saved: `rekeyRefused` for new person ids, then the marks refused before the save move to its key; see below), `clear` (results saved: `clearRefused`, the only way a mark leaves) and `dismiss` (clears the note only; the marks stay, flagged). On reading, the shape is checked; malformed parts are dropped and the key is rewritten without them, or removed. Every storage access is in try/catch: blocked or full storage leaves the page working from memory as before. The storage is injected (as in `savedViews.ts`) for unit tests. The note comes back on any list; the flags come back when their list is shown (`marksWithRefused`). `beforeunload` (both `warn` and `warnHeld`) now also asks while the refusal **note** is up, and never for refused marks alone: a mark on a voter who is off the list or gone can never be cleared, so it would mean a permanent prompt. Tests: `tests/v3/walkRefused.test.ts` (round trip, malformed data, dismiss keeps marks, save clears, a save moves the query marks to the key (with re-keyed ids), throwing or missing storage, sessionStorage only); `verify-walk-list.mjs` steps `refused survives reload` and `out of date survives reload`; every dialog in that script is now accepted and listed, and any the steps do not expect fails the run. Verified: frontend unit 143/143, `tsc` clean, build + compress-demo + no-eval OK, Django `server.research --parallel` 309 OK, `verify-walk-list.mjs` 23/23 on a fresh synthetic world on 8769.
     - Privacy: Chrome may keep `sessionStorage` for session restore (reopening a closed window or after a crash), and it copies it to a tab made with Duplicate tab. This is acceptable: the same person ids and codes are already in `accounts.sqlite3`, and the app is loopback-only.
     - **Fixed (follow-up commit) — marks refused before a list was first saved follow it to its key.** Before, marks refused while a list was unsaved (a whole-list save refusal, e.g. "This page is out of date") stayed under `query:<sorted query>`. Once the list was saved the address named it by key (`?list=<key>`), so after a reload those marks no longer showed on it. Now `saveEntry` calls `refusals.adopt(query, key, entry.moved)` (this replaces the store's `rekey`; storage is rewritten in one write):
       1. First the person ids are renamed to those of the saved list (`movedIds`, both buckets as before).
       2. Then the query bucket's marks move under `key:<key>`, and the query bucket is removed.
       - **Merge rule:** a voter marked in both buckets keeps the query bucket's mark. It is the newer one, set on the list just shown.
       - The note is keyed by list title, so it is unchanged.
       - Consequence: building the same streets again (a new, unsaved list) no longer shows those marks; they belong to the saved list now.
       - Unit-tested: move + merge, and id rename followed by move.
       - `verify-walk-list.mjs` 8d3 now saves the list by marking S again. It then reloads (accepting the prompt) on `?list=<key>` and checks the NH is still flagged "not saved" while the saved S is not.
       - Verified: frontend unit 144/144, `tsc` clean, build + compress-demo + no-eval OK, `verify-walk-list.mjs` 23/23 on a fresh synthetic world on 8769. No Python changed, so Django was not rerun.
   - On phones the refusal note sits in the rail at the top of the page, far above the cards. The per-card "! X (not saved)" flag is the visible cue at the door. Consider repeating a short note near the list or making it sticky. **Fixed:** at 640 px and below, while a refusal note shows, a bar fixed to the bottom of the screen says "! N result(s) not saved" with a 44 px View button that scrolls to the note and focuses it (`refusalBarText` in `lib/walk.ts`, `.walk-refused-bar` in `walk.css`); not a live region, the page keeps room under the last card, Dismiss removes it, no bar on desktop. Checked in the `mobile` step of `verify-walk-list.mjs`.
   - `fingerprint` is optional on save so pages loaded before `b672820` keep working. Making it required needs a contract bump (`walk-list-1` → next); owner's call. **Fixed:** required on a plain save (400 "This page is out of date. Reload it, then save the list again.", nothing saved), no contract bump (response shape unchanged, frozen payloads untouched); re-knock still takes none and refuses one; the client's `saveWalkList` now requires it and `saveEntry` refuses a list shown without one as a whole-list 400. Needs a server restart.
7. **Dispersed lists route slowly.** Re-measured on real data (`plan_payload`, wall clock, caps respected, measured while other tests ran, so approximate; 0 violations): typical (one precinct) p50 0.14 s / p95 0.81 s, 27 cases; clustered (one House district) p50 0.26 s / p95 1.26 s, 19 cases; dispersed (several House districts) p50 3.4 s / p95 5.0 s / max 5.7 s, only 5 cases within the voter cap. The time goes on the distance between groups, not the run count (14 streets / 37 runs took 5.7 s). Not fixed; next step: profile the A* between groups in `walk_route.py`.
3. Separate task (own session): contact-sheet CSV export guard only checks the first character for formula injection (`terrier_contacts.py csv_value`).
4. Codex tooling: the Codex MCP server is older than the bundled CLI, so `gpt-6-astra` works only via `codex exec` (MCP returns a 400 "requires a newer version").
5. GateGuard friction: optional `GATEGUARD_EXEMPT_GLOBS` in `~/.claude/settings.json` for this repo and scratch paths (owner's call).
6. Turf splitting into packets (e.g. ~100 doors per volunteer) was removed for simplicity; large lists (e.g. list A) need manual splitting by route stop.
8. **Live server crash, 2026-10-03 17:25.** The live server (PID 55544, started 17:07) died with exception code `0xc000070a` at `ntdll+0xc87a4`. That code is `STATUS_THREADPOOL_HANDLE_EXCEPTION` (`certutil -error 0xc000070a`: "Status 0x%08x was returned, waiting on handle 0x%x for wait 0x%p, in waiter 0x%p"), **not** heap corruption (`0xc0000374`). A thread-pool wait fired on a handle that had already been closed or was invalid. That is a handle-misuse bug in software (Python or a native library it loads), not a RAM fault.
   - It is not specific to Terrier. WER `ReportArchive` holds 172 `python.exe` AppCrash reports since 2026-09-19, most with this signature (`0xc000070a` at `ntdll+0xc87a4`). They come from both the codex-runtimes Python and the system Python312 (3.12.14). No readable `.dmp` files were found.
   - The crashed server's `terrier-server.err.log` was overwritten by the 17:40 start, so its output is lost, and nothing restarted it.
   - Fixed since: logs are rotated, a fault log is kept, and a supervisor restarts the server (see "Server supervision and crash logs"). Not fixed: the cause.
   - Suggested next step, owner's call: open the WER `Report.wer` for the 55544 crash to see its loaded-module list, or turn on WER LocalDumps for `python.exe`. LocalDumps is a system registry setting the owner must make; the next crash then leaves a dump showing which module closed the handle.

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

## Server supervision and crash logs

- **Start:** `Start-Terrier.ps1` (and the desktop shortcut) start the server, and also start a hidden, detached `Watch-TerrierServer.ps1` supervisor. If the supervisor cannot be started or recorded, the start still succeeds with a warning, and no unrecorded supervisor is left running. Pass `-NoSupervisor` (to `Start-Terrier.ps1` or `Start-TerrierServer.ps1`) to start the server alone. The server record `_build/terrier-server-pid.json` has the same format as before. The supervisor is recorded in `_build/terrier-supervisor-pid.json` (pid, script, started, url) and identified by PID, `powershell.exe`, its script path and its creation time.
- **On an unexpected exit** the supervisor appends a line to `_build/terrier-supervisor.log` (time, supervisor PID, old server PID, exit code, restart number). It then waits 5, 15, 30, then 60 s and relaunches through `Start-TerrierServer.ps1 -Relaunch` (which implies `-NoSupervisor`). That runs the same checks, log rotation, identity check and record writes as a manual start. It also refuses to launch once a stop has begun, and waits up to 90 s for readiness instead of about 15 s.
  - If a relaunch reports a failure but the server it recorded is still running (for example, readiness timed out while Python was still loading), the supervisor adopts and watches that server instead of giving it up. Once that server listens, the supervisor has `Start-TerrierServer.ps1` finish its record (`launcher_pid` plus the listener), so later starts recognise it.
  - Warnings from the relaunch (for example, a log it could only copy) are written to the supervisor log.
- **Restart cap:** at most 5 restarts in any 10 minutes. After that the supervisor logs "giving up" and exits, and the next `Start-Terrier.ps1` starts a fresh supervisor. If a first start fails, `Start-Terrier.ps1` reports it as before and no supervisor is left behind.
- **Supervisor log cap:** at 1 MB (`$supervisorLogLimit` in `Watch-TerrierServer.ps1`) `Write-SupervisorLog` moves `terrier-supervisor.log` to `terrier-supervisor.1.log`, replacing any older one, and starts a new log, so the two stay under about 2 MB. A move that fails (log held open, old copy read-only) is skipped and tried again on the next line; it never stops the supervisor. The supervisor now running on the live server (PID 19412, started 2026-10-03 21:15) loaded the earlier script, so the cap applies after its next restart; no restart is needed now, since the log grows only a few lines per server restart.
- **Logs** are in `_build/`:
  - Before each launch, a non-empty `terrier-server.err.log` or `.out.log` is renamed to `terrier-server.err.<yyyyMMddTHHmmss>.log`. The newest 10 of each are kept, so the log from the first crash survives a full 5-restart loop.
  - If another program holds the log open (a viewer tailing it, antivirus), it is copied instead of renamed. If even that fails, the start goes on with a warning.
  - `terrier-faults.log` gets one header line per start of the live app (time, PID, port). If Python dies on a fatal error, faulthandler appends the stacks of all threads (`serve.py`). A server on any other port writes `terrier-faults-<port>.log` instead.
  - **Caveat:** a Windows fail-fast (an error ntdll raises with fail-fast, such as the 2026-10-03 thread-pool handle fault) can end the process without running any handler, so faulthandler may record nothing. In that case look in Event Viewer → Windows Logs → Application ("Application Error" / Windows Error Reporting) and in `C:\ProgramData\Microsoft\Windows\WER\ReportArchive`.
- **Stop:** `./Stop-TerrierServer.ps1` handles the supervisor first, then the server, so a deliberate stop is never restarted:
  - It first writes `_build/terrier-supervisor.stop`. The supervisor checks for it every second and exits without restarting, and a relaunch that reaches the launcher lock after this point refuses to launch.
  - It then takes the launcher lock (`Local\TerrierLauncher8767`, waiting up to 120 s). This waits out a launch already under way, so the server it stops is the one recorded last.
  - It waits up to 40 s for the recorded supervisor to exit. It force-stops the supervisor only if that same process is still running after the wait. The identity check is case-insensitive and tolerates a missing command line.
  - It then stops the server exactly as before, with the same identity check.
  - A damaged supervisor record or any other supervisor problem only produces a warning; the server is still stopped.
  - It also works when no supervisor is running (old-style starts, `-NoSupervisor`). The flag stays until the next supervised start removes it.
- **The live server runs under these scripts** since the 2026-10-03 21:15 restart on `f7d9272` (server PID 25184, supervisor PID 19412; see Status), with the fault log and log rotation active.
  - Running `./Start-Terrier.ps1 -NoBrowser` or clicking the shortcut while an unsupervised server runs attaches a supervisor without restarting it; a fresh restart is needed for the fault log: `./Stop-TerrierServer.ps1; ./Start-Terrier.ps1 -NoBrowser`.
- **Testing the launch scripts:** they hard-code port 8767, `_build` and `.local`. Never run them, or `tools/Test-TerrierDesktop.ps1`, in the repo (the desktop test stops and restarts the live server). Use a temp copy instead:
  - Copy the scripts, `manage.py`, `server/`, `tools/`, `frontend/dist` and `assets/`.
  - Replace `8767` with `8769`, and give the absolute venv python path.
  - Build the copy's `.local` with `tools/make_walk_world.py`. The launch readiness check accepts only a `verified` w1 status, so relabel the world's `fixture` status in the copy only.

## Testing hygiene

- Start synthetic servers with `export CU_DATA_DIR=... CU_DEMO=0` in the same shell as the server. One server started without the variable served the real `.local` data. `verify-walk-list.mjs` refuses to run against a data set that large (its guard caught it).
- On Windows two servers can bind the same port and the older one takes the connections. Check `netstat` before starting, and stop every server you start (subagent-started servers have been left orphaned more than once).
- Never use port 8767 for tests.
