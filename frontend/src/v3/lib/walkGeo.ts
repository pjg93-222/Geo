// Map geometry for the walk list, shared by the in-app map and the printed map: the box to show, an equal-scale projection (east-west
// degrees shrink by cos(latitude)), polygon label points and small line helpers. Pure, and only `import type` from other files, so
// node --test can load it directly (same constraint as walk.ts).
import type {WalkListPayload,WalkRun} from '../api/walkTypes';

export type XY=[number,number];
export type Bounds=[number,number,number,number];   // west, south, east, north
export interface Frame{x:number;y:number;w:number;h:number}

const RAD=Math.PI/180;
const MIN_SPAN=0.0006;   // degrees: a list of one door still gets a street-sized map
const finite=(v:unknown):v is number=>typeof v==='number'&&Number.isFinite(v);
export const validPoint=(p:unknown):p is XY=>Array.isArray(p)&&finite(p[0])&&finite(p[1]);
/** The real coordinates of a list of points; anything else is dropped. */
export const cleanLine=(pts:unknown):XY[]=>Array.isArray(pts)?pts.filter(validPoint):[];

// --- what the map shows ------------------------------------------------------------------------------------------------------
/** A west/south/east/north box, or null unless it is four finite numbers in order (an all-zero box means "no data"). */
export function usableBounds(b:unknown):Bounds|null{
  if(!Array.isArray(b)||b.length!==4||!b.every(finite))return null;
  const [w,s,e,n]=b as number[];
  const ok=w<=e&&s<=n&&(w!==0||s!==0||e!==0||n!==0)&&Math.abs(w)<=180&&Math.abs(e)<=180&&Math.abs(s)<=90&&Math.abs(n)<=90;
  return ok?[w,s,e,n]:null;
}
/** The box widened to take in the points (a new box from the points alone when there is none). */
export function growBounds(b:Bounds|null,pts:Iterable<XY>):Bounds|null{
  let w=b?b[0]:Infinity,s=b?b[1]:Infinity,e=b?b[2]:-Infinity,n=b?b[3]:-Infinity;
  for(const p of pts){w=Math.min(w,p[0]);e=Math.max(e,p[0]);s=Math.min(s,p[1]);n=Math.max(n,p[1]);}
  return Number.isFinite(w)?[w,s,e,n]:null;
}
export const doorPoints=(r:WalkRun):XY[]=>cleanLine((r?.doors??[]).map(d=>[d?.lon,d?.lat]));
/** The centreline walked in a run; the doors stand in when a run has no usable path. */
export const runLine=(r:WalkRun):XY[]=>{const own=cleanLine(r?.path);return own.length>1?own:doorPoints(r);};
/** The box to show: the context box when the list came with one, else the list's own box, else the route; always wide enough for the whole route. */
export function viewBounds(p:Pick<WalkListPayload,'bbox'|'runs'|'context'>):{bounds:Bounds;from:'context'|'list'|'route'}|null{
  const route=growBounds(null,(p.runs??[]).flatMap(r=>[...runLine(r),...cleanLine(r?.approach),...doorPoints(r)]));
  const ctx=usableBounds(p.context?.bbox),own=usableBounds(p.bbox),base=ctx??own;
  const corners:XY[]=route?[[route[0],route[1]],[route[2],route[3]]]:[];
  const bounds=base?growBounds(base,corners):route;
  return bounds?{bounds,from:ctx?'context':own?'list':'route'}:null;
}

