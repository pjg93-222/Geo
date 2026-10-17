import type {jsPDF} from 'jspdf';
import type {AddressSheetRow} from './walkAddressSheets';
import {addressSheetsCsv} from './walkAddressSheets';
import {localToday,walkFileName} from './walk';
import {ZipBundle,downloadZip,listFileStem,yieldExport,checkZipFileCount} from './walkZip';

export interface AddressPdfOptions{
  sourceName:string;totalRows:number;splitSides:boolean;date?:string;watermark?:string;listOffset?:number;totalLists?:number;
  excludedSources?:{sourceSheet?:string;sourceCell?:string;sourceRow:number;sourceText:string}[];
  streetMatches?:{sourceSheet?:string;sourceCell?:string;sourceRow:number;input:string;streetId:number;street:string;city:string;state:string;zip:string;method:'exact'|'proximity'|'manual'|'image-layout';distanceMeters?:number;anchorCount?:number}[];
  imageEvidence?:unknown[];
}

export async function buildAddressPdf(sheets:AddressSheetRow[][],opts:AddressPdfOptions):Promise<{doc:jsPDF}>{
  if(!sheets.length)throw new Error('No address sheets to export.');
  const [{jsPDF},{default:autoTable}]=await Promise.all([import('jspdf'),import('jspdf-autotable')]);
  const doc=new jsPDF({orientation:'landscape',unit:'pt',format:'letter',compress:true});
  const date=opts.date??localToday(),total=opts.totalLists??sheets.length;
  sheets.forEach((rows,i)=>{
    if(i)doc.addPage();
    autoTable(doc,{startY:92,margin:{top:92,right:30,bottom:34,left:30},theme:'grid',showHead:'everyPage',rowPageBreak:'avoid',
      styles:{fontSize:9,cellPadding:6,overflow:'linebreak'},headStyles:{fillColor:[87,68,123]},
      head:[['Door','Address','City / State / ZIP','Side','Result','Notes']],
      body:rows.map((a,j)=>[j+1,[a.house,a.street,a.unit&&(/^(?:#|apt\b|apartment\b|unit\b|suite\b|ste\b|lot\b|room\b|rm\b|building\b|bldg\b|floor\b|fl\b)/i.test(a.unit)?a.unit:`#${a.unit}`)].filter(Boolean).join(' '),[a.city,a.state,a.zip].filter(Boolean).join(' '),opts.splitSides?a.side:'Both','',a.note??'']),
      columnStyles:{0:{cellWidth:36},1:{cellWidth:240},2:{cellWidth:140},3:{cellWidth:48},4:{cellWidth:70},5:{cellWidth:'auto'}},
      willDrawPage:()=>{
        doc.setFontSize(17);doc.setTextColor(24);doc.text(`Address walk sheet · List ${i+1+(opts.listOffset??0)} of ${total}`,30,34);
        doc.setFontSize(9);doc.text(`${rows.length} doors on this list · ${opts.totalRows} address rows covered across all lists · ${date}`,30,51);
        const source=opts.sourceName.length>100?`${opts.sourceName.slice(0,97)}...`:opts.sourceName;
        doc.text(doc.splitTextToSize(`Source: ${source}. Address-only sheet; voter details and route map are not added.`,730),30,66);
      }});
  });
  for(let page=1;page<=doc.getNumberOfPages();page++){
    doc.setPage(page);doc.setFontSize(8);doc.setTextColor(90);
    doc.text(`Page ${page} of ${doc.getNumberOfPages()}`,762,590,{align:'right'});
    if(opts.watermark)doc.text(opts.watermark,30,590);
  }
  return {doc};
}

export async function exportAddressZip(sheets:AddressSheetRow[][],opts:AddressPdfOptions&{includeCsv?:boolean}):Promise<void>{
  checkZipFileCount(sheets.length*(opts.includeCsv!==false?2:1)+(opts.includeCsv!==false?3:2));
  const bundle=new ZipBundle(),date=opts.date??localToday();
  const counts=sheets.map((rows,i)=>({list:i+1,doors:rows.length}));
  for(const [i,rows] of sheets.entries()){
    await yieldExport();
    const {doc}=await buildAddressPdf([rows],{...opts,date,listOffset:i,totalLists:sheets.length});
    const stem=`lists/${listFileStem(i)}`;
    bundle.add({name:`${stem}.pdf`,data:new Uint8Array(doc.output('arraybuffer'))});
    if(opts.includeCsv!==false)bundle.add({name:`${stem}.csv`,data:addressSheetsCsv([rows],i)});
  }
  if(opts.includeCsv!==false)bundle.add({name:'all-lists.csv',data:addressSheetsCsv(sheets)});
  bundle.add({name:'manifest.json',data:JSON.stringify({format:'terrier-address-sheets-1',source:opts.sourceName,date,addresses:sheets.reduce((n,s)=>n+s.length,0),splitEvenOdd:opts.splitSides,lists:counts,excludedSources:opts.excludedSources??[],streetMatches:opts.streetMatches??[],imageEvidence:opts.imageEvidence??[]},null,2)+'\n'});
  bundle.add({name:'README.txt',data:`Terrier address walk sheets\r\n\r\nEach numbered PDF is one complete list. Long lists can span multiple pages. ${opts.includeCsv!==false?'Matching CSV files and all-lists.csv are included. ':''}All ${opts.totalRows} selected address rows are covered across ${sheets.length} lists.\r\nSelected exact addresses and duplicate rows are retained. Street matches use the selected district's stored doors. No voter details or route map are added.\r\n${opts.excludedSources?.length??0} source entries were explicitly excluded during review; see manifest.json for their source cells and text.\r\nThe manifest also records selected street matches, including proximity inferences and manual choices.\r\n`});
  downloadZip(walkFileName(opts.sourceName,date,'zip'),bundle.build());
}
