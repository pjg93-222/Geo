import {ApiError,localJson,postJson} from './client.ts';
import type {PublicPoint} from './publicStreets.ts';

/** One choice of the Audience filter. Party codes: D R U G L W O T and "none"; canvass codes: S U O NH and "none". */
export interface AudienceChoice {code:string;label:string;count:number}
export interface AudienceOptions {
  district:string;candidate_party:'D'|'R'|null;support_coverage:boolean;party:AudienceChoice[];canvass:AudienceChoice[];
}
export interface AudienceSelection {party:string[];canvass:string[]}
export interface AudienceSupport {low:number;high:number;include_no_score:boolean}
export interface AudienceQuery {polygon:PublicPoint[];include:AudienceSelection;exclude:AudienceSelection;support:AudienceSupport}
export interface AudienceStreet {id:string;label:string;center:PublicPoint;matching_doors:number;voter_doors:number;dropped_by_exclude:number}
export interface AudienceTotals {matching_doors:number;voter_doors:number;dropped_by_exclude:number;unplaced:number}
export interface AudienceStreets {basis:'voter-file';streets:AudienceStreet[];totals:AudienceTotals;support_coverage:boolean}
const BASE='/api/v3/walk/audience';
export function getAudienceOptions(signal?:AbortSignal):Promise<AudienceOptions>{return localJson(`${BASE}/options`,signal);}
export function queryAudienceStreets(query:AudienceQuery,signal?:AbortSignal):Promise<AudienceStreets>{return postJson(`${BASE}/streets`,query,signal);}
/** 403 (not a manager, or demo mode) and 503 (no walk store): the panel then offers the public estimate only. */
export const isVoterFileUnavailable=(e:unknown):boolean=>e instanceof ApiError&&(e.status===403||e.status===503);
