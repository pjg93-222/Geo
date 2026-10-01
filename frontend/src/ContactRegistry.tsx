import {useEffect,useRef,useState} from 'react';
import './map-detail.css';

export type PersonRecord={id:string;name:string;email:string;email_match:string;phone:string;wireless:string;wireless_match:string;registration:string;residential:string;
  location:{id:string;address:string;longitude:number;latitude:number}|null};
type Contact={id:string;name:string;email:string;phone:string;wireless:string;address:string};
export type ContactSearch={draft:string;query:string;page:number};
type Result={people:Contact[];total:number;page:number;page_size:number;source_totals:{records:number;with_email:number;with_phone:number;with_wireless:number}};
async function get(path:string,signal:AbortSignal){const response=await fetch('/api/'+path,{credentials:'same-origin',signal});const data=await response.json();if(!response.ok)throw new Error(data.error||'The local contact registry could not be loaded.');return data;}
const fmt=(value:number)=>new Intl.NumberFormat('en-US').format(value);
function Phone({value}:{value:string}){return value?<a href={'tel:'+value.replace(/[^+\d]/g,'')}>{value}</a>:<>Not supplied</>;}

export function PersonContact({id,basis,onMap}:{id:string;basis:string;onMap?:(person:PersonRecord)=>void}){
  const [data,setData]=useState<PersonRecord|null>(null);const[error,setError]=useState('');const[retry,setRetry]=useState(0);
  useEffect(()=>{const controller=new AbortController();setData(null);setError('');get('person?'+new URLSearchParams({id,basis}),controller.signal).then(setData).catch(e=>{if(e.name!=='AbortError')setError(e.message);});return()=>controller.abort();},[id,basis,retry]);
  if(error)return <div role="alert"><p>{error}</p><button onClick={()=>setRetry(value=>value+1)}>Retry contact details</button></div>;
  if(!data)return <p role="status">Loading supplied contact details…</p>;
  return <div className="person-contact"><h3>{data.name||'Unnamed source record'}</h3><dl className="contact-fields">
    <dt>Email</dt><dd>{data.email?<a href={'mailto:'+encodeURIComponent(data.email)}>{data.email}</a>:'Not supplied'}</dd>
    <dt>Phone</dt><dd><Phone value={data.phone}/></dd><dt>Wireless phone</dt><dd><Phone value={data.wireless}/></dd>
    <dt>{basis==='registration'?'Registration':'Residential'} address</dt><dd>{data[basis as 'registration'|'residential']||'Not supplied'}</dd>
  </dl><p className="fine">Supplied by the vendor. Contact ownership and deliverability have not been verified.</p>
    {onMap&&(data.location?<button className="secondary" onClick={()=>onMap(data)}>Show household on map</button>:<p className="fine">No validated map location for this address basis.</p>)}
  </div>;
}

export function ContactRegistry({basis,onBasis,onMap,search,onSearch}:{basis:string;onBasis:(basis:string)=>void;onMap:(person:PersonRecord)=>void;search:ContactSearch;onSearch:(search:ContactSearch)=>void}){
  const {draft,query,page}=search;
  const dialog=useRef<HTMLDialogElement>(null);
  const[data,setData]=useState<Result|null>(null);const[error,setError]=useState('');const[selected,setSelected]=useState<string|null>(null);const[retry,setRetry]=useState(0);
  useEffect(()=>{if(selected&&!dialog.current?.open)dialog.current?.showModal();else if(!selected&&dialog.current?.open)dialog.current.close();},[selected]);
  useEffect(()=>{const controller=new AbortController();setData(null);setError('');get('contacts?'+new URLSearchParams({q:query,page:String(page),basis}),controller.signal).then(setData).catch(e=>{if(e.name!=='AbortError')setError(e.message);});return()=>controller.abort();},[query,page,basis,retry]);
  return <main className="reading-page"><h1>Contact registry</h1><p className="page-description">Find a supplied person, inspect their contact details, and open their household on the map.</p>
    <form className="registry-search" onSubmit={event=>{event.preventDefault();setSelected(null);onSearch({...search,page:0,query:draft.trim()});}}><label>Search name, email, phone or address<input value={draft} onChange={event=>onSearch({...search,draft:event.target.value})} maxLength={160} placeholder="Enter a name, email, phone or street" type="search"/></label><button className="primary">Search</button><button type="button" onClick={()=>{onSearch({draft:'',query:'',page:0});setSelected(null);}}>Clear</button></form>
    <label>Address basis<select value={basis} onChange={event=>{onBasis(event.target.value);onSearch({...search,page:0});}}><option value="registration">Registration address</option><option value="residential">Residential address</option></select></label>
    {error?<div role="alert"><p>{error}</p><button onClick={()=>setRetry(value=>value+1)}>Retry registry</button></div>:!data?<p role="status">Loading the local registry…</p>:<>
      <p>{fmt(data.total)} matching records · {fmt(data.source_totals.with_email)} source records have email; {fmt(data.source_totals.with_phone)} have phone; {fmt(data.source_totals.with_wireless)} have wireless phone.</p>
      <div className="registry-table" role="region" tabIndex={0} aria-label="Local contact registry"><table><thead><tr><th scope="col">Person / address</th><th scope="col">Email</th><th scope="col">Phone</th><th scope="col">Wireless</th></tr></thead><tbody>{data.people.map(person=><tr key={person.id}><td><button aria-haspopup="dialog" onClick={()=>setSelected(person.id)}>{person.name||'Unnamed source record'}</button><small>{person.address||'Address not supplied'}</small></td><td>{person.email||'Not supplied'}</td><td>{person.phone||'Not supplied'}</td><td>{person.wireless||'Not supplied'}</td></tr>)}</tbody></table>{!data.people.length&&<p className="empty-inline">No matching source records. Try a shorter name or a phone number without an extension.</p>}</div>
      <div className="pagination"><span>Page {page+1} of {Math.max(1,Math.ceil(data.total/data.page_size))}</span><button disabled={page===0} onClick={()=>onSearch({...search,page:page-1})}>Previous</button><button disabled={(page+1)*data.page_size>=data.total} onClick={()=>onSearch({...search,page:page+1})}>Next</button></div>
    </>}
    <dialog ref={dialog} className="contact-dialog" aria-label="Selected person's contact details" onClose={()=>setSelected(null)}>
      <form method="dialog" className="contact-dialog-close"><button autoFocus>Close contact details</button></form>
      {selected&&<PersonContact id={selected} basis={basis} onMap={onMap}/>}
    </dialog>
    <p className="fine">The registry stays on this computer. Empty source fields remain “Not supplied.” Searching either address does not change the selected map address basis.</p>
  </main>;
}
