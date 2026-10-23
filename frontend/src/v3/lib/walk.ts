// Walk list text rules: tallies, dates, phones, run headers, totals, result codes and the CSV in and out.
// Pure, so node --test can load it directly: value imports name the .ts file, types come in with `import type`.
import type {WalkArea,WalkCode,WalkDispositionEntry,WalkLast,WalkListMeta,WalkListPayload,WalkListTrim,WalkMatchInput,WalkRun,WalkSavedList,WalkSavedPayload,WalkScores,WalkSidePick,WalkStreet,WalkTally,WalkTotals,WalkTurf,WalkVoter,WalkWindow} from '../api/walkTypes';
import {csvValue,unguard} from './csv.ts';
import {hasEvidence} from './walkEvidence.ts';
import {MAX_LIST_PEOPLE,MAX_PLAN_SPANS,parseSpansParam,parseTrim} from './plan.ts';
import {marginBin} from './opinions.ts';
import {HEADER,PUBLIC_SCOPE_WARNING} from './publicStreetGroups.ts';
import {VOTER_HEADER,VOTER_SCOPE_WARNING} from './audienceGroups.ts';

const DOT=' · ';
const COUNT=new Intl.NumberFormat('en-US');
export const countText=(n:number,one:string,many=one+'s'):string=>`${COUNT.format(n)} ${n===1?one:many}`;

// --- result codes ----------------------------------------------------------------------------------------------------
export const WALK_CODES:readonly WalkCode[]=['S','U','O','NH','MV'];
export const CODE_LABEL:Record<WalkCode,string>={S:'Supporter',U:'Undecided',O:'Opposed',NH:'Not home',MV:'Moved'};
export const isWalkCode=(v:unknown):v is WalkCode=>typeof v==='string'&&(WALK_CODES as readonly string[]).includes(v);
/** "S Supporter · U Undecided · O Opposed · NH Not home · MV Moved" */
export const codeLegend=():string=>WALK_CODES.map(c=>`${c} ${CODE_LABEL[c]}`).join(DOT);

// --- voter and door cells --------------------------------------------------------------------------------------------
/** Elections voted out of elections eligible: "4/6". A lone slash when none were eligible. */
export function tallyText(t:WalkTally|null|undefined):string{return t&&t.eligible>0?`${t.voted}/${t.eligible}`:'/';}

const ISO=/^(\d{4})-(\d{2})-(\d{2})/;
function ymd(iso:string):[number,number,number]|null{
  const m=ISO.exec(iso??'');if(!m)return null;
  const y=+m[1],mo=+m[2],d=+m[3];
  return mo>=1&&mo<=12&&d>=1&&d<=31?[y,mo,d]:null;
}
/** Registration date as m/d/yy ("1994-09-29" -> "9/29/94"); empty when missing or malformed. */
export function regDate(iso:string):string{
  const p=ymd(iso);return p?`${p[1]}/${p[2]}/${String(p[0]%100).padStart(2,'0')}`:'';
}
/** A calendar date as m/d/yyyy, the way the printed list shows its own date. */
export function fullDate(iso:string):string{
  const p=ymd(iso);return p?`${p[1]}/${p[2]}/${p[0]}`:'';
}
export function localToday():string{
  const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}

/** "(719) 555-0142" from any 10 or 11 digit US number; 7 digits become "555-0142"; anything else is returned trimmed. */
export function phoneText(raw:string):string{
  const text=(raw??'').trim();
  let d=text.replace(/\D/g,'');
  if(d.length===11&&d[0]==='1')d=d.slice(1);
  if(d.length===10)return `(${d.slice(0,3)}) ${d.slice(3,6)}-${d.slice(6)}`;
  if(d.length===7)return `${d.slice(0,3)}-${d.slice(3)}`;
  return text;
}

export const sideLabel=(side:WalkSidePick):string=>side==='even'?'Even':side==='odd'?'Odd':'Both';

const UNIT_WORD=/^(?:#|(?:apt|apartment|unit|ste|suite|lot|rm|room|bldg|building|fl|floor|trlr|trailer|spc|space)(?![a-z]))/i;
/** A bare unit such as "204" or "B" reads "#204"; units that already carry a word ("Apt 204") are kept as they are. */
export function unitText(unit:string):string{
  const u=(unit??'').trim();
  return !u?'':UNIT_WORD.test(u)?u:`#${u}`;
}
/** House number plus unit, as the Address column shows it: "4102 #204". */
export function doorAddress(door:{house:string;unit:string}):string{
  const u=unitText(door.unit);return u?`${door.house} ${u}`.trim():door.house;
}

// --- runs and totals -------------------------------------------------------------------------------------------------
/** "4102 → 4172", or one number when the run covers a single house number. */
export function houseSpan(run:Pick<WalkRun,'from_house'|'to_house'>):string{
  const a=run.from_house,b=run.to_house;
  return !a||!b||a===b?a||b||'':`${a} → ${b}`;
}
/** "Stop 1 of 6 · 4102 → 4172 · 11 doors" */
export function runHeader(run:WalkRun,total:number):string{
  return [`Stop ${run.stop} of ${total}`,houseSpan(run),countText(run.doors.length,'door')].filter(Boolean).join(DOT);
}

export function distanceText(meters:number):string{return `${(Math.round((Number.isFinite(meters)?Math.max(0,meters):0)/100)/10).toFixed(1)} km`;}
/** "4 streets · 6 stops · 58 doors · 104 voters · 2.9 km". Lists saved before `minutes` was dropped still carry it; it is ignored. */
export function totalsLine(t:WalkTotals):string{
  return [countText(t.streets,'street'),countText(t.stops,'stop'),countText(t.doors,'door'),countText(t.voters,'voter'),distanceText(t.walk_m)].join(DOT);
}

/** Area words from the list query, for the printed title and file name when the caller has no label of its own. */
export function areaText(query:Record<string,string>|undefined):string{
  const q=query??{};const parts:string[]=[];
  if(q.ss)parts.push(`Senate district ${q.ss}`);
  if(q.sh)parts.push(`House district ${q.sh}`);
  if(q.precinct)parts.push(`Precinct ${q.precinct}`);
  return parts.join(DOT);
}
/** walk-list-<area>-<yyyy-mm-dd>.<ext>; the day falls back to today when the given date is not an ISO date. */
export function walkFileName(area:string,isoDate:string,ext:'pdf'|'csv'|'zip'):string{
  const slug=area.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-+|-+$/g,'').slice(0,60).replace(/-+$/,'')||'list';
  return `walk-list-${slug}-${ymd(isoDate)?isoDate.slice(0,10):localToday()}.${ext}`;
}
/** The line a hosted server's walk list carries on every page: who printed it, to the minute, in UTC ("name@example.org · 2026-10-12 14:03 UTC"). */
export function watermarkText(email:string,now:Date=new Date()):string{
  return `${email.trim()}${DOT}${now.toISOString().slice(0,16).replace('T',' ')} UTC`;
}

// --- CSV out ---------------------------------------------------------------------------------------------------------
export const CSV_COLUMNS=['Stop','Precinct','Street','Side','Address','Unit','Name','Phone','Pri','Gen','Abs','Party','Age','Gender','Reg. Date','Result'] as const;

// Printed scores (the Print scores toggle, off by default): whole numbers from 0 to 100 from POST /api/v3/walk/scores, by person id.
export type WalkScoreMap=WalkScores['scores'];
export const SCORE_NAMES={ovr:'Overall',sup:'Candidate support',part:'Participation',push:'Needs a push'} as const;
export type ScoreKey=keyof typeof SCORE_NAMES;
/** The score columns of a printed list: overall and participation, candidate support when anyone listed has a support score, then Needs a push when anyone has
 *  one (Colorado has neither). None without scores. */
