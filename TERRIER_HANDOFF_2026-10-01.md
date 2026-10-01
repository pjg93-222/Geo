# Terrier — completed handoff

Completed 2026-10-01 in the isolated synthetic preview. This current receipt includes the caption-free automatic startup and source-resolution media follow-up; it supersedes earlier preview-control, CSS-animation, and numerical partisan-display descriptions.

- App: http://127.0.0.1:8767/
- Motion preview: http://127.0.0.1:8767/brand/preview.html
- Earlier work: `HANDOFF_2026-10-01_COMPLETED.md`
- Project: `C:/Users/pgoff/Documents/Codex/2026-09-27/we-are-going-to-have-an/outputs/cu-geography-v3`

## Delivered identity and motion

The app is **Terrier**. The user-approved Higgsfield adaptation of `C:/Users/pgoff/Downloads/images.jpg` supplies its icon, header identity, and favicon. Palette from `C:/Users/pgoff/Mentat/mentat/app/acu-theme.css`: plum `#342649`, ivory `#F7EDE4`, paper `#FCF7F4`, purple `#57447B`, teal `#327A7A`, gold `#ECA91C`. Core UI tokens match; map categories retain distinct semantic colors.

The loader uses an actual Higgsfield-generated animation: the dog walks while sniffing, then turns left into its alert logo pose. This replaces the clipped-head CSS approximation. A short gold dart passes right-to-left at the standing dog's neck height immediately before the reaction. The final dart is a precise overlay produced in Higgsfield's media sandbox, rather than relying on the generative edit's timing.

- Current video: `frontend/public/brand/terrier-loading-hq.mp4`, also copied to `frontend/dist/brand/`. H.264, native 960 × 960, 24 fps, silent, 6.042 seconds, 557,821 bytes. Encoded once from the original generated source at CRF 16; the small compressed export was not enlarged.
- Overlay: nominal 2.667–2.887 seconds; 280 × 45 px tapered sprite; top y=357.5; x=`960-(t-2.667)*5637.5`. Decoded gold pixels occupy y=371–385. Full-frame scan confirms one right-to-left pass lasting at most 0.25 seconds.
- Plays once on document entry while the app prepares behind a noninteractive overlay. Opens automatically after animation completion and metadata readiness. If metadata is still pending, holds the still. Subsequent route/map loading uses static marks and never restarts the animation. Internal navigation does not replay startup.
- Reduced motion skips the intro and opens when metadata is ready. Media failure releases the intro; a ten-second media watchdog also prevents a stalled player from blocking startup. Metadata failure immediately exposes Retry. Loading status retains an accessible label with no visible captions.
- Removed captions, heading, Open link, and playback controls. The old preview URL automatically enters the app root. Direct app deep links retain their path/query. A static HTML mark covers bundle startup. Only one video plays at entry; headers and other loading states use stills.
- Local static server now supports video byte ranges (206) and invalid ranges (416), enabling correct seeking.
- Active still: `frontend/public/brand/terrier-icon-hq.png`; transparent export: `terrier-mark-hq.png`. Both are lossless 1,024 × 1,024 PNGs exported from the original approved image, preserving antialiased edges and exact palette interiors. The splash displays at 280 CSS px (240 on 3.5×+ displays); native video pixels cover DPR2 and DPR3 without upscaling. No SVG master.

Integration: `TerrierMark.tsx`, `Header.tsx`, `MapScreen.tsx`, `main.tsx`, `frontend/index.html`, the files under `frontend/public/brand/`, and `frontend/scripts/test-demo-server.mjs`.

### Higgsfield provenance

- Workspace: `1c6eef8f-17d0-4e93-b043-557d6b6ca2e0`
- Project/folder: `64306bc9-c5ca-44cd-8dea-e32d36568209` — Terrier — Mentat identity
- Reference upload: `dd5067a3-cf9b-4c53-9714-1afda0ef267d`
- Approved still: `6d7139cd-28d6-44bc-adac-7d3289671f50` (GPT Image 2.5)
- Walking/sniffing/turning source: `b983bd35-8ee8-4126-8caa-9e81fcf8c371` (Seedance 2.5)
- Superseded gold edits: `1bb5b97d-f1bc-4570-88a4-eddaab146430` (too low), `20c70dee-deed-4775-b74a-de1a2493ae9c` (raised but slow and late).
- Current HQ video media: `22a28e18-ac85-4498-80ed-ea94b360852c`
- HQ icon/transparent mark: `d0e7ef0c-468c-420d-b7c0-ba45e35f384a`, `d38af176-2c77-46a3-9333-6772de1d5c79`
- Prior compact video media: `63e85450-5fc8-4503-8f35-93c387111bf2` (superseded)
- Final video: https://d2ol7oe51mr4n9.cloudfront.net/user_3J1aVESwhbXLraSW8PtRFWWgpV2/22a28e18-ac85-4498-80ed-ea94b360852c.mp4

The final video combines the original generated dog clip with the precise gold overlay. Intermediate media are not the delivered animation. The old compact files remain unused local variants. Confirmed media downloaded through Chrome; lossless PNGs were transferred in a confirmed ZIP because direct image CDN downloads reset. The ZIP remains at `_build/terrier-hq-logos.zip`.

## Political views and standardization

Political views are categorical opinions, not a standardized score. The active interface preserves separate registration, affiliation, and outlook responses, including disagreements between them.

