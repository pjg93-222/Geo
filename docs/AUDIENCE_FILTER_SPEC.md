# Audience filter for area street groups: design spec

Status: **design only, not yet built.** Written 2026-10-22. No code changes accompany this file. Owner decisions D1 and D2 (section 0.1) settle the basis and the review question; the remaining open items are in section 5.

## 0.1 Owner decisions (2026-10-22)

**D1. Basis: Terrier's registration party.** The audience filter selects voters by the party recorded in Terrier's walk store (`person.party`), which `tools/build_walk.py:319, 329` loads unchanged from the source file's `svi_party_registration` column. The source file for OR-05 (`statara_or_cd005_20260904.csv`, 622 columns) also carries modeled political leanings: `svi_party_modeled_rollup`, `svi_party_combined_rollup`, `sai_ideology_score`, `sai_ideology_enhanced_score`, the `sci_ct_political_partyaff_*` and `sci_ct_political_outlk_*` flags, and `sei_party_trust_range`. None of these is used as the filter basis, and neither is Terrier's legacy lean.

Registration codes in that file (whole-file row counts, read 2026-10-22; raw code, vendor rollup):

| Raw `svi_party_registration` | Vendor rollup | Rows | Terrier label (`PARTY_CODES`) |
|---|---|---:|---|
| U | U | 201,555 | Unaffiliated |
| D | D | 168,983 | Democrat |
| R | R | 149,477 | Republican |
| U | O | 30,984 | Unaffiliated (rollup disagrees, see Q2) |
| O | O | 4,835 | Other |
| L | O | 3,660 | Libertarian |
| W | O | 1,241 | Working Families |
| G | O | 1,222 | Green |
| T | O | 605 | *none: T is not in `PARTY_CODES`* (see Q3) |
| U | (blank) | 1 | Unaffiliated |

**D2. Review.** The owner states: "We are trust, don't need independent review." No independent or counsel review is required before building or sharing this feature. This supersedes the counsel item in `HANDOFF_2026-10-01.md:98` for this feature, in the same way `WALK_LIST_HANDOFF_2026-10-02.md:108` recorded a waiver for walk lists. It is recorded as the owner's decision, not as a legal finding.

**D3. Second dimension: support for the candidate (added later on 2026-10-22).** The owner asked for "persuadable" voters, using the vendor's degrees of support for either party. This narrows D1: registration remains the party basis, and a support dimension is added beside it.

- **Source:** Terrier's existing per-voter support score, not raw vendor columns. `support.sup_score` (0..100, `tools/scores_schema.py:58-61`) is built by `tools/support.py:68-76` as the equal-weight mean of the available signals:
  - the vendor partisan score `sai_partisan_score`;
  - presidential support from `sai_harris_support_score` and `sai_trump_support_score` (`support.py:44-46`);
  - registration;
  - the primary ballot party.

  Missing signals drop out of the mean. The walk store already reads it (`server/research/walk_store.py:516-518`).
- **Control (D5 below):** a two-handle spectrum slider over `sup_score`, not band chips. "Persuadable" is the middle-third preset, the same group the existing "Voter identification" and "Mixed-support streets" presets use (`tools/field_labels.py:94-98`), with edges included as in `walk_views.py:653`.
- **Label (D4 below):** the UI says "Persuadable" for the middle-third preset.

**D5. Support control: two-handle spectrum slider (2026-10-22).** The owner chose a slider over band chips.

- **Scale:** one horizontal track over `sup_score`, 0 to 100, colored with Terrier's existing lean ramp (`--strong-r` through `--strong-d`). The end labels come from the district candidate's party: for a Democratic candidate such as OR-05's, 0 is "Republican-leaning" and 100 is "Democratic-leaning", with "Middle" at the center. The ends are never labeled with spectrum words (section 1.1); the score measures party support, not ideology.
- **Handles:** two handles set an inclusive range [low, high]. A voter is included on this dimension when low <= `sup_score` <= high. The handles cannot cross, and keyboard arrows move each handle by 1.
- **Presets:** four buttons set both handles. The button matching the current range is shown as selected.

  | Preset | Range |
  |---|---|
  | Leans R | 0 to 100/3 |
  | Persuadable | 100/3 to 200/3 |
  | Leans D | 200/3 to 100 |
  | Everyone | 0 to 100 |

  Presets use exact thirds internally and display as 33 and 67. For a Republican candidate, the first and third preset names swap so they still describe party lean.
