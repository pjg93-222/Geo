# Terrier synthetic data coherence audit — 2026-10-01

## Scope and result

Audited the active standalone Terrier fixture engine against its preceding committed version (`1c3b7c7`). This covers all **109,150 generated people in 45,000 households**. No original voter records, real contact database, or backend on ports 8765/8766 was read or changed.

The old generator independently chose a uniform affiliation and a uniform outlook, regardless of registration. This produced many combinations that are possible individually but disproportionately common in this authored demo. The replacement selects joint categorical dummy scenarios. Its scenario weights are design choices for test coverage, **not estimates of the electorate**, learned political beliefs, or quotas imposed on a cohort.

Registration, self-described affiliation, and outlook remain distinct. A conservative Democrat and a person registered unaffiliated who identifies with Democrats can be valid; these cases are retained. No political response is fitted to a score, standardized, re-ranked, or changed when a filter or balanced view is loaded. Ethical beliefs remain absent from this fixture schema. Conflicting selections within one opinion category now display **Multiple responses**; a missing response remains **Unknown**.

| Full-cohort check | Before | After |
| --- | ---: | ---: |
| Democratic affiliates | 27,267 | 23,943 |
| Democratic affiliates with a conservative outlook | 10,965 (40.21%) | 1,476 (6.16%) |
| Democratic affiliates, conservative outlook, registered unaffiliated | 5,968 | 516 |
| Major-party registration with opposite major-party self-description | 11,602 | 471 |
| Adult count greater than household size | 20,729 | 0 |
| Adult count different from the displayed household roster | 84,699 | 0 |
| Owner/renter and homeowner fields contradict one another | 8,750 | 0 |

The first four rows measure fixture composition; they are not counts of logical errors. The final three rows are actual structural contradictions, now removed.

## Household remedy and index effects

Every member of this demo's household roster is aged 18 or older. Household size and adult count now equal the roster length; children is an explicit numeric **0**, replacing generated, unexplained letter codes. Tenure, homeowner status, dwelling type, and the three household count fields are shared by household rather than generated independently for each person.

The six shared inputs and their contribution exposures are fixed before the remaining illustrative field values are generated. Household size and adult count use the fixed synthetic population's midrank distribution. Constant zero children has a constant midrank exposure of 0.5. Tenure uses the existing homeowner/renter/unknown mapping (1/0/0.5); dwelling uses its existing category mapping. These are structural index inputs, not opinion transformations.

An illustrative CUA or Sphere of Influence target outside the range permitted by the fixed inputs moves to the nearest feasible boundary. All other target scores remain unchanged, and the overall formula remains 40% CUA + 35% Participation + 25% Sphere of Influence.

| Score | People changed | Largest absolute change |
| --- | ---: | ---: |
| Credit Union Alignment | 5,234 | 18.0068 points |
| Participation | 0 | 0 |
| Sphere of Influence | 695 | 6.9384 points |
| Overall standing | 5,767 | 7.2239 points |

The independently generated area statistics still do not summarize these individual profiles; the Method page discloses that fixture limitation.

## Verification and files

- `npm --prefix frontend run test:unit`: **21 tests passed**, including preservation of valid crossover opinions and explicit conflicting categories.
- `node frontend/scripts/verify-test-fixtures.mjs`: passed all full-cohort count, tenure, shared-fact, category exclusivity, and score-contribution checks; also passed deterministic profiles, filter/cohort invariance, pagination, balanced selection, and all four static geographic layers.
- Full-cohort audit after the remedy: every person has exactly one outlook; at most one affiliation flag; conservative/liberal flags agree with the selected outlook; every household's shared fields agree; every CUA/Participation/Sphere score reconciles to its field contributions.
- Implementation: `frontend/src/v3/api/fixtures.ts` and `frontend/src/v3/lib/opinions.ts`.
- Regression checks: `frontend/scripts/verify-test-fixtures.mjs` and `frontend/tests/v3/opinions.test.ts`.
- Local machine-readable evidence: `_build/data-coherence/before.json`, `_build/data-coherence/score-changes.json`, and `_build/test-dummy-verification/fixture-checks.json` (ignored build artifacts).

The numeric lean fields retained for the legacy fixture response shape are not displayed or used to rank, filter, or calculate overall standing. UI code must continue to use the categorical opinion responses and registration proportions.
