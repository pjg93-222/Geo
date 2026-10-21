// Shared identity/search and persistent section navigation. The map adds its index and geography controls.
import {useEffect,useMemo,useRef,useState} from 'react';
import {createPortal,flushSync} from 'react-dom';
import {Search} from 'lucide-react';
import {LoadingTerrier,TerrierMark} from './TerrierMark';
import {Segmented,cx} from './ui';
import {navigate,withKept,LOCATION_EVENT,type RouteName} from '../lib/router';
import {int,countLabel,areaLabel,districtHref,districtOptions,LEVELS,LEVEL_LABEL,type DistrictEntry,type DistrictList,type Level} from '../lib/format';
import {getAreas,getFeatures,localJson,searchPeople} from '../api/client';
import {ALL_LAYERS,LAYER_NAME,industryChoices,raceBadge} from '../lib/overlay';
import type {Features,Group,Meta,OverlayLayer} from '../types';
import type {DesktopDataBridge} from '../api/desktopData';
import {useDistrictDataSource} from './DistrictDataSource';
import {districtDataSummary} from '../lib/desktopData';

/** Every layer the Index control can show; the district's /api/v3/features lists the ones it offers. */
export const INDEX_ITEMS:{id:OverlayLayer;label:string}[]=ALL_LAYERS.map(id=>({id,label:id==='lean'?'Registration':LAYER_NAME[id]}));
export const INDEX_NAME:Record<OverlayLayer,string>={
  ovr:'Overall standing',cua:'Credit Union Alignment',part:'Participation',soi:'Sphere of Influence',lean:'Registration',
  sup:'Candidate support',pop_density:'Population density',voter_density:'Voter density',untapped:'Untapped',
};
export const INDEX_TOP10_NAME:Record<Exclude<Group,'lean'>,string>={
  ovr:'overall',cua:'Credit Union Alignment',part:'Participation',soi:'Sphere of Influence',
};

export function Link(p:{to:string;className?:string;children:React.ReactNode;title?:string;'aria-current'?:'page'}){
  return <a href={withKept(p.to)} className={p.className} title={p.title} aria-current={p['aria-current']}
    onClick={e=>{if(e.metaKey||e.ctrlKey||e.shiftKey||e.button!==0)return;e.preventDefault();navigate(p.to);}}>{p.children}</a>;
}

function Brand({busy=false}:{busy?:boolean}){
  const {data}=useDistrictDataSource(useFeatures()?.district);
  return <Link to="/" className="brand">
    <span className="brand-mark"><TerrierMark busy={busy}/></span>
    <span className="brand-copy"><span className="brand-text">Terrier</span><span className="demo-label">{data?'GitHub data':'Local data'}</span></span>
  </Link>;
}
function DataSourceLink(){
  const {data}=useDistrictDataSource(useFeatures()?.district);
  return <Link to="/sources" className="data-context" title={data?districtDataSummary(data):undefined}>{data?'GitHub district data':'Local dataset'} <span>{data?.cached?'Verified cache · ':''}Data & sources ↗</span></Link>;
}