- **No score:** a checkbox, "Include voters with no score", decides whether voters without a `support` row count as included on this dimension. The default state is "Everyone" with the box on, which means the support dimension filters nothing until the user moves a handle or picks a preset.
- **Combining:** the slider range is an include condition. It is AND-ed with the registration include chips, and registration exclude chips still win at the door (section 2.2). The slider has no exclude mode; narrowing the range does that job.
- **Recompute:** dragging updates the matching-door count and regroups after the same debounce as other filter changes (section 3.2). The basis line states the range in words, for example "Support Persuadable (33 to 67)" or "Support 20 to 45, plus no score".
- **Help text:** "Terrier support score: mean of vendor partisan and presidential support scores, registration and primary ballot. A ranking aid, not a measured belief."

**D4. Label rule amended for "Persuadable" (2026-10-22).** The owner decided the UI should say "Persuadable" for the middle third of support. Today `ADVISORY_PATTERN` (`field_labels.py:145-147`) bans `persuad|persuasion`. It is enforced at import by `self_check` (`field_labels.py:198-209`) and mirrored by `frontend/tests/v3/copy.test.ts:8`. The build must amend the rule as follows:

- **Allow one label:** permit exactly the label "Persuadable" for the middle-third preset button. Do this by exempting that one string, not by deleting `persuad` from the pattern, so "persuasion", "persuade" and the other advisory words stay banned everywhere else.
- **Change both checks together:** amend the Python pattern or `self_check` and the frontend copy test in the same change, so they keep agreeing.
- **Help text:** the label gets a tooltip or help line that says what it means: "Middle third of Terrier's support score; not a measured belief."
- **Other presets:** "Leans R", "Leans D" and "Everyone" (D5).
- **Existing labels:** the presets' existing labels ("Voter identification", "Mixed-support streets") stay as they are. Only the audience filter uses the new word.
- **Not the far-right columns:** the last eleven columns of the OR-05 file (`sei_dem_pct_*`, `sei_pvi_*`, `sei_party_trust_range`, `sei_dem_avg_metric*`, `sei_dem_direction_*`) are undocumented in Terrier and excluded in `data/field_dictionary.csv`. Their names suggest area-level vote share and partisan index rather than a person's support, but that is unverified (Q11).
- **Accuracy:** the support score has no validation against vote choice (`research/systems/terrier-canvass.md:82`), and vendor partisan tags scored AUC 0.58 (`HANDOFF_2026-10-01.md:97`). Bands are a ranking aid, not a measured belief.
- **Coverage:** `support` rows are written only for candidate districts (`scores_writer.py:177-183`). Whether OR-05 has them in the current build is unverified (Q12). Without them every voter is "No score".

## 0. Context and scope

The Walk-list panel on `feat/area-street-estimates` lets a manager draw a polygon, finds the streets inside it from an installed public address-point catalog, and splits them into groups of 100–150 estimated doors. That basis is deliberately person-free: `server/research/area_street_views.py:3-5` says the module "never opens voter stores", and its export README says "door counts will be voter-based, not these estimates" (`frontend/src/v3/lib/publicStreetGroups.ts:110`).

The requested change is to let the user include or exclude groups of voters ("far left" through "far right", plus green, libertarian, independent and undecided), and have the street groups recompute from the doors that match. That switches the door basis from the public catalog to the voter file and adds person-level political attributes to the selection. Both were outside the original handoff, and both touch items the repo flags for review (section 4). This spec settles what the data can honestly mean and which decisions the owner has to make before anything is built.

Citation note: files under `frontend/src/v3/lib/publicStreetGroups.ts`, `frontend/src/v3/components/AreaStreetDiscovery.tsx`, `frontend/src/v3/api/publicStreets.ts` and `server/research/area_street_views.py` are **uncommitted** work in the `feat/area-street-estimates` worktree as of 2026-10-22. Line numbers refer to that working copy and may drift while it is still being edited. All other citations refer to `main` at `a290240`.

