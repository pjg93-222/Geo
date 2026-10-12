// Printed walk list (PDF): a cover page with the route map and door script, then list pages, one street side at a time in
// walking order. jsPDF and its table plugin load on demand, so they stay out of the main bundle.
import type {jsPDF} from 'jspdf';
import type {UserOptions} from 'jspdf-autotable';
import type {WalkListPayload,WalkRun} from '../api/walkTypes';
import {codeLegend,countText,doorAddress,fullDate,houseSpan,phoneText,regDate,scoreCell,scoreColumns,sideLabel,tallyText,totalsLine,walkFileName,areaText} from './walk';
import type {ScoreKey,WalkScoreMap} from './walk';
import {DOOR_SCRIPT,choiceText} from './walkScript';
import {box4,boxesHit,cleanLine,clipLine,doorPoints,fitFrame,growBounds,inArea,isListed,labelPoint,lineLength,listedPrecincts,offsetLine,pointAlong,
  ringsInBox,runLine,spotAlong,streetKey,thin,viewBounds} from './walkGeo';
import type {Fit,Frame,XY} from './walkGeo';

/** `scores`: the Print scores toggle (off by default, which leaves the pages as they were): adds a SCORES group of Ovr, Sup (candidate districts) and Part columns. */
/** `watermark`: who printed the list and when (hosted servers; walk.ts watermarkText), written on every page and faintly across it. Without it the pages are exactly as before. */
export interface WalkPdfOptions{area?:string;fileName?:string;scores?:WalkScoreMap;watermark?:string}

type RGB=[number,number,number];
type Box=Frame;

const INK:RGB=[24,24,24],MUTED:RGB=[105,105,105],RULE:RGB=[130,130,130],FRAME:RGB=[190,190,190];
const MARGIN=36;
const TABLE_TOP=66;      // first head row of a list page
const TABLE_BOTTOM=48;   // clear space under the last row, above the footer
const CHUNK=10;          // most voters printed in one household row
const ABS_LABEL='Abs = mail ballots';

// --- text safety ---------------------------------------------------------------------------------------------------
// The standard PDF fonts cover Latin-1 plus the Windows punctuation block; anything else would print as garbage.
const WIN_PUNCT='€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ';
const LATIN_FROM='ŁłĐđıĦħŦŧ',LATIN_TO='LlDdiHhTt';   // letters with no accent to drop
const printable=(c:number):boolean=>(c>=0x20&&c<0x7f)||(c>=0xa0&&c<=0xff)||WIN_PUNCT.includes(String.fromCodePoint(c));
/** Text the standard fonts can print: accents are dropped where there is no glyph, other characters become "?". */
function safe(text:string):string{
  let out='';
  for(const ch of String(text??'').normalize('NFC')){
    const c=ch.codePointAt(0) as number;
    if(printable(c))out+=ch;
    else if(c<0x20||c===0x7f||c===8232||c===8233)out+=' ';
    else if(c>=8208&&c<=8213||c===0x2212)out+='-';
    else if(c===0x200b||c===0xfeff)continue;
    else if(LATIN_FROM.includes(ch))out+=LATIN_TO[LATIN_FROM.indexOf(ch)];
    else{const base=ch.normalize('NFD').replace(/\p{M}/gu,'');if(base)out+=[...base].every(b=>printable(b.codePointAt(0) as number))?base:'?';}
  }
  return out;
}
function fit(doc:jsPDF,text:string,maxW:number):string{
  let t=safe(text);if(doc.getTextWidth(t)<=maxW)return t;
  while(t.length>1&&doc.getTextWidth(t+'...')>maxW)t=t.slice(0,-1);
  return t.trimEnd()+'...';
}

// --- drawn glyphs: the standard fonts have no arrow or approximately-equal sign ------------------------------------------------
function arrow(doc:jsPDF,x:number,y:number,fs:number):void{
  const cy=y-fs*0.3,w=fs*0.8,h=fs*0.17;
  doc.setLineWidth(Math.max(0.5,fs*0.07));
  doc.line(x+fs*0.05,cy,x+w,cy);doc.line(x+w-h*1.3,cy-h,x+w,cy);doc.line(x+w-h*1.3,cy+h,x+w,cy);
}
function approx(doc:jsPDF,x:number,y:number,fs:number):void{
  const w=fs*0.56,h=fs*0.08;
  doc.setLineWidth(Math.max(0.5,fs*0.07));
  for(const dy of [-0.4,-0.18])doc.lines([[w/4,-h],[w/2,2*h],[w/4,-h]],x+fs*0.03,y+fs*dy,[1,1],'S',false);
}
const GLYPH=/(→|≈)/;
const glyphWidth=(doc:jsPDF,part:string,fs:number):number=>part==='→'?fs*0.9:part==='≈'?fs*0.66:doc.getTextWidth(safe(part));
/** One line of text in the current font, with → and ≈ drawn as small vector marks. x is the left edge, or the right edge when aligned right. */
function rich(doc:jsPDF,text:string,x:number,y:number,align:'left'|'right'='left'):number{
  const fs=doc.getFontSize(),parts=text.split(GLYPH).filter(Boolean);
  const total=parts.reduce((s,p)=>s+glyphWidth(doc,p,fs),0);
  let cx=align==='right'?x-total:x;
  doc.setDrawColor(doc.getTextColor());
  for(const p of parts){
    if(p==='→')arrow(doc,cx,y,fs);else if(p==='≈')approx(doc,cx,y,fs);else doc.text(safe(p),cx,y);
    cx+=glyphWidth(doc,p,fs);
  }
  return total;
}

