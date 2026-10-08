// Ranked people list (render 03). Fifty rows a page, keyset paging, next page prefetched ("Next page ready"),
// header sticky, whole row links to the profile. Person-level data comes only from the local, no-store endpoint.
import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {ChevronLeft,ChevronRight} from 'lucide-react';
import {PageHeader,Link} from './Header';
import {GradeBox,RangeBar,Select,Sk,Switch,cx} from './ui';
import type {Meta,PeoplePage,PeopleQuery,PeopleRow} from '../types';
import {ENDASH,MIDDOT,RANGE,int,leanBandByCode,ordinal,round,weightsText} from '../lib/format';
import {LEVELS,LEVEL_LABEL,areaLabel,type Level} from '../lib/format';
import {getAreas,getPeople} from '../api/client';
import {begin,endAfterPaint} from '../lib/timing';
import {replaceQuery,type Route} from '../lib/router';

const REG:{id:PeopleQuery['reg'];label:string}[]=[
  {id:'all',label:'All'},{id:'D',label:'Democrat'},{id:'R',label:'Republican'},{id:'U',label:'Unaffiliated'},{id:'O',label:'Other'}];
const GRADE:{id:PeopleQuery['grade'];label:string}[]=[{id:'ABCD',label:`A${ENDASH}D`},{id:'AB',label:`A${ENDASH}B`}];
const LEAN:{id:PeopleQuery['lean'];label:string}[]=[
  {id:'all',label:'All'},{id:'-2',label:'Very conservative'},{id:'-1',label:'Somewhat conservative'},{id:'0',label:'Middle outlook'},{id:'1',label:'Somewhat liberal'},
  {id:'2',label:'Very liberal'},{id:'U',label:'Unknown / multiple responses'}];
const SORT:{id:string;label:string}[]=[{id:'ovr',label:'Overall standing'},{id:'sup',label:'Candidate support'}];
const LEAN_KEY:Record<string,string>={'-2':'strong-r','-1':'lean-r','0':'centre','1':'lean-d','2':'strong-d'};
const STORE='v3.people.query';

function fromRoute(q:URLSearchParams):PeopleQuery{
  const reg=q.get('reg') as PeopleQuery['reg']|null;const grade=q.get('grade') as PeopleQuery['grade']|null;const lean=q.get('lean') as PeopleQuery['lean']|null;
  const level=q.get('level') as Level|null;
  return {
    reg:reg&&REG.some(r=>r.id===reg)?reg:'all',grade:grade==='AB'?'AB':'ABCD',lean:lean&&LEAN.some(l=>l.id===lean)?lean:'all',
    level:level&&LEVELS.includes(level)?level:'',area:level?q.get('area')??'':'',balanced:q.get('balanced')==='1',after:q.get('after')??'',dir:q.get('dir')==='prev'?'prev':'next',
    sort:q.get('sort')==='sup'?'sup':'',
  };
}

interface Opt{id:string;label:string;group:string}