export const scoreColumns=(scores?:WalkScoreMap):ScoreKey[]=>{
  if(!scores)return [];
  const has=(k:'sup'|'push')=>Object.values(scores).some(s=>s[k]!=null);
  return ['ovr',...has('sup')?['sup' as const]:[],'part',...has('push')?['push' as const]:[]];
};
/** One person's score as text; blank when the person has none. */
export function scoreCell(scores:WalkScoreMap|undefined,id:string,key:ScoreKey):string{
  const v=scores&&Object.hasOwn(scores,id)?scores[id][key]:undefined;
  return v==null?'':String(v);
}

export {csvValue};
const csvField=(value:unknown):string=>`"${csvValue(value).replace(/[\r\n]+/g,' ').replace(/"/g,'""')}"`;
// A spreadsheet reads "4/6" as a date, so the file says "4 of 6"; the registration date stays ISO for the same reason.
const csvTally=(t:WalkTally|null|undefined):string=>t?`${t.voted} of ${t.eligible}`:'';

/**
 * Result codes by person id for the marks on this list, on screen; null is a cleared mark. A voter with no entry has no mark: a result
 * on file that was recorded on another list is history (WalkHistory), shown dated and apart, and is not a mark on this list.
 */
export type WalkResults=Record<string,WalkCode|null>;
/** The latest result on file for each voter by person id (null: none). A voter with no entry here shows what the list came with. */
export type WalkHistory=Record<string,WalkLast|null>;
/** The latest code on file for each voter of a list, as codes, whichever list it was recorded on. History, not marks: see listMarks. */
export function initialResults(payload:WalkListPayload):WalkResults{
  const out:WalkResults={};
  for(const run of payload.runs??[])for(const door of run.doors)for(const v of door.voters)if(v.last?.code)out[v.person_id]=v.last.code;
  return out;
}
/**
 * The marks a saved list already has: each voter whose latest result on file was recorded on this very list (`last.list` is the list's
 * key). They come back selected. A result recorded on another list, such as the list a re-knock list was made from, stays history.
 */
export function listMarks(payload:Pick<WalkListPayload,'runs'|'list'>):WalkResults{
  const key=payload.list?.key,out:WalkResults={};
  if(!key)return out;   // a list that is not saved has no results of its own
  for(const run of payload.runs??[])for(const door of run.doors)for(const v of door.voters)if(v.last?.code&&v.last.list===key)out[v.person_id]=v.last.code;
  return out;
}
/** A voter's latest result on file: the fresh look at the list when there is one, else what the list came with. */
export const lastOf=(v:Pick<WalkVoter,'person_id'|'last'>,history?:WalkHistory):WalkLast|null=>
  history&&Object.hasOwn(history,v.person_id)?history[v.person_id]:v.last??null;
/** The latest result on file for every voter of a list as the server sent it (null where there is none). */
export function historyOf(payload:Pick<WalkListPayload,'runs'>):WalkHistory{
  const out:WalkHistory={};
  for(const run of payload.runs??[])for(const door of run.doors)for(const v of door.voters)out[v.person_id]=v.last??null;
  return out;
}
/** "NH 9/30/2026": a result on file with the day it was recorded. */
export function lastText(last:WalkLast|null|undefined):string{
  return last?[last.code,fullDate(last.on)].filter(Boolean).join(' '):'';
}
/** "Not home on 9/30/2026", for a tooltip. */
export function lastLabel(last:WalkLast|null|undefined):string{
  if(!last)return '';
  const day=fullDate(last.on);
  return `${CODE_LABEL[last.code]??last.code}${day?` on ${day}`:''}`;
}
/** The voters whose mark is a cleared one (null) with no result still on its way: the server has the clearing, so what it holds for them is the history to show. `holds` says a result for the voter is still on its way. */
export const confirmedClears=(results:WalkResults,holds:(id:string)=>boolean):string[]=>Object.keys(results).filter(id=>results[id]===null&&!holds(id));
/** The marks without the cleared ones named in `ids` (a voter marked again since stays marked). Returns `results` itself when nothing changes. */
export function dropCleared(results:WalkResults,ids:readonly string[]):WalkResults{
  let out:WalkResults|null=null;
  for(const id of ids)if(results[id]===null){out??={...results};delete out[id];}
  return out??results;
}

/** What choosing `code` does to a voter's mark on this list. The code is recorded as a new visit whatever the history on file says (a voter last seen
 *  Not home who is not home again gets another Not home); choosing the code that is already marked takes the mark back (null), except a mark the
 *  server refused (`unsaved`): choosing it again records it again. */
export const markAfterClick=(mark:WalkCode|null|undefined,code:WalkCode,unsaved=false):WalkCode|null=>mark===code&&!unsaved?null:code;
/** A result as the walker set it: the code (null clears the result) and the day of the visit, taken when the walker set it. */
export interface WalkVisit{code:WalkCode|null;on:string}
/** The day and entries of one request. A request holds one day: the outbox batches by day, and the day is the one taken when the results were set, never today's. */
export function visitRequest(batch:ReadonlyMap<string,WalkVisit>):{on:string;entries:WalkDispositionEntry[]}{
  let on='';const entries:WalkDispositionEntry[]=[];
  for(const [person_id,v] of batch){
    if(!entries.length)on=v.on;else if(v.on!==on)throw new Error('A request can hold the results of one day only.');
    entries.push({person_id,code:v.code});
  }
  return {on,entries};
}

/** Most results one request carries (MAX_ENTRIES in walk_views.py). */
export const MAX_RESULTS_PER_REQUEST=200;
/** Takes up to `max` waiting results out of `pending`, oldest first, for one request. With `group`, a request holds the results of one group, the group of the oldest waiting one; the others wait for a request of their own. */
export function takeResults<K,V>(pending:Map<K,V>,max=MAX_RESULTS_PER_REQUEST,group?:(value:V)=>string):Map<K,V>{
  const batch=new Map<K,V>();let first:string|undefined;
  for(const [k,v] of pending){
    if(batch.size>=max)break;
    if(group){const g=group(v);if(first===undefined)first=g;else if(g!==first)continue;}
    batch.set(k,v);
  }
  for(const k of batch.keys())pending.delete(k);
  return batch;
}

export type SaveState='idle'|'pending'|'saving'|'saved'|'error';
/**
 * Results the walker has set that the server has not confirmed. A result waits until a request takes it, then stays unconfirmed
 * until that request is answered (confirm) or fails (restore), so a page that is closing can tell results are still on their way.
 */
