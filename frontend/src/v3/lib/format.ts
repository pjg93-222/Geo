// Display formatting rules (FIDELITY_SPEC section 4). Every number a screen shows goes through this file.

/** Display rounding: sign(x) * floor(|x| + 0.5), so half goes away from zero (framework_config display.rounding_rule). */
export function round(x:number):number{
  if(!Number.isFinite(x))return NaN;
  const r=Math.sign(x)*Math.floor(Math.abs(x)+0.5);
  return r===0?0:r;
}

const COUNT=new Intl.NumberFormat('en-US');
export const int=(n:number):string=>COUNT.format(round(n));

export const countLabel=(n:number,singular:string,plural=singular+'s'):string=>`${int(n)} ${n===1?singular:plural}`;

export function ordinal(n:number):string{
  const v=round(n);const a=Math.abs(v);const t=a%100;
  if(t>=11&&t<=13)return `${v}th`;
  switch(a%10){case 1:return `${v}st`;case 2:return `${v}nd`;case 3:return `${v}rd`;default:return `${v}th`;}
}

/** A share in 0..1 shown as "16.4" (one decimal; the caller adds the "%" once). */
export const shareText=(s:number):string=>(round(s*1000)/10).toFixed(1);
/** District rate: one decimal with ".0" dropped. */
export const rateText=(s:number):string=>{const t=shareText(s);return t.endsWith('.0')?t.slice(0,-2):t;};

export const MINUS='−';
export const ENDASH='–';
export const MIDDOT='·';
export const RANGE=(lo:number,hi:number):string=>`${round(lo)}${ENDASH}${round(hi)}`;

/** Signed lean: "+18", "−31", "0". */
export function signed(n:number):string{
  const v=round(n);
  if(v>0)return `+${v}`;
  if(v<0)return `${MINUS}${Math.abs(v)}`;
  return '0';
}

export type LeanKey='strong-r'|'lean-r'|'centre'|'lean-d'|'strong-d';
export interface LeanBand{key:LeanKey;label:string;code:-2|-1|0|1|2}
/** Band of an unrounded lean: |lean| < 25 Center, 25..75 Lean, >= 75 Strong; D for > 0, R for < 0. */
export function leanBand(v:number,cuts:readonly [number,number]=[25,75]):LeanBand{
  const a=Math.abs(v);
  if(a<cuts[0])return {key:'centre',label:'Center',code:0};
  const strong=a>=cuts[1];
  if(v>0)return strong?{key:'strong-d',label:'Strong D',code:2}:{key:'lean-d',label:'Lean D',code:1};
  return strong?{key:'strong-r',label:'Strong R',code:-2}:{key:'lean-r',label:'Lean R',code:-1};
}
export const LEAN_BANDS:LeanBand[]=[
  {key:'strong-r',label:'Strong R',code:-2},{key:'lean-r',label:'Lean R',code:-1},{key:'centre',label:'Center',code:0},
  {key:'lean-d',label:'Lean D',code:1},{key:'strong-d',label:'Strong D',code:2},
];
export const leanBandByCode=(c:number):LeanBand=>LEAN_BANDS[Math.max(-2,Math.min(2,c))+2];
export const leanColor=(k:LeanKey):string=>`var(--${k})`;

/** Position (0..1) of a lean value on the 1:2:2:2:1 linear bar. */
export function leanLinearPos(v:number):number{
  const c=Math.max(-100,Math.min(100,v));
  return (c+100)/200;
}
/** Position (0..1) inside five equal cells: piecewise-linear within the band's lean interval (FIDELITY_SPEC Q4). */
export function leanCellPos(v:number):number{
  const c=Math.max(-100,Math.min(100,v));
  const edges=[-100,-75,-25,25,75,100];
  for(let i=0;i<5;i++){
    if(c<=edges[i+1]||i===4){
      const f=(c-edges[i])/(edges[i+1]-edges[i]);
      return (i+Math.max(0,Math.min(1,f)))/5;
    }
  }
  return 0.5;
}

export function clamp(x:number,lo:number,hi:number):number{return Math.max(lo,Math.min(hi,x));}

