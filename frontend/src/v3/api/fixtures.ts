// Exact-shape fixtures for the person-level endpoints (people, person, household, street, search).
// Everything here is SYNTHETIC and deterministic: names come from embedded generic lists (never from any contacts
// layer), scores come from a seeded generator, and no value is derived from real people. The aggregate fixtures
// (meta, geometry, areas) are static files under /fixtures/v3 made by tools/make_v3_fixtures.py.
import type {
  AreaRef,AreasPayload,FieldGroup,FieldRow,Grade,HouseholdPopover,PeoplePage,PeopleQuery,PeopleRow,Profile,
  SearchResult,StreetPayload,StreetPoint,HouseholdMember
} from '../types';
import {FIELDS,GROUPS,GROUP_LABEL,OWNER_TEXT,PARTY_TEXT,type FieldDef} from './fields';
import {binOf,blockLabel,leanBand,leanBandByCode,round,areaLabel,type Level} from '../lib/format';
import {OUTLOOK_KEYS,OUTLOOK_LABELS} from '../lib/opinions';

// --- deterministic random ----------------------------------------------------------------------------------------
export function hash(...parts:(string|number)[]):number{
  let h=2166136261;const s=parts.join('|');
  for(let i=0;i<s.length;i++){h^=s.charCodeAt(i);h=Math.imul(h,16777619);}
  return h>>>0;
}
export function rng(seed:number):()=>number{
  let a=seed>>>0;
  return ()=>{a=(a+0x6D2B79F5)|0;let t=Math.imul(a^(a>>>15),1|a);t=(t+Math.imul(t^(t>>>7),61|t))^t;return ((t^(t>>>14))>>>0)/4294967296;};
}
function gauss(r:()=>number):number{
  const u=Math.max(1e-12,r());const v=r();return Math.sqrt(-2*Math.log(u))*Math.cos(2*Math.PI*v);
}
// Normal CDF (Abramowitz-Stegun), returns 0..100
function phi100(z:number):number{
  const t=1/(1+0.2316419*Math.abs(z));
  const d=0.3989423*Math.exp(-z*z/2);
  const p=d*t*(0.3193815+t*(-0.3565638+t*(1.781478+t*(-1.821256+t*1.330274))));
  return 100*(z>0?1-p:p);
}
const clamp=(x:number,lo:number,hi:number)=>Math.max(lo,Math.min(hi,x));

// --- generic name lists ------------------------------------------------------------------------------------------
const FIRST=['Alma','Ansel','Beatrix','Bram','Calla','Cyrus','Dara','Dmitri','Elin','Emeric','Fiona','Gideon','Hana','Hugo','Ilsa','Ivan','Jora','Jonas','Kira','Kofi','Lena','Linus','Mara','Mateo','Nadia','Nico','Odette','Orin','Petra','Pavel','Quinn','Rafa','Selma','Soren','Talia','Tomas','Una','Uri','Vera','Viktor','Wren','Wes','Xena','Yusuf','Zara','Zeke','Amara','Bastian','Cleo','Dario','Esme','Felix','Greta','Hector','Imani','Jules','Kenji','Lucia','Milo','Noor','Opal','Paz','Rhea','Silas','Thea','Ulises','Vida','Waldo','Yara','Zane','Adela','Boris','Cora','Dev','Elio','Freya','Gus','Hope','Idris','Juno','Kai','Lior','Maeve','Nell','Oskar','Pia','Remy','Sana','Teo','Uma','Vance','Willa','Xavi','Yael','Zora','Arlo','Bea','Cass','Dale','Etta','Finn','Gwen','Hal'];
const LAST=['Ainsley','Brandt','Calder','Dunmore','Ellison','Fairbairn','Garrow','Halvorsen','Iverson','Jessop','Kerrigan','Lindqvist','Marsh','Novak','Okafor','Pell','Quintero','Raman','Sorensen','Thorne','Ulrich','Vasquez','Whitlock','Yardley','Zielinski','Abernathy','Bellamy','Castellan','Dremel','Eastwood','Farrow','Gallo','Hartwell','Ingram','Jarrett','Kowal','Larkspur','Merrow','Nakamura','Oldham','Pruitt','Quill','Rosales','Sandoval','Trevino','Underhill','Valdez','Wexford','Yamada','Zeller','Anselm','Bishop','Corbin','Delgado','Ferreira','Galloway','Hollis','Ishikawa','Jovanovic','Keane','Lombardi','Maddox','Navarro','Okonkwo','Prescott','Rinaldi','Stroud','Tennant','Urquhart','Voss','Winslow','Yoder','Zamora','Atwood','Briggs','Cardenas','Dolan','Emerson','Fontaine','Grayson','Hatcher','Irving','Jansen','Kessler','Lyons','Mercer','Nolan','Ozawa','Pierce','Rowan','Sutter','Tran','Ueda','Vickery','Whitaker'];

