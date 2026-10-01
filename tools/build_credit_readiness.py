"""Build an observed-only credit-field readiness sidecar from a fixed aggregate ZIP.

No model fits, raw-person reads, hidden-cell reconstruction, or app mutation.
Missing-value bounds concern literal source-code shares, not true financial traits.
"""
import argparse
import hashlib
import json
import math
import unittest
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
EXPECTED_ROWS='4c6e30b04ab5f30523c30117b27d9953c86826f2ae61961a54c1b99fd578610f'
FIELDS={
    'sci_cpi_cc_index__nonzero':'CC index',
    'sci_credit_card_user__code_1':'Card-user share',
    'sci_cpi_credit_repair_index__nonzero':'Credit repair',
    'sci_cpi_credit_report_index__nonzero':'Credit report',
}
LEVELS=['block','zcta','postal_zip']
MIN_VALID=200
MIN_COVERAGE=.90
EXTENDED_COVERAGE=.85


def bounds(value,valid,mapped):
    if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in [value,valid,mapped]):
        raise ValueError('Finite numeric source values required')
    if not (0<=value<=1 and 0<valid<=mapped and int(valid)==valid and int(mapped)==mapped):
        raise ValueError('Invalid share or source denominator')
    numerator=value*valid
    if not math.isclose(numerator,round(numerator),rel_tol=0,abs_tol=1e-7):
        raise ValueError('Share does not reconcile to a count')
    coverage=valid/mapped
    lower=round(numerator)/mapped
    upper=(round(numerator)+mapped-valid)/mapped
    return {'lower':lower,'upper':upper,'width_pp':100*(1-coverage)}


def observed_cell(row,target):
    if row['status']!='ok':return None
    cell=row.get('measures',{}).get(target)
    if not cell or len(cell)!=5:raise ValueError('Missing field definition on published base')
    value,valid,coverage,percentile,status=cell
    if status not in ['ok','suppressed','unavailable']:raise ValueError('Unknown measure status')
    if status!='ok':
        if any(v is not None for v in cell[:4]):raise ValueError('Hidden payload must be empty')
        return None
    interval=bounds(value,valid,row['count'])
    if not math.isclose(coverage,valid/row['count'],rel_tol=0,abs_tol=1e-12):raise ValueError('Coverage mismatch')
    return {'geographic_id':row['id'],'source_code_share':value,'valid_profiles':valid,'mapped_profiles':row['count'],
            'field_coverage':coverage,'whole_mapped_source_code_bounds':interval,
            'meets_completeness_rule':valid>=MIN_VALID and coverage>=MIN_COVERAGE,
            'readiness_tier':'core_completeness' if valid>=MIN_VALID and coverage>=MIN_COVERAGE else
                             'extended_with_bounds' if valid>=MIN_VALID and coverage>=EXTENDED_COVERAGE else 'source_description_only',
            'partial_geography_in_extract':row['partial']}


def quantile(values,q):
    if not values:return None
    values=sorted(values);pos=(len(values)-1)*q;lo=int(pos);hi=min(lo+1,len(values)-1)
    return values[lo]+(values[hi]-values[lo])*(pos-lo)


def pair_ordering(cells):
    ordered=0;total=0
    for i,a in enumerate(cells):
        x=a['whole_mapped_source_code_bounds']
        for b in cells[i+1:]:
            y=b['whole_mapped_source_code_bounds'];total+=1
            ordered+=x['lower']>y['upper'] or y['lower']>x['upper']
    return {'pairs':total,'guaranteed_strictly_ordered_pairs':ordered,
            'guaranteed_strictly_ordered_pct':100*ordered/total if total else None}


def analyze(rows,manifest):
    result={}
    seen=set()
    for row in rows:
        key=(row['basis'],row['level'],row['id'])
        if key in seen:raise ValueError('Duplicate geography in a partition')
        seen.add(key)
    for target,label in FIELDS.items():
        entry={'label':label,'field':target.split('__')[0],'levels':{}}
        for level in LEVELS:
            subset=[r for r in rows if r['basis']=='registration' and r['level']==level]
            cells=[x for row in subset if (x:=observed_cell(row,target)) is not None]
            qualified=[c for c in cells if c['meets_completeness_rule']]
            denominator=manifest['bases']['registration']['zcta_mapped' if level=='zcta' else 'mapped']
            count=sum(c['mapped_profiles'] for c in cells)
            if count>denominator:raise ValueError('Partition counts exceed declared mapped total')
            grid=[]
            for coverage in [.75,.80,.85,.90,.95]:
                use=[c for c in cells if c['valid_profiles']>=MIN_VALID and c['field_coverage']>=coverage]
                grid.append({'minimum_coverage':coverage,'areas':len(use),
                             'mapped_profiles_in_qualifying_areas':sum(c['mapped_profiles'] for c in use),
                             'share_of_partition_mapped_profiles_pct':100*sum(c['mapped_profiles'] for c in use)/denominator,
                             'max_missingness_bound_width_pp':100*(1-coverage),
                             'strict_ordering':pair_ordering(use)})
            entry['levels'][level]={'geographies_in_snapshot':len(subset),'published_areas':len(cells),
                'published_nonzero_areas':sum(c['source_code_share']>0 for c in cells),
                'mapped_partition_profiles':denominator,'mapped_profiles_in_published_areas':count,
                'published_area_share_of_partition_profiles_pct':100*count/denominator,
                'field_coverage_weighted_in_published_areas_pct':100*sum(c['valid_profiles'] for c in cells)/count if count else None,
                'field_coverage_p10':quantile([c['field_coverage'] for c in cells],.1),
                'field_coverage_median':quantile([c['field_coverage'] for c in cells],.5),
                'field_coverage_p90':quantile([c['field_coverage'] for c in cells],.9),
                'minimum_valid_profiles':min([c['valid_profiles'] for c in cells],default=None),
                'completeness_qualified_areas':len(qualified),
                'qualified_area_share_of_partition_profiles_pct':100*sum(c['mapped_profiles'] for c in qualified)/denominator,
                'strict_ordering_among_qualified':pair_ordering(qualified),
                'coverage_rule_sensitivity':grid,'observed_cells':cells}
        result[target]=entry
    return result