declare global {interface Window {desktop?:DesktopDataBridge&{districts:()=>Promise<DistrictList>;switchDistrict:(id:string)=>Promise<boolean>;rendererReady?:()=>Promise<void>}}}
// Desktop uses its verified native district mapping. Browser deployments retain their separate server addresses.
// It stays hidden when the call fails or only one district exists.
let districtList:Promise<DistrictList|null>|null=null;
function DistrictSelect(){
  const [list,setList]=useState<DistrictList|null>(null);
  const [error,setError]=useState('');
  const [pending,setPending]=useState<DistrictEntry|null>(null);const changing=useRef(false);
  useEffect(()=>{
    if(!pending)return;
    const root=document.getElementById('root');if(root){root.inert=true;root.setAttribute('aria-hidden','true');}
    const frame=requestAnimationFrame(()=>performance.mark('terrier:district-overlay-painted'));
    return()=>{cancelAnimationFrame(frame);if(root){root.inert=false;root.removeAttribute('aria-hidden');}};
  },[pending]);
  useEffect(()=>{
    let live=true;
    districtList??=(window.desktop?.districts?window.desktop.districts():localJson<DistrictList>('/api/v3/districts')).catch(()=>null);
    districtList.then(l=>{if(live)setList(l);});
    return ()=>{live=false;};
  },[]);
  if(!list||list.districts.length<2)return null;
  async function change(d:DistrictEntry){
    if(changing.current||d.id===list!.current)return;
    if(!window.desktop?.switchDistrict){location.assign(districtHref(d,location));return;}
    changing.current=true;performance.mark('terrier:district-switch-click');
    flushSync(()=>{setError('');setPending(d);});
    try{
      const opened=await window.desktop.switchDistrict(d.id);
      if(opened)return; // The native host navigates this same window; keep the cover until that navigation.
      setError('District change cancelled. Your current district remains open.');
    }catch{setError('The district could not be opened. Your current district remains open. Try again.');}
    changing.current=false;setPending(null);performance.mark('terrier:district-switch-reset');
  }
  return <><select className="district-select" aria-label="District" disabled={!!pending} aria-busy={!!pending} value={pending?.id??list.current} onChange={e=>{
    const d=list.districts.find(x=>x.id===e.target.value);
    if(d)void change(d);
  }}>{districtOptions(list.districts,list.current).map(o=><option key={o.id} value={o.id} disabled={o.disabled}>{o.label}</option>)}</select>{error&&<span className="terrier-district-error" role="alert">{error}</span>}
    {pending&&createPortal(<LoadingTerrier startup label={`Opening ${pending.label}…`}/>,document.body)}</>;
}

// This district's settings, read once per page load; a failed read leaves the Colorado-style defaults.
let featuresCall:Promise<Features|null>|null=null;
export function preloadFeatures(){return featuresCall??=getFeatures().catch(()=>null);}
export function useFeatures():Features|null{
  const [f,setF]=useState<Features|null>(null);
  useEffect(()=>{
    let live=true;
    preloadFeatures().then(x=>{if(live)setF(x);});
    return ()=>{live=false;};
  },[]);
  return f;
}
/** The race this district is built for ("Bynum (D) · General election"); shown on every screen of a candidate district. */
function RaceBadge(){
  const text=raceBadge(useFeatures()?.candidate);
  return text?<span className="race-badge">{text}</span>:null;
}

interface Hit{kind:'area'|'person';key:string;label:string;sub:string;go:()=>void}

