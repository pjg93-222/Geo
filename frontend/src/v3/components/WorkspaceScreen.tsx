import {useEffect,useMemo,useState} from 'react';
import {ArrowUpRight,Bookmark,Download,Search,Trash2} from 'lucide-react';
import {GROUP_LABEL,GROUPS,type FieldDef} from '../api/fields';
import {getAreas,localJson} from '../api/client';
import {csvText} from '../lib/csv';
import {int,LEVEL_LABEL,type Level} from '../lib/format';
import {type Route} from '../lib/router';
import {lastView,readSavedViews,removeView,safeViewPath,saveView,type SavedView} from '../lib/savedViews';
import {Link,PageHeader} from './Header';
import type {Meta} from '../types';
import './WorkspaceScreen.css';

type Section='criteria'|'sources'|'workspace';
const TITLES:Record<Section,string>={criteria:'Criteria audit',sources:'Data & sources',workspace:'Team & snapshots'};
const SUBTITLES:Record<Section,string>={criteria:'Inspect the fields, inputs and weights in the active dataset.',sources:'Review the local data and its geographic context.',workspace:'Save useful views and inspect the active snapshot.'};
const FORMATS:Record<FieldDef['kind'],string>={yn:'Yes / No',score:'0–100 input',int:'Count',owner:'Owner / renter category',homeowner:'Homeowner category',dwelling:'Dwelling category',children:'Coded household category',party:'Registration category','lean-yn':'Yes / No response'};
const LAYER_LABELS:Record<string,string>={people:'People & households',areas:'Area values',block:'Census blocks',tract:'Census tracts',precinct:'Precincts',zip:'ZIP areas',district:'District',roads:'Roads'};
const WEIGHT=(n:number)=>`${Number((n*100).toFixed(2))}%`;

function downloadFile(name:string,type:string,content:string){
  const url=URL.createObjectURL(new Blob([content],{type}));const link=document.createElement('a');
  link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}

function viewDestination(path:string){
  const url=new URL(path,'https://terrier.invalid');
  const title=url.pathname==='/'?'Map':url.pathname.startsWith('/people/')?'Person profile':({people:'People',contacts:'Contact sheet',criteria:'Criteria audit',sources:'Data & sources',method:'Method'} as Record<string,string>)[url.pathname.slice(1)]??'Terrier page';
  return `${title}${url.search?' · saved filters':''}`;
}

