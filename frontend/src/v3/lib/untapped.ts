// Untapped potential on the client: the one place the /api/v3/untapped sums become numbers. The server sends four sums per area once
// (undecided, supporter and each times sphere of influence), so the map and the ranked list follow the sliders without a request.
// No React in this file: node --test loads it as it is. personPotential mirrors tools/field_labels.potential (one formula everywhere).
export type UntappedLevel='block'|'precinct'|'tract'|'zip'|'state_house'|'state_senate';
/** wp: share given to undecided voters (supporters get 1 - wp). wi: how much sphere of influence adds (top influence counts 1 + wi times). */
export interface UntappedWeights{wp:number;wi:number}
/** GET /api/v3/untapped?level=: columnar, index k = area k. Sums run over people not at a door with a recorded result. */
export interface UntappedAreas{
  schema:'v3-untapped-1';level:UntappedLevel;ids:string[];labels:string[];people:number[];knocked:number[];
  undecided:number[];supporter:number[];undecided_i:number[];supporter_i:number[];defaults:UntappedWeights;
  t_source:{sai:number;record:number;even:number};results:{version:string;people_knocked:number}
}
/** One area at given weights; `undecided` and `supporter` are the two weighted parts and add up to `value`. */
export interface UntappedArea{id:string;label:string;value:number;undecided:number;supporter:number;people:number;knocked:number}

/** The client formula of the endpoint contract. */
export function areaAt(u:UntappedAreas,k:number,w:UntappedWeights):UntappedArea{
  const undecided=w.wp*(u.undecided[k]+w.wi*u.undecided_i[k]);
  const supporter=(1-w.wp)*(u.supporter[k]+w.wi*u.supporter_i[k]);
  return {id:u.ids[k],label:u.labels[k],value:undecided+supporter,undecided,supporter,people:u.people[k],knocked:u.knocked[k]};
}

/** The areas with the most potential first (ties by id), at most `limit`. */
export function ranked(u:UntappedAreas,w:UntappedWeights,limit=25):UntappedArea[]{
  const all=u.ids.map((_,k)=>areaAt(u,k,w));
  all.sort((a,b)=>b.value-a.value||(a.id<b.id?-1:a.id>b.id?1:0));
  return all.slice(0,limit);
}

/** One person: s support 0..1, t chance of voting 0..1, i sphere-of-influence standing 0..1. */
export function personPotential(s:number,t:number,i:number,w:UntappedWeights):number{
  return (w.wp*(1-Math.abs(2*s-1))*t+(1-w.wp)*s*(1-t))*(1+w.wi*i);
}

const STEP=20; // slider steps of 0.05
/** Each weight limited to 0..1 and snapped to steps of 0.05; a missing or non-numeric one takes the default. */
export function clampWeights(w:Partial<UntappedWeights>,d:UntappedWeights):UntappedWeights{
  const one=(x:unknown,dflt:number)=>typeof x==='number'&&Number.isFinite(x)?Math.round(Math.min(1,Math.max(0,x))*STEP)/STEP:dflt;
  return {wp:one(w.wp,d.wp),wi:one(w.wi,d.wi)};
}

const KEY='terrier.untapped.weights.';
/** Storage the weights live in; the browser's by default. */
export interface WeightStorage{getItem(key:string):string|null;setItem(key:string,value:string):void}
/** This viewer's saved weights for a district, or the district defaults when none are saved or storage is unavailable. */
export function readWeights(district:string,d:UntappedWeights,storage?:WeightStorage):UntappedWeights{
  try{
    const raw=(storage??localStorage).getItem(KEY+district);
    const saved:unknown=raw?JSON.parse(raw):null;
    return saved&&typeof saved==='object'?clampWeights(saved as Partial<UntappedWeights>,d):d;
  }catch{return d;}
}
export function writeWeights(district:string,w:UntappedWeights,storage?:WeightStorage):void{
  try{(storage??localStorage).setItem(KEY+district,JSON.stringify({wp:w.wp,wi:w.wi}));}catch{/* The sliders still work when storage is unavailable. */}
}
/** Query parameters of the walk endpoints. */
export const weightsQuery=(w:UntappedWeights)=>({wp:w.wp.toFixed(2),wi:w.wi.toFixed(2)});

/** The district's default weights from /api/v3/features; a server that does not send them gets 0.5 and 0.5. */
export const defaultWeights=(roi:{potential?:UntappedWeights}|null|undefined):UntappedWeights=>
  clampWeights(roi?.potential??{},{wp:0.5,wi:0.5});
/** Where "Plan walk list" goes: the Walk tab with this precinct, which builds the list at once. */
export const planWalkPath=(precinct:string):string=>'/walk?'+new URLSearchParams({precinct,plan:'1'});

export const UNDECIDED_WORDS='Undecided, usually votes';
export const SUPPORTER_WORDS='Supporter, may not vote';
export const DEFAULTS_NOTE='Defaults: balance 50/50, influence 0.5 (top influence counts 1.5 times).';