// --- projection --------------------------------------------------------------------------------------------------------------
/** Metres in one degree of latitude at this latitude (WGS84 series). */
export const metresPerDegreeLat=(lat:number):number=>111132.92-559.82*Math.cos(2*lat*RAD)+1.175*Math.cos(4*lat*RAD);
export interface Fit{
  project:(p:XY)=>XY;
  kx:number;                    // longitude degrees shrink by this before they are drawn at the same scale as latitude degrees
  scale:number;                 // frame units per degree of latitude
  metresPerUnit:number;
  view:Bounds;                  // the longitude/latitude box the frame shows
}
/** Fits the box into the frame, centred, at one scale in both directions; `pad` keeps that much of the frame clear of the box. */
export function fitFrame(b:Bounds,f:Frame,pad=0):Fit{
  const lat=(b[1]+b[3])/2,cx=(b[0]+b[2])/2,kx=Math.cos(lat*RAD);
  const room=Math.min(pad,f.w/2-1,f.h/2-1);
  const scale=Math.min((f.w-2*room)/Math.max((b[2]-b[0])*kx,MIN_SPAN),(f.h-2*room)/Math.max(b[3]-b[1],MIN_SPAN));
  const hw=f.w/2/(kx*scale),hh=f.h/2/scale;
  return {
    kx,scale,metresPerUnit:metresPerDegreeLat(lat)/scale,view:[cx-hw,lat-hh,cx+hw,lat+hh],
    project:p=>[f.x+f.w/2+(p[0]-cx)*kx*scale,f.y+f.h/2-(p[1]-lat)*scale],
  };
}

// --- polygons ----------------------------------------------------------------------------------------------------------------
const open=(r:XY[]):XY[]=>r.length>1&&r[0][0]===r[r.length-1][0]&&r[0][1]===r[r.length-1][1]?r.slice(0,-1):r;
/** Signed area (positive when the ring runs counter-clockwise on a y-up plane). */
export function ringArea(r:XY[]):number{
  let a=0;
  for(let i=0;i<r.length;i++){const p=r[i],q=r[(i+1)%r.length];a+=p[0]*q[1]-q[0]*p[1];}
  return a/2;
}
export function pointInRing(p:XY,r:XY[]):boolean{
  let inside=false;
  for(let i=0,j=r.length-1;i<r.length;j=i++){
    if((r[i][1]>p[1])!==(r[j][1]>p[1])&&p[0]<(r[j][0]-r[i][0])*(p[1]-r[i][1])/(r[j][1]-r[i][1])+r[i][0])inside=!inside;
  }
  return inside;
}
/** A ring cut to the box (Sutherland-Hodgman); empty when none of it shows. */
export function clipRing(ring:XY[],[w,s,e,n]:Bounds):XY[]{
  const cut:[(p:XY)=>boolean,(a:XY,b:XY)=>XY][]=[
    [p=>p[0]>=w,(a,b)=>[w,a[1]+(b[1]-a[1])*(w-a[0])/(b[0]-a[0])]],
    [p=>p[0]<=e,(a,b)=>[e,a[1]+(b[1]-a[1])*(e-a[0])/(b[0]-a[0])]],
    [p=>p[1]>=s,(a,b)=>[a[0]+(b[0]-a[0])*(s-a[1])/(b[1]-a[1]),s]],
    [p=>p[1]<=n,(a,b)=>[a[0]+(b[0]-a[0])*(n-a[1])/(b[1]-a[1]),n]],
  ];
  let pts=ring;
  for(const [inside,cross] of cut){
    const from=pts;pts=[];
    for(let i=0;i<from.length;i++){
      const a=from[(i+from.length-1)%from.length],b=from[i];
      if(inside(b)){if(!inside(a))pts.push(cross(a,b));pts.push(b);}
      else if(inside(a))pts.push(cross(a,b));
    }
    if(!pts.length)break;
  }
  return pts;
}
/** Is the point in the filled part of an area? A ring inside another is a hole (a ring in a hole is filled again), so a neighbour's enclave is not part of the area. */
export function inArea(p:XY,rings:readonly XY[][]):boolean{
  let inside=false;
  for(const r of rings)if(pointInRing(p,r))inside=!inside;
  return inside;
}
/** The centre of mass of a ring, from its signed area `a`. */
function ringCentre(r:XY[],a:number):XY{
  let cx=0,cy=0;
  for(let i=0;i<r.length;i++){const p=r[i],q=r[(i+1)%r.length],k=p[0]*q[1]-q[0]*p[1];cx+=(p[0]+q[0])*k;cy+=(p[1]+q[1])*k;}
  return [cx/(6*a),cy/(6*a)];
}
/** The middle of the widest stretch of fill along the horizontal line `y`, and its width (0 when the line meets no fill). */
function widestStretch(rings:readonly XY[][],y:number):{at:XY;wide:number}{
  const xs:number[]=[];
  for(const r of rings)for(let i=0;i<r.length;i++){
    const p=r[i],q=r[(i+1)%r.length];
    if((p[1]>y)!==(q[1]>y))xs.push(p[0]+(y-p[1])*(q[0]-p[0])/(q[1]-p[1]));
  }
  xs.sort((m,n)=>m-n);
  let at:XY=[0,y],wide=0;
  for(let i=0;i+1<xs.length;i+=2)if(xs[i+1]-xs[i]>wide){wide=xs[i+1]-xs[i];at=[(xs[i]+xs[i+1])/2,y];}
  return {at,wide};
}
/** Where to write the name of an area: inside its fill, never in a hole (a neighbour's enclave), at the centre of the biggest piece of it
 *  that shows in the box, or the middle of its widest stretch along the line through that centre when the centre falls outside the fill
 *  (a bent shape, or a ring around an enclave). Null when none of the area shows. */
