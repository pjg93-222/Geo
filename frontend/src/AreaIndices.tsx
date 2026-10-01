import {useEffect,useState} from 'react';
import './area-indices.css';

type Part={measure:string;label:string;group:string;weight:number;direction:number;status:string;value:number|null;field_coverage:number|null;normalized:number|null;reference_areas:number};
type Detail={value:number|null;component_coverage:number|null;components_used:number|null;components_total:number;components:Part[];groups:{name:string;weight:number;used:number;total:number;value:number|null}[];notes:string};
const percent=(value:number)=>(value*100).toFixed(0)+'%';

export function IndexEvidence({snapshot,basis,level,metric,id}:{snapshot:string;basis:string;level:string;metric:string;id:string}){
  const [data,setData]=useState<Detail|null>(null);const[error,setError]=useState('');const[retry,setRetry]=useState(0);
  useEffect(()=>{const controller=new AbortController();setData(null);setError('');
    fetch('/api/index-detail?'+new URLSearchParams({snapshot,basis,level,metric,id}),{credentials:'same-origin',signal:controller.signal})
      .then(async response=>{const value=await response.json();if(!response.ok)throw new Error(value.error||'Index detail could not load.');return value;})
      .then(value=>{if(!controller.signal.aborted)setData(value);}).catch(error=>{if(error.name!=='AbortError')setError(error.message);});return()=>controller.abort();
  },[snapshot,basis,level,metric,id,retry]);
  if(error)return <p role="alert">{error} <button onClick={()=>setRetry(value=>value+1)}>Retry index detail</button></p>;
  if(!data)return <p role="status">Loading index components…</p>;
  return <section className="index-evidence" aria-label="Index components"><h3>What contributes to this index</h3>
    <p className="fine">{data.components_used??0} of {data.components_total} fields usable · {data.component_coverage==null?'Withheld':percent(data.component_coverage)} of planned component weight.</p>
    {data.value==null&&<p className="inline-note">There is not enough publishable evidence to calculate this combined index here. Choose a larger geographic layer.</p>}
    <dl className="index-groups">{data.groups.map(group=><div key={group.name}><dt>{group.name}<small>{percent(group.weight)} of final score · {group.used}/{group.total} fields</small></dt><dd>{group.value==null?'Unavailable':group.value.toFixed(1)}</dd></div>)}</dl>
    <details><summary>Inspect source components</summary>{data.components.map(part=><div className="index-part" key={part.measure}><strong>{part.label}</strong><span>{part.normalized==null?part.status.replaceAll('_',' '):part.normalized.toFixed(1)+' / 100'}{part.direction<0?' · reversed':''}</span><small>{part.field_coverage==null?'No published denominator':percent(part.field_coverage)+' source-field coverage'} · {part.reference_areas} reference areas</small><code>{part.measure.split('__')[0]}</code></div>)}</details>
    <p className="fine">Components are compared with publishable ZCTAs in this file and address basis. At least 75% of component weight and evidence in every group are required.</p><p className="fine">{data.notes}</p>
  </section>;
}

export function IndexMethods({manifest}:{manifest:any}){
  if(!manifest.composites?.length)return null;
  return <section className="audit-section"><h2>Combined area indices</h2><p>{manifest.index_method.formula}</p><p>{manifest.index_method.missing}</p><p>{manifest.index_method.percentile}</p>
    {manifest.composites.map((item:any)=><details className="index-method" key={item.id}><summary>{item.label}</summary><p>{item.description}</p>
      <dl>{item.domains.map((group:any)=><div key={group.name}><dt>{group.name} · {percent(group.weight)}</dt><dd>{group.components.map((part:any)=><code key={part.measure}>{part.measure.split('__')[0]}{part.direction<0?' (reversed)':''}</code>)}</dd></div>)}</dl><p>{item.notes}</p></details>)}
  </section>;
}

export function HierarchySources({manifest}:{manifest:any}){
  if(!manifest.hierarchy)return null;
  return <section className="audit-section"><h2>Geographic layers and regional coverage</h2><p>{manifest.hierarchy.region_definition}</p><p>{manifest.hierarchy.assignment}</p>
    <div className="hierarchy-table" role="region" tabIndex={0} aria-label="Geographic assignment reconciliation"><table><thead><tr><th scope="col">Layer</th><th scope="col">Registration assigned / unassigned</th><th scope="col">Residential assigned / unassigned</th></tr></thead><tbody>{manifest.levels.filter((level:any)=>manifest.hierarchy.reconciliation.registration[level.id]).map((level:any)=><tr key={level.id}><th scope="row">{level.label}<small>{level.vintage}</small></th>{['registration','residential'].map(basis=><td key={basis}>{manifest.hierarchy.reconciliation[basis][level.id].assigned.toLocaleString()} / {manifest.hierarchy.reconciliation[basis][level.id].unassigned.toLocaleString()}</td>)}</tr>)}</tbody></table></div>
    <ul className="source-links">{Object.entries(manifest.hierarchy.sources).map(([level,url])=><li key={level}><a href={String(url)} target="_blank" rel="noreferrer">{level.replaceAll('_',' ')} · U.S. Census TIGER/Line 2025</a></li>)}</ul>
  </section>;
}
