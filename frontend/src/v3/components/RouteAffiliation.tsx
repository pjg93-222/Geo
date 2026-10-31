import {useMemo} from 'react';
import type {Meta} from '../types';
import type {WalkListPayload} from '../api/walkTypes';
import {affiliationChoices,affiliationCodes,affiliationText,affiliationWire,routeAffiliationCounts} from '../lib/routeAffiliation';

export function RouteAffiliationControls({value,onChange,meta,names,disabled=false}:{value:string;onChange:(value:string)=>void;meta:Meta;names:Record<string,string>;disabled?:boolean}){
  let selected:string[]|null=null,error='';try{selected=affiliationCodes(value);}catch(e){error=(e as Error).message;}
  const choices=affiliationChoices(meta.district_parties,names,selected??[]);
  function toggle(code:string,checked:boolean){
    const next=new Set(selected??choices.map(c=>c.code));if(checked)next.add(code);else next.delete(code);
    onChange(affiliationWire([...next]));
  }
  return <fieldset className="route-affiliation" disabled={disabled}>
    <legend>Affiliation · simulation records</legend>
    <p className="walk-hint">Choose the records that enter the route. Streets, house positions and route ordering use the existing planner.</p>
    <div className="route-affiliation-actions"><button type="button" className="walk-link" onClick={()=>onChange('')}>All affiliations</button><button type="button" className="walk-link" onClick={()=>onChange('empty')}>Clear</button></div>
    {error&&<p className="walk-note" role="alert">{error}</p>}
    <div className="route-affiliation-choices">{choices.map(c=><label key={c.code}><input type="checkbox" checked={!error&&(selected===null||selected.includes(c.code))} onChange={e=>toggle(c.code,e.target.checked)}/><span>{c.label}</span><small>{meta.district_parties?c.count.toLocaleString('en-US'):'—'}</small></label>)}</div>
    <p className="walk-hint">Independent Party and Unaffiliated are separate categories. {meta.district_parties?'Counts describe the district; zero means no records with that code.':'District counts are unavailable.'}</p>
    <p className="route-affiliation-selected">{affiliationText(value,names)}</p>
  </fieldset>;
}

export function RouteContext({meta,payload,names,pending,loading,addresses}:{meta:Meta;payload:WalkListPayload|null;names:Record<string,string>;pending:string;loading:boolean;addresses:boolean}){
  const counts=useMemo(()=>routeAffiliationCounts(payload),[payload]);
  const rows=affiliationChoices(payload?counts:meta.district_parties,names,Object.keys(meta.district_parties??{}).map(c=>c||'none'));
  const total=rows.reduce((sum,row)=>sum+row.count,0),applied=payload?.query.affiliation??'';
  const inherited=payload?.list.kind==='reknock';
  const stale=!!payload&&!inherited&&applied!==pending;
  const available=!!payload||meta.district_parties!==undefined;
  return <aside className="walk-context" aria-label="Research context">
    <p className="walk-eyebrow">Pathfinder</p><h2>Research context</h2>
    <p className="route-context-scope">{payload?'Displayed list':`${meta.district_label} · district context`}</p>
    {loading&&<p className="walk-note" role="status">Building the route…</p>}
    {stale&&<p className="walk-note" role="status">Filter changed. Generate the route to apply it. The map and counts still show the previous result.</p>}
    {addresses&&<p className="walk-hint">Imported addresses retain their source details. Affiliation filtering applies to the Streets source.</p>}
    <dl className="route-context-totals"><div><dt>Records</dt><dd>{available?total.toLocaleString('en-US'):'—'}</dd></div>{payload&&<><div><dt>Doors</dt><dd>{payload.totals.doors.toLocaleString('en-US')}</dd></div><div><dt>Route</dt><dd>{(payload.totals.walk_m/1000).toFixed(2)} km</dd></div></>}</dl>
    <h3>Affiliation composition</h3>
    <p className="walk-hint">{inherited?'Records inherited from the saved source list.':payload?affiliationText(applied,names):'All records in the district snapshot.'}</p>
    <ul className="route-composition">{rows.map(row=><li key={row.code}><div><span>{row.label}</span><span>{available?row.count.toLocaleString('en-US'):'—'} <small>{total?`${(row.count/total*100).toFixed(1)}%`:'—'}</small></span></div><div className="route-composition-track" aria-hidden="true"><i style={{width:`${total?row.count/total*100:0}%`}}/></div></li>)}</ul>
    {!total&&<p className="walk-hint">No affiliation counts are available for this view.</p>}
    <div className="route-context-source"><h3>Source snapshot</h3><p>{meta.snapshot}</p><p>Simulation and research · owner-declared synthetic records.</p><p>Street and house detail is retained. Composition describes records, not an area average.</p></div>
  </aside>;
}
