"""Transparent composites of already-published AREA measures only.

The reference distribution is fixed to publishable ZCTAs in the same snapshot
and address basis. Missing/suppressed input cells cannot contribute to a score.
No personal record, contact attribute, or predicted missing value is used.
"""
from bisect import bisect_left, bisect_right
from tools.measures import MEASURE_BY_ID, midrank_percentiles

def component(field,suffix='mean',direction=1):
    return {'measure':field+'__'+suffix,'direction':direction}

def domain(name,weight,components,minimum=1):
    return {'name':name,'weight':weight,'components':components,'minimum':minimum}

DEFINITIONS = [
 {'id':'area_consumer','label':'Credit union issue indicators','description':'Relative prevalence of the selected consumer-source signals, grouped into digital access, auto/insurance, and credit/economic fields.',
  'domains':[
    domain('Digital',1/3,[component('sci_cpi_online_pay_index','nonzero'),component('sci_cpi_internet_buy_index','nonzero'),component('sci_ct_smrtphn_typeowns_iphone','code_1')]),
    domain('Auto and insurance',1/3,[component('sci_cpi_auto_index','nonzero'),component('sci_cpi_insurance_auto_index','nonzero'),component('sci_cpi_insurance_index','nonzero')]),
    domain('Credit and economy',1/3,[component('sci_cpi_credit_repair_index','nonzero'),component('sci_cpi_credit_report_index','nonzero'),component('sci_cpi_cc_index','nonzero'),component('sci_credit_card_user','code_1'),component('sai_recession_sensitivity_score')]),
  ],'notes':'Higher means more of these literal source signals relative to the ZIP-area reference. It does not measure agreement with credit union advocacy. Owner/renter, homeowner and dwelling codes remain separate measures because their categories have no verified common numeric ordering.'},
 {'id':'area_participation','label':'Participation index','description':'Combined area averages of vendor turnout, local-participation and early/absentee-voting estimates.',
  'domains':[
    domain('Turnout estimates',.5,[component('sai_'+name+'_score') for name in ('midterm_general_turnout','non_presidential_primary_turnout','offyear_general_turnout','presidential_general_turnout','presidential_primary_turnout')]),
    domain('Local participation',.25,[component('sai_activist_score'),component('sai_local_voter_score')]),
    domain('Early / absentee estimates',.25,[component('sai_evav_score'),component('sai_evav_timing_score')]),
  ],'notes':'Digital reachability code 50 remains available as a separate measure; its position relative to codes 0–10 is unverified, so it is not assigned a score direction.'},
 {'id':'area_civic','label':'Household and civic activity index','description':'Area-level household-size, giving and volunteering signals, with equal weight for each of the three groups.',
  'domains':[
    domain('Household size',1/3,[component('sci_number_of_adults_in_household')]),
    domain('Giving',1/3,[component('sci_charitable_donor','code_1'),component('sci_cpi_donor_index','nonzero'),component('sci_ct_dnr_contribamt_high','code_1')]),
    domain('Volunteering',1/3,[component('sci_ct_volunteer_chtyorg','code_1')]),
  ],'notes':'These are profile-weighted source signals, not a count of distinct households or a measured personal sphere of influence. Child-count categories and household IDs are not treated as numeric influence weights.'},
 {'id':'area_affiliation','label':'Recorded partisan balance','description':'Area balance of vendor party-affiliation, ideology and outlook signals. Lower values lean toward the Democrat/liberal source signals; higher values toward GOP/conservative signals.',
  'domains':[
    domain('Party-affiliation estimates',.5,[component('sci_ct_political_partyaff_gop','code_1'),component('sci_ct_political_partyaff_democrat','code_1',-1)],2),
    domain('Ideology indices',.25,[component('sci_cpi_conservative_index','nonzero'),component('sci_cpi_liberal_index','nonzero',-1)],2),
    domain('Political outlook',.25,[component('sci_ct_political_outlk_vconserv','code_1'),component('sci_ct_political_outlk_swconserv','code_1'),component('sci_ct_political_outlk_vliberal','code_1',-1),component('sci_ct_political_outlk_swliberal','code_1',-1)],4),
  ],'notes':'Both sides of each group must be available. Independent, middle-outlook and source registration categories remain separate measures. The result is relative source balance, not vote probability.'},
]
BY_ID={item['id']:item for item in DEFINITIONS}
CATALOG=[{'id':item['id'],'label':item['label'],'group':'Combined area indices','field':'Published area summaries','aggregation':'area_composite','units':'index','description':item['description'],
          'notes':item['notes'],'domains':item['domains']} for item in DEFINITIONS]