## 1. Dimensions

The requested labels describe one left–right spectrum. Terrier has no such field. It has four unrelated sources, each with a different meaning and a different reliability. They stay separate in the data model and in the UI; the feature never merges them into a single 11-point scale.

| Dimension | Source | Values | What it is | Reliability |
|---|---|---|---|---|
| Registration party | voter file, `person.party` (`tools/build_walk.py:84-87`), labels `PARTY_CODES` (`tools/field_labels.py:134-135`) | D Democrat, R Republican, U Unaffiliated, G Green, L Libertarian, O Other, W Working Families | The party the voter chose when registering | A recorded fact about registration. Says nothing about current belief, and unaffiliated is not an ideology. |
| Vendor Outlook | consumer-file flags `sci_ct_political_outlk_*` (`server/research/scoresdb.py:28-30`), labels `OUTLOOK_LABELS` (`scoresdb.py:32`), collapsed by `collapse_outlook` (`scoresdb.py:358-372`) | Very conservative, Somewhat conservative, Middle outlook, Somewhat liberal, Very liberal; plus Unknown (-3) and Multiple (3) | A modeled marketing segment bought from a data vendor | Unvalidated. The repo records vendor partisan tags at AUC 0.58 (`HANDOFF_2026-10-01.md:97`), barely better than chance. Outlook accuracy has not been measured at all. |
| Modeled lean | legacy score -100..100, banded by `lean_band_of` (`field_labels.py:155-159`) into Strong R, Lean R, Centre, Lean D, Strong D (`field_labels.py:141`) with cuts at 25 and 75 | -2..2 bands | A legacy numeric composite kept for wire compatibility | `TERRIER_HANDOFF_2026-10-01.md:59`: legacy lean is "not used for displayed opinions, Outlook filtering, registration colors, or rankings" and "is not a validated belief measure". |
| Canvass result | `Disposition` (`server/research/models.py:64-75`), codes `CODES = ('S','U','O','NH','MV')` (`server/research/walk_views.py:88`), labels Supporter, Undecided, Opposed, Not home, Moved (`frontend/src/v3/lib/walk.ts:15`) | S, U, O, NH, MV | What a canvasser recorded at the door, per walk list, append-only; newest row per (list, person) is current, null clears | Observed, but only for people already on a knocked walk list, and only as current as the last visit. |

Terrier does store contact results: the `Disposition` table above, written through `api/v3/walk/dispositions` (`server/urls.py:36`, `walk_views.py:995-1023`). No survey or support-ID table exists beyond it.

### 1.1 Mapping the requested labels

The feature shows each dimension's **own** labels. The requested words are mapped below only to say which control a user would reach for, not to rename anything.

| Requested label | Nearest honest control | Supported? |
|---|---|---|
| Far left | Outlook = Very liberal | **No.** "Very liberal" is a vendor marketing segment with unmeasured accuracy. Calling it "far left" claims an ideology measurement the data cannot support. |
| Left | Outlook = Somewhat liberal | **No** as a belief label, for the same reason. Registration D is not "left" either. |
| Lean left | Lean band Lean D | **No.** Legacy lean is documented as not a validated belief measure, and its bands are party-named (Lean D), not left/right. |
| Centrist | Outlook = Middle outlook | **No** as a belief label. |
| Independent | Registration U (Unaffiliated) | **Partly.** Supported as "Unaffiliated registration". Not supported as "independent" in the ideological sense. Oregon also has a registered Independent Party; whether its members are the 30,984 rows coded U but rolled up to O is unverified (Q2). |
| Green | Registration G | **Yes**, as registration. |
| Libertarian | Registration L | **Yes**, as registration. |
| Undecided | Canvass result U | **Yes**, for people a canvasser recorded as Undecided. Absent for everyone not yet knocked. |
| Lean right | Lean band Lean R | **No**, as for lean left. |
| Right | Outlook = Somewhat conservative | **No** as a belief label. |
| Far right | Outlook = Very conservative | **No**, as for far left. |