class Tests(unittest.TestCase):
    def test_complete_source_collapses_bounds(self):
        r=bounds(.3,200,200)
        self.assertEqual((r['lower'],r['upper'],r['width_pp']),(.3,.3,0))
    def test_incomplete_source_bounds(self):
        r=bounds(.5,80,100)
        self.assertEqual((r['lower'],r['upper']),(.4,.6))
        self.assertAlmostEqual(r['width_pp'],20)
    def test_extremes_stay_distinct_from_missing(self):
        self.assertEqual(bounds(0,80,100)['upper'],.2)
        self.assertEqual(bounds(1,80,100)['lower'],.8)
    def test_hidden_cells_cannot_create_bounds(self):
        row={'status':'ok','count':200,'measures':{'x':[None]*4+['suppressed']}}
        self.assertIsNone(observed_cell(row,'x'))
        row['measures']['x'][0]=.2
        with self.assertRaises(ValueError):observed_cell(row,'x')
    def test_noninteger_counts_fail(self):
        for args in [(.51,10,20),(.5,0,20),(.5,21,20),(.5,20,20.5),(float('nan'),20,20)]:
            with self.assertRaises(ValueError):bounds(*args)
    def test_touching_bounds_do_not_establish_strict_order(self):
        cells=[{'whole_mapped_source_code_bounds':{'lower':0,'upper':.2}},
               {'whole_mapped_source_code_bounds':{'lower':.2,'upper':.4}},
               {'whole_mapped_source_code_bounds':{'lower':.5,'upper':.6}}]
        self.assertEqual(pair_ordering(cells)['guaranteed_strictly_ordered_pairs'],2)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--test',action='store_true');args=parser.parse_args()
    if args.test:
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests))
        if not result.wasSuccessful():raise SystemExit(1)
        return
    source=ROOT/'data/co-cd005-39857d6b456f.zip'
    with zipfile.ZipFile(source) as z:
        raw=z.read('rows.json');manifest=json.loads(z.read('manifest.json'));rows=json.loads(raw)
    if hashlib.sha256(raw).hexdigest()!=EXPECTED_ROWS:raise ValueError('Unexpected aggregate snapshot')
    result={'schema':'credit-observed-readiness-v1','snapshot':manifest['snapshot'],'basis':'registration',
            'source_rows_sha256':EXPECTED_ROWS,'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'source_date':manifest['source_date'],'source_date_basis':manifest['source_date_basis'],
            'geographic_placement_coverage':manifest['bases']['registration']['coverage'],
            'default_rule':{'minimum_valid_profiles':MIN_VALID,'minimum_field_coverage':MIN_COVERAGE,
                            'maximum_missingness_range_width_pp':100*(1-MIN_COVERAGE),
                            'meaning':'Operational completeness filter, not a statistical confidence guarantee or validation of vendor meanings.'},
            'extended_rule':{'minimum_valid_profiles':MIN_VALID,'minimum_field_coverage':EXTENDED_COVERAGE,
                             'maximum_missingness_range_width_pp':100*(1-EXTENDED_COVERAGE),
                             'meaning':'Descriptive extension with visibly wider missingness bounds; not the core-completeness tier.'},
            'bounds_method':'For valid source-code share p, valid count n and mapped count N: [p*n/N, (p*n+N-n)/N]. Every unobserved source code may be zero or nonzero. Bounds concern this extract only and do not validate the vendor estimates.',
            'display_policy':{'default_geography':'zcta','default_value':'observed source-code share among valid profiles',
                              'show_coverage_and_bounds':True,'compare_only_same_basis_and_level':True,
                              'cross_geography_filling':False,'automatic_imputation':False,
                              'ranking_policy':'Prefer raw shares and bounds. Assert a whole-mapped-population strict ordering only when bounds do not overlap.'},
            'fields':analyze(rows,manifest)}
    out=ROOT/'data/credit-observed-readiness.json'
    out.write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    compact={v['label']:{level:{k:data[k] for k in ['published_areas','published_area_share_of_partition_profiles_pct','field_coverage_median','completeness_qualified_areas','qualified_area_share_of_partition_profiles_pct']} for level,data in v['levels'].items()} for v in result['fields'].values()}
    print(json.dumps(compact,indent=2))
    print(out)


if __name__=='__main__':main()
