# Terrier multi-district build: OR-05 (Bynum) + CT-01 (Bronin)

Build prompt and blueprint, written 2026-10-06, reviewed 2026-10-06 (see "Review 2026-10-06" at the bottom). Paste the block below into a fresh session, or point the session at this file. Do not start Phase 2 until the items in "Open for Paul" are answered or Paul says go on the defaults.

```
Terrier multi-district build: OR-05 (Bynum) + CT-01 (Bronin). Config-first.
Read this file, then the vault note C:\Users\pgoff\Documents\brain\projects\terrier.md (/bedrock:ask terrier).

CONTEXT (verified 2026-10-06 against code at Terrier HEAD 8c7a817 and the sibling Luna repo)
- Terrier: C:\Users\pgoff\Documents\Codex\2026-09-27\we-are-going-to-have-an\outputs\cu-geography-v3
  git, branch claude/walk-list, no remote. Django/Waitress :8767 (LIVE; never test against it) + React/MapLibre.
  Stores in .local (DATA_DIR, server/settings.py:5): contacts.sqlite3, scores.sqlite3, street.sqlite3,
  walk.sqlite3, roads.sqlite3, accounts.sqlite3, .walk-salt, snapshots\co-cd005-*. The street/contacts salt
  .street-salt is NOT in this .local: build_contacts.py:22, build_street.py:176, build_scores_v3.py:589 default
  to the sibling cu-geography\.local\.street-salt.
  Handoffs: WALK_LIST_HANDOFF_2026-10-02.md, TERRIER_HANDOFF_2026-10-01.md,
  ACTUAL_DATA_HANDOFF_2026-10-01.md, DATA_AUDIT_2026-10-01.md.
- Data chain for scores (Terrier does NOT compute the overall score itself):
  source CSV -> Luna completion (sibling repo) -> row framework rowfw-1.0.0 (sibling repo,
  .local\row-framework-20260930\build\rowfw-1.0.0: row_scores, household_scores, profiles parquet; frozen
  framework_config.json holds the 40/35/25 and theme weights; ovr_rank computed there) -> Terrier
  tools/build_scores_v3.py:49-53,95 -> scores.sqlite3. server/research/bundle_v4.py:271-273 refuses any bundle
  not built from the frozen rowfw sha256s (AL.FROZEN_SHA256). Field status labels already exist:
  tools/field_labels.py:88-95 (estimated_gpu_model / estimated_household_model / estimated_majority_default /
  estimated_accuracy_argmax).
- Known Colorado hard-codes (re-grep before editing; full list in the review table below):
  server/research/bundles.py:98 + :289 (co-cd005 snapshot regex), :213 ('0805' / 'Colorado · District 05');
  server/research/bundle_v4.py:24 SNAPSHOT_RE; server/research/v3_cache.py:32 SNAPSHOT_RE; server/urls.py:17-18;
  server/research/street_views.py:27 COLORADO_BOUNDS; server/research/v3_people_views.py:43,408-409
  ('Extent outside Colorado'); tools/walk_geo.py:5,54-60 EPSG:26913 (Colorado UTM zone 13);
  tools/ingest.py:50 (state=='CO' and CD '005'); tools/prepare_geography.py:10-12,35-37 (TIGER2025 state 08,
  El Paso roads 08041); tools/geography_levels.py:33-43; tools/extend_geography.py:53-88;
  walk city/route words (server/research/walk_views.py:234,243; tools/build_walk.py:138,150;
  server/research/walk_names.py:61,170); source paths (tools/build_walk.py:55, build_contacts.py:13,
  build_credit_quality.py:10, build_scores_v3.py:588); road attribution (road_views.py:37,
  build_road_context.py:29); frontend/src/main.tsx:89,102,105; Start.ps1:55; tools/container_start.py:12.
- Street network: Census TIGER/Line 2025 ROADS, El Paso County only (prepare_geography.py:12,
  tl_2025_08041_roads.zip), MTFCC S1200+S1400 (walk_geo.py:30), noded on a 1 m grid in EPSG:26913, plus
  virtual streets for voter streets TIGER lacks. Street segment unit = road_span (walk_geo.py:39-47,295:
  maximal same-name chain through degree-2 nodes, with even_left parity); stops carry span_id
  (tools/build_walk.py:76-86).
- Census blocks: CO already has TIGER2025 TABBLOCK20 on disk (work\geography\blocks.zip,
  tl_2025_08_tabblock20) and it carries POP20, HOUSING20, ALAND20, AWATER20. No CO download needed.
  Voter-file block id = svi_registration_censusid (15 digits; ingest.py:16,44,53); scores keep reg_block.
- Luna (LOCKED, never edit in place; sibling ...\outputs\cu-geography\.local\):
  stratified-scaling-20260929\ = 26 model-*.joblib, 26 selection-*.json, all-target-selection-lock.json,
  policy.json. The lock hashes policy.json, the 26 selection/model files and the code (train_revised.py,
  revised.py) - NOT the source file or row count. Source hash and row count are asserted elsewhere:
  four-metric-validation-20260928\validate_remaining.py:48,53; train_revised.py:130; apply_revised.py:38,85,112;
  literal 491308 in household_model.py:15, gpu_publish.py:14, prior_repair.py:328,407, household_copy.py:562+;
  CO source path in individual-validation-20260928\validate_individual.py:32 (b.SOURCE for every stage).
  Selection files hold scaler, metric, K, weights, knn_fraction, calibration flags only. They do NOT hold the
  feature list (completion-20260929\individual-results.json selected_features; 49-column union of sii_*,
  sai_*, eng_* engineered columns, no geography codes), the encoder (revised.py:81-103), the RF settings
  (fixed in train_revised.py:57-59), the seed (policy.json, 20260948) or the partitions (revised.py:61-79,
  StratifiedGroupKFold on household/address groups). 26 targets = 22 binary + 4 categorical (children,
  adults, owner_renter, dwelling_type).
  CO CURRENT result = section 30 export data-completion-gpu-model-20260930 (CURRENT_MODEL_OVERVIEW.md:3-7):
  stratified RF/KNN fills -> household model (CPU RF with leave-one-out household labels from
  sii_household_id; 13 fields, 875,850 values) -> GPU XGBoost on the RunPod H200 (8 fields, 449,343 values)
  -> majority-default marking (238,653 values, 3 fields). Holdout 86.760% (2nd use), test 86.797%. The
  household-copy stage (498,794 values, 85.7%) and the rare-code prior repair are SUPERSEDED and no longer
  apply to any blank. No 12-hour Luna run is recorded; the longest recorded run is the stratified grid
  search (780 configurations per field; run-completed.json 7,770 s, about 2.2 h).
- Definitions: ...\outputs\cu-geography\CONVERSATION_REQUESTS.md R006-R023.
  Overall = 40% alignment + 35% participation (10 sai_* turnout) + 25% sphere of influence
  (45% giving, 45% volunteering, 10% household).
- Inputs (verified by in-stream read, aggregates only):
  Downloads\Janelle Bynum (OR-05).zip -> statara_or_cd005_20260904.csv, 562,563 rows;
  Downloads\Luke Bronin (CT-01).zip -> statara_ct_cd001_20260904.csv, 458,372 rows.
  Same 622 columns in the same order as CO. sii_household_id is filled on every row in both
  (OR 396,480 households, CT 334,071). Blanks are per field, not whole rows: e.g. sci_cpi_auto_index blank
  OR 83,214 (14.8%), CT 59,719 (13.0%), CO 79,551 (16.2%); median blank rate across all sci_* columns
  OR 14.8%, CT 13.0%, CO 16.2%; no row is blank on every sci_* column; 10 sci_* columns are 100% blank in
  all three files (never imputable, not Luna targets).
  Text is UTF-8 with a few invalid byte sequences (OR 67, CT 10, CO 9); the Luna stages read cp1252.
  Geography: OR-05 = 6 counties (41005 Clackamas, 41017 Deschutes, 41043 Linn, 41047 Marion,
  41051 Multnomah, 41031 Jefferson 16 rows). CT-01 = 4 planning regions (09110 Capitol, 09130 Lower CT
  River Valley, 09140 Naugatuck Valley, 09160 Northwest Hills); block ids use the planning-region county
  codes (2022+ Census coding), so the 2020-edition TIGER block file (old county codes 09001-09015) will
  NOT join. A few stray out-of-state rows in sii_* fields (OR 139, CT 25); registration fields are in-state.
  Precinct formats: OR 2-4 digits ("COUNTY-999" names), CT 999-99 (+ svi_registration_township town
  names), CO 10 digits. No employer field (occupation only).
  Vote history svi_vh_<year><g|p|pp> = blank or 2 chars; first char = method (Y, A, E, P, M, Q).
  2024 general: OR Y 402,992 / A 3,742 / E 1,747 / M 1,515 / P 919; CT P 169,859 / E 124,318 / A 25,312 /
  Y 8,957 / M 302; CO M 311,877 dominant. svi_permanent_by_mail is blank in OR and CT.
  Today ABS_PREFIXES = ('M','A') (walk_names.py:19) would mark about 1% of OR votes as mail.
  Party (svi_party_registration): OR U/D/R plus O 4,835, L 3,660, W 1,241, G 1,222, T 605;
  CT U/D/R plus L 534, G 219, W 97, O 1. Today non-D/R/U codes are lumped to 'O' in
  tools/field_labels.py:116-118, server/research/walk_store.py:393-394, frontend/src/v3/lib/walk.ts:632.
- Tests at the last recorded run (WALK_LIST_HANDOFF_2026-10-02.md:91, commit 0c484fb): Django
  server.research 311 OK, frontend test:unit 151/151, walk unittests 121 OK, tools.test_measures 16 OK,
  verify-walk-list.mjs 24/24 on port 8769. Static counts match (311 def test_ under server/, 151 it/test).

PAUL'S RULINGS (binding)
- FLEXIBLE: districts, candidate, race mode, industries, layer combinations and ROI weights are all
  config. Never hard-code Bronin's race status.
- Brain = Obsidian vault notes only (projects/terrier.md + decision notes). No separate Project Brain:
  nothing for Terrier under the Project Brain folder; brainlink configuration untouched.
- O-1 a candidate's "ideology position" = their party membership (Bronin = his party). The support model
  switches general/primary per district; primary mode is never hidden.
- O-1b no door-result calibration. O-2 race mode is a per-district toggle.
- O-3 real OR/CT mail-ballot/absentee codes; no "n/a" column. Oregon is all vote-by-mail.
- O-4 imputation = fill missing measured values (blank sci_* values) with the Luna RF/KNN model; nothing to
  do with other industries. O-4b uncertainty ranges: unanswered, so OFF by default.
- O-5 missing geo-audit values are imputed too; no "Not audited" label.
- O-6 ROI presets: "Mixed-support streets", "Supporter turnout", "Voter identification".
- O-7 the copied Luna code takes per-district counts and hashes. O-8 no 12-hour rerun; applying the model
  is not a 12-hour job, and a run that long means something is wrong.
- O-9 NEVER strip personal data; all data always shown; run locally on the full data; no pod run,
  no de-identified run.
- O-10 every party code shown as itself (W = Working Families).
- O-11 numeric partisan and support layers count in the overall score.
- O-12 only CUA exists; more industries only when Paul defines them. O-13 extract to Downloads\terrier-sources\.
- Printed walk list: optional scores toggle, default raw.
- Census downloads APPROVED: TIGER 2020 tabblock20 (POP20, ALAND20) for OR, CT, CO from
  www2.census.gov, plus TIGER street files for the OR-05 and CT-01 counties if CO's street network
  came from TIGER (it did: TIGER2025 ROADS). Census.gov only; log filename + size of each.
  See Open for Paul item 2 before downloading (edition and boundary files).

OVERALL SCORE WEIGHTS (score_weights config; PROPOSED, not yet ruled - Open for Paul item 1)
- District WITH a candidate: Industry alignment 30 / Candidate support 25 / Participation 25 /
  Sphere of influence 20. (Paul's 40/35/25 scaled by 0.75, rounded to 5s; support takes 25.)
- Candidate support is the single partisan component: general mode = partisan direction signals oriented
  to the candidate's party; primary mode = within-party support. Raw partisan lean stays a map layer
  (the existing 'lean' group, field_labels.py:23), not a second score term (no double counting).
- Turnout-type signals (primary vote counts, turnout scores) are NOT support inputs: they already sit in
  Participation (sai primary/general turnout scores). Support inputs are direction only.
- District WITHOUT a candidate (CO today): 40/35/25 unchanged, so CO stays byte-identical.
- The weights live in the row framework's frozen framework_config.json, not in Terrier: the per-district
  score_weights must be applied in a versioned per-district copy of the row framework (Phase 3b).

ROLES AND PLUGINS
- Opus 5.5 (lead session): plan, decide, review. Sonnet 5.5 xhigh: all code. Opus 5.5 high: verify.
  Never Haiku for code.
- agent-manager: Phase 4 lanes run in parallel, one worktree/branch per lane off claude/walk-list.
  Remove each worktree's node_modules junction before removing the worktree.
- autonomous-agent-harness: imputation and store builds run as background jobs; notify on exit; no poll loops.
- quant-model-evaluation: imputation holdout, density join check, ROI sanity.
- analyze-issue: on every failing test or check, before any fix.
- Bedrock/Obsidian: /bedrock:ask terrier at start; update vault notes at end. Aggregates only in the vault.
- Caveman chat output.

PHASE 0 - CO baseline (before ANY code change; read-only on CO)
- Start a non-live copy of the current HEAD on a spare port (e.g. 8769; never 8767). Record golden
  responses for a fixed endpoint list (health, areas, geometry, people pages, contact sheet, criteria,
  method, walk list build for 3 fixed street sets, PDF/CSV export) and sha256 each.
- Canonical content hash of each CO store (sorted-row dump per table, not raw SQLite bytes) and sha256 of
  .walk-salt, the snapshot zips and the rowfw parquet files. Save as tests/fixtures/co_baseline.json.
- This baseline is the CO gate for every later phase and every lane.

PHASE 1 - Sources and census (scout, read-only except the approved extraction/downloads)
- Extract both zips to Downloads\terrier-sources\. SHA-256 each CSV.
- Profile per district: rows, households, blank rate per Luna target (26), party codes, vote-method letters
  per election, precinct formats, rows outside the district, block-id county codes.
- Vote codes (O-3): look for the vendor's (statara) data dictionary on disk first. Build a per-state map
  method letter -> meaning. Default until confirmed: OR = every recorded vote is a mail ballot (all
  vote-by-mail); CT = A absentee (+ M mail), E early, P in person; CO unchanged (M, A). Send Paul the
  letter table with counts (not a stop gate).
- O-5: trace flag_geo_unaudited (tools/scores_schema.py:13) to where the row framework sets it and list
  which geography values are missing for those rows. Plan the fill (estimated + marked, never "Not
  audited"). If the fill cannot come from the Luna model, say so to Paul in plain words.
- Re-grep Terrier for every CO / CD-05 / 0805 / 08 / Colorado / El Paso / 26913 literal. Output: hard-code
  list with file:line (start from the review table).

PHASE 2 - Multi-district foundation (Sonnet)
- districts config: {id, state, state_fips, cd, label, source_csv, sha256, rows, households, bounds,
  boundary file, utm_epsg (CO 26913, OR 26910, CT 26918), counties[], candidate, party, race_mode,
  primary_universe (party_only default; party_plus_unaffiliated allowed), industries[], vote_codes{},
  city_normaliser, score_weights{}, data_dir, salts}. The CO entry reproduces today's behaviour exactly.
- CO keeps its legacy paths: data_dir = .local root, .walk-salt, the sibling .street-salt, snapshot keys
  co-cd005-*. New districts get .local\<district>\ with their own .walk-salt and .street-salt.
- Replace each hard-code with config reads. District selector in the UI.
- Ingest: reader that decodes OR/CT the same way CO is decoded today (so CO output is unchanged);
  out-of-district rows flagged and kept, excluded from the map; precinct normaliser per state; CT town from
  svi_registration_township.
- Street network for OR-05 and CT-01 with the same tool and source as CO: TIGER2025 ROADS per county
  (OR: 41005, 41017, 41043, 41047, 41051, 41031; CT: 09110, 09130, 09140, 09160), S1200+S1400, noded in the
  district's UTM zone. Walk city/route-word normalisers per state.
- GATE: CO Phase 0 baseline matches (golden API responses and canonical store hashes identical);
  all suites green (311 Django, 151 frontend, 121 walk, 16 measures, verify-walk-list 24/24 on a spare
  port, plus new tests); never against 8767.

PHASE 3 - Imputation per district (Sonnet, background job, local only)
- Copy the Luna stages to .local\<district>\luna-v1\ in the Terrier repo. Record lock-file and stage-file
  hashes before and after; originals must not change. Every row count, source path and source hash comes
  from the district config (replace the literals and b.SOURCE listed in CONTEXT).
- Rebuild the inputs per district with the copied code: feature frame (features.pkl chain, stance
  columns, eng_* features), labels and splits, household/address groups (sii_household_id plus address).
- NO hyperparameter search. Frozen config = the 26 selection files (scaler, metric, K, weights,
  knn_fraction, calibration) + selected_features from individual-results.json + revised.py encoder +
  train_revised.py RF settings + policy seed. Refit each target on the district's own observed rows.
- Then the household model stage (CPU, household_model.py logic, district ROWS/HOUSEHOLDS from config).
- GPU XGBoost stage: SKIPPED by default (needs xgboost plus a GPU; no pod under O-9; worth about +0.09 pp
  in CO). Open for Paul item 3.
- Prior correction: not needed when the household model fills children and dwelling (as in CO's current
  export). If used, priors come from the district's own training partition, never CO's.
- Holdout discipline (/quant-model-evaluation): before any fit, freeze a district holdout of labelled rows,
  grouped so no household/address group spans folds; write its receipt and hash. Choose per target among
  refit / CO transfer (saved CO model-*.joblib applied read-only) / majority on development folds only;
  score the holdout ONCE. Report per target: accuracy vs majority vs CO transfer vs CO's own score, plus a
  blank-mix reweighted estimate (blank rows have fewer observed household members). Trend first.
- Majority rule = CO's ratified rule: if a target's lift over majority is under 0.5 pp, its fills are
  marked estimated_majority_default (do not invent other fallbacks).
- Expected runtime: each stage well under 1 hour. Any single stage over 2 hours = stop and /analyze-issue.
- Every filled cell carries status (existing field_labels.py statuses) + method + model id. All values shown
  in the UI, marked "estimated". No uncertainty ranges (O-4b default off).
- Send Paul the aggregate table. Not a stop gate.

PHASE 3b - Row framework per district (Sonnet)
- Copy rowfw-1.0.0 into a versioned per-district stage (CO original stays frozen). Run it on the
  district's completed data with the district score_weights. Same council defaults (theme weights,
  suppression below 20 people or 10 households).
- Make the bundle_v4.py frozen-sha check per district (CO keeps its exact FROZEN_SHA256).
- Feed tools/build_scores_v3.py from the district's rowfw outputs via config.

PHASE 4 - Features (parallel lanes via agent-manager)
A. Industries: CUA becomes the first entry in an industries config (fields + weights). Each industry
   is scored separately; the UI picks the industry; overall uses the selected one.
B. Candidate support, toggle per district.
   General mode: partisan direction signals (sai_partisan_score, harris/trump support, registered party,
   which party's primary ballot was taken) oriented to the candidate's party. No turnout counts.
   Primary mode: universe from primary_universe config (default the candidate's party registrants);
   within-party support model. Opus writes the spec; [PAUL] approves before build.
C. Density: TIGER tabblock20 (POP20, ALAND20) joined on the 15-digit block id with the SAME county coding
   as the voter file. Population density = POP20 / ALAND20 (km2); blocks with ALAND20 = 0 get no density.
   Voter density per block and per road_span. Report the join rate per district (target >= 99% of
   registration block ids).
D. Overlay: any combination of industry alignment, participation, sphere of influence, partisan,
   candidate support, population density, voter density. Modes: single toggle, weighted blend,
   two-layer bivariate. Legend per mode. Extends MapCanvas fill-a/fill-b (MapCanvas.tsx:110-112).
E. Street ROI planner beside the walk list. Unit = road_span (the walk graph's street segment).
   Per span: doors = placed stops, people = placed voters, length_m (floor 50 m), s = mean candidate support,
   p = mean participation, block population density. Spans with fewer than 5 doors are not ranked.
   Preset value per 100 m walked (PROPOSED formulas - Open for Paul item 1):
   - Mixed-support streets: sum over people of (1 - |2s - 1|)
   - Supporter turnout: sum over people of s x (1 - p)
   - Voter identification: count of people with no clear support signal (s in the middle third or missing)
   Weights exposed; population and voter density optional weights. Ranked spans feed the existing
   walk-list build and routing.
F. Walk-list fields: real per-state vote codes (Phase 1 map), Abs/mail column per state rule; party codes
   as-is (remove the D/R/U/O lumping); printed scores toggle default raw.
- O-11: candidate support enters the overall score through score_weights (see OVERALL SCORE WEIGHTS).
- Every lane re-runs the Phase 0 CO gate before merge.

PHASE 5 - Verify (Opus high)
- CO gate (Phase 0 baseline) identical; all suites and browser checks green on a spare port; new tests per lane.
- Per district: row counts reconcile to the source; imputation table; density join rate;
  10 hand-checked top streets per preset.
- /quant-model-evaluation on ROI: rank stability under +/-20% weight changes (top-50 overlap).

PHASE 6 - Land
- Commit each verified lane on claude/walk-list (local, no remote). Restarting the live 8767 server is Paul's.
- Update the vault: projects/terrier.md status, decision and dead-end notes. Update the lane memory pointer.

DO NOT: edit Luna or rowfw originals; change CO outputs; run a grid search; strip or hide personal data;
put voter rows in the brain, vault, cloud or pod; poll background jobs; invent definitions (ask);
test against 8767; take full memory dumps of the Python processes; delete .local\.walk-salt.
```