Result: of the eleven labels, three are honestly supportable (Green, Libertarian, Undecided) and one partly (Independent, as Unaffiliated). The six spectrum positions have no field that measures them. The UI must not show the words far left, left, lean left, centrist, lean right, right or far right.

### 1.2 Scope by dimension

- **Registration party: the basis (D1).** Same data the walk list already returns per voter (`walk_store.py:321-324` includes `party`), so the count query needs no join to another store. Choices: Democrat, Republican, Unaffiliated, Green, Libertarian, Working Families, Other, plus T once labeled (Q3).
- **Canvass result:** proposed as an optional second dimension for "Undecided", which registration cannot express. Not covered by D1; needs an owner yes and a staleness rule (Q5).
- **Support for the candidate: second dimension (D3).** A two-handle spectrum slider over Terrier's support score, with presets Leans R, Persuadable (D4), Leans D and Everyone, plus a no-score checkbox (D5).
- **Vendor Outlook, ideology scores and the raw vendor party columns: not used directly (D1).** Partisan and presidential support reach the filter only through the support score (D3).
- **Modeled lean: not used (D1).** The repo also says it is not a belief measure and is not used for filtering.

### 1.3 Combining selections

- Within one dimension, selected values are OR-ed (Registration G **or** L).
- Across dimensions, include selections are AND-ed (Registration U **and** Canvass U).
- Exclude selections are OR-ed: a person matching any exclude value is an excluded person.
- Unknown values never match an include value. A blank registration code is shown as its own "No party code" choice, so a user can include or exclude it explicitly. A person with no canvass result matches only an explicit "No result yet" choice.
- With no include selections, every voter is included (the filter then only subtracts).

## 2. Door rule

### 2.1 Door identity

A door is a distinct `(stop_id, unit)` pair, exactly as `WalkStore.street_counts` defines it (`server/research/walk_store.py:361-378`): person rows are grouped by `street_id, stop_id, unit`, and each group is one door. A stop is a house-number point on a street (`tools/build_walk.py:82-83`). Doors with no registered voter do not exist on this basis, which is why voter-basis counts are always lower than the public estimate for the same area.

### 2.2 Mixed households

A door is counted when:

1. **Any** resident at the door is an included person, **and**
2. **no** resident at the door is an excluded person (**exclude wins**).

Rationale: a door is knocked once, whoever answers. If the user excluded a group, knocking a door where that group lives contradicts the exclusion; if they included a group, one matching resident is enough reason to knock.

Cost of exclude-wins: in a mixed household it drops included residents along with the excluded one. The panel therefore reports, per area, the number of doors removed only because of an exclude match in a mixed household, so the effect is visible. Whether to instead drop only the excluded person and keep the door is open question Q1.

### 2.3 Clipping to the polygon

Counts are clipped to the drawn polygon at the stop, not to the whole street. A door counts when its stop's `lon, lat` is covered by the polygon (same `polygon.covers(Point(...))` test the public basis uses at `area_street_views.py:241`). A street that crosses the boundary contributes only its doors inside. Stops without coordinates are counted separately as "unplaced" and are not grouped.

The existing `street_counts(ids, area)` filters by administrative area, not polygon, so the voter-basis query needs a polygon-aware variant. Sketch (not final):

```sql
-- doors per street inside the polygon, after the door rule
SELECT street_id, COUNT(*) AS doors
FROM (
  SELECT p.street_id, p.stop_id, p.unit,
         MAX(<include predicate>) AS any_in,
         MAX(<exclude predicate>) AS any_out
  FROM person p JOIN stop s ON s.id = p.stop_id
  WHERE s.id IN (<stop ids covered by the polygon>)
  GROUP BY p.street_id, p.stop_id, p.unit
)
WHERE any_in = 1 AND any_out = 0
GROUP BY street_id
```

Predicates are built from a fixed whitelist of dimension/value pairs, never from free text. Registration is already on `person`; canvass result, if approved, is joined server-side by voter key and never leaves the server per person.

### 2.4 What reaches the browser

