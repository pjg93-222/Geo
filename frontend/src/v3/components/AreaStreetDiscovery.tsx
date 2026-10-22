import {Suspense,lazy,useEffect,useMemo,useRef,useState} from 'react';
import {getPublicStreetCatalogs,isLocalOnlyDenial,queryPublicStreets} from '../api/publicStreets.ts';
import type {PublicBounds,PublicPoint,PublicStreetCatalog,PublicStreetQuery} from '../api/publicStreets.ts';
import {boundsPolygon,groupPublicStreets,polygonBounds,publicExportFiles,PUBLIC_SCOPE_WARNING,validatePublicPolygon} from '../lib/publicStreetGroups.ts';
import {buildZip,downloadZip} from '../lib/walkZip.ts';
import '../styles/public-streets.css';
const AreaStreetMap=lazy(()=>import('./AreaStreetMap.tsx').then(m=>({default:m.AreaStreetMap})));
const count=(n:number)=>n.toLocaleString('en-US');
const message=(e:unknown)=>e instanceof Error?e.message:'Public street data could not be loaded.';
const safeLink=(url:string)=>/^https?:\/\//i.test(url)?url:undefined;
const polygonText=(p:PublicPoint[])=>p.map(v=>v.join(', ')).join('\n');

/** The street routes are local-only and the features payload carries no local-admin flag, so the panel stays hidden until the catalogs request answers, and a 403 keeps it hidden. */
export function AreaStreetDiscovery(){
  const [open,setOpen]=useState(false);
  const [catalogs,setCatalogs]=useState<PublicStreetCatalog[]>([]),[catalogError,setCatalogError]=useState(''),[catalogLoading,setCatalogLoading]=useState(true),[catalogRetry,setCatalogRetry]=useState(0),[access,setAccess]=useState<null|'shown'|'denied'>(null);
  useEffect(()=>{
    const ac=new AbortController();setCatalogLoading(true);setCatalogError('');
    getPublicStreetCatalogs(ac.signal).then(r=>{if(ac.signal.aborted)return;setCatalogs(r.catalogs);setAccess('shown');setCatalogLoading(false);}).catch(e=>{if(!ac.signal.aborted){if(isLocalOnlyDenial(e))setAccess('denied');else{setCatalogError(message(e));setAccess('shown');}setCatalogLoading(false);}});
    return ()=>ac.abort();
  },[catalogRetry]);
  if(access!=='shown')return null;
  return <details className="public-streets" open={open} onToggle={e=>setOpen(e.currentTarget.open)}>
    <summary><span>Discover streets in an area</span><small>Public geography · residential estimates</small></summary>
    {open&&<DiscoveryBody catalogs={catalogs} catalogLoading={catalogLoading} catalogError={catalogError} onRetry={()=>setCatalogRetry(n=>n+1)}/>}
  </details>;
}
function DiscoveryBody({catalogs,catalogLoading,catalogError,onRetry}:{catalogs:PublicStreetCatalog[];catalogLoading:boolean;catalogError:string;onRetry:()=>void}){
  const [catalogId,setCatalogId]=useState('');
  const [request,setRequest]=useState<{id:string;polygon:PublicPoint[]}|null>(null),[queryRetry,setQueryRetry]=useState(0);
  const [response,setResponse]=useState<{request:object;value:PublicStreetQuery}|null>(null),[loading,setLoading]=useState(false),[error,setError]=useState(''),[note,setNote]=useState('');
  const [selected,setSelected]=useState<Set<string>>(new Set()),[search,setSearch]=useState('');
  const [box,setBox]=useState(['','','','']),[coordinates,setCoordinates]=useState('');
  const [drawing,setDrawing]=useState(false),[draft,setDraft]=useState<PublicPoint[]>([]);
  const [minText,setMinText]=useState('100'),[maxText,setMaxText]=useState('150');
  const queryEpoch=useRef(0),catalog=catalogs.find(c=>c.id===catalogId);
  const data=response?.request===request&&response.value.catalog.id===catalogId?response.value:null;
  useEffect(()=>{setCatalogId(id=>catalogs.some(c=>c.id===id)?id:catalogs[0]?.id??'');},[catalogs]);
  useEffect(()=>{
    if(!catalog){setRequest(null);setResponse(null);return;}
    const polygon=boundsPolygon(catalog.bbox);setRequest({id:catalog.id,polygon});setCoordinates(polygonText(polygon));setBox(catalog.bbox.map(String));setNote('');setDrawing(false);setDraft([]);setSearch('');
  },[catalog?.id]);
  useEffect(()=>{
    const epoch=++queryEpoch.current;if(!request)return;
    const ac=new AbortController();setLoading(true);setError('');setResponse(null);setSelected(new Set());
    queryPublicStreets(request.id,request.polygon,ac.signal).then(r=>{
      if(ac.signal.aborted||epoch!==queryEpoch.current)return;
      setResponse({request,value:r});setSelected(new Set(r.streets.map(s=>s.id)));setLoading(false);
    }).catch(e=>{if(!ac.signal.aborted&&epoch===queryEpoch.current){setError(message(e));setLoading(false);}});
    return ()=>ac.abort();
  },[request,queryRetry]);
  useEffect(()=>{if(!drawing)return;const cancel=(e:KeyboardEvent)=>{if(e.key==='Escape'){setDrawing(false);setDraft([]);setNote('Drawing cancelled. The queried area is unchanged.');}};window.addEventListener('keydown',cancel);return ()=>window.removeEventListener('keydown',cancel);},[drawing]);
  function applyPolygon(points:PublicPoint[]){
    if(!catalog)return;
    try{const polygon=validatePublicPolygon(points,catalog.bbox);setRequest({id:catalog.id,polygon});setCoordinates(polygonText(polygon));setBox(polygonBounds(polygon).map(String));setDrawing(false);setDraft([]);setNote('');}
    catch(e){setNote(message(e));}
  }
  const filtered=useMemo(()=>data?.streets.filter(s=>s.label.toLowerCase().includes(search.trim().toLowerCase()))??[],[data,search]);
  const chosen=useMemo(()=>data?.streets.filter(s=>selected.has(s.id))??[],[data,selected]);
  const grouping=useMemo(()=>{
    try{if(!/^\d+$/.test(minText)||!/^\d+$/.test(maxText))throw new Error('Enter whole-number minimum and maximum estimates.');return {value:groupPublicStreets(chosen,Number(minText),Number(maxText)),error:''};}
    catch(e){return {value:null,error:message(e)};}
  },[chosen,minText,maxText]);
  const groups=grouping.value;
  function toggle(id:string){setSelected(old=>{const next=new Set(old);if(next.has(id))next.delete(id);else next.add(id);return next;});}
  function download(){
    if(!data||!groups?.groups.length)return;
    const {catalog:c}=data,info={catalogId:c.id,catalog:c.label,sourceDate:c.source_date,basis:data.count_basis,polygon:data.polygon,min:Number(minText),max:Number(maxText),generatedAt:new Date().toISOString(),sources:c.sources};
    try{downloadZip('public-street-groups.zip',buildZip(publicExportFiles(groups,info)));}catch(e){setNote(message(e));}
  }
  return <div className="public-street-body">
    <p className="public-street-intro">Choose public coverage, refine an area, then divide selected street names into groups by estimated residential points.</p>
    {catalogLoading&&<p role="status">Loading available coverage…</p>}
    {catalogError&&<p className="public-street-alert" role="alert">{catalogError} <button type="button" onClick={onRetry}>Retry coverage</button></p>}
    {!catalogLoading&&!catalogError&&!catalogs.length&&<p role="status">No public street coverage has been prepared on this server.</p>}
    {!!catalogs.length&&<label className="public-street-label">Coverage<select value={catalogId} onChange={e=>setCatalogId(e.target.value)}>{catalogs.map(c=><option key={c.id} value={c.id}>{c.label}</option>)}</select></label>}
    {catalog&&<>
      <p className="public-street-meta">{count(catalog.street_count)} streets · {count(catalog.estimated_doors)} residential estimates across loaded coverage{catalog.source_date?` · source date ${catalog.source_date}`:''}</p>
      <p className="public-street-basis"><strong>Count basis:</strong> {data?.count_basis??catalog.count_basis}</p>
      <ul className="public-street-sources">{catalog.sources.map((source,i)=><li key={i}>{safeLink(source.url)?<a href={source.url} target="_blank" rel="noreferrer">{source.label}</a>:source.label}</li>)}</ul>
      <div className="public-street-tools"><button type="button" aria-pressed={drawing} onClick={()=>{setDrawing(v=>!v);setDraft([]);setNote('');}}>{drawing?'Cancel drawing':'Draw area on map'}</button>
        {drawing&&<><button type="button" onClick={()=>setDraft(p=>p.slice(0,-1))} disabled={!draft.length}>Undo corner</button><button type="button" onClick={()=>applyPolygon(draft)} disabled={draft.length<3}>Use drawn area</button><span role="status">{draft.length} corners · click 3 or more points, then use area. Escape cancels.</span></>}
        <button type="button" onClick={()=>applyPolygon(boundsPolygon(catalog.bbox))}>Use full coverage</button></div>
      <Suspense fallback={<p role="status">Loading local map…</p>}><AreaStreetMap coverage={catalog.bbox} polygon={request?.id===catalog.id?request.polygon:[]} draft={draft} drawing={drawing} streets={data?.streets??[]} selected={selected} onPoint={p=>{
        if(p[0]<catalog.bbox[0]||p[0]>catalog.bbox[2]||p[1]<catalog.bbox[1]||p[1]>catalog.bbox[3]){setNote('Choose corners inside the loaded coverage.');return;}
        if(draft.length>=199){setNote('Use at most 199 polygon corners.');return;}setDraft(v=>[...v,p]);
      }}/></Suspense>
      <details className="public-street-coordinates"><summary>Set area with coordinates</summary>
        <form onSubmit={e=>{e.preventDefault();const b=box.map(v=>v.trim()?Number(v):NaN) as PublicBounds;if(!(b[0]<b[2]&&b[1]<b[3])){setNote('West must be smaller than east; south must be smaller than north.');return;}applyPolygon(boundsPolygon(b));}}>
          <div className="public-street-box">{['West longitude','South latitude','East longitude','North latitude'].map((label,i)=><label key={label}>{label}<input inputMode="decimal" value={box[i]} onChange={e=>setBox(v=>v.map((x,j)=>j===i?e.target.value:x))}/></label>)}</div><button type="submit">Use rectangle</button>
        </form>
        <form onSubmit={e=>{e.preventDefault();try{const points=coordinates.trim().split(/\n+/).map(row=>{const parts=row.split(',').map(s=>s.trim());if(parts.length!==2||parts.some(s=>!s))throw new Error('Enter one longitude, latitude pair per line.');return parts.map(Number) as PublicPoint;});applyPolygon(points);}catch(ex){setNote(message(ex));}}}>
          <label className="public-street-label">Polygon corners, longitude then latitude<textarea rows={5} value={coordinates} onChange={e=>setCoordinates(e.target.value)}/></label><button type="submit">Use polygon coordinates</button>
        </form>
      </details>
      {note&&<p className="public-street-alert" role="alert">{note}</p>}
      {loading&&<p role="status">Finding every street in the selected area…</p>}
      {error&&<p className="public-street-alert" role="alert">{error} <button type="button" onClick={()=>setQueryRetry(n=>n+1)}>Retry area</button></p>}
      {data&&<>
        <p className="public-street-total" role="status">{count(data.streets.length)} streets · {count(data.estimated_doors)} estimated residential points in this area</p>
        {!data.streets.length?<p>No public streets intersect this area. Expand the area or choose another coverage.</p>:<>
          <label className="public-street-label">Find street names<input type="search" value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search loaded streets"/></label>
          <div className="public-street-tools"><button type="button" onClick={()=>setSelected(s=>new Set([...s,...filtered.map(r=>r.id)]))}>Select shown</button><button type="button" onClick={()=>setSelected(s=>new Set([...s].filter(id=>!filtered.some(r=>r.id===id))))}>Clear shown</button><span>{count(chosen.length)} selected · {count(chosen.reduce((n,s)=>n+s.estimated_doors,0))} estimates</span></div>
          <ul className="public-street-list" aria-label="All public street names in queried area">{filtered.map(s=><li key={s.id}><label><input type="checkbox" checked={selected.has(s.id)} onChange={()=>toggle(s.id)}/><span>{s.label}</span><strong>{count(s.estimated_doors)}</strong></label>{!s.estimated_doors&&<small>No residential estimate; review separately</small>}</li>)}</ul>
          {!filtered.length&&<p>No street names match this search. Selections outside the search remain selected.</p>}
          <div className="public-street-limits"><label>Minimum estimates per group<input type="number" min={1} max={1000} step={1} value={minText} onChange={e=>setMinText(e.target.value)}/></label><label>Maximum estimates per group<input type="number" min={1} max={1000} step={1} value={maxText} onChange={e=>setMaxText(e.target.value)}/></label></div>
          <p className="public-street-hint">Limits include both endpoints. Whole selected street entries stay together. Groups are first formed from nearby street centers; groups made from leftovers have no distance limit. Road connectivity and occupied doors are not verified.</p>
          {grouping.error&&<p role="alert" className="public-street-alert">{grouping.error}</p>}
          {groups&&<>
            {!!groups.zero.length&&<p className="public-street-alert">{groups.zero.length} streets have zero estimated points and are excluded from counted groups: {groups.zero.map(s=>s.label).join('; ')}.</p>}
            {!!groups.oversized.length&&<p className="public-street-alert">Over maximum and unassigned: {groups.oversized.map(s=>`${s.label} (${s.estimated_doors})`).join('; ')}. Raise the limit or define smaller source street entries.</p>}
            {groups.reason&&<p className="public-street-alert">{groups.reason}</p>}
            {!!groups.leftovers.length&&<p className="public-street-alert">Unassigned balance: {groups.leftovers.map(s=>`${s.label} (${s.estimated_doors})`).join('; ')}. These streets are listed in not-grouped.csv, not in a group file.</p>}
            <ol className="public-street-groups">{groups.groups.map((g,i)=><li key={i}><strong>Group {i+1} · {g.estimated_doors} estimates</strong><p>{g.streets.map(s=>s.label).join(' · ')}</p>{g.regrouped&&<small>Formed from leftovers; streets may be far apart.</small>}</li>)}</ol>
            {!groups.groups.length&&<p>No groups meet the selected limits.</p>}
            <p className="public-street-alert">{PUBLIC_SCOPE_WARNING}</p>
            <button type="button" className="public-street-download" onClick={download} disabled={!groups.groups.length}>Download street groups ZIP</button>
          </>}
        </>}
      </>}
    </>}
  </div>;
}
