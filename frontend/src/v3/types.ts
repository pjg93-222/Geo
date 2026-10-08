// Wire types for the v3 endpoints (IMPLEMENTATION_PLAN section 6). The adapter in api/client.ts is the only place that
// knows whether a payload came from the live server or from a fixture of exactly this shape.
import type {Level} from './lib/format';

export type Group='ovr'|'cua'|'part'|'soi'|'lean';
/** Candidate districts add `sup` (Candidate support); Group stays the five Colorado groups so Record<Group,...> maps keep compiling. */
export type IndexLayer=Group|'sup';
/** What the map overlay can show: the index layers plus the two density layers from /api/v3/density. */
export type OverlayLayer=IndexLayer|'pop_density'|'voter_density';
export type RaceMode='general'|'primary';
export type Grade='A'|'B'|'C'|'D';
export type Source='live'|'fixture';

export interface CandidateInfo{name:string;party:'D'|'R';party_label:string;race_mode:RaceMode}
export interface Meta{
  schema:'v3-meta-1';snapshot:string;demo:boolean;source?:Source;
  people:number;households:number;district_label:string;
  store?:{status:string;note:string};
  data_origin?:string;
  district_registration?:{d:number;r:number;u:number;o:number};
  district_rate:Record<'ovr'|'cua'|'part'|'soi',number>;
  share_breaks:number[];lean_cuts:[number,number];measured_breaks:number[];household_breaks:number[];
  weights:{cua:number;part:number;soi:number;sup?:number};
  /** Present only for a district with a candidate (OR, CT). */
  candidate?:CandidateInfo;
  /** Present only for a district whose overall is interim (OR, CT); Colorado's meta has no such key. Shown as given under the overall weights. */
  overall_note?:string;
  levels:Level[];bbox:[number,number,number,number];
  floors:{min_n:number;min_households:number};
  /** District means from the score store (live only): expected scores, % measured per group, mean lean. Authoritative for the empty-selection rail. */
  district_mean?:{score:Record<'ovr'|'cua'|'part'|'soi',number>;measured:Record<Group,number>;lean:number};
}

/** One scored group of an area table, columnar (index i = area i). Withheld areas hold null everywhere. */
export interface ScoreCell{
  mean:(number|null)[];       // eb_mean, standing points 0..100
  se:(number|null)[];         // eb_se, posterior SD of the mean
  measured:(number|null)[];   // measured_pct_mean
  pct:(number|null)[];        // area_pct, midrank percentile among published areas of the level
  top10:(number|null)[];      // top10_share_eb, 0..1
  top10_se:(number|null)[];   // top10_eb_se
  top10_pct:(number|null)[];  // top10_area_pct
}
export interface LeanCell{
  mean:(number|null)[];se:(number|null)[];measured:(number|null)[];
  share_d:(number|null)[];share_r:(number|null)[];share_u:(number|null)[];
}
export interface AreasPayload{
  schema:'v3-areas-1';snapshot:string;basis:'registration';level:Level;
  ids:string[];labels:string[];status:('ok'|'withheld')[];
  n:(number|null)[];households:(number|null)[];
  parent:(string|null)[];     // block -> tract id
  precinct:(string|null)[];   // block -> modal precinct id
  tract:(string|null)[];      // precinct, zip -> modal tract id
  blocks:(number|null)[];     // precinct, tract, zip -> block count
  groups:Record<'ovr'|'cua'|'part'|'soi',ScoreCell>&{lean:LeanCell;sup?:ScoreCell};
}

export interface AreaGeometry extends GeoJSON.FeatureCollection<GeoJSON.Polygon|GeoJSON.MultiPolygon,{id:string;lx:number;ly:number;i?:number}>{}

