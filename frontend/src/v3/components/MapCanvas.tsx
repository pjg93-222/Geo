// MapLibre canvas for the map screens (IMPLEMENTATION_PLAN 7.3). Land is a flat background; area fills are opaque and
// colored by a per-feature bin looked up in a literal array, so an index switch only rewrites one paint expression
// (no network, no re-tiling). Two fill layers cross-fade over 150 ms, so the map is never blank. Withheld areas get a
// diagonal hatch. Roads are white lines over the fills; household markers appear at street zoom.
import {forwardRef,useEffect,useImperativeHandle,useRef,useState} from 'react';
import * as maplibregl from 'maplibre-gl';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import 'maplibre-gl/dist/maplibre-gl.css';
import type {AreaGeometry,StreetPoint} from '../types';
import {getRoads} from '../api/client';
import {uPx} from '../lib/units';
import {BIN_PENDING,BIN_WITHHELD} from '../lib/areas';
import {mark} from '../lib/timing';

maplibregl.setWorkerUrl(workerUrl);

export const STREET_ZOOM=15;
const HATCH_BASE='#FAF8FC';
const SKELETON='#EDE7ED';

export interface MapView{zoom:number;bounds:[number,number,number,number];center:[number,number]}
export interface MapHandle{
  project(lon:number,lat:number):{x:number;y:number}|null;
  zoomBy(delta:number):void;
  view():MapView|null;
}
export interface HouseholdSel{id:string;lon:number;lat:number;bin:number}
export interface MapCanvasProps{
  geometry:AreaGeometry|null;
  bins:Uint8Array|null;palette:string[];
  selectedId:string|null;
  focus:{bounds:[number,number,number,number];nonce:number}|null;
  initialBounds:[number,number,number,number]|null;
  district:GeoJSON.FeatureCollection|null;
  roadBounds:[number,number,number,number]|null;
  households:StreetPoint[]|null;selectedHousehold:HouseholdSel|null;
  onSelectArea:(id:string|null)=>void;
  onSelectHousehold:(h:HouseholdSel)=>void;
  onView:(v:MapView)=>void;
  onMove:()=>void;
  onIdle:()=>void;
}

function colorExpr(arr:number[],palette:string[]):maplibregl.ExpressionSpecification{
  return ['match',['at',['get','i'],['literal',arr]],
    ...palette.flatMap((c,i)=>[i,c]),BIN_WITHHELD,HATCH_BASE,BIN_PENDING,SKELETON,SKELETON] as unknown as maplibregl.ExpressionSpecification;
}
function hatchOpacityExpr(arr:number[]):maplibregl.ExpressionSpecification{
  return ['case',['==',['at',['get','i'],['literal',arr]],BIN_WITHHELD],1,0] as unknown as maplibregl.ExpressionSpecification;
}
function hatchImage():{width:number;height:number;data:Uint8Array}{
  // 45 degree stripes, period 12 device px (6 css px at pixelRatio 2), stripe 6: the 11u period / 5u stripe of the render
  const size=12;const data=new Uint8Array(size*size*4);
  const [r,g,b]=[0xC8,0xB8,0xE6];
  for(let y=0;y<size;y++)for(let x=0;x<size;x++){
    const on=((x+y)%size)<5.4;const o=(y*size+x)*4;
    data[o]=r;data[o+1]=g;data[o+2]=b;data[o+3]=on?255:0;
  }
  return {width:size,height:size,data};
}
const empty:GeoJSON.FeatureCollection={type:'FeatureCollection',features:[]};

