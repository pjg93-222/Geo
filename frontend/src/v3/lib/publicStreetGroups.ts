import type {PublicBounds,PublicPoint,PublicStreet} from '../api/publicStreets.ts';
import {csvText} from './csv.ts';

export const PUBLIC_SCOPE_WARNING='Counts estimate residential address points within the selected area. They are not verified occupied doors. A street-name CSV does not enforce this area or a partial-road boundary when imported elsewhere.';
export const MAX_PUBLIC_STREETS=5000;
export function boundsPolygon(b:PublicBounds):PublicPoint[]{return [[b[0],b[1]],[b[2],b[1]],[b[2],b[3]],[b[0],b[3]],[b[0],b[1]]];}
export function polygonBounds(p:PublicPoint[]):PublicBounds{
  return [Math.min(...p.map(v=>v[0])),Math.min(...p.map(v=>v[1])),Math.max(...p.map(v=>v[0])),Math.max(...p.map(v=>v[1]))];
}
const cross=(a:PublicPoint,b:PublicPoint,c:PublicPoint)=>(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
const onSegment=(a:PublicPoint,b:PublicPoint,c:PublicPoint)=>Math.abs(cross(a,b,c))<1e-12&&c[0]>=Math.min(a[0],b[0])-1e-12&&c[0]<=Math.max(a[0],b[0])+1e-12&&c[1]>=Math.min(a[1],b[1])-1e-12&&c[1]<=Math.max(a[1],b[1])+1e-12;
function intersects(a:PublicPoint,b:PublicPoint,c:PublicPoint,d:PublicPoint):boolean{
  const ab=cross(a,b,c),ac=cross(a,b,d),cd=cross(c,d,a),ce=cross(c,d,b);
  return (ab*ac<0&&cd*ce<0)||onSegment(a,b,c)||onSegment(a,b,d)||onSegment(c,d,a)||onSegment(c,d,b);
}
/** Geometry validation also applies to the keyboard coordinate fallback. Server validates independently. */
export function validatePublicPolygon(input:PublicPoint[],coverage:PublicBounds):PublicPoint[]{
  if(input.length<3||input.length>200)throw new Error('Choose 3 to 199 polygon corners.');
  const points=input.map(p=>[p[0],p[1]] as PublicPoint);
  if(points.some(p=>!p.every(Number.isFinite)||p[0]<coverage[0]||p[0]>coverage[2]||p[1]<coverage[1]||p[1]>coverage[3]))throw new Error('Keep every corner inside the loaded coverage.');
  if(points[0][0]!==points.at(-1)![0]||points[0][1]!==points.at(-1)![1])points.push([...points[0]]);
  const n=points.length-1;
  if(new Set(points.slice(0,-1).map(p=>p.join(','))).size!==n)throw new Error('Each polygon corner must be distinct.');
  const area=points.slice(0,-1).reduce((s,p,i)=>s+p[0]*points[i+1][1]-points[i+1][0]*p[1],0);
  if(Math.abs(area)<1e-12)throw new Error('Choose an area with a nonzero width and height.');
  for(let i=0;i<n;i++)for(let j=i+1;j<n;j++){
    if(j===i+1||(i===0&&j===n-1))continue;
    if(intersects(points[i],points[i+1],points[j],points[j+1]))throw new Error('Polygon edges cannot cross or touch each other.');
  }
  return points;
}
export function publicStreetCenter(street:PublicStreet):PublicPoint{
  const points:PublicPoint[]=street.address_points.length?street.address_points.map(p=>[p[0],p[1]]):street.geometry.coordinates.flat();
  if(!points.length)return [0,0];
  return [points.reduce((s,p)=>s+p[0],0)/points.length,points.reduce((s,p)=>s+p[1],0)/points.length];
}
/** The two door bases (AUDIENCE_FILTER_SPEC 3.1): public address-point estimates, or voter-file doors matching the audience filter. */
export type Basis='public-estimate'|'voter-file';
/** Basis-neutral grouping input: `doors` is whatever the basis counts. */
export interface StreetCount {id:string;label:string;center:PublicPoint;doors:number}
/** regrouped: formed from streets an earlier pass stranded, so with no distance limit. */
export interface StreetGroup<T extends StreetCount> {streets:T[];doors:number;basis:Basis;regrouped?:boolean}
export interface Grouping<T extends StreetCount> {basis:Basis;groups:StreetGroup<T>[];leftovers:T[];oversized:T[];zero:T[];reason:string|null}
export type PublicStreetCount=PublicStreet&StreetCount;
export type PublicStreetGroup=StreetGroup<PublicStreetCount>&{estimated_doors:number};
export type PublicGrouping=Omit<Grouping<PublicStreetCount>,'groups'>&{groups:PublicStreetGroup[]};
/** The public basis: estimates are the doors, the address points (or the road line) give the center. */
export function groupPublicStreets(input:PublicStreet[],min:number,max:number):PublicGrouping{
  const grouping=groupStreets(input.map(s=>({...s,center:publicStreetCenter(s),doors:s.estimated_doors})),'public-estimate',min,max);
  return {...grouping,groups:grouping.groups.map(g=>({...g,estimated_doors:g.doors}))};
}
/** Bounded spatial-order partitioning. Whole streets stay whole. No claim of road-network connectivity. */
export function groupStreets<T extends StreetCount>(input:T[],basis:Basis,min:number,max:number):Grouping<T>{
  if(!Number.isInteger(min)||!Number.isInteger(max)||min<1||max<min||max>1000)throw new Error('Use whole-number limits from 1 to 1,000, with minimum no larger than maximum.');
  if(input.length>MAX_PUBLIC_STREETS)throw new Error(`Choose at most ${MAX_PUBLIC_STREETS.toLocaleString('en-US')} streets at once.`);
  if(new Set(input.map(s=>s.id)).size!==input.length)throw new Error('Street IDs must be unique.');
  if(input.some(s=>!Number.isInteger(s.doors)||s.doors<0))throw new Error('Street counts must be nonnegative whole numbers.');
  const zero=input.filter(s=>s.doors===0),oversized=input.filter(s=>s.doors>max);
  const usable=input.filter(s=>s.doors>0&&s.doors<=max);
  const total=usable.reduce((s,r)=>s+r.doors,0),target=(min+max)/2;
  if(!usable.length)return {basis,groups:[],leftovers:[],oversized,zero,reason:input.length?'No selected streets can fit these limits.':null};
  // Best of four sweep axes over one street set. Four axes keep work bounded: O(4*n*max), because each positive street contributes at least one door.
  const partition=(streets:T[])=>{
    const latitude=streets.reduce((s,r)=>s+r.center[1],0)/streets.length;
    const lonScale=Math.cos(latitude*Math.PI/180);
    let best:{groups:StreetGroup<T>[];leftovers:T[];covered:number;cost:number}|null=null;
    for(const angle of [0,Math.PI/2,Math.PI/4,-Math.PI/4]){
      const projected=streets.map(street=>{const c=street.center;return {street,x:c[0]*lonScale*111,y:c[1]*111};});
      projected.sort((a,b)=>(a.x-b.x)*Math.cos(angle)+(a.y-b.y)*Math.sin(angle)||a.street.id.localeCompare(b.street.id));
      const n=projected.length,covered=new Int32Array(n+1),cost=new Float64Array(n+1),from=new Int32Array(n+1),taken=new Uint8Array(n+1);
      const sums=Array.from({length:6},()=>new Float64Array(n+1));
      projected.forEach((p,i)=>{const w=p.street.doors;[w,w*p.x,w*p.y,w*p.x*p.x,w*p.y*p.y,1].forEach((v,j)=>sums[j][i+1]=sums[j][i]+v);});
      for(let end=1;end<=n;end++){
        covered[end]=covered[end-1];cost[end]=cost[end-1];from[end]=end-1;
        for(let start=end-1;start>=0;start--){
          const w=sums[0][end]-sums[0][start];if(w>max)break;if(w<min)continue;
          const sx=sums[1][end]-sums[1][start],sy=sums[2][end]-sums[2][start];
          const spread=Math.max(0,sums[3][end]-sums[3][start]+sums[4][end]-sums[4][start]-(sx*sx+sy*sy)/w);
          const value=covered[start]+w,penalty=cost[start]+spread+(w-target)**2/target;
          if(value>covered[end]||(value===covered[end]&&penalty<cost[end])){covered[end]=value;cost[end]=penalty;from[end]=start;taken[end]=1;}
        }
      }
      const groups:StreetGroup<T>[]=[],leftovers:T[]=[];
      for(let end=n;end>0;){const start=from[end];if(taken[end]){const rows=projected.slice(start,end).map(p=>p.street);groups.push({streets:rows,doors:rows.reduce((s,r)=>s+r.doors,0),basis});}else leftovers.push(projected[end-1].street);end=start;}
      groups.reverse();leftovers.reverse();
      const candidate={groups,leftovers,covered:covered[n],cost:cost[n]};
      if(!best||candidate.covered>best.covered||(candidate.covered===best.covered&&candidate.cost<best.cost))best=candidate;
    }
    return best!;
  };
  // Streets a window sweep strands (80,80,70,70 in 100-150) can still pair with each other: regroup the leftovers until a pass adds nothing.
  const first=partition(usable),groups=[...first.groups];let leftovers=first.leftovers;
  while(leftovers.length){const next=partition(leftovers);if(!next.groups.length)break;groups.push(...next.groups.map(g=>({...g,regrouped:true})));leftovers=next.leftovers;}
  const reason=leftovers.length?(Math.ceil(total/max)>Math.floor(total/min)?'These totals cannot all fit the requested minimum and maximum.':'Some whole streets remain outside the bounded spatial grouping. Adjust limits or selections; a different arrangement may fit.'):null;
  return {basis,groups,leftovers,oversized,zero,reason};
}
export const csv=(rows:unknown[][])=>'\uFEFF'+csvText(rows)+'\r\n';
export const round6=(p:PublicPoint[])=>p.map(v=>v.map(x=>Math.round(x*1e6)/1e6));
export const PUBLIC_ZIP='street-groups-public-estimate.zip';
export type PublicStreetStatus='grouped'|'unassigned'|'over_max'|'zero';
export interface PublicExportInfo {
  catalogId:string;catalog:string;sourceDate:string;basis:string;polygon:PublicPoint[];min:number;max:number;generatedAt:string;
  sources:{label:string;url:string}[];
}
export const HEADER=['group','street','street_id','estimated_doors','status','count_basis','catalog','catalog_id','source_date','min_doors','max_doors','generated_at','scope'];
const ROW_SCOPE='Public estimate for the drawn area only; see README.txt';
/** One CSV: each row is a street with its group number (empty unless grouped), status and short provenance; the polygon and full warning live in README.txt. */
export function publicGroupsCsv(rows:{group:number|'';status:PublicStreetStatus;street:PublicStreet}[],info:PublicExportInfo):string{
  return csv([HEADER,...rows.map(r=>[r.group,r.street.label,r.street.id,r.street.estimated_doors,r.status,info.basis,info.catalog,info.catalogId,info.sourceDate,info.min,info.max,info.generatedAt,ROW_SCOPE])]);
}
/** The export bundle: group-NN.csv per group, not-grouped.csv for anything left out, and a README. */
export function publicExportFiles(grouping:PublicGrouping,info:PublicExportInfo):{name:string;data:string}[]{
  const pad=Math.max(2,String(grouping.groups.length).length);
  const files=grouping.groups.map((g,i)=>({name:`group-${String(i+1).padStart(pad,'0')}.csv`,data:publicGroupsCsv(g.streets.map(street=>({group:i+1,status:'grouped' as const,street})),info)}));
  const rest=[...grouping.leftovers.map(street=>({group:'' as const,status:'unassigned' as const,street})),...grouping.oversized.map(street=>({group:'' as const,status:'over_max' as const,street})),...grouping.zero.map(street=>({group:'' as const,status:'zero' as const,street}))];
  if(rest.length)files.push({name:'not-grouped.csv',data:publicGroupsCsv(rest,info)});
  files.push({name:'README.txt',data:[
    'Terrier public street groups','',PUBLIC_SCOPE_WARNING,'',
    `Catalog: ${info.catalog} (${info.catalogId})`,`Source date: ${info.sourceDate||'not stated'}`,`Count basis: ${info.basis}`,
    `Area polygon (longitude, latitude): ${JSON.stringify(round6(info.polygon))}`,`Limits: ${info.min} to ${info.max} estimates per group`,`Generated: ${info.generatedAt}`,'Sources:',...info.sources.map(s=>`  ${s.label} ${s.url}`.trimEnd()),'',
    'Each group-NN.csv holds one group. not-grouped.csv (if present) lists streets left out: unassigned, over the maximum, or with zero estimates.',
    'Importing a group file into Walk list matches whole streets by name, not the drawn area, and door counts will be voter-based, not these estimates.',''].join('\r\n')});
  return files;
}
