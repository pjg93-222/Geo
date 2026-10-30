// Walk list API contract (schema walk-list-1). Server side: server/research/walk_views.py.
// Coordinates are [lon, lat]. Dates are ISO yyyy-mm-dd ('' when unknown).

export type WalkLevel = 'ss' | 'sh' | 'precinct';
export type WalkSide = 'even' | 'odd';
export type WalkSidePick = WalkSide | 'both';
export type WalkCode = 'S' | 'U' | 'O' | 'NH' | 'MV';
export type WalkPlacement = 'geocode' | 'fuzzy' | 'nearest' | 'interpolated' | 'parcel' | 'unlocated';
export type WalkListKind = 'standard' | 'reknock';

// Per-address evidence on imported-address doors (older payloads and street walk lists carry none). `rejected` is set only by the frontend.
export type WalkAddressStatus = 'source_exact' | 'independently_confirmed' | 'recovered_number' | 'conflicting' | 'unresolved';
export type WalkLocationPrecision = 'parcel' | 'source_point' | 'interpolated' | 'unlocated';
export type WalkReviewDecision = 'accepted' | 'review' | 'rejected';
export interface WalkEvidence {
  version: 1;
  address_status: WalkAddressStatus;
  location_precision: WalkLocationPrecision;
  decision: WalkReviewDecision;
  reason_codes: string[];
  source_placement: string | null;
  original_house: string | null;   // '' = blank before recovery; null = unchanged
}

export interface WalkArea { ss?: string; sh?: string; precinct?: string }

// GET /api/v3/walk/options?level=ss|sh|precinct[&ss=&sh=]
export interface WalkOption { id: string; label: string; people: number; doors: number }
export interface WalkOptionsPayload { level: WalkLevel; options: WalkOption[] }

// GET /api/v3/walk/streets?q=&ss=&sh=&precinct=
export interface WalkStreet { id: number; label: string; city: string; zip: string; doors: number; people: number; even: number; odd: number }
export interface WalkStreetsPayload { streets: WalkStreet[] }

// POST /api/v3/walk/match {rows: WalkMatchInput[], ss?, sh?, precinct?}
export interface WalkMatchInput { street: string; side?: string; city?: string; zip?: string }
export type WalkMatchStatus = 'matched' | 'pick' | 'none';
export interface WalkMatchRow { row: number; input: string; side: WalkSidePick; status: WalkMatchStatus; candidates: WalkStreet[]; suggestions: string[] }
export interface WalkMatchPayload { rows: WalkMatchRow[] }

// GET /api/v3/walk/list?streets=12:even,15:both&ss=&sh=&precinct=&spans=&fresh=1&doors=&wp=&wi=&profile= (`profile`, omitted for 'all', limits the list to a voter profile; `fresh` leaves out the people at doors with a recorded result;
// `doors`, with `wp` and `wi`, needs `spans` and `fresh`: the blocks are taken in the order given and the last one is cut to reach that many doors)
export interface WalkListTrim { doors: number; wp?: string; wi?: string }
export interface WalkTally { voted: number; eligible: number }
// A voter's latest result across all lists: its code, the day of the visit, and the key of the saved list it was recorded on
// (a result with `list` equal to the open list's key is a mark on that list; any other is history from another list).
export interface WalkLast { code: WalkCode; on: string; list: string | null }
export type WalkOptInKind = 'donor' | 'volunteer';
export type WalkOptInStatus = 'unknown' | 'opted_in' | 'opted_out';
export interface WalkOptInState { status: WalkOptInStatus; recorded_at: string | null; source: 'recorded_manually' | null }
export interface WalkOptIns { donor: WalkOptInState; volunteer: WalkOptInState }
export interface WalkVoter {
  person_id: string; name: string; phone: string;
  pri: WalkTally; gen: WalkTally; abs: WalkTally;
  party: string; age: number | null; gender: string; reg_date: string;
  last: WalkLast | null;
  opt_ins?: WalkOptIns; // Older list payloads may omit recorded opt-ins.
}
export interface WalkDoor { seq: number; house: string; unit: string; placed_by: WalkPlacement; lon: number | null; lat: number | null; voters: WalkVoter[]; city?: string; state?: string; zip?: string; address?: string; warnings?: string[]; location_evidence?: unknown; evidence?: WalkEvidence }
export interface WalkRun {
  stop: number; street_id: number; street: string; side: WalkSidePick; precinct: string; locality?: string;
  from_house: string; to_house: string; walk_m: number; approach_m?: number;
  lr: 1 | -1 | 0;                // walked side relative to path direction: 1 left, -1 right, 0 unknown
  path: [number, number][];      // street centreline along this run, in walk direction
  approach: [number, number][];  // shortest path from the previous run's end ([] for stop 1)
  doors: WalkDoor[];
}
export interface WalkTotals { streets: number; stops: number; doors: number; voters: number; walk_m: number }
export interface WalkListMeta { key: string | null; title: string; kind: WalkListKind; created_at: string | null }
export interface WalkWindow { primaries: string[]; generals: string[]; abs_rule: string }
// Geographic overlay around the list (computed per response, never stored with saved lists).
export interface WalkContextRoad { k: 'S1200' | 'S1400' | 'V'; n: string; c: [number, number][] }  // V = generated street/connector
export interface WalkContextArea { id: string; label: string; rings: [number, number][][] }
export interface WalkContext { bbox: [number, number, number, number]; roads: WalkContextRoad[]; precincts: WalkContextArea[]; homes: [number, number][] }
// `fingerprint`: a digest of what the list shows and of the voter behind each person id (absent on lists saved before it existed).
export interface WalkListPayload {
  schema: 'walk-list-1'; generated_on: string; list: WalkListMeta; query: Record<string, string>;
  window: WalkWindow; totals: WalkTotals; bbox: [number, number, number, number]; runs: WalkRun[];
  fingerprint?: string; context?: WalkContext;
}

