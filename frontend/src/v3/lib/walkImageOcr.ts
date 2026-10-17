/** Local-only map-label OCR. Pixels and recognition results never leave this browser. */
export interface WalkImageLine {text:string;confidence:number;x:number;y:number;width:number;height:number}
export interface WalkImageRecognition {width:number;height:number;lines:WalkImageLine[]}
const MAX_BYTES=20*1024*1024,MAX_PIXELS=20_000_000,MAX_SIDE=2400,TIME_LIMIT=90_000;
const ASSET_PATH='/ocr/tesseract-6.0.1';
const cancelled=()=>new DOMException('Image reading cancelled.','AbortError');
type Size={width:number;height:number};
type Box={x0:number;y0:number;x1:number;y1:number};

/** Inspect compressed headers before asking the browser to allocate decoded pixels. */
export function walkImageDimensions(bytes:Uint8Array):Size{
  const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
  const has=(offset:number,count:number)=>offset>=0&&offset+count<=bytes.length;
  const word=(offset:number,little=false)=>has(offset,2)?view.getUint16(offset,little):0;
  const long=(offset:number,little=false)=>has(offset,4)?view.getUint32(offset,little):0;
  const ascii=(offset:number,count:number)=>String.fromCharCode(...bytes.subarray(offset,offset+count));
  let width=0,height=0;
  if(ascii(0,8)==='\x89PNG\r\n\x1a\n'&&ascii(12,4)==='IHDR'&&has(0,24)){
    width=long(16);height=long(20);
  }else if(['GIF87a','GIF89a'].includes(ascii(0,6))&&has(0,10)){
    width=word(6,true);height=word(8,true);
  }else if(ascii(0,2)==='BM'&&has(0,26)){
    const header=long(14,true);
    if(header===12){width=word(18,true);height=word(20,true);}
    else if(header>=40){width=view.getInt32(18,true);height=Math.abs(view.getInt32(22,true));}
  }else if(ascii(0,4)==='RIFF'&&ascii(8,4)==='WEBP'){
    for(let at=12;has(at,8);){
      const type=ascii(at,4),length=long(at+4,true),data=at+8;
      if(!has(data,length))break;
      if(type==='VP8X'&&length>=10){width=1+bytes[data+4]+(bytes[data+5]<<8)+(bytes[data+6]<<16);height=1+bytes[data+7]+(bytes[data+8]<<8)+(bytes[data+9]<<16);break;}
      if(type==='VP8 '&&length>=10&&ascii(data+3,3)==='\x9d\x01\x2a'){width=word(data+6,true)&0x3fff;height=word(data+8,true)&0x3fff;break;}
      if(type==='VP8L'&&length>=5&&bytes[data]===0x2f){const bits=long(data+1,true);width=(bits&0x3fff)+1;height=((bits>>>14)&0x3fff)+1;break;}
      at=data+length+(length&1);
    }
  }else if(word(0)===0xffd8){
    let at=2;
    while(has(at,2)){
      if(bytes[at++]!==0xff)break;
      while(has(at,1)&&bytes[at]===0xff)at++;
      const marker=bytes[at++];
      if(marker===0xd9||marker===0xda)break;
      if(marker===0x01||(marker>=0xd0&&marker<=0xd7))continue;
      const length=word(at);
      if(length<2||!has(at,length))break;
      if(marker>=0xc0&&marker<=0xcf&&![0xc4,0xc8,0xcc].includes(marker)&&length>=8){height=word(at+3);width=word(at+5);break;}
      at+=length;
    }
  }
  if(!Number.isInteger(width)||!Number.isInteger(height)||width<1||height<1)throw new Error('This image could not be read. Use a valid PNG, JPEG, WebP, GIF or BMP image.');
  if(width*height>MAX_PIXELS||Math.max(width,height)>12000)throw new Error('This image exceeds 20 million pixels or 12,000 pixels on one side. Crop or resize it before importing.');
  return {width,height};
}