// --- cover: map ----------------------------------------------------------------------------------------------------
// Drawn as vectors, no images: land, the precincts, every street around the route, other homes, then the route and its stops on top.
const LAND:RGB=[238,233,223],PAPER:RGB=[247,237,228],WALK:RGB=[87,68,123],CASING:RGB=[226,220,211],WHITE:RGB=[255,255,255],LINK:RGB=[200,194,185],HOME:RGB=[180,173,163];
const PATHS=300;                 // lines in one path operator, so no path grows without limit
const MAX_HOMES=20000;           // more dots than this are thinned evenly
const STREET_FS=7,ROAD_FS=6,AREA_FS=7.5,HALO=2.4;
const SIDE=7.4;                  // a street name sits this far from the centreline, on the side that is not walked
const ROAD_KINDS=['S1400','S1200'] as const;   // narrow first: the wider road's edge never cuts across the narrow one
const ROAD_WIDTH={S1400:[3.8,2.3],S1200:[5.4,3.7]};   // casing, fill (points, at the usual scale)
const SPOTS:XY[]=[[0,0],...[14,28,42,60,84].flatMap(r=>Array.from({length:8},(_,k):XY=>[r*Math.cos(k*Math.PI/4),r*0.75*Math.sin(k*Math.PI/4)]))];

export interface WalkMapLabel{text:string;kind:'street'|'road'|'precinct';corners:XY[]}
/** What the cover map drew, counted from the lists it drew from; `labels` are the boxes the names occupy. */
export interface WalkMapStats{
  frame:Box;from:'context'|'list'|'route'|'none';metresPerPoint:number;
  roads:{S1200:number;S1400:number;V:number};precincts:number;homes:number;doors:number;stops:number;labels:WalkMapLabel[];
}
interface Sheet{doc:jsPDF;box:Box;geo:Fit;px:(p:XY)=>XY;wf:number;stats:WalkMapStats}
interface Road{k:'S1200'|'S1400'|'V';n:string;pts:XY[]}
interface Area{a:{id:string;label:string;rings:unknown};rings:XY[][]}
interface Cand{c:XY;u:XY}
interface Label{text:string;kind:WalkMapLabel['kind'];fs:number;bold:boolean;color:RGB;c:XY;u:XY;w:number;corners:XY[]}

function trace(doc:jsPDF,pts:XY[],close=false):void{
  doc.moveTo(pts[0][0],pts[0][1]);
  for(let i=1;i<pts.length;i++)doc.lineTo(pts[i][0],pts[i][1]);
  if(close)doc.close();
}
/** Strokes lines in the current colour and width, a few hundred to a path; answers how many it drew. */
function strokeAll(doc:jsPDF,lines:XY[][]):number{
  let n=0;
  for(const pts of lines){if(pts.length<2)continue;trace(doc,pts);if(++n%PATHS===0)doc.stroke();}
  if(n%PATHS)doc.stroke();
  return n;
}
/** Round dots in the current colour: a line of no length with round caps is a filled circle, and costs far less than a drawn circle. */
function dotAll(doc:jsPDF,pts:XY[],radius:number):void{
  doc.setLineWidth(radius*2);doc.setLineCap('round');
  pts.forEach((p,i)=>{doc.moveTo(p[0],p[1]);doc.lineTo(p[0]+0.01,p[1]);if((i+1)%PATHS===0)doc.stroke();});
  if(pts.length%PATHS)doc.stroke();
}
function sampleLines(lines:XY[][],step:number):XY[]{
  const out:XY[]=[];
  for(const l of lines)for(let i=1;i<l.length;i++){
    const a=l[i-1],b=l[i],n=Math.max(1,Math.ceil(Math.hypot(b[0]-a[0],b[1]-a[1])/step));
    for(let k=0;k<n;k++)out.push([a[0]+(b[0]-a[0])*k/n,a[1]+(b[1]-a[1])*k/n]);
  }
  return out;
}
const rect4=(r:Box):XY[]=>[[r.x,r.y],[r.x+r.w,r.y],[r.x+r.w,r.y+r.h],[r.x,r.y+r.h]];
/** Text reads left to right, and upward when the line is vertical. */
const upright=(u:XY):XY=>u[0]<-1e-6||(Math.abs(u[0])<=1e-6&&u[1]>0)?[-u[0],-u[1]]:u;

// land: precinct shading, streets, precinct lines
function prepareAreas(s:Sheet,ctx:WalkListPayload['context']):Area[]{
  const list=ctx?.precincts;
  return (Array.isArray(list)?list:[]).filter(a=>a&&ringsInBox(a.rings,s.geo.view))
    .map(a=>({a,rings:(Array.isArray(a.rings)?a.rings:[]).map(r=>thin(cleanLine(r).map(s.px),0.3)).filter(r=>r.length>2)})).filter(x=>x.rings.length);
}
function prepareRoads(s:Sheet,ctx:WalkListPayload['context']):Road[]{
  const list=ctx?.roads,v=s.geo.view,out:Road[]=[];
  for(const r of Array.isArray(list)?list:[]){
    const k=r?.k;if(k!=='S1200'&&k!=='S1400'&&k!=='V')continue;
    const pts=cleanLine(r.c),b=growBounds(null,pts);
    if(pts.length<2||!b||b[0]>v[2]||b[2]<v[0]||b[1]>v[3]||b[3]<v[1])continue;
    out.push({k,n:typeof r.n==='string'?r.n:'',pts:thin(pts.map(s.px),0.3)});
  }
  return out;
}
function drawLand(s:Sheet,areas:Area[],roads:Road[],listed:Set<string>):void{
  const {doc,wf,stats}=s,linesOf=(k:Road['k'])=>roads.filter(r=>r.k===k).map(r=>r.pts);
  doc.setFillColor(...PAPER);
  for(const x of areas)if(isListed(x.a,listed)){for(const r of x.rings)trace(doc,r,true);doc.fillEvenOdd();}
  doc.setDrawColor(...CASING);
  for(const k of ROAD_KINDS){doc.setLineWidth(ROAD_WIDTH[k][0]*wf);stats.roads[k]=strokeAll(doc,linesOf(k));}
  doc.setDrawColor(...WHITE);
  for(const k of ROAD_KINDS){doc.setLineWidth(ROAD_WIDTH[k][1]*wf);strokeAll(doc,linesOf(k));}
  doc.setDrawColor(...LINK);doc.setLineWidth(0.7);doc.setLineCap('butt');doc.setLineDashPattern([2.4,1.8],0);
  stats.roads.V=strokeAll(doc,linesOf('V'));
  doc.setDrawColor(...WALK);doc.setLineWidth(0.8);doc.setLineDashPattern([3.2,2.4],0);
  for(const x of areas){for(const r of x.rings)trace(doc,r,true);doc.stroke();}
  stats.precincts=areas.length;
  doc.setLineDashPattern([],0);doc.setLineCap('round');
}

