import {useEffect,useRef,useState} from 'react';
import * as maplibregl from 'maplibre-gl';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import 'maplibre-gl/dist/maplibre-gl.css';
import type {PublicBounds,PublicPoint,PublicStreet} from '../api/publicStreets.ts';
import {boundsPolygon} from '../lib/publicStreetGroups.ts';
maplibregl.setWorkerUrl(workerUrl);
const empty:GeoJSON.FeatureCollection={type:'FeatureCollection',features:[]};
interface Props {coverage:PublicBounds;polygon:PublicPoint[];draft:PublicPoint[];drawing:boolean;streets:PublicStreet[];selected:ReadonlySet<string>;keyless?:boolean;onPoint:(p:PublicPoint)=>void}
/** Local vector canvas: no tiles, external glyphs, geocoder or identity-linked data. */
export function AreaStreetMap(p:Props){
  const el=useRef<HTMLDivElement>(null),map=useRef<maplibregl.Map|null>(null),current=useRef(p);current.current=p;
  const [ready,setReady]=useState(false),[failed,setFailed]=useState(false);
  useEffect(()=>{
    if(!el.current)return;let m:maplibregl.Map;
    try{m=new maplibregl.Map({container:el.current,style:{version:8,sources:{},layers:[{id:'land',type:'background',paint:{'background-color':'#EEE9DF'}}]},center:[(p.coverage[0]+p.coverage[2])/2,(p.coverage[1]+p.coverage[3])/2],zoom:13,attributionControl:false,dragRotate:false,pitchWithRotate:false,fadeDuration:0});}
    catch{setFailed(true);return;}
    map.current=m;m.touchZoomRotate.disableRotation();m.addControl(new maplibregl.NavigationControl({showCompass:false}),'top-right');
    m.on('load',()=>{
      for(const id of ['coverage','scope','streets','points','draft'])m.addSource(id,{type:'geojson',data:empty});
      m.addLayer({id:'scope-fill',type:'fill',source:'scope',paint:{'fill-color':'#54979B','fill-opacity':0.1}});
      m.addLayer({id:'coverage-line',type:'line',source:'coverage',paint:{'line-color':'#9A8DA3','line-width':1,'line-dasharray':[3,2]}});
      m.addLayer({id:'streets',type:'line',source:'streets',paint:{'line-color':['case',['get','selected'],'#286D73','#B8AEA8'],'line-width':['case',['get','selected'],3,1.5]}});
      m.addLayer({id:'points',type:'circle',source:'points',minzoom:13,paint:{'circle-color':'#57447B','circle-radius':['interpolate',['linear'],['zoom'],13,1.4,18,3.2],'circle-opacity':0.6}});
      m.addLayer({id:'scope-line',type:'line',source:'scope',paint:{'line-color':'#57447B','line-width':2}});
      m.addLayer({id:'draft-line',type:'line',source:'draft',paint:{'line-color':'#AE792A','line-width':3,'line-dasharray':[2,1]}});
      setReady(true);
    });
    m.on('click',e=>{if(current.current.drawing)current.current.onPoint([e.lngLat.lng,e.lngLat.lat]);});
    m.on('error',()=>setFailed(true));
    const observer=new ResizeObserver(()=>m.resize());observer.observe(el.current);
    return ()=>{observer.disconnect();m.remove();map.current=null;};
  },[]);
  useEffect(()=>{const m=map.current;if(!m||!ready)return;m.setMaxBounds([[p.coverage[0],p.coverage[1]],[p.coverage[2],p.coverage[3]]]);m.fitBounds([[p.coverage[0],p.coverage[1]],[p.coverage[2],p.coverage[3]]],{padding:25,duration:0,maxZoom:16});},[ready,p.coverage.join(',')]);
  useEffect(()=>{
    const m=map.current;if(!m||!ready)return;
    const set=(id:string,data:GeoJSON.FeatureCollection)=>(m.getSource(id) as maplibregl.GeoJSONSource).setData(data);
    const polygon=(points:PublicPoint[]):GeoJSON.FeatureCollection=>({type:'FeatureCollection',features:points.length>=4?[{type:'Feature',properties:{},geometry:{type:'Polygon',coordinates:[points]}}]:[]});
    set('coverage',polygon(boundsPolygon(p.coverage)));set('scope',polygon(p.polygon));
    set('streets',{type:'FeatureCollection',features:p.streets.map(s=>({type:'Feature',properties:{selected:p.selected.has(s.id)},geometry:s.geometry}))});
    set('points',{type:'FeatureCollection',features:p.streets.filter(s=>p.selected.has(s.id)).flatMap(s=>s.address_points.map(point=>({type:'Feature' as const,properties:{},geometry:{type:'Point' as const,coordinates:[point[0],point[1]]}})))});
    set('draft',{type:'FeatureCollection',features:p.draft.length>1?[{type:'Feature',properties:{},geometry:{type:'LineString',coordinates:p.draft}}]:[]});
  },[ready,p.coverage,p.polygon,p.streets,p.selected,p.draft]);
  useEffect(()=>{const m=map.current;if(m)m.getCanvas().style.cursor=p.drawing?'crosshair':'';},[p.drawing,ready]);
  return <div className="public-street-map-wrap"><div ref={el} className="public-street-map" aria-label="Public streets and anonymous residential points. Use the coordinate fields below as a keyboard alternative."/>
    {failed&&<p role="status" className="public-street-map-note">Map unavailable. Area coordinates and the complete street list remain available below.</p>}
    {!p.keyless&&<p className="public-street-map-key">Teal: selected streets · plum dots: residential points · dashed edge: loaded coverage</p>}
  </div>;
}
