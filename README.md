# Terrier

Local geography and profile application using the existing source records and stored model outputs, with the Terrier loading animation and Mentat palette.

The persistent navigation includes Map, People, Contact sheet, Criteria audit, Data & sources, Team & snapshots, Method, and Walk list. Contacts support search, filters, profile/map links and matching CSV export. Team & snapshots stores local saved views and exports the active snapshot summary.

People opens on the full stored population: **491,308 people in 343,000 scored households**. The optional **Balanced by registration** view targets 100 people, including ties, and is preserved in saved URLs. Profiles separate registration, affiliation and political outlook from scored indices. Political categories come from stored field values; unknown and multiple responses are preserved.

## Desktop app

```powershell
./Install-TerrierDesktop.ps1
```

This rebuilds the interface, generates the Windows icon from the lossless logo, and creates **Terrier** on the Desktop. The existing Credit Union Geography shortcut is archived under `_build/desktop-backup/`. Open Terrier to launch its own Chrome/Edge app window, titled exactly **Terrier**, with an isolated browser profile. `Start.cmd` opens the same app.

The launcher starts or reuses only this project's verified Django/Waitress server on loopback port 8767. It refuses an unrelated port owner and migrates only its own recorded former synthetic server. `./Start-Terrier.ps1 -NoBrowser` checks startup without opening a window. Rebuild the desktop app with the install command above; use `-SkipBuild` only when the frontend is already built.

## Run the application

Use the existing Python application runtime, Node.js 24 or newer for builds, and Google Chrome for browser verification. The private `.local` stores and active snapshot must be present. The launcher stops with an error when they are unavailable; it never substitutes generated records.

```powershell
npm --prefix frontend ci
npm --prefix frontend run build
node frontend/scripts/compress-demo.mjs
./Start-Terrier.ps1
```

Open http://127.0.0.1:8767/. Stop with `./Stop-TerrierServer.ps1`.

The startup animation plays once, then opens the application automatically. Reduced motion skips animation. All active frontend adapters read the local APIs. Profiles, contacts and household locations use read-only stores with matching source checksums. Existing source values, model estimates, scores and uncertainty are included without retraining or rewriting the data. Area availability follows the active snapshot. Historical fixture generators remain for offline regression reference and are not imported by the production app.

## Walk list

The **Walk list** tab (`/walk`) turns a set of streets into a door-knocking route you can print. Choose an area (state senate district, state house district or precinct), add streets by search or from a CSV, and Terrier orders the street sides into one walking route: doors in walking order with the voters at each door, a map of the route, the walking distance and an estimated time (2 minutes per door plus walking at 80 metres per minute). A list holds up to 40 streets and 1,500 voters. A saved list keeps its route, results can be recorded for its voters, and **Not home again** builds a new list over only the doors whose voters were marked NH (not home). The walk endpoints answer on loopback only.

**Street CSV.** The first row is a header. `street` is required; `side`, `city` and `zip` are optional columns.

```csv
street,side,city,zip
Maple Avenue,even,,
Oak Street,both,Sample City,80000
Elm Court,,,
```

- `street`: the street name as written on a mailing label. Case, punctuation, directionals and suffix abbreviations (`North` or `N`, `Drive` or `Dr`) are normalized before matching.
- `side`: `even`, `odd` or `both`. A blank value means both sides.
- `city`, `zip`: used to choose between streets that share a name. Leave them blank when the name is unique in the area.
- A file can have up to 500 street rows and 1 MB.

Each row is matched against the streets in the chosen area. A row with one match is added, a row that matches streets in several places asks you to pick one, and a row with no match shows the closest spellings.

**Build the walk store.** The tab reads `.local/walk.sqlite3`, built once from the source voter CSV and the existing local stores. Run it with the application's Python runtime, the same interpreter the checks below use:

```powershell
python tools/build_walk.py
```

The build joins each CSV row to the stored person by row order (it first checks that the CSV and the contact store have the same number of rows), builds the walking network from the stored roads, places every address on a street side, and records the source checksums, the election window and the Abs rule in the store's `meta` table. It also creates `.local/.walk-salt` on the first run. That key makes the voter keys that link recorded results across lists, so keep it with the store. `.local/` is excluded from Git like the other stores.

**Printed columns.** Each stop prints a table with Address, Name and Phone, then the voter history (Pri, Gen, Abs), Party, Age, Gender, Reg. date and a Result box.

| Column | Meaning |
| --- | --- |
| Pri | Primaries voted / primaries eligible, over the last six even-year primaries, 2014 to 2024. |
| Gen | Generals voted / generals eligible, over the last six even-year general elections, 2014 to 2024. |
| Abs | Mail ballots cast / all eligible primaries and generals in that window. **Abs = mail ballots**: a vote counts when its method code starts with M or A. |

A voter is eligible for an election when registered on or before its date and 18 or older on that date, or when the record shows a vote in it anyway. Cells read `voted/eligible`, for example `4/6`; a bare `/` means the voter had no eligible election in the window. Each list states its window and the Abs rule.

**Downloads.** **Export PDF** makes the printed list: a cover page with the route map and door script, then each route stop on its own page or pages, with Pri, Gen and Abs as `voted/eligible` and the registration date as `m/d/yy`. **Download CSV** writes one row per voter in walking order, with the result code set on screen. A spreadsheet reads `4/6` as a date, so the CSV says `4 of 6` (`0 of 0` when none were eligible) and writes the registration date as `yyyy-mm-dd`. A value that starts with `=`, `+`, `-` or `@`, even after spaces, gets a leading apostrophe so it stays text. The PDF libraries load only when **Export PDF** is used; after a build, `node frontend/scripts/check-no-eval.mjs --require` checks that their code has no `eval(` or `new Function`.

## Verify

With Terrier running:

```powershell
npm --prefix frontend run test:unit
node frontend/scripts/verify-terrier.mjs
node frontend/scripts/verify-actual-data.mjs
& ..\..\work\.venv\Scripts\python.exe manage.py test server.research.test_v3_people server.research.test_v3_static server.research.test_terrier_contacts
& ..\..\work\.venv\Scripts\python.exe tools/verify_v3_real.py --people 400
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/Test-TerrierDesktop.ps1
```

Generated receipts go to `_build/` and are not versioned. Browser checks use installed Google Chrome through Playwright. The actual-data browser receipt contains only aggregate checks, without personal records or screenshots. Local score/contact databases and imported data bundles are excluded from Git. The desktop check restarts only Terrier's verified server; run it after browser checks finish.

## Handoffs and scope

- [Current Terrier handoff](TERRIER_HANDOFF_2026-10-01.md)
- [Actual data connection](ACTUAL_DATA_HANDOFF_2026-10-01.md)
- [Synthetic data coherence audit](DATA_AUDIT_2026-10-01.md)
- [Debug and commit receipt](DEBUG_2026-10-01.md)
- [Completed synthetic UI handoff](HANDOFF_2026-10-01_COMPLETED.md)

Terrier now uses the retained Python backend and V3's local stores. The sibling application's files and processes on ports 8765/8766 are not replaced. Political views display stored categories and do not contribute to overall standing; legacy numerical lean fields remain stored but are not used for the visible belief labels, filters or map colors. The older synthetic handoffs describe historical releases, not the current data connection.
