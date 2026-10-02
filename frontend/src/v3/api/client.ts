// The application reads the active local snapshot. A failed request never falls back to generated records.
import type {AreaGeometry,AreasPayload,HouseholdPopover,Meta,PeoplePage,PeopleQuery,Profile,Source,StreetPayload,SearchResult} from '../types';
import type {Level} from '../lib/format';
export type Family='meta'|'geometry'|'areas'|'people'|'person'|'household'|'street'|'search';
export const sources:Record<Family,Source>={meta:'live',geometry:'live',areas:'live',people:'live',person:'live',household:'live',street:'live',search:'live'};
export class ApiError extends Error{readonly status:number;constructor(message:string,status:number){super(message);this.status=status;}}

export async function localJson<T>(url:string,signal?:AbortSignal):Promise<T>{
  const response=await fetch(url,{credentials:'same-origin',signal,cache:'no-store',headers:{Accept:'application/json'}});
  let body:unknown;
  try{body=await response.json();}catch{throw new ApiError('The local data service returned an unreadable response.',response.status);}
  if(!response.ok){
    const message=body&&typeof body==='object'&&'error' in body&&typeof body.error==='string'?body.error:'The local data could not be loaded.';
    throw new ApiError(message,response.status);
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
export function getGeometry(meta:Meta,level:Level,variant:'coarse'|'fine'):Promise<AreaGeometry>{return localJson(`/api/v3/geometry/${encodeURIComponent(meta.snapshot)}/${level}/${variant}.json`);}
export function getDistrict():Promise<GeoJSON.FeatureCollection>{return localJson('/api/geometry?level=district');}
type Roads=GeoJSON.FeatureCollection<GeoJSON.LineString|GeoJSON.MultiLineString,{kind:string;name?:string}>;
export function getRoads(bbox:[number,number,number,number],zoom:number,signal?:AbortSignal):Promise<Roads>{return localJson(endpoint('/api/roads',{bbox:bbox.join(','),zoom}),signal);}
export function getPeople(q:PeopleQuery,signal?:AbortSignal):Promise<PeoplePage>{return localJson(endpoint('/api/v3/people',{basis:'registration',...q}),signal);}
export function getProfile(id:string,signal?:AbortSignal):Promise<Profile>{return localJson(endpoint('/api/v3/person',{id}),signal);}
export function getHousehold(id:string,signal?:AbortSignal):Promise<HouseholdPopover>{return localJson(endpoint('/api/v3/household',{id,basis:'registration'}),signal);}
export function getStreet(bbox:[number,number,number,number],zoom:number,signal?:AbortSignal):Promise<StreetPayload>{return localJson(endpoint('/api/v3/street',{basis:'registration',bbox:bbox.join(','),zoom:Math.floor(zoom)}),signal);}
export function searchPeople(text:string,signal?:AbortSignal):Promise<SearchResult>{return localJson(endpoint('/api/v3/search',{q:text.trim().slice(0,80)}),signal);}
