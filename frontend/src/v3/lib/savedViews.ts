/** Local preview bookmarks. Only internal Terrier routes are accepted. */
export interface SavedView{id:string;name:string;path:string;savedAt:string}
export interface ViewStorage{getItem(key:string):string|null;setItem(key:string,value:string):void}
const KEY='terrier:saved-views:v1';
const LAST='terrier:last-view:v1';
const PATH=/^(?:\/|\/people(?:\/[^/]+)?|\/contacts|\/criteria|\/sources|\/method)$/;

export function safeViewPath(value:unknown):string|null{
  if(typeof value!=='string'||value.length>2048||!value.startsWith('/')||value.startsWith('//')||/[\\\r\n]/.test(value))return null;
  try{
    const url=new URL(value,'https://terrier.invalid');
    if(url.origin!=='https://terrier.invalid'||!PATH.test(url.pathname))return null;
    url.searchParams.delete('f'); // A transient map search event is not part of a saved view.
    return url.pathname+url.search;
  }catch{return null;}
}

export function readSavedViews(storage:ViewStorage=localStorage):SavedView[]{
  const raw=storage.getItem(KEY);if(!raw)return [];
  const items:unknown=JSON.parse(raw);
  if(!Array.isArray(items))throw new Error('Saved views could not be read.');
  const ids=new Set<string>();
  return items.flatMap((value):SavedView[]=>{
    if(!value||typeof value!=='object')return [];
    const row=value as Partial<SavedView>;const path=safeViewPath(row.path);
    if(typeof row.id!=='string'||!row.id||ids.has(row.id)||typeof row.name!=='string'||!row.name.trim()||!path||typeof row.savedAt!=='string'||!Number.isFinite(Date.parse(row.savedAt)))return [];
    ids.add(row.id);
    return [{id:row.id,name:row.name.trim().slice(0,100),path,savedAt:row.savedAt}];
  }).slice(0,100);
}

export function saveView(name:string,path:string,storage:ViewStorage=localStorage):SavedView[]{
  const safe=safeViewPath(path);const title=name.trim();
  if(!safe||!title||title.length>100)throw new Error('Choose a Terrier page and a name of 1–100 characters.');
  const previous=readSavedViews(storage);
  if(previous.length>=100)throw new Error('You have 100 saved views. Remove one before saving another.');
  const views=[{id:crypto.randomUUID(),name:title,path:safe,savedAt:new Date().toISOString()},...previous];
  storage.setItem(KEY,JSON.stringify(views));return views;
}

export function removeView(id:string,storage:ViewStorage=localStorage):SavedView[]{
  const views=readSavedViews(storage).filter(v=>v.id!==id);
  storage.setItem(KEY,JSON.stringify(views));return views;
}

export function rememberView(path:string,storage?:ViewStorage):void{
  const safe=safeViewPath(path);if(!safe)return;
  try{(storage??localStorage).setItem(LAST,safe);}catch{/* Browsing still works when local storage is unavailable. */}
}

export function lastView(storage?:ViewStorage):string{
  try{return safeViewPath((storage??localStorage).getItem(LAST))??'/';}catch{return '/';}
}