// route: walk beside the street on the walked side, dashed leads between stops, homes and doors, direction heads
interface Route{centre:XY[][];walks:XY[][];leads:XY[][];starts:(XY|null)[]}
function drawRoute(s:Sheet,runs:WalkRun[],ctx:WalkListPayload['context']):Route{
  const {doc,geo,px,wf,stats}=s,v=geo.view,gap=1.9*wf+1.3;
  const inView=(p:XY)=>p[0]>=v[0]&&p[0]<=v[2]&&p[1]>=v[1]&&p[1]<=v[3];
  const centre=runs.map(r=>runLine(r).map(px));
  const walks=centre.map((w,i)=>offsetLine(w,Math.sign(Number(runs[i].lr)||0)*gap));
  const leads=runs.map(r=>cleanLine(r.approach).map(px));
  doc.setDrawColor(...WHITE);doc.setLineWidth(3.4);strokeAll(doc,walks);
  const homes=cleanLine(ctx?.homes).filter(inView),every=Math.max(1,Math.ceil(homes.length/MAX_HOMES));
  const dots=homes.filter((_,i)=>i%every===0).map(px);
  if(dots.length){doc.setDrawColor(...HOME);dotAll(doc,dots,0.6);}
  stats.homes=dots.length;
  const doors=runs.flatMap(r=>doorPoints(r)).filter(inView).map(px);
  if(doors.length){doc.setDrawColor(...WALK);dotAll(doc,doors,1.15);}
  stats.doors=doors.length;
  doc.setLineWidth(1);doc.setLineCap('butt');doc.setLineDashPattern([3,2.5],0);strokeAll(doc,leads);doc.setLineDashPattern([],0);
  doc.setLineCap('round');doc.setLineWidth(1.6);strokeAll(doc,walks);
  doc.setFillColor(...WALK);
  for(const w of walks){
    if(w.length<2||lineLength(w)<24)continue;
    const b=w[w.length-1],a=[...w].reverse().find(p=>Math.hypot(p[0]-b[0],p[1]-b[1])>2);
    if(!a)continue;
    const t=Math.atan2(b[1]-a[1],b[0]-a[0]);
    doc.triangle(b[0]+Math.cos(t)*1.8,b[1]+Math.sin(t)*1.8,b[0]-Math.cos(t-0.5)*4.6,b[1]-Math.sin(t-0.5)*4.6,b[0]-Math.cos(t+0.5)*4.6,b[1]-Math.sin(t+0.5)*4.6,'F');
  }
  const starts=runs.map((r,i):XY|null=>{
    if(walks[i][0])return walks[i][0];
    const door=doorPoints(r)[0];
    return door?px(door):null;
  });
  return {centre,walks,leads,starts};
}
/** Moves each stop circle clear of the ones before it, so short stops side by side keep every number readable. */
function spread(pts:(XY|null)[],gap:number,box:Box):(XY|null)[] {
  const out:(XY|null)[]=[];
  for(const p of pts){
    if(!p){out.push(null);continue;}
    let q:XY=[p[0],p[1]];
    for(let n=0;n<8;n++){
      const o=out.find(c=>c&&Math.hypot(c[0]-q[0],c[1]-q[1])<gap);
      if(!o)break;
      const d=Math.hypot(q[0]-o[0],q[1]-o[1]);
      q=d<0.5?[o[0]+gap,o[1]+gap*0.3]:[o[0]+(q[0]-o[0])/d*gap,o[1]+(q[1]-o[1])/d*gap];
    }
    out.push([Math.min(Math.max(q[0],box.x+8),box.x+box.w-8),Math.min(Math.max(q[1],box.y+8),box.y+box.h-8)]);
  }
  return out;
}