## Answered by Paul 2026-10-07 (binding; supersedes the PROPOSED marks above)

1. Weights APPROVED: with a candidate 30/25/25/20; without (CO) 40/35/25. Street formulas APPROVED, with one change: "Mixed-support streets" = sum over people of (1 - |2s - 1|) x p (p = voting chance). The other two presets as written in Phase 4E.
2. Census APPROVED: TIGER 2025 edition block file (tabblock20, same 2020 counts) and district boundary files for OR and CT, census.gov only. Replaces the "2020 tabblock20" wording above.
3. GPU fill step SKIPPED for OR/CT.
4. Parallel lanes use Claude Code's built-in background agents with worktree isolation (agent-manager needs tmux/WSL; DevFleet needs its own server). Same rules: one worktree per lane off claude/walk-list, report on exit, no polling.

5. One server process per district APPROVED: CO 8767, OR 8775, CT 8777; a header menu jumps between them (CU_DISTRICT picks the config entry; no accounts.sqlite3 migration).
6. Walk-list Abs wording APPROVED: OR "Abs = mail ballots (Oregon votes by mail; every recorded vote counts)"; CT "Abs = absentee or mail ballots (A or M)"; CO unchanged.
7. Extra census.gov downloads APPROVED: OR/CT state legislative districts (SLDL, SLDU) and one TIGERweb ZCTA query per district. DONE 2026-10-07 (manifest work\geography\DOWNLOADS_2026-10-07.json, 20 rows). Notes for Phase 3b: drop CT's 09ZZZ water rows; the ZCTA files over-reach the district (keep only ZCTAs intersecting it; CT-01 has 34 Massachusetts ZCTAs); 1 OR and 5 CT ZCTAs have self-intersecting rings, so apply make_valid before any overlay.

