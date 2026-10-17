import type {ImportSource,ImportedWalkAddress} from './walkAddressImport';
import type {ImportedWalkImage} from './walkImageImport';
import {buildImportedWalkList} from './walkAddressSheets.ts';
import type {ImportedWalkSelection} from './walkAddressSheets';
import type {WalkListPayload} from '../api/walkTypes';

export interface ImportSection<T extends ImportSource=ImportSource>{key:string;label:string;imageId?:string;sources:T[];exact:ImportedWalkAddress[]}
export interface ImportedLocalGroup{key:string;sectionKey:string;label:string;reason:string;selections:ImportedWalkSelection[]}
const sameSource=(a:ImportSource,b:ImportSource)=>a.sourceFile===b.sourceFile&&a.sourceSheet===b.sourceSheet;

/** Source bands organize the file; they never establish coordinates or a street match. */
export function importSections<T extends ImportSource>(sources:T[],exact:ImportedWalkAddress[],images:ImportedWalkImage[]=[]):ImportSection<T>[] {
  const buckets=new Map<string,{rows:T[];imageId:string}>();
  for(const source of sources){
    const imagesHere=images.filter(image=>source.sourceImageId===image.id||!source.sourceImageId&&image.sourceFile===source.sourceFile&&image.sourceSheet===source.sourceSheet&&image.anchor&&source.sourceRow>=image.anchor.fromRow&&source.sourceRow<=image.anchor.toRow);
    const imageKey=source.sourceImageId??(imagesHere.length===1?imagesHere[0].id:'');
    const key=JSON.stringify([source.sourceFile??'',source.sourceSheet??'',imageKey,imageKey?'':source.sourceColumn??'']);
    const bucket=buckets.get(key)??{rows:[],imageId:imageKey};bucket.rows.push(source);buckets.set(key,bucket);
  }
  const sections:ImportSection<T>[]=[];
  for(const {rows,imageId} of buckets.values()){
    const ordered=[...rows].sort((a,b)=>a.sourceRow-b.sourceRow),bands:T[][]=[];
    for(const row of ordered){const current=bands.at(-1);if(current&&(imageId||row.sourceRow<=current.at(-1)!.sourceRow+1))current.push(row);else bands.push([row]);}
    for(const band of bands){const first=band[0],last=band.at(-1)!;sections.push({key:`section-${sections.length+1}`,label:[first.sourceSheet,first.sourceImageId?'Image text':`Rows ${first.sourceRow}${last.sourceRow!==first.sourceRow?`-${last.sourceRow}`:''}`].filter(Boolean).join(' · '),...(imageId?{imageId}:{}),sources:band,exact:[]});}
  }
  for(const row of exact){
    // A heading address stays with the nearest source band on its worksheet, without a map point.
    const candidates=sections.filter(section=>row.sourceImageId?section.imageId===row.sourceImageId:section.sources.some(source=>sameSource(source,row)));
    const nearest=candidates.sort((a,b)=>Math.min(...a.sources.map(source=>Math.abs(source.sourceRow-row.sourceRow)))-Math.min(...b.sources.map(source=>Math.abs(source.sourceRow-row.sourceRow))))[0];
    if(nearest)nearest.exact.push(row);
    else{
      const location=JSON.stringify([row.sourceImageId,row.sourceFile,row.sourceSheet,row.city,row.state,row.zip]);
      let section=sections.find(section=>!section.sources.length&&section.key===`exact:${location}`);
      if(!section){section={key:`exact:${location}`,label:[row.sourceSheet,row.city||'Supplied addresses'].filter(Boolean).join(' · '),...(row.sourceImageId?{imageId:row.sourceImageId}:{}),sources:[],exact:[]};sections.push(section);}
      section.exact.push(row);
    }
  }
  return sections;
}

/** Adapt each verified local route separately, retaining every explicit source occurrence. */
export function prepareImportedGroups(sections:ImportSection[],localGroups:ImportedLocalGroup[],options:{sourceName:string;splitSides:boolean;date?:string}){
  const payloads:WalkListPayload[]=[],addresses:ImportedWalkAddress[]=[],warnings:string[]=[];
  const primary:ReturnType<typeof buildImportedWalkList>[]=[],additional:ReturnType<typeof buildImportedWalkList>[]=[];
  for(const section of sections){
    const groups=localGroups.filter(group=>group.sectionKey===section.key);
    const assigned=new Map<ImportedLocalGroup,ImportedWalkAddress[]>(groups.map(group=>[group,[]]));
    const separate:ImportedWalkAddress[]=[];
    for(const row of section.exact){
      const matching=row.city?groups.filter(group=>group.selections.some(selection=>selection.city.trim().toLowerCase()===row.city.trim().toLowerCase()&&(!row.state||selection.state.toLowerCase()===row.state.toLowerCase()))):groups;
      const group=matching[0];if(group)assigned.get(group)!.push(row);else separate.push(row);
    }
    const entries=groups.map(group=>({key:group.key,label:group.label,selected:group.selections,exact:assigned.get(group)!}));
    if(separate.length)entries.push({key:`${section.key}:exact`,label:`${section.label} · Supplied addresses`,selected:[],exact:separate});
    for(const [entryIndex,entry] of entries.entries()){
      const built=buildImportedWalkList(entry.exact,entry.selected,options);
      if(!built.payload.totals.doors)continue;
      built.payload.query={...built.payload.query,imported:'1',import_group:entry.key,import_group_label:entry.label};
      built.payload.list={...built.payload.list,title:'Walk list'};
      (entryIndex===0?primary:additional).push(built);
    }
  }
  for(const built of [...primary,...additional]){
    const offset=addresses.length;for(const run of built.payload.runs)for(const door of run.doors)door.seq+=offset;
    payloads.push(built.payload);addresses.push(...built.addresses);warnings.push(...built.warnings);
  }
  return {payloads,addresses,warnings,voters:payloads.reduce((sum,payload)=>sum+payload.totals.voters,0)};
}