/** Map all four OCR-box corners back through the canvas rotation and scale. */
export function walkImageOriginalBox(box:Box,original:Size,scaled:Size,angle:number):Omit<WalkImageLine,'text'|'confidence'>{
  const radians=angle*Math.PI/180,c=Math.cos(radians),s=Math.sin(radians);
  const rotatedWidth=Math.round(Math.abs(scaled.width*c)+Math.abs(scaled.height*s));
  const rotatedHeight=Math.round(Math.abs(scaled.width*s)+Math.abs(scaled.height*c));
  const points=[[box.x0,box.y0],[box.x1,box.y0],[box.x0,box.y1],[box.x1,box.y1]].map(([x,y])=>{
    const dx=x-rotatedWidth/2,dy=y-rotatedHeight/2;
    return [(dx*c+dy*s+scaled.width/2)*original.width/scaled.width,(-dx*s+dy*c+scaled.height/2)*original.height/scaled.height];
  });
  const x=Math.max(0,Math.min(original.width,...points.map(p=>p[0]))),y=Math.max(0,Math.min(original.height,...points.map(p=>p[1])));
  const right=Math.min(original.width,Math.max(...points.map(p=>p[0]))),bottom=Math.min(original.height,Math.max(...points.map(p=>p[1])));
  return {x,y,width:Math.max(0,right-x),height:Math.max(0,bottom-y)};
}

export function mergeWalkImageLines(lines:WalkImageLine[]):WalkImageLine[]{
  const result:WalkImageLine[]=[];
  const key=(text:string)=>text.toUpperCase().replace(/[^A-Z0-9]/g,'');
  for(const line of [...lines].sort((a,b)=>b.confidence-a.confidence)){
    if(!line.text.trim()||!Number.isFinite(line.confidence)||line.width<=0||line.height<=0)continue;
    const duplicate=result.some(other=>{
      if(key(other.text)!==key(line.text))return false;
      const overlap=Math.max(0,Math.min(other.x+other.width,line.x+line.width)-Math.max(other.x,line.x))*Math.max(0,Math.min(other.y+other.height,line.y+line.height)-Math.max(other.y,line.y));
      return overlap/Math.min(other.width*other.height,line.width*line.height)>.45;
    });
    if(!duplicate)result.push(line);
  }
  return result.sort((a,b)=>a.y-b.y||a.x-b.x);
}

type Pending={resolve:(data:unknown)=>void;reject:(error:Error)=>void};
/** The pinned worker protocol gives us the native handle before initialization,
 * so a hung initialization can be terminated just like a recognition timeout. */
export class WalkOcrWorker{
  private pending=new Map<string,Pending>();
  private sequence=0;
  private timer:ReturnType<typeof setTimeout>;
  private closed=false;
  private worker:Worker;
  private signal?:AbortSignal;
  private onAbort?:()=>void;
  constructor(worker:Worker,onProgress?:(message:string)=>void,timeout=TIME_LIMIT,signal?:AbortSignal){
    this.worker=worker;
    this.timer=setTimeout(()=>this.close(new Error('Reading this image took too long. Crop a smaller map and try again.')),timeout);
    worker.onerror=()=>this.close(new Error('The local image reader could not start. Reopen Terrier and try again.'));
    worker.onmessageerror=()=>this.close(new Error('The local image reader returned an unreadable result.'));
    worker.onmessage=event=>{
      const {jobId,status,data}=event.data??{},pending=this.pending.get(jobId);
      if(status==='progress'){
        if(typeof data?.status==='string')onProgress?.(`Reading image: ${data.status}${typeof data.progress==='number'?` (${Math.round(data.progress*100)}%)`:''}`);
      }else if(pending){
        this.pending.delete(jobId);
        if(status==='resolve')pending.resolve(data);
        else pending.reject(new Error('The local image reader could not process this image. Try a clearer crop.'));
      }
    };
    this.signal=signal;this.onAbort=()=>this.close(cancelled());
    signal?.addEventListener('abort',this.onAbort,{once:true});
    if(signal?.aborted)this.close(cancelled());
  }
  job<T=unknown>(action:string,payload:unknown):Promise<T>{
    if(this.closed)return Promise.reject(new Error('The image reader has stopped. Try importing the image again.'));
    const jobId=String(++this.sequence);
    return new Promise<T>((resolve,reject)=>{
      this.pending.set(jobId,{resolve:data=>resolve(data as T),reject});
      try{this.worker.postMessage({workerId:'walk-image',jobId,action,payload});}
      catch{this.close(new Error('This image could not be passed to the local reader.'));}
    });
  }
  close(error=new Error('Image reading stopped.')):void{
    if(this.closed)return;
    this.closed=true;clearTimeout(this.timer);this.worker.terminate();
    if(this.onAbort)this.signal?.removeEventListener('abort',this.onAbort);
    for(const pending of this.pending.values())pending.reject(error);
    this.pending.clear();
  }
}

