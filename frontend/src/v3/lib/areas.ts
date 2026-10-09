// Derivations over the columnar area payload: color bins for the map and the numbers of the area rails.
import type {AreasPayload,Group,IndexLayer,Meta} from '../types';
import {binOf,countLabel,leanBand,likelyRange,likelyShare,round,type Level} from './format';
import {registrationBin} from './opinions';

// The sentinels sit above every class index, so a palette of up to 250 colors fits (the bivariate map has nine).
// BIN_NONE is an area with nothing to color: no registered people, or no value for the chosen layer. It gets a plain pale fill.
export const BIN_NONE=250;
export const BIN_PENDING=251;
export type Layer='share'|'measured';

/** When an area gets the "Small area" note. Nothing is withheld below these counts (the server sends its own in meta.small_area). */
export const SMALL_AREA={min_n:20,min_households:10};
/** "Small area: 3 people", or "Small area: 14 people in 6 households" when the household count is also under its line; null for a larger area. */
export function smallNote(n:number|null,households:number|null,small=SMALL_AREA):string|null{
  if(n==null||n<1)return null;
  const fewPeople=n<small.min_n,fewHomes=households!=null&&households<small.min_households;
  if(!fewPeople&&!fewHomes)return null;
  return `Small area: ${countLabel(n,'person','people')}${fewHomes?` in ${countLabel(households!,'household')}`:''}`;
}

/** Per-area color bin (index = position in payload.ids; the last element is the sentinel for features without an area). */
export function binsFor(p:AreasPayload,group:IndexLayer,layer:Layer,meta:Meta):Uint8Array{
  const n=p.ids.length;const out=new Uint8Array(n+1);out[n]=BIN_PENDING;
  for(let i=0;i<n;i++){
    if(p.status[i]!=='ok'){out[i]=BIN_NONE;continue;}
    if(layer==='measured'){const m=p.groups[group]?.measured[i];out[i]=m==null?BIN_NONE:binOf(m,meta.measured_breaks);continue;}
    if(group==='lean'){
      const c=p.groups.lean,d=c.share_d[i],r=c.share_r[i],u=c.share_u[i];
      out[i]=d==null||r==null||u==null?BIN_NONE:registrationBin({d,r,u,o:Math.max(0,1-d-r-u)});continue;
    }
    const t=p.groups[group]?.top10[i];
    out[i]=t==null?BIN_NONE:binOf(t,meta.share_breaks);
  }
  return out;
}
export function pendingBins(n:number):Uint8Array{return new Uint8Array(n+1).fill(BIN_PENDING);}

/** One party code of a registration mix: its share of the people counted, 0..1 ('' = no code). The shares of a mix add up to 1. */
export interface MixShare{code:string;share:number}
const MAIN_CODES=['D','R','U'];
/** The mix of people per party code (O-10): D, R and U first and always listed, then every other code that is present, most people first.
 *  Each share is a count over the total, so the shares add up to 1. null when nobody is counted. */
export function partyShares(counts:Record<string,number>):MixShare[]|null{
  const total=Object.values(counts).reduce((a,b)=>a+b,0);
  if(!(total>0))return null;
  const rest=Object.keys(counts).filter(c=>!MAIN_CODES.includes(c)&&counts[c]>0).sort((a,b)=>counts[b]-counts[a]||(a<b?-1:1));
  return [...MAIN_CODES,...rest].map(code=>({code,share:(counts[code]??0)/total}));
}
/** The grouped mix (D, R, U and Other) that the area table carries: what the rail shows when the server has no per-code counts. */
export const groupShares=(d:number,r:number,u:number):MixShare[]=>[{code:'D',share:d},{code:'R',share:r},{code:'U',share:u},{code:'O',share:Math.max(0,1-d-r-u)}];

export interface RowStat{group:'ovr'|'cua'|'part'|'soi';mean:number;lo:number;hi:number;pct:number}
export interface AreaView{
  index:number;id:string;level:Level;label:string;
  /** No registered people: n is 0 and there are no values (status 'empty'). */
  empty:boolean;
  n:number|null;households:number|null;measured:number|null;
  share:{value:number;lo:number;hi:number}|null;
  rows:RowStat[];
  lean:{mean:number;se:number;lo:number;hi:number}|null;
  mix:MixShare[]|null;
  tractId:string|null;precinctId:string|null;blocks:number|null;
}
const POS=['ovr','cua','part','soi'] as const;

/** `counts`: the people per party code of this area (GET /api/v3/parties); without it the mix is the grouped D, R, U and Other. */
export function areaView(p:AreasPayload,i:number,group:Group,counts?:Record<string,number>):AreaView{
  const empty=p.status[i]!=='ok';
  const base:AreaView={
    index:i,id:p.ids[i],level:p.level,label:p.labels[i],empty,n:p.n[i],households:p.households[i],measured:null,
    share:null,rows:[],lean:null,mix:null,
    tractId:p.level==='block'?p.parent[i]:p.tract[i],precinctId:p.level==='block'?p.precinct[i]:null,blocks:p.blocks[i],
  };
  if(empty)return base;
  const g=group==='lean'?'ovr':group;
  base.measured=p.groups[group].measured[i];
  const t=p.groups[g].top10[i],ts=p.groups[g].top10_se[i];
  if(t!=null&&ts!=null){const [lo,hi]=likelyShare(t,ts);base.share={value:t,lo,hi};}
  base.rows=POS.map(k=>{
    const c=p.groups[k];const mean=c.mean[i]!,se=c.se[i]!;const [lo,hi]=likelyRange(mean,se);
    return {group:k,mean,lo,hi,pct:c.pct[i]!};
  });
  const l=p.groups.lean;
  if(l.mean[i]!=null){
    const mean=l.mean[i]!,se=l.se[i]!;
    base.lean={mean,se,lo:mean-1.6449*se,hi:mean+1.6449*se};
    const d=l.share_d[i]??0,r=l.share_r[i]??0,u=l.share_u[i]??0;
    base.mix=(counts&&partyShares(counts))||groupShares(d,r,u);
  }
  return base;
}

/** Weighted district means for the empty-selection rail (areas with registered people; weights = people). */
export function districtSummary(p:AreasPayload){
  const sums={ovr:0,cua:0,part:0,soi:0,lean:0,measured:0};let w=0;let people=0;
  const mix={d:0,r:0,u:0};
  for(let i=0;i<p.ids.length;i++){
    if(p.status[i]!=='ok')continue;
    const n=p.n[i]!;w+=n;
    const d=p.groups.lean.share_d[i]??0,r=p.groups.lean.share_r[i]??0,u=p.groups.lean.share_u[i]??0;
    mix.d+=n*d;mix.r+=n*r;mix.u+=n*u;
    sums.ovr+=n*p.groups.ovr.mean[i]!;sums.cua+=n*p.groups.cua.mean[i]!;sums.part+=n*p.groups.part.mean[i]!;
    sums.soi+=n*p.groups.soi.mean[i]!;sums.lean+=n*p.groups.lean.mean[i]!;sums.measured+=n*(p.groups.ovr.measured[i]??0);
    people+=n;
  }
  const f=(x:number)=>w?x/w:0;
  return {ovr:f(sums.ovr),cua:f(sums.cua),part:f(sums.part),soi:f(sums.soi),lean:f(sums.lean),measured:f(sums.measured),people,mix:groupShares(f(mix.d),f(mix.r),f(mix.u))};
}

export const fmt1=(x:number)=>String(round(x));
