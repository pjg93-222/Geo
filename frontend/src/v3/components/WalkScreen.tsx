// Walk list: pick an area and streets, build the route, work through it door by door, record results (autosaved), export.
// The page address mirrors the picker (ss, sh, precinct, streets) and the open saved list (list), so a reload rebuilds the same list.
import {Component,Fragment,Suspense,lazy,memo,useCallback,useEffect,useMemo,useRef,useState} from 'react';
import type {ReactNode} from 'react';
import {Check,ChevronDown,Route as RouteIcon,Search,Upload,X} from 'lucide-react';
import {PageHeader,useFeatures} from './Header';
import {StreetPlan} from './StreetPlan';
import {WalkAddressSheets} from './WalkAddressSheets';
import {WalkOptIns,type RecordWalkOptIn} from './WalkOptIns';
import {useStartupReady} from './StartupFrame';
import {Segmented,cx} from './ui';
import type {WalkShade} from './WalkMap';
import {replaceQuery,type Route} from '../lib/router';
import type {Meta} from '../types';
import {downloadFile} from '../lib/download';
import {ApiError,getSession} from '../api/client';
import {getWalkList,getWalkLists,getWalkOptions,getWalkPlan,getWalkSavedList,getWalkScores,matchWalkStreets,reknockWalkList,saveWalkDispositions,saveWalkList,saveWalkOptIns,searchWalkStreets} from '../api/walk';
import type {WalkArea,WalkCode,WalkDoor,WalkLast,WalkLevel,WalkListMeta,WalkListPayload,WalkListTrim,WalkMatchInput,WalkMatchRow,WalkOption,WalkRun,WalkSavedList,WalkStreet,WalkTurf,WalkVoter} from '../api/walkTypes';
import {
  CODE_LABEL,MAX_WALK_STREETS,WALK_CODES,chipNames,codeLegend,confirmedClears,countText,defaultListTitle,doorAddress,doorsOnSide,dropCleared,flushList,fullDate,historyKey,historyOf,
  lastLabel,lastOf,lastText,latestJob,leftOutNote,listLabel,localToday,markAfterClick,mergePick,nextSide,parseStreetsParam,patchUploadRow,phoneText,pickName,pickerOf,readWalkAddress,readWalkCsv,
  doorsByPerson,flaggedOnList,listIdentity,marksWithRefused,refusalBarText,refusalHeadline,refusalOf,unsavedText,regDate,resultShelf,runHeader,savedLine,sideLabel,sideWords,startPlan,streetsParam,tallyText,totalsLine,visitRequest,walkFileName,watermarkText,
  LIST_REPLACED,OFF_LIST,adoptSaved,missingText,rekeyed,seenBy,splitOffRoster,turfLines,
  type HeldList,type RefusalNote,type SaveState,type WalkCsvProblem,type WalkCsvRead,type WalkHistory,type WalkPick,type WalkResults,type WalkScoreMap,type WalkVisit,
} from '../lib/walk';
import {DOORS_MAX,DOORS_MIN,clampDoors,idChunks,parseSpansParam,potentialOf,raceWords,readDoors,spansParam,trimFor,trimQuery,turfOf,writeDoors} from '../lib/plan';
import {readWeights,weightsQuery,writeWeights} from '../lib/untapped';
import type {UntappedWeights} from '../lib/untapped';
import {DOOR_SCRIPT,choiceText,fillScript} from '../lib/walkScript';
import {exportWalkPdf} from '../lib/walkPdf';
import {buildWalkSheetsCsv,doorCap,splitWalkSheets} from '../lib/walkSheets';
import {refusedStore} from '../lib/walkRefused';
import {onRefusedReset} from '../lib/walkRefusedReset';
import {hasRecordedOptIn,optInEntry,optInWriteScope,patchRecordedOptIn,recordedOptIns} from '../lib/walkOptIns';
import '../styles/walk.css';

// MapLibre loads only once a list exists, so opening this screen stays light.
const WalkMap=lazy(()=>import('./WalkMap').then(m=>({default:m.WalkMap})));

type Area=Required<WalkArea>;
const LEVELS:{level:WalkLevel;label:string;aria:string}[]=[
  {level:'ss',label:'Senate',aria:'Senate district'},{level:'sh',label:'House',aria:'House district'},{level:'precinct',label:'Precinct',aria:'Precinct'},
];
const AUTOSAVE_MS=600;
const COLUMNS=['Address','Name','Phone','Pri','Gen','Abs','Party','Age','Gender','Reg. date','Result'];
const noArea=(a:Area)=>!a.ss&&!a.sh&&!a.precinct;
const NO_TURF='Plan walk list is not available from this server yet.';
const NO_DOORS='No doors without a recorded result are in reach in this precinct.';

// --- results the server has not confirmed --------------------------------------------------------------------------------------
// One entry per list the walker has marked results on in this page load. The shelf belongs to the page, not to the screen: leaving the
// walk list while a save is failing keeps the results here, coming back sends them again, and closing the page asks first.
const outbox=resultShelf<string,WalkVisit>(v=>v.on);
// Results the server refused outlive the screen as well: they stay the walker's marks (flagged Not saved, never erased) for each list, and the
// note names their doors until it is dismissed, so a refusal while the screen is away is told on return. Both outlive a reload too: they are
// restored from this tab's sessionStorage when this module loads, and every change is written back (lib/walkRefused.ts).
const refusals=refusedStore();
// The voters of the list on screen (the last one shown, once the screen is away) whose mark is flagged Not saved (flaggedOnList). Kept here,
// beside the guards, as a ref the screen updates.
const shownFlagged:{current:ReadonlySet<string>}={current:new Set()};
// The guard while the screen is away: results not yet confirmed, a refusal note not yet dismissed, or a flagged mark on the list shown. A
// refused mark on a voter the list does not show (off the list, gone) does not ask: it is never cleared, so it would ask on every close.
const warnHeld=(e:BeforeUnloadEvent)=>{if(outbox.unconfirmed()||refusals.note||shownFlagged.current.size>0){e.preventDefault();e.returnValue='';}};
const identities=(entry:HeldList)=>[...new Set([listIdentity(entry.meta?.key,entry.query),listIdentity(null,entry.query)])];
/** Results the server refused: kept as the walker's marks on their list, and added to the note with their doors. Answers with the note. */
function holdRefused(entry:HeldList,marks:Map<string,WalkVisit>,why:string,local=false):RefusalNote{
  return refusals.hold(identities(entry)[0],entry.meta?.title||entry.title,marks,entry.where,why,local);
}
const OUT_OF_DATE='This page is out of date. Reload it, then save the list again.';   // the server's words for a save without a fingerprint
/** Saves the list an entry belongs to, once. The answer stays on the entry, so a second ask (or a screen that comes later) shares it. */
function saveEntry(entry:HeldList,title?:string):Promise<WalkListMeta>{
  if(entry.meta?.key)return Promise.resolve(entry.meta);
  if(entry.saving)return entry.saving;
  if(title?.trim())entry.title=title.trim();
  // Every list the server builds carries a fingerprint and it refuses a save without one; a list shown without one is answered the same way here
  // (a refusal of the whole list, so the marks stay flagged), and nothing is sent.
  const {fingerprint}=entry;
  const job:Promise<WalkListMeta>=(fingerprint?saveWalkList(entry.title,entry.query,fingerprint):Promise.reject(new ApiError(OUT_OF_DATE,400,true))).then(saved=>{
    if(!saved.list?.key)throw new Error('The list could not be saved.');
    const list=adoptSaved(entry,saved);   // when the voter data changed since the list was shown, the list saved is the new one: the entry keeps it
    // and the marks refused before the save follow the list to its key, a voter whose id changed keeping a refused mark (the tab's storage
    // is rewritten): a reload, which opens the list by its key, shows them
    refusals.adopt(listIdentity(null,entry.query),listIdentity(list.key,entry.query),entry.moved);
    return list;
  }).finally(()=>{if(entry.saving===job)entry.saving=null;});
  return entry.saving=job;
}
// A request the walk API refuses in its own words fails the same way every time (a voter no longer on the list, a list that is gone, a list it
// will not save): those results are refused and not sent again, so they cannot hold up the screen. Anything else (offline, an HTML page, a
// conflict while the store is swapped, a server fault) is a failure to retry.
const refusing=<T,>(job:Promise<T>,step:'list'|'results'):Promise<T>=>job.catch((e:unknown)=>{throw refusalOf(e,step)??e;});
/** Posts one request of an entry's results (one day of visits), saving the list first when it is not saved yet. Answers with the list's key and
 *  the results saved, by the person ids of the saved list. When the list saved was built again from changed voter data, a voter whose id changed
 *  is sent under the new id; results for voters it does not have are refused here (`offList`) and never sent. */
async function sendBatch(entry:HeldList,visits:Map<string,WalkVisit>,offList:(refused:Map<string,WalkVisit>,why:string)=>void,
  save:()=>Promise<string>=()=>saveEntry(entry).then(m=>String(m.key))):Promise<{list:string;sent:Map<string,WalkVisit>}>{
  const list=await refusing(save(),'list');
  const {send:batch,off}=splitOffRoster(entry.roster,visits,entry.moved);
  if(off.size)offList(off,OFF_LIST);
  if(batch.size){const {on,entries}=visitRequest(batch);await refusing(saveWalkDispositions(list,on,entries),'results');}
  refusals.clear(identities(entry),batch.keys());   // marked again and saved: no longer refused
  return {list,sent:batch};
}