export function labelPoint(rings:unknown,view:Bounds):XY|null{
  let best:XY[][]|null=null,most=0,sign:number[]=[];
  for(const part of areaParts(rings)){
    const shown=part.map(r=>clipRing(r,view));
    if(shown[0].length<3)continue;   // the outer ring is out of view, and so is all of it
    // a ring inside an even number of the others fills, one inside an odd number cuts a hole
    const s=part.map((r,i)=>part.reduce((n,o,j)=>n+(i!==j&&pointInRing(r[0],o)?1:0),0)%2===0?1:-1);
    const area=shown.reduce((sum,r,i)=>sum+(r.length>2?s[i]*Math.abs(ringArea(r)):0),0);
    if(area>most){most=area;best=shown.filter(r=>r.length>2);sign=s.filter((_,i)=>shown[i].length>2);}
  }
  if(!best)return null;
  let cx=0,cy=0,whole=0;
  best.forEach((r,i)=>{const a=ringArea(r);if(!a)return;const c=ringCentre(r,a),w=sign[i]*Math.abs(a);cx+=c[0]*w;cy+=c[1]*w;whole+=w;});
  const centre:XY=[cx/whole,cy/whole];
  if(inArea(centre,best))return centre;
  let hit=widestStretch(best,centre[1]);
  const box=growBounds(null,best.flat());
  if(box&&hit.wide<=(box[2]-box[0])*0.05)   // the line through the centre only grazes the fill: look along a few others for a wider stretch
    for(const f of [0.5,0.25,0.75,0.125,0.875]){const other=widestStretch(best,box[1]+(box[3]-box[1])*f);if(other.wide>hit.wide)hit=other;}
  return hit.wide>0?hit.at:centre;
}
/** The rings of an area as polygons [outer, ...holes]: a ring inside a bigger one is its hole, any other ring is a part of its own. */
export function areaParts(rings:unknown):XY[][][]{
  const all=(Array.isArray(rings)?rings:[]).map(r=>open(cleanLine(r))).filter(r=>r.length>2)
    .sort((p,q)=>Math.abs(ringArea(q))-Math.abs(ringArea(p)));
  const parts:XY[][][]=[];
  for(const r of all){const host=parts.find(p=>pointInRing(r[0],p[0]));if(host)host.push(r);else parts.push([r]);}
  return parts;
}
/** Does the area, as the server sent it, show anywhere in the box? */
export function ringsInBox(rings:unknown,b:Bounds):boolean{
  const box=growBounds(null,(Array.isArray(rings)?rings:[]).flatMap(cleanLine));
  return !!box&&box[0]<=b[2]&&box[2]>=b[0]&&box[1]<=b[3]&&box[3]>=b[1];
}

