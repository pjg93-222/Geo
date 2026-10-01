import {useEffect,useRef,useState} from 'react';
import type {Household} from './StreetOverlay';
import {buildHouseholdIndex,HouseholdIndex} from './householdIndex';

type State={key:string;phase:'idle'|'loading'|'ready'|'error';received:number;total:number;people:number;data?:HouseholdIndex;error?:string};
const cache=new Map<string,State>();
const idle=(key:string):State=>({key,phase:'idle',received:0,total:0,people:0});

export function useHouseholdPreload(source:string,basis:string){
  const key=source+':'+basis;const [state,setState]=useState<State>(idle(key));
  const controller=useRef<AbortController|null>(null);
  useEffect(()=>{
    controller.current?.abort();
    for(const cached of cache.keys())if(!cached.startsWith(source+':'))cache.delete(cached);
    setState(cache.get(key)||idle(key));
    return()=>controller.current?.abort();
  },[key,source]);
  async function start(){
    if(!source)return;
    controller.current?.abort();const pending=new AbortController();controller.current=pending;
    setState({...idle(key),phase:'loading'});
    const rows:Household[]=[];let after='';let people=0;let expectedTotal:number|undefined;
    try{
      do{
        const response=await fetch('/api/street-preload?'+new URLSearchParams({basis,after,source}),{credentials:'same-origin',signal:pending.signal});
        const page=await response.json();if(!response.ok)throw new Error(page.error||'Household preload failed.');
        if(pending.signal.aborted)return;
        if(page.schema!=='household-preload-v1'||page.source_sha256!==source||page.basis!==basis||!Array.isArray(page.rows)||page.rows.length>5000||
           !Number.isInteger(page.total_households)||page.total_households<0||page.total_households>1000000||
           (expectedTotal!==undefined&&expectedTotal!==page.total_households))throw new Error('The household source changed during preload. Retry to load a consistent set.');
        expectedTotal=page.total_households;
        let last=after;
        for(const row of page.rows){
          if(!Array.isArray(row)||row.length!==5||typeof row[0]!=='string'||!/^h_[a-f0-9]{64}$/.test(row[0])||row[0]<=last||
             !Number.isFinite(row[1])||!Number.isFinite(row[2])||!Number.isInteger(row[3])||row[3]<1||typeof row[4]!=='string')throw new Error('An invalid household batch was returned. Retry the preload.');
          rows.push({id:row[0],longitude:row[1],latitude:row[2],count:row[3],address:row[4]});people+=row[3];last=row[0];
        }
        if(rows.length>page.total_households||(page.next!==null&&(page.next!==last||last<=after)))throw new Error('The household batch order changed. Retry the preload.');
        after=page.next||'';
        setState({key,phase:'loading',received:rows.length,total:page.total_households,people});
        if(!after){
          if(rows.length!==page.total_households||people!==page.total_people)throw new Error('The complete household count could not be verified. Retry the preload.');
          const ready:State={key,phase:'ready',received:rows.length,total:rows.length,people,data:buildHouseholdIndex(rows)};
          cache.set(key,ready);setState(ready);
        }
      }while(after);
    }catch(error){if(!pending.signal.aborted)setState({...idle(key),phase:'error',error:(error as Error).message});}
  }
  function cancel(){controller.current?.abort();setState(idle(key));}
  function unload(){controller.current?.abort();cache.delete(key);setState(idle(key));}
  return {...(state.key===key?state:cache.get(key)||idle(key)),start,cancel,unload};
}
