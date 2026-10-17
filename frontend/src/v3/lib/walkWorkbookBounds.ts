import {unzipSync} from 'fflate';

const MAX_EXPANDED=128*1024*1024,MAX_FILES=10000,MAX_ROWS=100000,MAX_COLUMNS=1024,MAX_CELLS=2000000;
const fail=(reason:string):never=>{throw new Error(`This Excel workbook could not be read safely: ${reason}. Use a smaller unencrypted XLSX workbook or export the address cells as CSV.`);};
const decoder=new TextDecoder('utf-8',{fatal:true});

/** Reject huge declared ZIP payloads before a ZIP or worksheet reader allocates their output. */
function zipEntries(bytes:Uint8Array):Set<string>{
  const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
  const u16=(at:number)=>view.getUint16(at,true),u32=(at:number)=>view.getUint32(at,true);
  let end=-1;
  for(let at=bytes.length-22;at>=Math.max(0,bytes.length-65557);at--)if(u32(at)===0x06054b50&&at+22+u16(at+20)===bytes.length){end=at;break;}
  if(end<0)fail('the ZIP directory is missing or damaged');
  const count=u16(end+10),directorySize=u32(end+12),directoryOffset=u32(end+16);
  if(count===65535||directorySize===0xffffffff||directoryOffset===0xffffffff||(end>=20&&u32(end-20)===0x07064b50))fail('ZIP64 workbooks are not supported');
  if(u16(end)!==0x4b50||u16(end+4)||u16(end+6)||u16(end+8)!==count)fail('split ZIP archives are not supported');
  if(count>MAX_FILES)fail(`the workbook has more than ${MAX_FILES.toLocaleString('en-US')} internal files`);
  if(directoryOffset+directorySize!==end)fail('the ZIP directory has invalid bounds');
  const names=new Set<string>();let offset=directoryOffset,total=0;
  for(let index=0;index<count;index++){
    if(offset+46>end||u32(offset)!==0x02014b50)fail('the ZIP directory is damaged');
    const flags=u16(offset+8),method=u16(offset+10),compressed=u32(offset+20),original=u32(offset+24);
    const nameLength=u16(offset+28),extraLength=u16(offset+30),commentLength=u16(offset+32),local=u32(offset+42);
    const next=offset+46+nameLength+extraLength+commentLength;
    if(next>end||compressed===0xffffffff||original===0xffffffff||local===0xffffffff)fail('ZIP64 or damaged ZIP entries are not supported');
    if(u16(offset+34)||flags&1||![0,8].includes(method))fail('encrypted or unsupported ZIP entries were found');
    for(let extra=offset+46+nameLength;extra<offset+46+nameLength+extraLength;){
      if(extra+4>offset+46+nameLength+extraLength)fail('the ZIP extra fields are damaged');
      if(u16(extra)===1)fail('ZIP64 workbooks are not supported');
      extra+=4+u16(extra+2);if(extra>offset+46+nameLength+extraLength)fail('the ZIP extra fields are damaged');
    }
    total+=original;if(total>MAX_EXPANDED)fail('the uncompressed workbook exceeds 128 MiB');
    if(local+30>directoryOffset||u32(local)!==0x04034b50)fail('a ZIP entry has invalid bounds');
    const dataStart=local+30+u16(local+26)+u16(local+28);
    if(dataStart+compressed>directoryOffset||method===0&&compressed!==original)fail('a ZIP entry has invalid sizes');
    let name:string;try{name=decoder.decode(bytes.subarray(offset+46,offset+46+nameLength));}catch{fail('an internal filename is unreadable');}
    if(!name!||names.has(name!))fail('duplicate or empty internal filenames were found');
    names.add(name!);offset=next;
  }
  if(offset!==directoryOffset+directorySize)fail('the ZIP directory does not match its entry count');
  return names;
}

const attribute=(tag:string,name:string):string|undefined=>new RegExp(`(?:\\s|^)${name}\\s*=\\s*(["'])(.*?)\\1`,'s').exec(tag)?.[2];
function xmlText(bytes:Uint8Array):string{
  let text:string;try{text=decoder.decode(bytes);}catch{fail('worksheet text is not valid UTF-8');}
  if(/<!DOCTYPE|<!ENTITY/i.test(text!)||text!.includes('\0'))fail('unsupported XML declarations or encoding were found');
  return text!.replace(/<!--[\s\S]*?-->/g,'');
}