// --- area: Senate, House and Precinct, each optional; a parent change resets what lies below it ------------------------------
function useOptions(level:WalkLevel,area:Area){
  const [state,setState]=useState<{options:WalkOption[];error:boolean;loading:boolean}>({options:[],error:false,loading:true});
  const [attempt,setAttempt]=useState(0);
  const parent=level==='ss'?'':level==='sh'?area.ss:`${area.ss}|${area.sh}`;
  useEffect(()=>{
    const ac=new AbortController();
    setState(s=>({...s,loading:true,error:false}));
    getWalkOptions(level,area,ac.signal).then(p=>setState({options:p.options,error:false,loading:false})).catch(()=>{if(!ac.signal.aborted)setState({options:[],error:true,loading:false});});
    return ()=>ac.abort();
  },[level,parent,attempt]);
  return {...state,retry:()=>setAttempt(n=>n+1)};
}
function AreaField(p:{level:WalkLevel;label:string;aria:string;value:string;area:Area;onChange:(v:string)=>void}){
  const {options,loading,error,retry}=useOptions(p.level,p.area);
  const off=loading||error;
  const shown=loading?'Loading…':error?'Unavailable':p.value?options.find(o=>o.id===p.value)?.label??p.value:'All';
  return <div className="walk-field">
    <label className={cx('select','walk-select',off&&'is-off')}>
      <span className="select-text" aria-hidden="true"><span className="walk-k">{p.label}:</span> {shown}</span>
      <ChevronDown className="select-chev" strokeWidth={1.75} aria-hidden="true"/>
      <select aria-label={p.aria} value={p.value} disabled={off} onChange={e=>p.onChange(e.target.value)}>
        <option value="">All</option>
        {p.value&&!options.some(o=>o.id===p.value)&&<option value={p.value}>{p.value}</option>}
        {options.map(o=><option key={o.id} value={o.id}>{`${o.label} · ${countText(o.doors,'door')}`}</option>)}
      </select>
    </label>
    {error&&<p className="walk-field-error" role="alert">{p.label} choices could not be loaded. <button type="button" className="walk-link" onClick={retry}>Retry</button></p>}
  </div>;
}

// --- streets: search combobox ----------------------------------------------------------------------------------------------
function StreetSearch({area,onAdd}:{area:Area;onAdd:(s:WalkStreet)=>void}){
  const [q,setQ]=useState('');const [open,setOpen]=useState(false);const [active,setActive]=useState(0);
  const [hits,setHits]=useState<WalkStreet[]>([]);const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  const box=useRef<HTMLDivElement>(null);const input=useRef<HTMLInputElement>(null);
  const text=q.trim();const areaKey=`${area.ss}|${area.sh}|${area.precinct}`;
  useEffect(()=>{
    setHits([]);setActive(0);setError('');
    if(text.length<2){setBusy(false);return;}
    setBusy(true);
    const ac=new AbortController();
    const t=setTimeout(()=>{
      searchWalkStreets(text,area,ac.signal).then(r=>{setBusy(false);setHits(r.streets.slice(0,30));}).catch(()=>{if(!ac.signal.aborted){setBusy(false);setError('Search unavailable. Try again.');}});
    },160);
    return ()=>{clearTimeout(t);ac.abort();};
  },[text,areaKey]);
  useEffect(()=>{
    const close=(e:MouseEvent)=>{if(box.current&&!box.current.contains(e.target as Node))setOpen(false);};
    document.addEventListener('mousedown',close);return ()=>document.removeEventListener('mousedown',close);
  },[]);
  const show=open&&text.length>=2;const listed=show&&hits.length>0;
  useEffect(()=>{if(listed)document.getElementById(`walk-street-hit-${active}`)?.scrollIntoView({block:'nearest'});},[active,listed]);
  function pick(s:WalkStreet){onAdd(s);setQ('');setOpen(false);input.current?.focus();}
  return <div className="walk-find" ref={box}>
    <Search size={16} strokeWidth={1.75} aria-hidden="true"/>
    <input ref={input} type="search" role="combobox" aria-autocomplete="list" aria-expanded={listed} aria-controls={listed?'walk-street-results':undefined}
      aria-activedescendant={listed&&hits[active]?`walk-street-hit-${active}`:undefined} aria-label="Add a street" placeholder="Add a street" autoComplete="off" spellCheck={false} value={q}
      onFocus={()=>setOpen(true)} onChange={e=>{setQ(e.target.value);setOpen(true);}}
      onKeyDown={e=>{
        if(e.key==='Escape')setOpen(false);
        else if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();setOpen(true);setActive(i=>Math.max(0,Math.min(hits.length-1,i+(e.key==='ArrowDown'?1:-1))));}
        else if(e.key==='Enter'&&listed&&hits[active]){e.preventDefault();pick(hits[active]);}
      }}/>
    {open&&text.length===1&&<div className="walk-find-pop walk-find-empty" role="status">Type at least 2 letters</div>}
    {show&&!hits.length&&<div className="walk-find-pop walk-find-empty" role="status">{busy?'Searching…':error||'No match'}</div>}
    {listed&&<div id="walk-street-results" className="walk-find-pop" role="listbox" aria-label="Streets">
      {hits.map((s,i)=><button key={s.id} id={`walk-street-hit-${i}`} type="button" role="option" aria-selected={i===active} onMouseEnter={()=>setActive(i)} onMouseDown={e=>e.preventDefault()} onClick={()=>pick(s)}>
        <span className="walk-hit-name">{s.label}</span><span className="walk-hit-sub">{[s.city,countText(s.doors,'door')].filter(Boolean).join(' · ')}</span>
      </button>)}
    </div>}
  </div>;
}

// --- streets: the chips, and the uploaded list's match report -----------------------------------------------------------------
function Chips(p:{picks:WalkPick[];planned:number;onSide:(id:number)=>void;onRemove:(id:number)=>void;onUnplan:()=>void}){
  const names=chipNames(p.picks);
  if(!p.picks.length&&!p.planned)return <p className="walk-hint">No streets yet. Search above or upload a CSV.</p>;
  return <>
    <ul className="walk-chips" aria-label="Streets in this walk list">
      {p.picks.map((s,i)=>{
        const name=names[i];
        return <li key={s.id} className="walk-chip">
          <button type="button" className="walk-chip-main" onClick={()=>p.onSide(s.id)} aria-label={`${name}, ${sideWords(s.side)}. Change side`} title="Change side">
            {name}{s.side!=='both'&&<span className="walk-chip-side"> · {s.side}</span>}
          </button>
          <button type="button" className="walk-chip-x" onClick={()=>p.onRemove(s.id)} aria-label={`Remove ${name}`}><X size={12} strokeWidth={2} aria-hidden="true"/></button>
        </li>;
      })}
      {p.planned>0&&<li className="walk-chip">
        <span className="walk-chip-main">{countText(p.planned,'planned block')}</span>
        <button type="button" className="walk-chip-x" onClick={p.onUnplan} aria-label="Remove planned blocks"><X size={12} strokeWidth={2} aria-hidden="true"/></button>
      </li>}
    </ul>
    {p.picks.length>0&&<p className="walk-hint">Select a street to switch between both sides, even and odd.</p>}
  </>;
}

interface UploadRow{input:WalkMatchInput;row:WalkMatchRow;chosen:number|null;skipped:boolean;busy:boolean}
// Each file on screen has its own id: an answer is applied only while the upload it was asked about is still the one shown.
interface Upload{id:number;name:string;count:number;rows:UploadRow[]}
type RowState='ok'|'pick'|'left'|'none'|'busy';
const rowLive=(r:UploadRow,picks:WalkPick[])=>r.chosen!=null&&picks.some(p=>p.id===r.chosen);
function rowState(r:UploadRow,picks:WalkPick[]):RowState{
  if(r.busy)return 'busy';
  if(r.row.status==='none')return 'none';
  if(r.row.status==='matched'||r.chosen!=null)return rowLive(r,picks)?'ok':'left';
  return r.skipped?'left':'pick';
}
const rowName=(r:UploadRow)=>`${r.row.input||r.input.street}${r.row.side!=='both'?` (${r.row.side})`:''}`;

function UploadReport(p:{upload:Upload;picks:WalkPick[];onChoose:(i:number,c:WalkStreet)=>void;onSkip:(i:number)=>void;onRematch:(i:number,text:string)=>void}){
  return <div className="walk-matches" role="region" tabIndex={0} aria-label="Uploaded streets">
    <ul>
      {p.upload.rows.map((r,i)=>{
        const state=rowState(r,p.picks);const name=rowName(r);
        const cand=r.row.candidates.find(c=>c.id===r.chosen)??(r.row.status==='matched'?r.row.candidates[0]:undefined);
        return <li key={i} className={cx('walk-match',`is-${state}`)}>
          <div className="walk-match-line">
            <span className="walk-match-name">{name}</span>
            {state==='ok'&&<span className="walk-match-ok"><Check size={13} strokeWidth={2.25} aria-hidden="true"/><span className="walk-sr">Matched: </span>{cand?countText(doorsOnSide(cand,r.row.side),'door'):'Matched'}</span>}
            {state==='left'&&<span className="walk-match-muted">Left out</span>}
            {state==='pick'&&<span className="walk-match-pick">Pick a place</span>}
            {state==='busy'&&<span className="walk-match-muted" role="status">Matching…</span>}
            {state==='none'&&<span className="walk-match-muted walk-match-none">No match{r.row.suggestions.length>0&&' ·'}
              {r.row.suggestions.slice(0,3).map(s=><Fragment key={s}>{' '}<button type="button" className="walk-link" onClick={()=>p.onRematch(i,s)}>{s}?</button></Fragment>)}</span>}
          </div>{' '}
          {r.row.status==='pick'&&<div className="walk-places" role="group" aria-label={`Places for ${name}`}>
            {r.row.candidates.map(c=><button key={c.id} type="button" className="walk-place" aria-pressed={r.chosen===c.id&&rowLive(r,p.picks)} onClick={()=>p.onChoose(i,c)}>{`${c.city||c.zip||'Street'} · ${doorsOnSide(c,r.row.side)}`}</button>)}
            {state==='pick'&&<button type="button" className="walk-link" onClick={()=>p.onSkip(i)}>Leave out</button>}
          </div>}
        </li>;
      })}
    </ul>
  </div>;
}

