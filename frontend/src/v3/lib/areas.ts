// Derivations over the columnar area payload: color bins for the map and the numbers of the area rails.
import type {AreasPayload,Group,IndexLayer,Meta} from '../types';
import {binOf,leanBand,likelyRange,likelyShare,round,type Level} from './format';
import {registrationBin} from './opinions';

// The sentinels sit above every class index, so a palette of up to 250 colors fits (the bivariate map has nine).
export const BIN_WITHHELD=250;
export const BIN_PENDING=251;
export type Layer='share'|'measured';

/** Per-area color bin (index = position in payload.ids; the last element is the sentinel for features without an area). */
export function binsFor(p:AreasPayload,group:IndexLayer,layer:Layer,meta:Meta):Uint8Array{
  const n=p.ids.length;const out=new Uint8Array(n+1);out[n]=BIN_PENDING;
  for(let i=0;i<n;i++){
    if(p.status[i]!=='ok'){out[i]=BIN_WITHHELD;continue;}
    if(layer==='measured'){const m=p.groups[group]?.measured[i];out[i]=m==null?BIN_WITHHELD:binOf(m,meta.measured_breaks);continue;}
    if(group==='lean'){
      const c=p.groups.lean,d=c.share_d[i],r=c.share_r[i],u=c.share_u[i];
      out[i]=d==null||r==null||u==null?BIN_WITHHELD:registrationBin({d,r,u,o:Math.max(0,1-d-r-u)});continue;
    }
    const t=p.groups[group]?.top10[i];
    out[i]=t==null?BIN_WITHHELD:binOf(t,meta.share_breaks);
  }
  return out;
}
export function pendingBins(n:number):Uint8Array{return new Uint8Array(n+1).fill(BIN_PENDING);}

export interface RowStat{group:'ovr'|'cua'|'part'|'soi';mean:number;lo:number;hi:number;pct:number}
export interface AreaView{
  index:number;id:string;level:Level;label:string;withheld:boolean;
  n:number|null;households:number|null;measured:number|null;
  share:{value:number;lo:number;hi:number}|null;
  rows:RowStat[];
  lean:{mean:number;se:number;lo:number;hi:number}|null;
  mix:{d:number;r:number;u:number;o:number}|null;
  tractId:string|null;precinctId:string|null;blocks:number|null;
}
const POS=['ovr','cua','part','soi'] as const;

export function areaView(p:AreasPayload,i:number,group:Group):AreaView{
  const withheld=p.status[i]!=='ok';
  const base:AreaView={
    index:i,id:p.ids[i],level:p.level,label:p.labels[i],withheld,n:p.n[i],households:p.households[i],measured:null,
    share:null,rows:[],lean:null,mix:null,
    tractId:p.level==='block'?p.parent[i]:p.tract[i],precinctId:p.level==='block'?p.precinct[i]:null,blocks:p.blocks[i],
  };
  if(withheld)return base;
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
    base.mix={d,r,u,o:Math.max(0,1-d-r-u)};
  }
  return base;
}

/** Weighted district means for the empty-selection rail (published areas only; weights = people). */
export function districtSummary(p:AreasPayload){
  const sums={ovr:0,cua:0,part:0,soi:0,lean:0,measured:0};let w=0;let people=0;
  const mix={d:0,r:0,u:0,o:0};
  for(let i=0;i<p.ids.length;i++){
    if(p.status[i]!=='ok')continue;
    const n=p.n[i]!;w+=n;
    const d=p.groups.lean.share_d[i]??0,r=p.groups.lean.share_r[i]??0,u=p.groups.lean.share_u[i]??0;
    mix.d+=n*d;mix.r+=n*r;mix.u+=n*u;mix.o+=n*Math.max(0,1-d-r-u);
    sums.ovr+=n*p.groups.ovr.mean[i]!;sums.cua+=n*p.groups.cua.mean[i]!;sums.part+=n*p.groups.part.mean[i]!;
    sums.soi+=n*p.groups.soi.mean[i]!;sums.lean+=n*p.groups.lean.mean[i]!;sums.measured+=n*(p.groups.ovr.measured[i]??0);
    people+=n;
  }
  const f=(x:number)=>w?x/w:0;
  return {ovr:f(sums.ovr),cua:f(sums.cua),part:f(sums.part),soi:f(sums.soi),lean:f(sums.lean),measured:f(sums.measured),people,mix:{d:f(mix.d),r:f(mix.r),u:f(mix.u),o:f(mix.o)}};
}

export const fmt1=(x:number)=>String(round(x));