const Z90=1.6449;
/** Normal 90% interval of an area mean: the area table stores the posterior SD, not bounds (FIDELITY_SPEC section 5.3). */
export function likelyRange(mean:number,se:number):[number,number]{
  return [clamp(mean-Z90*se,0,100),clamp(mean+Z90*se,0,100)];
}
export function likelyShare(share:number,se:number):[number,number]{
  return [clamp(share-Z90*se,0,1),clamp(share+Z90*se,0,1)];
}

/** Bin = number of breaks that are <= value (a value equal to a break goes up). */
export function binOf(v:number,breaks:readonly number[]):number{
  let b=0;for(const k of breaks)if(k<=v)b++;return b;
}

// --- ids -----------------------------------------------------------------------------------------------------------
export const blockLabel=(geoid:string):string=>`Block ${geoid.slice(-4)}`;
export function tractLabel(geoid:string):string{
  const code=geoid.length>=11?geoid.slice(5,11):geoid.padStart(6,'0');
  const head=String(parseInt(code.slice(0,4),10));const tail=code.slice(4);
  return `Tract ${head}${tail==='00'?'':'.'+tail}`;
}
export const tractShort=(geoid:string):string=>tractLabel(geoid).replace(/^Tract /,'');
export const precinctLabel=(code:string):string=>`Precinct ${code.slice(-3)}`;
export const precinctShort=(code:string):string=>code.slice(-3);
export const zipLabel=(z:string):string=>`ZIP ${z}`;

export type Level='block'|'precinct'|'tract'|'zip';
export const LEVELS:Level[]=['block','precinct','tract','zip'];
export const LEVEL_LABEL:Record<Level,string>={block:'Block',precinct:'Precinct',tract:'Tract',zip:'ZIP'};
export const LEVEL_PLURAL:Record<Level,string>={block:'blocks',precinct:'precincts',tract:'tracts',zip:'ZIPs'};
export function areaLabel(level:Level,id:string):string{
  return level==='block'?blockLabel(id):level==='precinct'?precinctLabel(id):level==='tract'?tractLabel(id):zipLabel(id);
}
export const householdLabel=(code:number|string):string=>`Household H-${code}`;

export const REG_LABEL:Record<string,string>={D:'Democrat',R:'Republican',U:'Unaffiliated',O:'Other'};

/** The contacts layer stores names in upper case; display in title case (FIDELITY_SPEC section 4). */
export function titleName(raw:string):string{
  const keep=new Set(['JR','SR','II','III','IV']);
  return raw.toLowerCase().split(' ').map(word=>{
    const up=word.toUpperCase();
    if(keep.has(up))return up;
    let out='';let cap=true;
    for(const ch of word){
      out+=cap?ch.toUpperCase():ch;
      cap=ch==='-'||ch==="'"||ch==='’';
    }
    return out.replace(/^Mc([a-z])/,(_m,c:string)=>'Mc'+c.toUpperCase());
  }).join(' ');
}

export function seconds(ms:number):string{return (Math.max(100,Math.round(ms/100)*100)/1000).toFixed(1);}

/** One row of GET /api/v3/districts (schema v3-districts-1). Each district runs as its own server instance at `port`. */
export interface DistrictEntry{id:string;label:string;short_label:string;state:string;port:number;ready:boolean;center:[number,number];zoom:number}
export interface DistrictList{schema:string;current:string;districts:DistrictEntry[]}
/** The page of another district: same host, that district's port. */
export const districtHref=(d:Pick<DistrictEntry,'port'>,loc:Pick<Location,'protocol'|'hostname'>):string=>`${loc.protocol}//${loc.hostname}:${d.port}/`;
/** Menu rows of the header selector. A district that is not built yet is disabled unless it is the one being served. */
export const districtOptions=(list:Pick<DistrictEntry,'id'|'label'|'ready'>[],current:string)=>list.map(d=>{
  const disabled=!d.ready&&d.id!==current;
  return {id:d.id,label:disabled?`${d.label} (not built yet)`:d.label,disabled};
});
