// Right rail of the map screens. Variant A (Block level, render 01-v2) and variant B (Precinct, Tract, ZIP, render 04)
// share the same data; with nothing selected the rail shows the district summary in layout A.
import {LeanBar,RangeBar,Sk,cx} from './ui';
import {smallNote,type AreaView,type MixShare} from '../lib/areas';
import {partyName} from '../lib/walk';
import type {Group,Meta} from '../types';
import {
  ENDASH,MIDDOT,RANGE,LEVEL_PLURAL,int,countLabel,leanBand,ordinal,rateText,round,shareText,signed,
  areaLabel,tractLabel,precinctLabel,type Level
} from '../lib/format';
import {INDEX_NAME,INDEX_TOP10_NAME} from './Header';

const MAIN_COLOR:Record<string,string>={D:'var(--reg-d)',R:'var(--reg-r)',U:'var(--reg-u)'};
const ROW_NAMES=['ovr','cua','part','soi'] as const;
const EMPTY='No registered people';

export interface Neighbour{id:string;short:string;value:number|null;selected:boolean}
export interface DistrictSummary{ovr:number;cua:number;part:number;soi:number;lean:number;measured:number;people:number;measuredBy?:Record<Group,number>;mix?:MixShare[]|null}

export interface AreaRailProps{
  meta:Meta|null;level:Level;group:Group;
  view:AreaView|null;loading:boolean;
  summary:DistrictSummary|null;
  neighbors:Neighbour[]|null;
  /** Party code -> name (the district's, from /api/v3/features); a code without one reads "Code T". */
  names?:Record<string,string>;
}

/** The district's party names; D, R and U are known before they arrive. */
const namesOf=(given?:Record<string,string>):Record<string,string>=>({D:'Democrat',R:'Republican',U:'Unaffiliated',...given});
function topName(group:Group):string{return group==='lean'?'overall':INDEX_TOP10_NAME[group];}
function registrationHeadline(mix:MixShare[]|null|undefined,names:Record<string,string>){
  if(!mix)return {value:'',label:'Registration mix unavailable'};
  const top=[...mix].sort((a,b)=>b.share-a.share);
  return {value:`${shareText(top[0].share)}%`,label:Math.abs(top[0].share-top[1].share)<1e-9?'Tied largest registration groups':`${partyName(top[0].code,names)} · largest registration group`};
}
/** Every party code as itself (O-10): D, R and U, then each other code present, most people first. */
function RegistrationShares({mix,names}:{mix:MixShare[]|null|undefined;names:Record<string,string>}){
  return <div className="registration-shares">{mix?mix.map(s=>`${partyName(s.code,names)} ${shareText(s.share)}%`).join(' · '):'Registration mix unavailable'}</div>;
}

export function AreaRail(p:AreaRailProps){
  const summaryMode=!p.view;
  const variantB=!summaryMode&&p.level!=='block';
  return <aside className="rail" aria-label="Selected area">
    {variantB?<RailB {...p}/>:<RailA {...p}/>}
  </aside>;
}

function RailSkeleton({b}:{b:boolean}){
  return <div className={b?'railB':'railA'} aria-busy="true">
    <Sk w={b?200:260} h={b?26:34} style={{display:'block'}}/>
    <div style={{marginTop:'calc(14 * var(--u))'}}><Sk w={b?260:340} h={b?16:20}/></div>
    <div style={{marginTop:'calc(54 * var(--u))'}}><Sk w={b?210:300} h={b?50:66}/></div>
    <div style={{marginTop:'calc(28 * var(--u))'}}><Sk w={b?340:520} h={18}/></div>
    <div style={{marginTop:'calc(14 * var(--u))'}}><Sk w={b?260:440} h={16}/></div>
    {Array.from({length:7}).map((_,i)=><div key={i} style={{marginTop:'calc(36 * var(--u))'}}><Sk w={i%2?420:560} h={16}/></div>)}
  </div>;
}

function subtitleA(p:AreaRailProps):string{
  const v=p.view!;
  const parts:string[]=[];
  if(v.tractId)parts.push(tractLabel(v.tractId));
  if(v.precinctId)parts.push(precinctLabel(v.precinctId));
  return parts.join(` ${MIDDOT} `);
}

