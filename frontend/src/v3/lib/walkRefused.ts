// Results the server refused, kept across a reload of the tab: the walker's refused marks (RefusedMarks, by list and voter) and the refusal
// note (RefusalNote), apart from each other, under one versioned key of this tab's sessionStorage. Never localStorage, never the address.
// Stored is only what the screen shows again: each list's identity (listIdentity), each voter's person id and code, and the note's list
// titles, door labels with their codes, and reasons. Every change is written through; anything malformed on reading is dropped and the key
// rewritten without it. Storage that is missing or throws (blocked site data, a full quota) leaves the page working from memory.
// Pure apart from the storage it is handed, so node --test can load it directly.
import type {WalkCode} from '../api/walkTypes';
import {addRefusal,clearRefused,isWalkCode,keepRefused,rekeyRefused,type RefusalNote,type RefusedMarks} from './walk.ts';

export const REFUSED_KEY='terrier.walk.refused.v1';
export interface RefusedStorage{getItem(key:string):string|null;setItem(key:string,value:string):void;removeItem(key:string):void}
export interface RefusedState{marks:RefusedMarks;note:RefusalNote|null}

/** This tab's sessionStorage, or null where the browser withholds it (reading the property itself can throw). */
export function tabStorage():RefusedStorage|null{
  try{return typeof sessionStorage==='undefined'?null:sessionStorage;}catch{return null;}
}

const isRecord=(v:unknown):v is Record<string,unknown>=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const LIST=/^(?:key|query):/;
/** The marks part: list identity -> person id -> code (null for a clearing the server refused). Bad lists and entries are dropped. */
function marksOf(raw:unknown):{marks:RefusedMarks;clean:boolean}{
  const marks:RefusedMarks=new Map();
  if(raw===undefined)return {marks,clean:true};
  if(!isRecord(raw))return {marks,clean:false};
  let clean=true;
  for(const [list,voters] of Object.entries(raw)){
    if(!LIST.test(list)||!isRecord(voters)){clean=false;continue;}
    const kept=new Map<string,WalkCode|null>();
    for(const [id,code] of Object.entries(voters)){
      if(id&&(code===null||isWalkCode(code)))kept.set(id,code);else clean=false;
    }
    if(kept.size)marks.set(list,kept);else clean=false;
  }
  return {marks,clean};
}
/** The note part: each list's title and doors (person id -> "door (code)"), the reasons, and whether the page refused some itself. */
function noteOf(raw:unknown):{note:RefusalNote|null;clean:boolean}{
  if(raw===undefined||raw===null)return {note:null,clean:true};
  if(!isRecord(raw)||!Array.isArray(raw.lists)||!Array.isArray(raw.reasons)||(raw.local!==undefined&&typeof raw.local!=='boolean'))return {note:null,clean:false};
  let clean=true;
  const lists:RefusalNote['lists']=[];
  for(const l of raw.lists){
    if(!isRecord(l)||typeof l.title!=='string'||!l.title.trim()||!isRecord(l.doors)){clean=false;continue;}
    const doors=Object.entries(l.doors).filter(([id,door])=>id&&typeof door==='string'&&door);
    if(doors.length!==Object.keys(l.doors).length)clean=false;
    if(doors.length)lists.push({title:l.title,doors:Object.fromEntries(doors) as Record<string,string>});else clean=false;
  }
  const reasons=raw.reasons.filter((r):r is string=>typeof r==='string'&&!!r.trim());
  if(reasons.length!==raw.reasons.length)clean=false;
  if(!lists.length)return {note:null,clean:false};
  return {note:{lists,reasons,...raw.local?{local:true}:{}},clean};
}
/** What is written: the marks and the note's own fields, nothing more. */
function stored(state:RefusedState):string|null{
  const marks=Object.fromEntries([...state.marks].filter(([,m])=>m.size>0).map(([list,m])=>[list,Object.fromEntries(m)]));
  const n=state.note;
  if(!Object.keys(marks).length&&!n)return null;
  const note=n?{lists:n.lists.map(l=>({title:l.title,doors:{...l.doors}})),reasons:[...n.reasons],...n.local?{local:true}:{}}:null;
  return JSON.stringify({marks,note});
}

/** Writes the marks and the note; removes the key when both are empty. False when the storage refused (the page carries on from memory). */
export function writeRefused(state:RefusedState,storage:RefusedStorage|null=tabStorage()):boolean{
  if(!storage)return false;
  try{
    const value=stored(state);
    if(value===null)storage.removeItem(REFUSED_KEY);else storage.setItem(REFUSED_KEY,value);
    return true;
  }catch{return false;}
}
/** Reads the marks and the note. Anything malformed is ignored and removed (the key is rewritten without it, or removed). */
export function readRefused(storage:RefusedStorage|null=tabStorage()):RefusedState{
  const empty=():RefusedState=>({marks:new Map(),note:null});
  if(!storage)return empty();
  let raw:string|null;
  try{raw=storage.getItem(REFUSED_KEY);}catch{return empty();}
  if(raw===null)return empty();
  let parsed:unknown;
  try{parsed=JSON.parse(raw);}catch{parsed=undefined;}
  if(!isRecord(parsed)){try{storage.removeItem(REFUSED_KEY);}catch{/* nothing to keep either way */}return empty();}
  const m=marksOf(parsed.marks),n=noteOf(parsed.note);
  const state={marks:m.marks,note:n.note};
  const known=Object.keys(parsed).every(k=>k==='marks'||k==='note');
  if(!m.clean||!n.clean||!known)writeRefused(state,storage);
  return state;
}

/** The refused marks and the note of this tab: restored once, and written back on every change. */
export interface RefusedStore{
  /** Each list's refused marks (listIdentity -> person id -> code), as marksWithRefused reads them when the list is shown. */
  readonly marks:RefusedMarks;
  /** The note, until the walker dismisses it. */
  readonly note:RefusalNote|null;
  /** Results refused on a list: kept as the walker's marks under `list`, and added to the note with their doors. Answers with the note. */
  hold(list:string,title:string,refused:ReadonlyMap<string,{code:WalkCode|null}>,doors:Record<string,string>,why:string,local?:boolean):RefusalNote;
  /** The lists' refused marks renamed to the person ids of the list saved (movedIds). */
  rekey(lists:readonly string[],moved:ReadonlyMap<string,string>|null):void;
  /** Voters whose results are now saved: their marks are no longer refused. The only way a mark leaves. */
  clear(lists:readonly string[],ids:Iterable<string>):void;
  /** The walker dismissed the note. The marks stay: a walker's mark is never erased. */
  dismiss():void;
}
export function refusedStore(storage:RefusedStorage|null=tabStorage()):RefusedStore{
  const {marks,note:restored}=readRefused(storage);
  let note=restored;
  const write=()=>{writeRefused({marks,note},storage);};
  return {
    marks,
    get note(){return note;},
    hold(list,title,refused,doors,why,local=false){
      keepRefused(marks,list,refused);
      note=addRefusal(note,title,refused,doors,why,local);
      write();return note;
    },
    rekey(lists,moved){
      if(!moved?.size)return;
      for(const list of lists)rekeyRefused(marks,list,moved);
      write();
    },
    clear(lists,ids){
      const saved=[...ids];
      if(!saved.length||!lists.some(list=>marks.has(list)))return;
      for(const list of lists)clearRefused(marks,list,saved);
      write();
    },
    dismiss(){note=null;write();},
  };
}
