import type {AreasPayload} from '../types';
import {core,fixtureFile,fixtureTotals,hash} from './fixtures';

export type AddressBasis='registration'|'residential';
export type ContactChannel='all'|'email'|'phone'|'none';
export interface ContactQuery{text:string;page:number;basis:AddressBasis;channel:ContactChannel}
export interface DummyContact{
  id:string;name:string;household:number;email:string|null;phone:string|null;wireless:string|null;
  registrationAddress:string|null;residentialAddress:string|null;block:string;
}
export interface ContactCoverage{records:number;email:number;phone:number;wireless:number;withoutChannel:number}
export interface ContactPage{rows:DummyContact[];total:number;page:number;pages:number;pageSize:number;first:number;last:number;coverage:ContactCoverage}
const PAGE_SIZE=50;
let indexPromise:Promise<DummyContact[]>|null=null;

/** Contact placeholders belong to the same deterministic dummy people as the profiles.
 * They are never copied from a vendor file and are never used to contact anyone. */
function contactIndex():Promise<DummyContact[]>{
  if(!indexPromise)indexPromise=(async()=>{
    const areas=await fixtureFile<AreasPayload>('areas/block.json');
    const blocks=areas.ids.filter((_id,i)=>areas.status[i]==='ok'&&(areas.n[i]??0)>=40);
    if(!blocks.length)throw new Error('Synthetic map locations are unavailable.');
    const rows:DummyContact[]=[];const households=fixtureTotals().households;
    for(let hh=1;hh<=households;hh++){
      const size=core(hh,0).size;
      const address=`Test household ${hh} · Example Lane`;
      const block=blocks[hash('blk',hh)%blocks.length];
      for(let j=0;j<size;j++){
        const person=core(hh,j);
        const slug=person.name.toLowerCase().replace(/[^a-z]+/g,'.');
        rows.push({id:person.id,name:person.name,household:hh,block,
          email:hash('contact-email',hh,j)%100<68?`${slug}.${person.id}@example.test`:null,
          phone:hash('contact-landline-present',hh)%100<52?fictionalPhone('202',hash('contact-landline',hh)):null,
          wireless:hash('contact-mobile-present',hh,j)%100<73?fictionalPhone('303',hash('contact-mobile',hh,j)):null,
          registrationAddress:hash('contact-reg-address',hh)%100<83?address:null,
          residentialAddress:hash('contact-home-address',hh)%100<92?address:null});
      }
      // Keep the application responsive while creating the full synthetic registry.
      if(hh%1500===0)await new Promise(resolve=>setTimeout(resolve,0));
    }
    rows.sort((a,b)=>a.name<b.name?-1:a.name>b.name?1:a.id<b.id?-1:1);
    return rows;
  })().catch(error=>{indexPromise=null;throw error;});
  return indexPromise;
}

function fictionalPhone(area:string,seed:number):string{return `(${area}) 555-01${String(seed%100).padStart(2,'0')}`;}
export function contactAddress(contact:DummyContact,basis:AddressBasis):string|null{return basis==='registration'?contact.registrationAddress:contact.residentialAddress;}
export function contactQuery(search:URLSearchParams):ContactQuery{
  const value=search.get('page')??'1';const page=/^\d+$/.test(value)?Number(value):1;
  const channel=search.get('channel');
  return {text:(search.get('q')??'').slice(0,160).trim(),page:Number.isSafeInteger(page)?Math.max(1,page):1,
    basis:search.get('basis')==='residential'?'residential':'registration',
    channel:channel==='email'||channel==='phone'||channel==='none'?channel:'all'};
}
export function contactUrl(q:ContactQuery):string{
  const params=new URLSearchParams();if(q.text)params.set('q',q.text);if(q.page>1)params.set('page',String(q.page));
  if(q.basis!=='registration')params.set('basis',q.basis);if(q.channel!=='all')params.set('channel',q.channel);
  return '/contacts'+(params.size?'?'+params.toString():'');
}
function filterContacts(rows:DummyContact[],q:ContactQuery):DummyContact[]{
  const term=q.text.toLowerCase();const phoneTerm=term.replace(/\D/g,'');const phoneSearch=/^[\d\s()+.\-]+$/.test(term)&&phoneTerm.length>0;
  return rows.filter(row=>{
    if(q.channel==='email'&&!row.email)return false;
    if(q.channel==='phone'&&!row.phone&&!row.wireless)return false;
    if(q.channel==='none'&&(row.email||row.phone||row.wireless))return false;
    if(!term)return true;
    if(phoneSearch)return [row.phone,row.wireless].some(phone=>phone?.replace(/\D/g,'').includes(phoneTerm))||contactAddress(row,q.basis)?.toLowerCase().includes(term)||row.id.includes(term);
    return [row.id,row.name,row.email,row.phone,row.wireless,contactAddress(row,q.basis)].some(value=>value?.toLowerCase().includes(term));
  });
}
function coverageOf(rows:DummyContact[]):ContactCoverage{
  const coverage={records:rows.length,email:0,phone:0,wireless:0,withoutChannel:0};
  for(const row of rows){if(row.email)coverage.email++;if(row.phone)coverage.phone++;if(row.wireless)coverage.wireless++;if(!row.email&&!row.phone&&!row.wireless)coverage.withoutChannel++;}
  return coverage;
}
export async function getContacts(q:ContactQuery,signal?:AbortSignal):Promise<ContactPage>{
  signal?.throwIfAborted();const all=await contactIndex();signal?.throwIfAborted();const matches=filterContacts(all,q);
  const pages=Math.max(1,Math.ceil(matches.length/PAGE_SIZE));const page=Math.min(q.page,pages);const offset=(page-1)*PAGE_SIZE;
  return {rows:matches.slice(offset,offset+PAGE_SIZE),total:matches.length,page,pages,pageSize:PAGE_SIZE,
    first:matches.length?offset+1:0,last:Math.min(offset+PAGE_SIZE,matches.length),coverage:coverageOf(all)};
}
const csvCell=(value:string|number|null)=>'"'+String(value??'').replace(/"/g,'""')+'"';
export async function exportContacts(q:ContactQuery):Promise<{csv:string;count:number}>{
  const rows=filterContacts(await contactIndex(),q);
  const header=['Data type','Person ID','Name','Email','Phone','Wireless phone',q.basis==='registration'?'Registration address':'Residential address','Synthetic census block'];
  const lines=[header.map(csvCell).join(','),...rows.map(row=>['Synthetic test dummy',row.id,row.name,row.email,row.phone,row.wireless,contactAddress(row,q.basis),row.block].map(csvCell).join(','))];
  return {csv:'\uFEFF'+lines.join('\r\n')+'\r\n',count:rows.length};
}
