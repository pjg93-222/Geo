// Person profile (render 02). Every person has a profile. Numbers are display-rounded standings; contributions are
// score points; grades exist per person and per index, never per area.
import {useEffect,useState} from 'react';
import {ChevronDown,ChevronRight} from 'lucide-react';
import {PageHeader,Link} from './Header';
import {GradeBox,RangeBar,Sk,ValueBar,cx} from './ui';
import {describeOpinions} from '../lib/opinions';
import type {AreaRef,CandidateInfo,FieldGroup,IndexCard,Meta,Profile} from '../types';
import {ENDASH,MIDDOT,RANGE,areaLabel,blockLabel,leanBand,ordinal,precinctLabel,raceModeText,round,signed,REG_LABEL,shareText,int} from '../lib/format';
import {getProfile} from '../api/client';
import {begin,end} from '../lib/timing';
import {GROUP_LABEL} from '../api/fields';
import type {Route} from '../lib/router';

function backTarget():string{
  try{return '/people'+(sessionStorage.getItem('v3.people.query')||'');}catch{return '/people';}
}
const WITHHELD='Withheld · under 20 people or 10 households';

export function ProfileScreen({meta,route}:{meta:Meta|null;route:Route}){
  const [p,setP]=useState<Profile|null>(null);
  const [error,setError]=useState('');
  const pid=route.pid;
  useEffect(()=>{
    const ac=new AbortController();
    setP(prev=>prev&&prev.person_id===pid?prev:null);setError('');begin('list');
    getProfile(pid,ac.signal).then(x=>{setP(x);end('list');window.scrollTo(0,0);})
      .catch(e=>{if((e as Error).name!=='AbortError')setError((e as Error).message||'This profile could not be loaded.');});
    return ()=>ac.abort();
  },[pid]);
  return <div className="screen profile-screen">
    <PageHeader meta={meta} active="people"/>
    <main className="profile">
      {error?<div className="page-error" role="alert">{error}<p><Link to="/people" className="back-link">← People</Link></p></div>:p?<Body p={p} candidate={meta?.candidate}/>:<ProfileSkeleton/>}
    </main>
  </div>;
}

function ProfileSkeleton(){
  return <div aria-busy="true">
    <div className="p-head"><div><Link to={backTarget()} className="back-link">{'←'} People</Link>
      <div style={{marginTop:'calc(30 * var(--u))'}}><Sk w={360} h={60}/></div><div style={{marginTop:'calc(22 * var(--u))'}}><Sk w={600} h={20}/></div></div></div>
    <div className="p-cards">{[0,1,2,3].map(i=><section key={i} className="card"><Sk w={260} h={24}/><div style={{marginTop:'calc(36 * var(--u))'}}><Sk w={110} h={50}/></div></section>)}</div>
  </div>;
}

