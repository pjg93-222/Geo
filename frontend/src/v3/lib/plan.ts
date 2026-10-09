// Block plan helpers: which ranked blocks fit one walk list, the request the plan screen sends, the doors asked for, the planned blocks in the page
// address and the printed-scores batches. Pure, so node --test can load it directly (types come in with `import type`).
import type {WalkPlanOptions,WalkPlanSpan} from '../api/walkTypes';
import type {FeatureRoi} from '../types';
import type {UntappedWeights} from './untapped';

export const MAX_PLAN_SPANS=200;     // planned blocks in one walk list (walk_views.MAX_PICK_SPANS)
export const MAX_LIST_PEOPLE=1500;   // people on one walk list, and in one scores request (walk_views.MAX_VOTERS)

/** What each preset counts, in plain words. A preset this page does not know falls back to the formula the server sends. */
export const PRESET_WORDS:Record<string,string>={
  push:'Untapped potential: undecided people who usually vote and supporters who may not vote, each counted up to 1.5 times by sphere of influence. Doors with a recorded result are left out.',
  mixed:'Many people whose support is split between the two sides and who vote often. A person counts most when their support is halfway, times their chance of voting (the mean of five turnout scores).',
  turnout:'Many supporters who vote less often. A person counts their support times their chance of not voting (one minus the mean of five turnout scores).',
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
export const DOORS_MIN=10,DOORS_MAX=500,DOORS_DEFAULT=60,JUMP_DEFAULT=400;
export const POTENTIAL_DEFAULT:UntappedWeights={wp:0.5,wi:0.5};
/** The balance and influence defaults of the district, and the turf settings (doors, hop limit in metres); a server without them gets the agent defaults. */
export const potentialOf=(roi:Partial<Pick<FeatureRoi,'potential'>>|null|undefined):UntappedWeights=>roi?.potential??POTENTIAL_DEFAULT;
export const turfOf=(roi:Partial<Pick<FeatureRoi,'turf'>>|null|undefined):{doors:number;jump_m:number}=>({doors:roi?.turf?.doors??DOORS_DEFAULT,jump_m:roi?.turf?.jump_m??JUMP_DEFAULT});
const fitDoors=(n:number)=>Math.min(DOORS_MAX,Math.max(DOORS_MIN,Math.round(n)));
/** The doors asked for: a whole number from 10 to 500. Blank or unreadable text takes `d` (itself held to that range). */
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

/** "12,15" from the page address or a saved list's query: whole numbers, each once and in order, junk skipped, at most 200. */
export function parseSpansParam(text:string|null|undefined):number[]{
  const ids=new Set<number>();
  for(const part of (text??'').split(','))if(/^\d{1,9}$/.test(part.trim()))ids.add(+part);
  return [...ids].sort((a,b)=>a-b).slice(0,MAX_PLAN_SPANS);
}
export const spansParam=(ids:readonly number[]):string=>[...ids].sort((a,b)=>a-b).join(',');

/** The ids split into requests of at most one scores request each (1,500). */
export function idChunks(ids:readonly string[],size=MAX_LIST_PEOPLE):string[][]{
  const out:string[][]=[];
  for(let i=0;i<ids.length;i+=size)out.push(ids.slice(i,i+size));
  return out;
}
