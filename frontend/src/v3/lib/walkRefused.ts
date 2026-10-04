// Results the server refused, kept across a reload of the tab: the walker's refused marks (RefusedMarks, by list and voter) and the refusal
// note (RefusalNote), apart from each other, under one versioned key of this tab's sessionStorage. Never localStorage, never the address.
// Stored is only what the screen shows again: each list's identity (listIdentity), each voter's person id and code, and the note's list
// titles, door labels with their codes, and reasons. Every change is written through; anything malformed on reading is dropped and the key
// rewritten without it. Storage that is missing or throws (blocked site data, a full quota) leaves the page working from memory; a write that
// fails removes the key, so an older copy can never bring back a mark since saved or a note since dismissed. The session ending (Sign out, a
// 401) forgets both (walkRefusedReset.ts).
// Pure apart from the storage it is handed, so node --test can load it directly.
import type {WalkCode} from '../api/walkTypes';
import {addRefusal,clearRefused,isWalkCode,keepRefused,rekeyRefused,type RefusalNote,type RefusedMarks} from './walk.ts';
import {REFUSED_KEY,onRefusedReset,tabStorage,type RefusedStorage} from './walkRefusedReset.ts';

export {REFUSED_KEY,resetRefused,tabStorage,type RefusedStorage} from './walkRefusedReset.ts';
export interface RefusedState{marks:RefusedMarks;note:RefusalNote|null}

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

/** Writes the marks and the note; removes the key when both are empty. False when the storage refused: then the key is removed, so what a
 *  reload finds is nothing rather than an older state (a mark since saved, a note since dismissed); the page carries on from memory. */
export function writeRefused(state:RefusedState,storage:RefusedStorage|null=tabStorage()):boolean{
  if(!storage)return false;
  try{
    const value=stored(state);
    if(value===null)storage.removeItem(REFUSED_KEY);else storage.setItem(REFUSED_KEY,value);
    return true;
  }catch{
    try{storage.removeItem(REFUSED_KEY);}catch{/* storage withheld altogether: nothing can be read back from it either */}
    return false;
  }
}

/** A note's doors (person id -> "door (code)") as a plain object of its own keys: any person id, even "__proto__", stays a key. */
const doorsOf=(entries:Iterable<[string,string]>):Record<string,string>=>{
  const out:Record<string,string>={};
  for(const [id,door] of entries)Object.defineProperty(out,id,{value:door,enumerable:true,writable:true,configurable:true});
  return out;
};
/** The note with its doors renamed to the person ids of the list saved (movedIds), as the marks are. A renamed door replaces one already
 *  under the new id (the newer refusal). The same note when nothing is renamed. */
function renameDoors(note:RefusalNote|null,moved:ReadonlyMap<string,string>):RefusalNote|null{
  if(!note||!note.lists.some(l=>Object.keys(l.doors).some(id=>moved.has(id))))return note;
  const lists=note.lists.map(l=>{
    const doors=Object.entries(l.doors);
    return {title:l.title,doors:doorsOf([...doors.filter(([id])=>!moved.has(id)),...doors.filter(([id])=>moved.has(id)).map(([id,d]):[string,string]=>[moved.get(id)??id,d])])};
  });
  return {...note,lists};
}
/** The note without the doors of these voters; a list left with no doors goes, and a note with none left is null. The same note when nothing
 *  is taken out. */
function pruneDoors(note:RefusalNote|null,ids:readonly string[]):RefusalNote|null{
  if(!note||!ids.length)return note;
  const gone=new Set(ids);
  if(!note.lists.some(l=>Object.keys(l.doors).some(id=>gone.has(id))))return note;
  const lists=note.lists.map(l=>({title:l.title,doors:doorsOf(Object.entries(l.doors).filter(([id])=>!gone.has(id)))})).filter(l=>Object.keys(l.doors).length>0);
  return lists.length?{...note,lists}:null;
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
  /**
   * A list was saved under `key`: the marks refused while it was known by its query (`query`, listIdentity(null, query)) follow it, so the
   * list reopened by its key (as a reload does, the address naming the list) shows them flagged. In order: the person ids of the query's
   * marks are renamed to those of the list saved (`moved`, movedIds; the key's marks too, as before), then the query's marks move under the
   * key and the query bucket goes. A voter marked under both keeps the query's mark: it is the newer one, set on the list just shown. The
   * note's doors are renamed the same way, so a later save of those voters takes them out of the note.
   */
  adopt(query:string,key:string,moved:ReadonlyMap<string,string>|null):void;
  /** Voters whose results are now saved: their marks are no longer refused (the only way a mark leaves), and a voter left with no refused mark
   *  on any list leaves the note; a note with no door left goes, and with it the unload guard it holds up. One write. */
  clear(lists:readonly string[],ids:Iterable<string>):void;
  /** The walker dismissed the note. The marks stay: a walker's mark is never erased by the walker's own actions. */
  dismiss():void;
  /** The session ended (Sign out, a 401): the marks and the note are forgotten, in memory and in the tab's storage. */
  reset():void;
}
export function refusedStore(storage:RefusedStorage|null=tabStorage()):RefusedStore{
  const {marks,note:restored}=readRefused(storage);
  let note=restored;
  const write=()=>{writeRefused({marks,note},storage);};
  const reset=()=>{
    marks.clear();note=null;
    try{storage?.removeItem(REFUSED_KEY);}catch{/* storage withheld: nothing can be read back from it either */}
  };
  onRefusedReset(reset);   // resetRefused (Sign out, a 401 anywhere in the API client) empties this store too
  return {
    marks,
    get note(){return note;},
    hold(list,title,refused,doors,why,local=false){
      keepRefused(marks,list,refused);
      note=addRefusal(note,title,refused,doors,why,local);
      write();return note;
    },
    adopt(query,key,moved){
      let changed=false;
      if(moved?.size){
        for(const list of new Set([query,key]))if(marks.has(list)){rekeyRefused(marks,list,moved);changed=true;}
        const renamed=renameDoors(note,moved);
        if(renamed!==note){note=renamed;changed=true;}
      }
      const from=marks.get(query);
      if(from&&query!==key){
        const into=marks.get(key)??new Map<string,WalkCode|null>();
        for(const [id,code] of from)into.set(id,code);   // the query's mark wins over one already under the key
        marks.set(key,into);marks.delete(query);changed=true;
      }
      if(changed)write();
    },
    clear(lists,ids){
      const saved=[...ids];
      if(!saved.some(id=>lists.some(list=>marks.get(list)?.has(id))))return;
      for(const list of lists)clearRefused(marks,list,saved);
      // a voter refused on another list as well keeps the door in the note until that mark is saved too
      note=pruneDoors(note,saved.filter(id=>![...marks.values()].some(m=>m.has(id))));
      write();
    },
    dismiss(){note=null;write();},
    reset,
  };
}