export function PeopleScreen({meta,route}:{meta:Meta|null;route:Route}){
  const [q,setQ]=useState<PeopleQuery>(()=>fromRoute(route.search));
  const [page,setPage]=useState<PeoplePage|null>(null);
  const [busy,setBusy]=useState(true);
  const [error,setError]=useState('');
  const [attempt,setAttempt]=useState(0);
  const [ready,setReady]=useState<string|null>(null);   // cursor of the prefetched next page
  const cache=useRef(new Map<string,PeoplePage>());
  const [areaOpts,setAreaOpts]=useState<Opt[]|null>(null);
  const [clearOnLoad,setClearOnLoad]=useState(true);

  const key=(x:PeopleQuery)=>[x.reg,x.grade,x.lean,x.level,x.area,x.balanced?1:0,x.after,x.dir,x.sort].join('|');

  const previousRoute=useRef(route);
  useEffect(()=>{
    if(previousRoute.current===route)return;
    previousRoute.current=route;
    const next=fromRoute(route.search);
    if(key(next)===key(q))return;
    cache.current.clear();setReady(null);setClearOnLoad(true);setPage(null);setQ(next);
  },[route]);

  useEffect(()=>{
    const s=new URLSearchParams();
    if(q.reg!=='all')s.set('reg',q.reg);if(q.grade!=='ABCD')s.set('grade',q.grade);if(q.lean!=='all')s.set('lean',q.lean);
    if(q.level&&q.area){s.set('level',q.level);s.set('area',q.area);}s.set('balanced',q.balanced?'1':'0');
    replaceQuery({reg:s.get('reg'),grade:s.get('grade'),lean:s.get('lean'),level:s.get('level'),area:s.get('area'),balanced:s.get('balanced'),after:q.after||null,dir:q.after?q.dir:null,sort:q.sort||null});
    try{sessionStorage.setItem(STORE,location.search);}catch{/* storage may be unavailable */}
  },[q.reg,q.grade,q.lean,q.level,q.area,q.balanced,q.after,q.dir,q.sort]);

  // load the requested page; prefetch the next one
  useEffect(()=>{
    const ac=new AbortController();
    const k=key(q);
    const hit=cache.current.get(k);
    begin('list');
    setError('');
    const done=(p:PeoplePage)=>{
      if(ac.signal.aborted)return;
      setPage(p);setBusy(false);endAfterPaint('list');
      if(p.next){
        const nq={...q,after:p.next,dir:'next' as const};const nk=key(nq);
        setReady(cache.current.has(nk)?p.next:null);
        if(!cache.current.has(nk)){
          getPeople(nq,ac.signal).then(n=>{cache.current.set(nk,n);if(!ac.signal.aborted)setReady(p.next);}).catch(()=>{});
        }
      }else setReady(null);
    };
    if(hit){done(hit);return ()=>ac.abort();}
    setBusy(true);
    getPeople(q,ac.signal).then(p=>{cache.current.set(k,p);done(p);}).catch(e=>{
      if((e as Error).name==='AbortError')return;
      setError((e as Error).message||'The list could not be loaded.');setBusy(false);
    });
    return ()=>ac.abort();
  },[q.reg,q.grade,q.lean,q.level,q.area,q.balanced,q.after,q.dir,q.sort,attempt]);   // eslint-disable-line react-hooks/exhaustive-deps

  const change=(patch:Partial<PeopleQuery>)=>{
    cache.current.clear();setReady(null);setClearOnLoad(true);setPage(null);
    setQ(prev=>({...prev,...patch,after:'',dir:'next'}));
  };
  const go=(dir:'next'|'prev')=>{
    if(!page)return;
    const cursor=dir==='next'?page.next:page.prev;if(!cursor)return;
    setClearOnLoad(false);
    setQ(prev=>({...prev,after:cursor,dir}));
  };

  // area options load on first interaction
  const loadAreas=useCallback(()=>{
    if(areaOpts||!meta)return;
    Promise.all(meta.levels.map(l=>getAreas(meta,l).then(a=>a.ids.map((id,i):Opt=>({id:l+':'+id,label:a.labels[i]||areaLabel(l,id),group:LEVEL_LABEL[l]}))
      .filter((_o,i)=>a.status[i]==='ok')))).then(all=>setAreaOpts(all.flat())).catch(()=>setAreaOpts([]));
  },[areaOpts,meta]);
  useEffect(()=>{if(q.level&&q.area)loadAreas();},[q.level,q.area,loadAreas]);
  const areaValue=q.level&&q.area?q.level+':'+q.area:'all';
  const areaShown=q.level&&q.area?(areaOpts?.find(o=>o.id===areaValue)?.label??areaLabel(q.level,q.area)):`All of ${meta?.district_label??'Congressional District 5'}`;

  const sup=meta?.weights.sup!==undefined;      // a candidate district whose overall counts candidate support
  const sorted=q.sort==='sup'?'candidate support':'overall standing';
  const sub=`${meta?int(meta.people):''} people ${MIDDOT} ${weightsText(meta?.weights)}`;
  const pagerText=page?`${int(page.first)}${page.first===page.last?'':ENDASH+int(page.last)} of ${int(page.total)}`:'';

  return <div className="screen people-screen">
    <PageHeader meta={meta} active="people"/>
    <main className="people">
      <h1 className="page-title">People</h1>
      <p className="page-sub">{meta?sub:<Sk w={520} h={16}/>}</p>
      {meta?.overall_note&&<p className="page-sub">{meta.overall_note}</p>}
      <div className="filters">
        <Select label="Registration" shown={REG.find(r=>r.id===q.reg)!.label} value={q.reg} options={REG} onChange={v=>change({reg:v as PeopleQuery['reg']})}/>
        {sup&&<Select label="Sort" shown={q.sort==='sup'?'Candidate support':'Overall standing'} value={q.sort||'ovr'} options={SORT} onChange={v=>change({sort:v==='sup'?'sup':''})}/>}
        <Select label="Grade" shown={GRADE.find(r=>r.id===q.grade)!.label} value={q.grade} options={GRADE} onChange={v=>change({grade:v as PeopleQuery['grade']})}/>
        <Select label="Outlook" shown={LEAN.find(r=>r.id===q.lean)!.label} value={q.lean} options={LEAN} onChange={v=>change({lean:v as PeopleQuery['lean']})}/>
        <div className="select-area" onPointerDown={loadAreas} onFocus={loadAreas}>
          <label className="select">
            <span className="select-text" title={`Area: ${areaShown}`}>Area: {areaShown}</span>
            <svg className="select-chev" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"/></svg>
            <select aria-label="Area" value={areaValue} onChange={e=>{
              const v=e.target.value;
              if(v==='all')change({level:'',area:''});
              else{const [l,...rest]=v.split(':');change({level:l as Level,area:rest.join(':')});}
            }}>
              <option value="all">All of {meta?.district_label??'Congressional District 5'}</option>
              {q.level&&q.area&&!areaOpts&&<option value={areaValue}>{areaShown}</option>}
              {(['ZIP','Tract','Precinct','Block'] as const).map(g=>{
                const list=(areaOpts??[]).filter(o=>o.group===g);
                return list.length?<optgroup key={g} label={g}>{list.map(o=><option key={o.id} value={o.id}>{o.label}</option>)}</optgroup>:null;
              })}
            </select>
          </label>
        </div>
      </div>
      <div className="people-list-tools">
        <div className="sample-option"><Switch className="balanced" checked={q.balanced} onChange={v=>change({balanced:v})} label="Balanced by registration" labelClass="balanced-label"/><span>Optional registration-balanced view</span></div>
        {(q.reg!=='all'||q.grade!=='ABCD'||q.lean!=='all'||q.area||q.balanced)&&<button type="button" className="clear-people-filters" onClick={()=>change({reg:'all',grade:'ABCD',lean:'all',level:'',area:'',balanced:false})}>Reset filters</button>}
        <span className="pager-text">{pagerText||' '}</span>
        <button type="button" className="pager-btn" aria-label="Previous page" disabled={!page?.prev} onClick={()=>go('prev')}><ChevronLeft strokeWidth={1.75}/></button>
        <button type="button" className="pager-btn" aria-label="Next page" disabled={!page?.next} onClick={()=>go('next')}><ChevronRight strokeWidth={1.75}/></button>
      </div>
      <p className="people-context">{q.balanced?'Showing matches from the registration-balanced selection; ties are retained.':'Showing matches from the full local dataset.'} Sorted by {sorted}. Political responses are unscored.</p>
      <p className="scroll-hint">Scroll the table sideways to see all columns.</p>
      <section className={cx('table',sup&&'has-sup')} aria-label={`People ranked by ${sorted}`} aria-busy={busy}>
        <div className="thead">
          <span>Rank</span><span>Name</span><span>Overall standing</span><span className="r">Alignment</span>{sup&&<span className="r">Candidate support</span>}<span className="r">Participation</span>
          <span className="r">Sphere</span><span>Political outlook</span><span title="Data coverage grade">Data grade</span><span>Percentile within registration</span>
        </div>
        <div className="tbody">
          {error&&<div className="table-empty" role="alert">{error}<button className="retry-people" type="button" onClick={()=>setAttempt(n=>n+1)}>Retry people</button></div>}
          {!error&&page&&page.rows.length===0&&<div className="table-empty">No people match these filters.</div>}
          {!error&&(page?.rows??[]).map(r=><Row key={r.person_id} r={r} sup={sup}/>)}
          {!error&&!page&&clearOnLoad&&Array.from({length:8}).map((_,i)=><SkeletonRow key={i} sup={sup}/>)}
        </div>
        <div className="tfoot">
          <span>{page?`Page ${int(page.page)} of ${int(page.pages)}`:<Sk w={150} h={14}/>}</span>
          {ready&&<span className="ready"><i/>Next page ready</span>}
        </div>
      </section>
    </main>
  </div>;
}

