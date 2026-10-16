// The application reads the active local snapshot. A failed request never falls back to generated records.
import type {AreaGeometry,AreasPayload,Density,Features,HouseholdPopover,Meta,PeoplePage,PeopleQuery,Profile,PartiesPayload,Source,StreetPayload,SearchResult} from '../types';
import type {Level} from '../lib/format';
import type {UntappedAreas,UntappedLevel} from '../lib/untapped';
export type Family='meta'|'geometry'|'areas'|'people'|'person'|'household'|'street'|'search';
export const sources:Record<Family,Source>={meta:'live',geometry:'live',areas:'live',people:'live',person:'live',household:'live',street:'live',search:'live'};
/** `answered` is true only when the server of the requested URL answered in JSON with an `error` of its own; never for an unreadable body
 *  (a proxy's or Django's HTML page) or a failure on the way, such as fetching the session for a write. */
export class ApiError extends Error{
  readonly status:number;readonly answered:boolean;
  constructor(message:string,status:number,answered=false){super(message);this.status=status;this.answered=answered;}
}
const errorOf=(body:unknown):string|null=>body&&typeof body==='object'&&'error' in body&&typeof body.error==='string'?body.error:null;
export const AUTHENTICATION_REQUIRED='terrier:authentication-required';
function requireAuthentication(status:number){
  if(status===401&&typeof window!=='undefined')window.dispatchEvent(new Event(AUTHENTICATION_REQUIRED));
}

export async function localJson<T>(url:string,signal?:AbortSignal):Promise<T>{
  const response=await fetch(url,{credentials:'same-origin',signal,cache:'no-store',headers:{Accept:'application/json'}});
  return readJson<T>(response);
}
async function readJson<T>(response:Response):Promise<T>{
  let body:unknown;
  try{body=await response.json();}catch{throw new ApiError('The local data service returned an unreadable response.',response.status);}
  if(!response.ok){
    requireAuthentication(response.status);
    const said=errorOf(body);
    throw new ApiError(said??'The local data could not be loaded.',response.status,said!==null);
  }
  return body as T;
}
function endpoint(path:string,values:Record<string,string|number|boolean>):string{
  return path+'?'+new URLSearchParams(Object.entries(values).filter(([,v])=>v!=='').map(([k,v])=>[k,typeof v==='boolean'?(v?'1':'0'):String(v)]));
}
export async function getMeta():Promise<Meta>{
  const meta=await localJson<Meta>('/api/v3/meta');
  if(meta.demo)throw new ApiError('Terrier is connected to a demonstration service. Start the local data service to use the supplied records.',409);
  if(meta.store&&meta.store.status!=='ok')throw new ApiError(meta.store.note||'The local score store is unavailable.',503);
  return {...meta,source:'live'};
}
export function getAreas(meta:Meta,level:Level):Promise<AreasPayload>{return localJson(`/api/v3/areas/${encodeURIComponent(meta.snapshot)}/registration/${level}.json`);}
export function getParties(meta:Meta,level:Level):Promise<PartiesPayload>{return localJson(`/api/v3/parties/${encodeURIComponent(meta.snapshot)}/registration/${level}.json`);}
export function getGeometry(meta:Meta,level:Level|'state_house'|'state_senate',variant:'coarse'|'fine'):Promise<AreaGeometry>{return localJson(`/api/v3/geometry/${encodeURIComponent(meta.snapshot)}/${level}/${variant}.json`);}
export function getDistrict():Promise<GeoJSON.FeatureCollection>{return localJson('/api/geometry?level=district');}
/** This district's settings (industries, candidate and race mode, overlay layers, street plan); configuration only. */
export function getFeatures(signal?:AbortSignal):Promise<Features>{return localJson('/api/v3/features',signal);}
/** Population and voter density per area; rejects with a 404 ApiError until tools/build_density.py has run. */
export function getDensity(signal?:AbortSignal):Promise<Density>{return localJson('/api/v3/density',signal);}
/** Untapped sums of one level. The answer carries an ETag: the last one seen per level goes back as If-None-Match, so a refetch on focus costs
 *  a 304 while no result has been posted, and the same object comes back (a screen holding it does not redraw). */