function Body({p,candidate}:{p:Profile;candidate?:CandidateInfo}){
  const reg=p.registration_label??REG_LABEL[p.registration]??'Unknown';
  const opinions=describeOpinions(p.groups.find(g=>g.id==='lean')?.fields??[]);
  const [open,setOpen]=useState<Record<string,boolean>>({cua:true,part:false,soi:false,lean:false});
  useEffect(()=>{setOpen({cua:true,part:false,soi:false,lean:false});},[p.person_id]);
  return <>
    <div className="p-head">
      <div className="p-who">
        <Link to={backTarget()} className="back-link">{'←'} People</Link>
        <Link to={'/contacts?q='+encodeURIComponent(p.person_id)} className="profile-contact">Contact details</Link>
        <p className="profile-origin">Local record</p>
        <h1 className="person-name" title={p.name}>{p.name}</h1>
        <p className="person-meta">
          {[p.age!=null?String(p.age):'',`Household of ${p.household_size}`,`Party registration: ${reg}`,p.block?blockLabel(p.block):'',p.precinct?precinctLabel(p.precinct):''].filter(Boolean)
            .map((t,i)=><span key={i}>{i>0&&<b className="dot">{MIDDOT}</b>}{t}</span>)}
        </p>
      </div>
      <div className="p-summary">
        <div className="summary-label">Overall standing</div>
        <div className="summary-number">{round(p.header.pct)}</div>
        <div className="summary-line"><span>{ordinal(p.header.pct)} percentile</span> <b className="dot">{MIDDOT}</b> <em>likely {RANGE(p.header.lo,p.header.hi)}</em></div>
        <RangeBar variant="profile" value={p.header.pct} lo={p.header.lo} hi={p.header.hi} className="summary-bar"/>
      </div>
      <i className="p-vdiv"/>
      <div className="p-facts">
        <div className="fact-grade"><span>Grade</span><GradeBox grade={p.header.grade} size="lg"/></div>
        <div>{round(p.header.measured_pct)}% measured</div>
        <div>{ordinal(p.header.within_pct)} within {p.header.within_group}</div>
      </div>
    </div>

    <div className={cx('p-cards',p.cards.sup&&'has-sup')}>
      <IndexPanel title={GROUP_LABEL.cua} card={p.cards.cua}/>
      {p.cards.sup&&<SupportPanel card={p.cards.sup} candidate={candidate}/>}
      <IndexPanel title={GROUP_LABEL.part} card={p.cards.part}/>
      <IndexPanel title={GROUP_LABEL.soi} card={p.cards.soi}/>
    </div>
      <section className="opinion-panel" aria-label="Political responses">
        <div className="opinion-heading">
        <h2 className="panel-title">Political views</h2>
        <p>Separate responses · no belief score</p>
        </div>
        <dl className="opinion-facts"><div><dt>Party registration</dt><dd>{reg}</dd></div><div><dt>Affiliation</dt><dd>{opinions.affiliation}</dd></div><div><dt>Political outlook</dt><dd>{opinions.outlook}</dd></div></dl>
        <p className="opinion-explanation">Registration, affiliation and outlook are separate fields. Different responses across these fields can be valid.</p>
      </section>

    <div className="p-lower">
      <section className="evidence" aria-label="Field evidence">
        <header className="ev-head"><h2 className="section-title">Field evidence</h2><span className="section-count">{p.groups.reduce((s,g)=>s+g.n_fields,0)} fields</span></header>
        <p className="evidence-origin">Field values from the active local dataset.</p>
        <p className="scroll-hint">Scroll sideways for values and contributions.</p>
        <div className="ev-th"><span>Field</span><span>Value</span><span>Source</span><span>Contribution</span></div>
        {p.groups.map(g=><Group key={g.id} g={g} open={!!open[g.id]} onToggle={()=>setOpen(o=>({...o,[g.id]:!o[g.id]}))}/>)}
      </section>
      <div className="p-side">
        <section className="household" aria-label="Household">
          <h2 className="section-title">Household</h2>
          <div className="hh-th"><span/><span>Age</span><span>Overall standing</span><span>Grade</span></div>
          <div className="hh-rows">
            {p.household.members.map(m=>{
              const inner=<>
                <span className="hh-name" title={m.name}>{m.name}</span>
                <span className="hh-age">{m.age??''}</span>
                <span className="hh-standing"><b>{round(m.ovr_pct)}</b><ValueBar value={m.ovr_pct} variant="row"/></span>
                <span className="hh-grade"><GradeBox grade={m.grade} size="row"/></span>
              </>;
              return m.self
                ?<div key={m.person_id} className="hh-row self" aria-current="true">{inner}</div>
                :<Link key={m.person_id} to={`/people/${encodeURIComponent(m.person_id)}`} className="hh-row">{inner}</Link>;
            })}
          </div>
          <p className="hh-foot">Household mean standing <b>{round(p.household.mean_pct)}</b></p>
        </section>
        <section className="context" aria-label="Area context">
          <h2 className="section-title sm">Area context</h2>
          <ContextRow a={p.area_context.block}/>
          <ContextRow a={p.area_context.precinct}/>
        </section>
      </div>
    </div>
  </>;
}