## Open for Paul (answered above; kept for the record)

1. **Weights and street-plan formulas.** Do you approve the overall weights (with a candidate: industry 30, candidate support 25, participation 25, sphere 20; without a candidate, Colorado's 40/35/25 unchanged) and the three street-plan formulas written in Phase 4E?
2. **Census files.** Connecticut's voter file uses the newer county codes, which the 2020 edition of the Census block file does not have. Can the build use the 2025 edition of the same block file instead (it has the same 2020 population counts; Colorado already uses it) and also download the district boundary files? All from census.gov.
3. **Colorado's last fill step.** Colorado's final fill used a GPU add-on that gained about 0.1 percentage points of accuracy. It needs a new package and a GPU, and you said no pod. OK to skip it for Oregon and Connecticut?

## Review 2026-10-06

Fact check (VERIFIED / WRONG / UNVERIFIABLE). Evidence was read by scouts and re-checked by the lead session.

| Claim in the 2026-10-06 draft | Verdict | Correct fact |
|---|---|---|
| bundles.py:98 co-cd005 snapshot regex | VERIFIED | also :289 |
| bundles.py:126 '0805' label | WRONG | :126 is the extent check; label check is bundles.py:213 |
| bundle_v4.py:28 SNAPSHOT_RE | WRONG | bundle_v4.py:24 (:28 is TOLERANCE) |
| street_views.py COLORADO_BOUNDS | VERIFIED | street_views.py:27, used :130 |
| Hard-code list complete | WRONG | about 25 more sites, incl. EPSG:26913 in walk_geo.py, ingest.py:50, v3_people_views.py:43,408 |
| Stores contacts/scores/street/walk + .walk-salt | VERIFIED (incomplete) | also roads.sqlite3, accounts.sqlite3, snapshots; .street-salt lives in the sibling repo |
| 311 server + 151 frontend tests | VERIFIED | WALK_LIST_HANDOFF:91 at 0c484fb; static counts match; gate also needs walk 121, measures 16, verify-walk-list 24 |
| 26 model-*.joblib, 26 selection-*.json, lock file | VERIFIED | lock hashes code, policy and selections, not source or rows |
| Selections hold scaler, distance, K, weights, blend | VERIFIED (incomplete) | feature list, encoder, RF settings, seed, partitions are elsewhere (see CONTEXT) |
| CO deployed = reference + prior-corrected + household copy (498,794, 85.6%) | WRONG | current = section 30 GPU-model export (household model + GPU XGBoost + majority default; test 86.797%); household copy superseded |
| Long CO run was the grid search | UNVERIFIABLE as stated | no 12-hour run recorded; the grid search (about 2.2 h) is the longest recorded Luna run |
| CO street network built from TIGER | VERIFIED | TIGER2025 ROADS, El Paso 08041 only, S1200+S1400, EPSG:26913 |
| Row counts 562,563 / 458,372; 622-column schema | VERIFIED | header identical to CO |
| About 9-15% of rows have blank sci_* fields | WRONG | blanks are per field (median 14.8% OR, 13.0% CT, 16.2% CO); no row is blank on every sci_* column |
| cp1252 bytes | UNVERIFIABLE as stated | files are UTF-8 with a few invalid sequences; Luna reads cp1252 |
| OR geocodes outside district; state precinct formats; CT planning regions; no employer field | VERIFIED | |
| OR votes "almost entirely" by mail | Misstated ruling | ruling says all vote-by-mail; the file codes those votes Y, not M |
| R006-R023 definitions | VERIFIED | R023 exists |

Changes made:
1. Corrected the hard-code line numbers and listed the full set (wrong lines would send the builder to the wrong code).
2. Added roads/accounts stores, snapshots, and the sibling-repo .street-salt (per-district salts were missing).
3. Replaced the CO deployed-result description with the section 30 chain and dropped the household-copy stage (the draft would have rebuilt a superseded pipeline).
4. Recorded what the selection files do not hold and where the feature list, encoder, RF settings, seed and partitions live (a refit from the selections alone is impossible).
5. Listed every hard-coded row count, source hash and source path in the Luna stages (O-7 scope was understated).
6. Added the household-model stage, made the GPU stage skipped-by-default (no pod under O-9, xgboost not local), and replaced the invented "coverage-repair fallback" with CO's ratified majority-default rule.
7. Added a frozen per-district holdout scored once, development-only selection, and a blank-mix reweighted estimate.
8. Prior correction now uses the district's own training priors.
9. Added Phase 3b: the row framework (where the overall score and weights actually live) and the bundle frozen-sha check had no place in the plan.
10. Corrected the blank-rate fact and noted the 10 always-blank sci_* columns.
11. Census: CT block ids use planning-region codes, so the 2020-edition block file would not join; CO's on-disk 2025-edition file already has POP20/ALAND20; added boundary files. Moved to Open for Paul item 2.
12. Street network: per-county TIGER2025 ROADS lists for OR and CT and a per-district UTM zone (EPSG:26913 is Colorado's zone; Connecticut lies about 32 degrees of longitude east of its central meridian).
13. ROI: defined the street segment as the existing road_span, set a door minimum and length floor, and wrote proposed preset formulas (moved to Open for Paul item 1).
14. Candidate support: removed primary vote counts from support inputs (they are turnout, already in Participation); made the primary universe a config value (party rules can admit unaffiliated voters; not verified here).
15. Vote codes: recorded the real method letters per state and that today's ('M','A') rule would show about 1% of Oregon votes as mail.
16. Party: recorded the extra codes per state and the three places that lump them into "other".
17. O-5: no "geo-audit" field exists by that name; it maps to the row framework's flag_geo_unaudited. Added a Phase 1 trace.
18. Added Phase 0 CO baseline (golden API responses plus canonical store hashes) and made it the gate for every phase and lane; CO keeps its legacy paths and salts.
19. Rulings restated exactly: O-1 wording and "primary never hidden", O-3 "all vote-by-mail; no n/a column", O-4b default off, O-9 no pod, Ruling 2 (no Project Brain folder, brainlink untouched); weights marked as proposed.
20. Safety: never test against 8767, no full memory dumps, node_modules junction before worktree removal, live restart is Paul's.