// --- people engine -----------------------------------------------------------------------------------------------
export interface Core{
  id:string;hh:number;j:number;size:number;name:string;age:number;
  cua:number;part:number;soi:number;ovr:number;lean:number;undet:boolean;reg:'D'|'R'|'U'|'O';
  measured:number;lo:number;hi:number;within:number;
  grade:Record<'ovr'|'cua'|'part'|'soi'|'lean',Grade>;
}
const cores=new Map<string,Core>();
function hhSize(hh:number):number{
  const x=hash('size',hh)%100;
  return x<28?1:x<62?2:x<78?3:x<92?4:x<98?5:6;
}
export const personId=(hh:number,j:number)=>'p_'+hh.toString(36).padStart(7,'0')+String(j);
export function parseId(id:string):{hh:number;j:number}|null{
  const m=/^p_([0-9a-z]{7})(\d)$/.exec(id);if(!m)return null;
  return {hh:parseInt(m[1],36),j:Number(m[2])};
}
function gradeOf(r:()=>number,ps:number[]):Grade{
  const x=r();return x<ps[0]?'A':x<ps[0]+ps[1]?'B':x<ps[0]+ps[1]+ps[2]?'C':'D';
}
export function core(hh:number,j:number):Core{
  const key=hh+':'+j;const hit=cores.get(key);if(hit)return hit;
  const c=compute(hh,j);cores.set(key,c);return c;
}
type Affiliation='D'|'R'|'U'|'O';
/** Fixture scenario weights, not population estimates or quotas. A person's categorical
 * answers are selected once from a seeded joint scenario, never inferred from a score. */
const AFFILIATION_SCENARIOS:Record<Core['reg'],readonly number[]>={
  D:[88,1,9,2],R:[1,88,9,2],U:[14,14,70,2],O:[4,4,12,80],
};
const OUTLOOK_SCENARIOS:Record<Affiliation,readonly number[]>={
  D:[1,5,24,45,25],R:[25,45,24,5,1],U:[5,15,60,15,5],O:[10,20,40,20,10],
};
function scenarioChoice(r:()=>number,weights:readonly number[]):number{
  let choice=r()*weights.reduce((sum,w)=>sum+w,0);
  for(let i=0;i<weights.length;i++){choice-=weights[i];if(choice<0)return i;}
  return weights.length-1;
}
/** Registration, self-described affiliation and outlook remain distinct responses.
 * These authored dummy scenarios include occasional crossovers; they do not rewrite
 * real opinions, force party/outlook equivalence, or standardize any belief. */
function opinionResponses(hh:number,j:number,registration:Core['reg']):Record<string,number>{
  const r=rng(hash('raw-opinions',hh,j));
  const affiliation=(['D','R','U','O'] as const)[scenarioChoice(r,AFFILIATION_SCENARIOS[registration])];
  const outlook=scenarioChoice(r,OUTLOOK_SCENARIOS[affiliation]);
  return {
    sci_cpi_conservative_index:Number(outlook<2),sci_cpi_liberal_index:Number(outlook>2),
    sci_ct_political_partyaff_democrat:Number(affiliation==='D'),
    sci_ct_political_partyaff_gop:Number(affiliation==='R'),
    sci_ct_political_partyaff_ind:Number(affiliation==='U'),
    sci_ct_political_outlk_vconserv:Number(outlook===0),
    sci_ct_political_outlk_swconserv:Number(outlook===1),
    sci_ct_political_outlk_mid:Number(outlook===2),
    sci_ct_political_outlk_swliberal:Number(outlook===3),
    sci_ct_political_outlk_vliberal:Number(outlook===4),
  };
}