// names: a street's name on the side that is not walked, along the line when it runs straight and beside it when it bends
const BESIDE:XY[]=[[0,-8],[0,12],[0,-16],[0,20]];
function streetCands(line:XY[],len:number,w:number,lr:number):Cand[] {
  const out:Cand[]=[];
  for(const f of [0.5,0.3,0.7,0.15,0.85]){
    const sp=spotAlong(line,len*f,w+4),[ux,uy]=sp.u;
    if(sp.straight)for(const o of lr?[-lr*SIDE,0,lr*SIDE]:[0])out.push({c:[sp.at[0]+uy*o,sp.at[1]-ux*o],u:upright(sp.u)});
    else for(const [dx,dy] of [...BESIDE,[w/2+10,2] as XY,[-w/2-10,2] as XY])out.push({c:[sp.at[0]+dx,sp.at[1]+dy],u:[1,0]});
  }
  return out;
}
interface Names{runs:WalkRun[];centre:XY[][];roads:Road[];areas:Area[];taken:XY[][];samples:XY[]}
function layoutNames(s:Sheet,n:Names):Label[] {
  const {doc,box,geo,px}=s,ok={x:box.x+3,y:box.y+3,w:box.w-6,h:box.h-6},out:Label[]=[],taken=[...n.taken];
  const inside=(c:XY[])=>c.every(p=>p[0]>=ok.x&&p[0]<=ok.x+ok.w&&p[1]>=ok.y&&p[1]<=ok.y+ok.h);
  const text=(t:string,fs:number,bold:boolean,max:number)=>{doc.setFont('helvetica',bold?'bold':'normal');doc.setFontSize(fs);const str=fit(doc,t,max);return {str,w:doc.getTextWidth(str)};};
  /** The label goes at the first candidate that is inside the frame and clear of everything placed; `force` keeps it (nudged inside) when none is. */
  const put=(kind:Label['kind'],str:string,w:number,fs:number,bold:boolean,color:RGB,cands:Cand[],force:boolean,extra?:(q:XY[],k:Cand)=>boolean):boolean=>{
    const corners=(k:Cand)=>box4(k.c,k.u,w/2+1.5,fs*0.55+0.9);
    let pick=cands.find(k=>{const q=corners(k);return inside(q)&&!taken.some(t=>boxesHit(q,t))&&(!extra||extra(q,k));});
    if(!pick){
      if(!force||!cands.length)return false;
      const k=cands[0],q=corners(k),xs=q.map(p=>p[0]),ys=q.map(p=>p[1]);
      pick={c:[k.c[0]+Math.max(0,ok.x-Math.min(...xs))-Math.max(0,Math.max(...xs)-ok.x-ok.w),k.c[1]+Math.max(0,ok.y-Math.min(...ys))-Math.max(0,Math.max(...ys)-ok.y-ok.h)],u:k.u};
    }
    const q=corners(pick);
    taken.push(q);out.push({text:str,kind,fs,bold,color,c:pick.c,u:pick.u,w,corners:q});
    return true;
  };
  // the streets of the list, longest first; the two sides of one street share a name
  const seen=new Map<number,XY[]>(),listedKeys=new Set<string>();
  const order=n.runs.map((r,i)=>({r,line:n.centre[i],len:lineLength(n.centre[i])})).filter(x=>x.line.length>1&&x.len>0).sort((p,q)=>q.len-p.len);
  for(const x of order){
    const {str,w}=text(x.r.street,STREET_FS,true,130);
    listedKeys.add(streetKey(x.r.street));
    if(!str)continue;
    const mid=pointAlong(x.line,x.len/2).at,near=seen.get(x.r.street_id)??[];
    if(near.some(m=>Math.hypot(m[0]-mid[0],m[1]-mid[1])<Math.max(70,w*1.3)))continue;
    if(put('street',str,w,STREET_FS,true,INK,streetCands(x.line,x.len,w,Number(x.r.lr)||0),true))seen.set(x.r.street_id,[...near,mid]);
  }
  // precinct codes at the label point of each area, moved aside when the route or a name is in the way; always inside the area's fill, so
  // never in a hole where another precinct lies
  const inner:[number,number,number,number]=[geo.view[0]+3/(geo.kx*geo.scale),geo.view[1]+3/geo.scale,geo.view[2]-3/(geo.kx*geo.scale),geo.view[3]-3/geo.scale];
  for(const x of n.areas){
    const {str,w}=text(String(x.a.label||x.a.id||''),AREA_FS,true,100),p=labelPoint(x.a.rings,inner);
    if(!str||!p)continue;
    const at=px(p);
    const cands=SPOTS.map(([dx,dy]):Cand=>({c:[at[0]+dx,at[1]+dy],u:[1,0]}));
    put('precinct',str,w,AREA_FS,true,WALK,cands,true,(q,k)=>{
      const xs=q.map(c=>c[0]),ys=q.map(c=>c[1]),x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);
      return inArea(k.c,x.rings)&&!n.samples.some(c=>c[0]>=x0&&c[0]<=x1&&c[1]>=y0&&c[1]<=y1);
    });
  }
  // the bigger roads around, once per name at the longest stretch that shows
  const roads=new Map<string,{name:string;piece:XY[];len:number}>();
  for(const r of n.roads)if(r.k==='S1200'&&r.n){
    const key=streetKey(r.n);
    if(!key||listedKeys.has(key))continue;
    for(const piece of clipLine(r.pts,ok)){const len=lineLength(piece),cur=roads.get(key);if(!cur||len>cur.len)roads.set(key,{name:r.n,piece,len});}
  }
  let more=14;
  for(const x of [...roads.values()].sort((p,q)=>q.len-p.len)){
    if(more<=0)break;
    const {str,w}=text(x.name,ROAD_FS,false,110);
    if(!str||x.len<w+10)continue;
    const cands=[0.5,0.3,0.7].map(f=>spotAlong(x.piece,x.len*f,w+4)).filter(sp=>sp.straight).map((sp):Cand=>({c:sp.at,u:upright(sp.u)}));
    if(put('road',str,w,ROAD_FS,false,MUTED,cands,false))more--;
  }
  return out;
}
function paintLabel(doc:jsPDF,l:Label):void{
  const [ux,uy]=l.u,deg=-Math.atan2(uy,ux)*180/Math.PI;
  const x=l.c[0]-ux*l.w/2-uy*l.fs*0.33,y=l.c[1]-uy*l.w/2+ux*l.fs*0.33,pose=Math.abs(deg)>0.01?{angle:deg}:{};
  doc.setFont('helvetica',l.bold?'bold':'normal');doc.setFontSize(l.fs);
  doc.setDrawColor(...WHITE);doc.setLineWidth(HALO);doc.setLineJoin('round');
  doc.text(l.text,x,y,{...pose,renderingMode:'stroke'});
  doc.setTextColor(...l.color);doc.text(l.text,x,y,pose);
}

