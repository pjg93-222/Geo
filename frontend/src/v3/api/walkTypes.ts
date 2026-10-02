// Walk list API contract (schema walk-list-1). Server side: server/research/walk_views.py.
// Coordinates are [lon, lat]. Dates are ISO yyyy-mm-dd ('' when unknown).

export type WalkLevel = 'ss' | 'sh' | 'precinct';
export type WalkSide = 'even' | 'odd';
export type WalkSidePick = WalkSide | 'both';
export type WalkCode = 'S' | 'U' | 'O' | 'NH' | 'MV';
export type WalkPlacement = 'geocode' | 'fuzzy' | 'nearest' | 'interpolated';
export type WalkListKind = 'standard' | 'reknock';

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

// GET /api/v3/walk/list?streets=12:even,15:both&ss=&sh=&precinct=
export interface WalkTally { voted: number; eligible: number }
// A voter's latest result across all lists: its code, the day of the visit, and the key of the saved list it was recorded on
// (a result with `list` equal to the open list's key is a mark on that list; any other is history from another list).
export interface WalkLast { code: WalkCode; on: string; list: string | null }
export interface WalkVoter {
  person_id: string; name: string; phone: string;
  pri: WalkTally; gen: WalkTally; abs: WalkTally;
  party: string; age: number | null; gender: string; reg_date: string;
  last: WalkLast | null;
}
export interface WalkDoor { seq: number; house: string; unit: string; placed_by: WalkPlacement; lon: number; lat: number; voters: WalkVoter[] }
export interface WalkRun {
  stop: number; street_id: number; street: string; side: WalkSide; precinct: string;
  from_house: string; to_house: string; walk_m: number;
  lr: 1 | -1 | 0;                // walked side relative to path direction: 1 left, -1 right, 0 unknown
  path: [number, number][];      // street centreline along this run, in walk direction
  approach: [number, number][];  // shortest path from the previous run's end ([] for stop 1)
  doors: WalkDoor[];
}
export interface WalkTotals { streets: number; stops: number; doors: number; voters: number; walk_m: number; minutes: number }
export interface WalkListMeta { key: string | null; title: string; kind: WalkListKind; created_at: string | null }
export interface WalkWindow { primaries: string[]; generals: string[]; abs_rule: string }
// Geographic overlay around the list (computed per response, never stored with saved lists).
export interface WalkContextRoad { k: 'S1200' | 'S1400' | 'V'; n: string; c: [number, number][] }  // V = generated street/connector
export interface WalkContextArea { id: string; label: string; rings: [number, number][][] }
export interface WalkContext { bbox: [number, number, number, number]; roads: WalkContextRoad[]; precincts: WalkContextArea[]; homes: [number, number][] }
export interface WalkListPayload {
  schema: 'walk-list-1'; generated_on: string; list: WalkListMeta; query: Record<string, string>;
  window: WalkWindow; totals: WalkTotals; bbox: [number, number, number, number]; runs: WalkRun[];
  context?: WalkContext;
}

// POST /api/v3/walk/lists {title, query} | {title, from, reknock: true} -> 201 WalkListPayload (list.key set)
// GET  /api/v3/walk/lists -> WalkListsPayload ; GET /api/v3/walk/lists/<key> -> WalkListPayload
export interface WalkSavedList { key: string; title: string; kind: WalkListKind; created_at: string; doors: number; voters: number }
export interface WalkListsPayload { lists: WalkSavedList[] }

// POST /api/v3/walk/dispositions {list, on, entries} -> {saved}
export interface WalkDispositionEntry { person_id: string; code: WalkCode | null }
export interface WalkDispositionsResult { saved: number }

// GET /api/v3/walk/overview?ss=&sh=&precinct= -> GeoJSON FeatureCollection<LineString, WalkOverviewProps>
export interface WalkOverviewProps { span: number; street: string; doors: number; d: number; r: number; u: number; o: number }
