// Audience filter (voter-file basis): filter state, the support slider's rules, the filter in words, grouping of voter doors and the export bundle.
// Pure, so node --test can load it directly.
import type {AudienceChoice,AudienceOptions,AudienceSelection,AudienceStreet,AudienceTotals} from '../api/audience.ts';
import type {PublicPoint} from '../api/publicStreets.ts';
import {csv,groupStreets,round6} from './publicStreetGroups.ts';
import type {Grouping,StreetCount} from './publicStreetGroups.ts';

export const VOTER_ZIP='street-groups-voter-file.zip';
export const VOTER_BASIS='Basis: voter file, doors with at least one registered voter matching the filter below, clipped to the drawn area';
export const VOTER_NOTE='Counts are exact; small numbers can identify a household. Keep exports on this machine.';
export const VOTER_SCOPE_WARNING='Counts are voter-file doors matching the filter inside the drawn area. A street-name CSV does not enforce this area, the filter or a partial-road boundary when imported elsewhere.';
export const SUPPORT_HELP='Terrier support score: mean of vendor partisan and presidential support scores, registration and primary ballot. A ranking aid, not a measured belief.';
export const MIDDLE_HELP="Middle third of Terrier's support score; not a measured belief.";
export const NO_COVERAGE_NOTICE='No support scores for this district; every voter counts as no score.';
export const CANVASS_HELP='Latest recorded result; no age limit; moved voters are left out.';

// --- filter state ------------------------------------------------------------------------------------------------------
export interface SupportRange {low:number;high:number;includeNoScore:boolean}
export interface AudienceFilter {include:AudienceSelection;exclude:AudienceSelection;support:SupportRange}
export const DEFAULT_FILTER:AudienceFilter={include:{party:[],canvass:[]},exclude:{party:[],canvass:[]},support:{low:0,high:100,includeNoScore:true}};
export type Dimension='party'|'canvass';
/** A code is either included or excluded, never both: choosing it on one side clears it on the other; choosing it again clears it. */
export function toggleChoice(f:AudienceFilter,side:'include'|'exclude',dim:Dimension,code:string):AudienceFilter{
  const other=side==='include'?'exclude':'include',has=f[side][dim].includes(code);
  return {...f,[side]:{...f[side],[dim]:has?f[side][dim].filter(c=>c!==code):[...f[side][dim],code]},[other]:{...f[other],[dim]:f[other][dim].filter(c=>c!==code)}};
}