// --- results: five toggle buttons per voter, and the latest result on file as dated history beside them ----------------------------
// A button shows a mark on this list: one set on this screen, or one recorded on this list before, which comes back selected. A result on
// file from another list is history: it is not a mark, and choosing a code records a new visit on this list, the same code as the history
// included. Choosing the code that is marked takes the mark back.
const ResultRadios=memo(function ResultRadios(p:{id:string;name:string;value:WalkCode|null|undefined;unsaved:boolean;note?:string;onMark:(id:string,code:WalkCode|null)=>void}){
  const refs=useRef<(HTMLButtonElement|null)[]>([]);
  const tab=p.value?WALK_CODES.indexOf(p.value):0;
  const go=(i:number)=>{const n=(i+WALK_CODES.length)%WALK_CODES.length;refs.current[n]?.focus();p.onMark(p.id,WALK_CODES[n]);};
  return <div className="walk-res" role="radiogroup" aria-label={`Result for ${p.name}`} aria-describedby={p.note}>
    {WALK_CODES.map((c,i)=><button key={c} ref={el=>{refs.current[i]=el;}} type="button" role="radio" aria-checked={p.value===c} aria-label={`${CODE_LABEL[c]} (${c})`} title={CODE_LABEL[c]} tabIndex={i===tab?0:-1}
      onClick={()=>p.onMark(p.id,markAfterClick(p.value,c,p.unsaved))}
      onKeyDown={e=>{
        if(e.key==='ArrowRight'||e.key==='ArrowDown'){e.preventDefault();go(i+1);}
        else if(e.key==='ArrowLeft'||e.key==='ArrowUp'){e.preventDefault();go(i-1);}
        else if(e.key==='Home'){e.preventDefault();go(0);}
        else if(e.key==='End'){e.preventDefault();go(WALK_CODES.length-1);}
      }}>{c}</button>)}
  </div>;
});

const VoterRow=memo(function VoterRow(p:{v:WalkVoter;door:WalkDoor;first:boolean;value:WalkCode|null|undefined;unsaved:boolean;last:WalkLast|null;onMark:(id:string,code:WalkCode|null)=>void;showOptIns:boolean;optInsDisabled:boolean;onRecordOptIn:RecordWalkOptIn}){
  const {v,door,first}=p;
  const note=p.unsaved?`walk-unsaved-${door.seq}-${v.person_id}`:undefined;
  return <tr role="row" className={cx(!first&&'cont',p.showOptIns&&hasRecordedOptIn(v.opt_ins)&&'has-recorded-opt-in')}>
    <td role="cell" className="walk-addr">{first
      ?<>{doorAddress(door)}{door.placed_by==='interpolated'&&<span title="Placed by house number"><span aria-hidden="true"> ≈</span><span className="walk-sr">, placed by house number</span></span>}</>
      :<span className="walk-sr">{doorAddress(door)}</span>}</td>
    <td role="cell" className="walk-name" title={v.name}>{v.name}
      {p.showOptIns&&<WalkOptIns personId={v.person_id} name={v.name} value={v.opt_ins} disabled={p.optInsDisabled} onRecord={p.onRecordOptIn}/>}</td>
    <td role="cell" className="walk-detail">{phoneText(v.phone)}</td>
    <td role="cell" className="walk-detail" data-label="Pri">{tallyText(v.pri)}</td><td role="cell" className="walk-detail" data-label="Gen">{tallyText(v.gen)}</td>
    <td role="cell" className="walk-detail" data-label="Abs">{tallyText(v.abs)}</td><td role="cell" className="walk-detail" data-label="Party">{v.party}</td>
    <td role="cell" className="walk-detail" data-label="Age">{v.age??''}</td><td role="cell" className="walk-detail" data-label="Gender">{v.gender}</td>
    <td role="cell" className="walk-detail" data-label="Reg.">{regDate(v.reg_date)}</td>
    <td role="cell" className={cx('walk-res-cell',p.unsaved&&'is-unsaved')}><ResultRadios id={v.person_id} name={v.name} value={p.value} unsaved={p.unsaved} note={note} onMark={p.onMark}/>
      {p.unsaved&&<span id={note} className="walk-unsaved" title="The server refused this result. Choose it again to send it again.">{unsavedText(p.value)}</span>}
      {p.last&&<span className={cx('walk-last',p.value!==undefined&&'is-marked')} title={`Last result: ${lastLabel(p.last)}`}>Last: {lastText(p.last)}</span>}</td>
  </tr>;
});

const RunPanel=memo(function RunPanel(p:{run:WalkRun;total:number;results:WalkResults;unsaved:ReadonlySet<string>;history:WalkHistory;onMark:(id:string,code:WalkCode|null)=>void;showOptIns:boolean;optInsDisabled:boolean;optInPending:ReadonlyMap<string,unknown>;optInList:string;onRecordOptIn:RecordWalkOptIn}){
  const {run}=p;
  const title=`${run.street} (${sideLabel(run.side)})`;
  return <section className="walk-run" id={`walk-run-${run.stop}`} aria-labelledby={`walk-run-title-${run.stop}`}>
    <header className="walk-run-head">
      {run.precinct&&<span className="walk-run-precinct">Precinct {run.precinct}</span>}
      <h2 className="walk-run-title" id={`walk-run-title-${run.stop}`}>{title}</h2>
      <span className="walk-run-meta">{runHeader(run,p.total)}</span>
    </header>
    <div className="walk-table-wrap" role="region" tabIndex={0} aria-label={`Stop ${run.stop}, ${title}: voters`}>
      <table className={cx('walk-table',p.showOptIns&&'has-opt-ins')} role="table">
        <colgroup>{COLUMNS.map(h=><col key={h}/>)}</colgroup>
        <thead role="rowgroup"><tr role="row">{COLUMNS.map(h=><th key={h} scope="col" role="columnheader">{h}</th>)}</tr></thead>
        <tbody role="rowgroup">{run.doors.flatMap(door=>door.voters.map((v,i)=>   // a voter with a mark, or a cleared mark awaiting the server, keeps the room for the history line but does not show it
          <VoterRow key={`${p.optInList}-${door.seq}-${v.person_id}`} v={v} door={door} first={i===0} value={Object.hasOwn(p.results,v.person_id)?p.results[v.person_id]:undefined} unsaved={p.unsaved.has(v.person_id)} last={lastOf(v,p.history)} onMark={p.onMark} showOptIns={p.showOptIns} optInsDisabled={p.optInsDisabled||p.optInPending.has(v.person_id)} onRecordOptIn={p.onRecordOptIn}/>))}</tbody>
      </table>
    </div>
    {run.doors.some(d=>d.placed_by==='interpolated')&&<p className="walk-run-foot">“≈” = placed by house number (no geocode)</p>}
  </section>;
});

// --- door script -------------------------------------------------------------------------------------------------------------------
function DoorScript(){
  const [me,setMe]=useState('');const [campaign,setCampaign]=useState('');
  const values={'your name':me,campaign} as const;
  return <details className="walk-script">
    <summary>Door script</summary>
    <div className="walk-script-body">
      <div className="walk-script-fill">
        <label>Your name<input value={me} onChange={e=>setMe(e.target.value)} maxLength={60} autoComplete="off"/></label>
        <label>Campaign<input value={campaign} onChange={e=>setCampaign(e.target.value)} maxLength={60} autoComplete="off"/></label>
      </div>
      <ol className="walk-steps">{DOOR_SCRIPT.map(step=><li key={step.id}>
        <strong>{step.title}</strong>
        <p className="walk-say">“{fillScript(step.line,values)}”</p>
        {step.choices.length>0&&<ul>{step.choices.map(c=><li key={c.say}>{choiceText(c)}</li>)}</ul>}
      </li>)}</ol>
    </div>
  </details>;
}

// The map only adds to the list, so a map that fails to load leaves a note and nothing else.
class MapGuard extends Component<{children:ReactNode},{failed:boolean}>{
  state={failed:false};
  static getDerivedStateFromError(){return {failed:true};}
  render(){return this.state.failed?<p className="walk-map-fail" role="status">The map could not be loaded. The list below has every stop.</p>:this.props.children;}
}

