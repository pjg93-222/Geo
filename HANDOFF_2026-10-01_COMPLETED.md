# CU Geography v3 — completed test-dummy preview

Date: October 1, 2026 (America/New_York)

## Result

The **standalone test-dummy preview is complete and running at http://127.0.0.1:8767/**.

This handoff supersedes `REVIEW_HANDOFF_2026-10-01.md` for the synthetic preview requested in the follow-up. The original `HANDOFF_2026-10-01.md` remains historical context. Completion here covers the test-dummy UI and its generated fixtures, not a live-data deployment.

Project directory:
`C:\Users\pgoff\Documents\Codex\2026-09-27\we-are-going-to-have-an\outputs\cu-geography-v3`

The live application under `outputs\cu-geography` and its server on port 8765 were not changed. No score database, contact database, real-profile parquet, or live area CSV was rebuilt or edited.

## Completed

- Map overview with all four geographic layers, index controls, analytic illustrative ranges, selected-area rails, zoom controls, and timing readout.
- Person profile with all 40 synthetic fields, four expandable evidence groups, component grades, household navigation, and area context.
- Ranked list with 50-row pagination, filters, next-page prefetch, 100-row balanced view, and restoration of the current page when returning from a profile.
- Area detail with selected outline, registration mix, and neighboring-area comparison.
- Street view with synthetic household markers, bounded popovers, close control, profile links, histogram, and a notice when the displayed household set is capped.
- Loading skeletons, preserved content during layer changes, metadata retry, unknown-profile recovery, and a render-error fallback.
- Responsive layouts for desktop and narrow screens, horizontal scrolling for wide analytical tables, and visible scrolling guidance.
- Keyboard-operable search with arrow navigation, Enter selection, Escape dismissal, loading status, and error copy.
- US spelling, consistent `Congressional District 5` and `ZIP` labels, singular block/household/person formatting where applicable, and removal of the obsolete Withheld legend.

The existing Higgsfield renders were used as the visual references. No new Higgsfield generation or credit spend was required.

## Test data and isolation

The frontend adapter is fixture-only. It cannot opt into a live source through `?source=live` or `?demo=0`, and its preview server has no API proxy or database access. The visible `Test-dummy preview` label and Method page explain the data.

- People: **109,150**, deterministically generated in **45,000** households.
- Names, ages, scores, grades, field values, households, and household marker positions are synthetic.
- Public geographic polygons and Census road lines provide map context. They are not synthetic geography.
- Synthetic precincts use a spatial partition; they are not authoritative electoral precinct boundaries.
- Synthetic area statistics are independently generated illustrative values. They are not calculated from the individual fixture profiles.
- Block, precinct, and tract population/household totals reconcile exactly to the dummy population. ZIP geometry covers only blocks assigned to its available polygons, so ZIP coverage is partial.
- Overall area means use the 40/35/25 composition. District-wide synthetic top-decile shares normalize to 10%.

| Synthetic layer | Areas | Minimum people | Withheld |
|---|---:|---:|---:|
| Block | 9,103 | 1 | 0 |
| Precinct | 326 | 161 | 0 |
| Tract | 181 | 3 | 0 |
| ZIP | 43 | 7 | 0 |

These counts come from the standalone fixture geometry and differ from the original live-data handoff. The no-floor behavior is implemented for these generated fixtures; the original no-floor CSV and score store remain untouched.

## Verification

| Check | Result |
|---|---|
| TypeScript and Vite production build | PASS |
| Existing formatting/copy tests, updated for current copy | 16 passed |
| Deterministic synthetic profiles | 200 checked |
| Profile fields | 8,000 checked; bounds and contribution sums pass |
| Composite and household reconciliation | PASS; overall grade never exceeds the lowest component grade |
| Invalid synthetic person/household IDs | Rejected |
| List totals, reversible paging, balanced view | PASS |
| Four no-floor area layers and polygon coverage | PASS |
| Browser interactions and recovery | PASS |
| Responsive checks | 1920×1080, 1440×900, 390×844 |
| Final browser JavaScript errors | 0 |
| Final browser console errors | 0 |
| Live API requests during browser suite | 0 |
| Start/stop scripts | Tested; loopback listener confirmed |

Final browser coverage includes list paging, profile-back page restoration, expandable evidence, keyboard area selection, household popover and close, forced fixture mode, no-floor legend, mobile page-width checks, invalid profile recovery, failed metadata retry, and delayed-loading skeletons.

The final functional rerun fixed a missing favicon request found in the second visual pass. The earlier `browser-checks.json` retains that failure as history; **`browser-functional-final.json` is the final browser verdict**.

