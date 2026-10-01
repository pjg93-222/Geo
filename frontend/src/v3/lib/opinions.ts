import type {FieldRow} from '../types';

export const OUTLOOK_KEYS=['sci_ct_political_outlk_vconserv','sci_ct_political_outlk_swconserv','sci_ct_political_outlk_mid','sci_ct_political_outlk_swliberal','sci_ct_political_outlk_vliberal'] as const;
export const OUTLOOK_LABELS=['Very conservative','Somewhat conservative','Middle outlook','Somewhat liberal','Very liberal'] as const;
export function describeOpinions(fields:Pick<FieldRow,'field'|'value'>[]){
  const values=new Map(fields.map(f=>[f.field,f.value]));
  const outlook=OUTLOOK_KEYS.filter(k=>values.get(k)==='Yes');
  const parties=[['sci_ct_political_partyaff_democrat','Democrat'],['sci_ct_political_partyaff_gop','Republican'],['sci_ct_political_partyaff_ind','Independent']] as const;
  const chosen=parties.filter(([k])=>values.get(k)==='Yes');
  return {
    outlook:outlook.length===1?OUTLOOK_LABELS[OUTLOOK_KEYS.indexOf(outlook[0])]:'Unknown',
    affiliation:chosen.length===1?chosen[0][1]:chosen.length>1?'Multiple responses':parties.every(([k])=>values.get(k)==='No')?'Other':'Unknown',
  };
}
export type RegistrationMix={d:number;r:number;u:number;o:number};
export const REGISTRATION_LABELS=['Democrat','Republican','Unaffiliated','Other','Mixed'] as const;
export function registrationBin(mix:RegistrationMix):number{
  const values=[mix.d,mix.r,mix.u,mix.o];const largest=Math.max(...values);
  return values.filter(v=>Math.abs(v-largest)<1e-9).length>1?4:values.indexOf(largest);
}
