import {memo,useEffect,useRef,useState} from 'react';
import type {WalkOptInKind,WalkOptIns as OptIns,WalkOptInStatus} from '../api/walkTypes';
import {OPT_IN_KIND_LABEL,OPT_IN_KINDS,OPT_IN_LABEL,recordedOptIns} from '../lib/walkOptIns';

export type RecordWalkOptIn=(personId:string,kind:WalkOptInKind,status:WalkOptInStatus)=>Promise<void>;

export const WalkOptIns=memo(function WalkOptIns({personId,name,value,disabled=false,displayOnly=false,onRecord}:{personId:string;name:string;value?:OptIns;disabled?:boolean;displayOnly?:boolean;onRecord?:RecordWalkOptIn}){
  const states=recordedOptIns(value);
  const [pending,setPending]=useState<Partial<Record<WalkOptInKind,boolean>>>({});
  const [errors,setErrors]=useState<Partial<Record<WalkOptInKind,string>>>({});
  const mounted=useRef(true);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  async function record(kind:WalkOptInKind,status:WalkOptInStatus){
    if(!onRecord||displayOnly||disabled)return;
    setPending(p=>({...p,[kind]:true}));setErrors(p=>({...p,[kind]:''}));
    try{await onRecord(personId,kind,status);}
    catch(error){if(mounted.current&&!(error instanceof Error&&error.name==='AbortError'))setErrors(p=>({...p,[kind]:error instanceof Error?error.message:'The opt-in could not be saved.'}));}
    finally{if(mounted.current)setPending(p=>({...p,[kind]:false}));}
  }
  return <div className="walk-opt-ins" aria-label={`Recorded opt-ins for ${name}`}>
    {OPT_IN_KINDS.map(kind=>{
      const state=states[kind],date=state.recorded_at?new Date(state.recorded_at):null;
      return <div key={kind} className="walk-opt-in">
        <span className={`walk-opt-in-badge is-${state.status}`}>{OPT_IN_KIND_LABEL[kind]}: {OPT_IN_LABEL[state.status]}</span>
        <span className="walk-opt-in-source">{state.source==='recorded_manually'
          ?<>Recorded manually · {date&&Number.isFinite(date.getTime())?<time dateTime={state.recorded_at!}>{date.toLocaleString()}</time>:'Date unavailable'}</>
          :'No recorded opt-in'}</span>
        {!displayOnly&&onRecord&&<label className="walk-opt-in-edit"><span>Record {OPT_IN_KIND_LABEL[kind].toLowerCase()} opt-in</span>
          <select aria-label={`Record ${OPT_IN_KIND_LABEL[kind].toLowerCase()} opt-in for ${name}`} value={state.status} disabled={disabled||pending[kind]===true}
            onChange={event=>void record(kind,event.target.value as WalkOptInStatus)}>
            {(['unknown','opted_in','opted_out'] as const).map(status=><option key={status} value={status}>{OPT_IN_LABEL[status]}</option>)}
          </select>
        </label>}
        {pending[kind]&&<span className="walk-opt-in-source" role="status">Saving…</span>}
        {errors[kind]&&<span className="walk-opt-in-error" role="alert">Not saved: {errors[kind]}</span>}
      </div>;
    })}
  </div>;
});