function rectangle(xml:string):number{
  let maxRow=0,maxColumn=0,rowCount=0,currentRow=0;
  const row=(value:number)=>{if(!Number.isSafeInteger(value)||value<1||value>MAX_ROWS)fail(`a worksheet exceeds ${MAX_ROWS.toLocaleString('en-US')} rows`);maxRow=Math.max(maxRow,value);};
  const ref=(value:string)=>{
    for(const cell of value.split(':')){
      const match=/^\$?([A-Z]+)\$?([1-9][0-9]*)$/.exec(cell);
      if(!match)fail('a worksheet has an invalid cell reference');
      let column=0;for(const letter of match![1]){column=column*26+letter.charCodeAt(0)-64;if(column>MAX_COLUMNS)fail(`a worksheet exceeds ${MAX_COLUMNS.toLocaleString('en-US')} columns`);}
      row(Number(match![2]));maxColumn=Math.max(maxColumn,column);
    }
  };
  for(const match of xml.matchAll(/<(?:[A-Za-z_][\w.-]*:)?(dimension|row|c|mergeCell)(?=[\s/>])(?:[^"'<>]|"[^"]*"|'[^']*')*>/g)){
    const [tag,kind]=match;
    if(kind==='row'){
      rowCount++;if(rowCount>MAX_ROWS)fail(`a worksheet exceeds ${MAX_ROWS.toLocaleString('en-US')} rows`);
      const value=attribute(tag,'r');if(value!==undefined&&!/^[1-9][0-9]*$/.test(value))fail('a worksheet has an invalid row number');
      currentRow=value===undefined?currentRow+1:Number(value);row(currentRow);
    }else{
      const value=attribute(tag,kind==='c'?'r':'ref');
      if(value===undefined&&kind==='c')fail('a worksheet has a cell without a reference');
      if(value!==undefined)ref(value);
    }
    if(maxRow*Math.max(maxColumn,1)>MAX_CELLS)fail(`the worksheet rectangle exceeds ${MAX_CELLS.toLocaleString('en-US')} cells`);
  }
  return maxRow*Math.max(maxColumn,1);
}

/** Worksheet XML is inflated only after the whole archive's declared footprint is bounded. */
export function validateWalkWorkbook(bytes:Uint8Array):void{
  try{
    const names=zipEntries(bytes),worksheetNames=new Set([...names].filter(name=>/^xl\/worksheets\/.*\.xml$/i.test(name))),pathsById=new Map<string,string>();
    const rels=unzipSync(bytes,{filter:file=>file.name==='xl/_rels/workbook.xml.rels'||file.name==='xl/workbook.xml'});
    const workbookRels=rels['xl/_rels/workbook.xml.rels'];
    if(workbookRels)for(const match of xmlText(workbookRels).matchAll(/<(?:[A-Za-z_][\w.-]*:)?Relationship(?=[\s/>])(?:[^"'<>]|"[^"]*"|'[^']*')*>/g)){
      const type=attribute(match[0],'Type')??'';
      if(type.includes('&'))fail('an encoded worksheet relationship was found');
      if(!type.endsWith('/worksheet'))continue;
      const attributes:Record<string,string>=Object.fromEntries([...match[0].matchAll(/\s([\w:.-]+)\s*=\s*(["'])(.*?)\2/gs)].map(([,key,,value])=>[key,value]));
      const reference=attributes.Target??'';
      // Standard worksheet paths are plain relative or package-absolute paths. Fail closed on
      // unusual encodings so a relationship cannot bypass the cell bounds inspection.
      if(!reference||/[&\\]/.test(reference)||attributes.TargetMode==='External')fail('an unsupported worksheet path was found');
      const parts=(reference.startsWith('/')?reference.slice(1):`xl/${reference}`).split('/'),resolved:string[]=[];
      for(const part of parts){if(part==='..'){if(!resolved.length)fail('an invalid worksheet path was found');resolved.pop();}else if(part&&part!=='.')resolved.push(part);}
      const path=resolved.join('/');if(!names.has(path))fail('a worksheet file is missing');worksheetNames.add(path);
      const id=attribute(match[0],'Id');if(id?.includes('&'))fail('an encoded worksheet relationship was found');if(id)pathsById.set(id,path);
    }
    const sheets=unzipSync(bytes,{filter:file=>worksheetNames.has(file.name)}),rectangles=new Map<string,number>();let cells=0;
    for(const [path,sheet] of Object.entries(sheets)){const size=rectangle(xmlText(sheet));rectangles.set(path,size);cells+=size;if(cells>MAX_CELLS)fail(`the combined worksheet rectangles exceed ${MAX_CELLS.toLocaleString('en-US')} cells`);}
    // A workbook can reference the same XML repeatedly. Count logical sheets too, because the
    // reader returns a distinct dense array for every sheet reference.
    if(rels['xl/workbook.xml']){
      let referencedCells=0,count=0;
      for(const match of xmlText(rels['xl/workbook.xml']).matchAll(/<(?:[A-Za-z_][\w.-]*:)?sheet(?=[\s/>])(?:[^"'<>]|"[^"]*"|'[^']*')*>/g)){
        if(++count>MAX_FILES)fail('the workbook contains too many worksheet references');
        const id=attribute(match[0],'r:id');if(id?.includes('&'))fail('an encoded worksheet relationship was found');const path=id?pathsById.get(id):undefined;
        if(path)referencedCells+=rectangles.get(path)??0;
        if(referencedCells>MAX_CELLS)fail(`the combined worksheet rectangles exceed ${MAX_CELLS.toLocaleString('en-US')} cells`);
      }
    }
  }catch(error){
    if(error instanceof Error&&error.message.startsWith('This Excel workbook could not be read safely:'))throw error;
    fail('the compressed workbook is damaged or unsupported');
  }
}