interface HouseholdFact{value:string;exposure:number}
// All members in this fixture are adults. The two count fields therefore use the
// same fixed reference distribution; children is explicitly 0 (constant ridit .5).
const HOUSEHOLDS=45000;
const sizeRidits=(()=>{
  const counts=Array<number>(7).fill(0);
  for(let hh=1;hh<=HOUSEHOLDS;hh++){const size=hhSize(hh);counts[size]+=size;}
  const total=counts.reduce((sum,n)=>sum+n,0);let before=0;
  return counts.map(n=>{const value=(before+n/2)/total;before+=n;return value;});
})();
function householdFacts(hh:number):Record<string,HouseholdFact>{
  const r=rng(hash('household-facts',hh));const x=r();
  const owner=x<.45?'H':x<.9?'R':'U';
  const tenure=owner==='H'?1:owner==='R'?0:.5;
  const dwelling=['S','H','P','G'][Math.floor(r()*4)];const size=hhSize(hh);
  return {
    sci_owner_renter:{value:OWNER_TEXT[owner],exposure:tenure},
    sii_homeowner:{value:'Code '+owner,exposure:tenure},
    sii_dwelling_type:{value:'Code '+dwelling,exposure:dwelling==='S'?1:.5},
    household_size:{value:String(size),exposure:sizeRidits[size]},
    sci_number_of_adults_in_household:{value:String(size),exposure:sizeRidits[size]},
    sci_number_of_children_in_household:{value:'0',exposure:.5},
  };
}
function feasibleScore(group:'cua'|'soi',score:number,hh:number):number{
  const facts=householdFacts(hh);let floor=0,remaining=0;
  for(const d of FIELDS.filter(f=>f.group===group)){
    if(facts[d.key])floor+=d.w*facts[d.key].exposure;else remaining+=d.w;
  }
  // Keep the illustrative target wherever possible. An impossible extreme must
  // move to the feasible boundary rather than contradict the shared household facts.
  return clamp(score,100*floor,100*(floor+remaining));
}
function compute(hh:number,j:number):Core{
  const size=hhSize(hh);
  const rh=rng(hash('hh',hh));const zhh=gauss(rh);
  const r=rng(hash('p',hh,j));
  // /1.7 keeps the top of a 45,000-household world near 98 instead of saturating at 100 (fixture look only)
  const mix=(z:number)=>(0.72*zhh+0.69*z)/1.7;
  const cua=feasibleScore('cua',phi100(mix(gauss(r))),hh);const part=phi100(mix(gauss(r)));const soi=feasibleScore('soi',phi100(mix(gauss(r))),hh);
  const ovr=0.40*cua+0.35*part+0.25*soi;
  const x=r();const reg:Core['reg']=x<0.16?'D':x<0.43?'R':x<0.97?'U':'O';
  const regC=reg==='D'?50:reg==='R'?-50:0;
  gauss(r); // Keep the independent fixture streams (age, measured %, other grades) unchanged.
  const opinions=opinionResponses(hh,j,reg);
  const lean=regC+FIELDS.filter(d=>d.group==='lean'&&d.kind!=='party').reduce((sum,d)=>sum+d.w*opinions[d.key],0);
  const measured=clamp(70+gauss(r)*13,28,98);
  const hw=3+10*(1-measured/100);
  const first=FIRST[hash('f',hh,j)%FIRST.length];const last=LAST[hash('l',hh)%LAST.length];
  const age=j===0?24+Math.floor(r()*62):j===1?clamp(24+Math.floor(r()*62),19,92):18+Math.floor(r()*14);
  const c:Core={
    id:personId(hh,j),hh,j,size,name:`${first} ${last}`,age,cua,part,soi,ovr,lean,undet:false,reg,
    measured,lo:clamp(ovr-0.9*hw,0,100),hi:clamp(ovr+1.1*hw,0,100),within:clamp(ovr+1.2+gauss(r)*1.6,0,99.9),
    grade:{
      ovr:gradeOf(r,[0.26+(measured-60)/300,0.4,0.26,0.08]),cua:gradeOf(r,[0.3,0.38,0.24,0.08]),
      part:gradeOf(r,[0.3,0.38,0.24,0.08]),soi:gradeOf(r,[0.3,0.38,0.24,0.08]),lean:gradeOf(r,[0.25,0.4,0.25,0.1]),
    },
  };
  c.grade.ovr=[c.grade.ovr,c.grade.cua,c.grade.part,c.grade.soi].sort().at(-1)!;
  c.grade.lean='A'; // These categorical test responses are all explicitly supplied by the dummy generator.
  return c;
}

