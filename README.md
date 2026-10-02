# Terrier

Local synthetic geography and profile demo with the Terrier loading animation and Mentat palette.

The persistent navigation includes Map, People, Contact sheet, Criteria audit, Data & sources, Team & snapshots, and Method. Contacts support search, filters, profile/map links and matching CSV export. Team & snapshots stores local saved views and exports the bundled snapshot summary; team accounts and imports require the separate full backend.

People opens on the full generated population. The optional **Balanced by registration** switch selects a 100-person sample and is preserved in saved URLs. Profiles separate registration, self-described affiliation and political outlook from scored indices. These are synthetic responses; differences among those categories can be valid. The fixture audit did not audit or modify original imported person records.

## Desktop app

```powershell
./Install-TerrierDesktop.ps1
```

This rebuilds the interface, generates the Windows icon from the lossless logo, and creates **Terrier** on the Desktop. The existing Credit Union Geography shortcut is archived under `_build/desktop-backup/`. Open Terrier to launch its own Chrome/Edge app window, titled exactly **Terrier**, with an isolated browser profile. `Start.cmd` opens the same app.

The launcher starts or reuses only this project's verified loopback server on port 8767. It refuses an unrelated port owner. `./Start-Terrier.ps1 -NoBrowser` checks startup without opening a window. Rebuild the desktop app with the install command above; use `-SkipBuild` only when the frontend is already built.

## Run the preview

Use Node.js 24 or newer and Google Chrome for browser verification.

```powershell
npm --prefix frontend ci
npm --prefix frontend run build
node frontend/scripts/compress-demo.mjs
./Start-test-demo.ps1
```

Open http://127.0.0.1:8767/. Stop with `./Stop-test-demo.ps1`.

The startup animation plays once, then opens the application automatically. Reduced motion skips animation. The fixture-only adapter and static server do not query a live backend. All person data are synthetic; public geographic geometry is included in `frontend/public/fixtures/v3`.

## Verify

With the preview running:

```powershell
npm --prefix frontend run test:unit
node frontend/scripts/verify-test-fixtures.mjs
node frontend/scripts/verify-terrier.mjs
node frontend/scripts/verify-terrier-dart.mjs
node frontend/scripts/verify-test-demo.mjs --no-screenshots
node frontend/scripts/verify-debug.mjs
node frontend/scripts/verify-contacts.mjs
node frontend/scripts/verify-contacts-ui.mjs
node frontend/scripts/verify-workspace.mjs
node frontend/scripts/verify-restored-navigation.mjs
node frontend/scripts/verify-ui-refinements.mjs
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/Test-TerrierDesktop.ps1
```

Generated receipts go to `_build/` and are not versioned. Browser checks use installed Google Chrome through Playwright. The build and source fixtures are sufficient to run this preview; local score databases and imported data bundles are deliberately excluded from Git. Fixture regeneration tools depend on separate local geographic inputs and are not needed to use the committed fixtures.

## Handoffs and scope

- [Current Terrier handoff](TERRIER_HANDOFF_2026-10-01.md)
- [Synthetic data coherence audit](DATA_AUDIT_2026-10-01.md)
- [Debug and commit receipt](DEBUG_2026-10-01.md)
- [Completed synthetic UI handoff](HANDOFF_2026-10-01_COMPLETED.md)

The retained Python backend and `Start.ps1`/`Start-v3.ps1` predate this standalone app. They are not used or certified by these checks and require separate local data. Ports 8765/8766 and their databases are outside this change. Political views display original categories and do not contribute to rankings; internal legacy numerical fields remain documented in the handoff.
