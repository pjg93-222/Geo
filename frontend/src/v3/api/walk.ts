// Walk list endpoints (contract: ./walkTypes.ts). Reads use localJson, writes use postJson with the session's CSRF token.
// Every call takes an optional AbortSignal.
import {localJson,postJson} from './client.ts';
import type {
  WalkArea,WalkDispositionEntry,WalkDispositionsResult,WalkLevel,WalkListPayload,WalkListsPayload,WalkListTrim,WalkMatchInput,WalkMatchPayload,
  WalkOptionsPayload,WalkOverviewProps,WalkPlan,WalkPlanOptions,WalkReknockPayload,WalkSavedPayload,WalkScores,WalkStreetsPayload,
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
/** `streets` is "12:even,15:both"; the area narrows the list the same way as in the picker. `spans` are road-span ids from the street plan
 *  (at most 200, in the order given); they add those spans' voters, with or without an area. `fresh` leaves out the people at doors with a recorded result.
 *  `trim` (the one-click list: needs spans and fresh) keeps the blocks whole until the last one, which is cut to reach that many doors; a server without it refuses the name. */
export function getWalkList(streets:string,area:WalkArea,signal?:AbortSignal,spans?:number[],fresh?:boolean,trim?:WalkListTrim):Promise<WalkListPayload>{
  return localJson(`${BASE}/list${query({streets,spans:spans?.join(','),fresh:fresh?1:undefined,doors:trim?.doors,wp:trim?.wp,wi:trim?.wi,...areaOf(area)})}`,signal);
}
/** Block plan: the road spans of an area ranked by the preset's value per 100 m walked; with `doors`, in a precinct, also the blocks one click picks
 *  (candidate districts only). */
export function getWalkPlan(area:WalkArea,options:WalkPlanOptions,signal?:AbortSignal):Promise<WalkPlan>{
  return localJson(`${BASE}/plan${query({...areaOf(area),...options})}`,signal);
}
/** Overall, participation and (candidate districts) support whole-number scores of up to 1,500 listed people, for the printed list. */
export function getWalkScores(ids:string[],signal?:AbortSignal):Promise<WalkScores>{return postJson(`${BASE}/scores`,{ids},signal);}
export function getWalkLists(signal?:AbortSignal):Promise<WalkListsPayload>{return localJson(`${BASE}/lists`,signal);}
export function getWalkSavedList(key:string,signal?:AbortSignal):Promise<WalkListPayload>{return localJson(`${BASE}/lists/${encodeURIComponent(key)}`,signal);}
/** Freezes a list under a title; `queryOf` is the query the list was built from, `fingerprint` that of the list shown (the server refuses a save
 *  without one). Answers with the saved list, key set, and `changed` when it is not the list shown. */
export function saveWalkList(title:string,queryOf:Record<string,string>,fingerprint:string,signal?:AbortSignal):Promise<WalkSavedPayload>{
  return postJson(`${BASE}/lists`,{title,query:queryOf,fingerprint},signal);
}
/** A new list of the doors where the latest result on `from` is Not home; `missing` counts those the voter data no longer has. */
export function reknockWalkList(title:string,from:string,signal?:AbortSignal):Promise<WalkReknockPayload>{
  return postJson(`${BASE}/lists`,{title,from,reknock:true},signal);
}
/** Results for a saved list; a null code clears a result. `on` is the day of the visit (yyyy-mm-dd). */
export function saveWalkDispositions(list:string,on:string,entries:WalkDispositionEntry[],signal?:AbortSignal):Promise<WalkDispositionsResult>{
  return postJson(`${BASE}/dispositions`,{list,on,entries},signal);
}
/** Blocks of an area with door counts and registration counts, for the map shading. */
export function getWalkOverview(area:WalkArea,signal?:AbortSignal):Promise<WalkOverview>{return localJson(`${BASE}/overview${query(areaOf(area))}`,signal);}