/** Harness hook (fixture world only): the first person whose overall standing is in [lo, hi] in a household of `size`. */
export function pickPerson(lo:number,hi:number,size:number):string|null{
  for(let hh=1;hh<=4000;hh++){
    if(hhSize(hh)!==size)continue;
    const c=core(hh,0);
    if(c.ovr>=lo&&c.ovr<=hi&&!c.undet)return c.id;
  }
  return null;
}

// --- area lookups (the fixture world needs the static aggregate files) ------------------------------------------
const fileCache=new Map<string,Promise<unknown>>();
export function fixtureFile<T>(path:string):Promise<T>{
  let p=fileCache.get(path);
  if(!p){
    p=fetch('/fixtures/v3/'+path,{credentials:'same-origin'}).then(r=>{
      if(!r.ok)throw new Error('Fixture '+path+' is missing.');return r.json();
    });
    fileCache.set(path,p);
    p.catch(()=>fileCache.delete(path));
  }
  return p as Promise<T>;
}
interface Geo{blockIds:string[];precinctOf:Map<string,string>;tractOf:Map<string,string>;zips:string[];homes:string[];
  blockIndex:Map<string,number>;precinctIndex:Map<string,number>;blocks:AreasPayload;precincts:AreasPayload;}
let geoP:Promise<Geo>|null=null;
function geoWorld():Promise<Geo>{
  if(!geoP)geoP=Promise.all([
    fixtureFile<AreasPayload>('areas/block.json'),fixtureFile<AreasPayload>('areas/precinct.json'),fixtureFile<AreasPayload>('areas/zip.json'),
  ]).then(([b,p,z])=>{
    const precinctOf=new Map<string,string>();const tractOf=new Map<string,string>();
    b.ids.forEach((id,i)=>{precinctOf.set(id,b.precinct[i]!);tractOf.set(id,b.parent[i]!);});
    const homes=b.ids.filter((_id,i)=>b.status[i]==='ok'&&(b.n[i]??0)>=40);   // blocks big enough to place a fixture household in
    return {blockIds:b.ids,precinctOf,tractOf,zips:z.ids,homes,
      blockIndex:new Map(b.ids.map((id,i)=>[id,i])),precinctIndex:new Map(p.ids.map((id,i)=>[id,i])),blocks:b,precincts:p};
  });
  geoP.catch(()=>{geoP=null;});
  return geoP;
}
interface Placed{block:string;precinct:string;tract:string;zip:string}
function place(g:Geo,hh:number):Placed{
  const block=g.homes[hash('blk',hh)%g.homes.length];
  return {block,precinct:g.precinctOf.get(block)!,tract:g.tractOf.get(block)!,zip:g.zips[hash('zip',block)%g.zips.length]};
}

// --- pool: the fixture world's ranked list ---------------------------------------------------------------------
export function fixtureTotals(){let people=0;for(let hh=1;hh<=HOUSEHOLDS;hh++)people+=hhSize(hh);return {people,households:HOUSEHOLDS};}
interface Pool{rows:Core[];rankOf:Map<string,number>;balanced:Set<string>}
let poolP:Promise<Pool>|null=null;
function pool():Promise<Pool>{
  if(!poolP)poolP=geoWorld().then(()=>{
    const all:Core[]=[];
    for(let hh=1;hh<=HOUSEHOLDS;hh++){const s=hhSize(hh);for(let j=0;j<s;j++)all.push(compute(hh,j));}
    all.sort((a,b)=>b.ovr-a.ovr||(a.id<b.id?-1:1));
    const rows=all;
    for(const c of rows)cores.set(c.hh+':'+c.j,c);
    const rankOf=new Map(rows.map((c,i)=>[c.id,i+1]));
    // balanced by registration: K = 100 split D 16 / R 27 / U 54 / other 3, each group by rank
    const want:Record<string,number>={D:16,R:27,U:54,O:3};const got:Record<string,number>={D:0,R:0,U:0,O:0};const bal=new Set<string>();
    for(const c of rows){if(got[c.reg]<want[c.reg]){got[c.reg]++;bal.add(c.id);}}
    return {rows,rankOf,balanced:bal};
  });
  poolP.catch(()=>{poolP=null;});
  return poolP;
}
function rowOf(c:Core,rank:number):PeopleRow{
  const responses=opinionResponses(c.hh,c.j,c.reg);
  const index=OUTLOOK_KEYS.findIndex(k=>responses[k]===1);
  return {
    rank:String(rank),person_id:c.id,name:c.name,ovr_pct:c.ovr,lo:c.lo,hi:c.hi,cua_pct:c.cua,part_pct:c.part,soi_pct:c.soi,
    lean_code:index<0?'U':index-2,lean_label:index<0?'Unknown':OUTLOOK_LABELS[index],ovr_grade:c.grade.ovr,
    within_pct:c.within,within_group:c.reg==='D'?'Democrat':c.reg==='R'?'Republican':c.reg==='U'?'Unaffiliated':'Other',
  };
}

