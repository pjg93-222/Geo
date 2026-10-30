# Pathfinder research display

The owner selected Higgsfield option A on October 30, 2026: criteria on the left, the existing detailed route/map in the center, and aggregate context on the right. This change follows that layout using Terrier's existing Geist font, cream surfaces, plum controls, teal data bars and shared header.

The owner identifies the datasets as wholly synthetic records for educational simulations, with real street names. The existing owner declaration in `docs/district-testsets/2026-10-21/README.md` remains unchanged. No additional provenance audit is part of this implementation.

## Implementation plan and behavior

1. Preserve the existing route solver, turf algorithm, street geometry, individual door coordinates, even/odd control, caps, saved-list behavior and exports. The filter changes the records entering those algorithms; it does not replace the algorithms with area sampling or centroid routing.
2. Add categorical affiliation controls in the existing route builder. Include Unaffiliated, Independent Party, named third parties, other source codes and unknown/no code separately. Source labels take precedence; do not treat independents as a point on a D/R scale. Existing support and plan sliders remain intact.
3. Carry a canonical `affiliation` query through route generation, block planning, trimming, browser URLs and saved lists. Omission means the original unfiltered behavior. `empty` means no selected categories. `none` means the source has no recorded code. Other codes are distinct, validated, sorted and passed as SQL parameters.
4. Filter individual simulation records before routing. A mixed household retains matching records at the same door/unit; unmatched records are omitted. A door with no matching records contributes no routed stop. Existing profile restrictions, when selected, intersect with this affiliation selection.
5. Show composition for the displayed list/sheet next to its map; before a route exists, label the context as district-wide. Changes to criteria show a pending notice until regeneration. Missing counts are unavailable, not fabricated zeros. Revisit lists describe their inherited roster.
6. Test the query and category round trips, independently named parties, mixed households, parameter validation, saved-list persistence, empty selections, trimmed plans and route equivalence for an all-category selection. Build the actual frontend and exercise the built screen at desktop, laptop and phone widths using invented fixtures.
7. Review the diff, commit and push this branch. Report source delivery separately from a signed desktop release.

## Interface and compatibility

- Existing navigation: **Walk list → Pathfinder**. This is the same route workspace.
- Affiliation filtering applies to the **Streets** source. Imported addresses retain their existing source data and behavior, with an explicit explanation in the context panel.
- Checkbox counts describe the district snapshot, not the pending route. The context panel describes the displayed list.
- Unselected categories and filtered people are not deleted from the dataset.
- Unknown codes stay visible as `Code X`; Independent Party is `I`, while Unaffiliated remains `U`. If the snapshot has no `I` records, its district count is zero.
- The feature does not require a support model for ordinary street routes. Existing model requirements for the existing block planner remain unchanged.
- Saved rosters remain fixed. Regeneration uses current criteria; downloading the visible route preserves its current contents.

## Verification commands

`npm run build`, `npm run test:unit`, and `node scripts/verify-pathfinder.mjs` in `frontend`.

`python manage.py test server.research.test_route_affiliation server.research.test_walk_route server.research.test_walk_api server.research.test_walk_plan --noinput` from the repository root, with a full development Django installation. The bundled desktop runtime omits Django's test package; tests use an isolated copy of the same Django version.
