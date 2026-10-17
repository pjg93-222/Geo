import type {WalkListPayload,WalkRun} from '../api/walkTypes';
import type {WalkHistory,WalkResults,WalkScoreMap} from './walk';
import {buildWalkCsv,needsAddressLocality} from './walk.ts';
import {hasEvidence} from './walkEvidence.ts';
import {cleanLine,doorPoints,growBounds,walkRouteDistance} from './walkGeo.ts';

/** Blank means no cap. Invalid values are rejected instead of silently changing the request. */
export function doorCap(text:string):number|null{
  if(!text.trim())return null;
  if(!/^\d+$/.test(text.trim())||Number(text)<1||Number(text)>10000)throw new Error('Doors per list must be a whole number from 1 to 10,000, or blank for no cap.');
  return Number(text);
}

/** Partition complete doors in route order. Voters at a door always stay together. */
export function splitWalkSheets(payload:WalkListPayload,cap:number|null):WalkListPayload[]{
  if(cap!==null&&(!Number.isInteger(cap)||cap<1))throw new Error('Invalid door cap.');
  const splitSides=payload.query.split_sides!=='0';
  const sides=[...new Set(payload.runs.map(r=>r.side))];
  if((cap===null||payload.totals.doors<=cap)&&(!splitSides||sides.length<=1))return [payload];
  const limit=cap??Math.max(payload.totals.doors,1);
  const sources=splitSides?['even','odd','both'].map(side=>payload.runs.filter(r=>r.side===side)).filter(r=>r.length):[payload.runs];
  const groups:WalkRun[][]=[],sourceIndex=new Map(payload.runs.map((run,index)=>[run,index]));let runs:WalkRun[]=[],count=0,lastIndex=-1;
  const finish=()=>{if(runs.length)groups.push(runs);runs=[];count=0;lastIndex=-1;};
  for(const source of sources){for(const run of source){
    for(let at=0;at<run.doors.length;){
      const doors=run.doors.slice(at,at+limit-count);at+=doors.length;
      const currentIndex=sourceIndex.get(run)!;
      const contiguous=runs.length>0&&at===doors.length&&lastIndex===currentIndex-1;
      runs.push({...run,stop:runs.length+1,doors,approach:contiguous?run.approach:[],approach_m:contiguous?run.approach_m:0,from_house:doors[0].house,to_house:doors.at(-1)!.house});count+=doors.length;lastIndex=currentIndex;
      if(count===limit)finish();
    }
  }finish();}
  return groups.map((part,i)=>{
    const bounds=growBounds(null,part.flatMap(r=>[...cleanLine(r.path),...cleanLine(r.approach),...doorPoints(r)]));
    return {...payload,fingerprint:undefined,list:{...payload.list,title:payload.query.imported==='1'?'Walk list':`${payload.list.title||'Walk list'} · List ${i+1} of ${groups.length}`},runs:part,
    bbox:bounds??[0,0,0,0],context:bounds&&payload.context?{...payload.context,bbox:bounds}:undefined,
    totals:{streets:new Set(part.map(r=>r.street_id)).size,stops:part.length,doors:part.reduce((n,r)=>n+r.doors.length,0),
      voters:part.reduce((n,r)=>n+r.doors.reduce((m,d)=>m+d.voters.length,0),0),walk_m:walkRouteDistance(part)}} as WalkListPayload;
  });
}

/** One download includes every sheet and identifies each row's list. */
export function buildWalkSheetsCsv(sheets:WalkListPayload[],results?:WalkResults,history?:WalkHistory,unsaved?:ReadonlySet<string>,scores?:WalkScoreMap):string{
  if(sheets.length===1)return buildWalkCsv(sheets[0],results,history,unsaved,scores);
  const includeLocality=sheets.some(needsAddressLocality);
  const includeEvidence=sheets.some(hasEvidence);
  const rows=sheets.map(p=>buildWalkCsv(p,results,history,unsaved,scores,includeLocality,includeEvidence).replace(/^\uFEFF/,'').trimEnd().split('\r\n'));
  return '\uFEFF'+['"List",'+rows[0][0],...rows.flatMap((lines,i)=>lines.slice(1).map(line=>`"${i+1}",${line}`))].join('\r\n')+'\r\n';
}