Evidence in `_build\test-dummy-verification\`:

- `build.log`, `unit-tests.log`
- `fixture-checks.json`
- `browser-functional-final.json`
- `browser-checks.json` (second visual pass, before favicon response fix)
- Thirteen PNG screenshots covering the six screen/state families and responsive variants
- `impeccable-detection.json`
- `source-diff-summary.json`

The Impeccable scan reported only two font-popularity warnings, for Geist and Geist Mono. These are retained because the approved design specifies those fonts. Codex performed this review; no Sonnet/Opus-specific signoff is claimed.

## Performance observations

Final browser samples: list 559 ms, profile 154 ms, map rail available 964 ms, Method 91 ms. These are measured navigation-to-visible-selector times from this machine, not percentiles or full map-idle benchmarks. The visual pass showed approximately 1.1–1.3 seconds for the map's own ready indicator.

The 28 compressible production files total 23,917,755 bytes raw and 4,855,611 bytes gzip. The local static server serves the precomputed gzip files.

The earlier baseline used a different dataset, API stack, and machine load. Its repeated map-idle median was 1,441 ms, but the new selector timings are **not an equivalent benchmark**, so no speedup claim is made.

## Run and verify

From the project directory, in PowerShell:

```powershell
.\Start-test-demo.ps1
.\Stop-test-demo.ps1

npm --prefix frontend run build
node frontend\scripts\compress-demo.mjs
npm --prefix frontend run test:unit
node frontend\scripts\verify-test-fixtures.mjs
node frontend\scripts\verify-test-demo.mjs --no-screenshots
```

Use `node frontend\scripts\verify-test-demo.mjs` to regenerate the screenshot set. Browser verification requires the installed Chrome and existing Playwright dependency. The preview itself needs only Node.js and the built `frontend\dist` directory.

Process identity is recorded in `_build\test-demo-pid.json`. Logs are `_build\test-demo.out.log` and `_build\test-demo.err.log`. The stop script validates process name, script path, and creation time before stopping it. It never targets ports 8765 or 8766.

`tools\make_v3_fixtures.py` regenerates fixture geography and illustrative area values using the existing shared Python venv, public geometry JSON, and public-road SQLite store. It does not read person/score/contact stores. Normal preview startup does not run this generator.

## Files and recovery

- Active UI: `frontend\src\v3\`
- Additional responsive styling: `frontend\src\v3\styles\completion.css`
- Data adapter: `frontend\src\v3\api\client.ts`
- Synthetic person generator: `frontend\src\v3\api\fixtures.ts`
- Static fixture data: `frontend\public\fixtures\v3\`
- Standalone server and checks: `frontend\scripts\`
- Frontend closeout: `_build\FRONTEND.md`
- Pre-change source/tests/config backup: `_build\before-test-dummy-completion\`

The backup covers the prior source, tests, Vite configuration, package manifest, and fixture generator. It is not a database or old-build backup.

## Limits and deliberate differences

- The approved design's composition, typography, colors, and range treatments are retained, with updated copy, the requested no-floor legend change, and a small test-preview label.
- Public geometry replaces invented render geometry. The local map has road context but no photographic terrain/hillshade, water, parks, or building-footprint layer. Pixel-identical reproduction of the illustrated basemap is not claimed.
- The wide evidence and ranking tables scroll horizontally on phones; desktop remains the primary analytical layout.
- Geist files were already present and are used locally. Their existing provenance note remains in `frontend\src\v3\fonts\README.txt`; external distribution was not performed.
- No live-app swap, database deployment, external publication, model-validation signoff, or future election check is part of this completion.

## Historical follow-up: preserve the absolute partisan-lean scale

The subsequent Terrier handoff supersedes the numeric partisan displays described below: the active interface now shows separate opinion categories and registration proportions.

The user clarified that partisan lean should have little standardization. The synthetic **area** generator had z-scored its spatial lean signal and added a fixed +4 offset; both have now been removed. Individual fixture lean values already used a fixed scale without population standardization and were unchanged.

Current behavior:

- No cohort mean subtraction, division by cohort standard deviation, percentile conversion, or forced party balance in the lean values.
- The synthetic spatial signal retains its native mean and spread. The existing fixed conversion to lean units and random fixture noise remain; values are bounded to −100…+100.
- Area lean is a population-weighted mean, with no additional shrinkage or recentering. Fixed ±25/±75 display bands remain.
- The balanced-list toggle changes which dummy profiles are displayed; it never changes their lean values.
- Area captions no longer describe partisan lean as “shrunk.” Method copy states the absolute-scale behavior.

Verification: production build and all 16 unit tests passed again. Every generated area lean is finite and within bounds. Precinct and tract means reconcile to their synthetic block-weighted means within 0.007 points (two-decimal serialization). Evidence: `_build/test-dummy-verification/lean-before.json` and `lean-after.json`.

Earlier screenshots and timing samples document the layout before this follow-up; they are not the updated lean-value receipt. No individual fixture engine, live-data store, or live app was changed in this follow-up.

## Terrier follow-up

See [TERRIER_HANDOFF_2026-10-01.md](TERRIER_HANDOFF_2026-10-01.md) for the approved logo, Mentat palette, Higgsfield walking/sniffing animation with the brief neck-height gold dart, categorical political views, independent scoring review, layout observations, and final verification (20 unit tests). This later record supersedes the earlier numerical opinion displays and the statement that the individual fixture engine was unchanged.