interface WorkspaceData{
  mode:string;source_sha256:string;people:number;households:number;snapshot:string;basis:string;availableSnapshots:number;
  fields:FieldDef[];layers:{key:string;origin:string;detail:string}[];stores:{name:string;records:number;source_sha256:string}[];
}
function FieldAudit({meta,data}:{meta:Meta;data:WorkspaceData}){
  const FIELDS=data.fields;
  const [group,setGroup]=useState('all');const [query,setQuery]=useState('');
  const fields=useMemo(()=>FIELDS.filter(f=>(group==='all'||f.group===group)&&`${f.label} ${f.key}`.toLowerCase().includes(query.trim().toLowerCase())),[FIELDS,group,query]);
  function exportFields(){
    const rows=[['Field','Group','Input','Weight within index','Included in overall standing'],...fields.map(f=>[f.key,GROUP_LABEL[f.group],FORMATS[f.kind],f.group==='lean'?'Not scored':WEIGHT(f.w),f.group==='lean'?'No':'Yes'])];
    downloadFile('terrier-criteria.csv','text/csv;charset=utf-8',csvText(rows));
  }
  return <>
    <p className="work-context"><span className="work-context-dot" aria-hidden="true"/><span><strong>Political views are not scored.</strong> Registration, affiliation and outlook remain separate categories.</span></p>
    <section className="work-panel" aria-labelledby="dictionary-heading">
      <div className="work-panel-heading"><div><h2 id="dictionary-heading">Field dictionary <span className="work-heading-count">{fields.length} / {FIELDS.length}</span></h2><p>{FIELDS.filter(f=>f.group!=='lean').length} scored inputs · {FIELDS.filter(f=>f.group==='lean').length} political categories</p></div><button type="button" className="work-button" onClick={exportFields}><Download size={16} aria-hidden="true"/>Export shown fields</button></div>
      <div className="work-filter-bar"><label className="work-search"><Search size={17} aria-hidden="true"/><input type="search" aria-label="Search criteria" placeholder="Find a field or source key" value={query} onChange={e=>setQuery(e.target.value)}/></label><label className="work-select-label">Group<select value={group} onChange={e=>setGroup(e.target.value)} aria-label="Criteria group"><option value="all">All groups</option>{GROUPS.map(id=><option value={id} key={id}>{GROUP_LABEL[id]}</option>)}</select></label></div>
      <div className="work-table-scroll" tabIndex={0} aria-label="Field dictionary"><table className="work-table work-criteria-table"><thead><tr><th scope="col">Field</th><th scope="col">Group</th><th scope="col">Input</th><th scope="col" className="work-numeric">Weight within index</th></tr></thead><tbody>{fields.map(f=><tr key={f.key}><th scope="row"><strong>{f.label}</strong><code>{f.key}</code></th><td className="work-field-group">{GROUP_LABEL[f.group]}</td><td data-label="Input">{FORMATS[f.kind]}</td><td className="work-numeric" data-label="Weight">{f.group==='lean'?<span className="work-tag">Not scored</span>:WEIGHT(f.w)}</td></tr>)}</tbody></table>{!fields.length&&<p className="work-empty">No fields match. Try another name or choose all groups.</p>}</div>
      <div className="work-panel-foot">Weights shown apply within each scored index. Political fields retain their response labels.</div>
    </section>
    <div className="work-two-column"><section className="work-prose"><h2>How the indices combine</h2><dl className="work-facts">{GROUPS.filter(id=>id!=='lean').map(id=><div key={id}><dt>{GROUP_LABEL[id]} <small>· {FIELDS.filter(f=>f.group===id).length} fields</small></dt><dd>{meta.weights[id]}%</dd></div>)}</dl><p>These weights form overall standing. Within Sphere of Influence, giving and volunteering each receive 45%, with 10% for household fields.</p><Link to="/method" className="work-text-link">Read the full method <ArrowUpRight size={14} aria-hidden="true"/></Link></section><section className="work-prose"><h2>Reading the evidence</h2><p>Values and evidence details come from the active local snapshot. The existing stored scores and field values are included.</p><p>Missing or conflicting opinions remain Unknown or Multiple responses. Political fields are categorical and have no score contribution. Area summaries come from the active snapshot.</p></section></div>
  </>;
}