function SearchBox({meta}:{meta:Meta|null}){
  const [q,setQ]=useState('');const [open,setOpen]=useState(false);
  const [active,setActive]=useState(0);const [searching,setSearching]=useState(false);const [searchError,setSearchError]=useState('');
  const [people,setPeople]=useState<Hit[]>([]);const [labels,setLabels]=useState<{level:Level;id:string;label:string}[]>([]);
  const box=useRef<HTMLDivElement>(null);

  useEffect(()=>{
    if(!open||!meta||labels.length)return;
    let live=true;
    Promise.all(meta.levels.map(l=>getAreas(meta,l).then(a=>a.ids.map((id,i)=>({level:l,id,label:a.labels[i]||areaLabel(l,id),ok:a.status[i]==='ok'}))))).then(all=>{
      if(live)setLabels(all.flat().map(({level,id,label})=>({level,id,label})));
    }).catch(()=>{});
    return ()=>{live=false;};
  },[open,meta,labels.length]);

  useEffect(()=>{
    const text=q.trim();
    setPeople([]);setActive(0);setSearchError('');
    if(text.length<2){setSearching(false);return;}
    setSearching(true);
    const ac=new AbortController();
    const t=setTimeout(()=>{
      searchPeople(text,ac.signal).then(r=>{
        setSearching(false);
        setPeople(r.people.map(p=>({kind:'person' as const,key:p.person_id,label:p.name,sub:p.block_label,go:()=>navigate('/people/'+encodeURIComponent(p.person_id))})));
      }).catch(e=>{if(!ac.signal.aborted){setSearching(false);setSearchError('Search unavailable. Try again.');}});
    },160);
    return ()=>{clearTimeout(t);ac.abort();};
  },[q]);

  useEffect(()=>{
    const close=(e:MouseEvent)=>{if(box.current&&!box.current.contains(e.target as Node))setOpen(false);};
    document.addEventListener('mousedown',close);return ()=>document.removeEventListener('mousedown',close);
  },[]);

  const areas=useMemo<Hit[]>(()=>{
    const text=q.trim().toLowerCase();if(text.length<2)return [];
    const out:Hit[]=[];
    for(const a of labels){
      if(a.label.toLowerCase().includes(text)||a.id.includes(text)){
        out.push({kind:'area',key:a.level+a.id,label:a.label,sub:LEVEL_LABEL[a.level],
          go:()=>navigate(`/?level=${a.level}&sel=${encodeURIComponent(a.id)}&f=${Date.now()}`)});
        if(out.length>=6)break;
      }
    }
    return out;
  },[q,labels]);
  const hits=[...areas,...people];

  function pick(h:Hit){setOpen(false);setQ('');h.go();}
  return <div className="search" ref={box}>
    <Search strokeWidth={1.75} aria-hidden="true"/>
    <input type="search" role="combobox" aria-autocomplete="list" aria-expanded={open&&q.trim().length>=2} aria-controls="search-results" aria-activedescendant={open&&hits[active]?`search-hit-${active}`:undefined} value={q} placeholder="Search areas or people" aria-label="Search areas or people" autoComplete="off" spellCheck={false}
      onFocus={()=>setOpen(true)} onChange={e=>{setQ(e.target.value);setOpen(true);}}
      onKeyDown={e=>{
        if(e.key==='Escape')setOpen(false);
        if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();setOpen(true);setActive(i=>Math.max(0,Math.min(hits.length-1,i+(e.key==='ArrowDown'?1:-1))));}
        if(e.key==='Enter'&&hits[active]){e.preventDefault();pick(hits[active]);}
      }}/>
    {open&&q.trim().length>=2&&<div id="search-results" className="search-pop" role="listbox" aria-label="Search results">
      {hits.length===0&&<div className="search-empty" role="status">{searching?'Searching…':searchError||'No match'}</div>}
      {hits.map((h,i)=><button key={h.key} id={`search-hit-${i}`} type="button" role="option" aria-selected={i===active} onMouseEnter={()=>setActive(i)} onClick={()=>pick(h)}>
        <span className="search-name">{h.label}</span><span className="search-sub">{h.sub}</span>
      </button>)}
    </div>}
  </div>;
}

/** `index` is the single layer on show, or null while a blend or two-layer map is; `layers` are the ones this district offers. */
export function MapHeader(p:{meta:Meta|null;busy?:boolean;index:OverlayLayer|null;level:Level;onIndex:(g:OverlayLayer)=>void;onLevel:(l:Level)=>void;
  features:Features|null;layers:OverlayLayer[];industry:string;onIndustry:(id:string)=>void}){
  const industries=industryChoices(p.features);
  const industryLabel=p.features?.industries.find(i=>i.id===p.industry)?.label;
  const items=INDEX_ITEMS.filter(i=>p.layers.includes(i.id)).map(i=>i.id==='cua'&&industryLabel?{...i,label:industryLabel}:i);
  return <header className="topbar map">
    <div className="masthead"><Brand busy={p.busy}/><DataSourceLink/><DistrictSelect/><RaceBadge/><div className="topbar-right"><SearchBox meta={p.meta}/></div></div>
    <SectionNav active="map"/>
    <div className="map-toolbar">
      <div className="seg-slot index"><Segmented<OverlayLayer|''> variant="index" label="Index" items={items} value={p.index??''} onChange={v=>{if(v)p.onIndex(v);}}/></div>
      {p.features&&!p.features.candidate&&<span className="untapped-note">Untapped needs a candidate: see Oregon District 5 or Connecticut District 1.</span>}
      {industries&&<select className="district-select" aria-label="Industry" value={p.industry} onChange={e=>p.onIndustry(e.target.value)}>
        {industries.map(i=><option key={i.id} value={i.id}>{i.label}</option>)}</select>}
      <div className="seg-slot level"><Segmented variant="level" label="Level" items={LEVELS.map(l=>({id:l,label:LEVEL_LABEL[l]}))} value={p.level} onChange={p.onLevel}/></div>
      <div className="counts">{p.meta
        ?<><Link to="/people">{countLabel(p.meta.people,'person','people')}</Link> {'·'} {countLabel(p.meta.households,'household')}</>
        :<span className="sk" style={{width:'calc(250 * var(--u))',height:'calc(14 * var(--u))'}}/>}</div>
    </div>
  </header>;
}

