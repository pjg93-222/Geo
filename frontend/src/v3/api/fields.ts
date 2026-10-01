// The 40 framework fields in framework_config.field_order (FIDELITY_SPEC section 5.4): CUA 12, Participation 10,
// Sphere 7, Partisan lean 11. Labels are the drafted neutral noun phrases; the live endpoint sends its own labels, and
// this table is used only to build exact-shape fixtures.
export type FieldKind='yn'|'score'|'int'|'owner'|'homeowner'|'dwelling'|'children'|'party'|'lean-yn';
export interface FieldDef{
  key:string;group:'cua'|'part'|'soi'|'lean';label:string;kind:FieldKind;
  /** index weight w_f for the positive groups; signed weight (100 * w) for the lean tags, +-50 for registration */
  w:number;
}
const D=(key:string,group:FieldDef['group'],label:string,kind:FieldKind,w:number):FieldDef=>({key,group,label,kind,w});

export const FIELDS:FieldDef[]=[
  D('sci_cpi_online_pay_index','cua','Online payment','yn',1/15),
  D('sci_cpi_internet_buy_index','cua','Internet buying','yn',1/15),
  D('sci_ct_smrtphn_typeowns_iphone','cua','iPhone ownership','yn',1/15),
  D('sci_cpi_auto_index','cua','Automobile interest','yn',1/15),
  D('sci_cpi_insurance_auto_index','cua','Auto insurance','yn',1/15),
  D('sci_cpi_insurance_index','cua','Insurance','yn',1/15),
  D('sci_cpi_cc_index','cua','Credit card index','yn',4/25),
  D('sci_credit_card_user','cua','Credit card use','yn',4/25),
  D('sai_recession_sensitivity_score','cua','Recession sensitivity','score',2/25),
  D('sci_owner_renter','cua','Owner or renter','owner',1/15),
  D('sii_homeowner','cua','Homeowner','homeowner',1/15),
  D('sii_dwelling_type','cua','Dwelling type','dwelling',1/15),

  D('sai_activist_score','part','Activist score','score',1/4),
  D('sai_digital_reachability_score','part','Digital reachability','score',1/4),
  D('sai_evav_score','part','Early or mail voting','score',1/8),
  D('sai_evav_timing_score','part','Early or mail voting timing','score',1/8),
  D('sai_local_voter_score','part','Local voter','score',1/24),
  D('sai_midterm_general_turnout_score','part','Midterm general turnout','score',1/24),
  D('sai_non_presidential_primary_turnout_score','part','Non-presidential primary turnout','score',1/24),
  D('sai_offyear_general_turnout_score','part','Off-year general turnout','score',1/24),
  D('sai_presidential_general_turnout_score','part','Presidential general turnout','score',1/24),
  D('sai_presidential_primary_turnout_score','part','Presidential primary turnout','score',1/24),

  D('household_size','soi','Household size','int',1/30),
  D('sci_number_of_adults_in_household','soi','Adults in household','int',1/30),
  D('sci_number_of_children_in_household','soi','Children in household','children',1/30),
  D('sci_charitable_donor','soi','Charitable donor','yn',9/80),
  D('sci_cpi_donor_index','soi','Donor index','yn',9/80),
  D('sci_ct_dnr_contribamt_high','soi','High contribution amount','yn',9/40),
  D('sci_ct_volunteer_chtyorg','soi','Charitable-organization volunteer','yn',9/20),

  D('sci_cpi_conservative_index','lean','Conservative index','lean-yn',-100/6),
  D('sci_cpi_liberal_index','lean','Liberal index','lean-yn',100/6),
  D('sci_ct_political_partyaff_democrat','lean','Democratic affiliation','lean-yn',100/6),
  D('sci_ct_political_partyaff_gop','lean','Republican affiliation','lean-yn',-100/6),
  D('sci_ct_political_partyaff_ind','lean','Independent affiliation','lean-yn',0),
  D('sci_ct_political_outlk_vconserv','lean','Very conservative outlook','lean-yn',-100/9),
  D('sci_ct_political_outlk_swconserv','lean','Somewhat conservative outlook','lean-yn',-100/18),
  D('sci_ct_political_outlk_mid','lean','Middle outlook','lean-yn',0),
  D('sci_ct_political_outlk_swliberal','lean','Somewhat liberal outlook','lean-yn',100/18),
  D('sci_ct_political_outlk_vliberal','lean','Very liberal outlook','lean-yn',100/9),
  D('svi_party_registration','lean','Party registration','party',50),
];

export const GROUP_LABEL={cua:'Credit Union Alignment',part:'Participation',soi:'Sphere of Influence',lean:'Political views'} as const;
export const GROUPS=['cua','part','soi','lean'] as const;

export const OWNER_TEXT:Record<string,string>={H:'Known homeowner',O:'Probable homeowner',U:'Unknown',P:'Probable renter',R:'Known renter'};
export const PARTY_TEXT:Record<string,string>={D:'Democrat',R:'Republican',U:'Unaffiliated',G:'Green',L:'Libertarian',O:'Other',T:'Code T'};
