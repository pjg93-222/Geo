import {unzipSync} from 'fflate';
import {validateWalkWorkbook} from './walkWorkbookBounds.ts';

export interface ImportedWalkImage {
  id:string;name:string;blob:Blob;width:number;height:number;sourceFile:string;sourceSheet?:string;sourceCell?:string;
  anchor?:{fromRow:number;toRow:number;fromColumn:number;toColumn:number};
}
export interface WalkImageSource {sourceFile:string;sourceSheet?:string;sourceCell?:string;sourceRow?:number}
export interface WalkImageNote extends WalkImageSource {sourceRow:number;text:string}
export interface WalkImageResult {images:ImportedWalkImage[];notes:WalkImageNote[]}
const MAX_IMAGES=12,MAX_BYTES=40*1024*1024;
const RASTER=/\.(?:png|jpe?g|webp|gif|bmp)$/i;
const IMAGE_REFERENCE=/\.(?:png|jpe?g|webp|gif|bmp|svg|tiff?|heic|avif|emf|wmf)(?:[?#].*)?$/i;
const IMAGE_HEADERS=new Set(['image','images','imagefile','imagefilename','imagepath','imageurl','photo','photograph','picture','screenshot','mapimage','attachment']);
let nextImage=0;
const note=(source:WalkImageSource,text:string):WalkImageNote=>({...source,sourceRow:source.sourceRow??1,text});

export const isWalkImageHeader=(value:string):boolean=>IMAGE_HEADERS.has(value.toLowerCase().replace(/[^a-z0-9]/g,''));
export const isWalkImageFile=(name:string,type=''):boolean=>IMAGE_REFERENCE.test(name)||/^image\//i.test(type);
export function assertWalkImages(images:ImportedWalkImage[]):void{
  if(images.length>MAX_IMAGES)throw new Error('Use at most 12 images in one import.');
  if(images.reduce((sum,image)=>sum+image.blob.size,0)>MAX_BYTES)throw new Error('The images in one import must total 40 MiB or less.');
}
function mimeOf(bytes:Uint8Array):string|null{
  if(bytes.length>=8&&bytes[0]===137&&bytes[1]===80&&bytes[2]===78&&bytes[3]===71&&bytes[4]===13&&bytes[5]===10&&bytes[6]===26&&bytes[7]===10)return 'image/png';
  if(bytes.length>=3&&bytes[0]===255&&bytes[1]===216&&bytes[2]===255)return 'image/jpeg';
  const prefix=new TextDecoder('ascii').decode(bytes.subarray(0,12));
  if(prefix.startsWith('RIFF')&&prefix.slice(8,12)==='WEBP')return 'image/webp';
  if(prefix.startsWith('GIF87a')||prefix.startsWith('GIF89a'))return 'image/gif';
  if(prefix.startsWith('BM'))return 'image/bmp';
  return null;
}
/** Read container dimensions without asking the browser to allocate decoded pixels. */
function dimensionsOf(bytes:Uint8Array,type:string):{width:number;height:number}|null{
  const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength),ascii=(at:number,count:number)=>new TextDecoder('ascii').decode(bytes.subarray(at,at+count));
  if(type==='image/png')return bytes.length>=33&&view.getUint32(8)===13&&ascii(12,4)==='IHDR'?{width:view.getUint32(16),height:view.getUint32(20)}:null;
  if(type==='image/gif')return bytes.length>=13?{width:view.getUint16(6,true),height:view.getUint16(8,true)}:null;
  if(type==='image/bmp'){
    if(bytes.length<26)return null;
    const dib=view.getUint32(14,true);
    if(dib===12)return {width:view.getUint16(18,true),height:view.getUint16(20,true)};
    return dib>=40&&bytes.length>=54?{width:view.getInt32(18,true),height:Math.abs(view.getInt32(22,true))}:null;
  }
  if(type==='image/jpeg'){
    for(let offset=2;offset<bytes.length;){
      if(bytes[offset++]!==255)return null;
      while(offset<bytes.length&&bytes[offset]===255)offset++;
      if(offset>=bytes.length)return null;
      const marker=bytes[offset++];
      if(marker===0xd9||marker===0xda)return null;
      if(marker===1||marker===0xd8||(marker>=0xd0&&marker<=0xd7))continue;
      if(offset+2>bytes.length)return null;
      const length=view.getUint16(offset);if(length<2||offset+length>bytes.length)return null;
      if([0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf].includes(marker))return length>=8?{width:view.getUint16(offset+5),height:view.getUint16(offset+3)}:null;
      offset+=length;
    }
  }
  if(type==='image/webp'){
    const end=view.getUint32(4,true)+8;if(end>bytes.length)return null;
    for(let offset=12;offset+8<=end;){
      const chunk=ascii(offset,4),length=view.getUint32(offset+4,true),at=offset+8;
      if(at+length>end)return null;
      const u24=(pos:number)=>bytes[pos]|bytes[pos+1]<<8|bytes[pos+2]<<16;
      if(chunk==='VP8X'&&length>=10)return {width:u24(at+4)+1,height:u24(at+7)+1};
      if(chunk==='VP8L'&&length>=5&&bytes[at]===0x2f)return {width:1+(bytes[at+1]|(bytes[at+2]&0x3f)<<8),height:1+((bytes[at+2]>>6)|bytes[at+3]<<2|(bytes[at+4]&0xf)<<10)};
      if(chunk==='VP8 '&&length>=10&&bytes[at+3]===0x9d&&bytes[at+4]===1&&bytes[at+5]===0x2a)return {width:view.getUint16(at+6,true)&0x3fff,height:view.getUint16(at+8,true)&0x3fff};
      offset=at+length+(length%2);
    }
  }
  return null;
}
function makeImage(bytes:Uint8Array,name:string,source:WalkImageSource):ImportedWalkImage|null{
  const type=mimeOf(bytes);if(!type)return null;
  const dimensions=dimensionsOf(bytes,type);if(!dimensions||dimensions.width<1||dimensions.height<1)return null;
  if(dimensions.width>12000||dimensions.height>12000||dimensions.width*dimensions.height>20000000)throw new Error('Each image must be at most 20 million pixels and 12,000 pixels on either side. Resize this image before importing it.');
  // Container bounds are checked before preview; the OCR reader also checks decoded dimensions.
  const blob=new Blob([bytes.slice().buffer],{type});
  return {id:`walk-image-${++nextImage}`,name,blob,...dimensions,sourceFile:source.sourceFile,...source.sourceSheet?{sourceSheet:source.sourceSheet}:{},...source.sourceCell?{sourceCell:source.sourceCell}:{}};
}
export async function readWalkImageFile(file:Pick<File,'name'|'arrayBuffer'>&{size?:number}):Promise<ImportedWalkImage>{
  if((file.size??0)>MAX_BYTES)throw new Error('The images in one import must total 40 MiB or less.');
  const bytes=new Uint8Array(await file.arrayBuffer());
  if(bytes.byteLength>MAX_BYTES)throw new Error('The images in one import must total 40 MiB or less.');
  const image=makeImage(bytes,file.name,{sourceFile:file.name});
  if(!image)throw new Error('This image could not be read. Use PNG, JPEG, WebP, GIF or BMP.');
  return image;
}

/** Image cells are extracted locally; references are reported without fetching a URL or path. */
export function readWalkImageCell(value:string,source:WalkImageSource,declaredImageColumn=false):{handled:boolean;image?:ImportedWalkImage;note?:WalkImageNote}{
  const text=value.trim();if(!text)return {handled:false};
  if(/^data:image\//i.test(text)){
    const data=/^data:(image\/[^;,]+);base64,([\s\S]*)$/i.exec(text);
    if(!data||!/^image\/(?:png|jpe?g|webp|gif|bmp|x-ms-bmp)$/i.test(data[1]))return {handled:true,note:note(source,'An embedded image uses an unsupported format. Attach a PNG, JPEG, WebP, GIF or BMP copy.')};
    const encoded=data[2].replace(/\s/g,'');
    if(encoded.length>Math.ceil(MAX_BYTES/3)*4)throw new Error('The images in one import must total 40 MiB or less.');
    let binary:string;
    try{binary=atob(encoded);}catch{return {handled:true,note:note(source,'An embedded image has invalid base64 data. Attach the image file separately.')};}
    const bytes=Uint8Array.from(binary,char=>char.charCodeAt(0));
    const extension=data[1].split('/')[1].replace('jpeg','jpg').replace('x-ms-bmp','bmp');
    const image=makeImage(bytes,`${source.sourceCell||'embedded-image'}.${extension}`,source);
    return image?{handled:true,image}:{handled:true,note:note(source,'An embedded image has an unreadable raster signature. Attach the image file separately.')};
  }
  if(declaredImageColumn||IMAGE_REFERENCE.test(text))return {handled:true,note:note(source,`Image reference: ${text}. Attach this image file separately; referenced URLs and local paths are not opened automatically.`)};
  return {handled:false};
}

const decodeXml=(value:string):string=>value.replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos);/gi,(_,entity:string)=>{
  const named:Record<string,string>={amp:'&',lt:'<',gt:'>',quot:'"',apos:"'"};
  if(entity[0]!=='#')return named[entity.toLowerCase()]??'';
  const number=entity[1].toLowerCase()==='x'?parseInt(entity.slice(2),16):Number(entity.slice(1));
  return number>0&&number<=0x10ffff?String.fromCodePoint(number):'';
});
const attributes=(tag:string):Record<string,string>=>Object.fromEntries([...tag.matchAll(/\s([\w:.-]+)\s*=\s*(["'])(.*?)\2/gs)].map(([,key,,value])=>[key,decodeXml(value)]));
const xml=(bytes:Uint8Array|undefined):string=>bytes?new TextDecoder().decode(bytes).replace(/<!--[\s\S]*?-->/g,''):'';
const tags=(text:string,name:string):string[]=>[...text.matchAll(new RegExp(`<(?:[A-Za-z_][\\w.-]*:)?${name}(?=[\\s/>])(?:[^"'<>]|"[^"]*"|'[^']*')*>`,'g'))].map(match=>match[0]);
function resolvePart(base:string,reference:string):string|null{
  if(!reference||/^[a-z][a-z0-9+.-]*:/i.test(reference)||reference.includes('\\'))return null;
  const parts=(reference.startsWith('/')?reference.slice(1):`${base.slice(0,base.lastIndexOf('/')+1)}${reference}`).split('/'),path:string[]=[];
  for(const part of parts){if(part==='..'){if(!path.length)return null;path.pop();}else if(part&&part!=='.')path.push(part);}
  return path.join('/');
}
function relationships(files:Record<string,Uint8Array>,part:string):Map<string,{path:string|null;external:boolean;type:string}>{
  const slash=part.lastIndexOf('/'),rels=`${part.slice(0,slash+1)}_rels/${part.slice(slash+1)}.rels`;
  return new Map(tags(xml(files[rels]),'Relationship').map(tag=>{
    const attrs=attributes(tag);return [attrs.Id,{path:attrs.TargetMode==='External'?null:resolvePart(part,attrs.Target),external:attrs.TargetMode==='External',type:attrs.Type??''}];
  }));
}
const columnName=(column:number):string=>{let out='';for(let n=column;n>0;){n--;out=String.fromCharCode(65+n%26)+out;n=Math.floor(n/26);}return out;};
function cellMarker(block:string,kind:'from'|'to'):{row:number;column:number}|null{
  const section=new RegExp(`<(?:[\\w.-]+:)?${kind}\\b[^>]*>([\\s\\S]*?)<\\/(?:[\\w.-]+:)?${kind}>`).exec(block)?.[1];
  if(!section)return null;
  const get=(name:string)=>new RegExp(`<(?:[\\w.-]+:)?${name}\\b[^>]*>\\s*(\\d+)\\s*<\\/(?:[\\w.-]+:)?${name}>`).exec(section)?.[1];
  const row=Number(get('row'))+1,column=Number(get('col'))+1;
  return Number.isInteger(row)&&row>=1&&row<=1048576&&Number.isInteger(column)&&column>=1&&column<=16384?{row,column}:null;
}

/** Validates the archive first, then extracts bounded media and standard OOXML drawing provenance. */
export function extractWalkWorkbookImages(bytes:Uint8Array,sourceFile:string):WalkImageResult{
  validateWalkWorkbook(bytes);
  const output:WalkImageResult={images:[],notes:[]};let mediaCount=0,mediaBytes=0;
  // The filter runs before decompression. Unsupported pictures remain notes and are not inflated.
  const files=unzipSync(bytes,{filter:file=>{
    if(/^xl\/media\//i.test(file.name)){
      if(!RASTER.test(file.name)){output.notes.push(note({sourceFile},`Embedded image ${file.name.split('/').at(-1)} is not a supported raster. Attach a PNG, JPEG, WebP, GIF or BMP copy.`));return false;}
      mediaCount++;mediaBytes+=file.originalSize;
      if(mediaCount>MAX_IMAGES)throw new Error('Use at most 12 images in one import.');
      if(mediaBytes>MAX_BYTES)throw new Error('The images in one import must total 40 MiB or less.');
      return true;
    }
    return /^xl\/(?:workbook\.xml|_rels\/workbook\.xml\.rels|worksheets\/.*\.xml(?:\.rels)?|drawings\/.*\.xml(?:\.rels)?)$/i.test(file.name);
  }});
  const media=new Map(Object.keys(files).filter(name=>/^xl\/media\//i.test(name)).map(name=>[name,files[name]]));
  const used=new Set<string>(),bookRels=relationships(files,'xl/workbook.xml');
  function add(path:string,source:WalkImageSource,anchor?:ImportedWalkImage['anchor']){
    const data=media.get(path);if(!data)return;
    const image=makeImage(data,path.split('/').at(-1)??'embedded-image',source);
    used.add(path);
    if(!image){output.notes.push(note(source,`Embedded image ${path.split('/').at(-1)} could not be read. Attach a raster copy separately.`));return;}
    if(anchor)image.anchor=anchor;
    output.images.push(image);assertWalkImages(output.images);
  }
  for(const sheetTag of tags(xml(files['xl/workbook.xml']),'sheet')){
    const sheet=attributes(sheetTag),sheetPart=bookRels.get(sheet['r:id'])?.path;
    if(!sheetPart)continue;
    const sheetRels=relationships(files,sheetPart);
    for(const drawingTag of tags(xml(files[sheetPart]),'drawing')){
      const drawingPart=sheetRels.get(attributes(drawingTag)['r:id'])?.path;
      if(!drawingPart)continue;
      const drawingRels=relationships(files,drawingPart),drawing=xml(files[drawingPart]);
      for(const block of drawing.matchAll(/<(?:[\w.-]+:)?(twoCellAnchor|oneCellAnchor|absoluteAnchor)\b[^>]*>([\s\S]*?)<\/(?:[\w.-]+:)?\1>/g)){
        const from=cellMarker(block[2],'from'),to=cellMarker(block[2],'to')??from;
        const source:WalkImageSource={sourceFile,sourceSheet:sheet.name,...from?{sourceRow:from.row,sourceCell:`${columnName(from.column)}${from.row}`}:{}};
        const anchor=from&&to?{fromRow:from.row,toRow:to.row,fromColumn:from.column,toColumn:to.column}:undefined;
        for(const blip of tags(block[2],'blip')){
          const attrs=attributes(blip),imageRel=drawingRels.get(attrs['r:embed']||attrs['r:link']);
          if(imageRel?.path)add(imageRel.path,source,anchor);
          else if(imageRel?.external)output.notes.push(note(source,'An externally linked workbook image was not opened. Attach that image file separately.'));
        }
      }
    }
  }
  for(const path of media.keys())if(!used.has(path)){
    add(path,{sourceFile});
    output.notes.push(note({sourceFile},`Embedded image ${path.split('/').at(-1)} has no readable worksheet position; review its placement separately.`));
  }
  return output;
}