function RailA(p:AreaRailProps){
  const v=p.view;const meta=p.meta;const names=namesOf(p.names);
  if(p.loading||!meta)return <RailSkeleton b={false}/>;
  const summary=!v;
  const plural=LEVEL_PLURAL[p.level];
  const title=v?areaLabel(p.level,v.id):meta.district_label;
  const sub=v?subtitleA(p):'All areas';
  const g=p.group;
  const rate=g==='lean'?null:meta.district_rate[g];
  // headline
  let headline:React.ReactNode='';let caption='';let likely='';let ink=false;
  if(summary){
    const registration=registrationHeadline(p.summary?.mix,names);
    headline=g==='lean'?registration.value:`${rateText(rate??0.1)}%`;ink=g==='lean';
    caption=g==='lean'?registration.label:`of people are in the ${topName(g)} top 10%`;
    likely='';
  }else if(v!.empty){
    headline='';caption=EMPTY;likely='';
  }else if(g==='lean'&&v!.lean){
    const registration=registrationHeadline(v!.mix,names);headline=registration.value;ink=true;
    caption=registration.label;
  }else if(v!.share){
    const s=v!.share;headline=`${shareText(s.value)}%`;
    caption=`in the ${topName(g)} top 10%, shrunk toward the ${p.level==='block'?'tract':'district'}`;
    likely=`Likely ${shareText(s.lo)}${ENDASH}${shareText(s.hi)}% ${MIDDOT} district rate ${rateText(rate??0.1)}%`;
  }
  const eyebrow=summary?'District indices':`${p.level==='block'?'Block':'Area'} indices`;
  const lean=summary?{mean:p.summary?.lean??0}:v!.lean;
  const facts={
    people:summary?meta.people:v!.n,households:summary?meta.households:v!.households,
    measured:summary?(p.summary?.measuredBy?.[g]??p.summary?.measured??null):v!.measured,
  };
  const note=v?smallNote(v.n,v.households,meta.small_area):null;
  return <div className="railA">
    <h2 className="rail-title" title={title}>{title}</h2>
    <p className="rail-sub">{sub}</p>
    <div className={cx('headline',ink&&'ink')}>{headline}</div>
    <p className="headline-cap">{caption}</p>
    <p className="headline-likely">{likely}</p>
    {note&&<p className="small-note">{note}</p>}
    <hr className="rule a1"/>
    <p className="eyebrow">{eyebrow}</p>
    {ROW_NAMES.map((k,i)=>{
      const r=v?.rows[i];
      const mean=summary?(p.summary as DistrictSummary)[k]:r?.mean;
      const has=mean!==undefined&&mean!==null;
      return <div className="row" key={k}>
        <div className="row-head"><span className="row-label">{INDEX_NAME[k]}</span><span className="row-value">{has?round(mean!):''}</span></div>
        <div className="row-sub">{has?(summary?'District mean':`${ordinal(r!.pct)} among ${plural} ${MIDDOT} likely ${RANGE(r!.lo,r!.hi)}`):' '}</div>
        {has?<RangeBar variant="railA" value={mean!} lo={summary?null:r!.lo} hi={summary?null:r!.hi}/>:<div className="rbar v-railA"/>}
      </div>;
    })}
    <hr className="rule a2"/>
    <div className="row lean">
      <div className="row-head"><span className="row-label">Registration mix</span></div>
      <div className="row-sub">Not part of overall standing</div>
      {!v?.empty&&<RegistrationShares mix={summary?p.summary?.mix:v?.mix} names={names}/>}
    </div>
    <hr className="rule a3"/>
    <dl className="facts">
      <div><dt>People</dt><dd>{facts.people!=null?int(facts.people):''}</dd></div>
      <div><dt>Households</dt><dd>{facts.households!=null?int(facts.households):''}</dd></div>
      <div><dt>% measured</dt><dd>{facts.measured!=null?`${round(facts.measured)}%`:''}</dd></div>
    </dl>
  </div>;
}