Per street only: `street_id`, label, matching doors, total voter doors in the polygon, doors dropped by exclude-wins, and a center point for grouping. No person rows, voter keys, party, Outlook or canvass values per person or per door.

Small counts can still identify a household (one matching Green door on a short street). Whether to suppress or round street counts below a floor is open question Q7. `HANDOFF_2026-10-01.md:98` already notes that "The no-floor table is local-only".

## 3. Grouping

### 3.1 Input

`groupPublicStreets` (`publicStreetGroups.ts:41`) groups `PublicStreet` records (`api/publicStreets.ts:9-12`) using `id`, `estimated_doors` and a center from `address_points`. The algorithm itself is basis-neutral: target = (min + max) / 2, four projection axes, a DP over contiguous windows whose door sum lies in [min, max], whole streets kept whole, leftovers re-partitioned (`publicStreetGroups.ts:40-81`).

The voter basis reuses that algorithm unchanged, fed with matching-door counts instead of estimates. The change needed is to type the input on a neutral count field plus an explicit basis, rather than overloading `estimated_doors` with voter-file numbers:

```ts
type StreetCount = { id: string; label: string; center: [number, number]; doors: number }
type Basis = 'public-estimate' | 'voter-file'
groupStreets(streets: StreetCount[], basis: Basis, min: number, max: number)
```

The public path maps `estimated_doors` to `doors`. The voter path maps matching doors to `doors`. Every group and export carries `basis`.

### 3.2 Automatic recompute

Changing an include/exclude choice, the polygon or min/max re-queries the server for per-street counts (debounced, one request in flight, stale responses dropped) and regroups on the client. Grouping is O(4·n·max) (`publicStreetGroups.ts:50`), cheap for the 5,000-street cap. Streets whose matching doors drop to zero leave the groups and appear in the not-grouped list with reason "No matching doors". Streets whose matching doors exceed max stay listed as oversized, as today.

### 3.3 Labeling the two bases

The two bases must be impossible to confuse:

- **Mode switch** at the top of the panel: "Public estimate" / "Voter file". Not a checkbox buried in the filter.
- **Unit word:** public basis keeps "estimates" ("Group 3 · 128 estimates", `AreaStreetDiscovery.tsx:114`). Voter basis uses "voter doors" ("Group 3 · 128 voter doors"). The bare word "doors" is never used alone.
- **Basis line** on the summary, groups and every exported file: "Basis: voter file, doors with ≥1 registered voter matching the filter below, clipped to the drawn area" plus the filter in words, e.g. "Include registration Green or Libertarian; exclude canvass result Opposed". Public basis keeps its `count_basis` line and `PUBLIC_SCOPE_WARNING` (`publicStreetGroups.ts:4`).
- **Colour/badge:** a distinct badge on voter-basis groups so a screenshot is unambiguous.
- **Export file names** carry the basis (`street-groups-voter-file.zip` vs `street-groups-public-estimate.zip`).
- **Registration wording:** choices read "Registered Green", "Registered Unaffiliated" and so on, never a spectrum word, so the label says what the data is.

All new label strings must pass `ADVISORY_PATTERN` (`field_labels.py:145-147`), enforced at import by `self_check` (`field_labels.py:198-209`). That rules out words such as target, priority, best, recommend, should, worth, persuasion, convert. Note the pattern is word-bounded, so "persuadable" would not trip `\bpersuad\b`; the one exception is the "Persuadable" preset label, allowed by the amendment in D4. The filter's own name stays "Audience filter" with "Include"/"Exclude" controls.

## 4. Governance gates

G1 and G2 are settled by D1 and D2. G3 and G4 are build requirements with one owner choice each. G5 is a build rule.

### G1. Person-level political profiling: settled by D1

Because the basis is registration only, the filter makes no individual political inference. The background below is kept for the record.

`REVIEW_HANDOFF_2026-10-01.md:16` records that inferred individual political lean and household political summaries were not completed or validated. Line 43 lists "Person-level or household political profiling implementation and validation" as left out. Lines 56-57 recommend aggregate geographic reporting "with no individual political inference" and continuing review "without developing the sensitive profiling functions".

