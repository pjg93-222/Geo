import {localJson} from './client';

export type AddressBasis='registration'|'residential';
export type ContactChannel='all'|'email'|'phone'|'none';
export interface ContactQuery{text:string;page:number;basis:AddressBasis;channel:ContactChannel}
export interface Contact{
  id:string;name:string;household:number|string|null;email:string|null;phone:string|null;wireless:string|null;
  registrationAddress:string|null;residentialAddress:string|null;block:string|null;
}
export interface ContactCoverage{records:number;email:number;phone:number;wireless:number;withoutChannel:number}
export interface ContactPage{rows:Contact[];total:number;page:number;pages:number;pageSize:number;first:number;last:number;coverage:ContactCoverage}
export function contactAddress(contact:Contact,basis:AddressBasis):string|null{return basis==='registration'?contact.registrationAddress:contact.residentialAddress;}
export function contactQuery(search:URLSearchParams):ContactQuery{
  const value=search.get('page')??'1';const page=/^\d+$/.test(value)?Number(value):1;const channel=search.get('channel');
  return {text:(search.get('q')??'').slice(0,160).trim(),page:Number.isSafeInteger(page)?Math.max(1,page):1,
    basis:search.get('basis')==='residential'?'residential':'registration',channel:channel==='email'||channel==='phone'||channel==='none'?channel:'all'};
}
export function contactUrl(q:ContactQuery):string{
  const params=new URLSearchParams();if(q.text)params.set('q',q.text);if(q.page>1)params.set('page',String(q.page));
  if(q.basis!=='registration')params.set('basis',q.basis);if(q.channel!=='all')params.set('channel',q.channel);
  return '/contacts'+(params.size?'?'+params.toString():'');
}
function queryString(q:ContactQuery):string{return new URLSearchParams({q:q.text,page:String(q.page),basis:q.basis,channel:q.channel}).toString();}
export function getContacts(q:ContactQuery,signal?:AbortSignal):Promise<ContactPage>{return localJson('/api/v3/contacts?'+queryString(q),signal);}
export function exportContacts(q:ContactQuery):Promise<{csv:string;count:number}>{return localJson('/api/v3/contacts/export?'+queryString(q));}