// scale bar and north mark sit on small white cards
function chrome(s:Sheet){
  const {doc,box,geo}=s,nice=[10,20,25,50,100,200,250,500,1000,2000,2500,5000,10000];
  let len=nice[0];for(const n of nice)if(n/geo.metresPerUnit<=96)len=n;
  const bar=len/geo.metresPerUnit,label=len>=1000?`${len/1000} km`:`${len} m`;
  doc.setFont('helvetica','normal');doc.setFontSize(7);
  const scale:Box={x:box.x+6,y:box.y+box.h-24,w:bar+doc.getTextWidth(label)+24,h:18},north:Box={x:box.x+box.w-28,y:box.y+6,w:22,h:38};
  return {
    boxes:[rect4(scale),rect4(north)],
    draw(){
      doc.setFillColor(...WHITE);doc.setDrawColor(...FRAME);doc.setLineWidth(0.5);
      doc.rect(scale.x,scale.y,scale.w,scale.h,'FD');doc.rect(north.x,north.y,north.w,north.h,'FD');
      const bx=scale.x+8,by=scale.y+9;
      doc.setDrawColor(...INK);doc.setLineWidth(1);doc.setLineCap('butt');
      doc.line(bx,by,bx+bar,by);doc.line(bx,by-3,bx,by+3);doc.line(bx+bar,by-3,bx+bar,by+3);
      doc.setFont('helvetica','normal');doc.setFontSize(7);doc.setTextColor(...INK);doc.text(label,bx+bar+6,by+2.5);
      const nx=north.x+north.w/2,ny=north.y+31;
      doc.setFillColor(...INK);doc.triangle(nx,ny-14,nx-4.5,ny,nx+4.5,ny,'F');
      doc.setFont('helvetica','bold');doc.text('N',nx,ny-17,{align:'center'});
    },
  };
}

function drawMap(doc:jsPDF,payload:WalkListPayload,box:Box):WalkMapStats {
  const runs=payload.runs??[],ctx=payload.context;
  const stats:WalkMapStats={frame:{...box},from:'none',metresPerPoint:0,roads:{S1200:0,S1400:0,V:0},precincts:0,homes:0,doors:0,stops:0,labels:[]};
  doc.setFillColor(...LAND);doc.rect(box.x,box.y,box.w,box.h,'F');
  const view=viewBounds(payload);
  if(!view){
    doc.setDrawColor(...FRAME);doc.setLineWidth(0.6);doc.rect(box.x,box.y,box.w,box.h,'S');
    doc.setFont('helvetica','normal');doc.setFontSize(9);doc.setTextColor(...MUTED);
    doc.text('No route geometry for this list.',box.x+12,box.y+20);
    return stats;
  }
  // equal scale both ways: east-west degrees shrink by cos(latitude); a context box is fitted nearly edge to edge, a bare route with room round it
  const geo=fitFrame(view.bounds,box,view.from==='context'?4:26);
  const s:Sheet={doc,box,geo,px:geo.project,wf:Math.min(1.6,Math.max(0.7,(1/geo.metresPerUnit/0.35)**0.4)),stats};
  stats.from=view.from;stats.metresPerPoint=geo.metresPerUnit;
  doc.saveGraphicsState();
  doc.rect(box.x,box.y,box.w,box.h,null);doc.clip();doc.discardPath();   // everything below is cut at the frame
  doc.setLineCap('round');doc.setLineJoin('round');
  const areas=prepareAreas(s,ctx),roads=prepareRoads(s,ctx);
  drawLand(s,areas,roads,listedPrecincts(runs));
  const route=drawRoute(s,runs,ctx),bar=chrome(s),circles=spread(route.starts,12.6,box);
  const taken=[...circles.flatMap(c=>c?[box4(c,[1,0],8.2,8.2)]:[]),...bar.boxes];
  const labels=layoutNames(s,{runs,centre:route.centre,roads,areas,taken,samples:sampleLines([...route.walks,...route.leads],4)});
  for(const kind of ['road','precinct','street'] as const)for(const l of labels)if(l.kind===kind)paintLabel(doc,l);
  doc.setDrawColor(...WHITE);doc.setLineWidth(1);
  runs.forEach((r,i)=>{
    const c=circles[i];if(!c)return;
    doc.setFillColor(...WALK);doc.circle(c[0],c[1],6.2,'FD');
    doc.setFont('helvetica','bold');doc.setFontSize(r.stop>99?5:6.5);doc.setTextColor(255,255,255);
    doc.text(String(r.stop),c[0],c[1]+(r.stop>99?1.8:2.3),{align:'center'});
    stats.stops++;
  });
  bar.draw();
  doc.restoreGraphicsState();
  doc.setDrawColor(...FRAME);doc.setLineWidth(0.6);doc.rect(box.x,box.y,box.w,box.h,'S');
  stats.labels=labels.map(({text,kind,corners})=>({text,kind,corners}));
  return stats;
}

