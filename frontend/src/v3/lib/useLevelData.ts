// Loads one level's values and geometry. The previous level stays on screen until the new one is ready ("never blank");
// coarse geometry paints first and the fine geometry replaces it ("first tiles from cache, low resolution, then sharp").
import {useEffect,useMemo,useRef,useState} from 'react';
import type {AreaGeometry,AreasPayload,Meta} from '../types';
import {getAreas,getGeometry} from '../api/client';
import type {Level} from './format';

export interface GeoEntry{fc:AreaGeometry;pos:Map<string,number>;quality:'coarse'|'fine'}
export interface LevelState{level:Level;areas:AreasPayload|null;geo:GeoEntry|null}

function entryOf(fc:AreaGeometry,quality:'coarse'|'fine'):GeoEntry{
  const pos=new Map<string,number>();
  fc.features.forEach((f,k)=>{f.properties.i=k;pos.set(f.properties.id,k);});
  return {fc,pos,quality};
}

export function useLevelData(meta:Meta|null,level:Level):{state:LevelState|null;loadingLevel:Level|null;error:string;retry:()=>void}{
  const [state,setState]=useState<LevelState|null>(null);
  const [loadingLevel,setLoading]=useState<Level|null>(null);
  const [error,setError]=useState('');
  const [nonce,setNonce]=useState(0);
  const live=useRef(0);

  useEffect(()=>{
    if(!meta)return;
    const token=++live.current;
    setLoading(level);setError('');
    const coarse=getGeometry(meta,level,'coarse');
    const areas=getAreas(meta,level);
    // skeleton first: the geometry is local and draws before the values arrive
    coarse.then(fc=>{
      if(live.current!==token)return;
      setState(prev=>prev?prev:{level,areas:null,geo:entryOf(fc,'coarse')});
    }).catch(()=>{});
    Promise.all([coarse,areas]).then(([fc,a])=>{
      if(live.current!==token)return;
      setState(prev=>({level,areas:a,geo:prev&&prev.level===level&&prev.geo?prev.geo:entryOf(fc,'coarse')}));
      setLoading(null);
      getGeometry(meta,level,'fine').then(fine=>{
        if(live.current!==token)return;
        setState(prev=>prev&&prev.level===level?{...prev,geo:entryOf(fine,'fine')}:prev);
      }).catch(()=>{});
    }).catch(e=>{if(live.current===token){setError((e as Error).message||'The data could not be loaded.');setLoading(null);}});
    return ()=>{live.current++;};
  },[meta,level,nonce]);

  return {state,loadingLevel,error,retry:()=>setNonce(n=>n+1)};
}

/** Areas payload id -> index. */
export function useIdIndex(areas:AreasPayload|null):Map<string,number>{
  return useMemo(()=>new Map((areas?.ids??[]).map((id,i)=>[id,i])),[areas]);
}

/** Re-order area bins (index = payload position) into geometry order (index = feature position). */
export function binsInGeometryOrder(areaBins:Uint8Array,areas:AreasPayload,geo:GeoEntry,pendingBin:number):Uint8Array{
  const out=new Uint8Array(geo.fc.features.length+1).fill(pendingBin);
  const idx=new Map(areas.ids.map((id,i)=>[id,i]));
  geo.fc.features.forEach((f,k)=>{const a=idx.get(f.properties.id);if(a!==undefined)out[k]=areaBins[a];});
  return out;
}

export function featureBounds(f:GeoJSON.Feature<GeoJSON.Polygon|GeoJSON.MultiPolygon>):[number,number,number,number]{
  let w=180,s=90,e=-180,n=-90;
  const walk=(c:unknown):void=>{
    if(Array.isArray(c)&&typeof c[0]==='number'){const x=c[0] as number,y=c[1] as number;if(x<w)w=x;if(x>e)e=x;if(y<s)s=y;if(y>n)n=y;}
    else if(Array.isArray(c))c.forEach(walk);
  };
  walk(f.geometry.coordinates);
  return [w,s,e,n];
}
