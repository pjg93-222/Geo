import type {WalkListPayload} from '../api/walkTypes';

// Category labels, never positions on a D/R continuum. Source labels take precedence.
export const AFFILIATION_LABELS:Record<string,string>={D:'Democrat',R:'Republican',U:'Unaffiliated',I:'Independent Party',L:'Libertarian',G:'Green',W:'Working Families',O:'Other',none:'Unknown / no code'};
export function affiliationCodes(value:string):string[]|null{
  if(!value)return null;
  if(value==='empty')return [];
  const codes=value.split(',');
  if(value.length>512||codes.length>32||new Set(codes).size!==codes.length||codes.some(c=>c!=='none'&&!/^[A-Z0-9][A-Z0-9_-]{0,15}$/.test(c)))throw new Error('Invalid affiliation selection. Reset the filter to continue.');
  return codes.sort();
}
export const affiliationWire=(codes:string[]|null):string=>codes===null?'':codes.length?[...new Set(codes)].sort().join(','):'empty';
/** Canonicalize valid links; preserve invalid input so the server rejects it rather than broadening it. */
export function normalizeAffiliation(value:string):string{try{return affiliationWire(affiliationCodes(value));}catch{return value;}}
export const affiliationLabel=(code:string,names:Record<string,string>={}):string=>Object.hasOwn(names,code)?names[code]:Object.hasOwn(AFFILIATION_LABELS,code)?AFFILIATION_LABELS[code]:`Code ${code}`;
export function affiliationChoices(counts:Record<string,number>={},names:Record<string,string>={},selected:string[]=[]){
  return [...new Set([...Object.keys(AFFILIATION_LABELS),...Object.keys(names),...Object.keys(counts).map(c=>c||'none'),...selected])].map(code=>({code,label:affiliationLabel(code,names),count:counts[code==='none'?'':code]??0}));
}
export function affiliationText(value:string,names:Record<string,string>={}):string{
  try{const codes=affiliationCodes(value);return codes===null?'All affiliations':codes.length?codes.map(c=>affiliationLabel(c,names)).join(', '):'No affiliations selected';}catch{return 'Invalid affiliation selection';}
}
/** Aggregate only the displayed sheet, so its totals always describe the visible map. */
export function routeAffiliationCounts(payload:WalkListPayload|null):Record<string,number>{
  const counts:Record<string,number>=Object.create(null);
  for(const run of payload?.runs??[])for(const door of run.doors)for(const person of door.voters){const code=person.party||'';counts[code]=(counts[code]??0)+1;}
  return counts;
}
