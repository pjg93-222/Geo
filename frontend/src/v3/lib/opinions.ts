import type {FieldRow} from '../types';

export const OUTLOOK_KEYS=['sci_ct_political_outlk_vconserv','sci_ct_political_outlk_swconserv','sci_ct_political_outlk_mid','sci_ct_political_outlk_swliberal','sci_ct_political_outlk_vliberal'] as const;
export const OUTLOOK_LABELS=['Very conservative','Somewhat conservative','Middle outlook','Somewhat liberal','Very liberal'] as const;
/** Outlook label for the positions (0 very conservative .. 4 very liberal) of the flags set. One flag is itself. An unbroken run that does not
 *  reach both sides of middle (2) collapses to its most extreme flag (very + somewhat conservative is very conservative; somewhat conservative +
 *  middle is somewhat conservative; middle + somewhat + very liberal is very liberal). A run across middle or flags with a gap are Multiple
 *  responses; no flags is Unknown. Mirrors collapse_outlook in server/research/scoresdb.py. */
export function outlookLabel(selected:number[]):string{
  if(!selected.length)return 'Unknown';
  const low=Math.min(...selected),high=Math.max(...selected);
  if(high-low+1!==new Set(selected).size||(low<2&&high>2))return 'Multiple responses';
  return OUTLOOK_LABELS[high<=2?low:high];
}
export function describeOpinions(fields:Pick<FieldRow,'field'|'value'>[]){
  const values=new Map(fields.map(f=>[f.field,f.value]));
  const outlook=OUTLOOK_KEYS.filter(k=>values.get(k)==='Yes');
  const parties=[['sci_ct_political_partyaff_democrat','Democrat'],['sci_ct_political_partyaff_gop','Republican'],['sci_ct_political_partyaff_ind','Independent']] as const;
  const chosen=parties.filter(([k])=>values.get(k)==='Yes');
  return {
    outlook:outlookLabel(outlook.map(k=>OUTLOOK_KEYS.indexOf(k))),
    affiliation:chosen.length===1?chosen[0][1]:chosen.length>1?'Multiple responses':parties.every(([k])=>values.get(k)==='No')?'Other':'Unknown',
  };
}
/** The five steps of the registration map, R side first: the D share minus the R share, both over everyone registered (U and others too). */
export const MARGIN_LABELS=['R +15 or more','R +5 to 15','Within 5','D +5 to 15','D +15 or more'] as const;
/** The same five steps, short enough to sit under a legend swatch without running into the next label. */
export const MARGIN_SHORT=['R 15+','R 5–15','Within 5','D 5–15','D 15+'] as const;
/** The MARGIN_LABELS step (0..4) of an area or block with D share `d` and R share `r` (0..1): 15 points or more either way is the strong
 *  step, 5 to 15 the lean step, and under 5 either way the centre. */
export function marginBin(d:number,r:number):number{
  const pts=Math.round((d-r)*1e6)/1e4;   // percentage points, rounded so 0.20 - 0.15 is 5 and not 4.999...
  return pts<=-15?0:pts<=-5?1:pts<5?2:pts<15?3:4;
}