type OcrPage={blocks?:{paragraphs?:{lines?:{text:string;confidence:number;bbox:Box}[]}[]}[]};
const canvasBlob=(canvas:HTMLCanvasElement)=>new Promise<Blob>((resolve,reject)=>canvas.toBlob(blob=>blob?resolve(blob):reject(new Error('This image could not be prepared for reading.')),'image/png'));
/** Satellite maps use bright labels with a dark outline. Isolate that contrast
 * for an additional pass; the untouched-color pass still reads ordinary scans. */
function mapLabelMask(context:CanvasRenderingContext2D,width:number,height:number):void{
  const frame=context.getImageData(0,0,width,height),pixels=frame.data,mask=new Uint8Array(width*height);
  const neighbors=[[0,-3],[0,3],[-3,0],[3,0],[-2,-2],[2,-2],[-2,2],[2,2]];
  for(let y=0;y<height;y++)for(let x=0;x<width;x++){
    const i=(y*width+x)*4,r=pixels[i],g=pixels[i+1],b=pixels[i+2];
    if(Math.min(r,g,b)<190||Math.max(r,g,b)-Math.min(r,g,b)>55)continue;
    let outline=false;
    for(const [dx,dy] of neighbors){
      const xx=x+dx,yy=y+dy;if(xx<0||xx>=width||yy<0||yy>=height)continue;
      const j=(yy*width+xx)*4,lo=Math.min(pixels[j],pixels[j+1],pixels[j+2]),hi=Math.max(pixels[j],pixels[j+1],pixels[j+2]);
      if(hi<130&&hi-lo<55){outline=true;break;}
    }
    if(outline)mask[y*width+x]=1;
  }
  for(let p=0;p<mask.length;p++){const v=mask[p]?0:255,i=p*4;pixels[i]=v;pixels[i+1]=v;pixels[i+2]=v;pixels[i+3]=255;}
  context.putImageData(frame,0,0);
}
const decodeBitmap=(blob:Blob,signal?:AbortSignal)=>new Promise<ImageBitmap>((resolve,reject)=>{
  let settled=false;
  const finish=(error?:Error,bitmap?:ImageBitmap)=>{
    if(settled){bitmap?.close();return;}
    settled=true;clearTimeout(timer);signal?.removeEventListener('abort',abort);
    if(error)reject(error);else resolve(bitmap!);
  };
  const timer=setTimeout(()=>finish(new Error('This image took too long to decode. Crop or resize it and try again.')),15000);
  const abort=()=>finish(cancelled());
  signal?.addEventListener('abort',abort,{once:true});
  if(signal?.aborted){abort();return;}
  createImageBitmap(blob).then(bitmap=>finish(undefined,bitmap),()=>finish(new Error('This image could not be decoded. Save it as a PNG or JPEG and try again.')));
});

