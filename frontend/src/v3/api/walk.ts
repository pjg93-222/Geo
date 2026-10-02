// Walk list endpoints (contract: ./walkTypes.ts). Reads use localJson, writes use postJson with the session's CSRF token.
// Every call takes an optional AbortSignal.
import {localJson,postJson} from './client';
import type {
  WalkArea,WalkDispositionEntry,WalkDispositionsResult,WalkLevel,WalkListPayload,WalkListsPayload,WalkMatchInput,WalkMatchPayload,
  WalkOptionsPayload,WalkOverviewProps,WalkStreetsPayload,
} from './walkTypes';

const BASE='/api/v3/walk';
type Params=Record<string,string|number|undefined>;
function query(values:Params):string{
  const q=new URLSearchParams();
  for(const [key,value] of Object.entries(values))if(value!==undefined&&value!=='')q.set(key,String(value));
  const text=q.toString();return text?'?'+text:'';
}
const areaOf=(a:WalkArea):Params=>({ss:a.ss,sh:a.sh,precinct:a.precinct});
const cleanArea=(a:WalkArea):WalkArea=>Object.fromEntries(Object.entries(a).filter(([,v])=>typeof v==='string'&&v!==''));

export type WalkOverview=GeoJSON.FeatureCollection<GeoJSON.LineString,WalkOverviewProps>;

/** Choices for one level. A level lists what lies inside the districts above it. */
export function getWalkOptions(level:WalkLevel,area:WalkArea,signal?:AbortSignal):Promise<WalkOptionsPayload>{
  return localJson(`${BASE}/options${query({level,ss:level==='ss'?undefined:area.ss,sh:level==='precinct'?area.sh:undefined})}`,signal);
}
export function searchWalkStreets(text:string,area:WalkArea,signal?:AbortSignal):Promise<WalkStreetsPayload>{
  return localJson(`${BASE}/streets${query({q:text.trim().slice(0,60),...areaOf(area)})}`,signal);
}
export function matchWalkStreets(rows:WalkMatchInput[],area:WalkArea,signal?:AbortSignal):Promise<WalkMatchPayload>{
  return postJson(`${BASE}/match`,{rows,...cleanArea(area)},signal);
}
/** `streets` is "12:even,15:both"; the area narrows the list the same way as in the picker. */
export function getWalkList(streets:string,area:WalkArea,signal?:AbortSignal):Promise<WalkListPayload>{
  return localJson(`${BASE}/list${query({streets,...areaOf(area)})}`,signal);
}
export function getWalkLists(signal?:AbortSignal):Promise<WalkListsPayload>{return localJson(`${BASE}/lists`,signal);}
export function getWalkSavedList(key:string,signal?:AbortSignal):Promise<WalkListPayload>{return localJson(`${BASE}/lists/${encodeURIComponent(key)}`,signal);}
/** Freezes a list under a title; `queryOf` is the query the list was built from. Answers with the saved list, key set. */
export function saveWalkList(title:string,queryOf:Record<string,string>,signal?:AbortSignal):Promise<WalkListPayload>{
  return postJson(`${BASE}/lists`,{title,query:queryOf},signal);
}
/** A new list of the doors where the latest result on `from` is Not home. */
export function reknockWalkList(title:string,from:string,signal?:AbortSignal):Promise<WalkListPayload>{
  return postJson(`${BASE}/lists`,{title,from,reknock:true},signal);
}
/** Results for a saved list; a null code clears a result. `on` is the day of the visit (yyyy-mm-dd). */
export function saveWalkDispositions(list:string,on:string,entries:WalkDispositionEntry[],signal?:AbortSignal):Promise<WalkDispositionsResult>{
  return postJson(`${BASE}/dispositions`,{list,on,entries},signal);
}
/** Blocks of an area with door counts and registration counts, for the map shading. */
export function getWalkOverview(area:WalkArea,signal?:AbortSignal):Promise<WalkOverview>{return localJson(`${BASE}/overview${query(areaOf(area))}`,signal);}
