// The tab's key for refused walk-list results, and forgetting them on Sign out. Kept apart from walkRefused.ts, which needs the walk list
// helpers, so sign-out can forget the refusals without loading the walk screen's code. A 401 (most often the same walker's session timing
// out mid-canvass) does NOT forget them: they are the walker's only copy of those results, and a walker's mark is never erased. Otherwise
// they go when the tab closes.
// Pure apart from the storage it is handed, so node --test can load it directly.

/** One versioned key of this tab's sessionStorage: the refused marks and the refusal note (lib/walkRefused.ts). */
export const REFUSED_KEY='terrier.walk.refused.v1';
export interface RefusedStorage{getItem(key:string):string|null;setItem(key:string,value:string):void;removeItem(key:string):void}

/** This tab's sessionStorage, or null where the browser withholds it (reading the property itself can throw). */
export function tabStorage():RefusedStorage|null{
  try{return typeof sessionStorage==='undefined'?null:sessionStorage;}catch{return null;}
}

const listeners=new Set<()=>void>();
/** Called when the refusals are forgotten: a loaded refusal store empties its memory, a walk screen on show drops what it shows of them.
 *  Answers with a function that stops listening. */
export function onRefusedReset(listener:()=>void):()=>void{
  listeners.add(listener);
  return ()=>{listeners.delete(listener);};
}
/** Sign out: every loaded refusal store is emptied and the tab's key is removed, so a later sign-in in this tab never sees the refused marks
 *  or the note of the session before. Never called for a 401. Never throws. */
export function resetRefused(storage:RefusedStorage|null=tabStorage()):void{
  for(const listener of [...listeners]){try{listener();}catch{/* a listener that fails does not stop the others, or the removal */}}
  try{storage?.removeItem(REFUSED_KEY);}catch{/* storage withheld: nothing can be read back from it either */}
}
