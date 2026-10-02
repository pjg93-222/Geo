import {useEffect,useMemo,useRef,useState} from 'react';
import {ArrowUpRight,ChevronLeft,ChevronRight,Download,MapPin,Search,X} from 'lucide-react';
import {PageHeader,Link} from './Header';
import {navigate,type Route} from '../lib/router';
import {int} from '../lib/format';
import type {Meta} from '../types';
import {contactAddress,contactQuery,contactUrl,exportContacts,getContacts,type AddressBasis,type ContactChannel,type ContactPage,type ContactQuery,type Contact} from '../api/contacts';
import '../styles/contacts.css';

export function ContactsScreen({meta,route}:{meta:Meta|null;route:Route}){
  const query=useMemo(()=>contactQuery(route.search),[route]);
  const [draft,setDraft]=useState(query.text);const [data,setData]=useState<ContactPage|null>(null);
  const [error,setError]=useState('');const [retry,setRetry]=useState(0);const [exporting,setExporting]=useState(false);const [exportNote,setExportNote]=useState('');
  const [selected,setSelected]=useState<Contact|null>(null);const dialog=useRef<HTMLDialogElement>(null);const table=useRef<HTMLDivElement>(null);
  useEffect(()=>{setDraft(query.text);setSelected(null);setExportNote('');table.current?.scrollTo({top:0});},[query]);
  useEffect(()=>{
    const controller=new AbortController();setData(null);setError('');
    getContacts(query,controller.signal).then(page=>{
      setData(page);
      if(page.page!==query.page)navigate(contactUrl({...query,page:page.page}),{replace:true});
    }).catch(error=>{if(error.name!=='AbortError')setError(error.message||'The contact sheet could not be loaded.');});
    return ()=>controller.abort();
  },[query,retry]);
  useEffect(()=>{if(selected&&!dialog.current?.open)dialog.current?.showModal();else if(!selected&&dialog.current?.open)dialog.current.close();},[selected]);
  function change(patch:Partial<ContactQuery>){navigate(contactUrl({...query,...patch,page:patch.page??1}));}
  async function download(){
    setExporting(true);setExportNote('');
    try{
      await new Promise(resolve=>setTimeout(resolve,0));
      const {csv,count}=await exportContacts(query);const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));
      const link=document.createElement('a');link.href=url;link.download='Terrier-contacts.csv';document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
      setExportNote(`${int(count)} records exported.`);
    }catch{setExportNote('Export could not be created. Please try again.');}finally{setExporting(false);}
  }
  return <div className="screen contacts-screen">
    <PageHeader meta={meta} active="contacts"/>
    <main className="contacts-main">
      <div className="contacts-heading"><div><h1 className="page-title">Contact sheet</h1><p className="page-sub">Find a person and review their contact details.</p></div>
        <button type="button" className="contact-button contact-export" aria-label={exporting?'Preparing CSV…':'Export matching CSV'} onClick={download} disabled={!data||!data.total||exporting}><Download size={17} aria-hidden="true"/>{exporting?'Preparing…':'Export CSV'}</button></div>
      <p className="contacts-notice"><span>Local contact registry</span> Contact details supplied in the source dataset.</p>
      <div className="contact-summary" aria-label="Contact coverage">
        <Summary label="people" value={data?.coverage.records??meta?.people}/><Summary label="with email" value={data?.coverage.email}/><Summary label="with phone" value={data?.coverage.phone}/><Summary label="with wireless" value={data?.coverage.wireless}/>
      </div>
      <div className="contacts-tools">
        <form className="contact-search" onSubmit={event=>{event.preventDefault();change({text:draft.trim()});}}>
          <label htmlFor="contact-search">Search the contact sheet</label><div className="contact-search-controls"><Search size={18} aria-hidden="true"/><input id="contact-search" type="search" maxLength={160} placeholder="Name, email, phone, address or person ID" value={draft} onChange={event=>setDraft(event.target.value)}/><button className="contact-button contact-primary" type="submit">Search</button></div>
        </form>
        <label className="contact-filter">Address shown<select value={query.basis} onChange={event=>change({basis:event.target.value as AddressBasis})}><option value="registration">Registration address</option><option value="residential">Residential address</option></select></label>
        <label className="contact-filter">Contact availability<select value={query.channel} onChange={event=>change({channel:event.target.value as ContactChannel})}><option value="all">All records</option><option value="email">Has email</option><option value="phone">Has a phone number</option><option value="none">No contact channels</option></select></label>
        {(query.text||query.channel!=='all')&&<button className="contact-clear" type="button" onClick={()=>change({text:'',channel:'all'})}>Clear filters</button>}
      </div>
      <div className="contact-result-line"><p role="status">{error?'Contact sheet unavailable':!data?'Loading the contact sheet…':`${int(data.total)} ${data.total===1?'record':'records'}${query.text?` matching “${query.text}”`:''}`}</p>{exportNote&&<p role="status">{exportNote}</p>}<span className="contact-scroll-hint">Scroll across for contact details</span></div>
      {error?<div className="contact-empty" role="alert"><p>{error}</p><button className="contact-button" type="button" onClick={()=>setRetry(value=>value+1)}>Retry contact sheet</button></div>:<>
        <div ref={table} className="contacts-table-wrap" role="region" tabIndex={0} aria-label="Contact sheet table; scroll for all rows and columns" aria-busy={!data}>
          <table className="contacts-table"><thead><tr><th scope="col">Person / {query.basis==='registration'?'registration':'residential'} address</th><th scope="col">Email</th><th scope="col">Phone</th><th scope="col">Wireless</th><th scope="col">Open</th></tr></thead>
            <tbody>{data?.rows.map(row=><tr key={row.id}><td><button type="button" className="contact-person" aria-haspopup="dialog" onClick={()=>setSelected(row)}>{row.name}</button><span className="contact-address">{contactAddress(row,query.basis)??'Address not supplied'}</span></td><td><ContactValue value={row.email}/></td><td><ContactValue value={row.phone}/></td><td><ContactValue value={row.wireless}/></td><td><Link className="contact-profile" to={'/people/'+encodeURIComponent(row.id)} title={`Open ${row.name}’s profile`}>Profile <ArrowUpRight size={14} aria-hidden="true"/></Link></td></tr>)}</tbody>
          </table>
          {!data&&<div className="contact-empty" role="status">Loading local records…</div>}
          {data&&!data.rows.length&&<div className="contact-empty"><h2>No matching contacts</h2><p>Try a shorter name, another address basis, or fewer filters.</p><button className="contact-button" type="button" onClick={()=>change({text:'',channel:'all'})}>Clear filters</button></div>}
        </div>
        {data&&<div className="contact-pagination"><span>{int(data.first)}–{int(data.last)} of {int(data.total)} <span className="contact-page-count">· Page {int(data.page)} of {int(data.pages)}</span></span><div><button className="contact-button" type="button" disabled={data.page<=1} onClick={()=>change({page:data.page-1})}><ChevronLeft size={16} aria-hidden="true"/>Previous</button><button className="contact-button" type="button" disabled={data.page>=data.pages} onClick={()=>change({page:data.page+1})}>Next<ChevronRight size={16} aria-hidden="true"/></button></div></div>}
      </>}
      <p className="contact-footnote">“Not supplied” means the local record has no value for that field.</p>
    </main>
    <dialog ref={dialog} className="contact-detail-dialog" aria-labelledby="contact-detail-title" onClose={()=>setSelected(null)}>
      {selected&&<><div className="contact-dialog-heading"><div><p className="contacts-eyebrow">Contact details</p><h2 id="contact-detail-title">{selected.name}</h2></div><form method="dialog"><button className="contact-close" autoFocus aria-label="Close contact details"><X size={21}/></button></form></div>
        <p className="contact-detail-id">{selected.id}{selected.household!=null?` · Household ${selected.household}`:''}</p><dl className="contact-detail-fields"><dt>Email</dt><dd><ContactValue value={selected.email}/></dd><dt>Phone</dt><dd><ContactValue value={selected.phone}/></dd><dt>Wireless phone</dt><dd><ContactValue value={selected.wireless}/></dd><dt>Registration address</dt><dd><ContactValue value={selected.registrationAddress}/></dd><dt>Residential address</dt><dd><ContactValue value={selected.residentialAddress}/></dd></dl>
        <div className="contact-detail-actions"><Link className="contact-button contact-primary" to={'/people/'+encodeURIComponent(selected.id)}>Open profile<ArrowUpRight size={16}/></Link>{selected.block&&<Link className="contact-button" to={`/?level=block&sel=${encodeURIComponent(selected.block)}&f=${Date.now()}`}><MapPin size={16}/>View census block</Link>}</div></>}
    </dialog>
  </div>;
}
function ContactValue({value}:{value:string|null}){return value?<span>{value}</span>:<span className="contact-missing">Not supplied</span>;}
function Summary({label,value}:{label:string;value:number|undefined}){return <div><strong>{value===undefined?'—':int(value)}</strong><span>{label}</span></div>;}
