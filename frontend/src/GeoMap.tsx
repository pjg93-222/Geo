import {useEffect,useRef,useState} from 'react';
import * as maplibregl from 'maplibre-gl';
import {LocateFixed} from 'lucide-react';
import type {Row,Config,Snapshot} from './main';
import {StreetOverlay,HouseholdDetail,StreetInfo} from './StreetOverlay';
import type {HouseholdIndex} from './householdIndex';
import './map-detail.css';

type Props={config:Config;rows:Row[];snapshot:Snapshot;selected:string|null;cuts:number[];
  palette:string[];onNavigate:(zoom:number)=>void;
  preloaded?:HouseholdIndex;
  localStreet:boolean;household:HouseholdDetail|null;onSelect:(id:string)=>void;
  onMove:(center:[number,number],zoom:number)=>void;onError:(error:string)=>void;
  onHousehold:(id:string)=>void;onStreetInfo:(info:StreetInfo)=>void;onPerson:(id:string)=>void};
const empty={type:'FeatureCollection',features:[]} as GeoJSON.FeatureCollection;
const colors=['#dcecea','#b1d4cd','#7ab2a9','#468d83','#236359'];
async function get(path:string,signal:AbortSignal){
  const response=await fetch('/api/'+path,{credentials:'same-origin',signal});
  const data=await response.json();if(!response.ok)throw new Error(data.error||'Map detail could not be loaded.');return data;
}

