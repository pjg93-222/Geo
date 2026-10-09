// Block plan helpers: which ranked blocks fit one walk list, the request the plan screen sends, the doors asked for, the planned blocks in the page
// address and the printed-scores batches. Pure, so node --test can load it directly (types come in with `import type`).
import type {WalkListTrim,WalkPlanOptions,WalkPlanSpan,WalkTurf} from '../api/walkTypes';
import type {FeatureRoi} from '../types';
import type {UntappedWeights} from './untapped';

export const MAX_PLAN_SPANS=200;     // planned blocks in one walk list (walk_views.MAX_PICK_SPANS)
export const MAX_LIST_PEOPLE=10000;  // people on one walk list: a size safety ceiling (walk_views.MAX_VOTERS)
export const MAX_SCORE_IDS=1500;     // voter ids in one scores request (walk_views.MAX_SCORE_IDS)

/** What each preset counts, in plain words. A preset this page does not know falls back to the formula the server sends. */
export const PRESET_WORDS:Record<string,string>={
  push:'Untapped potential: undecided people who usually vote and supporters who may not vote, each counted up to 1.5 times by sphere of influence. Doors with a recorded result are left out.',
  mixed:'Many people whose support is split between the two sides and who vote often. A person counts most when their support is halfway, times their chance of voting (five turnout scores, lined up against people\'s own voting record).',
  turnout:'Many supporters who vote less often. A person counts their support times their chance of not voting (one minus their chance of voting: five turnout scores, lined up against people\'s own voting record).',
  identify:'Many people whose support is unknown or in the middle third. Each of them counts once.',
};
export const raceWords=(mode:string|null|undefined):string=>mode==='primary'?'Primary election':'General election';

/** The first blocks in rank order whose people fit one list (`cap`, and at most 200 blocks). Stops at the first block that would go over:
 *  a lower rank never takes the place of a higher one. Blocks are ids of road spans. */
export function pickUnderCap(spans:readonly Pick<WalkPlanSpan,'span'|'people'|'rank'>[],cap=MAX_LIST_PEOPLE):number[]{
  const out:number[]=[];let people=0;
  for(const s of [...spans].sort((a,b)=>a.rank-b.rank)){
    if(out.length>=MAX_PLAN_SPANS||people+s.people>cap)break;
    out.push(s.span);people+=s.people;
  }
  return out;
}

// --- one click: the doors asked for and the settings an older server does not send -----------------------------------------
export const DOORS_MIN=1,DOORS_MAX=MAX_LIST_PEOPLE,DOORS_DEFAULT=60,JUMP_DEFAULT=400;   // no more doors than one list can hold people
export const POTENTIAL_DEFAULT:UntappedWeights={wp:0.5,wi:0.5};
/** The balance and influence defaults of the district, and the turf settings (doors, hop limit in metres); a server without them gets the agent defaults. */
export const potentialOf=(roi:Partial<Pick<FeatureRoi,'potential'>>|null|undefined):UntappedWeights=>roi?.potential??POTENTIAL_DEFAULT;
export const turfOf=(roi:Partial<Pick<FeatureRoi,'turf'>>|null|undefined):{doors:number;jump_m:number}=>({doors:roi?.turf?.doors??DOORS_DEFAULT,jump_m:roi?.turf?.jump_m??JUMP_DEFAULT});
const fitDoors=(n:number)=>Math.min(DOORS_MAX,Math.max(DOORS_MIN,Math.round(n)));
/** The doors asked for: a whole number from 1 up to the people one list can hold. Blank or unreadable text takes `d` (itself held to that range). */
export function clampDoors(v:unknown,d:number=DOORS_DEFAULT):number{
  const text=typeof v==='number'?String(v):typeof v==='string'?v.trim():'';
  const n=text===''?NaN:Number(text);
  return fitDoors(Number.isFinite(n)?n:Number.isFinite(d)?d:DOORS_DEFAULT);
}
type DoorsStorage=Pick<Storage,'getItem'|'setItem'>;
const DOORS_KEY='terrier.untapped.doors.';
/** This viewer's doors for a district, or `d` when none are saved or storage is unavailable. */
export function readDoors(district:string,d:number,storage?:DoorsStorage):number{
  try{return clampDoors((storage??localStorage).getItem(DOORS_KEY+district),d);}catch{return clampDoors(d);}
}
export function writeDoors(district:string,n:number,storage?:DoorsStorage):void{
  try{(storage??localStorage).setItem(DOORS_KEY+district,String(clampDoors(n)));}catch{/* the number still works for this visit */}
}

/** A weight as the server reads it (up to six digits and four decimals, no sign, no exponent); blank, negative or unreadable counts as 0. */
export function weightValue(text:string):number{
  const n=Number(String(text??'').trim());
  return Number.isFinite(n)&&n>0?Math.min(999999,Math.round(n*1e4)/1e4):0;
}
/** The plan request: the preset and the three weights typed in (the server scales them to sum to 1). */
export const planOptions=(preset:string,w:{value:string;pop:string;voter:string}):WalkPlanOptions=>
  ({preset,w_value:weightValue(w.value),w_pop:weightValue(w.pop),w_voter:weightValue(w.voter)});

/** "12,15" from the page address or a saved list's query: whole numbers, each once, junk skipped, at most 200. The order stays as written:
 *  the one-click list is cut at the last of its blocks in pick order. */
export function parseSpansParam(text:string|null|undefined):number[]{
  const ids=new Set<number>();
  for(const part of (text??'').split(','))if(/^\d{1,9}$/.test(part.trim()))ids.add(+part);
  return [...ids].slice(0,MAX_PLAN_SPANS);
}
export const spansParam=(ids:readonly number[]):string=>ids.join(',');

// --- the one-click list cut to the doors asked for ------------------------------------------------------------------------------
const WEIGHT_TEXT=/^(0(\.\d{1,4})?|1(\.0{1,4})?)$/;   // the weights the server reads: 0 to 1, up to four decimals
/** The doors, wp and wi of a page address or a saved list's query. Null unless doors is a whole number from 1 up to the people one list can hold; a weight the server would refuse is left out. */
export function parseTrim(doors:string|null|undefined,wp?:string|null,wi?:string|null):WalkListTrim|null{
  const d=(doors??'').trim();
  if(!/^\d{1,6}$/.test(d)||+d<DOORS_MIN||+d>DOORS_MAX)return null;
  const weight=(w:string|null|undefined)=>{const x=(w??'').trim();return WEIGHT_TEXT.test(x)?x:undefined;};
  const p=weight(wp),i=weight(wi);
  return {doors:+d,...p!==undefined?{wp:p}:{},...i!==undefined?{wi:i}:{}};
}
/** What the one-click list asks for, once the plan has answered: the doors and weights it was planned with, when the server cuts the last block (its turf says `trim`, null or not). */
export const trimFor=(turf:Pick<WalkTurf,'trim'>,doors:number,w:{wp:string;wi:string}):WalkListTrim|null=>turf.trim===undefined?null:{doors,wp:w.wp,wi:w.wi};
/** The address and saved-query names for a cut list, each null when there is none. */
export const trimQuery=(t:WalkListTrim|null):Record<'doors'|'wp'|'wi',string|null>=>({doors:t?String(t.doors):null,wp:t?.wp??null,wi:t?.wi??null});

/** The ids split into requests of at most one scores request each (1,500). */
export function idChunks(ids:readonly string[],size=MAX_SCORE_IDS):string[][]{
  const out:string[][]=[];
  for(let i=0;i<ids.length;i+=size)out.push(ids.slice(i,i+size));
  return out;
}
