// Map overlays and the street rail: legend (share / lean / measured / household variants), zoom control, timing
// readout, "In view" rail, and the household popover (FIDELITY_SPEC sections 3.5, 6.1, 6.5, 6.6).
import {useEffect,useRef,useState} from 'react';
import {Minus,Plus} from 'lucide-react';
import {GradeBox,Sk,Switch,ValueBar,cx} from './ui';
import {Link,INDEX_NAME} from './Header';
import type {Group,HouseholdPopover,StreetPayload} from '../types';
import {ENDASH,MIDDOT,householdLabel,int,round,seconds} from '../lib/format';
import {useTiming} from '../lib/timing';
import {uPx} from '../lib/units';

const SHARE_LABELS=['Under 5%',`5${ENDASH}8%`,`8${ENDASH}12%`,`12${ENDASH}18%`,'18%+'];
const MEASURED_LABELS=['Under 20%',`20${ENDASH}40%`,`40${ENDASH}60%`,`60${ENDASH}80%`,'80%+'];
const LEAN_LABELS=['Dem.','Rep.','Unaff.','Other','Mixed'];
const HH_LABELS=[`0${ENDASH}20`,`20${ENDASH}40`,`40${ENDASH}60`,`60${ENDASH}80`,`80${ENDASH}100`];
const RAMP=['var(--s1)','var(--s2)','var(--s3)','var(--s4)','var(--s5)'];
const LEAN_RAMP=['var(--reg-d)','var(--reg-r)','var(--reg-u)','var(--reg-o)','var(--centre)'];
const TOP_NAME:Record<Exclude<Group,'lean'>,string>={ovr:'overall',cua:'Credit Union Alignment',part:'Participation',soi:'Sphere of Influence'};

export function Legend(p:{group:Group;measured:boolean;onMeasured:(v:boolean)=>void;loading:boolean}){
  let title:string,sub:string,labels:string[],ramp:string[];
  if(p.measured){title='% measured';sub='Area mean of % measured';labels=MEASURED_LABELS;ramp=RAMP;}
  else if(p.group==='lean'){title='Registration';sub='Largest registration group; Mixed means a tie';labels=LEAN_LABELS;ramp=LEAN_RAMP;}
  else{title=INDEX_NAME[p.group];sub=`Share of people in the ${TOP_NAME[p.group]} top 10%`;labels=SHARE_LABELS;ramp=RAMP;}
  return <section className="legend" aria-label="Legend">
    {p.loading?<><Sk w={240} h={20}/><div style={{marginTop:'calc(12 * var(--u))'}}><Sk w={420} h={15}/></div></>
      :<><h3 className="legend-title">{title}</h3><p className="legend-sub">{sub}</p></>}
    <ul className="legend-swatches">
      {ramp.map((c,i)=><li key={i}><i style={p.loading?undefined:{background:c}} className={cx(p.loading&&'sk')}/><span>{p.loading?'':labels[i]}</span></li>)}
    </ul>
    <Switch className="legend-switch" checked={p.measured} onChange={p.onMeasured} label="% measured" labelClass="legend-note"/>
  </section>;
}

export function HouseholdLegend(){
  return <section className="legend hh" aria-label="Legend">
    <h3 className="legend5-title">Household mean standing</h3>
    <ul className="legend5-circles">
      {RAMP.map((c,i)=><li key={i}><i style={{background:c}}/><span>{HH_LABELS[i]}</span></li>)}
    </ul>
  </section>;
}

export function ZoomControl(p:{onZoom:(d:number)=>void}){
  return <div className="zoom" role="group" aria-label="Zoom">
    <button type="button" aria-label="Zoom in" onClick={()=>p.onZoom(1)}><Plus strokeWidth={1.75}/></button>
    <button type="button" aria-label="Zoom out" onClick={()=>p.onZoom(-1)}><Minus strokeWidth={1.75}/></button>
  </div>;
}

export function Readout(){
  const t=useTiming();
  return <div className="readout" aria-live="off">
    {t.map!==null&&<div>Map ready {seconds(t.map)} s</div>}
    {t.list!==null&&<div>List ready {seconds(t.list)} s</div>}
  </div>;
}

// --- area ids drawn on the map (render 04: "212", "213" ...) -----------------------------------------------------------
// Glyph PBFs are not available offline, so these are DOM labels at each polygon's label point: Geist Mono, at most 60 in
// view, and only where the polygon is wide enough on screen to hold the text.
/** Relative luminance of a #RRGGBB color (0 dark .. 1 light); unknown colors read as light. */
function luminance(hex:string|undefined):number{
  const m=hex?/^#([0-9a-f]{6})$/i.exec(hex):null;if(!m)return 1;
  const v=parseInt(m[1],16);const c=[(v>>16)&255,(v>>8)&255,v&255].map(x=>{const f=x/255;return f<=0.03928?f/12.92:Math.pow((f+0.055)/1.055,2.4);});
  return 0.2126*c[0]+0.7152*c[1]+0.0722*c[2];
}
export interface MapLabel{id:string;text:string;lon:number;lat:number;widthDeg:number;bin:number}
export function AreaLabels(p:{items:MapLabel[];palette:string[];selectedId:string|null;project:(lon:number,lat:number)=>{x:number;y:number}|null;zoom:number;width:number;height:number}){
  const pxPerDeg=512*Math.pow(2,p.zoom)/360;
  const seen:{id:string;text:string;x:number;y:number;light:boolean;w:number;sel:boolean}[]=[];
  for(const it of p.items){
    const w=it.widthDeg*pxPerDeg;
    if(w<56)continue;
    const pt=p.project(it.lon,it.lat);
    if(!pt||pt.x<-20||pt.y<-20||pt.x>p.width+20||pt.y>p.height+20)continue;
    seen.push({id:it.id,text:it.text,x:pt.x,y:pt.y,light:luminance(p.palette[it.bin])<0.42,w,sel:it.id===p.selectedId});
  }
  // the selected area always keeps its label; the rest by on-screen width, at most 60 in all
  const out=seen.sort((a,b)=>Number(b.sel)-Number(a.sel)||b.w-a.w).slice(0,60);
  return <div className="area-labels" aria-hidden="true">
    {out.map(l=><span key={l.id} className={cx('map-label',l.light&&'on-dark')} style={{left:l.x,top:l.y}}>{l.text}</span>)}
  </div>;
}