/** One row under the map naming what the marks mean; Homes and Precinct only when the map shows them. */
function drawLegend(doc:jsPDF,x:number,y:number,stats:WalkMapStats):void {
  const items:[string,(cx:number)=>void][]=[
    ['Walk',cx=>{doc.setDrawColor(...WALK);doc.setLineWidth(1.6);doc.setLineCap('round');doc.line(cx,y-2.5,cx+16,y-2.5);}],
    ['Between stops',cx=>{doc.setDrawColor(...WALK);doc.setLineWidth(1);doc.setLineCap('butt');doc.setLineDashPattern([3,2.5],0);doc.line(cx,y-2.5,cx+16,y-2.5);doc.setLineDashPattern([],0);}],
    ['Doors',cx=>{doc.setFillColor(...WALK);doc.circle(cx+8,y-2.5,1.7,'F');}],
  ];
  if(stats.homes)items.push(['Homes',cx=>{doc.setFillColor(...HOME);doc.circle(cx+8,y-2.5,1.3,'F');}]);
  if(stats.precincts)items.push(['Precinct',cx=>{
    doc.setFillColor(...PAPER);doc.setDrawColor(...WALK);doc.setLineWidth(0.8);doc.setLineDashPattern([2,1.5],0);doc.rect(cx+1,y-6,14,7,'FD');doc.setLineDashPattern([],0);
  }]);
  items.push(['Stop',cx=>{doc.setFillColor(...WALK);doc.circle(cx+8,y-2.5,4.2,'F');doc.setFont('helvetica','bold');doc.setFontSize(5.5);doc.setTextColor(255,255,255);doc.text('1',cx+8,y-0.6,{align:'center'});}]);
  let cx=x;
  for(const [name,mark] of items){
    mark(cx);
    doc.setFont('helvetica','normal');doc.setFontSize(7.5);doc.setTextColor(...MUTED);
    doc.text(name,cx+21,y);
    cx+=21+doc.getTextWidth(name)+13;
  }
  doc.setLineCap('butt');
}

// --- cover: script and notes -------------------------------------------------------------------------------------------
function drawScript(doc:jsPDF,x:number,top:number,w:number):void {
  let y=top;
  doc.setFont('helvetica','bold');doc.setFontSize(11);doc.setTextColor(...INK);doc.text('Door script',x,y);
  doc.setDrawColor(...FRAME);doc.setLineWidth(0.6);doc.line(x,y+5,x+w,y+5);y+=22;
  const wrap=(text:string,width:number):string[]=>doc.splitTextToSize(safe(text),width) as string[];
  DOOR_SCRIPT.forEach((step,i)=>{
    doc.setFont('helvetica','bold');doc.setFontSize(9);doc.setTextColor(...INK);doc.text(`${i+1}.  ${safe(step.title)}`,x,y);y+=12;
    doc.setFont('helvetica','italic');doc.setFontSize(9);
    for(const l of wrap(`“${step.line}”`,w-12)){doc.text(l,x+12,y);y+=11.5;}
    doc.setFont('helvetica','normal');doc.setFontSize(8.5);doc.setTextColor(...MUTED);
    for(const c of step.choices)wrap(choiceText(c),w-26).forEach((l,j)=>{if(!j)doc.text('•',x+12,y);doc.text(l,x+24,y);y+=11;});
    y+=7;
  });
  doc.setFont('helvetica','bold');doc.setFontSize(9);doc.setTextColor(...INK);doc.text('Result codes',x,y);y+=12;
  doc.setFont('helvetica','normal');doc.setFontSize(8.5);doc.setTextColor(...MUTED);
  for(const l of wrap(`${codeLegend()}. Circle one beside each voter on the list pages.`,w)){doc.text(l,x,y);y+=11;}
}
function yearSpan(list:string[]|undefined):string{
  const ys=(list??[]).map(s=>/\d{4}/.exec(s)?.[0]).filter((s):s is string=>!!s).map(Number).sort((a,b)=>a-b);
  return !ys.length?'':ys[0]===ys[ys.length-1]?String(ys[0]):`${ys[0]}–${ys[ys.length-1]}`;
}

function drawCover(doc:jsPDF,payload:WalkListPayload,area:string,scores?:WalkScoreMap):WalkMapStats {
  const pageW=doc.internal.pageSize.getWidth(),inner=pageW-2*MARGIN;
  doc.setTextColor(...INK);doc.setFont('helvetica','bold');doc.setFontSize(22);
  doc.text(fit(doc,payload.list?.title||'Walk list',inner),MARGIN,64);
  const sub=[area,payload.list?.kind==='reknock'?'Re-knock list':'',`Generated ${fullDate(payload.generated_on)}`].filter(Boolean).join(' · ');
  doc.setFont('helvetica','normal');doc.setFontSize(11);doc.setTextColor(...MUTED);doc.text(fit(doc,sub,inner),MARGIN,84);
  doc.setFont('helvetica','bold');doc.setTextColor(...INK);doc.text(fit(doc,payload.totals?totalsLine(payload.totals):'',inner),MARGIN,106);
  const win=payload.window,pri=yearSpan(win?.primaries),gen=yearSpan(win?.generals);
  const years=pri||gen?` (${[pri&&`primaries ${pri}`,gen&&`generals ${gen}`].filter(Boolean).join(', ')})`:'';
  doc.setFont('helvetica','normal');doc.setFontSize(8);doc.setTextColor(...MUTED);
  doc.text(fit(doc,`Pri and Gen: elections voted out of elections eligible${years}. A lone slash means none were eligible.`,inner),MARGIN,124);
  doc.text(fit(doc,`${win?.abs_rule||ABS_LABEL}: ballots cast by mail or absentee, out of all eligible primaries and generals.`,inner),MARGIN,135);
  rich(doc,'≈ after an address: position estimated from nearby house numbers.',MARGIN,146);
  const cols=scoreColumns(scores);
  if(cols.length)doc.text(fit(doc,`Scores run 0 to 100, higher is more: ${cols.map(k=>SCORE_WORDS[k]).join(', ')}.`,inner-300),pageW-MARGIN,146,{align:'right'});
  const map:Box={x:MARGIN,y:158,w:410,h:388};
  const stats=drawMap(doc,payload,map);
  drawLegend(doc,MARGIN,map.y+map.h+14,stats);
  doc.setFont('helvetica','normal');doc.setFontSize(7.5);doc.setTextColor(...MUTED);
  doc.text('Solid line: street side walked, arrowhead shows direction  ·  Numbered circle: route stop, in walking order',MARGIN,map.y+map.h+26);
  drawScript(doc,MARGIN+map.w+24,map.y+10,pageW-MARGIN-(MARGIN+map.w+24));
  return stats;
}

