import {useEffect,useMemo,useState} from 'react';
import {ArrowUpRight,Bookmark,Download,FileText,Layers,Search,Trash2,Users} from 'lucide-react';
import {FIELDS,GROUP_LABEL,GROUPS,type FieldDef} from '../api/fields';
import {getAreas} from '../api/client';
import {int,LEVEL_LABEL,type Level} from '../lib/format';
import {type Route} from '../lib/router';
import {lastView,readSavedViews,removeView,safeViewPath,saveView,type SavedView} from '../lib/savedViews';
import {Link,PageHeader} from './Header';
import type {Meta} from '../types';
import './WorkspaceScreen.css';

type Section='criteria'|'sources'|'workspace';
const TITLES:Record<Section,string>={criteria:'Criteria audit',sources:'Data & sources',workspace:'Team & snapshots'};
const SUBTITLES:Record<Section,string>={criteria:'Follow every field from its definition to its role in the preview.',sources:'See what is generated, where the map comes from, and what each view can tell you.',workspace:'Keep useful views and inspect the snapshot behind this local workspace.'};
const FORMATS:Record<FieldDef['kind'],string>={yn:'Yes / No',score:'0–100 input',int:'Count',owner:'Owner / renter category',homeowner:'Homeowner category',dwelling:'Dwelling category',children:'Count',party:'Registration category','lean-yn':'Yes / No response'};
const WEIGHT=(n:number)=>`${Number((n*100).toFixed(2))}%`;

function downloadFile(name:string,type:string,content:string){
  const url=URL.createObjectURL(new Blob([content],{type}));const link=document.createElement('a');
  link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}

function FieldAudit({meta}:{meta:Meta}){
  const [group,setGroup]=useState('all');const [query,setQuery]=useState('');
  const fields=useMemo(()=>FIELDS.filter(f=>(group==='all'||f.group===group)&&`${f.label} ${f.key}`.toLowerCase().includes(query.trim().toLowerCase())),[group,query]);
  function exportFields(){
    const rows=[['Field','Group','Input','Weight within index','Included in overall standing'],...fields.map(f=>[f.key,GROUP_LABEL[f.group],FORMATS[f.kind],f.group==='lean'?'Not scored':WEIGHT(f.w),f.group==='lean'?'No':'Yes'])];
    downloadFile('terrier-criteria.csv','text/csv;charset=utf-8',rows.map(row=>row.map(value=>`"${value.replaceAll('"','""')}"`).join(',')).join('\r\n'));
  }
  return <>
    <div className="work-stat-grid" aria-label="Framework groups">{GROUPS.map(id=><div className="work-stat" key={id}><span>{GROUP_LABEL[id]}</span><strong>{FIELDS.filter(f=>f.group===id).length}<small> fields</small></strong><p>{id==='lean'?'Categorical responses · not scored':`${meta.weights[id]}% of overall standing`}</p></div>)}</div>
    <section className="work-callout"><FileText aria-hidden="true"/><div><h2>Opinions stay in their own terms</h2><p>Party registration, self-described affiliation and outlook are separate categories. Political views have no standardized score, rank, or weight in overall standing. Ethical beliefs are not collected in this preview.</p></div></section>
    <section className="work-panel" aria-labelledby="dictionary-heading">
      <div className="work-panel-heading"><div><h2 id="dictionary-heading">Field dictionary</h2><p>Current preview framework · {fields.length} of {FIELDS.length} fields</p></div><button type="button" className="work-button" onClick={exportFields}><Download size={16} aria-hidden="true"/>Export shown fields</button></div>
      <div className="work-filter-bar"><label className="work-search"><Search size={17} aria-hidden="true"/><input type="search" aria-label="Search criteria" placeholder="Find a field or source key" value={query} onChange={e=>setQuery(e.target.value)}/></label><label className="work-select-label">Group<select value={group} onChange={e=>setGroup(e.target.value)} aria-label="Criteria group"><option value="all">All groups</option>{GROUPS.map(id=><option value={id} key={id}>{GROUP_LABEL[id]}</option>)}</select></label></div>
      <div className="work-table-scroll" tabIndex={0} aria-label="Field dictionary, horizontally scrollable"><table className="work-table"><thead><tr><th scope="col">Field</th><th scope="col">Group</th><th scope="col">Input</th><th scope="col" className="work-numeric">Weight within index</th></tr></thead><tbody>{fields.map(f=><tr key={f.key}><th scope="row"><strong>{f.label}</strong><code>{f.key}</code></th><td>{GROUP_LABEL[f.group]}</td><td>{FORMATS[f.kind]}</td><td className="work-numeric">{f.group==='lean'?<span className="work-tag">Not scored</span>:WEIGHT(f.w)}</td></tr>)}</tbody></table>{!fields.length&&<p className="work-empty">No fields match. Try another name or choose all groups.</p>}</div>
      <div className="work-panel-foot">Weights shown apply within each scored index. Political fields retain their response labels.</div>
    </section>
    <div className="work-two-column"><section className="work-panel work-prose"><h2>How the three indices combine</h2><p>Overall standing = {meta.weights.cua}% Credit Union Alignment + {meta.weights.part}% Participation + {meta.weights.soi}% Sphere of Influence. Sphere of Influence assigns 45% to giving, 45% to volunteering and 10% to household fields.</p><p>The scored indices use synthetic values and the preview’s existing score and percentile conventions. These are demonstrations of the framework, not validated predictions about people.</p><Link to="/method" className="work-text-link">Read the full method <ArrowUpRight size={14} aria-hidden="true"/></Link></section><section className="work-panel work-prose"><h2>Evidence and missing values</h2><p>Each profile distinguishes observed and estimated fields. Estimated values show their probability. “Observed” here means supplied by the dummy generator.</p><p>Missing or conflicting opinion responses remain Unknown or Multiple responses. They are never replaced with a middle outlook. Area summaries are generated independently of the profile population.</p></section></div>
  </>;
}

