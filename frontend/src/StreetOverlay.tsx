import {useEffect, useRef} from 'react';
import * as maplibregl from 'maplibre-gl';
import type {Map as MLMap} from 'maplibre-gl';
import type {HouseholdIndex} from './householdIndex';

export type Household = {id:string; longitude:number; latitude:number; count:number; address:string};
export type HouseholdDetail = {id:string;address:string;count:number;people:{id:string;name:string}[];truncated?:boolean};
export type StreetInfo = {loading:boolean;count:number;truncated:boolean;error?:string};

/** Local source records are displayed separately from all geographic indices. */
export function StreetOverlay({map,active,basis,viewport,sourceHash,household,preloaded,onHousehold,onInfo,onPerson}: {
  map:MLMap|null;active:boolean;basis:string;viewport:number;sourceHash:string;
  household:HouseholdDetail|null;onHousehold:(id:string)=>void;
  preloaded?:HouseholdIndex;
  onInfo:(info:StreetInfo)=>void;onPerson:(id:string)=>void;
}) {
  const markers = useRef<maplibregl.Marker[]>([]);
  const sourceRows = useRef<Household[]>([]);
  const callbacks = useRef({onHousehold,onInfo,onPerson});
  callbacks.current={onHousehold,onInfo,onPerson};
  const selected = useRef(household);selected.current=household;
  const loadedBasis = useRef(basis);

  function clear() {markers.current.forEach(marker=>marker.remove());markers.current=[];}
  function draw() {
    clear();if(!map||!active)return;
    const groups = new Map<string,Household[]>();
    for(const row of sourceRows.current) {
      const pixel=map.project([row.longitude,row.latitude]);
      const key=`${Math.floor(pixel.x/46)},${Math.floor(pixel.y/46)}`;
      const group=groups.get(key);if(group)group.push(row);else groups.set(key,[row]);
    }
    for(const rows of groups.values()) {
      const wrap=document.createElement('div');wrap.className='household-marker';
      const button=document.createElement('button');button.type='button';
      const count=rows.reduce((sum,row)=>sum+row.count,0);
      button.className=rows.length>1?'street-cluster':'street-household';
      button.textContent=String(count);
      button.setAttribute('aria-label',rows.length>1?`${rows.length} households, ${count} source records. Zoom in.`:`${rows[0].address}, ${count} source records`);
      button.title=button.getAttribute('aria-label')||'';
      const chosen=rows.find(row=>row.id===selected.current?.id);
      if(chosen)button.classList.add('is-selected');
      const longitude=rows.reduce((sum,row)=>sum+row.longitude,0)/rows.length;
      const latitude=rows.reduce((sum,row)=>sum+row.latitude,0)/rows.length;
      button.onclick=()=>{
        if(rows.length===1)callbacks.current.onHousehold(rows[0].id);
        else if(map.getZoom()<19.5)map.easeTo({center:[longitude,latitude],zoom:Math.min(map.getZoom()+1.5,20),duration:0});
        else {
          // Multiple source households can occupy the same address. The list
          // makes each accessible without assigning invented coordinates.
          let list=wrap.querySelector('.coincident-households');
          if(list){list.remove();return;}
          list=document.createElement('div');list.className='coincident-households';
          rows.forEach((row,index)=>{const choice=document.createElement('button');choice.type='button';choice.textContent=`Household ${index+1} · ${row.count} records`;choice.onclick=()=>callbacks.current.onHousehold(row.id);list!.appendChild(choice);});
          wrap.appendChild(list);
        }
      };
      wrap.appendChild(button);
      if(chosen&&selected.current) {
        const people=selected.current.people;
        people.forEach((person,index)=>{
          const dot=document.createElement('button');dot.type='button';dot.className='street-person';
          const ring=Math.floor(index/12);
          const members=Math.min(12,people.length-ring*12);
          const angle=2*Math.PI*(index%12)/Math.max(members,1)-Math.PI/2;
          const radius=35+ring*23;
          dot.style.left=`${Math.cos(angle)*radius}px`;dot.style.top=`${Math.sin(angle)*radius}px`;
          dot.title=person.name;dot.setAttribute('aria-label',person.name);
          dot.onclick=()=>callbacks.current.onPerson(person.id);wrap.appendChild(dot);
        });
      }
      markers.current.push(new maplibregl.Marker({element:wrap}).setLngLat([longitude,latitude]).addTo(map));
    }
  }

  useEffect(()=>{
    if(loadedBasis.current!==basis){clear();sourceRows.current=[];loadedBasis.current=basis;}
    if(!map||!active){clear();sourceRows.current=[];callbacks.current.onInfo({loading:false,count:0,truncated:false});return;}
    // React can activate street mode before MapLibre has applied the new
    // camera. Wait for moveend instead of projecting the entire region once.
    if(map.getZoom()<15){callbacks.current.onInfo({loading:true,count:0,truncated:false});return;}
    const controller=new AbortController();const bounds=map.getBounds();
    const bbox=[bounds.getWest(),bounds.getSouth(),bounds.getEast(),bounds.getNorth()];
    if(preloaded){
      sourceRows.current=preloaded.query(bbox as [number,number,number,number]);
      callbacks.current.onInfo({loading:false,count:sourceRows.current.length,truncated:false});draw();return;
    }
    if(bbox[2]-bbox[0]>0.06||bbox[3]-bbox[1]>0.06){callbacks.current.onInfo({loading:false,count:0,truncated:false,error:'Zoom closer to load household records.'});return;}
    callbacks.current.onInfo({loading:true,count:0,truncated:false});
    const query=new URLSearchParams({bbox:bbox.join(','),basis,zoom:String(map.getZoom())});
    fetch('/api/street?'+query,{credentials:'same-origin',signal:controller.signal})
      .then(async response=>{const data=await response.json();if(!response.ok)throw new Error(data.error||'Household records are unavailable.');return data;})
      .then(data=>{
        if(controller.signal.aborted)return;
        if(data.source_sha256!==sourceHash)throw new Error('Street records belong to a different source snapshot.');
        sourceRows.current=data.households;
        callbacks.current.onInfo({loading:false,count:data.total_households,truncated:data.truncated});draw();
      }).catch(error=>{if(error.name!=='AbortError')callbacks.current.onInfo({loading:false,count:0,truncated:false,error:error.message});});
    return()=>controller.abort();
  },[map,active,basis,viewport,sourceHash,preloaded]);
  useEffect(()=>{draw();},[household]);
  useEffect(()=>()=>clear(),[]);
  return null;
}