export function GeoMap(props:Props){
  const {config,rows,snapshot,selected,cuts,palette,localStreet,household,onHousehold,onStreetInfo,onPerson}=props;
  const holder=useRef<HTMLDivElement>(null);const map=useRef<maplibregl.Map|null>(null);
  const current=useRef(props);current.current=props;
  const rawGeometry=useRef<GeoJSON.FeatureCollection>(empty);
  const geometryCache=useRef(new Map<string,GeoJSON.FeatureCollection>());
  const labels=useRef<maplibregl.Marker[]>([]);
  const [ready,setReady]=useState(false);const[viewport,setViewport]=useState(0);
  const [loading,setLoading]=useState(true);const[hasDetail,setHasDetail]=useState(false);
  const [mapError,setMapError]=useState('');const[retry,setRetry]=useState(0);
  const streetActive=localStreet&&config.zoom>=15;
  function paint(){
    const m=map.current;if(!m?.getSource('areas'))return;
    const data=current.current;const byId=new Map(data.rows.map(row=>[row.id,row]));
    const geo={...rawGeometry.current,features:rawGeometry.current.features.map(feature=>{
      const row=byId.get(String(feature.properties?.id));return {...feature,properties:{...feature.properties,
        value:row?(data.config.scale==='percentile'?row.index:row.value):null,
        status:row?.measure_status||row?.status||'unavailable',partial:row?.partial??true}};
    })};
    (m.getSource('areas') as maplibregl.GeoJSONSource).setData(geo);
  }
  useEffect(()=>{
    if(!holder.current)return;let timer:ReturnType<typeof setTimeout>|undefined;
    try{
      const m=new maplibregl.Map({container:holder.current,style:{version:8,sources:{},layers:[{id:'background',type:'background',paint:{'background-color':'#f4f1eb'}}]},
        center:config.center,zoom:config.zoom,minZoom:7,maxZoom:20,attributionControl:false});map.current=m;
      m.addControl(new maplibregl.NavigationControl({showCompass:false}),'top-right');
      m.addControl(new maplibregl.ScaleControl({unit:'imperial'}),'bottom-left');
      m.on('load',()=>{
        for(const id of ['areas','district','streets','roads'])m.addSource(id,{type:'geojson',data:empty});
        m.addLayer({id:'areas-fill',type:'fill',source:'areas',paint:{'fill-color':colors[0],'fill-opacity':.8}});
        m.addLayer({id:'areas-line',type:'line',source:'areas',paint:{'line-color':'#678780','line-width':.7}});
        m.addLayer({id:'partial-line',type:'line',source:'areas',filter:['==',['get','partial'],true],paint:{'line-color':'#9b6713','line-width':1.2,'line-dasharray':[2,2]}});
        m.addLayer({id:'street-line',type:'line',source:'streets',paint:{'line-color':'#736574','line-width':.65,'line-opacity':.5}});
        m.addLayer({id:'road-casing',type:'line',source:'roads',paint:{'line-color':'#a79c8a','line-width':['interpolate',['linear'],['zoom'],9,1,14,3,18,9],'line-opacity':.7}});
        m.addLayer({id:'road-line',type:'line',source:'roads',paint:{'line-color':['case',['==',['get','kind'],'S1100'],'#f7d28b','#fffdf6'],'line-width':['interpolate',['linear'],['zoom'],9,.5,14,1.8,18,7]}});
        m.addLayer({id:'district-line',type:'line',source:'district',paint:{'line-color':'#57447b','line-width':2,'line-dasharray':[4,2]}});
        m.addLayer({id:'selection-line',type:'line',source:'areas',filter:['==',['get','id'],''],paint:{'line-color':'#b77700','line-width':3}});
        m.on('click','areas-fill',event=>{if(m.getZoom()>=15&&current.current.localStreet)return;const id=event.features?.[0]?.properties?.id;
          if(id){current.current.onSelect(String(id));current.current.onMove([event.lngLat.lng,event.lngLat.lat],m.getZoom());}});
        m.on('mouseenter','areas-fill',()=>{m.getCanvas().style.cursor='pointer';});
        m.on('mouseleave','areas-fill',()=>{m.getCanvas().style.cursor='';});setReady(true);
      });
      m.on('moveend',()=>{clearTimeout(timer);timer=setTimeout(()=>{
        setViewport(value=>value+1);current.current.onMove([m.getCenter().lng,m.getCenter().lat],m.getZoom());
      },180);});
      m.on('error',()=>setMapError('Some map detail could not load. Retry or use Table view.'));
      const observer=new ResizeObserver(()=>m.resize());observer.observe(holder.current);
      return ()=>{clearTimeout(timer);observer.disconnect();labels.current.forEach(label=>label.remove());m.remove();map.current=null;};
    }catch{setMapError('This browser cannot display the map. Use Table view to explore the same areas.');setLoading(false);}
  },[]);
  useEffect(()=>{
    const m=map.current;if(!m||!ready)return;
    if(streetActive){setLoading(false);return;}
    const controller=new AbortController();const b=m.getBounds();const dx=(b.getEast()-b.getWest())*.15,dy=(b.getNorth()-b.getSouth())*.15;
    const bbox=[b.getWest()-dx,b.getSouth()-dy,b.getEast()+dx,b.getNorth()+dy].map(value=>value.toFixed(4)).join(',');
    const zoom=m.getZoom()<11?'10':m.getZoom()<14?'12':'15';
    const query=new URLSearchParams({snapshot:snapshot.key,level:config.level,bbox,zoom});const key=query.toString();
    setLoading(true);setMapError('');
    const cached=geometryCache.current.get(key);
    (cached?Promise.resolve(cached):get('geometry?'+key,controller.signal)).then(geo=>{
      if(controller.signal.aborted)return;
      if(geometryCache.current.size>=12)geometryCache.current.delete(geometryCache.current.keys().next().value!);
      geometryCache.current.set(key,geo);rawGeometry.current=geo;paint();setHasDetail(true);setLoading(false);
    }).catch(error=>{if(error.name!=='AbortError'){setLoading(false);setMapError(error.message);}});
    return ()=>controller.abort();
  },[ready,snapshot.key,config.level,viewport,retry,streetActive]);
  // Changing a value or color scale only repaints the existing geometry.
  useEffect(()=>{if(ready)paint();},[rows,config.metric,config.scale,ready]);
  useEffect(()=>{
    if(!ready||!map.current)return;const controller=new AbortController();
    get('geometry?'+new URLSearchParams({snapshot:snapshot.key,level:'district'}),controller.signal)
      .then(data=>{if(!controller.signal.aborted)(map.current?.getSource('district') as maplibregl.GeoJSONSource)?.setData(data);})
      .catch(error=>{if(error.name!=='AbortError')setMapError(error.message);});
    return ()=>controller.abort();
  },[ready,snapshot.key,retry]);
  useEffect(()=>{
    const m=map.current;if(!m||!ready)return;const controller=new AbortController();const b=m.getBounds();
    const bbox=[b.getWest(),b.getSouth(),b.getEast(),b.getNorth()].join(',');
    get('roads?'+new URLSearchParams({bbox,zoom:String(m.getZoom())}),controller.signal).then(data=>{
      if(controller.signal.aborted)return;(m.getSource('roads') as maplibregl.GeoJSONSource).setData(data);
      labels.current.forEach(label=>label.remove());labels.current=[];const names=new Set<string>();const occupied:{x:number;y:number}[]=[];
      for(const feature of data.features){
        const name=feature.properties.name;if(!name||names.has(name)||labels.current.length>=35)continue;
        const coords=feature.geometry.type==='LineString'?feature.geometry.coordinates:feature.geometry.coordinates[0];
        if(coords.length<2)continue;
        const a=m.project(coords[0]),z=m.project(coords[coords.length-1]);if(Math.hypot(a.x-z.x,a.y-z.y)<80)continue;
        const point=coords[Math.floor(coords.length/2)];const pixel=m.project(point);
        if(pixel.x<25||pixel.y<65||pixel.x>m.getContainer().clientWidth-70||pixel.y>m.getContainer().clientHeight-35||occupied.some(p=>Math.abs(p.x-pixel.x)<105&&Math.abs(p.y-pixel.y)<30))continue;
        const element=document.createElement('span');element.className='road-label';element.textContent=name;
        labels.current.push(new maplibregl.Marker({element}).setLngLat(point).addTo(m));names.add(name);occupied.push(pixel);
      }
    }).catch(error=>{if(error.name!=='AbortError')setMapError(error.message);});
    return ()=>controller.abort();
  },[ready,viewport,retry]);
  useEffect(()=>{
    const m=map.current;if(!m||!ready)return;const controller=new AbortController();
    m.setLayoutProperty('street-line','visibility',config.streets?'visible':'none');
    if(config.streets){const b=m.getBounds();get('geometry?'+new URLSearchParams({snapshot:snapshot.key,level:'streets',zoom:String(m.getZoom()),bbox:[b.getWest(),b.getSouth(),b.getEast(),b.getNorth()].join(',')}),controller.signal)
      .then(data=>{if(!controller.signal.aborted)(m.getSource('streets') as maplibregl.GeoJSONSource).setData(data);})
      .catch(error=>{if(error.name!=='AbortError')setMapError(error.message);});}
    m.moveLayer('street-line',config.streetOrder==='below'?'areas-fill':'road-casing');return ()=>controller.abort();
  },[ready,config.streets,config.streetOrder,snapshot.key,viewport,retry]);
  useEffect(()=>{
    const m=map.current;if(!m||!ready)return;const ramp:any=['step',['coalesce',['get','value'],0],palette[0]];cuts.forEach((value,index)=>ramp.push(value,palette[index+1]));
    m.setPaintProperty('areas-fill','fill-color',['case',['==',['get','status'],'suppressed'],'#d6cbd8',['==',['get','value'],null],'#e1dfda',['==',['get','value'],0],'#ffffff',ramp]);
    m.setPaintProperty('areas-fill','fill-opacity',streetActive?0:config.opacity);
    m.setLayoutProperty('areas-line','visibility',config.outline&&!streetActive?'visible':'none');
    m.setLayoutProperty('partial-line','visibility',streetActive?'none':'visible');
    m.setFilter('selection-line',['==',['get','id'],selected||'']);
  },[ready,cuts,palette,config.opacity,config.outline,selected,streetActive]);
  useEffect(()=>{const m=map.current;if(m&&ready&&(Math.abs(m.getCenter().lng-config.center[0])+Math.abs(m.getCenter().lat-config.center[1])>1e-7||Math.abs(m.getZoom()-config.zoom)>1e-4))m.jumpTo({center:config.center,zoom:config.zoom});},[ready,config.center[0],config.center[1],config.zoom]);
  return <div className="map-stage">
    <div ref={holder} className="map-canvas" aria-label="Interactive geographic heatmap and street records"/>
    <StreetOverlay map={map.current} active={ready&&streetActive} basis={config.basis} viewport={viewport} sourceHash={snapshot.manifest.source_sha256} household={household} preloaded={props.preloaded} onHousehold={onHousehold} onInfo={onStreetInfo} onPerson={onPerson}/>
    <div className="map-navigation" aria-label="Map zoom levels"><button onClick={()=>props.onNavigate(9.0)} aria-pressed={config.level==='region'}>Overview</button><button onClick={()=>props.onNavigate(10.0)} aria-pressed={config.level==='zcta'&&config.zoom<15}>ZIPs</button><button onClick={()=>props.onNavigate(12.5)} aria-pressed={config.level==='block'&&config.zoom<15}>Areas</button><button onClick={()=>props.onNavigate(16)} aria-pressed={config.zoom>=15}>Households</button></div>
    {loading&&<div className={hasDetail?'map-progress':'map-loading'} role="status">{hasDetail?'Updating detail…':'Loading map…'}</div>}
    {mapError&&<div className="map-detail-error" role="alert"><span>{mapError}</span><button onClick={()=>setRetry(value=>value+1)}>Retry</button></div>}
    <div className="map-caption">U.S. Census · TIGER/Line 2025 roads</div>
    <button className="fit-map" onClick={()=>{const b=snapshot.manifest.geography.bounds;map.current?.fitBounds([[b[0],b[1]],[b[2],b[3]]],{padding:38,duration:0});}}><LocateFixed size={16}/>Fit district</button>
  </div>;
}
