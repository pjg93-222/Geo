# Terrier

Local geography and profile application using the existing source records and stored model outputs, with the Terrier loading animation and Mentat palette.

The persistent navigation includes Map, People, Contact sheet, Criteria audit, Data & sources, Team & snapshots, and Method. Contacts support search, filters, profile/map links and matching CSV export. Team & snapshots stores local saved views and exports the active snapshot summary.

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
