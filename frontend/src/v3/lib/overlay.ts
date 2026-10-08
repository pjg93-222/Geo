// Map overlay modes (Phase 4D): one layer, a weighted blend of layers, or two layers on a 3 x 3 grid. Everything is worked out
// here from the area payload and density.json, so the map itself only needs one bin per area and a palette.
import type {AreasPayload,CandidateInfo,Density,Features,Group,Meta,OverlayLayer} from '../types';
import {BIN_NONE,BIN_PENDING,binsFor} from './areas.ts';
import {ENDASH,MIDDOT,binOf,int,round} from './format.ts';

export const SHARE_RAMP=['#E6F1EC','#C2E0DB','#8BC2BC','#54979B','#286D73'];
export const LEAN_RAMP=['#95ADCE','#E7A490','#C6C2D3','#F5F1E9','#E5DFDA'];
/** Bivariate palette, index = 3 * (first layer's third) + (second layer's third). The first layer runs teal across (--s1, a light --s3, a
 *  deepened --s4), the second runs plum down (white, a pale lavender, a mid --accent), each cell the product of the two. Both axes darken
 *  at every step, and the lowest cell is at least 3:1 in luminance from each of the other three corners (tested). */
export const BI_PALETTE=['#E6F1EC','#BFBBDA','#7B6CAC','#9FCFC8','#84A1B9','#555D92','#3F8589','#34677F','#223B64'];

export const BASE_LAYERS:OverlayLayer[]=['ovr','cua','part','soi','lean'];
export const ALL_LAYERS:OverlayLayer[]=[...BASE_LAYERS,'sup','pop_density','voter_density'];
export const LAYER_NAME:Record<OverlayLayer,string>={
  ovr:'Overall',cua:'Credit Union Alignment',part:'Participation',soi:'Sphere of Influence',lean:'Partisan lean',
  sup:'Candidate support',pop_density:'Population density',voter_density:'Voter density',
};
const DENSITY_UNIT={pop_density:'People per km²',voter_density:'Registered voters per km²'} as const;

export type Mode='single'|'blend'|'bi';
export type Blend=Partial<Record<OverlayLayer,number>>;
/** What the map shows: `layer` in single mode, the weights of `blend` (free numbers, normalised), or `pair` = [across, down]. */
export interface Overlay{mode:Mode;layer:OverlayLayer;blend:Blend;pair:[OverlayLayer,OverlayLayer]}
/** `axes` is set for the 3 x 3 legend; `ramp` then holds the nine colours in palette order. */
export interface OverlayLegend{title:string;sub:string;labels:string[];ramp:string[];axes?:[string,string]}
export interface OverlayResult{bins:Uint8Array;palette:string[];legend:OverlayLegend|null}

export const DEFAULT_OVERLAY:Overlay={mode:'single',layer:'ovr',blend:{cua:50,part:50},pair:['cua','part']};
export const isGroup=(l:OverlayLayer):l is Group=>BASE_LAYERS.includes(l);
export const isDensity=(l:OverlayLayer):l is 'pop_density'|'voter_density'=>l==='pop_density'||l==='voter_density';
const isLayer=(v:unknown):v is OverlayLayer=>ALL_LAYERS.includes(v as OverlayLayer);

// --- URL ----------------------------------------------------------------------------------------------------------------
export function readOverlay(q:URLSearchParams):Overlay{
  const pick=(k:string,d:OverlayLayer)=>{const v=q.get(k);return isLayer(v)?v:d;};
  const blend:Blend={};
  for(const part of (q.get('blend')??'').split(',')){
    const [l,w]=part.split(':');const n=Number(w);
    if(isLayer(l)&&Number.isFinite(n)&&n>0)blend[l]=n;
  }
  const mode=q.get('mode');
  return {mode:mode==='blend'||mode==='bi'?mode:'single',layer:pick('index','ovr'),
    blend:Object.keys(blend).length?blend:DEFAULT_OVERLAY.blend,pair:[pick('a','cua'),pick('b','part')]};
}
/** The query parameters of an overlay (null removes one); the default overlay writes none. */
export function overlayQuery(o:Overlay):Record<string,string|null>{
  return {index:o.layer==='ovr'?null:o.layer,mode:o.mode==='single'?null:o.mode,
    blend:o.mode==='blend'?ALL_LAYERS.filter(l=>o.blend[l]).map(l=>`${l}:${o.blend[l]}`).join(','):null,
    a:o.mode==='bi'?o.pair[0]:null,b:o.mode==='bi'?o.pair[1]:null};
}

