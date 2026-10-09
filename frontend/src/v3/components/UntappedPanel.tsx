// Untapped potential beside the map: the two sliders, the level, the areas with the most potential (each with its undecided and supporter
// parts and its people), and the block of the selected area. The sums come from /api/v3/untapped once; the sliders only re-weigh them here.
import {useEffect,useMemo,useRef,useState} from 'react';
import {Sk} from './ui';
import {getUntapped} from '../api/client';
import {MIDDOT,countLabel,int,round} from '../lib/format';
import {DEFAULTS_NOTE,SUPPORTER_WORDS,UNDECIDED_WORDS,ranked,type UntappedArea,type UntappedAreas,type UntappedLevel,type UntappedWeights} from '../lib/untapped';

/** The levels the panel ranks. Precinct rows can start a walk list. */
export type PanelLevel=Extract<UntappedLevel,'precinct'|'state_house'|'state_senate'>;
const PANEL_LEVELS:{id:PanelLevel;label:string}[]=[{id:'precinct',label:'Precinct'},{id:'state_house',label:'State House'},{id:'state_senate',label:'State Senate'}];

/** A potential sum: whole numbers from 10 up, one decimal below. */
export const amount=(x:number):string=>x>=10?int(x):(round(x*10)/10).toFixed(1);

/** The sums of one level, fetched on mount and again whenever the window is shown or focused (a 304 while no result has been posted).
 *  Nothing is fetched for a null level. Sums of another level are never returned, and a failed refetch keeps the sums already held. */
export function useUntapped(level:UntappedLevel|null):{data:UntappedAreas|null;error:string}{
  const [got,setGot]=useState<{level:UntappedLevel;data:UntappedAreas}|null>(null);
  const [error,setError]=useState('');
  useEffect(()=>{
    if(!level)return;
    setError('');
    const ac=new AbortController();
    let busy=false;
    const load=()=>{
      if(busy)return;
      busy=true;
      getUntapped(level,ac.signal).then(data=>{setGot(prev=>prev&&prev.level===level&&prev.data===data?prev:{level,data});setError('');})
        .catch((e:unknown)=>{if(!ac.signal.aborted)setError((e as Error).message||'Untapped potential could not be loaded.');})
        .finally(()=>{busy=false;});
    };
    const onShow=()=>{if(document.visibilityState==='visible')load();};
    load();
    window.addEventListener('focus',load);document.addEventListener('visibilitychange',onShow);
    return ()=>{ac.abort();window.removeEventListener('focus',load);document.removeEventListener('visibilitychange',onShow);};
  },[level]);
  return {data:level&&got&&got.level===level?got.data:null,error:level?error:''};
}

export interface UntappedPanelProps{
  level:PanelLevel;onLevel:(l:PanelLevel)=>void;
  data:UntappedAreas|null;error:string;
  weights:UntappedWeights;defaults:UntappedWeights;onWeights:(w:UntappedWeights)=>void;
  /** "Plan walk list" shows on precinct rows when the district has the street plan (features.roi). */
  canPlan:boolean;onPlan:(precinct:string)=>void;
}

