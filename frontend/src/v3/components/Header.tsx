// Top bar (FIDELITY_SPEC section 3.5): one bar on every screen. Map variant carries the index and level controls;
// page variant carries the Map / People / Method tabs. Both carry the search field.
import {useEffect,useMemo,useRef,useState} from 'react';
import {Search} from 'lucide-react';
import {TerrierMark} from './TerrierMark';
import {Segmented,cx} from './ui';
import {navigate,withKept} from '../lib/router';
import {int,countLabel,areaLabel,LEVELS,LEVEL_LABEL,type Level} from '../lib/format';
import {getAreas,searchPeople} from '../api/client';
import type {Group,Meta} from '../types';

export const INDEX_ITEMS:{id:Group;label:string}[]=[
  {id:'ovr',label:'Overall'},{id:'cua',label:'Credit Union Alignment'},{id:'part',label:'Participation'},
  {id:'soi',label:'Sphere of Influence'},{id:'lean',label:'Registration'},
];
export const INDEX_NAME:Record<Group,string>={
  ovr:'Overall standing',cua:'Credit Union Alignment',part:'Participation',soi:'Sphere of Influence',lean:'Registration',
};
export const INDEX_TOP10_NAME:Record<Exclude<Group,'lean'>,string>={
  ovr:'overall',cua:'Credit Union Alignment',part:'Participation',soi:'Sphere of Influence',
};

export function Link(p:{to:string;className?:string;children:React.ReactNode;title?:string;'aria-current'?:'page'}){
  return <a href={withKept(p.to)} className={p.className} title={p.title} aria-current={p['aria-current']}
    onClick={e=>{if(e.metaKey||e.ctrlKey||e.shiftKey||e.button!==0)return;e.preventDefault();navigate(p.to);}}>{p.children}</a>;
}

function Brand({busy=false}:{busy?:boolean}){
  return <Link to="/" className="brand">
    <span className="brand-mark"><TerrierMark busy={busy}/></span>
    <span className="brand-copy"><span className="brand-text">Terrier</span><span className="demo-label">Test-dummy preview</span></span>
  </Link>;
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
    Promise.all(LEVELS.map(l=>getAreas(meta,l).then(a=>a.ids.map((id,i)=>({level:l,id,label:a.labels[i]||areaLabel(l,id),ok:a.status[i]==='ok'}))))).then(all=>{
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

export function MapHeader(p:{meta:Meta|null;busy?:boolean;index:Group;level:Level;onIndex:(g:Group)=>void;onLevel:(l:Level)=>void}){
  return <header className="topbar map">
    <Brand busy={p.busy}/>
    <div className="seg-slot index"><Segmented variant="index" label="Index" items={INDEX_ITEMS} value={p.index} onChange={p.onIndex}/></div>
    <div className="seg-slot level"><Segmented variant="level" label="Level" items={LEVELS.map(l=>({id:l,label:LEVEL_LABEL[l]}))} value={p.level} onChange={p.onLevel}/></div>
    <div className="topbar-right">
      <SearchBox meta={p.meta}/>
      <div className="counts">{p.meta
        ?<><Link to="/people">{countLabel(p.meta.people,'person','people')}</Link> {'·'} {countLabel(p.meta.households,'household')}</>
        :<span className="sk" style={{width:'calc(250 * var(--u))',height:'calc(14 * var(--u))'}}/>}</div>
    </div>
  </header>;
}

export function PageHeader(p:{meta:Meta|null;busy?:boolean;active:'map'|'people'|'method'}){
  const tabs:{id:'map'|'people'|'method';label:string;to:string}[]=[{id:'map',label:'Map',to:'/'},{id:'people',label:'People',to:'/people'},{id:'method',label:'Method',to:'/method'}];
  return <header className="topbar page">
    <Brand busy={p.busy}/>
    <nav className="tabs" aria-label="Sections">
      {tabs.map(t=><Link key={t.id} to={t.to} className={cx('tab',t.id===p.active&&'on')} aria-current={t.id===p.active?'page':undefined}>{t.label}</Link>)}
    </nav>
    <div className="topbar-right"><SearchBox meta={p.meta}/></div>
  </header>;
}