/** Layers the screen offers: what /api/v3/features lists (the five base layers while it has not answered), and the density layers only once density.json is loaded. */
export function offeredLayers(f:Features|null,density:boolean):OverlayLayer[]{
  return (f?f.overlay.layers:BASE_LAYERS).filter(l=>!isDensity(l)||density);
}
/** The overlay with every layer that is not offered replaced by its default. */
export function usable(o:Overlay,offered:OverlayLayer[]):Overlay{
  const ok=(l:OverlayLayer)=>offered.includes(l);
  const blend=Object.fromEntries(Object.entries(o.blend).filter(([l])=>ok(l as OverlayLayer))) as Blend;
  return {mode:o.mode,layer:ok(o.layer)?o.layer:'ovr',blend:Object.keys(blend).length?blend:DEFAULT_OVERLAY.blend,
    pair:[ok(o.pair[0])?o.pair[0]:'cua',ok(o.pair[1])?o.pair[1]:'part']};
}

// --- the numbers ----------------------------------------------------------------------------------------------------------
/** Standing 0..100 among the non-null values (ties share their midrank). */
export function midrank(v:(number|null)[]):(number|null)[]{
  const idx=v.flatMap((x,i)=>x==null?[]:[i]).sort((a,b)=>v[a]!-v[b]!);
  const out:(number|null)[]=v.map(()=>null);
  for(let s=0;s<idx.length;){
    let e=s;while(e<idx.length&&v[idx[e]]===v[idx[s]])e++;
    for(let k=s;k<e;k++)out[idx[k]]=100*(s+e)/2/idx.length;
    s=e;
  }
  return out;
}
/** density.json values per area of the payload; null for an area with no registered people or an id density.json does not hold. */
function densityValues(p:AreasPayload,d:Density|null,layer:'pop_density'|'voter_density'):(number|null)[]{
  const lv=d?.levels[p.level];const at=new Map((lv?.ids??[]).map((id,i)=>[id,i]));
  return p.ids.map((id,i)=>{const k=at.get(id);return k===undefined||p.status[i]!=='ok'?null:lv![layer][k]??null;});
}
/** Standing 0..100 of each area among the areas that have a value, for any layer. */
export function layerPct(p:AreasPayload,d:Density|null,layer:OverlayLayer):(number|null)[]{
  if(isDensity(layer))return midrank(densityValues(p,d,layer));
  if(layer==='lean')return midrank(p.groups.lean.mean.map((x,i)=>p.status[i]==='ok'?x:null));
  const g=p.groups[layer];
  return p.ids.map((_,i)=>g&&p.status[i]==='ok'?g.pct[i]:null);
}
/** Weighted mean of the chosen layers' standings; null where any chosen layer has no value. */
export function blendScores(p:AreasPayload,d:Density|null,blend:Blend):(number|null)[]{
  const use=ALL_LAYERS.filter(l=>(blend[l]??0)>0);
  const total=use.reduce((s,l)=>s+blend[l]!,0);
  const pct=use.map(l=>layerPct(p,d,l));
  return p.ids.map((_,i)=>{
    let s=0;
    for(let k=0;k<use.length;k++){const x=pct[k][i];if(x==null)return null;s+=blend[use[k]]!*x;}
    return use.length?s/total:null;
  });
}
/** The value at the 20th, 40th, 60th and 80th place of the sorted values: the breaks between fifths of areas. */
export function fifths(v:(number|null)[]):number[]{
  const s=v.filter((x):x is number=>x!=null).sort((a,b)=>a-b);
  return s.length?[1,2,3,4].map(k=>s[Math.floor(k*s.length/5)]):[];
}
/** Class of a pair of standings: thirds at 100/3 and 200/3, class = 3 * first + second; null when either has no value. */
export function biClass(a:number|null,b:number|null):number|null{
  const t=(x:number)=>x<100/3?0:x<200/3?1:2;
  return a==null||b==null?null:3*t(a)+t(b);
}