// --- street rail ("In view") ----------------------------------------------------------------------------------------
export function StreetRail(p:{stats:StreetPayload['stats']|null;truncated?:boolean}){
  const s=p.stats;
  const hist=s?.hist??[0,0,0,0,0];const max=Math.max(1,...hist);
  return <aside className="rail street" aria-label="In view">
    <h2 className="rail5-title">In view</h2>
    <dl className="rail5-facts">
      <div><dt>Households</dt><dd>{s?int(s.households):<Sk w={50} h={30}/>}</dd></div>
      <div><dt>People</dt><dd>{s?int(s.people):<Sk w={70} h={30}/>}</dd></div>
      <div><dt>% measured</dt><dd>{s?`${round(s.measured_pct)}%`:<Sk w={70} h={30}/>}</dd></div>
    </dl>
    <hr className="rule s1"/>
    <h3 className="rail5-section">Household mean standing</h3>
    <div className="hist" role="img" aria-label="Households in view by mean standing">
      {hist.map((c,i)=><div key={i} className="hist-col">
        <i style={{height:`calc(${(c/max)*148} * var(--u))`,background:RAMP[i]}}/>
        <span>{HH_LABELS[i]}</span>
      </div>)}
    </div>
    <hr className="rule s2"/>
    <p className="rail5-note">Zoom out for block, precinct and tract values.</p>
    {p.truncated&&<p className="rail5-note" role="status">Showing the first {int(s?.households??0)} test households. Zoom in to show a smaller area.</p>}
  </aside>;
}

// --- household popover -----------------------------------------------------------------------------------------------------
export interface PopoverProps{
  data:HouseholdPopover|null;x:number;y:number;paneW:number;paneH:number;
  onClose:()=>void;
}
export function Popover(p:PopoverProps){
  const ref=useRef<HTMLDivElement>(null);
  const [box,setBox]=useState({w:611*uPx(),h:0});
  // the popover is 611u wide as drawn, and grows only when its widest name needs more room
  useEffect(()=>{if(ref.current)setBox({w:ref.current.offsetWidth,h:ref.current.offsetHeight});},[p.data]);
  const h=box.h;
  useEffect(()=>{
    const k=(e:KeyboardEvent)=>{if(e.key==='Escape')p.onClose();};
    window.addEventListener('keydown',k);return ()=>window.removeEventListener('keydown',k);
  },[p]);
  const u=uPx();
  const w=box.w;const gap=(14+12)*u;       // marker radius + white/gold rings, then the 12u pointer
  const above=p.y-gap-h>8;
  const left=Math.max(8,Math.min(p.paneW-w-8,p.x-w/2));
  const top=Math.max(8,Math.min(p.paneH-h-8,above?p.y-gap-h:p.y+gap));
  const pointerLeft=Math.max(24*u,Math.min(w-24*u-24*u,p.x-left-12*u));
  return <div ref={ref} className={cx('popover',above?'above':'below')} role="dialog" aria-label="Household"
    style={{left,top,width:w,visibility:h?'visible':'hidden'}}>
    <button className="pop-close" type="button" aria-label="Close household" onClick={p.onClose}>×</button>
    <i className="pop-pointer" style={{left:pointerLeft}}/>
    {p.data?<>
      <h3 className="pop-title">{householdLabel(p.data.code)}</h3>
      <p className="pop-sub">{p.data.n_people} {p.data.n_people===1?'person':'people'} {MIDDOT} household mean standing {round(p.data.mean_pct)} {MIDDOT} {p.data.block_label}</p>
      <div className="pop-rows">
        {p.data.members.slice(0,40).map(m=><div className="pop-row" key={m.person_id}>
          <span className="pop-name" title={m.name}>{m.name}{m.age!=null?`, ${m.age}`:''}</span>
          <span className="pop-num">{round(m.ovr_pct)}</span>
          <ValueBar value={m.ovr_pct} variant="pop"/>
          <GradeBox grade={m.grade} size="sm"/>
          <Link to={`/people/${encodeURIComponent(m.person_id)}`} className="pop-link">View profile {'→'}</Link>
        </div>)}
      </div>
    </>:<div className="pop-skeleton"><Sk w={300} h={22}/><div style={{marginTop:'calc(12 * var(--u))'}}><Sk w={470} h={14}/></div>
      {[0,1,2].map(i=><div key={i} style={{marginTop:'calc(30 * var(--u))'}}><Sk w={560} h={16}/></div>)}</div>}
  </div>;
}
