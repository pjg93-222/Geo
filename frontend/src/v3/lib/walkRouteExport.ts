import type {WalkListPayload} from '../api/walkTypes';
import type {WalkHistory,WalkResults,WalkScoreMap} from './walk';
import {buildWalkCsv,walkFileName} from './walk';
import {buildWalkSheetsCsv} from './walkSheets';
import {buildWalkPdf} from './walkPdf';
import {ZipBundle,downloadZip,yieldExport,checkZipFileCount} from './walkZip';
import {walkExportNames,walkSummaryCsv} from './walkExportNames';

export interface WalkZipOptions{area:string;scores?:WalkScoreMap;watermark?:string;includeCsv:boolean;results:WalkResults;history:WalkHistory;unsaved:ReadonlySet<string>;provenance?:Record<string,unknown>;notes?:string[];originalLayout?:boolean}

export async function buildWalkZip(sheets:WalkListPayload[],opts:WalkZipOptions):Promise<Uint8Array<ArrayBuffer>>{
  if(!sheets.length)throw new Error('No walk lists to export.');
  checkZipFileCount(sheets.length*(opts.includeCsv?2:1)+(opts.includeCsv?5:3));
  const bundle=new ZipBundle(),names=walkExportNames(sheets);
  for(const [i,sheet] of sheets.entries()){
    await yieldExport();
    const {stem}=names[i];
    const originalLayout=opts.originalLayout??sheet.query.imported==='1';
    const {doc}=await buildWalkPdf(originalLayout?{...sheet,list:{...sheet.list,title:'Walk list'}}:sheet,{area:originalLayout?'':`${opts.area} · List ${i+1} of ${sheets.length}`,scores:opts.scores,watermark:opts.watermark});
    bundle.add({name:`${stem}.pdf`,data:new Uint8Array(doc.output('arraybuffer'))});
    if(opts.includeCsv)bundle.add({name:`${stem}.csv`,data:buildWalkCsv(sheet,opts.results,opts.history,opts.unsaved,opts.scores)});
  }
  if(opts.includeCsv){
    bundle.add({name:'all-lists.csv',data:buildWalkSheetsCsv(sheets,opts.results,opts.history,opts.unsaved,opts.scores)});
    bundle.add({name:'Summary.csv',data:walkSummaryCsv(sheets,names)});
  }
  const doors=sheets.reduce((n,s)=>n+s.totals.doors,0),voters=sheets.reduce((n,s)=>n+s.totals.voters,0);
  bundle.add({name:'manifest.json',data:JSON.stringify({...opts.provenance,format:'terrier-walk-lists-1',area:opts.area,date:sheets[0].generated_on,doors,voters,splitEvenOdd:sheets[0].query.split_sides!=='0',lists:sheets.map((s,i)=>({list:i+1,label:names[i].list,side:names[i].side,precinct:names[i].precinct,file:`${names[i].stem}.pdf`,doors:s.totals.doors,voters:s.totals.voters}))},null,2)+'\n'});
  const notes=[`Walk lists for ${opts.area||'the selected streets'}.`,`${sheets.length} lists cover ${doors} doors and ${voters} recorded voters.`,
    sheets[0].query.split_sides==='0'?'Even and odd addresses are combined.':'Even and odd addresses are separated; doors with unknown parity remain included.',
    'The door cap divides the complete selection into parts; it does not discard doors.',
    'Voter columns contain the selected district records. Addresses without a matched voter record remain as blank voter rows.',
    'Precinct headings on list pages identify the actual street segment. A file may cover several precincts.',
    ...opts.notes??[]].join('\r\n')+'\r\n';
  bundle.add({name:'Notes.txt',data:notes});
  bundle.add({name:'README.txt',data:`Terrier walk lists\r\n\r\nEach PDF contains a map cover, door script and street-by-street canvassing pages. ${opts.includeCsv?'Matching CSV files, Summary.csv and all-lists.csv are included. ':''}See Notes.txt and manifest.json for coverage and source review.\r\nMap paths show full street sections. Use the door tables for each list's assigned addresses.\r\n`});
  return bundle.build();
}

export async function exportWalkZip(sheets:WalkListPayload[],opts:WalkZipOptions):Promise<void>{
  const bytes=await buildWalkZip(sheets,opts);
  downloadZip(walkFileName(opts.area,sheets[0].generated_on,'zip'),bytes);
}