METHOD = {'version':'area-indices-1','reference':'Publishable ZCTAs in the same snapshot and address basis',
 'formula':'For each source measure, rank the area value against the ZCTA reference using 100 × (values below + half the ties) / reference areas. Reverse the named opposite-direction components. Average available components within each group, then apply the fixed group weights.',
 'missing':'Require at least 75% of the original component weight, the specified minimum in every group, at least 70% source-field coverage per used component, and at least five reference ZCTAs per component. Missing and suppressed cells are omitted, never replaced by zero.',
 'percentile':'The final score is 0–100. Its geographic percentile is a separate rank among available areas in the selected layer. A layer with one available area has no comparative percentile.'}

def reference(rows):
    result={}
    for row in rows:
        if row['status']!='ok':continue
        for key,cell in row.get('measures',{}).items():
            if cell[4]=='ok' and cell[2]>=.7:result.setdefault(key,[]).append(cell[0])
    return {key:sorted(values) for key,values in result.items()}

def evaluate(row,metric,refs,detail=False):
    definition=BY_ID[metric];parts=[];groups=[];available_weight=0.;coverages=[]
    for group in definition['domains']:
        scores=[];weight=group['weight']/len(group['components'])
        for part in group['components']:
            key=part['measure'];cell=row.get('measures',{}).get(key,[None,None,None,None,row['status'] if row['status']!='ok' else 'unavailable'])
            pool=refs.get(key,[]);reason=cell[4]
            if reason=='ok' and cell[2]<.7:reason='low_coverage'
            if reason=='ok' and len(pool)<5:reason='thin_reference'
            score=None
            if reason=='ok':
                lo,hi=bisect_left(pool,cell[0]),bisect_right(pool,cell[0])
                score=100*(lo+(hi-lo)/2)/len(pool)
                if part['direction']<0:score=100-score
                scores.append(score);available_weight+=weight;coverages.append(cell[2])
            if detail:parts.append({'measure':key,'label':MEASURE_BY_ID[key]['label'],'group':group['name'],'weight':weight,'direction':part['direction'],
                'status':reason,'value':cell[0] if cell[4]=='ok' else None,'field_coverage':cell[2] if cell[4]=='ok' else None,'normalized':score,'reference_areas':len(pool)})
        groups.append({'name':group['name'],'weight':group['weight'],'used':len(scores),'total':len(group['components']),
                       'value':sum(scores)/len(scores) if len(scores)>=group['minimum'] else None})
    eligible=row['status']=='ok' and available_weight>=.75-1e-10 and all(group['value'] is not None for group in groups)
    result={'value':sum(group['weight']*group['value'] for group in groups) if eligible else None,
            'valid_count':None,'field_coverage':min(coverages) if eligible else None,'index':None,
            'measure_status':'ok' if eligible else 'suppressed' if row['status']=='suppressed' else 'unavailable',
            'component_coverage':available_weight if row['status']=='ok' else None,
            'components_used':sum(group['used'] for group in groups) if row['status']=='ok' else None,
            'components_total':sum(group['total'] for group in groups)}
    if detail:result.update(components=parts,groups=groups,method=METHOD,notes=definition['notes'])
    return result

def summarize(rows,metric,refs):
    output=[]
    for source in rows:
        row={key:value for key,value in source.items() if key!='measures'}
        row.update(evaluate(source,metric,refs));output.append(row)
    valid=[row for row in output if row['measure_status']=='ok']
    if len(valid)>1:
        for row,rank in zip(valid,midrank_percentiles([row['value'] for row in valid])):row['index']=rank
    return output
