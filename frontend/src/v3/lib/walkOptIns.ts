import type {WalkListPayload,WalkOptInEntry,WalkOptInKind,WalkOptIns,WalkOptInState,WalkOptInStatus} from '../api/walkTypes';

export const OPT_IN_KINDS:WalkOptInKind[]=['donor','volunteer'];
export const OPT_IN_LABEL:Record<WalkOptInStatus,string>={unknown:'Unknown',opted_in:'Opted in',opted_out:'Opted out'};
export const OPT_IN_KIND_LABEL:Record<WalkOptInKind,string>={donor:'Donor',volunteer:'Volunteer'};
const unknownState=():WalkOptInState=>({status:'unknown',recorded_at:null,source:null});

/** Only explicit manual records count. Missing states, vendor fields and unrecognized sources never imply an opt-in. */
export function recordedOptIns(value:unknown):WalkOptIns{
  const read=(kind:WalkOptInKind):WalkOptInState=>{
    if(!value||typeof value!=='object'||!(kind in value))return unknownState();
    const state=(value as Record<string,unknown>)[kind];
    if(!state||typeof state!=='object')return unknownState();
    const raw=state as Record<string,unknown>;
    if(raw.source!=='recorded_manually'||!['unknown','opted_in','opted_out'].includes(String(raw.status)))return unknownState();
    return {status:raw.status as WalkOptInStatus,recorded_at:typeof raw.recorded_at==='string'?raw.recorded_at:null,source:'recorded_manually'};
  };
  return {donor:read('donor'),volunteer:read('volunteer')};
}
export function hasRecordedOptIn(value:unknown):boolean{
  const states=recordedOptIns(value);
  return OPT_IN_KINDS.some(kind=>states[kind].status==='opted_in');
}
export function optInEntry(personId:string,kind:WalkOptInKind,status:WalkOptInStatus):WalkOptInEntry{
  return {person_id:personId,[kind]:status};
}
/** Responses contain both kinds; merge only the kind this request wrote, so concurrent edits cannot clobber each other. */
export function patchRecordedOptIn(payload:WalkListPayload,personId:string,kind:WalkOptInKind,state:WalkOptInState):WalkListPayload{
  return {...payload,runs:payload.runs.map(run=>({...run,doors:run.doors.map(door=>({...door,voters:door.voters.map(voter=>
    voter.person_id===personId?{...voter,opt_ins:{...recordedOptIns(voter.opt_ins),[kind]:state}}:voter)}))}))};
}

/** A new list/district or an unmount invalidates pending replies, even if fetch ignores an abort after the server accepted a write. */
export function optInWriteScope(){
  let epoch=0;
  const requests=new Map<string,AbortController>();
  return {
    reset(){epoch++;for(const request of requests.values())request.abort();requests.clear();},
    pending(){return requests.size>0;},
    begin(personId:string,kind:WalkOptInKind){
      const key=JSON.stringify([personId,kind]),generation=epoch,controller=new AbortController();
      requests.get(key)?.abort();requests.set(key,controller);
      return {signal:controller.signal,current:()=>epoch===generation&&requests.get(key)===controller&&!controller.signal.aborted,
        finish:()=>{if(requests.get(key)===controller)requests.delete(key);}};
    },
  };
}