interface AreaCount{level:Level;total:number;available:number}
function DataSources({meta}:{meta:Meta}){
  const [counts,setCounts]=useState<AreaCount[]>([]);const [error,setError]=useState('');const [attempt,setAttempt]=useState(0);
  useEffect(()=>{let live=true;setError('');setCounts([]);Promise.all(meta.levels.map(async level=>{const a=await getAreas(meta,level);return {level,total:a.ids.length,available:a.status.filter(s=>s==='ok').length};})).then(rows=>{if(live)setCounts(rows);}).catch(()=>{if(live)setError('Geographic counts could not be loaded.');});return()=>{live=false;};},[meta,attempt]);
  return <>
    <div className="work-stat-grid"><div className="work-stat"><span>Generated people</span><strong>{int(meta.people)}</strong><p>Fictional names and responses</p></div><div className="work-stat"><span>Generated households</span><strong>{int(meta.households)}</strong><p>Synthetic household membership</p></div><div className="work-stat"><span>Framework fields</span><strong>{FIELDS.length}</strong><p>29 scored · 11 political categories</p></div><div className="work-stat"><span>Geographic layers</span><strong>{meta.levels.length}</strong><p>Blocks, precincts, tracts and ZIPs</p></div></div>
    <section className="work-callout"><Layers aria-hidden="true"/><div><h2>A test population on geographic context</h2><p>Names, contact details, responses, scores, household memberships and household locations are generated. The maps provide geographic context. No source voter records or live contact database is served by this preview.</p></div></section>
    <section className="work-panel" aria-labelledby="source-ledger-heading"><div className="work-panel-heading"><div><h2 id="source-ledger-heading">Source ledger</h2><p>Active snapshot: <code>{meta.snapshot}</code> · {meta.district_label}</p></div><span className="work-tag">Synthetic preview</span></div><div className="work-table-scroll" tabIndex={0} aria-label="Source ledger, horizontally scrollable"><table className="work-table"><thead><tr><th scope="col">Dataset</th><th scope="col">Origin</th><th scope="col">Interpretation</th></tr></thead><tbody>
      <tr><th scope="row">People & households</th><td>Deterministic dummy generator</td><td>Fictional test records. The same identifier returns the same profile.</td></tr>
      <tr><th scope="row">Area values</th><td>Generated spatial fixtures</td><td>Independent illustrative statistics; they do not aggregate the displayed people.</td></tr>
      <tr><th scope="row">Census blocks & tracts</th><td>2020 block polygons, TIGER/Line 2025 release</td><td>Tracts are derived by dissolving the supplied block polygons. Geometry is simplified for display.</td></tr>
      <tr><th scope="row">Precincts</th><td>Synthetic partitions of block centroids</td><td>Demonstration areas, not official election precinct boundaries.</td></tr>
      <tr><th scope="row">ZIP areas</th><td>2020 Census ZIP Code Tabulation Areas</td><td>ZCTAs approximate ZIP geography. Dummy profile ZIP assignments are illustrative.</td></tr>
      <tr><th scope="row">District & roads</th><td>119th Congress district and TIGER/Line 2025 roads</td><td>Public map context. Road lines are not evidence of a person’s address.</td></tr>
    </tbody></table></div></section>
    <div className="work-two-column"><section className="work-panel work-prose"><h2>Geographic coverage</h2><p>Counts are read from the active fixture files. Small dummy areas remain visible.</p>{error?<div className="work-inline-error" role="alert"><p>{error}</p><button className="work-button" onClick={()=>setAttempt(n=>n+1)}>Retry counts</button></div>:!counts.length?<p role="status">Loading geographic counts…</p>:<dl className="work-facts">{counts.map(row=><div key={row.level}><dt>{LEVEL_LABEL[row.level]}</dt><dd>{int(row.available)} available <small>of {int(row.total)}</small></dd></div>)}</dl>}</section><section className="work-panel work-prose"><h2>Limits to keep in view</h2><p>This preview is not an electorate census, a voter file, or evidence of credit union support. Registration, affiliation and outlook describe different categorical responses; none establishes a person’s intentions.</p><p>The sample does not establish real population prevalence. Quality grades, measured percentages and likely ranges are illustrative interface values, not verified confidence estimates.</p><Link to="/criteria" className="work-text-link">Inspect fields and weights <ArrowUpRight size={14} aria-hidden="true"/></Link></section></div>
  </>;
}