function IndexPanel({title,card}:{title:string;card:IndexCard}){
  const single=card.lo===card.hi;
  return <section className="card">
    <h2 className="panel-title">{title}</h2>
    <div className="grade-slot"><span className="panel-eyebrow">Grade</span><GradeBox grade={card.grade} size="lg"/></div>
    <div className="panel-number">{round(card.pct)}</div>
    <div className="panel-pct">{ordinal(card.pct)} percentile</div>
    <RangeBar variant="profile" value={card.pct} lo={card.lo} hi={card.hi} className="panel-bar"/>
    <div className="panel-range">{single?round(card.pct):RANGE(card.lo,card.hi)}</div>
  </section>;
}

/** Candidate districts only: a score and a standing. Support has no likely range and no grade. */
function SupportPanel({card,candidate}:{card:{score:number;pct:number};candidate?:CandidateInfo}){
  return <section className="card">
    <h2 className="panel-title">Candidate support</h2>
    <div className="panel-number">{round(card.pct)}</div>
    <div className="panel-pct">{ordinal(card.pct)} percentile</div>
    <div className="panel-range">Support score {round(card.score)} of 100</div>
    {candidate&&<div className="support-mode">{raceModeText(candidate.race_mode)}</div>}
  </section>;
}

function Group({g,open,onToggle}:{g:FieldGroup;open:boolean;onToggle:()=>void}){
  return <div className={cx('ev-group',open&&'open')}>
    <button type="button" className="ev-grouprow" aria-expanded={open} onClick={onToggle}>
      {open?<ChevronDown strokeWidth={1.75} aria-hidden="true"/>:<ChevronRight strokeWidth={1.75} aria-hidden="true"/>}
      <span className="group-row">{g.label}</span>
      <span className="section-count">{MIDDOT} {g.n_fields} fields{open?'':` ${MIDDOT} ${g.observed} observed`}</span>
    </button>
    {open&&g.fields.map(f=>{
      const lean=g.id==='lean';
      const c=f.contribution;
      const cText=Math.abs(c)<0.05?'0.0':(c>0?'+':'−')+Math.abs(c).toFixed(1);
      return <div className="ev-row" key={f.field}>
        <span className="field-name">{f.label}</span>
        <span className="field-value"><i className={cx('src-dot',f.status==='observed'?'obs':'est')}/>{f.value}</span>
        <span className="field-source">{f.status==='observed'?'Observed':f.status==='children_rule'?`Estimated ${MIDDOT} children rule`:f.p==null?'Estimated':`Estimated ${MIDDOT} p ${f.p.toFixed(2)}`}</span>
        <span className="field-contrib">{lean?'—':cText}</span>
        {lean?<span className="opinion-unscored">Not scored</span>:<span className="cbar" role="img" aria-label={`Contribution ${cText}`}>
          {lean
            ?<i style={f.exposure*Math.sign(c)>=0
                ?{left:'50%',width:`${Math.abs(c)/50*50}%`,background:'var(--lean-d)'}
                :{right:'50%',width:`${Math.abs(c)/50*50}%`,background:'var(--lean-r)'}}/>
            :<i style={{width:`${Math.max(1.2,f.exposure*100)}%`}}/>}
        </span>}
      </div>;
    })}
  </div>;
}

function ContextRow({a}:{a:AreaRef}){
  if(!a.id)return <div className="ctx-row"><div className="ctx-text"><div className="ac-row">{a.level==='block'?'Census block':'Precinct'}</div><div className="ac-sub">Not supplied</div></div></div>;
  return <div className="ctx-row">
    <div className="ctx-text">
      <div className="ac-row">{a.label||areaLabel(a.level,a.id)}</div>
      <div className="ac-sub">{a.withheld?WITHHELD:`${ordinal(a.pct!)} ${MIDDOT} ${shareText(a.top10!)}% in top 10%`}</div>
    </div>
    {a.withheld?<i className="hatch ctx-hatch"/>:<div className="strip" role="img" aria-label="Position among areas">
      {['s1','s2','s3','s4','s5'].map(s=><i key={s} style={{background:`var(--${s})`}}/>)}
      <b style={{left:`${Math.max(0,Math.min(100,a.pct!))}%`}}/>
    </div>}
  </div>;
}