// --- lines -------------------------------------------------------------------------------------------------------------------
export function lineLength(pts:XY[]):number{
  let n=0;
  for(let i=1;i<pts.length;i++)n+=Math.hypot(pts[i][0]-pts[i-1][0],pts[i][1]-pts[i-1][1]);
  return n;
}
/** The point `d` along the line (held within its ends) and the unit direction of the piece it falls on. */
export function pointAlong(pts:XY[],d:number):{at:XY;u:XY}{
  if(pts.length<2)return {at:pts[0]??[0,0],u:[1,0]};
  let left=Math.max(0,d);
  for(let i=1;i<pts.length;i++){
    const dx=pts[i][0]-pts[i-1][0],dy=pts[i][1]-pts[i-1][1],l=Math.hypot(dx,dy);
    if(l>0&&(left<=l||i===pts.length-1)){const t=Math.min(1,left/l);return {at:[pts[i-1][0]+dx*t,pts[i-1][1]+dy*t],u:[dx/l,dy/l]};}
    left-=l;
  }
  return {at:pts[pts.length-1],u:[1,0]};
}
export interface Spot{at:XY;u:XY;straight:boolean}
/** The point `d` along the line with the direction of the line across a window of length `win` centred there, and whether the line runs
 *  straight enough across that window (and is long enough) for text of that length to lie along it. */
export function spotAlong(pts:XY[],d:number,win:number):Spot{
  const len=lineLength(pts),here=pointAlong(pts,d);
  const a=pointAlong(pts,Math.max(0,d-win/2)).at,b=pointAlong(pts,Math.min(len,d+win/2)).at;
  const chord=Math.hypot(b[0]-a[0],b[1]-a[1]);
  if(chord<1e-9)return {at:here.at,u:here.u,straight:false};
  const u:XY=[(b[0]-a[0])/chord,(b[1]-a[1])/chord];
  const off=(p:XY)=>Math.abs((p[0]-a[0])*u[1]-(p[1]-a[1])*u[0]);
  let bend=off(here.at),run=0;
  for(let i=0;i<pts.length;i++){
    if(i)run+=Math.hypot(pts[i][0]-pts[i-1][0],pts[i][1]-pts[i-1][1]);
    if(run>d-win/2&&run<d+win/2)bend=Math.max(bend,off(pts[i]));
  }
  return {at:here.at,u,straight:chord>=win*0.8&&bend<=win*0.045};
}
/** The line moved sideways by `d` (positive to the left of travel on a y-down page); corners are mitred, at most twice the distance. */
export function offsetLine(pts:XY[],d:number):XY[]{
  const p=pts.filter((q,i)=>!i||q[0]!==pts[i-1][0]||q[1]!==pts[i-1][1]),n=p.length;
  if(n<2||!d)return pts;
  const left:XY[]=[];
  for(let i=1;i<n;i++){const dx=p[i][0]-p[i-1][0],dy=p[i][1]-p[i-1][1],l=Math.hypot(dx,dy);left.push([dy/l,-dx/l]);}
  return p.map((q,i):XY=>{
    const a=left[Math.max(0,i-1)],b=left[Math.min(n-2,i)],sx=a[0]+b[0],sy=a[1]+b[1],l=Math.hypot(sx,sy);
    if(l<1e-9)return [q[0]+a[0]*d,q[1]+a[1]*d];
    const m=[sx/l,sy/l],k=Math.min(2,1/Math.max(1e-6,m[0]*a[0]+m[1]*a[1]));
    return [q[0]+m[0]*d*k,q[1]+m[1]*d*k];
  });
}
/** Drops points closer than `tol` to the one kept before them; the ends stay. */
export function thin(pts:XY[],tol:number):XY[]{
  if(pts.length<3)return pts;
  const out=[pts[0]];
  for(let i=1;i<pts.length-1;i++){const l=out[out.length-1];if(Math.hypot(pts[i][0]-l[0],pts[i][1]-l[1])>=tol)out.push(pts[i]);}
  out.push(pts[pts.length-1]);
  return out;
}
/** The parts of a line that lie inside the frame (Liang-Barsky). */
export function clipLine(pts:XY[],f:Frame):XY[][]{
  const out:XY[][]=[];
  let cur:XY[]=[];
  for(let i=1;i<pts.length;i++){
    const a=pts[i-1],b=pts[i],dx=b[0]-a[0],dy=b[1]-a[1];
    let t0=0,t1=1,ok=true;
    for(const [p,q] of [[-dx,a[0]-f.x],[dx,f.x+f.w-a[0]],[-dy,a[1]-f.y],[dy,f.y+f.h-a[1]]]){
      if(p===0){if(q<0){ok=false;break;}continue;}
      const r=q/p;
      if(p<0){if(r>t1){ok=false;break;}t0=Math.max(t0,r);}else{if(r<t0){ok=false;break;}t1=Math.min(t1,r);}
    }
    if(!ok){if(cur.length>1)out.push(cur);cur=[];continue;}
    if(!cur.length)cur.push([a[0]+dx*t0,a[1]+dy*t0]);
    cur.push([a[0]+dx*t1,a[1]+dy*t1]);
    if(t1<1){out.push(cur);cur=[];}
  }
  if(cur.length>1)out.push(cur);
  return out;
}