export interface ResultOutbox<K,V>{
  readonly waiting:Map<K,V>;
  set(key:K,value:V):void;
  /** Moves up to `max` waiting results into one request and returns them. */
  take(max?:number):Map<K,V>;
  confirm(batch:Map<K,V>):void;
  /** The request failed: its results wait again, except any the walker has changed since. */
  restore(batch:Map<K,V>):void;
  /** True while a result is waiting or travelling in a request that has not been answered. */
  unconfirmed():boolean;
  /** True while a result for this key is waiting or travelling in a request that has not been answered. */
  holds(key:K):boolean;
  /** Forgets the waiting results; requests already sent count until they are answered. */
  clear():void;
  /** Renames waiting results (old key -> new key), in their order; a request already sent keeps its keys. */
  rekey(moved:ReadonlyMap<K,K>):void;
}
/** `group` splits the waiting results into requests: one request never holds results of two groups (here, two days of visits). */
export function resultOutbox<K,V>(group?:(value:V)=>string):ResultOutbox<K,V>{
  const waiting=new Map<K,V>();const sent=new Set<Map<K,V>>();
  return {
    waiting,
    set(key,value){waiting.set(key,value);},
    take(max=MAX_RESULTS_PER_REQUEST){const batch=takeResults(waiting,max,group);if(batch.size)sent.add(batch);return batch;},
    confirm(batch){sent.delete(batch);},
    restore(batch){sent.delete(batch);for(const [k,v] of batch)if(!waiting.has(k))waiting.set(k,v);},
    unconfirmed:()=>waiting.size>0||sent.size>0,
    holds:key=>waiting.has(key)||[...sent].some(b=>b.has(key)),
    clear(){waiting.clear();},
    rekey(moved){if(!moved.size)return;const all=[...waiting];waiting.clear();for(const [k,v] of all)waiting.set(moved.get(k)??k,v);},
  };
}
/**
 * The server will not take these results however often they are sent: a voter no longer on the list or a visit day it refuses (400), or a
 * list that is gone (404). `whole` when none of the batch can be saved (the list is gone, or the server will not save the list itself), so
 * the batch is not split to look for the refused ones.
 */
export class ResultsRefused extends Error{
  whole:boolean;
  constructor(message:string,whole=false){super(message);this.name='ResultsRefused';this.whole=whole;}
}
/** The walk API's answers that mean the same request is refused again: a bad request, a list not found. Nothing else: a conflict while the
 *  store is swapped (409), access refused (403), a timeout, a rate limit or a server fault is worth sending again. */
export const refusedStatus=(status:number):boolean=>status===400||status===404;
/**
 * The refusal a failed request means, or null for a failure to retry. Only the walk API answering in JSON with an error of its own counts
 * (`answered`): an HTML page from a proxy or from Django, or a failure to get the session, does not. Saving the list (`list`) is refused only
 * on a 400, and then all its results are; a results request is refused on a 400 (split to find the refused ones) or a 404 (the list is gone: all of it).
 */
export function refusalOf(e:unknown,step:'list'|'results'):ResultsRefused|null{
  if(!e||typeof e!=='object')return null;
  const {status,answered,message}=e as {status?:unknown;answered?:unknown;message?:unknown};
  if(answered!==true||typeof status!=='number'||!refusedStatus(status)||(step==='list'&&status!==400))return null;
  return new ResultsRefused(typeof message==='string'?message:'',step==='list'||status===404);
}
/** What a drain needs from its caller. */
export interface DrainIo<K,V>{
  ready():boolean;                                    // false while no list is on screen
  send(batch:Map<K,V>):Promise<unknown>;              // saves the list if it is not saved yet, then posts the batch; throws ResultsRefused for a refusal
  report(state:SaveState,message?:string):void;
  refuse?(refused:Map<K,V>,message:string):void;     // results the server refused: they leave the outbox and are not sent again
}
/** One request: true when it is saved, the server's word when it is refused. A refusal of the whole batch, and any other failure, is thrown. */
async function trySend<K,V>(batch:Map<K,V>,io:DrainIo<K,V>,left:Map<K,V>):Promise<true|string>{
  try{await io.send(batch);}
  catch(e){if(e instanceof ResultsRefused&&!e.whole)return e.message;throw e;}
  for(const k of batch.keys())left.delete(k);
  return true;
}
const refuseAll=<K,V>(batch:Map<K,V>,left:Map<K,V>,refused:Map<K,V>):void=>{for(const [k,v] of batch){refused.set(k,v);left.delete(k);}};
/**
 * Sends a batch. A refused request is split in halves to find the refused results, so one bad result costs no other; when both halves are
 * refused as well the split stops and the whole batch is refused, so a batch refused throughout costs three requests, not one per result.
 * Saved and refused results leave `left`; answers with the server's word on a refusal (`known`: the batch was already sent and refused).
 */
async function sendSplit<K,V>(batch:Map<K,V>,io:DrainIo<K,V>,left:Map<K,V>,refused:Map<K,V>,known?:string):Promise<string>{
  const first=known??await trySend(batch,io,left);
  if(first===true)return '';
  if(batch.size===1){refuseAll(batch,left,refused);return first;}
  const all=[...batch],half=all.length>>1,parts=[new Map(all.slice(0,half)),new Map(all.slice(half))],said:(true|string)[]=[];
  for(const part of parts){
    const s=await trySend(part,io,left);said.push(s);
    if(s!==true&&part.size===1)refuseAll(part,left,refused);   // settled now, so a failure of the other half cannot send it again
  }
  if(said[0]!==true&&said[1]!==true){refuseAll(batch,left,refused);return said[0]||said[1]||first;}
  let why='';
  for(let i=0;i<2;i++){const s=said[i];if(s!==true)why=(await sendSplit(parts[i],io,left,refused,s))||why;}
  return why||first;
}
/**
 * Sends the waiting results, one request at a time and at most MAX_RESULTS_PER_REQUEST each, until none are left.
 * False when a request fails: its results that were not saved go back to waiting and the failure is reported. Results the server
 * refuses are not a failure: they leave the outbox and go to `refuse`, so a refusal never holds up the results behind it, or the screen.
 */
export async function drainResults<K,V>(outbox:ResultOutbox<K,V>,io:DrainIo<K,V>):Promise<boolean>{
  while(io.ready()&&outbox.waiting.size){
    const batch=outbox.take(),left=new Map(batch),refused=new Map<K,V>();
    io.report('saving');
    let why='',failed:unknown=null;
    try{why=await sendSplit(batch,io,left,refused);}
    catch(e){
      if(e instanceof ResultsRefused){why=e.message;for(const [k,v] of left)refused.set(k,v);left.clear();}   // the list cannot be saved: nor can any of its results
      else failed=e;
    }
    outbox.confirm(batch);outbox.restore(left);   // what was neither saved nor refused waits again, unless the walker has changed it since
    if(refused.size)io.refuse?.(refused,why||'The server refused them.');
    if(failed!==null){
      io.report('error',failed instanceof Error&&failed.message?failed.message:'The results could not be saved.');
      return false;
    }
    io.report(outbox.waiting.size?'pending':refused.size===batch.size?'idle':'saved');
  }
  return true;
}

// --- results that outlive the screen -------------------------------------------------------------------------------------
/**
 * The results of one list that the server has not confirmed, with what saving them needs. It belongs to the page, not to the
 * screen: leaving the walk list with a failed save keeps the entry on the shelf, and coming back sends it again.
 */
