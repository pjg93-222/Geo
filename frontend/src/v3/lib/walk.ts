// Walk list text rules: tallies, dates, phones, run headers, totals, result codes and the CSV in and out.
// Pure, and only `import type` from other files, so node --test can load it directly (same constraint as format.ts).
import type {WalkArea,WalkCode,WalkListPayload,WalkMatchInput,WalkRun,WalkSavedList,WalkSidePick,WalkStreet,WalkTally,WalkTotals,WalkVoter,WalkWindow} from '../api/walkTypes';

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
/** "4 streets · 6 stops · 58 doors · 104 voters · 2.9 km". The list carries a `minutes` figure from the server; no walk-list output shows it. */
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
export function walkFileName(area:string,isoDate:string,ext:'pdf'|'csv'):string{
  const slug=area.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-+|-+$/g,'').slice(0,60).replace(/-+$/,'')||'list';
  return `walk-list-${slug}-${ymd(isoDate)?isoDate.slice(0,10):localToday()}.${ext}`;
}

// --- CSV out ---------------------------------------------------------------------------------------------------------
export const CSV_COLUMNS=['Stop','Precinct','Street','Side','Address','Unit','Name','Phone','Pri','Gen','Abs','Party','Age','Gender','Reg. Date','Result'] as const;

// A formula character counts after leading spaces, control characters and invisible marks too.
const FORMULA=/^(?:[\t\r\n]|[\s\u0000-\u001f\u007f-\u009f\u{200b}-\u{200f}\u{2060}]*[=+\-@])/u;
/** Keeps a value from turning into a formula when the file opens in a spreadsheet (csv_value in terrier_contacts.py, plus leading spaces). */
export function csvValue(value:unknown):string{
  const text=value==null?'':String(value);
  return FORMULA.test(text)?`'${text}`:text;
}
const csvField=(value:unknown):string=>`"${csvValue(value).replace(/[\r\n]+/g,' ').replace(/"/g,'""')}"`;
// A spreadsheet reads "4/6" as a date, so the file says "4 of 6"; the registration date stays ISO for the same reason.
const csvTally=(t:WalkTally|null|undefined):string=>t?`${t.voted} of ${t.eligible}`:'';

/** Result codes by person id as the screen holds them; null is a cleared result. */
export type WalkResults=Record<string,WalkCode|null>;
/** The results already on file for a list: each voter's latest code. */
export function initialResults(payload:WalkListPayload):WalkResults{
  const out:WalkResults={};
  for(const run of payload.runs??[])for(const door of run.doors)for(const v of door.voters)if(v.last?.code)out[v.person_id]=v.last.code;
  return out;
}
/** Most results one request carries (MAX_ENTRIES in walk_views.py). */
export const MAX_RESULTS_PER_REQUEST=200;
/** Takes up to `max` waiting results out of `pending`, oldest first, for one request. */
export function takeResults<K,V>(pending:Map<K,V>,max=MAX_RESULTS_PER_REQUEST):Map<K,V>{
  const batch=new Map<K,V>();
  for(const [k,v] of pending){if(batch.size>=max)break;batch.set(k,v);}
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
  /** Forgets the waiting results (another list is on screen); requests already sent count until they are answered. */
  clear():void;
}
export function resultOutbox<K,V>():ResultOutbox<K,V>{
  const waiting=new Map<K,V>();const sent=new Set<Map<K,V>>();
  return {
    waiting,
    set(key,value){waiting.set(key,value);},
    take(max=MAX_RESULTS_PER_REQUEST){const batch=takeResults(waiting,max);if(batch.size)sent.add(batch);return batch;},
    confirm(batch){sent.delete(batch);},
    restore(batch){sent.delete(batch);for(const [k,v] of batch)if(!waiting.has(k))waiting.set(k,v);},
    unconfirmed:()=>waiting.size>0||sent.size>0,
    clear(){waiting.clear();},
  };
}
/**
 * Sends the waiting results, one request at a time and at most MAX_RESULTS_PER_REQUEST each, until none are left.
 * False when a request fails: its results go back to waiting and the failure is reported.
 */
