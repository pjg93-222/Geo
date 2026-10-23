import {useId,useRef} from 'react';
import type {PointerEvent} from 'react';
import type {AudienceChoice,AudienceOptions} from '../api/audience.ts';
import {CANVASS_HELP,MIDDLE_HELP,NO_COVERAGE_NOTICE,SUPPORT_HELP,keyHandle,moveHandle,nearerHandle,presetMatches,registeredLabel,scoreAt,supportEnds,supportPresets,toggleChoice} from '../lib/audienceGroups.ts';
import type {AudienceFilter as Filter,Dimension} from '../lib/audienceGroups.ts';
const count=(n:number)=>n.toLocaleString('en-US');

/** Include and Exclude chips for one dimension. Pressed chips carry a mark as well as a colour. */
function Chips({title,dim,choices,name,filter,onChange,help}:{title:string;dim:Dimension;choices:AudienceChoice[];name:(c:AudienceChoice)=>string;filter:Filter;onChange:(f:Filter)=>void;help?:string}){
  const note=useId();
  return <div className="audience-dimension">
    <h4>{title}</h4>
    {help&&<p id={note} className="public-street-hint">{help}</p>}
    {(['include','exclude'] as const).map(side=><div key={side} role="group" aria-label={`${title}: ${side}`} aria-describedby={help?note:undefined} className="audience-chips">
      <span className="audience-side">{side==='include'?'Include':'Exclude'}</span>
      {choices.map(c=>{const on=filter[side][dim].includes(c.code);
        return <button type="button" key={c.code} className={`audience-chip ${side}`} aria-pressed={on} onClick={()=>onChange(toggleChoice(filter,side,dim,c.code))}>
          {on&&<span aria-hidden="true">{side==='include'?'✓ ':'✕ '}</span>}{name(c)} <small>{count(c.count)}</small>
        </button>;})}
    </div>)}
  </div>;
}

/** Two handles on a 0-100 track over Terrier's support score; the handles cannot cross. */
function SupportSlider({filter,onChange,candidate}:{filter:Filter;onChange:(f:Filter)=>void;candidate:'D'|'R'|null}){
  const track=useRef<HTMLDivElement>(null),drag=useRef<'low'|'high'|null>(null),thumbs={low:useRef<HTMLDivElement>(null),high:useRef<HTMLDivElement>(null)};
  const s=filter.support,ends=supportEnds(candidate),set=(support:Filter['support'])=>onChange({...filter,support});
  const score=(e:PointerEvent)=>{const r=track.current!.getBoundingClientRect();return scoreAt(e.clientX,r.x,r.width);};
  const thumb=(handle:'low'|'high',label:string)=><div key={handle} ref={thumbs[handle]} role="slider" tabIndex={0} className="audience-thumb" style={{left:`${s[handle]}%`}}
    aria-label={label} aria-orientation="horizontal" aria-valuemin={handle==='low'?0:Math.round(s.low)} aria-valuemax={handle==='low'?Math.round(s.high):100} aria-valuenow={Math.round(s[handle])}
    onKeyDown={e=>{const next=keyHandle(s,handle,e.key);if(next){e.preventDefault();set(next);}}}/>;
  return <div className="audience-slider" data-tone={candidate??'none'}>
    <div ref={track} className="audience-track"
      onPointerDown={e=>{const v=score(e),h=nearerHandle(s,v);drag.current=h;e.currentTarget.setPointerCapture(e.pointerId);set(moveHandle(s,h,v));thumbs[h].current?.focus();}}
      onPointerMove={e=>{if(drag.current)set(moveHandle(s,drag.current,score(e)));}}
      onPointerUp={()=>{drag.current=null;}} onPointerCancel={()=>{drag.current=null;}}>
      <div className="audience-range" style={{left:`${s.low}%`,width:`${s.high-s.low}%`}}/>
      {thumb('low','Lowest support score included')}{thumb('high','Highest support score included')}
    </div>
    <div className="audience-ends"><span>{ends.low}</span><span>{ends.mid}</span><span>{ends.high}</span></div>
  </div>;
}

function SupportControl({filter,onChange,candidate,coverage}:{filter:Filter;onChange:(f:Filter)=>void;candidate:'D'|'R'|null;coverage:boolean}){
  const help=useId(),middle=useId(),s=filter.support;
  return <div className="audience-dimension">
    <h4>Support score</h4>
    <p id={help} className="public-street-hint">{SUPPORT_HELP}</p>
    {!coverage&&<p className="public-street-alert" role="status">{NO_COVERAGE_NOTICE}</p>}
    <div role="group" aria-label="Support presets" className="audience-chips">
      {supportPresets(candidate).map(p=>{const on=presetMatches(p,s);
        return <button type="button" key={p.id} className="audience-chip preset" aria-pressed={on} aria-describedby={p.id==='mid'?middle:help} onClick={()=>onChange({...filter,support:{...s,low:p.low,high:p.high}})}>
          {on&&<span aria-hidden="true">✓ </span>}{p.label} <small>{Math.round(p.low)} to {Math.round(p.high)}</small>
        </button>;})}
    </div>
    <p id={middle} className="public-street-hint">{MIDDLE_HELP}</p>
    <SupportSlider filter={filter} onChange={onChange} candidate={candidate}/>
    <p className="audience-readout">Support score {Math.round(s.low)} to {Math.round(s.high)}</p>
    <label className="audience-check"><input type="checkbox" checked={s.includeNoScore} onChange={e=>onChange({...filter,support:{...s,includeNoScore:e.target.checked}})}/>Include voters with no score</label>
  </div>;
}

export function AudienceFilterControls({options,filter,onChange,supportCoverage}:{options:AudienceOptions;filter:Filter;onChange:(f:Filter)=>void;supportCoverage:boolean}){
  return <fieldset className="audience-filter">
    <legend>Audience filter</legend>
    <Chips title="Registration" dim="party" choices={options.party} name={registeredLabel} filter={filter} onChange={onChange}/>
    <Chips title="Canvass result" dim="canvass" choices={options.canvass} name={c=>c.label} filter={filter} onChange={onChange} help={CANVASS_HELP}/>
    <SupportControl filter={filter} onChange={onChange} candidate={options.candidate_party} coverage={supportCoverage}/>
  </fieldset>;
}