export const MapCanvas=forwardRef<MapHandle,MapCanvasProps>(function MapCanvas(props,ref){
  const holder=useRef<HTMLDivElement>(null);
  const mapRef=useRef<maplibregl.Map|null>(null);
  const cur=useRef(props);cur.current=props;
  const [ready,setReady]=useState(false);
  const front=useRef<'a'|'b'>('a');
  const armed=useRef(true);
  const lastGeom=useRef<AreaGeometry|null>(null);
  const fadeTimer=useRef<ReturnType<typeof setTimeout>|null>(null);
  const roadsBox=useRef<[number,number,number,number]|null>(null);
  const roadsAbort=useRef<AbortController|null>(null);

  useImperativeHandle(ref,()=>({
    project(lon,lat){const m=mapRef.current;if(!m)return null;const p=m.project([lon,lat]);return {x:p.x,y:p.y};},
    zoomBy(delta){const m=mapRef.current;if(m)m.easeTo({zoom:m.getZoom()+delta,duration:220});},
    view(){const m=mapRef.current;if(!m)return null;return viewOf(m);},
  }),[]);

  function viewOf(m:maplibregl.Map):MapView{
    const b=m.getBounds();const c=m.getCenter();
    return {zoom:m.getZoom(),bounds:[b.getWest(),b.getSouth(),b.getEast(),b.getNorth()],center:[c.lng,c.lat]};
  }

  // ---- create ------------------------------------------------------------------------------------------------------
  useEffect(()=>{
    if(!holder.current)return;
    const u=uPx();
    const m=new maplibregl.Map({
      container:holder.current,
      style:{version:8,sources:{},layers:[{id:'land',type:'background',paint:{'background-color':'#EEE9DF'}}]},
      center:[-104.82,38.86],zoom:10,minZoom:7,maxZoom:19,attributionControl:false,
      fadeDuration:0,dragRotate:false,pitchWithRotate:false,
    });
    mapRef.current=m;
    (window as unknown as {__cuMap?:maplibregl.Map}).__cuMap=m;   // handle for the screenshot harness
    m.touchZoomRotate.disableRotation();
    // "zoom" may only be the input of a top-level interpolate, so the casing's extra width is added to every stop
    const road=(base:number,extra=0)=>['interpolate',['exponential',2],['zoom'],10,base+extra,12,base*1.5+extra,14,base*3+extra,16,base*7+extra,18,base*22+extra] as unknown as maplibregl.ExpressionSpecification;
    const casing=(base:number)=>road(base,3*u);
    m.on('load',()=>{
      m.addImage('hatch',hatchImage(),{pixelRatio:2});
      m.addSource('areas',{type:'geojson',data:empty,tolerance:0.2,buffer:16});
      m.addSource('district',{type:'geojson',data:empty});
      m.addSource('roads',{type:'geojson',data:empty,tolerance:0.3});
      m.addSource('hh',{type:'geojson',data:empty});
      m.addSource('hh-sel',{type:'geojson',data:empty});
      const idle=colorExpr([],['#E6F1EC','#C2E0DB','#8BC2BC','#54979B','#286D73']);
      for(const id of ['a','b'] as const){
        m.addLayer({id:'fill-'+id,type:'fill',source:'areas',maxzoom:STREET_ZOOM,
          paint:{'fill-color':idle,'fill-opacity':id==='a'?1:0,'fill-antialias':false,'fill-opacity-transition':{duration:0,delay:0}}});
      }
      m.addLayer({id:'hatch',type:'fill',source:'areas',maxzoom:STREET_ZOOM,paint:{'fill-pattern':'hatch','fill-opacity':hatchOpacityExpr([]),'fill-antialias':false}});
      const kinds:[string,number][]=[['S1400',1.5*u*1.15],['S1200',3*u],['S1100',4*u]];
      for(const [kind,base] of kinds){
        m.addLayer({id:'casing-'+kind,type:'line',source:'roads',minzoom:14,filter:['==',['get','kind'],kind],layout:{'line-cap':'round','line-join':'round'},
          paint:{'line-color':'#E2DCD3','line-width':casing(base),'line-opacity':['interpolate',['linear'],['zoom'],14,0,15.5,1] as unknown as number}});
      }
      for(const [kind,base] of kinds){
        m.addLayer({id:'road-'+kind,type:'line',source:'roads',filter:['==',['get','kind'],kind],layout:{'line-cap':'round','line-join':'round'},
          paint:{'line-color':'#FFFFFF','line-width':road(base)}});
      }
      m.addLayer({id:'areas-line',type:'line',source:'areas',maxzoom:STREET_ZOOM,
        paint:{'line-color':'#FFFFFF','line-opacity':0.92,'line-width':['interpolate',['linear'],['zoom'],8,0.15,11,0.45,13,0.9,15,1.5*u*1.4] as unknown as number}});
      m.addLayer({id:'district-line',type:'line',source:'district',layout:{'line-cap':'round','line-join':'round'},
        paint:{'line-color':'#423376','line-width':4*u,'line-dasharray':[1.9,2.6]}});
      const none:maplibregl.FilterSpecification=['==',['get','id'],''];
      m.addLayer({id:'sel-glow',type:'line',source:'areas',maxzoom:STREET_ZOOM,filter:none,paint:{'line-color':'#EFB154','line-width':9*u,'line-blur':3*u,'line-opacity':0.35}});
      m.addLayer({id:'sel-ring',type:'line',source:'areas',maxzoom:STREET_ZOOM,filter:none,layout:{'line-join':'round'},paint:{'line-color':'#EFB154','line-width':(2.5+2*4)*u*1.0}});
      m.addLayer({id:'sel-line',type:'line',source:'areas',maxzoom:STREET_ZOOM,filter:none,layout:{'line-join':'round'},paint:{'line-color':'#2A2456','line-width':2.5*u}});
      const binColor=['match',['get','bin'],0,'#E6F1EC',1,'#C2E0DB',2,'#8BC2BC',3,'#54979B',4,'#286D73','#E6F1EC'] as unknown as maplibregl.ExpressionSpecification;
      m.addLayer({id:'hh',type:'circle',source:'hh',minzoom:STREET_ZOOM-0.2,
        paint:{'circle-radius':9*u*1.0,'circle-color':binColor,'circle-stroke-color':'#FFFFFF','circle-stroke-width':3*u,'circle-pitch-alignment':'map'}});
      m.addLayer({id:'hh-sel-gold',type:'circle',source:'hh-sel',paint:{'circle-radius':20*u,'circle-color':'#EFB154'}});
      m.addLayer({id:'hh-sel-white',type:'circle',source:'hh-sel',paint:{'circle-radius':16*u,'circle-color':'#FFFFFF'}});
      m.addLayer({id:'hh-sel-fill',type:'circle',source:'hh-sel',paint:{'circle-radius':14*u,'circle-color':binColor}});

      m.on('click',e=>{
        const p=cur.current;
        const hh=m.getZoom()>=STREET_ZOOM-0.2?m.queryRenderedFeatures(e.point,{layers:['hh']}):[];
        if(hh.length){
          const f=hh[0];const c=(f.geometry as GeoJSON.Point).coordinates;
          p.onSelectHousehold({id:String(f.properties?.id),lon:c[0],lat:c[1],bin:Number(f.properties?.bin)});return;
        }
        if(m.getZoom()>=STREET_ZOOM){p.onSelectArea(null);return;}
        const fs=m.queryRenderedFeatures(e.point,{layers:['fill-a','fill-b']});
        p.onSelectArea(fs.length?String(fs[0].properties?.id):null);
      });
      m.on('mousemove',e=>{
        const over=m.getZoom()>=STREET_ZOOM-0.2
          ?m.queryRenderedFeatures(e.point,{layers:['hh']}).length>0
          :m.queryRenderedFeatures(e.point,{layers:['fill-a','fill-b']}).length>0;
        m.getCanvas().style.cursor=over?'pointer':'';
      });
      setReady(true);
    });
    let moveFrame=0;
    m.on('move',()=>{if(moveFrame)return;moveFrame=requestAnimationFrame(()=>{moveFrame=0;cur.current.onMove();});});
    m.on('moveend',()=>cur.current.onView(viewOf(m)));
    m.on('idle',()=>{if(armed.current){armed.current=false;cur.current.onIdle();}});
    const ro=new ResizeObserver(()=>m.resize());ro.observe(holder.current);
    return ()=>{ro.disconnect();if(fadeTimer.current)clearTimeout(fadeTimer.current);m.remove();mapRef.current=null;};
  },[]);

  // ---- geometry ----------------------------------------------------------------------------------------------------
  useEffect(()=>{
    const m=mapRef.current;if(!m||!ready||!props.geometry)return;
    armed.current=true;
    (m.getSource('areas') as maplibregl.GeoJSONSource).setData(props.geometry as GeoJSON.FeatureCollection);
    mark('geometry-set');
  },[ready,props.geometry]);

  // ---- colors (cross-fade) -------------------------------------------------------------------------------------------
  const painted=useRef(false);
  useEffect(()=>{
    const m=mapRef.current;if(!m||!ready)return;
    armed.current=true;
    const bothLayers=['a','b'] as const;
    if(!props.bins){
      // cold load: the shapes draw at once in the skeleton color
      for(const id of bothLayers)m.setPaintProperty('fill-'+id,'fill-color',SKELETON);
      m.setPaintProperty('hatch','fill-opacity',0);
      return;
    }
    const arr=Array.from(props.bins);
    const expr=colorExpr(arr,props.palette);
    m.setPaintProperty('hatch','fill-opacity',hatchOpacityExpr(arr));
    const animate=painted.current&&lastGeom.current===props.geometry&&!!props.geometry;
    lastGeom.current=props.geometry;
    const topNow=front.current;
    if(!animate){
      if(fadeTimer.current){clearTimeout(fadeTimer.current);fadeTimer.current=null;}
      for(const id of bothLayers)m.setPaintProperty('fill-'+id,'fill-color',expr);
      m.setPaintProperty('fill-'+topNow,'fill-opacity',1);
      m.setPaintProperty('fill-'+(topNow==='a'?'b':'a'),'fill-opacity',0);
      painted.current=true;mark('colors-set');return;
    }
    if(fadeTimer.current){
      // a fade is still running: land on the newest colors at once
      clearTimeout(fadeTimer.current);fadeTimer.current=null;
      for(const id of bothLayers)m.setPaintProperty('fill-'+id,'fill-color',expr);
      return;
    }
    const back=topNow==='a'?'b':'a';
    m.setPaintProperty('fill-'+back,'fill-color',expr);
    m.moveLayer('fill-'+back,'hatch');
    m.setPaintProperty('fill-'+back,'fill-opacity-transition',{duration:150,delay:0});
    m.setPaintProperty('fill-'+back,'fill-opacity',1);
    mark('colors-set');
    fadeTimer.current=setTimeout(()=>{
      fadeTimer.current=null;
      m.setPaintProperty('fill-'+topNow,'fill-opacity-transition',{duration:0,delay:0});
      m.setPaintProperty('fill-'+topNow,'fill-opacity',0);
      m.setPaintProperty('fill-'+back,'fill-opacity-transition',{duration:0,delay:0});
      front.current=back;
    },190);
  },[ready,props.bins,props.palette]);

  // ---- selection -----------------------------------------------------------------------------------------------------
  useEffect(()=>{
    const m=mapRef.current;if(!m||!ready)return;
    armed.current=true;
    const f:maplibregl.FilterSpecification=['==',['get','id'],props.selectedId??''];
    for(const id of ['sel-glow','sel-ring','sel-line'])m.setFilter(id,f);
  },[ready,props.selectedId]);

  // ---- district, camera ------------------------------------------------------------------------------------------------
  useEffect(()=>{
    const m=mapRef.current;if(!m||!ready||!props.district)return;
    (m.getSource('district') as maplibregl.GeoJSONSource).setData(props.district);
  },[ready,props.district]);
  const didInit=useRef(false);
  useEffect(()=>{
    const m=mapRef.current;if(!m||!ready||didInit.current||!props.initialBounds)return;
    didInit.current=true;
    m.fitBounds(props.initialBounds,{padding:{top:44,bottom:44,left:44,right:44},duration:0});
  },[ready,props.initialBounds]);
  useEffect(()=>{
    const m=mapRef.current;if(!m||!ready||!props.focus)return;
    m.fitBounds(props.focus.bounds,{padding:{top:120,bottom:120,left:120,right:120},duration:450,maxZoom:14.7});
  },[ready,props.focus?.nonce]);

  // ---- roads ----------------------------------------------------------------------------------------------------------
  const roadsKind=useRef<'none'|'overview'|'detail'>('none');
  useEffect(()=>{
    const m=mapRef.current;if(!m||!ready)return;
    const loadFor=async()=>{
      const v=viewOf(m);
      let want:[number,number,number,number]|null=null;
      if(v.zoom<12){
        if(roadsKind.current==='overview'||!cur.current.roadBounds)return;
        want=cur.current.roadBounds;
      }else{
        const [w,s,e,n]=v.bounds;const have=roadsBox.current;
        if(roadsKind.current==='detail'&&have&&have[0]<=w&&have[1]<=s&&have[2]>=e&&have[3]>=n&&(have[2]-have[0])<(e-w)*4.5)return;
        const dx=(e-w)*0.35,dy=(n-s)*0.35;want=[w-dx,s-dy,e+dx,n+dy];
      }
      roadsAbort.current?.abort();roadsAbort.current=new AbortController();
      try{
        const fc=await getRoads(want,v.zoom<12?10:Math.max(12,v.zoom),roadsAbort.current.signal);
        (m.getSource('roads') as maplibregl.GeoJSONSource).setData(fc);
        roadsBox.current=want;roadsKind.current=v.zoom<12?'overview':'detail';
      }catch{/* roads are decoration; a failure leaves the previous set on screen */}
    };
    let t:ReturnType<typeof setTimeout>|null=null;
    const schedule=()=>{if(t)clearTimeout(t);t=setTimeout(loadFor,220);};
    m.on('moveend',schedule);schedule();
    return ()=>{if(t)clearTimeout(t);m.off('moveend',schedule);roadsAbort.current?.abort();};
  },[ready,props.roadBounds]);

  // ---- households -------------------------------------------------------------------------------------------------------
  useEffect(()=>{
    const m=mapRef.current;if(!m||!ready)return;
    const fc:GeoJSON.FeatureCollection={type:'FeatureCollection',features:(props.households??[]).map(h=>({
      type:'Feature',properties:{id:h[0],bin:h[4],n:h[3]},geometry:{type:'Point',coordinates:[h[1],h[2]]}}))};
    (m.getSource('hh') as maplibregl.GeoJSONSource).setData(fc);
  },[ready,props.households]);
  useEffect(()=>{
    const m=mapRef.current;if(!m||!ready)return;
    const s=props.selectedHousehold;
    (m.getSource('hh-sel') as maplibregl.GeoJSONSource).setData(s?{type:'FeatureCollection',features:[{type:'Feature',properties:{id:s.id,bin:s.bin},geometry:{type:'Point',coordinates:[s.lon,s.lat]}}]}:empty);
  },[ready,props.selectedHousehold]);

  return <div ref={holder} className="map-canvas" aria-label="Map of areas and households"/>;
});