export interface ListResults<K,V>{
  readonly box:ResultOutbox<K,V>;
  /** The saved list; null until the list is saved. */
  meta:WalkListMeta|null;
  title:string;
  readonly query:Record<string,string>;
  /** The request that saves the list, while one is open: a second ask shares it, so a list is saved once. */
  saving:Promise<WalkListMeta>|null;
  /** The latest flush of this list; the next one starts after it, so requests for one list never overlap and stay in order. */
  chain:Promise<boolean>;
  /** Where each voter of the list lives (doorsByPerson), to name the doors of results the server refuses. */
  where:Record<string,string>;
  /** The fingerprint of the list shown, sent when it is saved. */
  readonly fingerprint?:string;
  /** Each voter of the list shown as the walker saw them (seenBy), to find them again on a list built anew. */
  readonly seen:Record<string,string>;
  /** When the list saved is not the one shown (the voter data changed in between): that list, its voters, and the new person id of each
   *  voter shown whose id changed but who is plainly on it (same door and name). Null otherwise. */
  replaced:WalkSavedPayload|null;
  roster:ReadonlySet<K>|null;
  moved:ReadonlyMap<K,K>|null;
}
/** A list's results with the day of each visit: the form the walk screen keeps them in. */
export type HeldList=ListResults<string,WalkVisit>;
export interface ResultShelf<K,V>{
  /** A new, empty entry for a list that has come on screen. It joins the shelf when `keep` is called (the first result does). */
  open(list:{meta:WalkListMeta|null;title:string;query:Record<string,string>;where?:Record<string,string>;seen?:Record<string,string>;fingerprint?:string}):ListResults<K,V>;
  keep(entry:ListResults<K,V>):void;
  /** The entries that hold results the server has not confirmed. An entry that holds none leaves the shelf. */
  held():ListResults<K,V>[];
  /** True while any list holds a result the server has not confirmed. */
  unconfirmed():boolean;
}
export function resultShelf<K,V>(group?:(value:V)=>string):ResultShelf<K,V>{
  const entries=new Set<ListResults<K,V>>();
  const held=():ListResults<K,V>[]=>{for(const e of entries)if(!e.box.unconfirmed())entries.delete(e);return [...entries];};
  return {
    open:list=>({box:resultOutbox<K,V>(group),meta:list.meta,title:list.title,query:list.query,saving:null,chain:Promise.resolve(true),where:list.where??{},
      seen:list.seen??{},fingerprint:list.fingerprint,replaced:null,roster:null,moved:null}),
    keep:entry=>{entries.add(entry);},
    held,
    unconfirmed:()=>held().length>0,
  };
}
// --- a list saved after the voter data changed ------------------------------------------------------------------------------
// Saving builds the list again. When the voter data changed since the list was shown, the server saves the list it builds now and says so
// (`changed`): the page shows that list with a note, and results for voters it no longer has are refused here, kept as the walker's marks.
export const LIST_REPLACED='The voter data was updated since this list was shown; showing the saved version.';
export const OFF_LIST='The voter data was updated since this list was shown, and the saved list does not have these voters.';
/** A name as two builds both spell it: no accents, case, punctuation or extra spaces. */
const nameKey=(name:string):string=>name.normalize('NFKD').replace(/\p{M}/gu,'').toLowerCase().replace(/[^a-z0-9]+/g,' ').trim();
/** Each voter of a list as the walker sees them: street, house, unit and name. */
export function seenBy(payload:Pick<WalkListPayload,'runs'>):Record<string,string>{
  const out:Record<string,string>={};
  for(const run of payload.runs??[])for(const door of run.doors)for(const v of door.voters)out[v.person_id]=[run.street,door.house,door.unit,nameKey(v.name)].join('|');
  return out;
}
/** The new person id of each voter shown whose id is not on the saved list, when one voter there is at the same door with the same name and
 *  no other voter shown was. Two people alike at one door, or a voter who is not there, are left out: their results are not guessed. */
export function movedIds(seen:Record<string,string>,saved:Pick<WalkListPayload,'runs'>):Map<string,string>{
  const now=seenBy(saved),there=new Map<string,string[]>(),was=new Map<string,number>(),out=new Map<string,string>();
  for(const [id,k] of Object.entries(now))there.set(k,[...there.get(k)??[],id]);
  for(const k of Object.values(seen))was.set(k,(was.get(k)??0)+1);
  for(const [id,k] of Object.entries(seen)){
    const to=there.get(k);
    if(!Object.hasOwn(now,id)&&to?.length===1&&was.get(k)===1&&!Object.hasOwn(seen,to[0]))out.set(id,to[0]);
  }
  return out;
}
/** Takes the answer to saving an entry's list: its details and, when it is not the list shown, that list, its voters, and the voters shown whose
 *  person id changed (movedIds), whose waiting results now carry the new id. Answers with the details. */
export function adoptSaved<V>(entry:ListResults<string,V>,saved:WalkSavedPayload):WalkListMeta{
  if(saved.changed){
    entry.replaced=saved;entry.roster=new Set(Object.keys(historyOf(saved)));entry.where={...doorsByPerson(saved),...entry.where};
    entry.moved=movedIds(entry.seen,saved);entry.box.rekey(entry.moved);
  }
  return entry.meta=saved.list;
}
/** `record` with its keys renamed (old -> new); `record` itself when nothing moves. */
export function rekeyed<T>(record:Record<string,T>,moved:ReadonlyMap<string,string>|null):Record<string,T>{
  if(!moved?.size||!Object.keys(record).some(k=>moved.has(k)))return record;
  return Object.fromEntries(Object.entries(record).map(([k,v])=>[moved.get(k)??k,v]));
}
/** The results of a batch for voters on the saved list (`send`, under their id there), and those for voters it does not have (`off`). No roster: all are sent. */
export function splitOffRoster<K,V>(roster:ReadonlySet<K>|null,batch:Map<K,V>,moved?:ReadonlyMap<K,K>|null):{send:Map<K,V>;off:Map<K,V>}{
  if(!roster)return {send:batch,off:new Map()};
  const send=new Map<K,V>(),off=new Map<K,V>();
  for(const [k,v] of batch){const id=moved?.get(k)??k;if(roster.has(id))send.set(id,v);else off.set(k,v);}
  return {send,off};
}
/** "2 voters marked Not home are no longer in the voter data." */
export const missingText=(n:number):string=>`${countText(n,'voter')} marked Not home ${n===1?'is':'are'} no longer in the voter data.`;

/** Sends the waiting results of one list after any flush of that list still open. False when a request fails (the results stay on the entry). */
export function flushList<K,V>(entry:ListResults<K,V>,io:DrainIo<K,V>):Promise<boolean>{
  const run=():Promise<boolean>=>drainResults(entry.box,io);
  return entry.chain=entry.chain.then(run,run);
}

// --- one answer at a time ------------------------------------------------------------------------------------------------
export interface LatestJob{readonly signal:AbortSignal;live():boolean;end():void}
/**
 * Follows the one job whose answer is still wanted, such as the street matching of the file chosen last. Starting another job, or
 * cancelling, makes the earlier one stale and aborts its request; a stale job checks `live()` before it changes anything.
 */
export function latestJob(){
  let open:AbortController|null=null;
  return {
    start():LatestJob{
      open?.abort();
      const mine=open=new AbortController();
      return {signal:mine.signal,live:()=>open===mine,end(){if(open===mine)open=null;}};
    },
    /** True when a job was open. */
    cancel():boolean{const had=open!==null;open?.abort();open=null;return had;},
  };
}

// --- results the server refused: still the walker's marks, flagged as not saved, and named in a note ----------------------------------
/** Where each voter of a list lives, to name a door: "4102 N Cedar Heights Dr". */
export function doorsByPerson(payload:Pick<WalkListPayload,'runs'>):Record<string,string>{
  const out:Record<string,string>={};
  for(const run of payload.runs??[])for(const door of run.doors)for(const v of door.voters)out[v.person_id]=`${doorAddress(door)} ${run.street}`.trim();
  return out;
}
/** What names a list across visits: its key once it is saved, else the query it is built from. */
export const listIdentity=(key:string|null|undefined,query?:Record<string,string>):string=>
  key?`key:${key}`:'query:'+Object.keys(query??{}).sort().map(k=>`${k}=${query?.[k]??''}`).join('&');