// --- list pages --------------------------------------------------------------------------------------------------------
const WIDTH={address:80,phone:74,pri:30,gen:30,abs:34,party:36,age:28,gender:44,reg:58,s:20,u:20,o:20,gap:14,nh:26,mv:26};
const KEYS=['address','name','phone','pri','gen','abs','party','age','gender','reg','s','u','o','gap','nh','mv'] as const;
const SCORE_AT=10,SCORE_WIDTH=24;   // the score columns (when printed) sit after Reg. Date, before the result boxes
const SCORE_HEAD:Record<ScoreKey,string>={ovr:'Ovr',sup:'Sup',part:'Part',push:'Push'};
const SCORE_WORDS:Record<ScoreKey,string>={ovr:'Ovr overall',sup:'Sup candidate support',part:'Part participation',push:'Push needs a push'};
interface RowMeta{approx:boolean;cont:boolean}

function drawRunTitle(doc:jsPDF,run:WalkRun,total:number,cont:boolean):void {
  const pageW=doc.internal.pageSize.getWidth(),y=52;
  doc.setFont('helvetica','normal');doc.setFontSize(8);doc.setTextColor(...MUTED);
  const span=houseSpan(run);
  const rightW=rich(doc,[`Route stop ${run.stop} of ${total}`,span&&`walk ${span}`,countText(run.doors.length,'door')].filter(Boolean).join(' · '),pageW-MARGIN,y,'right');
  doc.setFont('helvetica','bold');doc.setFontSize(11);doc.setTextColor(...INK);
  let x=MARGIN;
  if(run.precinct){doc.text(fit(doc,`Precinct: ${run.precinct}`,140),x,y);x+=150;}
  const room=pageW-MARGIN-rightW-16-x,label=fit(doc,`Street: ${run.street} (${sideLabel(run.side)})`,Math.max(room-(cont?42:0),60));
  doc.text(label,x,y);
  const after=x+doc.getTextWidth(label)+5;
  if(cont){doc.setFont('helvetica','normal');doc.text('(cont.)',after,y);}
}

function runRows(run:WalkRun,scores?:WalkScoreMap):{rows:string[][];meta:RowMeta[]} {
  const rows:string[][]=[],meta:RowMeta[]=[],cols=scoreColumns(scores);
  for(const door of run.doors){
    for(let at=0;at<door.voters.length;at+=CHUNK){
      const vs=door.voters.slice(at,at+CHUNK),lines=(f:(i:number)=>string):string=>vs.map((_,i)=>safe(f(i))).join('\n');
      rows.push([
        lines(i=>i===0?doorAddress(door):''),lines(i=>vs[i].name),lines(i=>phoneText(vs[i].phone)),
        lines(i=>tallyText(vs[i].pri)),lines(i=>tallyText(vs[i].gen)),lines(i=>tallyText(vs[i].abs)),
        lines(i=>vs[i].party),lines(i=>vs[i].age==null?'':String(vs[i].age)),lines(i=>vs[i].gender),lines(i=>regDate(vs[i].reg_date)),
        ...cols.map(k=>lines(i=>scoreCell(scores,vs[i].person_id,k))),
        lines(()=>'S'),lines(()=>'U'),lines(()=>'O'),lines(()=>''),lines(()=>'NH'),lines(()=>'MV'),
      ]);
      meta.push({approx:door.placed_by==='interpolated'&&at===0,cont:at>0});
    }
  }
  return {rows,meta};
}

function drawRun(doc:jsPDF,autoTable:(d:jsPDF,o:UserOptions)=>void,run:WalkRun,total:number,scores?:WalkScoreMap):void {
  const {rows,meta}=runRows(run,scores);
  if(!rows.length)return;
  const cols=scoreColumns(scores),keys:string[]=[...KEYS.slice(0,SCORE_AT),...cols,...KEYS.slice(SCORE_AT)];
  const pageW=doc.internal.pageSize.getWidth(),tableW=pageW-2*MARGIN;
  const fixed=Object.values(WIDTH).reduce((s,w)=>s+w,0)+SCORE_WIDTH*cols.length;
  const widths=keys.map(k=>k==='name'?tableW-fixed:(WIDTH as Record<string,number>)[k]??SCORE_WIDTH);
  const group=(content:string,colSpan:number)=>({content,colSpan,styles:{fontStyle:'bold' as const,fontSize:7.5,cellPadding:{top:0,right:0,bottom:1.5,left:0}}});
  const label=(content:string)=>({content,styles:{cellPadding:{top:0,right:3,bottom:3,left:0}}});
  doc.addPage();
  autoTable(doc,{
    startY:TABLE_TOP,margin:{top:TABLE_TOP,right:MARGIN,bottom:TABLE_BOTTOM,left:MARGIN},tableWidth:tableW,
    theme:'plain',showHead:'everyPage',rowPageBreak:'avoid',
    styles:{font:'helvetica',fontSize:9,textColor:INK,cellPadding:{top:3,right:5,bottom:9,left:0},lineWidth:0,overflow:'ellipsize',valign:'top',halign:'left'},
    headStyles:{fontStyle:'normal',fontSize:8.5,textColor:INK,cellPadding:{top:1,right:3,bottom:4,left:0}},
    columnStyles:Object.fromEntries(widths.map((w,i)=>[i,{cellWidth:w}])),
    columns:keys.map((_,i)=>i),
    head:[
      [group('',3),group('VOTER HISTORY',3),group('VOTER INFORMATION',4),...cols.length?[group('SCORES',cols.length)]:[],group('',6)],
      [label('Address'),label('Name'),label('Phone'),label('Pri'),label('Gen'),label('Abs'),label('Party'),label('Age'),label('Gender'),label('Reg. Date'),...cols.map(k=>label(SCORE_HEAD[k])),group('',6)],
    ],
    body:rows,
    willDrawPage:d=>drawRunTitle(doc,run,total,d.pageNumber>1),
    willDrawCell:d=>{
      // thin full-width rule above each household
      if(d.section!=='body'||d.column.index!==0||meta[d.row.index]?.cont)return;
      doc.setDrawColor(...RULE);doc.setLineWidth(0.6);doc.line(MARGIN,d.cell.y,pageW-MARGIN,d.cell.y);
    },
    didDrawCell:d=>{
      const c=d.cell,fs=c.styles.fontSize,base=c.y+c.padding('top')+fs*0.85;
      if(d.section==='head'&&d.row.index===1&&c.text[0]){
        doc.setDrawColor(...INK);doc.setLineWidth(0.5);doc.line(c.x,base+1.8,c.x+doc.getTextWidth(c.text[0]),base+1.8);
      }else if(d.section==='body'&&d.column.index===0&&meta[d.row.index]?.approx){
        doc.setDrawColor(...INK);approx(doc,c.x+c.padding('left')+doc.getTextWidth(c.text[0]??'')+fs*0.3,base,fs);
      }
    },
  });
}