| Surface | Current behavior |
| --- | --- |
| Profile | Registration, affiliation, and outlook rows; no political score, grade, percentile, or contribution bar |
| Raw opinion evidence | Original categorical Yes/No responses; marked Not scored |
| People | Outlook filter and text labels from original response categories |
| Registration map | Largest registration category from proportions; Mixed means a tie |
| Area details | Registration proportions; no signed partisan composite |
| Overall standing/rankings | Opinion categories do not contribute |
| Missing/conflicting categories | Unknown or Multiple responses; never silently converted to a middle position |
| Ethical beliefs | No ethical-belief fields or scores exist in this active preview |
| Other indices | Existing alignment, participation, influence, and overall transformations/percentiles remain |

The generator no longer back-fits opinion exposures with `scaleTo`; area lean z-scoring and offsets remain removed. The complete dummy opinion fixtures supply observed responses. Missing/conflicting behavior is covered by helper tests, not represented as naturally occurring fixture missingness.

**Internal limitation:** legacy numerical lean calculations and wire keys remain for compatibility. They are not used for displayed opinions, Outlook filtering, registration colors, or rankings. Their presence is not a validated belief measure. Removing unused compatibility fields entirely remains future data-model cleanup.

Area statistics are independently generated fixtures, not summaries of the displayed individual profiles. Method copy explicitly states this. All profiles remain test dummies, not evidence about real residents.

Key changes: `frontend/src/v3/lib/opinions.ts`, `lib/areas.ts`, `fixtures.ts`, `ProfileScreen.tsx`, `PeopleScreen.tsx`, `AreaRail.tsx`, `MapParts.tsx`, `MapScreen.tsx`, `MethodScreen.tsx`, and `frontend/tests/v3/opinions.test.ts`.

## Independent scoring review

The user requested another agent. `/root/scoring_review` inspected the changes read-only, found three issues, then re-reviewed the corrections:

1. The fixed composite collapsed conflicting opinions into Center. Example `p_000006l0`: Democrat registration, Republican affiliation, Very conservative outlook. Corrected by displaying/filtering separate categories and removing the composite from map colors.
2. Unknown-response claims lacked coverage. Added missing/conflicting response tests and clarified the complete fixtures.
3. Method copy implied area means summarized displayed people. Corrected to disclose independent generation.

Final reviewer result: **no remaining actionable scoring findings in the active synthetic preview**. The reviewer verified all five Outlook categories partition 109,150 profiles, sampled labels against responses, checked registration proportions across four layers, and confirmed changing legacy area lean does not change registration bins. The review acknowledged the remaining internal compatibility calculations.

## Layout observations

- Retain the desktop map and contextual detail panel to preserve geographic orientation.
- The political card now uses categorical rows and explicitly says it is not part of overall standing. It no longer presents opinions as a performance number.
- Compact expandable people rows would improve mobile scanning. Current wide evidence/ranking tables remain horizontally scrollable.
- Header simplification and consistent map/people navigation remain potential refinements, not completed redesign work.

## Verification

- Production TypeScript/Vite build and static compression passed after the automatic startup changes. Current HQ assets are in both public and dist.
- Unit suite: **20/20 passed**, including four new opinion/category tests.
- Fixtures: 200 deterministic profiles and 8,000 fields passed, including categorical opinions, no back-fitting, and invariance after cohort/balanced-list loading.
- Browser functional suite passed with zero JavaScript errors, console errors, or live API requests.
- Additional browser checks verified the conflicting-opinion example, original Outlook filtering, registration legend/proportions, and mobile width.
- Visual inspection: desktop/mobile categorical profiles, registration map, loader, walking/sniffing/turn/alert frames, and final gold frame.
- Current startup browser checks passed: automatic entry from the former preview, no visible captions/controls, one player, inert app during startup, natural-end automatic reveal, DPR2/DPR3 native resolution, deep-link preservation, reduced motion, slow metadata, slow route chunks without replay, media fallback, and metadata-error Retry. Zero unexpected browser errors. Desktop and mobile screenshots inspected in one batch.
- Full-frame video scan confirmed the final dart's direction, duration, and vertical position.

Current evidence:

- `_build/terrier-startup-verification/checks.json`, `desktop-2x.png`, `mobile-3x.png` (current automatic startup)
- `_build/terrier-video-verification/gold-frame-scan.json` and `gold-flash.png`
- `_build/terrier-video-verification/categorical-browser-checks.json` and categorical profile/map PNGs
- `_build/test-dummy-verification/fixture-checks.json`
- `_build/test-dummy-verification/browser-functional-final.json`

Earlier `terrier-verification` and `terrier-video-verification/checks.json` captures document the old preview controls/compact media. Current startup evidence is under `terrier-startup-verification`; `gold-frame-scan.json` was rerun against the HQ video.

```powershell
npm --prefix frontend run build
node frontend/scripts/compress-demo.mjs
npm --prefix frontend run test:unit
node frontend/scripts/verify-test-fixtures.mjs
node frontend/scripts/verify-terrier.mjs
node frontend/scripts/verify-terrier-dart.mjs
node frontend/scripts/verify-test-demo.mjs --no-screenshots
```

`Start-test-demo.ps1` / `Stop-test-demo.ps1` manage this loopback preview. Original live backend, databases, and ports 8765/8766 were not modified. No hosted deployment, Git commit, or publication was performed.


## Debug and Git follow-up

See [DEBUG_2026-10-01.md](DEBUG_2026-10-01.md) for the debug pass and initial local repository commit. Fixed map/People browser-navigation synchronization, handled speculative chunk failures, and made the preview launcher create its own log directory. The previous statement that no Git commit was performed describes the earlier delivery; the requested local commit follows this debug pass.