function toBins(p:AreasPayload,cls:(i:number)=>number|null):Uint8Array{
  const out=new Uint8Array(p.ids.length+1);out[p.ids.length]=BIN_PENDING;
  p.ids.forEach((_,i)=>{out[i]=cls(i)??BIN_NONE;});
  return out;
}
/** A density break in at most five characters: 8.4, 196, 1.1k, 12k. */
const num=(x:number)=>x>=10000?`${round(x/1000)}k`:x>=1000?`${round(x/100)/10}k`:x>=10?int(x):String(round(x*10)/10);
const FIFTH_LABELS=['Lowest','Low','Middle','High','Highest'];

export function overlayBins(o:Overlay,p:AreasPayload,d:Density|null,meta:Meta,measured=false):OverlayResult{
  if(o.mode==='bi'){
    const [a,b]=o.pair,pa=layerPct(p,d,a),pb=layerPct(p,d,b);
    return {bins:toBins(p,i=>biClass(pa[i],pb[i])),palette:BI_PALETTE,legend:{
      title:`${LAYER_NAME[a]} and ${LAYER_NAME[b]}`,sub:'Each layer in thirds of areas, low to high',labels:['Low third','Middle third','High third'],
      ramp:BI_PALETTE,axes:[LAYER_NAME[a],LAYER_NAME[b]]}};
  }
  if(o.mode==='blend'){
    const use=ALL_LAYERS.filter(l=>(o.blend[l]??0)>0);
    const total=use.reduce((s,l)=>s+o.blend[l]!,0);
    const score=blendScores(p,d,o.blend),breaks=fifths(score);
    const parts=use.map(l=>`${LAYER_NAME[l]} ${round(100*o.blend[l]!/total)}%`).join(', ');
    return {bins:toBins(p,i=>score[i]==null?null:binOf(score[i]!,breaks)),palette:SHARE_RAMP,legend:{
      title:'Blend',sub:use.length?`${parts} ${MIDDOT} fifths of areas${use.includes('lean')?'. Partisan lean: higher is more Democratic':''}`:'Choose at least one layer',
      labels:FIFTH_LABELS,ramp:SHARE_RAMP}};
  }
  if(isDensity(o.layer)){
    const v=densityValues(p,d,o.layer),breaks=fifths(v);
    return {bins:toBins(p,i=>v[i]==null?null:binOf(v[i]!,breaks)),palette:SHARE_RAMP,legend:{
      title:LAYER_NAME[o.layer],sub:`${DENSITY_UNIT[o.layer]} ${MIDDOT} fifths of areas`,ramp:SHARE_RAMP,
      labels:breaks.length?[`Under ${num(breaks[0])}`,...breaks.slice(1).map((x,i)=>`${num(breaks[i])}${ENDASH}${num(x)}`),`${num(breaks[3])}+`]:FIFTH_LABELS}};
  }
  return {bins:binsFor(p,o.layer,measured?'measured':'share',meta),palette:o.layer==='lean'&&!measured?LEAN_RAMP:SHARE_RAMP,legend:null};
}

// --- header pieces that come from /api/v3/features ---------------------------------------------------------------------------
/** The industry menu: only when the district has two or more. */
export const industryChoices=(f:Pick<Features,'industries'>|null)=>f&&f.industries.length>1?f.industries.map(({id,label})=>({id,label})):null;
/** "Bynum (D) · General election" for a district with a candidate, null for Colorado. */
export const raceBadge=(c:CandidateInfo|null|undefined):string|null=>
  c?`${c.name.trim().split(/\s+/).pop()} (${c.party}) ${MIDDOT} ${c.race_mode==='general'?'General election':'Primary election'}`:null;

/** Relative luminance of a #RRGGBB color (0 dark .. 1 light); unknown colors read as light. */
export function luminance(hex:string|undefined):number{
  const m=hex?/^#([0-9a-f]{6})$/i.exec(hex):null;if(!m)return 1;
  const v=parseInt(m[1],16);const c=[(v>>16)&255,(v>>8)&255,v&255].map(x=>{const f=x/255;return f<=0.03928?f/12.92:Math.pow((f+0.055)/1.055,2.4);});
  return 0.2126*c[0]+0.7152*c[1]+0.0722*c[2];
}