export async function recognizeWalkImage(blob:Blob,onProgress?:(message:string)=>void,signal?:AbortSignal):Promise<WalkImageRecognition>{
  if(signal?.aborted)throw cancelled();
  if(!blob.size||blob.size>MAX_BYTES)throw new Error('Choose an image smaller than 20 MiB.');
  walkImageDimensions(new Uint8Array(await blob.arrayBuffer()));
  const bitmap=await decodeBitmap(blob,signal);
  const original={width:bitmap.width,height:bitmap.height};
  if(original.width*original.height>MAX_PIXELS){bitmap.close();throw new Error('This image exceeds 20 million pixels. Crop or resize it before importing.');}
  let reader:WalkOcrWorker|undefined;
  const canvas=document.createElement('canvas');
  try{
    onProgress?.('Starting the local image reader…');
    const base=new URL(ASSET_PATH+'/',window.location.origin).href.replace(/\/$/,'');
    reader=new WalkOcrWorker(new Worker(`${base}/worker.min.js`),onProgress,TIME_LIMIT,signal);
    await reader.job('load',{options:{lstmOnly:true,corePath:base,logging:false}});
    await reader.job('loadLanguage',{langs:'eng',options:{langPath:base,cacheMethod:'none',gzip:true,lstmOnly:true}});
    await reader.job('initialize',{langs:'eng',oem:1,config:{}});
    await reader.job('setParameters',{params:{tessedit_pageseg_mode:'11',preserve_interword_spaces:'1'}});
    const scale=Math.min(2,MAX_SIDE/Math.max(original.width,original.height));
    const scaled={width:Math.max(1,Math.round(original.width*scale)),height:Math.max(1,Math.round(original.height*scale))};
    const lines:WalkImageLine[]=[];
    for(const [angle,enhanced] of [[0,false],[0,true],[90,true],[-90,true],[12,true],[-12,true],[78,true],[-78,true]] as const){
      onProgress?.(`Reading map labels (${enhanced?'enhanced ':''}${angle===0?'horizontal':angle===90?'clockwise':'counterclockwise'})…`);
      const radians=angle*Math.PI/180;
      canvas.width=Math.round(Math.abs(scaled.width*Math.cos(radians))+Math.abs(scaled.height*Math.sin(radians)));
      canvas.height=Math.round(Math.abs(scaled.width*Math.sin(radians))+Math.abs(scaled.height*Math.cos(radians)));
      const ctx=canvas.getContext('2d');
      if(!ctx)throw new Error('This browser cannot prepare images for reading.');
      ctx.fillStyle='#fff';ctx.fillRect(0,0,canvas.width,canvas.height);
      ctx.translate(canvas.width/2,canvas.height/2);ctx.rotate(angle*Math.PI/180);
      ctx.drawImage(bitmap,-scaled.width/2,-scaled.height/2,scaled.width,scaled.height);
      if(enhanced)mapLabelMask(ctx,canvas.width,canvas.height);
      const image=new Uint8Array(await (await canvasBlob(canvas)).arrayBuffer());
      const page=await reader.job<OcrPage>('recognize',{image,options:{rotateAuto:false},output:{text:true,blocks:true}});
      for(const block of page.blocks??[])for(const paragraph of block.paragraphs??[])for(const line of paragraph.lines??[]){
        const text=line.text.replace(/\s+/g,' ').trim();
        if(!/[A-Za-z]{2}/.test(text)||line.confidence<15)continue;
        lines.push({text,confidence:line.confidence,...walkImageOriginalBox(line.bbox,original,scaled,angle)});
        if(lines.length>2000)throw new Error('This image contains too many labels. Crop a smaller area and try again.');
      }
    }
    if(signal?.aborted)throw cancelled();
    return {...original,lines:mergeWalkImageLines(lines)};
  }finally{reader?.close();bitmap.close();canvas.width=0;canvas.height=0;}
}