function RailB(p:AreaRailProps){
  const v=p.view!;const meta=p.meta;const names=namesOf(p.names);
  if(p.loading||!meta)return <RailSkeleton b={true}/>;
  const g=p.group;
  const title=areaLabel(p.level,v.id);
  let sub='';
  if(p.level==='precinct'){sub=[v.tractId?tractLabel(v.tractId):'',v.blocks!=null?countLabel(v.blocks,'block'):''].filter(Boolean).join(` ${MIDDOT} `);}
  else sub=v.blocks!=null?countLabel(v.blocks,'block'):'';
  const plural=LEVEL_PLURAL[p.level];
  let headline:React.ReactNode='';let caption='';let likely='';let ink=false;
  if(v.empty){caption=EMPTY;}
  else if(g==='lean'&&v.lean){
    const registration=registrationHeadline(v.mix,names);headline=registration.value;ink=true;caption=registration.label;
  }else if(v.share){
    headline=`${shareText(v.share.value)}%`;caption=`in the ${topName(g)} top 10%, shrunk`;
    likely=`Likely ${shareText(v.share.lo)}${ENDASH}${shareText(v.share.hi)}%`;
  }
  const note=smallNote(v.n,v.households,meta.small_area);
  const mix=v.mix;
  // D, R and U keep their colors; every other code is a darker step of the Other color, in the order of the list
  const segs=(mix??[]).map((s,i)=>({...s,label:partyName(s.code,names),color:MAIN_COLOR[s.code]??`color-mix(in srgb, var(--reg-o) ${Math.max(30,100-15*(i-2))}%, #6F675A)`}));
  const maxBar=100;
  return <div className="railB">
    <h2 className="rb-title" title={title}>{title}</h2>
    <p className="rb-sub">{sub}</p>
    <div className={cx('rb-headline',ink&&'ink')}>{headline}</div>
    <p className="rb-cap">{caption}</p>
    <p className="rb-likely">{likely}</p>
    {note&&<p className="small-note">{note}</p>}
    <hr className="rule b1"/>
    {ROW_NAMES.map((k,i)=>{
      const r=v.rows[i];
      return <div className="rb-row" key={k}>
        <span className="rb-label">{INDEX_NAME[k]}</span>
        <div className="rb-line">
          <span className="rb-value">{r?round(r.mean):''}</span>
          <span className="rb-rowlikely">{r?`likely ${RANGE(r.lo,r.hi)}`:''}</span>
          {r?<RangeBar variant="railB" value={r.mean} lo={r.lo} hi={r.hi}/>:<div className="rbar v-railB"/>}
        </div>
      </div>;
    })}
    <hr className="rule b2"/>
    <dl className="rb-facts">
      <div><dt>People</dt><dd>{v.n!=null?int(v.n):''}</dd></div>
      <div><dt>Households</dt><dd>{v.households!=null?int(v.households):''}</dd></div>
      <div><dt>% measured</dt><dd>{v.measured!=null?`${round(v.measured)}%`:''}</dd></div>
    </dl>
    <hr className="rule b3"/>
    {mix&&<>
      <h3 className="rb-section">Registration mix</h3>
      <div className="mixbar" role="img" aria-label="Registration mix">
        {segs.map(s=><span key={s.code||'none'} className="mix-seg" style={{flexGrow:s.share>0?Math.max(s.share,0.008):0,background:s.color}}>
          {s.share>=0.1?`${round(s.share*100)}%`:''}
        </span>)}
      </div>
      <div className="mixlegend">
        {segs.map(s=><span key={s.code||'none'}><i style={{background:s.color}}/>{s.label} {shareText(s.share)}%</span>)}
      </div>
      <hr className="rule b4"/>
    </>}
    {p.neighbors&&p.neighbors.length>0&&<>
      <h3 className="rb-section">Standing across neighboring {plural}</h3>
      <div className="nbars">
        {p.neighbors.map(n=><div key={n.id} className={cx('nbar',n.selected&&'sel')}>
          <span className="nbar-value">{n.value!=null?round(n.value):''}</span>
          <i className="nbar-fill" style={{height:`${Math.max(4,Math.min(100,n.value??0))*(maxBar/100)*1.0}%`}}/>
          <span className="nbar-id">{n.short}</span>
        </div>)}
      </div>
    </>}
  </div>;
}