const untappedSeen=new Map<UntappedLevel,{etag:string;body:UntappedAreas}>();
export async function getUntapped(level:UntappedLevel,signal?:AbortSignal):Promise<UntappedAreas>{
  const seen=untappedSeen.get(level);
  const response=await fetch(endpoint('/api/v3/untapped',{level}),{credentials:'same-origin',signal,
    headers:{Accept:'application/json',...(seen?{'If-None-Match':seen.etag}:{})}});
  if(response.status===304&&seen)return seen.body;
  const body=await readJson<UntappedAreas>(response);
  const etag=response.headers.get('ETag');
  if(etag)untappedSeen.set(level,{etag,body});else untappedSeen.delete(level);
  return body;
}
type Roads=GeoJSON.FeatureCollection<GeoJSON.LineString|GeoJSON.MultiLineString,{kind:string;name?:string}>;
export function getRoads(bbox:[number,number,number,number],zoom:number,signal?:AbortSignal):Promise<Roads>{return localJson(endpoint('/api/roads',{bbox:bbox.join(','),zoom}),signal);}
export function getPeople(q:PeopleQuery,signal?:AbortSignal):Promise<PeoplePage>{return localJson(endpoint('/api/v3/people',{basis:'registration',...q}),signal);}
export function getProfile(id:string,signal?:AbortSignal):Promise<Profile>{return localJson(endpoint('/api/v3/person',{id}),signal);}
export function getHousehold(id:string,signal?:AbortSignal):Promise<HouseholdPopover>{return localJson(endpoint('/api/v3/household',{id,basis:'registration'}),signal);}
export function getStreet(bbox:[number,number,number,number],zoom:number,signal?:AbortSignal):Promise<StreetPayload>{return localJson(endpoint('/api/v3/street',{basis:'registration',bbox:bbox.join(','),zoom:Math.floor(zoom)}),signal);}
export function searchPeople(text:string,signal?:AbortSignal):Promise<SearchResult>{return localJson(endpoint('/api/v3/search',{q:text.trim().slice(0,80)}),signal);}

/** GET /api/auth/session. `hosted` and `email` (the Cloudflare Access identity the server verified) are there only on a hosted server. */
export interface Session{authenticated?:boolean;csrf?:string;hosted?:boolean;email?:string|null;account_identity?:string|null}
export const getSession=(signal?:AbortSignal):Promise<Session>=>localJson<Session>('/api/auth/session',signal);

// Writes carry the CSRF token the session endpoint hands out (same header and field as the legacy client). It is fetched once and kept.
let csrfToken='';
async function csrf(fresh:boolean,signal?:AbortSignal):Promise<string>{
  if(csrfToken&&!fresh)return csrfToken;
  const session=await localJson<{csrf?:string}>('/api/auth/session',signal);
  csrfToken=typeof session.csrf==='string'?session.csrf:'';
  return csrfToken;
}
export async function postJson<T>(url:string,body:unknown,signal?:AbortSignal):Promise<T>{
  // a failure to get the token is not this endpoint's answer: it never counts as one
  const token=(fresh:boolean)=>csrf(fresh,signal).catch((e:unknown)=>{throw e instanceof ApiError?new ApiError(e.message,e.status):e;});
  const send=async(fresh:boolean)=>fetch(url,{method:'POST',credentials:'same-origin',signal,cache:'no-store',headers:{Accept:'application/json','Content-Type':'application/json','X-CSRFToken':await token(fresh)},body:JSON.stringify(body)});
  const cached=!!csrfToken;
  let response=await send(false);
  if(response.status===403&&cached)response=await send(true); // a kept token can go stale; ask once more with a fresh one
  let data:unknown;
  try{data=await response.json();}catch{throw new ApiError('The local data service returned an unreadable response.',response.status);}
  if(!response.ok){
    if(!url.startsWith('/api/auth/'))requireAuthentication(response.status);
    const said=errorOf(data);
    throw new ApiError(said??'The request could not be completed.',response.status,said!==null);
  }
  return data as T;
}
