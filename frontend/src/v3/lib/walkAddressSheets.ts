import type {ImportedWalkAddress,ImportSource} from './walkAddressImport';
import type {WalkContext,WalkListPayload,WalkRun,WalkSidePick} from '../api/walkTypes';
import {csvValue} from './csv.ts';
import {growBounds,usableBounds,walkRouteDistance} from './walkGeo.ts';

export interface ImportedWalkSelection {
  source:ImportSource;payload:WalkListPayload;personIds:readonly string[];
  streetId:number;city:string;state:string;zip:string;
}
export interface ImportedWalkList {payload:WalkListPayload;addresses:ImportedWalkAddress[];warnings:string[]}

/** Keep recorded household rows, including their precinct and people, for each explicit source request. */
export function buildImportedWalkList(exact:ImportedWalkAddress[],selections:ImportedWalkSelection[],options:{sourceName:string;splitSides:boolean;date?:string}):ImportedWalkList{
  const runs:WalkRun[]=[],addresses:ImportedWalkAddress[]=[],warnings:string[]=[];
  const parity=(house:string):WalkSidePick=>{const number=/^\d+/.exec(house);return options.splitSides&&number?(Number(number[0])%2?'odd':'even'):'both';};
  const append=(run:WalkRun,rows:ImportedWalkAddress[])=>{
    const doors=run.doors.map((door,index)=>{addresses.push(rows[index]);return {...door,seq:addresses.length,voters:door.voters.map(voter=>({...voter}))};});
    if(doors.length)runs.push({...run,stop:runs.length+1,doors,from_house:doors[0].house,to_house:doors.at(-1)!.house});
  };
  // Supplied exact addresses have no verified person or coordinate match. Keep them printable without inventing either.
  const exactGroups=new Map<string,{run:WalkRun;rows:ImportedWalkAddress[]}>();
  for(const address of exact){
    const side=parity(address.house),key=JSON.stringify([address.street,address.city,address.state,address.zip,side]);
    let group=exactGroups.get(key);
    if(!group){group={run:{stop:0,street_id:-exactGroups.size-1,street:address.street,side,precinct:'',locality:[address.city,address.state,address.zip].filter(Boolean).join(' '),from_house:'',to_house:'',walk_m:0,lr:0,path:[],approach:[],doors:[]},rows:[]};exactGroups.set(key,group);}
    group.run.doors.push({seq:0,house:address.house,unit:address.unit,placed_by:'unlocated',lon:null,lat:null,voters:[],city:address.city,state:address.state,zip:address.zip,address:address.address});group.rows.push({...address});
  }
  for(const {run,rows} of exactGroups.values())append(run,rows);
  const payloads=[...new Set(selections.map(selection=>selection.payload))];
  const memberships=selections.map(selected=>{
    const wanted=new Set(selected.personIds);
    if(!wanted.size||wanted.size!==selected.personIds.length)throw new Error('The recorded street selection has missing or repeated person identifiers. Recheck the street matches.');
    return {selected,wanted,seen:new Set<string>()};
  });
  let previous:{payload:WalkListPayload;runIndex:number;lastSeq:number}|null=null;
  // Keep each backend route's ordering. Source-file ordering must not replace the calculated walk order.
  const byPayload=new Map(payloads.map(payload=>[payload,memberships.filter(membership=>membership.selected.payload===payload)]));
  for(const payload of payloads)for(const [runIndex,original] of payload.runs.entries())for(const membership of byPayload.get(payload)!){
    const {selected,wanted,seen}=membership;
    const doors=original.doors.flatMap(door=>{
      const voters=door.voters.filter(voter=>wanted.has(voter.person_id));if(!voters.length)return [];
      if(original.street_id!==selected.streetId)throw new Error('A recorded person belongs to a different street. Recheck the street matches.');
      for(const voter of voters){if(seen.has(voter.person_id))throw new Error('The recorded street contains a repeated person. Recheck the street matches.');seen.add(voter.person_id);}
      return [{...door,voters}];
    });
    if(!doors.length)continue;
    const groups=new Map<WalkSidePick,typeof doors>();
    for(const door of doors){const side=parity(door.house);const group=groups.get(side)??[];group.push(door);groups.set(side,group);}
    for(const [side,group] of groups){
      const rows=group.map(door=>{
        const notes=[...(door.warnings??[])];if(!door.house&&!notes.includes('No house number recorded.'))notes.push('No house number recorded.');
        const city=door.city??selected.city,state=door.state??selected.state,zip=door.zip??selected.zip;
        const address=door.address??[`${door.house} ${original.street}${door.unit?` Apt ${door.unit}`:''}`.trim(),city,[state,zip].filter(Boolean).join(' ')].filter(Boolean).join(', ');
        if(notes.length)warnings.push(`${selected.source.sourceCell||`Row ${selected.source.sourceRow}`} - ${address}: ${notes.join(' ')}`);
        return {...selected.source,house:door.house,unit:door.unit,street:original.street,city,state,zip,address,note:[selected.source.note,...notes].filter(Boolean).join(' ')};
      });
      const preceding=previous?.payload===payload&&previous.runIndex===runIndex-1&&previous.lastSeq===payload.runs[runIndex-1]?.doors.at(-1)?.seq&&group[0].seq===original.doors[0]?.seq;
      append({...original,side,lr:options.splitSides?original.lr:0,approach:preceding?original.approach:[],approach_m:preceding?original.approach_m:0,doors:group},rows);
      previous={payload,runIndex,lastSeq:group.at(-1)!.seq};
    }
  }
  for(const {seen,wanted} of memberships)if(seen.size!==wanted.size)throw new Error('Some recorded people were missing from the returned street list. No partial sheets were created.');
  let bbox:WalkListPayload['bbox']|null=null;
  for(const payload of payloads){const box=usableBounds(payload.bbox);if(box)bbox=growBounds(bbox,[[box[0],box[1]],[box[2],box[3]]]);}
  const contexts=payloads.flatMap(payload=>payload.context?[payload.context]:[]);
  const unique=<T,>(rows:T[])=>[...new Map(rows.map(row=>[JSON.stringify(row),row])).values()];
  const context:WalkContext|undefined=contexts.length&&bbox?{bbox,roads:unique(contexts.flatMap(value=>value.roads)),precincts:unique(contexts.flatMap(value=>value.precincts)),homes:unique(contexts.flatMap(value=>value.homes))}:undefined;
  const first=payloads[0];
  const payload:WalkListPayload={schema:'walk-list-1',generated_on:options.date??first?.generated_on??new Date().toISOString().slice(0,10),list:{key:null,title:'Walk list',kind:'standard',created_at:null},query:{split_sides:options.splitSides?'1':'0',imported:'1'},window:first?.window??{primaries:[],generals:[],abs_rule:''},bbox:bbox??[0,0,0,0],runs,...context?{context}:{},totals:{streets:new Set(runs.map(run=>run.street_id)).size,stops:runs.length,doors:addresses.length,voters:runs.reduce((sum,run)=>sum+run.doors.reduce((n,door)=>n+door.voters.length,0),0),walk_m:walkRouteDistance(runs)}};
  return {payload,addresses,warnings};
}

