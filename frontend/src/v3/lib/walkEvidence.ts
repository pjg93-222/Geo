import type {WalkDoor,WalkEvidence,WalkRun} from '../api/walkTypes';

// Human labels and small pure helpers for per-address evidence. No imports beyond types, so the PDF and CSV code can use them.
export const STATUS_LABEL:Record<WalkEvidence['address_status'],string>={
  source_exact:'Matches source address',independently_confirmed:'Independently confirmed',recovered_number:'House number recovered',
  conflicting:'Conflicting records',unresolved:'Not independently located',
};
export const PRECISION_LABEL:Record<WalkEvidence['location_precision'],string>={
  parcel:'Parcel point – entrance not confirmed',source_point:'Source point',interpolated:'Interpolated',unlocated:'No map pin',
};
export const REASON_LABEL:Record<string,string>={
  missing_coordinates:'No coordinates recorded',inferred_no_house:'Street inferred; no house number',inferred_off_road:'Inferred location is off the road',
  unsupported_provenance:'Location source is not supported',no_confirming_parcel:'No parcel confirms this address',
  conflicting_parcel_elsewhere:'A parcel with this number is elsewhere',no_unique_parcel_match:'No single matching parcel',
  parcel_representative_point:'Parcel representative point; entrance not confirmed',number_recovered:'House number recovered from source and parcel records',
  no_house_number:'No house number',mixed_zip:'Mixed ZIP codes',
};
export const reasonText=(code:string):string=>REASON_LABEL[code]??code;

/** Rows printed with a ‡: the address could not be independently located. */
export const unlocatedFlag=(door:Pick<WalkDoor,'evidence'>):boolean=>door.evidence?.address_status==='unresolved'||door.evidence?.address_status==='conflicting';
export const hasEvidence=(payload:{runs:WalkRun[]}):boolean=>(payload.runs??[]).some(run=>run.doors.some(door=>!!door.evidence));

/** Stable identity of a door across rebuilds, splits and caps (sequence numbers change; the address does not).
 *  The key is address-based with no stop identity: distinct same-address doors (e.g. blank house on the same street/precinct/unit/zip)
 *  share one decision. That is acceptable only while such rows offer a single option. */
export const evidenceDoorKey=(run:Pick<WalkRun,'street_id'|'street'|'precinct'>,door:Pick<WalkDoor,'house'|'unit'|'zip'>):string=>
  JSON.stringify([run.street_id,run.street,run.precinct,door.house,door.unit,door.zip??'']);

export type EvidenceChoice='accepted'|'rejected';
export interface DecisionOption{decision:EvidenceChoice;label:string}
/** The choices offered for a row that needs review. Rejecting only ever removes a map pin; the address and the people on it stay. */
export function decisionOptions(evidence:WalkEvidence,hasPin:boolean):DecisionOption[]{
  const remove:DecisionOption={decision:'rejected',label:'Remove map pin'};
  if(evidence.location_precision==='parcel')return [{decision:'accepted',label:evidence.address_status==='recovered_number'?'Accept parcel location and recovered number':'Accept parcel location'},remove];
  if(hasPin&&evidence.address_status!=='unresolved'&&evidence.address_status!=='conflicting')return [{decision:'accepted',label:'Accept location'},remove];
  return [{decision:'accepted',label:'Keep printable without map pin'}];
}
