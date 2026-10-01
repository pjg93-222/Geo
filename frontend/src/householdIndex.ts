import type {Household} from './StreetOverlay';
export type HouseholdIndex={total:number;query:(bounds:[number,number,number,number])=>Household[]};

/** A geographic grid avoids scanning every household on each map movement. */
export function buildHouseholdIndex(rows:Household[]):HouseholdIndex{
  const buckets=new Map<string,Household[]>();
  for(const row of rows){const key=`${Math.floor(row.longitude*100)},${Math.floor(row.latitude*100)}`;const bucket=buckets.get(key);if(bucket)bucket.push(row);else buckets.set(key,[row]);}
  return {total:rows.length,query:([west,south,east,north])=>{
    if(![west,south,east,north].every(Number.isFinite)||west>east||south>north)return [];
    const xmin=Math.floor(west*100),xmax=Math.floor(east*100),ymin=Math.floor(south*100),ymax=Math.floor(north*100);
    const result:Household[]=[];
    const append=(bucket:Household[]|undefined)=>{if(bucket)for(const row of bucket)if(row.longitude>=west&&row.longitude<=east&&row.latitude>=south&&row.latitude<=north)result.push(row);};
    if((xmax-xmin+1)*(ymax-ymin+1)>10000){for(const bucket of buckets.values())append(bucket);}
    else for(let x=xmin;x<=xmax;x++)for(let y=ymin;y<=ymax;y++)append(buckets.get(`${x},${y}`));
    return result;
  }};
}
