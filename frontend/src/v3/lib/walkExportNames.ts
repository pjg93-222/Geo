import type {WalkListPayload} from '../api/walkTypes';
import {csvValue} from './csv.ts';

export interface WalkExportName{list:string;side:string;precinct:string;stem:string}
const alphabet=(index:number):string=>{let out='';for(let n=index+1;n>0;n=Math.floor((n-1)/26))out=String.fromCharCode(65+(n-1)%26)+out;return out;};
const safeName=(text:string):string=>text.replace(/[<>:"/\\|?*\x00-\x1f]/g,'-').replace(/[. ]+$/g,'').slice(0,90)||'Unassigned';
const sideOf=(sheet:WalkListPayload):string=>{
  if(sheet.query.split_sides==='0')return 'Both';
  const sides=[...new Set(sheet.runs.map(run=>run.side))];
  return sides.length===1&&sides[0]==='even'?'Even':sides.length===1&&sides[0]==='odd'?'Odd':'Both';
};

/** Precinct letters are stable across Even and Odd, with numbered parts only when a cap splits that group. */
export function walkExportNames(sheets:WalkListPayload[]):WalkExportName[]{
  const imported=sheets.some(sheet=>!!sheet.query.import_group);
  const groupKeys=[...new Set(sheets.map(sheet=>sheet.query.import_group||''))];
  const precincts=[...new Set(sheets.map(sheet=>sheet.query.precinct||''))].sort((a,b)=>a.localeCompare(b,'en',{numeric:true}));
  const actual=[...new Set(sheets.flatMap(sheet=>sheet.runs.map(run=>run.precinct).filter(Boolean)))];
  const names=sheets.map(sheet=>{
    const listed=[...new Set(sheet.runs.map(run=>run.precinct).filter(Boolean))];
    return {list:alphabet(imported?groupKeys.indexOf(sheet.query.import_group||''):precincts.indexOf(sheet.query.precinct||'')),side:sideOf(sheet),precinct:sheet.query.precinct||((imported?listed:actual).length===1?(imported?listed:actual)[0]:'')};
  });
  const counts=new Map<string,number>(),seen=new Map<string,number>();
  for(const item of names){const key=`${item.list}|${item.side}`;counts.set(key,(counts.get(key)??0)+1);}
  return names.map((item,index)=>{
    const key=`${item.list}|${item.side}`,part=(seen.get(key)??0)+1;seen.set(key,part);
    const label=imported?safeName(sheets[index].query.import_group_label||`Local area ${item.list}`):item.precinct?`Precinct ${safeName(item.precinct)}`:actual.length?'Multiple precincts':'Unassigned precinct';
    return {...item,stem:`${item.side}/${item.list} - ${label} - ${item.side}${counts.get(key)!>1?` - Part ${part}`:''}`};
  });
}

export function walkSummaryCsv(sheets:WalkListPayload[],names=walkExportNames(sheets)):string{
  const rows:unknown[][]=[['List','Side','Precinct','Streets','Doors','Voters','File','Street names']];
  sheets.forEach((sheet,i)=>rows.push([names[i].list,names[i].side,names[i].precinct,sheet.totals.streets,sheet.totals.doors,sheet.totals.voters,
    `${names[i].stem}.pdf`,[...new Set(sheet.runs.map(run=>run.street))].sort((a,b)=>a.localeCompare(b)).join('; ')]));
  return '\uFEFF'+rows.map(row=>row.map(value=>'"'+csvValue(value).replace(/[\r\n]+/g,' ').replace(/"/g,'""')+'"').join(',')).join('\r\n')+'\r\n';
}
