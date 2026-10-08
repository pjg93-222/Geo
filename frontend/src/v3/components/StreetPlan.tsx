// Street plan: ranks the streets (road spans) of the area picked above by one of the presets and the density weights, and sends the
// checked streets to the walk list as planned streets (spans=). Candidate districts only: the screen shows nothing without features.roi.
import {useEffect,useRef,useState} from 'react';
import {ChevronDown} from 'lucide-react';
import {getWalkPlan} from '../api/walk';
import type {WalkArea,WalkPlan} from '../api/walkTypes';
import type {FeatureRoi} from '../types';
import {countText} from '../lib/walk';
import {MAX_LIST_PEOPLE,MAX_PLAN_SPANS,PRESET_WORDS,pickUnderCap,planOptions,raceWords} from '../lib/plan';

const TWO=new Intl.NumberFormat('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
const areaKey=(a:WalkArea)=>[a.ss,a.sh,a.precinct].join('|');
const WEIGHTS=[['value','Value per 100 m'],['pop','Population density'],['voter','Voter density']] as const;

export function StreetPlan(p:{area:Required<WalkArea>;roi:FeatureRoi;race:string;density:boolean;busy:boolean;onWalk:(ids:number[])=>void}){
  const {roi}=p;
  const [preset,setPreset]=useState(roi.preset);
  const [w,setW]=useState({value:String(roi.weights.value),pop:String(roi.weights.pop_density),voter:String(roi.weights.voter_density)});
  const [result,setResult]=useState<{key:string;plan:WalkPlan}|null>(null);   // the plan, and the area it was ranked for
  const [checked,setChecked]=useState<ReadonlySet<number>>(()=>new Set());
  const [state,setState]=useState({loading:false,error:''});
  const job=useRef<AbortController|null>(null);
  useEffect(()=>()=>job.current?.abort(),[]);

  const noArea=!p.area.ss&&!p.area.sh&&!p.area.precinct;
  const plan=result?.key===areaKey(p.area)?result.plan:null;   // a plan ranked for another area is not shown
  const picked=plan?plan.spans.filter(s=>checked.has(s.span)):[];
  const people=picked.reduce((n,s)=>n+s.people,0);
  const over=picked.length>MAX_PLAN_SPANS||people>MAX_LIST_PEOPLE;
  const preview=PRESET_WORDS[preset]??roi.presets.find(x=>x.id===preset)?.formula??'';

  async function rank(){
    job.current?.abort();const ac=job.current=new AbortController();
    const key=areaKey(p.area);
    setState({loading:true,error:''});
    try{
      const next=await getWalkPlan(p.area,planOptions(preset,{...w,pop:p.density?w.pop:'0'}),ac.signal);
      if(ac.signal.aborted)return;
      setResult({key,plan:next});setChecked(new Set(pickUnderCap(next.spans)));setState({loading:false,error:''});
    }catch(e){
      if(ac.signal.aborted)return;
      setResult(null);setState({loading:false,error:e instanceof Error&&e.message?e.message:'The street plan could not be built.'});
    }
  }
  const toggle=(id:number)=>setChecked(c=>{const next=new Set(c);if(!next.delete(id))next.add(id);return next;});

  return <details className="walk-script walk-plan">
    <summary>Street plan</summary>
    <div className="walk-script-body">
      <p className="walk-hint">Ranks the streets of the area above by what a walker reaches per 100 m. Check the streets to walk, then build the list from them.</p>
      {p.race&&<p className="walk-plan-race">Race: {p.race}</p>}
      <label className="select walk-select"><span className="select-text" aria-hidden="true"><span className="walk-k">Plan:</span> {roi.presets.find(x=>x.id===preset)?.label??preset}</span>
        <ChevronDown className="select-chev" strokeWidth={1.75} aria-hidden="true"/>
        <select aria-label="Street plan ranking" value={preset} onChange={e=>setPreset(e.target.value)}>
          {roi.presets.map(x=><option key={x.id} value={x.id}>{x.label}</option>)}
        </select></label>
      <p className="walk-hint">{preview} Streets with fewer than {roi.min_doors} doors are left out, and a block under {roi.min_length_m} m counts as {roi.min_length_m} m.</p>
      <fieldset className="walk-weights">
        <legend>Weights</legend>
        {WEIGHTS.map(([k,label])=><label key={k}>{label}<input type="number" inputMode="decimal" min="0" step="any" value={w[k]} disabled={k==='pop'&&!p.density}
          onChange={e=>setW({...w,[k]:e.target.value})}/></label>)}
      </fieldset>
      <p className="walk-hint">Weights are relative. 1, 0, 0 ranks by value per 100 m alone.{!p.density&&' Population density is not built yet.'}</p>
      <button type="button" className="walk-button" onClick={()=>void rank()} disabled={state.loading||noArea} aria-busy={state.loading}>{state.loading?'Ranking…':'Rank streets'}</button>
      {noArea&&<p className="walk-hint">Pick a district or a precinct first.</p>}
      {state.error&&<p className="walk-note" role="alert">{state.error}</p>}
      {plan&&<>
        <p className="walk-plan-meta" role="status">{countText(plan.spans.length,'street')} ranked · {raceWords(plan.race_mode)}
          {plan.not_ranked.spans>0&&` · ${countText(plan.not_ranked.spans,'street')} left out`}
          {plan.matched&&plan.matched.scored<plan.matched.people&&` · ${plan.matched.scored.toLocaleString('en-US')} of ${plan.matched.people.toLocaleString('en-US')} people have a score`}</p>
        {plan.spans.length>0&&<>
          <p className="walk-plan-tools">
            <button type="button" className="walk-link" onClick={()=>setChecked(new Set(pickUnderCap(plan.spans)))}>Check the top streets that fit one list</button>
            <button type="button" className="walk-link" onClick={()=>setChecked(new Set())}>Clear checks</button></p>
          <table className="walk-plan-table" role="table" aria-label="Ranked streets">
            <thead role="rowgroup"><tr role="row">{['Walk','Rank','Street','Doors','People','Per 100 m','Score'].map(h=><th key={h} scope="col" role="columnheader">{h}</th>)}</tr></thead>
            <tbody role="rowgroup">{plan.spans.slice(0,MAX_PLAN_SPANS).map(s=><tr role="row" key={s.span} className={checked.has(s.span)?'is-checked':undefined}>
              <td role="cell" className="walk-plan-pick"><input type="checkbox" checked={checked.has(s.span)} onChange={()=>toggle(s.span)} aria-label={`Walk ${s.street}, rank ${s.rank}`}/></td>
              <td role="cell" className="walk-plan-rank">{s.rank}</td>
              <td role="cell" className="walk-plan-street">{s.street}</td>
              <td role="cell" data-label="Doors">{s.doors}</td>
              <td role="cell" data-label="People">{s.people}</td>
              <td role="cell" data-label="Per 100 m">{TWO.format(s.value_per_100m)}</td>
              <td role="cell" data-label="Score">{TWO.format(s.score)}</td>
            </tr>)}</tbody>
          </table>
          {plan.spans.length>MAX_PLAN_SPANS&&<p className="walk-hint">The top {MAX_PLAN_SPANS} of {plan.spans.length.toLocaleString('en-US')} streets are listed; one walk list holds up to {MAX_PLAN_SPANS}.</p>}
          <div className="walk-plan-go">
            <p role="status">{countText(picked.length,'street')} checked · {countText(people,'person','people')}{over&&`. One walk list holds up to ${MAX_LIST_PEOPLE.toLocaleString('en-US')} people and ${MAX_PLAN_SPANS} streets.`}</p>
            <button type="button" className="walk-button walk-primary" disabled={!picked.length||over||p.busy} onClick={()=>p.onWalk(picked.map(s=>s.span))}>Walk the checked streets</button>
          </div>
        </>}
      </>}
    </div>
  </details>;
}