// --- support slider ------------------------------------------------------------------------------------------------------
export type Candidate='D'|'R'|null;
export interface SupportPreset {id:'low'|'mid'|'high'|'all';label:string;low:number;high:number}
/** The first and third presets describe party lean, so their names swap with the candidate's party; the ranges never change. */
export function supportPresets(candidate:Candidate):SupportPreset[]{
  const names=candidate==='D'?['Leans R','Persuadable','Leans D']:candidate==='R'?['Leans D','Persuadable','Leans R']:['Lowest third','Persuadable','Highest third'];
  return [{id:'low',label:names[0],low:0,high:100/3},{id:'mid',label:names[1],low:100/3,high:200/3},{id:'high',label:names[2],low:200/3,high:100},{id:'all',label:'Everyone',low:0,high:100}];
}
export function supportEnds(candidate:Candidate):{low:string;mid:string;high:string}{
  return candidate==='D'?{low:'Republican-leaning',mid:'Middle',high:'Democratic-leaning'}:candidate==='R'?{low:'Democratic-leaning',mid:'Middle',high:'Republican-leaning'}:{low:'Lowest scores',mid:'Middle',high:'Highest scores'};
}
const EPS=1e-6;
export const presetMatches=(p:SupportPreset,r:Pick<SupportRange,'low'|'high'>):boolean=>Math.abs(p.low-r.low)<EPS&&Math.abs(p.high-r.high)<EPS;
export const isEveryone=(r:Pick<SupportRange,'low'|'high'>):boolean=>r.low<=0&&r.high>=100;
const clamp=(v:number,lo:number,hi:number)=>Math.min(hi,Math.max(lo,v));
/** Sets one handle. The handles may meet but never cross; the range stays inside 0 to 100. */
export function moveHandle(r:SupportRange,handle:'low'|'high',value:number):SupportRange{
  if(!Number.isFinite(value))return r;
  return handle==='low'?{...r,low:clamp(value,0,r.high)}:{...r,high:clamp(value,r.low,100)};
}
/** Arrow keys move a handle by 1, Page keys by 10, Home and End to its limit. Returns null for any other key. */
export function keyHandle(r:SupportRange,handle:'low'|'high',key:string):SupportRange|null{
  const at=r[handle],step:Record<string,number>={ArrowRight:1,ArrowUp:1,ArrowLeft:-1,ArrowDown:-1,PageUp:10,PageDown:-10};
  if(key in step)return moveHandle(r,handle,at+step[key]);
  if(key==='Home')return moveHandle(r,handle,0);
  if(key==='End')return moveHandle(r,handle,100);
  return null;
}
/** The score under a pointer: its horizontal distance along a track of the given width, 0 to 100. */
export const scoreAt=(clientX:number,trackStart:number,trackWidth:number):number=>trackWidth>0?clamp((clientX-trackStart)/trackWidth*100,0,100):0;
/** The handle a press at `score` should take: the nearer one; on a tie the one that can move toward the press. */
export const nearerHandle=(r:SupportRange,score:number):'low'|'high'=>{
  const a=Math.abs(score-r.low),b=Math.abs(score-r.high);
  return a<b?'low':b<a?'high':score>r.high?'high':'low';
};

// --- the filter in words ------------------------------------------------------------------------------------------------
/** Registration choices read "Registered Green"; the "none" choice is a blank party code. */
export const registeredLabel=(c:AudienceChoice):string=>c.code==='none'?'No party code':/^registered\b/i.test(c.label)?c.label:`Registered ${c.label}`;
const names=(codes:string[],choices:AudienceChoice[],name:(c:AudienceChoice)=>string)=>codes.map(code=>{const c=choices.find(x=>x.code===code);return c?name(c):code;});
/** e.g. "Include Registered Green or Registered Libertarian; exclude canvass Opposed; support 33 to 67, plus no score" */
export function filterInWords(f:AudienceFilter,o:Pick<AudienceOptions,'party'|'canvass'>):string{
  const side=(s:AudienceSelection,join:string)=>{
    const party=names(s.party,o.party,registeredLabel),canvass=names(s.canvass,o.canvass,c=>c.label);
    return [party.length?party.join(' or '):'',canvass.length?`canvass ${canvass.join(' or ')}`:''].filter(Boolean).join(join);
  };
  const parts:string[]=[];
  const inc=side(f.include,' and '),exc=side(f.exclude,' or ');
  if(inc)parts.push(`include ${inc}`);if(exc)parts.push(`exclude ${exc}`);
  const s=f.support;
  if(!isEveryone(s))parts.push(`support ${Math.round(s.low)} to ${Math.round(s.high)}${s.includeNoScore?', plus no score':''}`);
  else if(!s.includeNoScore)parts.push('support 0 to 100');
  return parts.length?parts.join('; ').replace(/^./,ch=>ch.toUpperCase()):'No filter; every registered voter counts';
}

// --- grouping ---------------------------------------------------------------------------------------------------------
/** A voter-file street as the grouping sees it: `doors` is the matching doors; the other two counts ride along for the lists and exports. */
export interface VoterStreetCount extends StreetCount {voter_doors:number;dropped_by_exclude:number}
export const voterStreetCounts=(streets:AudienceStreet[]):VoterStreetCount[]=>streets.map(s=>({id:s.id,label:s.label,center:s.center,doors:s.matching_doors,voter_doors:s.voter_doors,dropped_by_exclude:s.dropped_by_exclude}));
/** The voter-file basis: matching doors are the doors. Streets with none land in `zero`, listed as "No matching doors". */
export const groupVoterStreets=(streets:AudienceStreet[],min:number,max:number):Grouping<VoterStreetCount>=>groupStreets(voterStreetCounts(streets),'voter-file',min,max);
export const NO_MATCHING_DOORS='No matching doors';