export async function fixturePeople(q:PeopleQuery):Promise<PeoplePage>{
  const [world,p]=await Promise.all([geoWorld(),pool()]);
  let list=p.rows.map((c,i)=>({c,rank:i+1}));
  if(q.balanced)list=list.filter(x=>p.balanced.has(x.c.id));
  if(q.reg!=='all')list=list.filter(x=>x.c.reg===q.reg);
  if(q.grade==='AB')list=list.filter(x=>x.c.grade.ovr==='A'||x.c.grade.ovr==='B');
  if(q.lean!=='all')list=list.filter(x=>{
    const responses=opinionResponses(x.c.hh,x.c.j,x.c.reg);const index=OUTLOOK_KEYS.findIndex(k=>responses[k]===1);
    if(q.lean==='U')return index<0;
    return index>=0&&index-2===Number(q.lean);
  });
  if(q.level&&q.area)list=list.filter(x=>{
    const pl=place(world,x.c.hh);
    return (q.level==='block'?pl.block:q.level==='precinct'?pl.precinct:q.level==='tract'?pl.tract:pl.zip)===q.area;
  });
  const total=list.length;const size=50;
  let start=0;
  if(q.after){
    const after=Number(q.after);
    if(q.dir==='next'){start=list.findIndex(x=>x.rank>after);if(start<0)start=Math.max(0,total-size);}
    else{const end=list.findIndex(x=>x.rank>=after);const e=end<0?total:end;start=Math.max(0,e-size);}
  }
  const page=list.slice(start,start+size);
  return {
    rows:page.map(x=>rowOf(x.c,x.rank)),total,first:total?start+1:0,last:start+page.length,
    page:Math.floor(start/size)+1,pages:Math.max(1,Math.ceil(total/size)),
    next:start+size<total?String(page[page.length-1].rank):null,prev:start>0?String(page[0].rank):null,
  };
}

// --- profile -----------------------------------------------------------------------------------------------------
function scaleTo(base:number[],w:number[],target:number):number[]{
  const f=(l:number)=>base.reduce((s,b,i)=>s+w[i]*Math.min(1,b*l),0);
  let lo=0,hi=1;
  const cap=w.reduce((s,x)=>s+x,0);const t=Math.min(target,cap);
  while(f(hi)<t&&hi<1e7)hi*=2;
  for(let k=0;k<60;k++){const mid=(lo+hi)/2;if(f(mid)<t)lo=mid;else hi=mid;}
  return base.map(b=>Math.min(1,b*hi));
}
const BINARY=new Set(['yn','lean-yn']);

function valueText(d:FieldDef,e:number,c:Core,r:()=>number):string{
  switch(d.kind){
    case 'yn':case 'lean-yn':return e>=0.5?'Yes':'No';
    case 'score':return String(clamp(round(e*100),0,100));
    case 'int':return d.key==='household_size'?String(c.size):String(clamp(round(1+e*2.4),1,4));
    case 'children':return 'Code '+'ABCD'[Math.min(3,Math.floor(e*4))];
    case 'owner':return OWNER_TEXT[e>=0.85?'H':e>=0.6?'O':e>=0.4?'U':e>=0.15?'P':'R'];
    case 'homeowner':return 'Code '+(e>=0.66?'H':e>=0.33?'U':'R');
    case 'dwelling':return 'Code '+'GHPS'[Math.min(3,Math.floor((1-e)*4*0.999))];
    case 'party':return PARTY_TEXT[c.reg==='O'?'O':c.reg];
    default:void r;return '';
  }
}