export async function drainResults<K,V>(outbox:ResultOutbox<K,V>,io:{
  ready():boolean;                                    // false while no list is on screen
  send(batch:Map<K,V>):Promise<unknown>;              // saves the list if it is not saved yet, then posts the batch
  report(state:SaveState,message?:string):void;
}):Promise<boolean>{
  while(io.ready()&&outbox.waiting.size){
    const batch=outbox.take();
    io.report('saving');
    try{await io.send(batch);}
    catch(e){
      outbox.restore(batch);
      io.report('error',e instanceof Error&&e.message?e.message:'The results could not be saved.');
      return false;
    }
    outbox.confirm(batch);
    io.report(outbox.waiting.size?'pending':'saved');
  }
  return true;
}
// A code set on screen wins over the one on file; a cleared one leaves the cell empty.
const resultCode=(v:WalkVoter,results?:WalkResults):string=>results&&Object.hasOwn(results,v.person_id)?results[v.person_id]??'':v.last?.code??'';

/** One row per voter in walking order. Result holds the code on file, or the one in `results` when the walker has set it. */
export function walkCsvRows(payload:WalkListPayload,results?:WalkResults):string[][]{
  const rows:string[][]=[];
  for(const run of payload.runs??[])for(const door of run.doors)for(const v of door.voters)rows.push([
    String(run.stop),run.precinct,run.street,sideLabel(run.side),door.house,door.unit,v.name,phoneText(v.phone),
    csvTally(v.pri),csvTally(v.gen),csvTally(v.abs),v.party,v.age==null?'':String(v.age),v.gender,v.reg_date,resultCode(v,results),
  ]);
  return rows;
}
const BOM=String.fromCharCode(0xfeff);
/** UTF-8 BOM, CRLF rows, every field quoted. */
export function buildWalkCsv(payload:WalkListPayload,results?:WalkResults):string{
  const lines=[[...CSV_COLUMNS] as string[],...walkCsvRows(payload,results)].map(r=>r.map(csvField).join(',')+'\r\n');
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
export interface WalkAddress{area:Required<WalkArea>;picks:WalkPick[];list:string}
/** The picker settings and the saved list key the page address carries (ss, sh, precinct, streets, list). */
export function readWalkAddress(q:URLSearchParams):WalkAddress{
  const list=(q.get('list')??'').trim();
  return {
    area:{ss:addressText(q.get('ss')),sh:addressText(q.get('sh')),precinct:addressText(q.get('precinct'))},
    picks:parseStreetsParam(q.get('streets')).map((p):WalkPick=>({...p,label:'',city:''})),
    list:/^[\w-]{1,64}$/.test(list)?list:'',
  };
}
/** What a page that has just opened builds: the saved list its address names, else a list from the streets and area in the address (an area alone is enough, the server builds a list from one), else nothing. */
export function startPlan(a:Pick<WalkAddress,'area'|'picks'|'list'>):'saved'|'build'|'none'{
  if(a.list)return 'saved';
  return a.picks.length>0||!!(a.area.ss||a.area.sh||a.area.precinct)?'build':'none';
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
export interface PickerState{area:Required<WalkArea>;streets:string}
/** The area and streets a list was built from, to put back in the picker. Null when it carries none, and always for a re-knock list: its doors come from another list, whatever its query holds. */
export function pickerOf(p:ListKey):PickerState|null{
  const q=p.query??{};
  if(isReknock(p)||!['ss','sh','precinct','streets'].some(k=>Object.hasOwn(q,k)))return null;
  return {area:{ss:queryText(p,'ss'),sh:queryText(p,'sh'),precinct:queryText(p,'precinct')},streets:queryText(p,'streets')};
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
export type PartyKey='d'|'r'|'u'|'o';
/** The registration group with the most voters on a block; a tie is "mixed" and no voters is "none". */
export function majorParty(c:Record<PartyKey,number>):PartyKey|'mixed'|'none'{
  const top=Math.max(c.d,c.r,c.u,c.o);if(!(top>0))return 'none';
  const lead=(['d','r','u','o'] as const).filter(k=>c[k]===top);
  return lead.length===1?lead[0]:'mixed';
}

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
export interface WalkCsvRead{rows:WalkMatchInput[];problems:WalkCsvProblem[]}
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
 * could be sent, or the file is over 1 MB.
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
  for(let n=hasHeader?1:0;n<rows.length;n++){
    const r=rows[n];
    const get=(f:Field):string=>at[f]===undefined?'':squash(r[at[f] as number]??'');
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