// --- export -----------------------------------------------------------------------------------------------------------
export interface VoterExportInfo {
  district:string;filter:string;polygon:PublicPoint[];min:number;max:number;generatedAt:string;totals:AudienceTotals;supportCoverage:boolean;
}
export type VoterStreetStatus='grouped'|'unassigned'|'over_max'|'zero';
export const VOTER_HEADER=['group','street','street_id','matching_doors','voter_doors','dropped_by_exclude','status','reason','basis','filter','min_doors','max_doors','generated_at','scope'];
const REASON:Record<VoterStreetStatus,string>={grouped:'',unassigned:'Unassigned balance',over_max:'Over maximum',zero:NO_MATCHING_DOORS};
const ROW_SCOPE='Voter-file doors matching the filter in the drawn area only; see README.txt';
/** One CSV: each row is a street with its group number (empty unless grouped), status, reason and the basis; the polygon and full warning live in README.txt. */
export function voterGroupsCsv(rows:{group:number|'';status:VoterStreetStatus;street:VoterStreetCount}[],info:VoterExportInfo):string{
  return csv([VOTER_HEADER,...rows.map(r=>[r.group,r.street.label,r.street.id,r.street.doors,r.street.voter_doors,r.street.dropped_by_exclude,r.status,REASON[r.status],'voter-file',info.filter,info.min,info.max,info.generatedAt,ROW_SCOPE])]);
}
/** group-NN.csv per group, not-grouped.csv for anything left out, and a README with the basis line and the filter in words. */
export function voterExportFiles(grouping:Grouping<VoterStreetCount>,info:VoterExportInfo):{name:string;data:string}[]{
  const pad=Math.max(2,String(grouping.groups.length).length),t=info.totals;
  const files=grouping.groups.map((g,i)=>({name:`group-${String(i+1).padStart(pad,'0')}.csv`,data:voterGroupsCsv(g.streets.map(street=>({group:i+1,status:'grouped' as const,street})),info)}));
  const rest=[...grouping.leftovers.map(street=>({group:'' as const,status:'unassigned' as const,street})),...grouping.oversized.map(street=>({group:'' as const,status:'over_max' as const,street})),...grouping.zero.map(street=>({group:'' as const,status:'zero' as const,street}))];
  if(rest.length)files.push({name:'not-grouped.csv',data:voterGroupsCsv(rest,info)});
  files.push({name:'README.txt',data:[
    'Terrier voter-file street groups','',VOTER_SCOPE_WARNING,'',VOTER_BASIS,`Filter: ${info.filter}`,
    `District: ${info.district||'not stated'}`,...(info.supportCoverage?[]:[NO_COVERAGE_NOTICE]),
    `Area polygon (longitude, latitude): ${JSON.stringify(round6(info.polygon))}`,`Limits: ${info.min} to ${info.max} voter doors per group`,
    `Matching voter doors: ${t.matching_doors}; voter doors in the area: ${t.voter_doors}; dropped by exclude in mixed households: ${t.dropped_by_exclude}; unplaced: ${t.unplaced}`,`Generated: ${info.generatedAt}`,'',
    VOTER_NOTE,'',
    `Each group-NN.csv holds one group. not-grouped.csv (if present) lists streets left out: unassigned, over the maximum, or with ${NO_MATCHING_DOORS.toLowerCase()}.`,
    'Importing a group file into Walk list matches whole streets by name, not the drawn area or the filter, and door counts will be those of the walk list.',''].join('\r\n')});
  return files;
}