// --- label boxes -------------------------------------------------------------------------------------------------------------
/** Corners of a rectangle centred on `c`: `hl` to each side of the centre along `u`, `hw` to each side across it. */
export function box4(c:XY,u:XY,hl:number,hw:number):XY[]{
  const v:XY=[-u[1],u[0]];
  return [[1,1],[1,-1],[-1,-1],[-1,1]].map(([s,t]):XY=>[c[0]+u[0]*hl*s+v[0]*hw*t,c[1]+u[1]*hl*s+v[1]*hw*t]);
}
/** Do two rectangles (corner lists from box4) overlap? Separating axis test. */
export function boxesHit(a:XY[],b:XY[]):boolean{
  for(const poly of [a,b])for(let i=0;i<4;i++){
    const p=poly[i],q=poly[(i+1)%4],ax=q[1]-p[1],ay=p[0]-q[0];
    const span=(r:XY[])=>{const v=r.map(c=>c[0]*ax+c[1]*ay);return [Math.min(...v),Math.max(...v)];};
    const [a0,a1]=span(a),[b0,b1]=span(b);
    if(a1<b0||b1<a0)return false;
  }
  return true;
}

// --- names ------------------------------------------------------------------------------------------------------------------
const SUFFIX:Record<string,string>={DRIVE:'DR',STREET:'ST',ROAD:'RD',AVENUE:'AVE',BOULEVARD:'BLVD',LANE:'LN',COURT:'CT',CIRCLE:'CIR',PLACE:'PL',PARKWAY:'PKWY',HIGHWAY:'HWY',TERRACE:'TER',TRAIL:'TRL'};
const DIRECTION=/^(N|S|E|W|NE|NW|SE|SW|NORTH|SOUTH|EAST|WEST)$/;
/** A street name reduced for comparing: capitals, no punctuation, no direction at either end, common suffixes shortened. */
export function streetKey(name:string):string{
  const words=String(name??'').toUpperCase().replace(/[^A-Z0-9 ]+/g,' ').split(/\s+/).filter(Boolean);
  if(words.length>1&&DIRECTION.test(words[0]))words.shift();
  if(words.length>1&&DIRECTION.test(words[words.length-1]))words.pop();
  return words.map(w=>SUFFIX[w]??w).join('');
}
/** The precinct codes the runs of a list walk through. */
export const listedPrecincts=(runs:WalkRun[]):Set<string>=>new Set((runs??[]).map(r=>String(r?.precinct??'').trim()).filter(Boolean));
export const isListed=(a:{id?:string;label?:string},codes:Set<string>):boolean=>codes.has(String(a?.id??'').trim())||codes.has(String(a?.label??'').trim());