interface AreaCount{level:Level;total:number;available:number}
function DataSources({meta,data}:{meta:Meta;data:WorkspaceData}){
  const [counts,setCounts]=useState<AreaCount[]>([]);const [error,setError]=useState('');const [attempt,setAttempt]=useState(0);
  useEffect(()=>{let live=true;setError('');setCounts([]);Promise.all(meta.levels.map(async level=>{const a=await getAreas(meta,level);return {level,total:a.ids.length,available:a.status.filter(s=>s==='ok').length};})).then(rows=>{if(live)setCounts(rows);}).catch(()=>{if(live)setError('Geographic counts could not be loaded.');});return()=>{live=false;};},[meta,attempt]);
  return <>
    <div className="work-stat-grid"><div className="work-stat"><span>People</span><strong>{int(data.people)}</strong><p>Records in the local dataset</p></div><div className="work-stat"><span>Households</span><strong>{int(data.households)}</strong><p>Local household membership</p></div><div className="work-stat"><span>Framework fields</span><strong>{data.fields.length}</strong><p>{data.fields.filter(f=>f.group!=='lean').length} scored · {data.fields.filter(f=>f.group==='lean').length} political categories</p></div><div className="work-stat"><span>Geographic layers</span><strong>{meta.levels.length}</strong><p>Blocks, precincts, tracts and ZIPs</p></div></div>
    <p className="work-context"><span className="work-context-dot" aria-hidden="true"/><span><strong>Connected to the local dataset.</strong> People, contact details, field values and scores come from the prepared local stores. Missing values remain unavailable.</span></p>
    <section className="work-panel" aria-labelledby="source-ledger-heading"><div className="work-panel-heading"><div><h2 id="source-ledger-heading">Source ledger</h2><p>Active snapshot: <code>{data.snapshot}</code> · {meta.district_label}</p></div><span className="work-tag">Local dataset</span></div><div className="work-table-scroll" tabIndex={0} aria-label="Source ledger"><table className="work-table work-source-table"><thead><tr><th scope="col">Dataset</th><th scope="col">Origin</th><th scope="col">Interpretation</th></tr></thead><tbody>{data.layers.map(layer=><tr key={layer.key}><th scope="row">{LAYER_LABELS[layer.key]??layer.key}</th><td>{layer.origin}</td><td>{layer.detail}</td></tr>)}</tbody></table></div></section>
    <div className="work-two-column"><section className="work-prose"><h2>Geographic coverage</h2><p>Published areas in the active snapshot. Small areas follow the snapshot’s minimum-count rules.</p>{error?<div className="work-inline-error" role="alert"><p>{error}</p><button className="work-button" onClick={()=>setAttempt(n=>n+1)}>Retry counts</button></div>:!counts.length?<p role="status">Loading geographic counts…</p>:<dl className="work-facts">{counts.map(row=><div key={row.level}><dt>{LEVEL_LABEL[row.level]}</dt><dd>{int(row.available)} available <small>of {int(row.total)}</small></dd></div>)}</dl>}</section><section className="work-prose"><h2>Reading the fields</h2><p>Registration, affiliation and outlook describe different categorical fields. A person can have different responses across those fields; Terrier preserves them as supplied.</p><p>Unavailable values remain Unknown or Not supplied. Conflicting positive opinion responses remain Multiple responses.</p><Link to="/criteria" className="work-text-link">Inspect fields and weights <ArrowUpRight size={14} aria-hidden="true"/></Link></section></div>
  </>;
}

