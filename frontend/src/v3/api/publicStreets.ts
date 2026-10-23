import {ApiError,localJson,postJson} from './client.ts';

export type PublicPoint=[number,number];
export type PublicBounds=[number,number,number,number];
export interface PublicStreetCatalog {
  id:string;label:string;bbox:PublicBounds;street_count:number;estimated_doors:number;
  source_date:string;count_basis:string;sources:{label:string;url:string}[];
}
export interface PublicStreet {
  id:string;label:string;geometry:{type:'MultiLineString';coordinates:PublicPoint[][]};
  estimated_doors:number;address_points:[number,number,number][];
}
export interface PublicStreetQuery {
  catalog:PublicStreetCatalog;polygon:PublicPoint[];streets:PublicStreet[];
  estimated_doors:number;count_basis:string;
}
const BASE='/api/v3/walk/public-streets';
export function getPublicStreetCatalogs(signal?:AbortSignal):Promise<{catalogs:PublicStreetCatalog[]}>{
  return localJson(`${BASE}/catalogs`,signal);
}
export function queryPublicStreets(catalog_id:string,polygon:PublicPoint[],signal?:AbortSignal):Promise<PublicStreetQuery>{
  return postJson(`${BASE}/query`,{catalog_id,polygon},signal);
}
/** The server answers 403 when the caller is not the local administrator; the panel then stays hidden. */
export const isLocalOnlyDenial=(e:unknown):boolean=>e instanceof ApiError&&e.status===403;
