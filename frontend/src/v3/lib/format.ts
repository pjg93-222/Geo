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

// ---- the overall's terms and candidate support (Phase 4 Lane SD) ----------------------------------------------------------------------
export type OverallWeights=Partial<Record<'cua'|'sup'|'part'|'soi',number>>;
const TERM_ORDER=['cua','sup','part','soi'] as const;
const TERM_SHORT={cua:'Alignment',sup:'Candidate support',part:'Participation',soi:'Sphere of Influence'};
const TERM_LONG={...TERM_SHORT,cua:'Credit Union Alignment'};
/** "40% Alignment", "25% Candidate support", ... in the overall's order. Colorado's 40/35/25 stand in until the meta has loaded. */
export const weightTerms=(w:OverallWeights|undefined,long=false):string[]=>{
  const v:OverallWeights={cua:40,part:35,soi:25,...w};
  return TERM_ORDER.filter(k=>v[k]!==undefined).map(k=>`${v[k]}% ${(long?TERM_LONG:TERM_SHORT)[k]}`);
};
/** "40% Alignment + 35% Participation + 25% Sphere of Influence", with "25% Candidate support" after Alignment once the overall counts it. */
export const weightsText=(w:OverallWeights|undefined):string=>weightTerms(w).join(' + ');
/** "three indices: 40% Credit Union Alignment, 35% Participation and 25% Sphere of Influence" (four with Candidate support). */
export function indicesText(w:OverallWeights|undefined):string{
  const t=weightTerms(w,true);
  return `${['three','four'][t.length-3]} indices: ${t.slice(0,-1).join(', ')} and ${t[t.length-1]}`;
}
export const raceModeText=(mode:'general'|'primary'):string=>mode==='general'?'General-election mode':'Primary mode';

const SIGNAL_TEXT={partisan:'the modeled partisan score',presidential:'the modeled support for the two major candidates in the last presidential race',
  registration:'party registration (the candidate’s party counts as full support, the other major party as none and every other registration as halfway)',
  primary_ballot:'the party of the most recent primary ballot, where one is recorded'};
/** The Method page paragraph on candidate support: its inputs, how they are combined and what is left out. Only signals with a weight are named. */
export function supportMethodText(name:string,partyLabel:string,mode:'general'|'primary',signals:Record<keyof typeof SIGNAL_TEXT,number>):string{
  const used=(Object.keys(SIGNAL_TEXT) as (keyof typeof SIGNAL_TEXT)[]).filter(k=>signals[k]>0);
  const equal=used.every(k=>signals[k]===signals[used[0]]);
  const list=used.map(k=>SIGNAL_TEXT[k]+(equal?'':` (weight ${signals[k]})`));
  return `${raceModeText(mode)}. Candidate support runs from 0 to 100 and shows how far a person leans toward ${name}’s party (${partyLabel}). It averages `
    +`${list.length>1?list.slice(0,-1).join(', ')+' and '+list[list.length-1]:list[0]}, ${equal?'counted equally':'weighted as shown'}; a person without one of them is scored on the rest. `
    +`Every input is turned toward the candidate’s party, and a build check stops if a score points the wrong way. No turnout count is an input; turnout sits in Participation. `
    +`The overall uses each person’s standing on this score. An area value is the plain mean of its people’s scores, without shrinkage.`;
}