That file is marked historical and superseded for the synthetic preview (`REVIEW_HANDOFF_2026-10-01.md:3`), on the basis that the preview was test-dummy work. This feature runs on the real voter file, so the supersession does not obviously cover it.

How the dimensions sit against that line:

- Registration party and canvass result are recorded facts already shown per voter on walk lists. Filtering doors by them is a selection over existing data, not new inference.
- Vendor Outlook is individual political inference. Filtering doors by it is the profiling the review held back, even if only counts reach the browser.
- Modeled lean: excluded (section 1.2).

Outcome: Outlook is out of scope (D1).

### G2. Counsel check: waived by D2

`HANDOFF_2026-10-01.md:98`: "Counsel check on voter-file and profiling use before any sharing." `WALK_LIST_HANDOFF_2026-10-02.md:108` recorded a waiver for walk lists. D2 extends that to this feature, including exports to field staff.

### G3. Role gating

The public basis is local-administrator only (`@local_only` / `@local_only_post` on `area_street_views.py:272, 286`) with no audit. The voter basis reads person data and must instead match the walk views:

- `@manager_read` (`walk_views.py:164-166`): local admin, or on hosted deployments the `field-manager` group (`field_access.py:29`), with audit.
- Never `@field_read` (`walk_views.py:145-147`, `field-staff`): field staff receive walk lists, not the filter.
- Demo mode returns 403, as `_guard` does for the public basis (`area_street_views.py:255-269`).

Decision needed: hosted for field managers, or local-only first?

### G4. Audit logging

`audited` (`walk_views.py:155-161`) calls `FA.audit(request, 'walk.build.' + function.__name__)`, and `field_access.audit` (`field_access.py:86-90`) writes an `AuditEvent` only when `settings.HOSTED`. Locally nothing is written. Also, `audited` logs before the body runs, so failed requests are logged too.

Proposed: each voter-basis count request logs action `walk.audience.count` with detail: dimensions and values used for include and exclude, a hash of the polygon plus its area, number of streets returned, total matching doors. Never voter keys, names or per-door values. Decision needed: should this feature also log locally, since local is where the no-floor data lives?

### G5. Label rule

All labels pass `ADVISORY_PATTERN`, amended per D4 to allow the single label "Persuadable", and the spectrum words in section 1.1 are not used. This is a build rule, not an owner decision, listed so reviewers check it.

## 5. Open questions for the owner

Answered 2026-10-22: Outlook and other modeled leanings are out (D1); no independent or counsel review is needed (D2).

1. **Door rule:** exclude-wins at the door (recommended), or drop only the excluded person and keep the door if an included person remains?
2. **Unaffiliated vs Independent Party:** 30,984 rows are raw code U but vendor rollup O. If these are Independent Party of Oregon members, should they be split out as their own choice? That needs a code from the source, because Terrier stores only the raw U.
3. **Code T:** 605 rows carry T, which has no label in `PARTY_CODES` (`field_labels.py:134-135`). What party is it, so it can be labeled?
4. **Other and Working Families:** show O and W as their own choices (proposed), or fold them into an "Other parties" choice like `reg_group_of` does (`field_labels.py:150-152`)?
5. **Undecided:** add the canvass result as a second dimension? If yes: newest non-null disposition across all lists, ignored after how many days, and should MV (Moved) remove the person from every count?
6. **G3:** hosted for `field-manager`, or local-admin only for the first version?
7. **Small counts:** suppress or round per-street matching-door counts below a floor (for example 5)? If yes, suppressed streets still group but show "fewer than 5".
8. **Exports:** voter-basis groups export as street-name CSVs like the public basis, or only as Terrier walk lists (which enforce the stop-level boundary that a street-name CSV cannot, per `PUBLIC_SCOPE_WARNING`)?
9. **Audit locally (G4):** add local audit for this feature?
10. **"Persuadable" wording:** answered by D4 (amend the rule to allow that one label).
11. **Far-right `sei_*` columns:** confirm whether they are person-level or area-level and what `sei_party_trust_range` encodes, before any use.
12. **Support coverage for OR-05:** does the current OR-05 build write `support` rows? If not, D3 needs a support build for OR-05 first.