/** The marks the server refused, by list (listIdentity) and voter. They stay the walker's marks until marked again and saved: never erased. */
export type RefusedMarks=Map<string,Map<string,WalkCode|null>>;
export function keepRefused(store:RefusedMarks,list:string,refused:ReadonlyMap<string,{code:WalkCode|null}>):void{
  const marks=store.get(list)??new Map<string,WalkCode|null>();
  for(const [id,v] of refused)marks.set(id,v.code);
  if(marks.size)store.set(list,marks);
}
/** A list's refused marks renamed to the person ids of the list saved (movedIds). */
export function rekeyRefused(store:RefusedMarks,list:string,moved:ReadonlyMap<string,string>|null):void{
  const marks=store.get(list);
  if(marks&&moved?.size)store.set(list,new Map([...marks].map(([k,v])=>[moved.get(k)??k,v])));
}
/** Voters whose result has now been saved: their marks are no longer refused. */
export function clearRefused(store:RefusedMarks,list:string,ids:Iterable<string>):void{
  const marks=store.get(list);if(!marks)return;
  for(const id of ids)marks.delete(id);
  if(!marks.size)store.delete(list);
}
/** A list as it comes on screen: the marks recorded on it (listMarks), with any mark of the walker's that the server refused on top, flagged. */
export function marksWithRefused(payload:Pick<WalkListPayload,'runs'|'list'>,store:RefusedMarks,list:string):{results:WalkResults;unsaved:Set<string>}{
  const results=listMarks(payload),unsaved=new Set<string>(),marks=store.get(list);
  if(marks)for(const run of payload.runs??[])for(const door of run.doors)for(const v of door.voters)
    if(marks.has(v.person_id)){results[v.person_id]=marks.get(v.person_id)??null;unsaved.add(v.person_id);}
  return {results,unsaved};
}
/** The voters of the list on screen whose mark is flagged Not saved. Flags for voters the list does not show (a voter the saved list dropped)
 *  are left out: such a mark can never be saved from this list, so it must not hold the page's unload guard up. */
export function flaggedOnList(payload:Pick<WalkListPayload,'runs'>|null,unsaved:ReadonlySet<string>):ReadonlySet<string>{
  if(!payload||!unsaved.size)return new Set();
  const out=new Set<string>();
  for(const run of payload.runs??[])for(const door of run.doors)for(const v of door.voters)if(unsaved.has(v.person_id))out.add(v.person_id);
  return out;
}
/** A refused mark as the list and the CSV show it: "S (not saved)", or "cleared (not saved)" for a clearing the server refused. */
export const unsavedText=(code:WalkCode|null|undefined):string=>`${code??'cleared'} (not saved)`;
/** The results the server refused since the walker last dismissed the note: each list by title, each door once with its code, each reason once.
 *  `local`: some were refused by the page itself (voters the saved list does not have), so the headline does not say the server refused them. */
export interface RefusalNote{lists:{title:string;doors:Record<string,string>}[];reasons:string[];local?:boolean}
export function addRefusal(note:RefusalNote|null,title:string,refused:ReadonlyMap<string,{code:WalkCode|null}>,doors:Record<string,string>,why:string,local=false):RefusalNote{
  const lists=(note?.lists??[]).map(l=>({title:l.title,doors:{...l.doors}}));
  const name=title.trim()||'Walk list';
  let list=lists.find(l=>l.title===name);if(!list){list={title:name,doors:{}};lists.push(list);}
  // own properties only, defined rather than assigned: any person id (even "__proto__") is a plain key of the note
  for(const [id,v] of refused)Object.defineProperty(list.doors,id,{value:`${(Object.hasOwn(doors,id)&&doors[id])||'A door on this list'} (${v.code??'cleared'})`,enumerable:true,writable:true,configurable:true});
  const reasons=note?.reasons??[],w=why.trim();
  return {lists,reasons:w&&!reasons.includes(w)?[...reasons,w]:reasons,...note?.local||local?{local:true}:{}};
}
export const refusalCount=(note:RefusalNote|null):number=>(note?.lists??[]).reduce((n,l)=>n+Object.keys(l.doors).length,0);
/** "The server refused 3 results, so they were not saved. Record these doors again:"; "3 results were not saved:" when some were refused by the page. */
export function refusalHeadline(note:RefusalNote|null):string{
  const n=refusalCount(note);
  if(note?.local)return `${countText(n,'result')} ${n===1?'was':'were'} not saved:`;
  return `The server refused ${countText(n,'result')}, so ${n===1?'it was':'they were'} not saved. Record ${n===1?'that door':'these doors'} again:`;
}

/** The phone bar that points at the note: "1 result not saved". */
export const refusalBarText=(note:RefusalNote|null):string=>`${countText(refusalCount(note),'result')} not saved`;

// A mark set on screen wins over the result on file; a cleared mark leaves the cell empty until the screen has asked what is on file. A mark
// the server refused keeps its code, flagged: the CSV is the walker's copy of what was seen at the door.
const resultCode=(v:WalkVoter,results?:WalkResults,history?:WalkHistory,unsaved?:ReadonlySet<string>):string=>
  results&&Object.hasOwn(results,v.person_id)?unsaved?.has(v.person_id)?unsavedText(results[v.person_id]):results[v.person_id]??'':lastOf(v,history)?.code??'';

/** One row per voter in walking order. Result holds the mark in `results` when the walker has set one, flagged when it is in `unsaved`, else the latest
 *  result on file (`history` is a fresher look at what is on file than the list itself). */
export const needsAddressLocality=(payload:WalkListPayload):boolean=>payload.query.imported==='1'||(payload.runs??[]).some(run=>run.doors.some(door=>!door.voters.length&&!!(door.city||door.state||door.zip)));
export function walkCsvRows(payload:WalkListPayload,results?:WalkResults,history?:WalkHistory,unsaved?:ReadonlySet<string>,scores?:WalkScoreMap,includeLocality=needsAddressLocality(payload),includeEvidence=hasEvidence(payload)):string[][]{
  const rows:string[][]=[],cols=scoreColumns(scores);
  const evidenceCells=(door:WalkRun['doors'][number]):string[]=>includeEvidence?(door.evidence?[door.evidence.address_status,door.evidence.location_precision,door.evidence.decision,door.evidence.reason_codes.join('; ')]:['','','','']):[];
  for(const run of payload.runs??[])for(const door of run.doors){
    if(!door.voters.length){
      rows.push([String(run.stop),run.precinct,run.street,sideLabel(run.side),door.house,door.unit,'','','','','','','','','','',...cols.map(()=>''),...includeLocality?[door.city??'',door.state??'',door.zip??'']:[],...evidenceCells(door)]);
      continue;
    }
    for(const v of door.voters)rows.push([
    String(run.stop),run.precinct,run.street,sideLabel(run.side),door.house,door.unit,v.name,phoneText(v.phone),
    csvTally(v.pri),csvTally(v.gen),csvTally(v.abs),v.party,v.age==null?'':String(v.age),v.gender,v.reg_date,resultCode(v,results,history,unsaved),
    ...cols.map(k=>scoreCell(scores,v.person_id,k)),   // after Result, so no column of the default file moves
    ...includeLocality?[door.city??'',door.state??'',door.zip??'']:[],
    ...evidenceCells(door),
  ]);
  }
  return rows;
}
const BOM=String.fromCharCode(0xfeff);
/** UTF-8 BOM, CRLF rows, every field quoted. */
export function buildWalkCsv(payload:WalkListPayload,results?:WalkResults,history?:WalkHistory,unsaved?:ReadonlySet<string>,scores?:WalkScoreMap,includeLocality=needsAddressLocality(payload),includeEvidence=hasEvidence(payload)):string{
  const head=[...CSV_COLUMNS,...scoreColumns(scores).map(k=>SCORE_NAMES[k]),...includeLocality?['City','State','ZIP']:[],...includeEvidence?['Address status','Location precision','Review decision','Evidence reasons']:[]] as string[];
  const lines=[head,...walkCsvRows(payload,results,history,unsaved,scores,includeLocality,includeEvidence)].map(r=>r.map(csvField).join(',')+'\r\n');
  return BOM+lines.join('');
}