function SavedWorkspace({meta,route,data}:{meta:Meta;route:Route;data:WorkspaceData}){
  const [views,setViews]=useState<SavedView[]>([]);const [name,setName]=useState('');
  const [path,setPath]=useState(()=>safeViewPath(route.search.get('from'))??lastView());
  const [message,setMessage]=useState('');const [error,setError]=useState('');
  useEffect(()=>{try{setViews(readSavedViews());}catch{setError('Saved views are unavailable in this browser. Check local storage access before saving.');}},[]);
  useEffect(()=>{setPath(safeViewPath(route.search.get('from'))??lastView());},[route]);
  function remove(id:string){try{setViews(removeView(id));setMessage('Saved view removed.');setError('');}catch{setError('The saved view could not be removed. Your stored views have not been changed.');}}
  return <>
    <p className="work-context"><span className="work-context-dot" aria-hidden="true"/><span><strong>Saved on this device.</strong> Views stay in this browser profile after Terrier closes.</span></p>
    <section className="work-panel" aria-labelledby="saved-views-heading"><div className="work-panel-heading"><div><h2 id="saved-views-heading">Saved views</h2><p>Return to a map selection, people filters, or another Terrier page.</p></div><span className="work-tag">{views.length} saved</span></div>
      <form className="work-save-form" onSubmit={e=>{e.preventDefault();try{setViews(saveView(name,path));setName('');setError('');setMessage('View saved to this browser.');}catch(e){setMessage('');setError(e instanceof Error?e.message:'The view could not be saved.');}}}>
        <label>View name<input value={name} onChange={e=>setName(e.target.value)} required maxLength={100} placeholder="For example, ZIP registration overview"/></label>
        <label>Page to save<input value={path} onChange={e=>setPath(e.target.value)} required maxLength={2048} spellCheck={false} placeholder="/?index=lean&level=zip" aria-label="Terrier page and filters" aria-describedby="save-view-help"/></label>
        <button type="submit" className="work-button work-primary"><Bookmark size={16} aria-hidden="true"/>Save view</button>
        <p id="save-view-help">Your last page and filters are filled in. Map views also save the selected area; pan and zoom are not stored.</p>
      </form>
      {message&&<p className="work-feedback" role="status">{message}</p>}{error&&<p className="work-feedback work-error" role="alert">{error}</p>}
      {views.length?<ul className="work-saved-list">{views.map(view=><li key={view.id}><Bookmark size={18} aria-hidden="true"/><div><Link className="work-saved-title" to={view.path}>{view.name}</Link><p>{viewDestination(view.path)} <span aria-hidden="true">·</span> Saved {new Date(view.savedAt).toLocaleDateString()}</p></div><button type="button" className="work-icon-button" onClick={()=>remove(view.id)} aria-label={`Remove saved view ${view.name}`}><Trash2 size={17} aria-hidden="true"/></button></li>)}</ul>:<div className="work-empty"><Bookmark size={24} aria-hidden="true"/><h3>No saved views yet</h3><p>Name the view above to keep a shortcut here.</p></div>}
    </section>
    <div className="work-two-column"><section className="work-prose"><span className="work-eyebrow">Active snapshot</span><h2 className="work-snapshot-name">{meta.snapshot}</h2><p>{meta.district_label} · {int(meta.people)} people in {int(meta.households)} households.</p><dl className="work-facts"><div><dt>Data source</dt><dd>Local dataset</dd></div><div><dt>Address basis</dt><dd>Registration geography</dd></div><div><dt>Available snapshots</dt><dd>{int(data.availableSnapshots)}</dd></div></dl><button type="button" className="work-button" onClick={()=>downloadFile('terrier-snapshot.json','application/json',JSON.stringify({snapshot:meta.snapshot,mode:data.mode,source_sha256:data.source_sha256,people:meta.people,households:meta.households,levels:meta.levels,weights:meta.weights,political_views:'Categorical responses; not scored',area_values:'Active local snapshot'},null,2))}><Download size={16} aria-hidden="true"/>Download snapshot summary</button></section><section className="work-prose"><span className="work-eyebrow">Team access</span><h2 className="work-snapshot-name">Local workspace</h2><p>This workspace runs on your computer. Saved views belong to this browser profile.</p><p>The active dataset is shared by the local People, Contact sheet and Map views.</p><Link to="/sources" className="work-text-link">Review the data sources <ArrowUpRight size={14} aria-hidden="true"/></Link></section></div>
  </>;
}

export function WorkspaceScreen({meta,route}:{meta:Meta;route:Route}){
  const section=route.name as Section;
  const [data,setData]=useState<WorkspaceData|null>(null);const [error,setError]=useState('');const [attempt,setAttempt]=useState(0);
  useEffect(()=>{const ac=new AbortController();setError('');localJson<WorkspaceData>('/api/v3/workspace',ac.signal).then(setData).catch(e=>{if(e.name!=='AbortError')setError(e.message||'The workspace data could not be loaded.');});return()=>ac.abort();},[meta.snapshot,attempt]);
  return <div className="screen workspace-screen"><PageHeader meta={meta} active={section}/><main className="work-main"><div className="work-heading"><span className="work-eyebrow">Terrier workspace</span><h1 className="page-title">{TITLES[section]}</h1><p>{SUBTITLES[section]}</p></div>{error?<div role="alert" className="work-inline-error"><p>{error}</p><button type="button" className="work-button" onClick={()=>setAttempt(n=>n+1)}>Retry workspace</button></div>:!data?<p role="status">Loading the local workspace…</p>:section==='criteria'?<FieldAudit meta={meta} data={data}/>:section==='sources'?<DataSources meta={meta} data={data}/>:<SavedWorkspace meta={meta} route={route} data={data}/>}</main></div>;
}