function buildGroup(group:'cua'|'part'|'soi'|'lean',c:Core):FieldGroup{
  const defs=FIELDS.filter(f=>f.group===group);
  const r=rng(hash('fields',c.hh,c.j,group));
  const rows:FieldRow[]=[];
  if(group!=='lean'){
    const score=group==='cua'?c.cua:group==='part'?c.part:c.soi;
    const target=score/100;const w=defs.map(d=>d.w);
    const frac=c.measured/100;
    let observed=defs.map(d=>r()<frac);
    const exposures=new Array<number>(defs.length).fill(0);
    const fixed=new Array<boolean>(defs.length).fill(false);
    const facts=householdFacts(c.hh);
    const attempt=(useFixed:boolean):boolean=>{
      let sFixed=0;
      defs.forEach((d,i)=>{
        const fact=facts[d.key];
        fixed[i]=Boolean(fact)||(useFixed&&observed[i]&&BINARY.has(d.kind));
        if(fixed[i]){exposures[i]=fact?fact.exposure:r()<target?1:0;sFixed+=w[i]*exposures[i];}
      });
      const idx=defs.map((_d,i)=>i).filter(i=>!fixed[i]);
      const cap=idx.reduce((s,i)=>s+w[i],0);const need=target-sFixed;
      if(need<-1e-9||need>cap+1e-9)return false;
      const base=idx.map(()=>0.2+0.8*r());
      const scaled=scaleTo(base,idx.map(i=>w[i]),need);
      idx.forEach((i,k)=>{exposures[i]=scaled[k];});
      return true;
    };
    if(!attempt(true)){observed=observed.map(()=>false);attempt(false);}
    defs.forEach((d,i)=>{
      const e=exposures[i];
      const isObs=fixed[i]||(!BINARY.has(d.kind)&&observed[i]);
      const status=isObs?'observed' as const:'estimated' as const;
      const yes=e>=0.5;
      rows.push({
        field:d.key,label:d.label,value:facts[d.key]?.value??valueText(d,e,c,r),status,
        p:isObs?null:Math.round((BINARY.has(d.kind)?(yes?e:1-e):0.55+0.4*r())*100)/100,
        exposure:e,contribution:100*d.w*e,
      });
    });
  }else{
    const regC=c.reg==='D'?50:c.reg==='R'?-50:0;
    const opinions=opinionResponses(c.hh,c.j,c.reg);
    for(const d of defs){
      if(d.key==='svi_party_registration'){
        rows.push({field:d.key,label:d.label,value:valueText(d,1,c,r),status:'observed',p:null,exposure:1,contribution:regC});
        continue;
      }
      const e=opinions[d.key];
      rows.push({field:d.key,label:d.label,value:e===1?'Yes':'No',status:'observed',
        p:null,exposure:e,contribution:d.w*e});
    }
  }
  return {id:group,label:GROUP_LABEL[group],n_fields:rows.length,observed:rows.filter(x=>x.status==='observed').length,fields:rows};
}

export async function fixtureProfile(id:string):Promise<Profile>{
  const parsed=parseId(id);if(!parsed||parsed.hh<1||parsed.hh>HOUSEHOLDS||parsed.j>=hhSize(parsed.hh))throw new Error('Unknown person. Return to People to choose a test profile.');
  const world=await geoWorld();const c=core(parsed.hh,parsed.j);
  const pl=place(world,c.hh);
  const members:HouseholdMember[]=[];
  for(let j=0;j<c.size;j++){const m=core(c.hh,j);members.push({person_id:m.id,name:m.name,age:m.age,ovr_pct:m.ovr,grade:m.grade.ovr,self:j===c.j});}
  const mean=members.reduce((s,m)=>s+m.ovr_pct,0)/members.length;
  members.sort((a,b)=>Number(b.self)-Number(a.self)||b.ovr_pct-a.ovr_pct);
  const ctx=(level:Level,areaId:string,payload:AreasPayload,idx:number|undefined):AreaRef=>{
    const i=idx??-1;const none=i<0||payload.status[i]!=='ok';
    return {level,id:areaId,label:areaLabel(level,areaId),pct:none?null:payload.groups.ovr.top10_pct[i],
      top10:none?null:payload.groups.ovr.top10[i],n:i<0?null:payload.n[i],households:i<0?null:payload.households[i]};
  };
  const card=(score:number,grade:Grade,single=false)=>{
    const hw=2.5+8*(1-c.measured/100);
    return {score,pct:score,lo:single?score:clamp(score-hw*0.9,0,100),hi:single?score:clamp(score+hw*1.1,0,100),grade};
  };
  return {
    person_id:c.id,name:c.name,age:c.age,household_size:c.size,registration:c.reg,block:pl.block,precinct:pl.precinct,
    header:{pct:c.ovr,lo:c.lo,hi:c.hi,grade:c.grade.ovr,measured_pct:c.measured,within_pct:c.within,
      within_group:c.reg==='D'?'Democrat':c.reg==='R'?'Republican':c.reg==='U'?'Unaffiliated':'Other'},
    cards:{cua:card(c.cua,c.grade.cua),part:card(c.part,c.grade.part,true),soi:card(c.soi,c.grade.soi),
      lean:{lean:c.lean,grade:c.grade.lean,undetermined:c.undet}},
    groups:GROUPS.map(g=>buildGroup(g,c)),
    household:{code:c.hh,mean_pct:mean,members},
    area_context:{
      block:ctx('block',pl.block,world.blocks,world.blockIndex.get(pl.block)),
      precinct:ctx('precinct',pl.precinct,world.precincts,world.precinctIndex.get(pl.precinct)),
    },
  };
}