function Row({r,sup}:{r:PeopleRow;sup:boolean}){
  const band=r.lean_code==='U'?null:leanBandByCode(r.lean_code);
  return <Link to={`/people/${encodeURIComponent(r.person_id)}`} className="trow">
    <span className="c-rank">{r.rank}</span>
    <span className="c-name" title={r.name}>{r.name}</span>
    <span className="c-overall"><b>{round(r.ovr_pct)}</b><RangeBar variant="table" value={r.ovr_pct} lo={r.lo} hi={r.hi}/><em>{RANGE(r.lo,r.hi)}</em></span>
    <span className="c-num r">{round(r.cua_pct)}</span>{sup&&<span className="c-num r">{r.sup_pct==null?'':round(r.sup_pct)}</span>}<span className="c-num r">{round(r.part_pct)}</span><span className="c-num r">{round(r.soi_pct)}</span>
    <span className="c-lean" title={r.lean_label}>{r.lean_label}</span>
    <span className="c-grade"><GradeBox grade={r.ovr_grade} size="md"/></span>
    <span className="c-within">{ordinal(r.within_pct)} {MIDDOT} {r.within_group}</span>
  </Link>;
}
function SkeletonRow({sup}:{sup:boolean}){
  return <div className="trow sk-row" aria-hidden="true">
    <span><Sk w={30} h={14}/></span><span><Sk w={200} h={14}/></span><span><Sk w={80} h={14}/></span>
    <span className="r"/>{sup&&<span className="r"/>}<span className="r"/><span className="r"/><span/><span/><span/>
  </div>;
}