// POST /api/v3/walk/lists {title, query, fingerprint} -> 201 WalkSavedPayload | {title, from, reknock: true} -> 201 WalkListPayload (list.key set)
// The list is built again to be saved; `changed` is true when that list differs from the one whose fingerprint was sent (the voter data
// changed in between): the list saved, and sent back, is the new one.
export interface WalkSavedPayload extends WalkListPayload { changed?: boolean }
// A re-knock list also says how many of the voters marked Not home the voter data no longer has (they are not on it).
export interface WalkReknockPayload extends WalkListPayload { missing?: number }
// GET  /api/v3/walk/lists -> WalkListsPayload ; GET /api/v3/walk/lists/<key> -> WalkListPayload
export interface WalkSavedList { key: string; title: string; kind: WalkListKind; created_at: string; doors: number; voters: number }
export interface WalkListsPayload { lists: WalkSavedList[] }

// POST /api/v3/walk/dispositions {list, on, entries} -> {saved}
export interface WalkDispositionEntry { person_id: string; code: WalkCode | null }
export interface WalkDispositionsResult { saved: number }

// POST /api/v3/walk/opt-ins {list, entries}; only supplied kinds are changed.
export type WalkOptInEntry = { person_id: string } & Partial<Record<WalkOptInKind, WalkOptInStatus>>;
export interface WalkOptInsResult { saved: number; opt_ins: Record<string, WalkOptIns> }

// GET /api/v3/walk/overview?ss=&sh=&precinct= -> GeoJSON FeatureCollection<LineString, WalkOverviewProps>
// `parties` counts each person by party code as it is on file ('' = no code), codes sorted.
export interface WalkOverviewProps { span: number; street: string; doors: number; parties: Record<string, number> }

// GET /api/v3/walk/plan?ss=&sh=&precinct=&preset=&w_value=&w_pop=&w_voter=&wp=&wi=&doors= (candidate districts; 404 without a candidate, 503 until support is built)
// Each row is a block (a road span). Doors and people leave out the doors with a recorded result. `t_mean` is the block's average chance of voting, 0 to 1
// (five turnout scores, lined up against people's own voting record; a server before walk-plan-2 sends no `t_mean`).
export interface WalkPlanSpan {
  span: number; street: string; doors: number; people: number; len_m: number; s_mean: number | null; t_mean?: number | null;
  pop_density: number | null; voter_density: number | null; value: number; value_per_100m: number; score: number; rank: number;
}
// The blocks one click picks in a precinct (`doors` in the request): `asked` is the door count asked for, `spans` the blocks in the order picked,
// `jumps` the blocks reached by a straight-line hop from the blocks before them. `stopped`: full (the doors asked for), exhausted (no block left),
// far (the next block is beyond the hop limit), cap (one walk list is full). `trim` says the last block was cut to the doors asked for (`kept` of its
// `of` doors); it is null when nothing was cut and absent from a server that cannot cut (and cannot take `doors` on the list either).
export interface WalkTurf {
  asked: number; spans: number[]; doors: number; people: number; value: number; undecided: number; supporter: number;
  jumps: {span: number; metres: number}[]; stopped: 'full' | 'exhausted' | 'far' | 'cap'; trim?: {span: number; kept: number; of: number} | null;
}
export interface WalkPlan {
  schema: 'walk-plan-1' | 'walk-plan-2'; preset: string; weights: Record<'value' | 'pop_density' | 'voter_density', number>; p: string;
  race_mode: 'general' | 'primary'; spans: WalkPlanSpan[]; not_ranked: { spans: number; people: number };
  matched?: { people: number; scored: number };   // people in the area, and how many of them have a score
  potential?: { wp: number; wi: number }; knocked?: { people: number; doors: number }; turf?: WalkTurf;   // walk-plan-2
  affiliation?: string; // categorical simulation filter
  profile?: string;   // only when not 'all'
}
// `wp` and `wi` are the balance and influence weights as text (weightsQuery); `doors` asks for a turf and needs a precinct.
// `profile` limits the sums to a voter profile (omitted for 'all'); the plan then says which one it counted.
export interface WalkPlanOptions { preset?: string; w_value?: number; w_pop?: number; w_voter?: number; wp?: string; wi?: string; doors?: number; profile?: string; affiliation?: string }

// POST /api/v3/walk/scores {ids} -> {scores}; `sup` and `push` (Needs a push) only in candidate districts. Whole numbers, for the printed list.
export interface WalkScores { scores: Record<string, { ovr: number; part: number; sup?: number; push?: number }> }