// --- streets in a list: page address, chips, titles --------------------------------------------------------------------
export const MAX_WALK_STREETS=40;
export interface WalkPick{id:number;side:WalkSidePick;label:string;city:string}
type PickKey=Pick<WalkPick,'id'|'side'>;
const SIDE_PICKS:readonly string[]=['both','even','odd'];

export const pickName=(p:Pick<WalkPick,'id'|'label'>):string=>p.label||`Street ${p.id}`;
/** Text of each street chip: the label, plus the city when another street in the list has the same label. Labels compare without regard to case; a city shows as the server spelled it. */
export function chipNames(picks:readonly Pick<WalkPick,'id'|'label'|'city'>[]):string[]{
  const key=(p:Pick<WalkPick,'id'|'label'>)=>pickName(p).trim().toLowerCase();
  const count=new Map<string,number>();for(const p of picks)count.set(key(p),(count.get(key(p))??0)+1);
  return picks.map(p=>{const city=(p.city||'').trim();return pickName(p)+(city&&(count.get(key(p))??0)>1?` (${city})`:'');});
}

export const nextSide=(side:WalkSidePick):WalkSidePick=>side==='both'?'even':side==='even'?'odd':'both';
export const sideWords=(side:WalkSidePick):string=>side==='even'?'even side':side==='odd'?'odd side':'both sides';
/** Doors of a street on the chosen side. */
export const doorsOnSide=(street:Pick<WalkStreet,'doors'|'even'|'odd'>,side:WalkSidePick):number=>side==='even'?street.even:side==='odd'?street.odd:street.doors;

/** A street added to the list. A street already there keeps one entry; asking for the other side makes it both. Returns the same array when nothing changes. */
export function mergePick<T extends PickKey>(list:T[],pick:T):T[]{
  const at=list.findIndex(p=>p.id===pick.id);
  if(at<0)return [...list,pick];
  return list[at].side===pick.side?list:list.map((p,i)=>i===at?{...p,side:'both' as WalkSidePick}:p);
}
/** "12:even,15:both", the form the list request and the page address use. */
export const streetsParam=(picks:readonly PickKey[]):string=>picks.map(p=>`${p.id}:${p.side}`).join(',');
/** Reads that text back. Entries that are not "<id>" or "<id>:even|odd|both" are skipped, repeats merge, and at most MAX_WALK_STREETS are kept. */
export function parseStreetsParam(text:string|null|undefined):PickKey[]{
  let out:PickKey[]=[];
  for(const part of (text??'').split(',')){
    const [id,side='both']=part.trim().split(':');
    if(!/^\d{1,9}$/.test(id)||!SIDE_PICKS.includes(side))continue;
    out=mergePick(out,{id:+id,side:side as WalkSidePick});
  }
  return out.slice(0,MAX_WALK_STREETS);
}

const addressText=(v:string|null):string=>(v??'').trim().slice(0,40);
/** The voter profile key of a page address or a saved list's query: lower-case letters as the server reads them, else 'all' (everyone). */
export const profileKey=(v:string|null|undefined):string=>/^[a-z]{1,16}$/.test(v??'')?v!:'all';
/** The name of the voter profile a list was built with (its query carries `profile` only when it is not 'all'): '' for everyone. A key the district no longer offers shows as the key. */
export function profileLabel(query:Record<string,string>|undefined,profiles?:readonly {key:string;label:string}[]):string{
  const key=profileKey(query?.profile);
  return key==='all'?'':profiles?.find(x=>x.key===key)?.label??key;
}
export interface WalkAddress{area:Required<WalkArea>;picks:WalkPick[];list:string;spans?:number[];plan:boolean;fresh:boolean;trim?:WalkListTrim;profile?:string}
/** The picker settings and the saved list key the page address carries (ss, sh, precinct, streets, spans, list), and the two flags: `plan` (plan=1, from the
 *  map: plan one click in the precinct) and `fresh` (fresh=1: the list leaves out doors with a recorded result). `spans` (planned blocks, in pick order) is
 *  there only when the address has some. `trim` (doors, wp, wi: the one-click list cut to that many doors) is there only with spans and fresh, as the server wants. */
export function readWalkAddress(q:URLSearchParams):WalkAddress{
  const list=(q.get('list')??'').trim(),spans=parseSpansParam(q.get('spans')),fresh=q.get('fresh')==='1';
  const trim=spans.length&&fresh?parseTrim(q.get('doors'),q.get('wp'),q.get('wi')):null;
  const profile=profileKey(q.get('profile'));
  return {
    area:{ss:addressText(q.get('ss')),sh:addressText(q.get('sh')),precinct:addressText(q.get('precinct'))},
    picks:parseStreetsParam(q.get('streets')).map((p):WalkPick=>({...p,label:'',city:''})),
    list:/^[\w-]{1,64}$/.test(list)?list:'',
    ...spans.length?{spans}:{},
    plan:q.get('plan')==='1',
    fresh,
    ...trim?{trim}:{},
    ...profile!=='all'?{profile}:{},
  };
}
/** What a page that has just opened builds: the saved list its address names, else (plan=1 and a precinct) the one-click turf, else a list from the streets, planned blocks and area in the address (an area alone is enough, the server builds a list from one), else nothing. */
export function startPlan(a:Pick<WalkAddress,'area'|'picks'|'list'|'spans'|'plan'>):'saved'|'turf'|'build'|'none'{
  if(a.list)return 'saved';
  if(a.plan&&a.area.precinct)return 'turf';
  return a.picks.length>0||!!a.spans?.length||!!(a.area.ss||a.area.sh||a.area.precinct)?'build':'none';
}

/** Words that name a list in titles and file names: the area, else its first streets ("Cedar Heights Dr, Oak St and 2 more"). */
export function listLabel(payload:Pick<WalkListPayload,'query'|'runs'>):string{
  const area=areaText(payload.query);if(area)return area;
  const names:string[]=[];for(const r of payload.runs??[])if(r.street&&!names.includes(r.street))names.push(r.street);
  return names.length<3?names.join(' and '):`${names[0]}, ${names[1]} and ${names.length-2} more`;
}
/** The title a list gets when the walker does not type one: "Precinct 02012 · 10/2/2026". */
export function defaultListTitle(payload:Pick<WalkListPayload,'query'|'runs'|'generated_on'>):string{
  return [listLabel(payload)||'Walk list',fullDate(payload.generated_on)].filter(Boolean).join(DOT).slice(0,120);
}