function SavedWorkspace({meta,route}:{meta:Meta;route:Route}){
  const [views,setViews]=useState<SavedView[]>([]);const [name,setName]=useState('');
  const [path,setPath]=useState(()=>safeViewPath(route.search.get('from'))??lastView());
  const [message,setMessage]=useState('');const [error,setError]=useState('');
  useEffect(()=>{try{setViews(readSavedViews());}catch{setError('Saved views are unavailable in this browser. Check local storage access before saving.');}},[]);
  useEffect(()=>{setPath(safeViewPath(route.search.get('from'))??lastView());},[route]);
  function remove(id:string){try{setViews(removeView(id));setMessage('Saved view removed.');setError('');}catch{setError('The saved view could not be removed. Your stored views have not been changed.');}}
  return <>
    <div className="work-two-column"><section className="work-panel work-prose"><span className="work-eyebrow">Active snapshot</span><h2 className="work-snapshot-name">{meta.snapshot}</h2><p>{meta.district_label} · {int(meta.people)} generated people in {int(meta.households)} generated households.</p><dl className="work-facts"><div><dt>Data source</dt><dd>Synthetic fixtures</dd></div><div><dt>Address basis</dt><dd>Registration geography</dd></div><div><dt>Snapshot switching</dt><dd>One bundled preview</dd></div></dl><button type="button" className="work-button" onClick={()=>downloadFile('terrier-snapshot.json','application/json',JSON.stringify({snapshot:meta.snapshot,mode:'synthetic-preview',people:meta.people,households:meta.households,levels:meta.levels,weights:meta.weights,political_views:'Categorical responses; not scored',area_values:'Generated independently of profiles'},null,2))}><Download size={16} aria-hidden="true"/>Download snapshot summary</button></section><section className="work-panel work-prose"><Users className="work-section-icon" aria-hidden="true"/><h2>Your local workspace</h2><p>This Terrier preview runs on this computer. Saved views belong to this browser profile and persist after the app closes.</p><p>Team invitations, account management and snapshot imports require the full backend. This preview has no connected team accounts or import service.</p><span className="work-tag">Local access</span></section></div>
    <section className="work-panel" aria-labelledby="saved-views-heading"><div className="work-panel-heading"><div><h2 id="saved-views-heading">Saved views</h2><p>Return to a map selection, people filters, or another Terrier page.</p></div><span className="work-tag">{views.length} saved</span></div>
      <form className="work-save-form" onSubmit={e=>{e.preventDefault();try{setViews(saveView(name,path));setName('');setError('');setMessage('View saved to this browser.');}catch(e){setMessage('');setError(e instanceof Error?e.message:'The view could not be saved.');}}}>
        <label>View name<input value={name} onChange={e=>setName(e.target.value)} required maxLength={100} placeholder="For example, ZIP registration overview"/></label>
        <label>Terrier page and filters<input value={path} onChange={e=>setPath(e.target.value)} required maxLength={2048} spellCheck={false} placeholder="/?index=lean&level=zip" aria-describedby="save-view-help"/></label>
        <button type="submit" className="work-button work-primary"><Bookmark size={16} aria-hidden="true"/>Save view</button>
        <p id="save-view-help">The most recently visited page is filled in. Saved map views retain the layer, index and selected area; viewport pan and zoom are not stored.</p>
      </form>
      {message&&<p className="work-feedback" role="status">{message}</p>}{error&&<p className="work-feedback work-error" role="alert">{error}</p>}
      {views.length?<ul className="work-saved-list">{views.map(view=><li key={view.id}><Bookmark size={18} aria-hidden="true"/><div><Link className="work-saved-title" to={view.path}>{view.name}</Link><p>{view.path}</p><small>Saved {new Date(view.savedAt).toLocaleDateString()}</small></div><button type="button" className="work-icon-button" onClick={()=>remove(view.id)} aria-label={`Remove saved view ${view.name}`}><Trash2 size={17} aria-hidden="true"/></button></li>)}</ul>:<div className="work-empty"><Bookmark size={24} aria-hidden="true"/><h3>No saved views yet</h3><p>Name the view above to keep a shortcut here.</p></div>}
    </section>
  </>;
}

export function WorkspaceScreen({meta,route}:{meta:Meta;route:Route}){
  const section=route.name as Section;
  return <div className="screen workspace-screen"><PageHeader meta={meta} active={section}/><main className="work-main"><div className="work-heading"><span className="work-eyebrow">Terrier workspace</span><h1 className="page-title">{TITLES[section]}</h1><p>{SUBTITLES[section]}</p></div>{section==='criteria'?<FieldAudit meta={meta}/>:section==='sources'?<DataSources meta={meta}/>:<SavedWorkspace meta={meta} route={route}/>}</main></div>;
}