// --- watermark ---------------------------------------------------------------------------------------------------------
interface Faint{GState?:new(o:{opacity:number})=>unknown;setGState?:(g:unknown)=>unknown;saveGraphicsState?:()=>unknown;restoreGraphicsState?:()=>unknown}
/** The watermark on every page: small and dark in the top margin (legible), and large and faint across the middle (a photocopy keeps it). */
function stamp(doc:jsPDF,text:string):void{
  const pageW=doc.internal.pageSize.getWidth(),pageH=doc.internal.pageSize.getHeight(),faint=doc as unknown as Faint;
  for(let p=1;p<=doc.getNumberOfPages();p++){
    doc.setPage(p);doc.setFont('helvetica','normal');doc.setTextColor(...MUTED);doc.setFontSize(8.5);
    doc.text(fit(doc,text,pageW-2*MARGIN-200),pageW-MARGIN,30,{align:'right'});
    if(!(faint.GState&&faint.setGState&&faint.saveGraphicsState&&faint.restoreGraphicsState))continue;   // an older jsPDF keeps the top-margin line alone
    faint.saveGraphicsState();faint.setGState(new faint.GState({opacity:0.09}));
    doc.setFontSize(30);doc.setTextColor(0,0,0);
    const line=fit(doc,text,pageH*1.1),w=doc.getTextWidth(line),a=Math.PI/6;   // 30 degrees up to the right, centred on the page
    doc.text(line,pageW/2-w/2*Math.cos(a),pageH/2+w/2*Math.sin(a),{angle:30});
    faint.restoreGraphicsState();
  }
}

// --- document ----------------------------------------------------------------------------------------------------------
/** Builds the PDF without saving it. `map` counts what the cover map drew. */
export async function buildWalkPdf(payload:WalkListPayload,opts:WalkPdfOptions={}):Promise<{doc:jsPDF;fileName:string;map:WalkMapStats}> {
  const [{jsPDF},{default:autoTable}]=await Promise.all([import('jspdf'),import('jspdf-autotable')]);
  const doc=new jsPDF({orientation:'landscape',unit:'pt',format:'letter',compress:true,floatPrecision:3});   // the map has thousands of points
  const area=(opts.area??areaText(payload.query)).trim();
  const runs=(payload.runs??[]).filter(r=>r.doors.length);
  doc.setLineHeightFactor(1.34);
  doc.setProperties({title:safe(`${payload.list?.title||'Walk list'}${area?` - ${area}`:''}`),subject:'Walk list',creator:'Terrier'});
  const map=drawCover(doc,payload,area,opts.scores);
  for(const run of runs)drawRun(doc,autoTable,run,payload.totals?.stops||runs.length,opts.scores);
  // date top left and footer on every page
  const pageW=doc.internal.pageSize.getWidth(),pageH=doc.internal.pageSize.getHeight(),pages=doc.getNumberOfPages();
  const footer=`${codeLegend()} · ${payload.window?.abs_rule||ABS_LABEL}`;
  for(let p=1;p<=pages;p++){
    doc.setPage(p);doc.setFont('helvetica','normal');doc.setTextColor(...MUTED);
    doc.setFontSize(8.5);doc.text(fullDate(payload.generated_on),MARGIN,30);
    doc.setFontSize(7.5);doc.text(fit(doc,footer,pageW-2*MARGIN-90),MARGIN,pageH-24);
    doc.text(`Page ${p} of ${pages}`,pageW-MARGIN,pageH-24,{align:'right'});
  }
  if(opts.watermark)stamp(doc,opts.watermark);
  return {doc,fileName:opts.fileName||walkFileName(area,payload.generated_on,'pdf'),map};
}

/** Builds the walk list PDF and hands it to the browser as walk-list-<area>-<yyyy-mm-dd>.pdf. Resolves to the file name. */
export async function exportWalkPdf(payload:WalkListPayload,opts:WalkPdfOptions={}):Promise<string> {
  const {doc,fileName}=await buildWalkPdf(payload,opts);
  doc.save(fileName);
  return fileName;
}