// --- screen ---------------------------------------------------------------------------------------------------------------------------
export function WalkScreen({meta,route}:{meta:Meta;route:Route}){
  const [init]=useState(()=>readWalkAddress(route.search));
  const [source,setSource]=useState<'streets'|'addresses'>('streets');
  const [splitSides,setSplitSides]=useState(()=>new URLSearchParams(route.search).get('split_sides')!=='0');
  const [capText,setCapText]=useState(()=>new URLSearchParams(route.search).get('max_doors')??'');
  const [sheetIndex,setSheetIndex]=useState(0);
  let cap:number|null=null,capError='';try{cap=doorCap(capText);}catch(e){capError=(e as Error).message;}
  const [area,setArea]=useState<Area>(init.area);
  const [picks,setPicks]=useState<WalkPick[]>(init.picks);const picksRef=useRef(picks);picksRef.current=picks;
  const [spans,setSpans]=useState<number[]>(init.spans??[]);const spansRef=useRef(spans);   // planned blocks (road spans from the block plan)
  const [fresh,setFresh]=useState(init.fresh);const freshRef=useRef(fresh);   // the list leaves out the doors with a recorded result; it goes with planned blocks
  const [trim,setTrim]=useState<WalkListTrim|null>(init.trim??null);const trimRef=useRef(trim);   // the one-click list cut to a door count (doors, wp, wi); it goes with fresh planned blocks
  const putSpans=(next:number[],left=false,cut:WalkListTrim|null=null)=>{
    spansRef.current=next;setSpans(next);freshRef.current=left;setFresh(left);
    const kept=next.length>0&&left?cut:null;trimRef.current=kept;setTrim(kept);
  };
  const features=useFeatures();
  // One click (Plan walk list) and the sliders and doors it uses. A server without these settings gets the agent defaults.
  const roi=features?.roi??null;
  const turfCfg=turfOf(roi),defaults=potentialOf(roi);
  const [picked,setPicked]=useState<UntappedWeights|null>(null);   // the sliders as moved on this screen
  const weights=useMemo(()=>picked??(features?.roi?readWeights(features.district,defaults):defaults),[picked,features]);
  const changeWeights=(next:UntappedWeights)=>{setPicked(next);if(features)writeWeights(features.district,next);};
  const savedDoors=useMemo(()=>features?.roi?readDoors(features.district,turfCfg.doors):turfCfg.doors,[features]);
  const [doorsText,setDoorsText]=useState<string|null>(null);   // what is typed in Doors; null until it is touched
  const doorsNow=clampDoors(doorsText??savedDoors,turfCfg.doors);
  const [turf,setTurf]=useState<WalkTurf|null>(null);   // the blocks the last click picked: the note under the totals
  const [printScores,setPrintScores]=useState(false);   // the Print scores toggle: off, the PDF and CSV carry no scores
  const [showOptIns,setShowOptIns]=useState(false);
  const optInWrites=useRef(optInWriteScope()).current;
  const optInView=useRef(0);
  const [optInPending,setOptInPending]=useState<ReadonlyMap<string,unknown>>(new Map());
  const optInDistrict=useRef(features?.district);optInDistrict.current=features?.district;
  useEffect(()=>{optInWrites.reset();setOptInPending(new Map());return()=>optInWrites.reset();},[optInWrites,features?.district]);
  const [hosted,setHosted]=useState(false);   // a hosted server: no CSV download and no scores (the server refuses them), and the PDF is watermarked
  useEffect(()=>{getSession().then(s=>setHosted(s.hosted===true)).catch(()=>{});},[]);
  const [upload,setUpload]=useState<Upload|null>(null);const uploadRef=useRef<Upload|null>(null);const uploadSeq=useRef(0);const uploadAbort=useRef<AbortController|null>(null);
  const [uploadError,setUploadError]=useState('');const [skipped,setSkipped]=useState<WalkCsvProblem[]>([]);const [matching,setMatching]=useState(false);
  const [payload,setPayload]=useState<WalkListPayload|null>(null);const payloadRef=useRef<WalkListPayload|null>(null);
  const [listKey,setListKey]=useState(init.list);
  const [loading,setLoading]=useState(false);const [loadError,setLoadError]=useState<{message:string;retry:()=>void}|null>(null);
  const [note,setNote]=useState('');const [actionNote,setActionNote]=useState<{text:string;alert:boolean}|null>(null);const [announce,setAnnounce]=useState('');
  useStartupReady(startPlan(init)==='none'||!!payload||!!loadError||!!note);
  const [shade,setShade]=useState<WalkShade>('doors');
  const [results,setResults]=useState<WalkResults>({});const resultsRef=useRef(results);resultsRef.current=results;
  const [history,setHistory]=useState<WalkHistory>({});   // the marks on this list, and the results on file that a later look at the list changed
  const historyRef=useRef(history);historyRef.current=history;
  const [saveState,setSaveState]=useState<SaveState>('idle');const [saveError,setSaveError]=useState('');
  const [saveOpen,setSaveOpen]=useState(false);const [saveTitle,setSaveTitle]=useState('');const [busy,setBusy]=useState<''|'save'|'pdf'|'zip'>('');
  const [lists,setLists]=useState<WalkSavedList[]>([]);const [listsError,setListsError]=useState(false);
  const [earlier,setEarlier]=useState<{message:string}|null>(null);   // results from before the screen was left that could not be saved
  const [refusal,showRefusal]=useState<RefusalNote|null>(()=>refusals.note);   // results the server refused: told until dismissed, whichever list comes on screen meanwhile
  const [unsaved,setUnsaved]=useState<ReadonlySet<string>>(()=>new Set());   // marks on screen the server refused: kept and flagged until marked again and saved
  const unsavedRef=useRef(unsaved);unsavedRef.current=unsaved;
  const [replaced,setReplaced]=useState(false);   // the list saved is not the one shown (the voter data changed in between), and is shown now
  const entryRef=useRef<HeldList|null>(null);const timer=useRef<ReturnType<typeof setTimeout>>(undefined);   // the shelf entry of the list on screen
  const saving=useRef<{base:WalkListPayload;job:Promise<string>}|null>(null);const resume=useRef<(()=>void)|null>(null);
  const gen=useRef(0);const genAbort=useRef<AbortController|null>(null);const matchJobs=useRef(latestJob()).current;const refocusSave=useRef(false);
  const main=useRef<HTMLElement>(null);const refusedNote=useRef<HTMLDivElement>(null);const saveButton=useRef<HTMLButtonElement>(null);const fileInput=useRef<HTMLInputElement>(null);const search=useRef<HTMLDivElement>(null);

  // ---- saved lists ---------------------------------------------------------------------------------------------------------------
  const refreshLists=useCallback(async()=>{
    try{setLists((await getWalkLists()).lists??[]);setListsError(false);}catch{setListsError(true);}
  },[]);

  // A list is frozen on the server the first time it is saved, by the Save button or by the first result recorded.
  const ensureSaved=useCallback((title?:string):Promise<string>=>{
    const base=payloadRef.current,entry=entryRef.current;
    if(!base||!entry)return Promise.reject(new Error('There is no list to save.'));
    if(base.list.key)return Promise.resolve(base.list.key);
    if(saving.current?.base===base)return saving.current.job;   // only this list's own save: another list's key would take these results
    const job:Promise<string>=saveEntry(entry,title).then(list=>{
      const key=String(list.key);
      if(payloadRef.current===base){
        const next=entry.replaced??{...base,list},moved=entry.moved;   // marks follow a voter whose id changed; the rest are refused when sent
        payloadRef.current=next;setPayload(next);setListKey(key);
        if(entry.replaced){
          setResults(r=>rekeyed(r,moved));setHistory(h=>rekeyed(h,moved));setUnsaved(u=>moved?.size?new Set([...u].map(id=>moved.get(id)??id)):u);
          setReplaced(true);setAnnounce(LIST_REPLACED);
        }
      }
      void refreshLists();
      return key;
    }).finally(()=>{if(saving.current?.job===job)saving.current=null;});
    saving.current={base,job};return job;
  },[refreshLists]);

  const recordOptIn=useCallback<RecordWalkOptIn>(async(personId,kind,status)=>{
    const entry=entryRef.current,district=optInDistrict.current;
    if(!entry||!payloadRef.current)throw new Error('Build or open a list first.');
    const request=optInWrites.begin(personId,kind);
    setOptInPending(p=>new Map(p).set(personId,request));
    const current=()=>request.current()&&entryRef.current===entry&&optInDistrict.current===district;
    const requireCurrent=()=>{if(!current())throw new DOMException('The displayed list changed.','AbortError');};
    try{
      const key=await ensureSaved();
      requireCurrent();
      const shown=payloadRef.current;
      if(shown?.list.key!==key||!shown.runs.some(run=>run.doors.some(door=>door.voters.some(voter=>voter.person_id===personId))))
        throw new Error('The saved roster changed. Review the list before recording an opt-in.');
      const result=await saveWalkOptIns(key,[optInEntry(personId,kind,status)],request.signal);
      requireCurrent();
      const returned=result.opt_ins?.[personId];
      if(result.saved!==1||!returned||recordedOptIns(returned)[kind].source!=='recorded_manually'||recordedOptIns(returned)[kind].status!==status)
        throw new Error('The server did not confirm this opt-in. Reload the list before trying again.');
      const base=payloadRef.current;
      if(base?.list.key!==key)throw new DOMException('The displayed list changed.','AbortError');
      const next=patchRecordedOptIn(base,personId,kind,recordedOptIns(returned)[kind]);
      payloadRef.current=next;setPayload(next);
    }finally{
      const wasCurrent=request.current();request.finish();
      if(wasCurrent&&entryRef.current===entry&&optInDistrict.current===district)setOptInPending(p=>{
        if(p.get(personId)!==request)return p;
        const next=new Map(p);next.delete(personId);return next;
      });
    }
  },[ensureSaved,optInWrites]);

  // Clearing a mark brings back whatever else is on file for the voter (a result recorded on another list). That is history to show, so
  // the list is looked at again: the voters whose clearing is confirmed show what the server holds for them, as a reload would, and their
  // cleared marks stop standing in for it. Everyone else keeps the history the list came with.
  const reconcile=useCallback(async(key:string)=>{
    try{
      const fresh=historyOf(await getWalkSavedList(key));
      const entry=entryRef.current;
      if(!entry||payloadRef.current?.list.key!==key)return;   // another list is on screen now
      const ids=confirmedClears(resultsRef.current,id=>entry.box.holds(id));
      if(!ids.length)return;
      setHistory(h=>({...h,...Object.fromEntries(ids.map(id=>[id,fresh[id]??null]))}));
      setResults(r=>dropCleared(r,ids));
    }catch{/* the cleared cells stay empty; the next clear looks again */}
  },[]);
  // Results the server refused stay the walker's marks: flagged Not saved on screen and in the CSV, named in the note, and sent again only when
  // the walker marks them again. Nothing is taken off the screen.
  const refuse=useCallback((entry:HeldList,refused:Map<string,WalkVisit>,why:string,local=false)=>{
    showRefusal(holdRefused(entry,refused,why,local));
    if(entryRef.current===entry)setUnsaved(u=>new Set([...u,...refused.keys()]));
  },[]);

  // Results are sent after 600 ms of quiet, a day of visits and at most 200 a request; one request at a time, in order. A failure keeps
  // the batch for Retry. The entry counts a batch as unsaved until its request is answered, so closing the page is warned about while one
  // is in flight, and a batch that fails after the screen was left stays on the shelf for the next visit.
  const flush=useCallback((): Promise<boolean>=>{
    clearTimeout(timer.current);
    const entry=entryRef.current;
    if(!entry)return Promise.resolve(true);
    return flushList(entry,{
      ready:()=>true,
      send:async batch=>{
        const shown=entryRef.current===entry;   // the list on screen takes its key into the screen; one that was left saves quietly
        const {list:key,sent}=await sendBatch(entry,batch,(r,why)=>refuse(entry,r,why,true),shown?()=>ensureSaved():undefined);
        if(entryRef.current===entry)setUnsaved(u=>[...sent.keys()].some(id=>u.has(id))?new Set([...u].filter(id=>!sent.has(id))):u);   // saved now: no longer flagged
        showRefusal(refusals.note);   // and no longer in the note: a note whose doors are all saved goes
        if(shown&&[...batch.values()].some(v=>v.code===null))void reconcile(key);
      },
      report:(state,message)=>{
        if(entryRef.current!==entry)return;
        if(state==='saving')setSaveError('');else if(state==='error')setSaveError(message??'');
        setSaveState(state);
      },
      refuse:(refused,why)=>refuse(entry,refused,why),
    });
  },[ensureSaved,reconcile,refuse]);
  const mark=useCallback((id:string,code:WalkCode|null)=>{
    const entry=entryRef.current;if(!entry)return;
    setResults(r=>({...r,[id]:code}));
    outbox.keep(entry);entry.box.set(id,{code,on:localToday()});setSaveState('pending');   // the day is the day of the visit, taken now
    clearTimeout(timer.current);timer.current=setTimeout(()=>void flush(),AUTOSAVE_MS);
  },[flush]);
  // Results kept from before the screen was left, for lists that are not on screen: sent again here; a failure shows with Retry.
  const settleEarlier=useCallback(async():Promise<boolean>=>{
    const others=outbox.held().filter(e=>e!==entryRef.current);
    if(!others.length){setEarlier(null);return true;}
    let message:string|null=null,refused=false;
    for(const entry of others){
      let why='';
      // a failure blocks with Retry; a refusal is only told: those results are never sent again, so there is nothing to wait for
      const held=(local:boolean)=>(r:Map<string,WalkVisit>,text:string)=>{refused=true;showRefusal(holdRefused(entry,r,text,local));};
      const ok=await flushList(entry,{ready:()=>true,send:batch=>sendBatch(entry,batch,held(true)),report:(state,text)=>{if(state==='error')why=text??'';},refuse:held(false)});
      if(!ok)message??=why||'The results could not be saved.';
    }
    setEarlier(message===null?null:{message});
    showRefusal(refusals.note);   // results saved here leave the note too
    if(message===null){if(!refused)setAnnounce('Results from earlier are saved.');void refreshLists();}
    return message===null;
  },[refreshLists]);

  // ---- build a list --------------------------------------------------------------------------------------------------------------
  const show=useCallback((p:WalkListPayload,adopt:boolean)=>{
    optInWrites.reset();optInView.current++;setOptInPending(new Map());
    // results recorded on this list come back selected, and so do the walker's marks the server refused (flagged); the rest is history
    const marks=marksWithRefused(p,refusals.marks,listIdentity(p.list?.key,p.query??{}));
    payloadRef.current=p;setPayload(p);setSheetIndex(0);setSource('streets');setTurf(null);setResults(marks.results);setUnsaved(marks.unsaved);setHistory({});setReplaced(false);clearTimeout(timer.current);
    if(adopt)setSplitSides(p.query?.split_sides!=='0');
    entryRef.current=outbox.open({meta:p.list?.key?p.list:null,title:defaultListTitle(p),query:p.query??{},where:doorsByPerson(p),seen:seenBy(p),fingerprint:p.fingerprint});
    setSaveState('idle');setSaveError('');setSaveOpen(false);setActionNote(null);setListKey(p.list?.key??'');
    const names=new Map<number,string>(p.runs.map(r=>[r.street_id,r.street]));
    const from=adopt?pickerOf(p):null;   // a re-knock list (either query shape) and a list with no picker settings leave the picker as it is
    if(from){
      setArea(from.area);
      const next=parseStreetsParam(from.streets).map((s):WalkPick=>({...s,label:names.get(s.id)??'',city:''}));picksRef.current=next;setPicks(next);
      putSpans(parseSpansParam(from.spans),!!from.fresh,from.trim??null);
      putUpload(null);setSkipped([]);
    }else{
      const next=picksRef.current.map(s=>s.label?s:{...s,label:names.get(s.id)??''});picksRef.current=next;setPicks(next);
    }
    setAnnounce(`Walk list ready. ${totalsLine(p.totals)}.`);
  },[]);
  const load=useCallback(async<T extends WalkListPayload,>(job:(signal:AbortSignal)=>Promise<T>,done:(p:T)=>void,opts:{fail?:()=>void;say?:(message:string)=>void}={}):Promise<void>=>{
    resume.current=null;
    if(!(await flush())){setNote('Some results are not saved yet. Use Retry beside the list actions, then try again.');return;}
    if(!(await settleEarlier())){   // results from before the screen was left go first; Retry under the note builds the list once they are saved
      resume.current=()=>void load(job,done,opts);
      setNote('Some results from earlier are not saved yet. Use Retry below, and the list builds once they are.');return;
    }
    const id=++gen.current;
    optInWrites.reset();setOptInPending(new Map());
    genAbort.current?.abort();const ac=genAbort.current=new AbortController();
    setLoading(true);setLoadError(null);setNote('');setAnnounce('Building the walk list.');
    if(matchMedia('(max-width:900px)').matches)requestAnimationFrame(()=>main.current?.scrollIntoView({block:'start',behavior:'smooth'}));
    try{
      const p=await job(ac.signal);
      if(id!==gen.current)return;
      done(p);
    }catch(e){
      if(id!==gen.current||ac.signal.aborted)return;
      opts.fail?.();
      if(e instanceof ApiError&&(e.status===400||e.status===422))(opts.say??setNote)(e.message);   // a limit or a bad pick: said beside the button that was used
      else setLoadError({message:e instanceof Error&&e.message?e.message:'The walk list could not be built.',retry:()=>void load(job,done,opts)});
    }finally{if(id===gen.current)setLoading(false);}
  },[flush,settleEarlier]);
  const retryEarlier=async()=>{
    if(!(await settleEarlier()))return;
    setNote('');const go=resume.current;resume.current=null;go?.();   // the list the walker asked for while these were failing
  };

  function generate(planned:number[]=spansRef.current){
    if(capError){setNote(capError);return;}
    const waiting=(upload?.rows??[]).filter(r=>rowState(r,picks)==='pick');
    if(waiting.length){
      const names=waiting.slice(0,2).map(rowName).join(' and ')+(waiting.length>2?` and ${waiting.length-2} more`:'');
      setNote(`Choose a place for ${names}, or leave ${waiting.length===1?'it':'them'} out.`);return;
    }
    if(!picks.length&&!planned.length&&noArea(area)){setNote('Pick an area or add a street first.');return;}
    const streets=streetsParam(picks),where={...area},left=freshRef.current,cut=trimRef.current??undefined;
    void load(sig=>getWalkList(streets,where,sig,planned,left,cut,splitSides),p=>show(p,false));
  }
  function walkPlanned(ids:number[]){putSpans(ids,true);generate(ids);}   // the checked blocks of the block plan, whole (no doors); their counts leave out doors with a recorded result
  const commitDoors=():number=>{setDoorsText(String(doorsNow));if(features)writeDoors(features.district,doorsNow);return doorsNow;};
  // The one click: the blocks with the most untapped potential in the precinct, then the routed list built from them alone.
  function planTurf(){
    if(!roi)return;
    if(!area.precinct){setNote('Pick a precinct first.');return;}
    const where={...area},asked=commitDoors(),q=weightsQuery(weights),got:{turf?:WalkTurf;cut?:WalkListTrim|null}={};
    void load(async sig=>{
      const plan=await getWalkPlan(where,{preset:'push',...q,doors:asked},sig);
      got.turf=plan.turf;
      if(!plan.turf)throw new ApiError(NO_TURF,400,true);
      if(!plan.turf.spans.length)throw new ApiError(NO_DOORS,400,true);
      got.cut=trimFor(plan.turf,asked,q);   // the list is cut to the doors asked for, with the weights the blocks were picked by (a server that cannot cut sends no trim)
      return getWalkList('',where,sig,plan.turf.spans,true,got.cut??undefined,splitSides);
    },p=>{
      picksRef.current=[];setPicks([]);putUpload(null);setSkipped([]);   // the list is the planned blocks alone
      putSpans(got.turf?.spans??[],true,got.cut??null);show(p,false);setTurf(got.turf??null);
    });
  }
  const openSaved=useCallback((key:string)=>{
    setListKey(key);
    void load(sig=>getWalkSavedList(key,sig),p=>show(p,true),{fail:()=>setListKey('')});
  },[load,show]);
  function reknock(){
    const p=payloadRef.current;
    if(!p?.list.key){setActionNote({text:'Save this list first. Then record results and use Not home again to list the doors where nobody answered.',alert:false});return;}
    const key=p.list.key,title=`Not home again · ${p.list.title}`.slice(0,120);setActionNote(null);
    void load(sig=>reknockWalkList(title,key,sig),q=>{
      show(q,false);void refreshLists();
      if(q.missing)setActionNote({text:missingText(q.missing),alert:false});   // voters marked Not home that the voter data no longer has
    },{say:text=>setActionNote({text,alert:true})});
  }
  async function save(title:string){
    setBusy('save');setActionNote(null);
    try{await ensureSaved(title);refocusSave.current=true;setSaveOpen(false);setAnnounce('List saved.');void flush();}   // focus moves once this render has cleared busy
    catch(e){setActionNote({text:e instanceof Error&&e.message?e.message:'The list could not be saved.',alert:true});}
    finally{setBusy('');}
  }
  // The Save button is disabled while saving and the form that held focus is gone once the list is saved: focus goes back to the button after that render.
  useEffect(()=>{
    if(refocusSave.current&&busy===''&&!saveOpen){refocusSave.current=false;saveButton.current?.focus();}
  },[busy,saveOpen]);

  // ---- exports -------------------------------------------------------------------------------------------------------------------
  /** The scores of the people on the list, asked for only while Print scores is on. `undefined`: off. `null`: they could not be had, and the note says so. */
  async function printedScores(p:WalkListPayload):Promise<WalkScoreMap|undefined|null>{
    if(!printScores)return undefined;
    const ids=p.runs.flatMap(r=>r.doors.flatMap(d=>d.voters.map(v=>v.person_id)));
    try{
      const out:WalkScoreMap={};
      for(const chunk of idChunks(ids))Object.assign(out,(await getWalkScores(chunk)).scores);
      return out;
    }catch(e){
      setActionNote({text:`The scores could not be added. ${e instanceof ApiError&&e.message?e.message:'Try again, or turn Print scores off.'}`,alert:true});
      return null;
    }
  }
  async function exportPdf(){
    const p=payloadRef.current;if(!p)return;
    if(capError){setActionNote({text:capError,alert:true});return;}
    setBusy('pdf');setActionNote(null);
    try{
      const scores=await printedScores(p);if(scores===null)return;
      const session=await getSession();   // hosted: every page says who printed the list and when; no identity, no PDF
      if(session.hosted&&!session.email)throw new Error('no identity');
      const watermark=session.hosted&&session.email?watermarkText(session.email):undefined;
      await exportWalkPdf(p,{area:listLabel(p),scores,watermark,sheets:splitWalkSheets(p,cap)});
    }catch{setActionNote({text:'The PDF could not be created. Try again.',alert:true});}
    finally{setBusy('');}
  }
  async function exportCsv(){
    const p=payloadRef.current;if(!p||hosted)return;
    if(capError){setActionNote({text:capError,alert:true});return;}
    const scores=await printedScores(p);if(scores===null)return;
    downloadFile(walkFileName(listLabel(p),p.generated_on,'csv'),'text/csv;charset=utf-8',buildWalkSheetsCsv(splitWalkSheets(p,cap),resultsRef.current,historyRef.current,unsavedRef.current,scores));   // marks made while the scores came in are in the file
  }
  async function exportZip(){
    const p=payloadRef.current;if(!p||busy)return;
    if(capError){setActionNote({text:capError,alert:true});return;}
    const parts=splitWalkSheets(p,cap);setBusy('zip');setActionNote(null);
    try{
      const scores=await printedScores(p);if(scores===null)return;
      const session=await getSession();
      if(session.hosted&&!session.email)throw new Error('Sign in again before exporting.');
      const {exportWalkZip}=await import('../lib/walkRouteExport');
      await exportWalkZip(parts,{area:listLabel(p),scores,watermark:session.hosted&&session.email?watermarkText(session.email):undefined,includeCsv:!session.hosted,
        results:{...resultsRef.current},history:{...historyRef.current},unsaved:new Set(unsavedRef.current)});
    }catch(e){setActionNote({text:e instanceof Error?e.message:'The ZIP could not be created. Try again.',alert:true});}
    finally{setBusy('');}
  }

  // ---- streets: chips and upload -------------------------------------------------------------------------------------------------
  function addPicks(add:WalkPick[]){
    let next=picksRef.current;for(const a of add)next=mergePick(next,a);
    if(next.length>MAX_WALK_STREETS){next=next.slice(0,MAX_WALK_STREETS);setNote(`A walk list can have up to ${MAX_WALK_STREETS} streets.`);}
    picksRef.current=next;setPicks(next);
  }
  const removePick=(id:number)=>{setNote('');const s=picksRef.current.find(p=>p.id===id);setPicks(picksRef.current=picksRef.current.filter(p=>p.id!==id));if(s)setAnnounce(`${pickName(s)} removed.`);search.current?.querySelector('input')?.focus();};
  function cycleSide(id:number){
    const next=picksRef.current.map(p=>p.id===id?{...p,side:nextSide(p.side)}:p);picksRef.current=next;setPicks(next);
    const s=next.find(p=>p.id===id);if(s)setAnnounce(`${pickName(s)}: ${sideWords(s.side)}.`);
  }
  const fromStreet=(s:WalkStreet,side:WalkPick['side']='both'):WalkPick=>({id:s.id,side,label:s.label,city:s.city});
  // The upload on screen lives in uploadRef as well as in state; replacing or clearing it cancels the requests still open for the old one,
  // and clearing it (or leaving it behind for a saved list) also drops a file that is still being matched.
  function putUpload(next:Upload|null){
    if(next?.id!==uploadRef.current?.id){uploadAbort.current?.abort();uploadAbort.current=next?new AbortController():null;}
    if(!next&&matchJobs.cancel())setMatching(false);
    uploadRef.current=next;setUpload(next);
  }
  function patchRow(id:number,i:number,patch:Partial<UploadRow>){const next=patchUploadRow(uploadRef.current,id,i,patch);if(next)putUpload(next);}
  function choose(i:number,c:WalkStreet){
    const u=uploadRef.current,r=u?.rows[i];if(!u||!r)return;setNote('');
    if(r.chosen!=null&&r.chosen!==c.id)setPicks(picksRef.current=picksRef.current.filter(p=>p.id!==r.chosen));
    patchRow(u.id,i,{chosen:c.id,skipped:false});addPicks([fromStreet(c,r.row.side)]);
  }
  function skip(i:number){setNote('');const u=uploadRef.current;if(u)patchRow(u.id,i,{skipped:true});}
  async function rematch(i:number,text:string){
    const u=uploadRef.current,r=u?.rows[i];if(!u||!r)return;
    const id=u.id,signal=uploadAbort.current?.signal;
    const current=()=>uploadRef.current?.id===id;   // false once this upload was replaced or cleared: the answer is dropped before anything changes
    patchRow(id,i,{busy:true});
    try{
      const input:WalkMatchInput={...r.input,street:text};
      const res=(await matchWalkStreets([input],area,signal)).rows[0];
      if(!current())return;
      if(!res){patchRow(id,i,{busy:false});return;}
      const chosen=res.status==='matched'?res.candidates[0]?.id??null:null;
      patchRow(id,i,{input,row:res,chosen,skipped:false,busy:false});
      if(chosen!=null&&res.candidates[0])addPicks([fromStreet(res.candidates[0],res.side)]);
    }catch(e){
      if(!current())return;
      patchRow(id,i,{busy:false});setUploadError(e instanceof Error&&e.message?e.message:'The street could not be matched.');
    }
  }
  async function onFile(file:File){
    // The file chosen last is the only one whose answer is wanted: choosing another, clearing the upload, opening a saved list that
    // restores the picker or leaving the screen makes this one stale, and a stale answer changes nothing.
    const job=matchJobs.start();setMatching(false);
    setUploadError('');setSkipped([]);setNote('');
    let read:WalkCsvRead;
    try{read=readWalkCsv(await file.text());}catch(e){if(job.live()){job.end();setUploadError(e instanceof Error?e.message:'That file could not be read.');}return;}
    if(!job.live())return;
    setSkipped(read.problems);   // rows with a value the server would refuse are reported and left out; the rest go on
    const inputs=read.rows;
    if(!inputs.length){job.end();if(!read.problems.length)setUploadError('No streets found in that file. Add a column named street.');return;}
    setMatching(true);
    try{
      const res=await matchWalkStreets(inputs,area,job.signal);
      if(!job.live())return;
      const rows=res.rows.map((row,i):UploadRow=>({input:inputs[i]??{street:row.input},row,chosen:row.status==='matched'?row.candidates[0]?.id??null:null,skipped:false,busy:false}));
      putUpload({id:++uploadSeq.current,name:file.name,count:inputs.length,rows});
      addPicks(rows.flatMap(r=>r.row.status==='matched'&&r.row.candidates[0]?[fromStreet(r.row.candidates[0],r.row.side)]:[]));
      setAnnounce(`${countText(rows.length,'row')} matched against the street list.`);
    }catch(e){if(job.live())setUploadError(e instanceof Error&&e.message?e.message:'The streets could not be matched.');}
    finally{if(job.live()){job.end();setMatching(false);}}
  }

  // ---- area, address bar, lifecycle ----------------------------------------------------------------------------------------------
  const setLevel=(level:WalkLevel,v:string)=>{setNote('');putSpans([]);setArea(a=>level==='ss'?{ss:v,sh:'',precinct:''}:level==='sh'?{...a,sh:v,precinct:''}:{...a,precinct:v});};
  useEffect(()=>{
    replaceQuery({ss:area.ss||null,sh:area.sh||null,precinct:area.precinct||null,streets:streetsParam(picks)||null,spans:spansParam(spans)||null,fresh:fresh?'1':null,...trimQuery(trim),split_sides:splitSides?'1':'0',max_doors:capText||null,plan:null,list:listKey||null});
  },[area,picks,spans,fresh,trim,listKey,splitSides,capText]);   // plan=1 (from the map) is read once and dropped, so a reload shows the list and does not plan again
  const started=useRef(false);
  useEffect(()=>{
    if(started.current)return;started.current=true;
    void refreshLists();
    void settleEarlier();   // results that could not be saved before the screen was left are sent again now
    const plan=startPlan(init);   // the address names a saved list, or streets and/or an area to build a list from
    if(plan==='saved')openSaved(init.list);else if(plan==='build')generate();
  },[]);
  const turfWaits=useRef(startPlan(init)==='turf');   // plan=1 and a precinct: the one click runs once the district settings are here
  useEffect(()=>{
    if(!turfWaits.current||!features)return;
    turfWaits.current=false;
    if(features.roi)planTurf();else generate();   // a district with no plan builds the precinct's list as it always did
  },[features]);
  useEffect(()=>{
    const warn=(e:BeforeUnloadEvent)=>{if(outbox.unconfirmed()||saving.current||optInWrites.pending()||refusals.note||shownFlagged.current.size>0){e.preventDefault();e.returnValue='';}};
    const hidden=()=>{if(document.visibilityState==='hidden')void flush();};
    window.removeEventListener('beforeunload',warnHeld);   // while the screen is here, its own guard speaks for every list on the shelf
    window.addEventListener('beforeunload',warn);document.addEventListener('visibilitychange',hidden);
    return ()=>{
      window.removeEventListener('beforeunload',warn);document.removeEventListener('visibilitychange',hidden);
      window.addEventListener('beforeunload',warnHeld);   // results the server has not confirmed outlive the screen, and so does the warning about them
      void flush();entryRef.current=null;   // sent once more; whatever fails stays on the shelf, and the screen no longer takes part in the sending
      genAbort.current?.abort();matchJobs.cancel();uploadAbort.current?.abort();
    };
  },[flush]);
  // The list's flagged marks, for both unload guards: only voters this list shows count (flaggedOnList).
  useEffect(()=>{shownFlagged.current=flaggedOnList(payload,unsaved);},[payload,unsaved]);
  // Sign out: the store has forgotten the refusals, and the screen drops what it shows of them, the flagged marks too. (A 401 forgets nothing.)
  useEffect(()=>onRefusedReset(()=>{
    const gone=unsavedRef.current;
    showRefusal(null);setUnsaved(new Set());
    if(gone.size)setResults(r=>Object.fromEntries(Object.entries(r).filter(([id])=>!gone.has(id))));
  }),[]);
  const goToStop=useCallback((stop:number)=>{
    const el=document.getElementById(`walk-run-${stop}`);if(!el)return;
    el.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'auto':'smooth'});
    el.querySelector<HTMLElement>('.walk-table-wrap')?.focus({preventScroll:true});
  },[]);

  const saved=!!payload?.list.key;
  const leftOut=leftOutNote(skipped);
  const sheets=useMemo(()=>payload?splitWalkSheets(payload,capError?null:cap):[],[payload,cap,capError]);
  const selectedSheet=Math.min(sheetIndex,Math.max(0,sheets.length-1));
  const shownSheet=sheets[selectedSheet]??payload;
  const total=shownSheet?.totals?.stops||shownSheet?.runs.length||0;
  return <div className={cx('screen walk-screen',refusal&&'has-refusal')}>
    <PageHeader meta={meta} active="walk"/>
    <main className="walk-body">
      <aside className="walk-rail" aria-label="Walk list builder">
        <p className="walk-eyebrow">Walk list</p>
        <h1 className="walk-title">Build a walk list</h1>
        <p className="walk-sub">Build from streets, paste addresses or import a file.</p>

        <div className="walk-source"><Segmented variant="index" label="Walk list source" items={[{id:'streets',label:'Streets'},{id:'addresses',label:'Addresses'}]} value={source} onChange={setSource}/></div>
        <label className="walk-check"><input type="checkbox" checked={splitSides} onChange={e=>setSplitSides(e.target.checked)}/> Split even and odd</label>
        <label className="walk-doors"><span>Maximum doors per list</span><input type="number" min={1} max={10000} step={1} inputMode="numeric" placeholder="No cap" value={capText} onChange={e=>{setCapText(e.target.value);setSheetIndex(0);}} aria-describedby="walk-cap-hint"/></label>
        <p id="walk-cap-hint" className="walk-hint">Creates as many lists as needed to cover every door. Blank means no cap. Even and odd stay on separate lists when enabled. Downloads include all lists.</p>
        <label className="walk-check"><input type="checkbox" checked={showOptIns} onChange={event=>setShowOptIns(event.target.checked)} aria-describedby={showOptIns?'walk-opt-ins-hint':undefined}/> Show recorded opt-ins</label>
        {showOptIns&&<p id="walk-opt-ins-hint" className="walk-hint">Manually recorded donor and volunteer status with its recording time. Unknown means no current opt-in is recorded. {source==='streets'?'Changing a status saves it; the list is saved first when needed.':'Imported sheets show existing records; use a saved street list to record changes.'} Highlights appear on screen; downloads keep their existing columns.</p>}
        {capError&&<p className="walk-note" role="alert">{capError}</p>}
        {source==='streets'&&<>
        <p className="walk-hint">Generate again after changing even/odd grouping. The cap updates the sheets immediately.</p>

        <h2 className="walk-lab">Area</h2>
        {LEVELS.map(l=><AreaField key={l.level} level={l.level} label={l.label} aria={l.aria} area={area} value={area[l.level]} onChange={v=>setLevel(l.level,v)}/>)}
        {roi&&area.precinct&&<div className="walk-oneclick">
          <label className="walk-doors"><span>Doors</span>
            <input type="number" inputMode="numeric" min={DOORS_MIN} max={DOORS_MAX} step={1} value={doorsText??String(savedDoors)}
              onChange={e=>setDoorsText(e.target.value)} onBlur={commitDoors} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();planTurf();}}}/></label>
          <button type="button" className="walk-button walk-primary" onClick={planTurf} disabled={loading} aria-busy={loading}>Plan walk list</button>
          <p className="walk-hint">Builds a walk list of connected blocks with the most untapped potential, up to this many doors ({DOORS_MIN} to {DOORS_MAX.toLocaleString('en-US')}). Doors with a recorded result are left out. Balance and influence are under Adjust plan.</p>
        </div>}

        <h2 className="walk-lab">Streets</h2>
        <div ref={search}><StreetSearch area={area} onAdd={s=>{setNote('');addPicks([fromStreet(s)]);setAnnounce(`${s.label} added.`);}}/></div>
        <Chips picks={picks} planned={spans.length} onSide={cycleSide} onRemove={removePick} onUnplan={()=>{setNote('');putSpans([]);setAnnounce('Planned blocks removed.');}}/>
        <div className="walk-upload">
          <input ref={fileInput} type="file" accept=".csv,text/csv" hidden aria-label="Upload a CSV of streets" onChange={e=>{const f=e.target.files?.[0];e.target.value='';if(f)void onFile(f);}}/>
          <button type="button" className="walk-button" onClick={()=>fileInput.current?.click()} disabled={matching}><Upload size={15} strokeWidth={1.75} aria-hidden="true"/>{matching?'Matching…':'Upload street CSV'}</button>
          {upload&&<span className="walk-file">{upload.name} · {countText(upload.count,'row')} <button type="button" className="walk-link" onClick={()=>{putUpload(null);setSkipped([]);}}>Clear</button></span>}
        </div>
        {uploadError&&<p className="walk-note" role="alert">{uploadError}</p>}
        {leftOut&&<div className="walk-note" role="alert"><p>{leftOut.headline}</p><ul>{leftOut.reasons.map(t=><li key={t}>{t}</li>)}</ul></div>}
        {upload&&<UploadReport upload={upload} picks={picks} onChoose={choose} onSkip={skip} onRematch={(i,t)=>void rematch(i,t)}/>}

        {features?.roi&&<StreetPlan area={area} roi={features.roi} race={features.candidate?raceWords(features.candidate.race_mode):''} density={features.overlay.density} busy={loading}
          weights={weights} defaults={defaults} onWeights={changeWeights} doors={doorsNow} onWalk={walkPlanned}/>}

        <button type="button" className="walk-generate" onClick={()=>generate()} disabled={loading} aria-busy={loading}>{loading?'Building…':'Generate walk list'}</button>
        {note&&<p className="walk-note" role="alert">{note}</p>}
        {earlier&&<div className="walk-note" role="alert"><p>Results from earlier are not saved yet. {earlier.message}</p>
          <p><button type="button" className="walk-link" onClick={()=>void retryEarlier()}>Retry</button></p></div>}
        {refusal&&<div className="walk-note walk-refused" role="alert" tabIndex={-1} ref={refusedNote}><p>{refusalHeadline(refusal)}</p>
          {refusal.lists.map(l=><p key={l.title}><strong>{l.title}:</strong> {Object.values(l.doors).join(', ')}</p>)}
          {refusal.reasons.length>0&&<p>{refusal.reasons.join(' ')}</p>}
          <p><button type="button" className="walk-link" onClick={()=>{refusals.dismiss();showRefusal(null);}}>Dismiss</button></p></div>}

        <h2 className="walk-lab">Saved lists</h2>
        {lists.length>0
          ?<label className="select walk-select"><span className="select-text" aria-hidden="true"><span className="walk-k">Saved lists:</span> {lists.find(l=>l.key===payload?.list.key)?.title??'Open one'}</span>
            <ChevronDown className="select-chev" strokeWidth={1.75} aria-hidden="true"/>
            <select aria-label="Saved lists" value={lists.some(l=>l.key===payload?.list.key)?payload?.list.key??'':''} onChange={e=>{if(e.target.value)openSaved(e.target.value);}}>
              <option value="">Open a saved list</option>
              {lists.map(l=><option key={l.key} value={l.key}>{`${l.title} · ${countText(l.doors,'door')}${l.kind==='reknock'?' · re-knock':''}${fullDate(l.created_at)?` · ${fullDate(l.created_at)}`:''}`}</option>)}
            </select></label>
          :<p className="walk-hint" role={listsError?'alert':undefined}>{listsError?'Saved lists could not be loaded.':'No saved lists yet.'} {listsError&&<button type="button" className="walk-link" onClick={()=>void refreshLists()}>Retry</button>}</p>}
        </>}
      </aside>

      <section className="walk-main" ref={main} aria-label="Walk list" aria-busy={loading}>
        <p className="walk-sr" role="status">{announce}</p>
        {source==='addresses'?<WalkAddressSheets capText={capText} splitSides={splitSides} hosted={hosted} showOptIns={showOptIns}/>:loading?<div className="walk-wait" aria-hidden="true">
            <span className="sk walk-sk" style={{width:'62%',height:16}}/>
            <span className="sk walk-sk" style={{width:'46%',height:36,marginTop:14}}/>
            <span className="sk walk-sk" style={{width:'100%',height:260,marginTop:14}}/>
            <span className="sk walk-sk" style={{width:'100%',height:150,marginTop:14}}/>
          </div>
        :loadError?<div className="walk-empty is-error" role="alert"><h2>The walk list could not be built</h2><p>{loadError.message}</p><button type="button" className="walk-button" onClick={loadError.retry}>Retry</button></div>
        :payload?<>
          {saved&&<p className="walk-saved-line">{savedLine(payload,lists)}</p>}
          {sheets.length>1&&<div className="walk-sheet-picker">
            <label>Preview list <select aria-label="Walk list preview" value={selectedSheet} onChange={e=>setSheetIndex(Number(e.target.value))}>{sheets.map((s,i)=><option key={i} value={i}>List {i+1} of {sheets.length} · {s.totals.doors} doors</option>)}</select></label>
            <p className="walk-hint">All {payload.totals.doors} doors are covered across {sheets.length} lists{cap?`, at most ${cap} doors each`:''}. The preview and map show the chosen list; map paths show full street sections. The saved record and PDF cover map show the full route. PDF, CSV and ZIP downloads include every list.</p>
          </div>}
          {replaced&&<p className="walk-note" role="status">{LIST_REPLACED}</p>}
          <div className="walk-summary-row">
            <p className="walk-summary">{totalsLine(payload.totals)}</p>
            {payload.runs.length>0&&<div className="walk-seg"><Segmented variant="index" label="Map shading" items={[{id:'doors',label:'Doors'},{id:'party',label:'Party'}]} value={shade} onChange={setShade}/></div>}
          </div>
          {turf&&<div className="walk-turf" role="status">{turfLines(turf,payload.totals,turfCfg.jump_m).map(t=><p key={t}>{t}</p>)}</div>}
          {payload.runs.length>0&&<div className="walk-actions">
            <button type="button" className="walk-button walk-primary" onClick={()=>void exportPdf()} disabled={!!busy||!!capError} aria-busy={busy==='pdf'}>{busy==='pdf'?'Preparing PDF…':sheets.length>1?'Export all lists PDF':'Export PDF'}</button>
            {!hosted&&<button type="button" className="walk-button" disabled={!!busy||!!capError} onClick={()=>void exportCsv()}>{sheets.length>1?'Download all lists CSV':'Download CSV'}</button>}
            <button type="button" className="walk-button" disabled={!!busy||!!capError} aria-busy={busy==='zip'} onClick={()=>void exportZip()}>{busy==='zip'?'Preparing ZIP…':'Download ZIP'}</button>
            {!hosted&&<label className="walk-check"><input type="checkbox" checked={printScores} onChange={e=>setPrintScores(e.target.checked)} aria-describedby="walk-scores-hint"/> Print scores</label>}
            {!hosted&&<span id="walk-scores-hint" className="walk-check-hint">{features?.candidate?'Adds overall, candidate support and participation (0 to 100) to the PDF and the CSV.':'Adds overall and participation (0 to 100) to the PDF and the CSV.'}</span>}
            <button type="button" ref={saveButton} className={cx('walk-button',saved&&'is-off')} aria-disabled={saved||undefined} onClick={()=>{if(saved)return;setSaveTitle(defaultListTitle(payload));setSaveOpen(o=>!o);}} disabled={busy==='save'} aria-expanded={saved?undefined:saveOpen} title={saved?'This list is saved':undefined}>Save</button>
            <button type="button" className={cx('walk-button',!saved&&'is-off')} aria-disabled={!saved||undefined} title={saved?undefined:'Save this list first'} onClick={reknock}>Not home again</button>
            <span className="walk-status">
              {saveState==='error'
                ?<span role="alert" className="is-error">Results not saved. {saveError} <button type="button" className="walk-link" onClick={()=>void flush()}>Retry</button></span>
                :<span role="status">{saveState==='pending'||saveState==='saving'?'Saving…':saveState==='saved'?'Saved':''}</span>}
            </span>
          </div>}
          {saveOpen&&!saved&&<form className="walk-save-form" onSubmit={e=>{e.preventDefault();void save(saveTitle);}} onKeyDown={e=>{if(e.key==='Escape'){setSaveOpen(false);saveButton.current?.focus();}}}>
            <label>List name<input value={saveTitle} onChange={e=>setSaveTitle(e.target.value)} maxLength={120} required autoFocus autoComplete="off"/></label>
            <button type="submit" className="walk-button walk-primary" disabled={busy==='save'}>Save list</button>
            <button type="button" className="walk-button" onClick={()=>{setSaveOpen(false);saveButton.current?.focus();}}>Cancel</button>
          </form>}
          {actionNote&&<p className="walk-note" role={actionNote.alert?'alert':'status'}>{actionNote.text}</p>}
          {payload.runs.length>0?<>
            <div className="walk-map-slot">
              <MapGuard><Suspense fallback={<div className="walk-map-wait sk" role="status" aria-label="Loading the map"/>}><WalkMap payload={shownSheet??payload} shade={shade} onStop={goToStop}/></Suspense></MapGuard>
            </div>
            <DoorScript/>
            <p className="walk-key">{historyKey(payload.window)} {codeLegend()}. Last shows the latest result on file with its date; choosing a result records a new visit.</p>
            {shownSheet?.runs.map(run=><RunPanel key={run.stop} run={run} total={total} results={results} unsaved={unsaved} history={history} onMark={mark} showOptIns={showOptIns} optInsDisabled={loading||busy!==''} optInPending={optInPending} optInList={`${features?.district??''}:${optInView.current}`} onRecordOptIn={recordOptIn}/>)}
          </>:<div className="walk-empty"><h2>No doors found</h2><p>These streets have no voters on file. Try another street or a wider area.</p></div>}
        </>
        :<div className="walk-empty">
          <RouteIcon size={26} strokeWidth={1.5} aria-hidden="true"/>
          <h2>Your walk list will appear here</h2>
          <p>Pick an area and add the streets you plan to walk, then choose Generate walk list. You get the route on a map and the voters at each door in walking order, with a place to record every result.</p>
          <p>For your own addresses, choose Addresses and paste addresses or street names, or upload CSV, TSV, TXT, JSON or XLSX.</p>
        </div>}
      </section>
    </main>
    {refusal&&<div className="walk-refused-bar" role="region" aria-label="Results not saved">
      <span><span aria-hidden="true">!</span> {refusalBarText(refusal)}</span>
      <button type="button" onClick={()=>{const el=refusedNote.current;if(!el)return;el.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'auto':'smooth'});el.focus({preventScroll:true});}}>View</button></div>}
  </div>;
}