// --- re-knock lists --------------------------------------------------------------------------------------------------
type ListKey=Pick<WalkListPayload,'list'|'query'>;
const queryText=(p:Pick<WalkListPayload,'query'>,key:string):string=>{const v=(p.query??{})[key];return typeof v==='string'?v:'';};
/** Key of the list a re-knock list was made from: its query is {from, reknock}. '' when the query does not say (older re-knock lists carry the parent's own query). */
export const reknockParent=(p:Pick<WalkListPayload,'query'>):string=>queryText(p,'from');
/** A re-knock list: its kind says so, or its query names the list it came from. */
export const isReknock=(p:ListKey):boolean=>p.list?.kind==='reknock'||reknockParent(p)!=='';
export interface PickerState{area:Required<WalkArea>;streets:string;spans?:string;fresh?:boolean;trim?:WalkListTrim;profile?:string}
/** The area, streets and planned blocks (`spans`, only when the list has some) a list was built from, `fresh` (only when it left out doors with a recorded result) and `trim` (doors, wp, wi: only when it was cut to a door count, which needs spans and fresh), to put back in the picker. Null when it carries none, and always for a re-knock list: its doors come from another list, whatever its query holds. */
export function pickerOf(p:ListKey):PickerState|null{
  const q=p.query??{};
  if(isReknock(p)||!['ss','sh','precinct','streets','spans'].some(k=>Object.hasOwn(q,k)))return null;
  const spans=queryText(p,'spans'),fresh=queryText(p,'fresh')==='1',trim=spans&&fresh?parseTrim(queryText(p,'doors'),queryText(p,'wp'),queryText(p,'wi')):null;
  return {area:{ss:queryText(p,'ss'),sh:queryText(p,'sh'),precinct:queryText(p,'precinct')},streets:queryText(p,'streets'),...spans?{spans}:{},...fresh?{fresh:true}:{},...trim?{trim}:{},...profileKey(queryText(p,'profile'))!=='all'?{profile:queryText(p,'profile')}:{}};
}

// --- the note after a one-click build ------------------------------------------------------------------------------------
/** What the blocks one click picked came to, in the routed list's own numbers (`totals`), then the cut to the last block (when `trim` is set), the hops between blocks and, when the precinct ran short, why. */
export function turfLines(turf:Pick<WalkTurf,'spans'|'jumps'|'stopped'|'trim'>,totals:Pick<WalkTotals,'doors'|'voters'|'walk_m'>,jumpM=400):string[]{
  const lines=[`From ${countText(turf.spans.length,'block')} with the most untapped potential in reach: ${countText(totals.doors,'door')}, ${countText(totals.voters,'person','people')}, ${distanceText(totals.walk_m)} of walking.`];
  if(turf.trim)lines.push(`Took the ${countText(turf.trim.kept,'door')} with the most untapped people from the last block (of its ${turf.trim.of}).`);
  if(turf.jumps.length)lines.push(`${countText(turf.jumps.length,'hop')} of up to ${jumpM} m between blocks.`);
  if(turf.stopped==='far'||turf.stopped==='exhausted')lines.push(`Only ${countText(totals.doors,'door')} without a recorded result ${totals.doors===1?'is':'are'} in reach of these blocks.`);
  else if(turf.stopped==='cap')lines.push(`Stopped before the list got too large to build in one list (${COUNT.format(MAX_LIST_PEOPLE)} people or ${MAX_PLAN_SPANS} blocks).`);
  return lines;
}
/**
 * The line above a saved list: "Saved list · Saturday · 10/2/2026". A re-knock list names the list it came from, by title
 * when that list is among `lists` and by key otherwise: "Not home again · from Saturday".
 */
export function savedLine(p:ListKey,lists:readonly Pick<WalkSavedList,'key'|'title'>[]=[]):string{
  const parent=reknockParent(p);
  const head=parent
    ?[`Not home again${DOT}from ${lists.find(l=>l.key===parent)?.title||parent}`]
    :[p.list.kind==='reknock'?'Re-knock list':'Saved list',p.list.title];
  const when=fullDate(p.list.created_at??'');
  if(when&&!head.join(DOT).includes(when))head.push(when);
  return head.filter(Boolean).join(DOT);
}

/** "2014–2024", or one year when the list holds a single election year. */
export function yearRange(list:readonly string[]|undefined):string{
  const ys=(list??[]).map(s=>/\d{4}/.exec(s)?.[0]).filter((s):s is string=>!!s).map(Number).sort((a,b)=>a-b);
  return !ys.length?'':ys[0]===ys[ys.length-1]?String(ys[0]):`${ys[0]}–${ys[ys.length-1]}`;
}
/** What Pri, Gen and Abs count, from the list's history window. */
export function historyKey(win:WalkWindow|undefined):string{
  const pri=yearRange(win?.primaries),gen=yearRange(win?.generals);
  const years=pri||gen?` (${[pri&&`primaries ${pri}`,gen&&`generals ${gen}`].filter(Boolean).join(', ')})`:'';
  return `Pri and Gen: elections voted out of elections eligible${years}. A lone slash means none were eligible. ${win?.abs_rule||'Abs = mail ballots'}: ballots cast by mail or absentee, out of all eligible primaries and generals.`;
}

// --- map shading ----------------------------------------------------------------------------------------------------------
/** Lower edges of the 2nd..nth shade for door counts: up to bins-1 ascending numbers that give each shade about the same number of blocks. */
export function doorBreaks(values:readonly number[],bins=5):number[]{
  const v=values.filter(n=>Number.isFinite(n)&&n>0).sort((a,b)=>a-b);
  const out:number[]=[];
  for(let k=1;k<bins;k++){
    const edge=v[Math.floor(k*v.length/bins)];
    if(edge!==undefined&&edge>v[0]&&(!out.length||edge>out[out.length-1]))out.push(edge);
  }
  return out;
}
/** The party lean of a block from its voters per party code: the opinions.MARGIN_LABELS step (0..4) of its D share minus its R share,
 *  every voter (U and other codes too) counted in the shares; null when the block has no voters. */
export function blockLean(c:Record<string,number>):number|null{
  const total=Object.values(c).reduce((sum,n)=>sum+(n>0?n:0),0);
  return total>0?marginBin((c.D??0)/total,(c.R??0)/total):null;
}
/** A party code for the map key: "W Working Families" when the district knows the code, "Code T" when it does not. */
export const partyName=(code:string,names:Record<string,string>):string=>!code?'No code':names[code]?`${code} ${names[code]}`:`Code ${code}`;

// --- CSV in ----------------------------------------------------------------------------------------------------------
export const MAX_UPLOAD_ROWS=500;
const MAX_UPLOAD_CHARS=1_000_000;
type Field='street'|'side'|'city'|'zip';
const FIELDS:readonly Field[]=['street','side','city','zip'];
/** Most characters the server takes in each field of an uploaded row (match_row in walk_views.py); it turns the whole request down when one cell is longer. */
export const MATCH_LIMITS:Readonly<Record<Field,number>>={street:120,side:40,city:60,zip:20};
const FIELD_WORDS:Record<Field,string>={street:'street name',side:'side',city:'city',zip:'ZIP code'};
const HEADERS:Record<Field,readonly string[]>={
  street:['street','streets','street name','street names','streetname','road','road name'],
  side:['side','side of street','parity','even odd','odd even','even or odd','e o','o e','eo','oe'],
  city:['city','town','municipality'],
  zip:['zip','zip code','zipcode','postal code','postcode'],
};
const SIDES:Record<string,WalkSidePick>={
  e:'even',ev:'even',even:'even',evens:'even','even side':'even','even numbers':'even',
  o:'odd',od:'odd',odd:'odd',odds:'odd','odd side':'odd','odd numbers':'odd',
  b:'both',both:'both',all:'both',any:'both','both sides':'both','all sides':'both',
};
const norm=(s:string):string=>s.toLowerCase().replace(/[^a-z0-9]+/g,' ').trim();
const squash=(s:string):string=>s.replace(/[\u0000-\u001f\u007f]+/g,' ').replace(/\s+/g,' ').trim();