// --- household / street ------------------------------------------------------------------------------------------
export const householdId=(hh:number)=>'h_'+hh.toString(36).padStart(7,'0');
export const parseHousehold=(id:string):number|null=>{const m=/^h_([0-9a-z]{7})$/.exec(id);return m?parseInt(m[1],36):null;};
const HBREAKS=[20,40,60,80];

export async function fixtureHousehold(id:string):Promise<HouseholdPopover>{
  const hh=parseHousehold(id);if(hh===null||hh<1||hh>HOUSEHOLDS)throw new Error('Unknown household.');
  const world=await geoWorld();const first=core(hh,0);
  const members=[];
  for(let j=0;j<first.size;j++){const m=core(hh,j);members.push({person_id:m.id,name:m.name,age:m.age,ovr_pct:m.ovr,grade:m.grade.ovr});}
  return {id,code:hh,n_people:members.length,mean_pct:members.reduce((s,m)=>s+m.ovr_pct,0)/members.length,
    block_label:blockLabel(place(world,hh).block),members};
}

export function fixtureStreet(bbox:[number,number,number,number],_zoom:number):StreetPayload{
  const [w,s,e,n]=bbox;const cx=0.00052,cy=0.00041;
  const pts:StreetPoint[]=[];const hist:[number,number,number,number,number]=[0,0,0,0,0];
  let people=0,measured=0;let truncated=false;
  outer:for(let ix=Math.floor(w/cx);ix<=Math.floor(e/cx);ix++){
    for(let iy=Math.floor(s/cy);iy<=Math.floor(n/cy);iy++){
      const hcode=hash('cell',ix,iy);
      if(hcode%100<44)continue;
      const r=rng(hcode);const lon=(ix+0.15+0.7*r())*cx;const lat=(iy+0.15+0.7*r())*cy;
      if(lon<w||lon>e||lat<s||lat>n)continue;
      const hh=(hcode%HOUSEHOLDS)+1;const size=hhSize(hh);
      let sum=0,ms=0;for(let j=0;j<size;j++){const m=core(hh,j);sum+=m.ovr;ms+=m.measured;}
      const bin=binOf(sum/size,HBREAKS);
      pts.push([householdId(hh),Math.round(lon*1e6)/1e6,Math.round(lat*1e6)/1e6,size,bin]);
      hist[bin]++;people+=size;measured+=ms;
      if(pts.length>=1500){truncated=true;break outer;}
    }
  }
  return {points:pts,truncated,stats:{households:pts.length,people,measured_pct:people?measured/people:0,hist}};
}

export async function fixtureSearch(q:string):Promise<SearchResult>{
  const [world,p]=await Promise.all([geoWorld(),pool()]);
  const needle=q.trim().toLowerCase();const out:SearchResult['people']=[];
  if(needle.length<2)return {people:out};
  for(const c of p.rows){
    if(c.name.toLowerCase().includes(needle)){
      out.push({person_id:c.id,name:c.name,block_label:blockLabel(place(world,c.hh).block)});
      if(out.length>=8)break;
    }
  }
  return {people:out};
}

export {leanBandByCode};
