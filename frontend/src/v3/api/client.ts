// Standalone test-dummy adapter. No live-server fallback or URL opt-out.
import type {AreaGeometry,AreasPayload,HouseholdPopover,Meta,PeoplePage,PeopleQuery,Profile,Source,StreetPayload,SearchResult} from '../types';
import type {Level} from '../lib/format';
import {fixtureFile,fixtureHousehold,fixturePeople,fixtureProfile,fixtureSearch,fixtureStreet,fixtureTotals} from './fixtures';
export type Family='meta'|'geometry'|'areas'|'people'|'person'|'household'|'street'|'search';
export const sources:Record<Family,Source>={meta:'fixture',geometry:'fixture',areas:'fixture',people:'fixture',person:'fixture',household:'fixture',street:'fixture',search:'fixture'};
export const demoParam=true;
export class ApiError extends Error{constructor(message:string,readonly status:number){super(message);}}
async function cancellable<T>(work:()=>T|Promise<T>,signal?:AbortSignal):Promise<T>{
  signal?.throwIfAborted();const value=await work();signal?.throwIfAborted();return value;
}
export async function getMeta():Promise<Meta>{
  const meta=await fixtureFile<Meta>('meta.json');
  return {...meta,...fixtureTotals(),demo:true,source:'fixture',district_label:'Congressional District 5'};
}
export function getAreas(_meta:Meta,level:Level):Promise<AreasPayload>{return fixtureFile(`areas/${level}.json`);}
export function getGeometry(_meta:Meta,level:Level,variant:'coarse'|'fine'):Promise<AreaGeometry>{return fixtureFile(`geometry/${level}.${variant}.json`);}
export function getDistrict():Promise<GeoJSON.FeatureCollection>{return fixtureFile('district.json');}
type Roads=GeoJSON.FeatureCollection<GeoJSON.LineString|GeoJSON.MultiLineString,{kind:string;name?:string}>;
export async function getRoads(bbox:[number,number,number,number],zoom:number,signal?:AbortSignal):Promise<Roads>{
  return cancellable(async()=>{
    const roads=await fixtureFile<Roads>('roads.json');const [w,s,e,n]=bbox;
    return {...roads,features:roads.features.filter(f=>{
      if(zoom<12&&f.properties.kind==='S1400')return false;
      const b=f.bbox;return !b||(b[0]<=e&&b[2]>=w&&b[1]<=n&&b[3]>=s);
    })};
  },signal);
}
export function getPeople(q:PeopleQuery,signal?:AbortSignal):Promise<PeoplePage>{return cancellable(()=>fixturePeople(q),signal);}
export function getProfile(id:string,signal?:AbortSignal):Promise<Profile>{return cancellable(()=>fixtureProfile(id),signal);}
export function getHousehold(id:string,signal?:AbortSignal):Promise<HouseholdPopover>{return cancellable(()=>fixtureHousehold(id),signal);}
export function getStreet(bbox:[number,number,number,number],zoom:number,signal?:AbortSignal):Promise<StreetPayload>{return cancellable(()=>fixtureStreet(bbox,zoom),signal);}
export function searchPeople(text:string,signal?:AbortSignal):Promise<SearchResult>{return cancellable(()=>fixtureSearch(text),signal);}