export function UntappedPanel(p:UntappedPanelProps){
  const {weights:w,defaults:d}=p;
  const top=useMemo(()=>p.data?ranked(p.data,w):[],[p.data,w]);
  const people=p.data?p.data.people.reduce((a,b)=>a+b,0):0,left=p.data?p.data.knocked.reduce((a,b)=>a+b,0):0;
  const supporters=Math.round((1-w.wp)*100);
  return <section className="untapped-panel" aria-label="Untapped potential">
    <h2 className="up-title">Untapped potential</h2>
    <p className="up-sub">{p.data?`${countLabel(people,'person','people')} counted${left>0?` ${MIDDOT} ${int(left)} left out, their door has a recorded result`:''}`:' '}</p>
    <div className="up-controls">
      <label className="up-slider"><span className="up-slider-label">Balance: undecided voters ↔ supporters</span>
        <input type="range" min={0} max={1} step={0.05} value={1-w.wp} aria-valuetext={`Undecided ${100-supporters} percent, supporters ${supporters} percent`}
          onChange={e=>p.onWeights({...w,wp:1-Number(e.target.value)})}/>
        <span className="up-slider-value">Undecided {100-supporters}% {MIDDOT} Supporters {supporters}%</span></label>
      <label className="up-slider"><span className="up-slider-label">Influence weight</span>
        <input type="range" min={0} max={1} step={0.05} value={w.wi} onChange={e=>p.onWeights({...w,wi:Number(e.target.value)})}/>
        <span className="up-slider-value">{w.wi.toFixed(2)} {MIDDOT} top influence counts {(1+w.wi).toFixed(2)} times</span></label>
      <div className="up-defaults"><button type="button" disabled={w.wp===d.wp&&w.wi===d.wi} onClick={()=>p.onWeights(d)}>Defaults</button><span>{DEFAULTS_NOTE}</span></div>
    </div>
    <div className="up-listhead">
      <h3 className="up-section">Most untapped</h3>
      <select aria-label="Area level" value={p.level} onChange={e=>p.onLevel(e.target.value as PanelLevel)}>
        {PANEL_LEVELS.map(l=><option key={l.id} value={l.id}>{l.label}</option>)}</select>
    </div>
    <p className="up-note">Total potential per area, split into its undecided and supporter parts.</p>
    {p.error&&!p.data&&<p className="up-note" role="alert">{p.error}</p>}
    {!p.data&&!p.error&&<div aria-busy="true">{[0,1,2,3,4].map(i=><div key={i} style={{marginTop:10}}><Sk w={i%2?380:440} h={16}/></div>)}</div>}
    {p.data&&<ol className="up-list">
      {top.map((a,i)=><li key={a.id}>
        <span className="up-rank">{i+1}</span>
        <div className="up-main">
          <div className="up-head"><span className="up-name" title={a.label}>{a.label}</span><span className="up-value">{amount(a.value)}</span></div>
          <div className="up-parts">
            <span>{UNDECIDED_WORDS} <b>{amount(a.undecided)}</b></span>
            <span>{SUPPORTER_WORDS} <b>{amount(a.supporter)}</b></span>
            <span><b>{int(a.people)}</b> people</span>
          </div>
        </div>
        {p.level==='precinct'&&p.canPlan&&<button type="button" className="up-plan" aria-label={`Plan walk list for ${a.label}`} onClick={()=>p.onPlan(a.id)}>Plan walk list</button>}
      </li>)}
      {top.length===0&&<li className="up-empty">No areas with people counted.</li>}
    </ol>}
  </section>;
}

export interface UntappedBlockProps{area:UntappedArea|null;loading:boolean;precinct:boolean;canPlan:boolean;onPlan:(precinct:string)=>void}
/** The selected area's potential, in the rail: the total, its two parts, the people counted and the people left out. */
export function UntappedBlock(p:UntappedBlockProps){
  const box=useRef<HTMLElement>(null);
  const id=p.area?.id;
  useEffect(()=>{if(id)box.current?.scrollIntoView({block:'nearest'});},[id]);   // selecting an area on the map brings its numbers into view
  if(p.loading)return <section className="untapped-block" aria-busy="true"><Sk w={300} h={20}/><div style={{marginTop:10}}><Sk w={400} h={16}/></div></section>;
  const a=p.area;
  if(!a)return null;
  return <section className="untapped-block" ref={box} aria-label="Untapped potential of this area">
    <h3 className="up-section">Untapped potential</h3>
    <p className="ub-area">{a.label}</p>
    <div className="ub-value">{amount(a.value)}</div>
    <dl className="ub-facts">
      <div><dt>{UNDECIDED_WORDS}</dt><dd>{amount(a.undecided)}</dd></div>
      <div><dt>{SUPPORTER_WORDS}</dt><dd>{amount(a.supporter)}</dd></div>
      <div><dt>People counted</dt><dd>{int(a.people)}</dd></div>
      <div><dt>Left out, recorded result</dt><dd>{int(a.knocked)}</dd></div>
    </dl>
    {p.precinct&&p.canPlan&&<button type="button" className="up-plan" onClick={()=>p.onPlan(a.id)}>Plan walk list</button>}
  </section>;
}