// --- people ------------------------------------------------------------------------------------------------------
export interface PeopleRow{
  rank:string;person_id:string;name:string;
  ovr_pct:number;lo:number;hi:number;cua_pct:number;part_pct:number;soi_pct:number;
  lean_code:number|'U';lean_label:string;ovr_grade:Grade;
  within_pct:number;within_group:string;
  /** Candidate districts only. */
  sup_pct?:number;
}
export interface PeoplePage{
  rows:PeopleRow[];total:number;first:number;last:number;page:number;pages:number;
  next:string|null;prev:string|null;
}
export interface PeopleQuery{
  reg:'all'|'D'|'R'|'U'|'O';grade:'ABCD'|'AB';lean:'all'|'-2'|'-1'|'0'|'1'|'2'|'U';
  level:Level|'';area:string;balanced:boolean;after:string;dir:'next'|'prev';
  /** '' is the overall standing; 'sup' (candidate districts) sorts by candidate support. */
  sort:''|'sup';
}

// --- profile -----------------------------------------------------------------------------------------------------
export interface IndexCard{score:number;pct:number;lo:number;hi:number;grade:Grade}
export type FieldStatus='observed'|'estimated'|'children_rule';
export interface FieldRow{
  field:string;label:string;value:string;status:FieldStatus;p:number|null;exposure:number;contribution:number;
}
export interface FieldGroup{id:'cua'|'part'|'soi'|'lean';label:string;n_fields:number;observed:number;fields:FieldRow[]}
export interface HouseholdMember{person_id:string;name:string;age:number|null;ovr_pct:number;grade:Grade;self:boolean}
export interface AreaRef{level:Level;id:string;label:string;pct:number|null;top10:number|null;withheld:boolean}
export interface Profile{
  person_id:string;name:string;age:number|null;household_size:number;
  registration:'D'|'R'|'U'|'O';block:string;precinct:string;
  registration_label?:string;
  header:{pct:number;lo:number;hi:number;grade:Grade;measured_pct:number;within_pct:number;within_group:string};
  cards:{cua:IndexCard;part:IndexCard;soi:IndexCard;lean:{lean:number;grade:Grade;undetermined:boolean};sup?:{score:number;pct:number}};
  groups:FieldGroup[];
  household:{code:number;mean_pct:number;members:HouseholdMember[]};
  area_context:{block:AreaRef;precinct:AreaRef};
}

// --- street ------------------------------------------------------------------------------------------------------
/** [household id, lon, lat, people, bin 0..4] */
export type StreetPoint=[string,number,number,number,number];
export interface StreetPayload{
  points:StreetPoint[];truncated:boolean;
  stats:{households:number;people:number;measured_pct:number;hist:[number,number,number,number,number]};
}
export interface HouseholdPopover{
  id:string;code:number;n_people:number;mean_pct:number;block_label:string;
  members:{person_id:string;name:string;age:number|null;ovr_pct:number;grade:Grade}[];
}
export interface SearchResult{people:{person_id:string;name:string;block_label:string}[]}

// --- district settings and density (GET /api/v3/features, /api/v3/density) -----------------------------------------------
export type SupportSignal='partisan'|'presidential'|'registration'|'primary_ballot';
export interface FeatureIndustry{id:string;label:string;fields:{name:string;label:string;weight:[number,number]}[]}
export interface FeatureRoi{
  presets:{id:string;label:string;formula:string}[];preset:string;p:string;
  weights:Record<'value'|'pop_density'|'voter_density',number>;min_doors:number;min_length_m:number;
}
/** `candidate` and `roi` are null for Colorado. `layers` lists `sup` once the score store holds support and the density layers once density.json is built. */
export interface Features{
  schema:'v3-features-1';district:string;industries:FeatureIndustry[];industry:string;
  candidate:(CandidateInfo&{primary_universe:'party_only'|'party_plus_unaffiliated';signals:Record<SupportSignal,number>})|null;
  party_codes:Record<string,string>;
  overlay:{layers:OverlayLayer[];density:boolean};
  roi:FeatureRoi|null;
}
/** Per level, parallel arrays over `ids`; null where an area has no value (no land, or voter density withheld). */
export interface DensityLevel{ids:string[];pop_density:(number|null)[];voter_density:(number|null)[];land_km2:(number|null)[]}
export interface Density{
  schema:'density-1';district:string;blocks_source:{file:string;sha256:string};
  join:{rate:number}&Record<string,number>;levels:Partial<Record<Level,DensityLevel>>;notes?:string[];
}
