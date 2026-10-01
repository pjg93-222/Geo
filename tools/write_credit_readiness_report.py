"""Write usable-source guidance and configuration from the observed readiness data."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def rule(level,coverage):
    return next(x for x in level['coverage_rule_sensitivity'] if x['minimum_coverage']==coverage)


def main():
    path=ROOT/'data/credit-observed-readiness.json'
    d=json.loads(path.read_text(encoding='utf-8'))
    if d['script_sha256']!=hashlib.sha256((ROOT/'tools/build_credit_readiness.py').read_bytes()).hexdigest():
        raise ValueError('Regenerate the readiness data after code changes')
    modes={'CC index':'extended_with_bounds','Card-user share':'core_completeness',
           'Credit repair':'source_context_only','Credit report':'source_context_only'}
    lines=['# A usable partial credit-field dataset', '',
           '**Use the observed area summaries, choose an appropriate geography, and label completeness. Full imputation is unnecessary for descriptive use.** '
           'The prepared sidecar adds field coverage, record counts, completeness tiers, and worst-case missing-code bounds to existing published aggregates. '
           'It contains no predicted replacement values.', '',
           '## Recommended working configuration', '',
           '- Use **ZCTA (Census ZIP-like areas)** for the default overview. It has observed data over much more of the mapped extract than blocks do.',
           '- Use **card-user source share** as the first core-completeness layer: at least 200 valid profiles and 90% field coverage.',
           '- Use **CC source-code share** in an extended layer with at least 200 valid profiles and 85% coverage; display its wider missingness bounds.',
           '- Keep **credit-repair and credit-report source-code shares** as observed geographic context. Avoid precise whole-population rankings from these sparse signals.',
           '- At block zoom, show only that block’s own published source value and coverage. A ZCTA value stays labeled as a ZCTA summary; it is never copied down to a block, household, or person.',
           '- Keep unavailable and suppressed states distinct. Compare like geography, address basis, source field, and snapshot.', '',
           'These are operating rules for a partial source dataset, not a claim that vendor estimates equal verified financial behavior. '
           'The 90% and 85% cutoffs cap missing-code ranges at 10 and 15 percentage points; they are not statistical confidence levels. '
           'The 200-record floor limits a single code flip’s effect on the valid-record share to at most 0.5 percentage points, holding the denominator fixed.', '',
           '## Coarser geography recovers useful observed coverage', '',
           '| Field | Published blocks | Mapped profiles in published block areas | Published ZCTAs | Mapped profiles in published ZCTA areas | Valid field coverage within published ZCTAs |',
           '|---|---:|---:|---:|---:|---:|']
    for field in d['fields'].values():
        b=field['levels']['block'];z=field['levels']['zcta']
        lines.append(f"| {field['label']} | {b['published_areas']} | {b['published_area_share_of_partition_profiles_pct']:.2f}% | {z['published_areas']} | {z['published_area_share_of_partition_profiles_pct']:.2f}% | {z['field_coverage_weighted_in_published_areas_pct']:.2f}% |")
    lines += ['', '**Geographic coverage and field completeness are different.** An area can have a publishable summary while some of its source records lack the field. '
              'Coarsening reduces suppression; it does not recover those missing records. Each level is a separate partition; ZIP, ZCTA, and block counts must not be added together.', '',
              f"The placement pipeline maps {100*d['geographic_placement_coverage']:.2f}% of accepted source profiles. All geographic-coverage percentages above use the mapped extract, "
              'not every imported profile or the full electorate. ZCTAs can be partial within the supplied district extract.', '',
              '## Practical completeness tiers', '',
              '| Field | ZCTAs at ≥90% / ≥200 valid | Share of mapped profiles in those areas | ZCTAs at ≥85% / ≥200 valid | Share of mapped profiles in those areas |',
              '|---|---:|---:|---:|---:|']
    for field in d['fields'].values():
        z=field['levels']['zcta'];core=rule(z,.9);extended=rule(z,.85)
        lines.append(f"| {field['label']} | {core['areas']} | {core['share_of_partition_mapped_profiles_pct']:.2f}% | {extended['areas']} | {extended['share_of_partition_mapped_profiles_pct']:.2f}% |")
    lines += ['', 'Core card-user coverage gives a substantive starting dataset. CC has useful observed summaries but little support at the stricter 90% completeness cutoff. '
              'The extended tier makes that tradeoff visible instead of marking both tiers as equally complete.', '',
              '## Bounds instead of invented values', '',
              'If every mapped profile had a valid source code, the possible whole-area code share is bounded as follows. For observed valid-code share p, valid records n, and mapped records N:', '',
              '```text', 'whole-area lower bound = p × n / N', 'whole-area upper bound = (p × n + N − n) / N', '```', '',
              'For example, 50% nonzero codes among 80 valid records out of 100 mapped records gives a whole-mapped-area range of **40%–60%**. '
              'The missing 20 records could all be zero or all be nonzero. This requires no missing-at-random assumption. '
              'A fully observed area collapses to one value. An observed zero stays a valid source code.', '',
              'These are deterministic bounds on literal source-code shares. They do not quantify vendor model error, code-definition ambiguity, geocoding mistakes, '
              'or representativeness beyond this extract. Vendor definitions and vintages still need verification before assigning behavioral meanings to the codes.', '',
              '## Make only comparisons the data supports', '',
              'For whole-mapped-area source-code shares, a strict ordering is supported regardless of the missing codes only when the two bounds do not overlap. '
              'Otherwise present overlapping ranges rather than a precise whole-population ranking. Raw observed shares can still describe the available source records.', '',
              '| Field / tier | Eligible ZCTAs | Pairs with guaranteed strict ordering | All eligible pairs |',
              '|---|---:|---:|---:|']
    for field in d['fields'].values():
        threshold=.9 if field['label']=='Card-user share' else .85
        r=rule(field['levels']['zcta'],threshold);pairs=r['strict_ordering']
        lines.append(f"| {field['label']} / {int(threshold*100)}% | {r['areas']} | {pairs['guaranteed_strictly_ordered_pairs']} | {pairs['pairs']} |")
    lines += ['', 'These counts concern source-code shares within this extract. Overlapping bounds do not support a strict ordering of the whole-mapped-area shares.', '',
              '## Prepared artifacts', '',
              '- `data/credit-observed-readiness.json`: published geographic cells with original source shares, counts, coverage, bounds, and completeness tiers; includes threshold sensitivity from 75% to 95%.',
              '- `CREDIT_OBSERVED_USE_POLICY.json`: recommended per-field display use and minimum coverage rules.',
              '- `tools/build_credit_readiness.py`: reproducible standard-library builder with six regression checks.', '',
              'This is a sidecar for analysis and future display integration. It does not replace the source snapshot or automatically alter the live map. '
              'The prediction workflow remains disabled for genuine gaps.', '',
              'To reproduce from the app folder:', '', '```powershell',
              '& .local/model-study/.venv/Scripts/python.exe tools/build_credit_readiness.py --test',
              '& .local/model-study/.venv/Scripts/python.exe tools/build_credit_readiness.py',
              '& .local/model-study/.venv/Scripts/python.exe tools/write_credit_readiness_report.py',
              '```', '',
              f"Source rows SHA-256: `{d['source_rows_sha256']}`", '',
              f"Readiness sidecar SHA-256: `{hashlib.sha256(path.read_bytes()).hexdigest()}`", '']
    policy={'schema':'credit-observed-use-policy-v1','snapshot':d['snapshot'],'basis':'registration',
            'overview_level':'zcta','readiness_sidecar':'data/credit-observed-readiness.json',
            'imputation_enabled':False,'geographic_downfill_enabled':False,
            'core_rule':d['default_rule'],'extended_rule':d['extended_rule'],
            'comparison_rule':'Only claim strict whole-mapped-area ordering if missing-code bounds are disjoint.',
            'fields':{key:{'use':modes[field['label']],
                           'value':'observed_source_code_share',
                           'display_required':['valid_profiles','mapped_profiles','field_coverage','readiness_tier','whole_mapped_source_code_bounds'],
                           'minimum_field_coverage':.9 if field['label']=='Card-user share' else .85 if field['label']=='CC index' else None,
                           'minimum_valid_profiles':200 if field['label'] in ['Card-user share','CC index'] else None,
                           'whole_population_ranking':False if field['label'] in ['Credit repair','Credit report'] else 'only_disjoint_bounds'}
                      for key,field in d['fields'].items()}}
    (ROOT/'CREDIT_OBSERVED_USE_POLICY.json').write_text(json.dumps(policy,indent=2),encoding='utf-8')
    report=ROOT/'CREDIT_DATA_READINESS.md';report.write_text('\n'.join(lines),encoding='utf-8')
    print(report)


if __name__=='__main__':main()