export type AddressSheetRow=ImportedWalkAddress&{side:'even'|'odd'|'unknown'|'both'};
export function splitAddressSheets(addresses:ImportedWalkAddress[],cap:number|null,splitSides:boolean):AddressSheetRow[][]{
  const rows:AddressSheetRow[]=addresses.map(a=>({...a,side:splitSides?( /^\d+/.test(a.house)?Number(/^\d+/.exec(a.house)![0])%2?'odd':'even':'unknown'):'both'}));
  const compare=new Intl.Collator('en',{numeric:true,sensitivity:'base'});
  rows.sort((a,b)=>compare.compare([a.city,a.state,a.zip,a.street].join('|'),[b.city,b.state,b.zip,b.street].join('|'))||(splitSides?compare.compare(a.side,b.side):0)||compare.compare(a.house,b.house)||compare.compare(a.unit,b.unit)||a.sourceRow-b.sourceRow);
  const size=cap??Math.max(rows.length,1);
  if(!Number.isInteger(size)||size<1)throw new Error('Invalid door cap.');
  const sheets:AddressSheetRow[][]=[];
  const groups=splitSides?['even','odd','unknown'].map(side=>rows.filter(row=>row.side===side)).filter(group=>group.length):[rows];
  for(const group of groups)for(let at=0;at<group.length;at+=size)sheets.push(group.slice(at,at+size));
  return sheets;
}

export function addressSheetsCsv(sheets:AddressSheetRow[][],listOffset=0):string{
  const rows:unknown[][]=[['List','Door','Address','Unit','Street','City','State','ZIP','Side','Result','Notes','Source file','Source sheet','Source row','Source cell','Source text']];
  sheets.forEach((sheet,i)=>sheet.forEach((a,j)=>rows.push([i+1+listOffset,j+1,a.address,a.unit,a.street,a.city,a.state,a.zip,a.side==='both'?'Both':a.side,'',a.note??'',a.sourceFile??'',a.sourceSheet??'',a.sourceRow,a.sourceCell??'',a.sourceText??''])));
  return '\uFEFF'+rows.map(row=>row.map(v=>'"'+csvValue(v).replace(/[\r\n]+/g,' ').replace(/"/g,'""')+'"').join(',')).join('\r\n')+'\r\n';
}
