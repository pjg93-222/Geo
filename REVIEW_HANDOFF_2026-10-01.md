# CU Geography v3 — review handoff

> Historical review. Superseded for the completed synthetic preview by `HANDOFF_2026-10-01_COMPLETED.md` following the user's clarification that this is test-dummy work.

Date: October 1, 2026 (America/New_York)

## Status

**Partial review completed; application completion is not certified.**

The requested source handoff was located at:
`C:\Users\pgoff\Documents\Codex\2026-09-27\we-are-going-to-have-an\outputs\cu-geography-v3\HANDOFF_2026-10-01.md`.

It is outside the chat's initial Melange Systems working directory. All work in this pass was confined to the isolated v3 project. The original handoff is preserved.

The existing specification includes inferred individual political lean, identified person profiles, and household political summaries. Completion or validation of those sensitive profiling features was not undertaken. A continuation can instead implement a wholly synthetic demonstration or an aggregate analysis that does not infer or expose individuals' political beliefs. Demo names alone do not make the existing real scores synthetic.

## Verified this pass

Commands were run from `cu-geography-v3\frontend`:

| Check | Result | Scope |
|---|---|---|
| `npm run build` | PASS, exit 0 | TypeScript `tsc --noEmit`, then Vite production build; 1,605 modules transformed |
| `node --test tests/v3/copy.test.ts` | PASS, 3 tests | Existing static copy checks only |

The build regenerated `frontend\dist`. No application source files, score stores, bundles, model artifacts, or live-root files were edited. No server was started, stopped, or swapped. No Higgsfield generation was submitted. No individual records were opened or exported.

Build success does not establish runtime correctness, visual fidelity, data validity, or release readiness.

## Findings

1. **The copy tests conflict with the requested edits.** `frontend/tests/v3/copy.test.ts` explicitly requires `Standing across neighbouring`, while the source handoff requests US spelling. The same test requires the old Withheld threshold wording. Passing these tests therefore does not establish conformance with the latest requested copy.
2. **US spelling changes remain outstanding.** Source inspection found `Centre` in `lib/format.ts`, `components/MapParts.tsx`, and `components/PeopleScreen.tsx`; `neighbouring` in `components/AreaRail.tsx`; `colour` in visible method copy; and `Charitable-organisation volunteer` in `api/fields.ts`. Comment-only occurrences should be distinguished from visible text.
3. **District fallback labels remain inconsistent.** `components/PeopleScreen.tsx` contains two `District 5` fallbacks, rather than the handoff's `Congressional District 5`.
4. **The font status is stale.** Both Geist WOFF2 files already exist in `frontend/src/v3/fonts`, and the production build includes them. This confirms inclusion, not a decision about font licensing or user approval.
5. **Frontend closeout documentation remains absent.** `_build/FRONTEND.md` was not found during this review.
6. **Visual and performance checks remain unverified.** No screenshot comparison, interaction testing, or latency measurement was performed. The MapLibre production chunk is approximately 1,024 kB before gzip (280 kB gzip); this is an observation, not a measured performance failure.

## Not completed or certified

- Completion of the six approved screen designs.
- Person-level or household political profiling implementation and validation.
- Wiring the no-floor area table or rebuilding the real score database.
- Verification of 200 real profiles or real-data area reconciliation.
- Model-specific Sonnet/Opus review and the independent P1 verification.
- Three-round visual fidelity review, runtime error checks, or latency comparison.
- Live-app replacement on port 8765.
- Future election-outcome checks, ratification decisions, or any other deferred review.

Prior handoff and backend claims remain historical claims unless explicitly verified above. Server PIDs and running-state claims were not rechecked.

## Safe continuation options

- Finish and visually test the six screens using fully synthetic people, attributes, scores, households, and locations.
- Revise the product to aggregate geographic reporting with adequate group sizes and no individual political inference or drill-down to sensitive personal profiles.
- Continue general documentation and privacy review without developing the sensitive profiling functions.

Preserve the existing live app and original data while selecting and implementing a revised scope. Do not treat this review as a completed release receipt.
