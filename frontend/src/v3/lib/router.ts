// History router for the map, people/profiles, contacts, audit, sources, workspace, walk list and method.
import {useEffect,useState} from 'react';
import {rememberView} from './savedViews';

export type RouteName='map'|'people'|'profile'|'method'|'contacts'|'criteria'|'sources'|'workspace'|'walk';
export interface Route{name:RouteName;pid:string;search:URLSearchParams}

const KEEP:string[]=[];

export function parseRoute(loc:Location=location):Route{
  const search=new URLSearchParams(loc.search);
  const path=loc.pathname.replace(/\/+$/,'')||'/';
  if(path==='/people')return {name:'people',pid:'',search};
  const m=/^\/people\/([^/]+)$/.exec(path);
  if(m){let pid=m[1];try{pid=decodeURIComponent(pid);}catch{/* invalid URI becomes an unknown person id */}return {name:'profile',pid,search};}
  if(path==='/method')return {name:'method',pid:'',search};
  for(const name of ['contacts','criteria','sources','workspace','walk'] as const){
    if(path==='/'+name)return {name,pid:'',search};
  }
  return {name:'map',pid:'',search};
}

export function withKept(to:string):string{
  const here=new URLSearchParams(location.search);
  const [path,qs=''] = to.split('?');
  const next=new URLSearchParams(qs);
  for(const k of KEEP){const v=here.get(k);if(v&&!next.has(k))next.set(k,v);}
  const s=next.toString();
  return path+(s?'?'+s:'');
}

const EVENT='v3:navigate';
export const LOCATION_EVENT='v3:location';
export function navigate(to:string,opts:{replace?:boolean}={}):void{
  rememberView(location.pathname+location.search);
  const url=withKept(to);
  if(opts.replace)history.replaceState(null,'',url);else history.pushState(null,'',url);
  window.dispatchEvent(new Event(LOCATION_EVENT));
  window.dispatchEvent(new Event(EVENT));
}

export function useRoute():Route{
  const [route,setRoute]=useState<Route>(()=>parseRoute());
  useEffect(()=>{
    const on=()=>setRoute(parseRoute());
    rememberView(location.pathname+location.search);
    window.addEventListener('popstate',on);window.addEventListener(EVENT,on);
    return ()=>{window.removeEventListener('popstate',on);window.removeEventListener(EVENT,on);};
  },[]);
  return route;
}

/** Keep the query of the current URL in sync without a navigation event (map state). */
export function replaceQuery(params:Record<string,string|null>):void{
  const q=new URLSearchParams(location.search);
  for(const [k,v] of Object.entries(params)){if(v===null||v==='')q.delete(k);else q.set(k,v);}
  const s=q.toString();
  history.replaceState(null,'',location.pathname+(s?'?'+s:''));
  window.dispatchEvent(new Event(LOCATION_EVENT));
  rememberView(location.pathname+location.search);
}
