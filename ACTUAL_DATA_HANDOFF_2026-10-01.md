# Terrier — actual data connection

This release replaces the active generated preview with the existing local dataset. The user explicitly requested inclusion of both supplied records and all existing model outputs, without additional modeled-data badges. This supersedes the earlier synthetic-preview descriptions.

## Connected records

- Source: `statara_co_cd005_20260904.csv`, dated September 4, 2026.
- Source SHA256: `4c48800b8c21870e6e2b6242810bbbe917d8169e529b440efb662f71c464239f`.
- Active snapshot: `co-cd005-6cf898496ca2`.
- Existing V3 stores: `.local/scores.sqlite3`, `contacts.sqlite3`, `street.sqlite3`, `roads.sqlite3`, and the active snapshot in `.local/snapshots`.
- **491,308 people and 343,000 scored households.** Contacts include 359,486 email records, 146,938 phone records and 349,132 wireless records; 65,987 have no contact channel.
- Street coverage is a subset: 436,057 registration people at 299,800 validated household locations. No point is invented for a missing location.

The source crosswalk was verified for all 491,308 rows, with zero matches in the shifted-row control. Contacts, scores and street metadata agree on the source checksum. Reads use SQLite read-only connections. SHA256 checks before and after reconciliation confirm those three data stores were not changed. No source records, model artifacts or predictions were retrained, regenerated or corrected to match assumptions.

## Application behavior

All seven Terrier sections now use the local APIs. Production entrypoints no longer import the fixture generators. An unavailable service or mismatched source produces an error and retry action; it never falls back to generated people, contacts, scores, geography or responses.

People opens on the full stored population. The optional registration-balanced view targets 100 and includes ties. Person scores, ranges and evidence values come from the existing score store, including its model outputs. Contact search, channel/address filters, dialogs, profile/map links and matching CSV export use the actual contact registry. Criteria and snapshot/source pages use the stored field catalog and metadata. Saved views remain local to the browser.

Political registration, affiliation and outlook are separate categories. The visible outlook and its filters read stored categorical flags, not the legacy weighted lean score. Exactly one positive outlook displays its category; no selected flag displays **Unknown**; more than one displays **Multiple responses**. Current stored counts include 245,400 without a selected outlook and 74,822 with multiple positive flags. These are preserved, not forced to a single belief or treated as evidence that one political combination is invalid. Political fields have no displayed score contribution and are excluded from overall standing. Existing legacy numeric lean fields remain untouched in storage.

Map statistics and boundaries come from the active snapshot. Its existing minimums are 20 people and 10 households; withheld areas remain withheld. The registration map and district/area panels use the recorded registration proportions. Household roster membership means people present in this dataset and may omit residents; supplied household facts are not rewritten to agree with the roster.

## Desktop and startup

- App and shortcut remain **Terrier**, at `http://127.0.0.1:8767/`.
- `Start-Terrier.ps1` calls `Start-TerrierServer.ps1`, which starts Django/Waitress with the existing Python runtime and `CU_DEMO=0`.
- The launcher checks the stored dataset and records the actual listening Python process, including Windows virtualenv child processes. It reuses only its verified server, migrates only the recorded former synthetic server, and refuses unrelated port owners.
- Stop with `Stop-TerrierServer.ps1`; regenerate the desktop app with `Install-TerrierDesktop.ps1` (or `-SkipBuild` after a verified build).
- `/_terrier/health` identifies mode `recorded`, version 2, and the active record counts. Cookies use the `cu_terrier_` prefix.
- The original logo and 960px loading video remain unchanged. Django now supports MP4 byte ranges, preserving seeking and the existing automatic startup behavior.
- The sibling application and processes on 8765/8766 were not replaced. No data was uploaded or published.

## Verification

- Production TypeScript/Vite build and compression passed; **30 frontend unit tests and 51 backend tests passed**.
- Reconciled all 491,308 evidence records, 404 profiles / 16,160 fields, all categorical outlook filter totals, 229,704 area values, and 50 real-mode name joins against the existing stores. Database checksums remained unchanged.
- Actual-data browser checks passed all seven routes, full population and filters, profile/contact joins, one-record CSV export, criteria/source metadata, bookmarks/snapshot export, map registration/withholding, mobile widths, and error recovery. **99 API requests, zero fixture requests, zero page errors.** The receipt contains only aggregate results; personal records and screenshots were not persisted.
- Loading checks passed automatic entry, native resolution at DPR2/DPR3, reduced motion, deep links, slow metadata/routes and media/error recovery.
- Desktop checks cover cold launch, warm process reuse, icon/shortcut identity, unrelated-port refusal and service restoration.

Receipts are local, ignored build artifacts: `_build/backend_actual_data_verification.json`, `_build/actual-data-verification/checks.json`, `_build/terrier-startup-verification/checks.json`, and `_build/desktop-verification/checks.json`. Backend reconciliation masks display names for most checks while using the actual stored data; a separate real-mode join check compares names internally without printing them.

This validates the application connection and faithful display of the stored data. It does not establish the original vendor data's accuracy or independently validate the predictive accuracy of the existing models.

Release branch: `codex/terrier-actual-data`, merged into local `main`. No Git remote is configured. Source databases and private data bundles remain excluded from Git.
