"""Read public-legislator methodology as documentation, never as voter predictors."""
import argparse, ast, csv, hashlib, json
from datetime import datetime, timezone
from pathlib import Path

def literal(path, name):
    tree=ast.parse(path.read_text(encoding='utf-8-sig'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError(f'{name} missing')

def audit(source, csv_path, target):
    page=source/'cu-member-desk.html';text=page.read_text(encoding='utf-8');start=text.index('const D=')+len('const D=');d,_=json.JSONDecoder().raw_decode(text[start:])
    policy=source/'fec/engine/pillar_policy.py';core=source/'fec/moneyball_core.py';dp=source/'fec/district/demographic_potential.py'
    meta=d['demographic_potential_meta'];pillars=literal(policy,'PILLAR_WEIGHTS');importance=literal(core,'IMP');evidence=literal(policy,'LABEL_EVIDENCE_WEIGHTS')
    footprint=literal(dp,'SCORE_WEIGHTS_FOOTPRINT')
    if meta['score_version']!='3.0' or meta['score_weights']!=footprint:raise ValueError('Footprint code/page drift')
    page_weights={2:d['weights']['leg'],3:d['weights']['press'],4:d['weights']['comm'],5:d['weights']['demo']}
    if pillars!=page_weights:raise ValueError('Ranking code/page drift')
    if abs(sum(footprint.values())-1)>1e-9 or abs(sum(pillars.values())-1)>1e-9 or abs(sum(importance.values())-17.45)>1e-9:raise ValueError('Unexpected criterion weights')
    names={2:'Legislative action',3:'Press sentiment',4:'Influence',5:'Demographics'}
    def rows(vals):return [{'name':k,'weight':v} for k,v in vals.items()]
    result={'schema':'criteria-audit-v1','audited_at':datetime.now(timezone.utc).isoformat(),'interpretation':'Existing Melange measures only. No voter-level predictors or political-support probabilities are computed.','support_likelihood':'Not measured','persistent_voices':'Not measured','sources':[{'name':p.name,'sha256':hashlib.file_digest(p.open('rb'),'sha256').hexdigest()} for p in [page,policy,core,dp]],'systems':[
      {'id':'footprint','name':'Credit-union footprint','version':'3.0 · footprint','weight_kind':'Judgmental percentage weights','total':1.0,'formula':meta['score_formula'],'qualification':meta['method'],'missing':'Existing Melange method uses neutral 0.50 and lowers evidence coverage. This app does not compute this score from the CSV.','normalization':'Tie-aware percentiles within qualifying district or state markets; full precision.','rows':[{'key':x['key'],'name':x['label'],'weight':x['weight'],'definition':x['raw_formula'],'caveat':x['caveat'],'csv_match':'Absent'} for x in meta['component_definitions']]},
      {'id':'ranking','name':'Congressional ranking','version':'Current pillar policy','weight_kind':'Percentage weights','total':1.0,'formula':'0.45 × legislative + 0.10 × press + 0.40 × influence + 0.05 × demographic','normalization':'Legislation and demographics use percent ranks; press and influence divide by their respective maxima. Preserve source normalization.','missing':'The existing ranking is documented, not applied to source profiles.','rows':[{'name':names[k],'weight':v,'csv_match':'No comparable voter measure'} for k,v in pillars.items()]},
      {'id':'evidence','name':'Evidence within an issue','version':'Current label policy','weight_kind':'Nominal relative weights','total':None,'formula':'Normalize source weights over available evidence. Nominal 0.10 media is not necessarily 10% of the result.','normalization':'Defensive categories: Interchange, CU Tax Status, Credit Card Mandates.','missing':'Absent evidence is not treated as opposition.','rows':[{'name':k.title(),'weight':v,'defensive_weight':evidence['defensive'][k]} for k,v in evidence['standard'].items()]},
      {'id':'importance','name':'Issue importance','version':'18-category IMP table','weight_kind':'Relative weights','total':17.45,'formula':'Weighted mean across observed issue positions; relative weights do not represent fixed percentage contributions.','normalization':'The category "No recorded position" is excluded from the overall stance mean; retain the source semantic ladder.','missing':'Unknown is excluded, not scored as zero support.','rows':rows(importance)}],
      'drift':[{'source':'DEMOGRAPHIC_POTENTIAL_METHOD.md','finding':'The eight-component growth-room document is not the active four-component footprint mode.'},{'source':'MATHEMATICAL_SPECIFICATION.md','finding':'The documented 25/30/40/5 ranking differs from the inspected current 45/10/40/5 code and page.'}],
      'rejected_proxies':[{'field':'sai_labor_union_support_score','reason':'Labor-union support is not credit-union support.'},{'field':'sai_activist_score','reason':'Modeled activism does not document repeated CU engagement.'},{'field':'sci_ct_volunteer_chtyorg','reason':'Does not establish CU-specific participation or persistence.'},{'field':'svi_vh_* / svi_voter_frequency','reason':'Election participation does not measure CU support.'},{'field':'Party / ideology / religion / race / moral-model fields','reason':'Excluded from descriptive metric calculations.'}]}
    target.mkdir(parents=True,exist_ok=True);(target/'criteria.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    with csv_path.open(encoding='cp1252',newline='') as f:headers=next(csv.reader(f))
    allowed={'sii_persist_id':'Local deduplication','sii_statara_id':'Local identity validation','svi_registration_congressional_district':'Supplied district scope'}
    for b in ['sii','svi_registration']:
        for s in ['state','zip','censusid','latitude','longitude','geolevel']:
            allowed[b+'_'+s]='Local geography processing; only aggregate output is shared'
    with (target/'field_dictionary.csv').open('w',encoding='utf-8',newline='') as f:
        writer=csv.writer(f);writer.writerow(['column','local_processing','purpose','predictor_weight','hosted_profile_value'])
        for h in headers:writer.writerow([h,'included' if h in allowed else 'excluded',allowed.get(h,'Not used in this descriptive phase'),'Not assigned','Never published'])
    print(json.dumps({'systems':len(result['systems']),'dictionary_columns':len(headers),'weights_verified':True}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--melange',required=True);p.add_argument('--csv',required=True);p.add_argument('--output',required=True);a=p.parse_args();audit(Path(a.melange),Path(a.csv),Path(a.output))