type Section=Exclude<RouteName,'profile'>;
const tabs:{id:Section;label:string;to:string}[]=[
  {id:'map',label:'Map',to:'/'},{id:'people',label:'People',to:'/people'},
  {id:'contacts',label:'Contact sheet',to:'/contacts'},{id:'criteria',label:'Criteria audit',to:'/criteria'},
  {id:'sources',label:'Data & sources',to:'/sources'},{id:'workspace',label:'Team & snapshots',to:'/workspace'},
  {id:'method',label:'Method',to:'/method'},{id:'walk',label:'Walk list',to:'/walk'},
];
function SectionNav({active}:{active:Section}){
  const [viewPath,setViewPath]=useState(()=>location.pathname+location.search);
  const nav=useRef<HTMLElement>(null);
  useEffect(()=>{
    const sync=()=>setViewPath(location.pathname+location.search);sync();
    window.addEventListener(LOCATION_EVENT,sync);window.addEventListener('popstate',sync);
    return ()=>{window.removeEventListener(LOCATION_EVENT,sync);window.removeEventListener('popstate',sync);};
  },[]);
  useEffect(()=>{
    const container=nav.current;const current=container?.querySelector<HTMLElement>('[aria-current="page"]');
    if(container&&current){
      const left=current.offsetLeft-container.offsetLeft;const right=left+current.offsetWidth;
      if(left<container.scrollLeft)container.scrollLeft=Math.max(0,left-16);
      else if(right>container.scrollLeft+container.clientWidth)container.scrollLeft=right-container.clientWidth+16;
    }
  },[active]);
  return <div className="section-bar"><nav ref={nav} className="tabs" aria-label="Sections">
    {tabs.map(t=><Link key={t.id} to={t.to} className={cx('tab',t.id===active&&'on')} aria-current={t.id===active?'page':undefined}>{t.label}</Link>)}
  </nav><label className="mobile-section"><span>Section</span><select aria-label="Open section" value={active} onChange={e=>navigate(tabs.find(t=>t.id===e.target.value)!.to)}>{tabs.map(t=><option key={t.id} value={t.id}>{t.label}</option>)}</select></label>{(active==='map'||active==='people')&&<a className="save-view-link" href={withKept('/workspace?from='+encodeURIComponent(viewPath))} onClick={e=>{if(e.metaKey||e.ctrlKey||e.shiftKey||e.button!==0)return;e.preventDefault();navigate('/workspace?from='+encodeURIComponent(location.pathname+location.search));}}>Save view</a>}</div>;
}
export function PageHeader(p:{meta:Meta|null;busy?:boolean;active:Section}){
  return <header className="topbar page">
    <div className="masthead"><Brand busy={p.busy}/><DataSourceLink/><DistrictSelect/><RaceBadge/><div className="topbar-right"><SearchBox meta={p.meta}/></div></div>
    <SectionNav active={p.active}/>
  </header>;
}