/** Side words a spreadsheet might hold ("Evens", "E", "odd side", "all") as even, odd or both; null when unrecognised. */
export function sideFromText(text:string):WalkSidePick|null{const k=norm(text);return Object.hasOwn(SIDES,k)?SIDES[k]:null;}

function pickDelimiter(line:string):string{
  const count:Record<string,number>={',':0,';':0,'\t':0};let quoted=false;
  for(const ch of line){if(ch==='"')quoted=!quoted;else if(!quoted&&ch in count)count[ch]++;}
  return [';','\t'].reduce((lead,d)=>count[d]>count[lead]?d:lead,',');
}

/** The rows of a CSV text, and each one's number in the file as a spreadsheet counts it (the first row is 1; blank rows are dropped but still counted). */
function scanCsv(text:string):{rows:string[][];numbers:number[]}{
  const src=text.charCodeAt(0)===0xfeff?text.slice(1):text;
  const delim=pickDelimiter(src.split(/\r\n|\r|\n/).find(l=>l.trim()!=='')??'');
  const rows:string[][]=[];const numbers:number[]=[];let row:string[]=[];let cell='';let quoted=false;let seen=0;
  const endRow=()=>{row.push(cell);cell='';seen++;if(row.some(c=>c.trim()!=='')){rows.push(row);numbers.push(seen);}row=[];};
  for(let i=0;i<src.length;i++){
    const ch=src[i];
    if(quoted){
      if(ch!=='"')cell+=ch;
      else if(src[i+1]==='"'){cell+='"';i++;}
      else quoted=false;
    }else if(ch==='"'&&cell.trim()===''){quoted=true;cell='';}
    else if(ch===delim){row.push(cell);cell='';}
    else if(ch==='\r'||ch==='\n'){if(ch==='\r'&&src[i+1]==='\n')i++;endRow();}
    else cell+=ch;
  }
  if(cell!==''||row.length)endRow();
  return {rows,numbers};
}
/** RFC 4180 reader: quoted fields, doubled quotes, embedded delimiters and line breaks, any line ending, a leading BOM. Blank lines are dropped. */
export function splitCsv(text:string):string[][]{return scanCsv(text).rows;}

/** A row of an uploaded file that cannot be sent; `row` is its number as a spreadsheet shows it (the header row is 1). */
export interface WalkCsvProblem{row:number;message:string}
/** `warning` is set when the file is a public-estimate export: it names streets, not the area they were chosen in. */
export interface WalkCsvRead{rows:WalkMatchInput[];problems:WalkCsvProblem[];warning?:string}
const chars=(s:string):number=>[...s].length;   // code points, as the server counts them
/** "street name is too long", "street name and city are too long" */
const tooLong=(fields:readonly Field[]):string=>{
  const words=fields.map(f=>FIELD_WORDS[f]);const last=words.pop();
  return words.length?`${words.join(', ')} and ${last} are too long`:`${last} is too long`;
};

/**
 * Street rows from an uploaded CSV, ready to send, and the rows left out. A first row with recognised column names (street is
 * required; side, city and zip are optional) is the header; without one the columns are street, side, city, zip in that order.
 * Cells are trimmed. Rows with no street are skipped. A row with a value longer than the server takes (MATCH_LIMITS, counted the
 * way the server counts, on the value as it would be sent) is left out with a problem such as "Row 7: street name is too long",
 * and the other rows go on. Throws a plain-language Error when a header has no street column, more than MAX_UPLOAD_ROWS rows
 * could be sent, or the file is over 1 MB. A public-estimate export (header row exactly publicStreetGroups' HEADER) or a voter-file export (exactly audienceGroups' VOTER_HEADER) is read one group at a time:
 * several groups, or any row that was not grouped, throws; its street names lose the formula guard apostrophe and `warning` is set.
 */
export function readWalkCsv(text:string):WalkCsvRead{
  if(text.length>MAX_UPLOAD_CHARS)throw new Error('That file is larger than 1 MB.');
  const {rows,numbers}=scanCsv(text);
  const out:WalkCsvRead={rows:[],problems:[]};
  if(!rows.length)return out;
  const found:Partial<Record<Field,number>>={};
  rows[0].forEach((cell,i)=>{const n=norm(cell);for(const f of FIELDS)if(found[f]===undefined&&HEADERS[f].includes(n))found[f]=i;});
  const hasHeader=Object.keys(found).length>0;
  if(hasHeader&&found.street===undefined)throw new Error('Add a column named street.');
  const at:Partial<Record<Field,number>>=hasHeader?found:{street:0,side:1,city:2,zip:3};
  const heads=rows[0].map(norm),statusAt=heads.indexOf('status'),groupAt=heads.indexOf('group');
  const same=(h:readonly string[])=>heads.length===h.length&&h.every((x,i)=>rows[0][i].trim()===x);
  const kind=same(HEADER)?'public-estimate':same(VOTER_HEADER)?'voter-file':null,exported=kind!==null;
  if(kind){
    const body=rows.slice(1);
    if(new Set(body.map(r=>(r[groupAt]??'').trim()).filter(Boolean)).size>1)throw new Error(`This ${kind==='voter-file'?'voter-file export':'public-estimate file'} has several groups. Import one group file at a time.`);
    if(body.some(r=>(r[statusAt]??'').trim().toLowerCase()!=='grouped'))throw new Error("Streets that were not grouped can't be imported as a walk list.");
    out.warning=kind==='voter-file'?VOTER_SCOPE_WARNING:PUBLIC_SCOPE_WARNING;
  }
  for(let n=hasHeader?1:0;n<rows.length;n++){
    const r=rows[n];
    const get=(f:Field):string=>at[f]===undefined?'':squash(exported&&f==='street'?unguard(r[at[f] as number]??''):r[at[f] as number]??'');
    const street=get('street');if(!street)continue;
    const item:WalkMatchInput={street};
    const side=get('side');if(side)item.side=sideFromText(side)??side;
    const city=get('city');if(city)item.city=city;
    const zip=get('zip');if(zip)item.zip=zip;
    const long=FIELDS.filter(f=>chars(item[f]??'')>MATCH_LIMITS[f]);
    if(long.length){out.problems.push({row:numbers[n],message:`Row ${numbers[n]}: ${tooLong(long)}`});continue;}
    if(out.rows.length>=MAX_UPLOAD_ROWS)throw new Error(`A street list can have up to ${MAX_UPLOAD_ROWS} rows.`);
    out.rows.push(item);
  }
  return out;
}
/** The rows of readWalkCsv that can be sent; use readWalkCsv to learn which rows were left out and why. */
export function parseWalkCsv(text:string):WalkMatchInput[]{return readWalkCsv(text).rows;}

/** What to tell the walker about rows left out of an upload: a count, the first few reasons, and how many more there are. Null when none were. */
export function leftOutNote(problems:readonly WalkCsvProblem[],show=5):{headline:string;reasons:string[]}|null{
  if(!problems.length)return null;
  const more=problems.length-show;
  return {
    headline:`${countText(problems.length,'row')} left out of this upload:`,
    reasons:[...problems.slice(0,show).map(p=>p.message),...(more>0?[`and ${COUNT.format(more)} more`]:[])],
  };
}

/** `patch` applied to row `i` of the upload numbered `id`. Any other upload, or none, comes back as it was, so an answer that arrives after its upload was replaced or cleared changes nothing. */
export function patchUploadRow<U extends {id:number;rows:object[]}>(upload:U|null,id:number,i:number,patch:Partial<U['rows'][number]>):U|null{
  if(!upload||upload.id!==id)return upload;
  return {...upload,rows:upload.rows.map((r,k)=>k===i?{...r,...patch}:r)};
}
