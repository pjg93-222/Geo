// Map screen: renders 01 (Block overview), 04 (Precinct / Tract / ZIP detail) and 05 (street-level households).
import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {MapHeader} from './Header';
import {LoadingTerrier} from './TerrierMark';
import {MapCanvas,STREET_ZOOM,type HouseholdSel,type MapHandle,type MapView} from './MapCanvas';
import {AreaRail,type Neighbour} from './AreaRail';
import {AreaLabels,HouseholdLegend,Legend,Popover,Readout,StreetRail,ZoomControl,type MapLabel} from './MapParts';
import type {Group,HouseholdPopover,Meta,StreetPayload} from '../types';
import {BIN_PENDING,areaView,binsFor,districtSummary,type Layer} from '../lib/areas';
import {binsInGeometryOrder,featureBounds,useLevelData} from '../lib/useLevelData';
import {LEVELS,precinctShort,tractShort,zipLabel,type Level} from '../lib/format';
import {replaceQuery,type Route} from '../lib/router';
import {begin,end} from '../lib/timing';
import {getDistrict,getHousehold,getStreet} from '../api/client';

const SHARE_RAMP=['#E6F1EC','#C2E0DB','#8BC2BC','#54979B','#286D73'];
const LEAN_RAMP=['#95ADCE','#E7A490','#C6C2D3','#F5F1E9','#E5DFDA'];
const GROUPS:Group[]=['ovr','cua','part','soi','lean'];

function readGroup(q:URLSearchParams):Group{const v=q.get('index');return GROUPS.includes(v as Group)?(v as Group):'ovr';}
function readLevel(q:URLSearchParams):Level{const v=q.get('level');return LEVELS.includes(v as Level)?(v as Level):'block';}

export function MapScreen({meta,route}:{meta:Meta|null;route:Route}){
  const [group,setGroup]=useState<Group>(()=>readGroup(route.search));
  const [level,setLevel]=useState<Level>(()=>readLevel(route.search));
  const [selected,setSelected]=useState<string|null>(()=>route.search.get('sel'));
  const [measured,setMeasured]=useState(false);
  const [view,setView]=useState<MapView|null>(null);
  const [focus,setFocus]=useState<{bounds:[number,number,number,number];nonce:number}|null>(null);
  const [district,setDistrict]=useState<GeoJSON.FeatureCollection|null>(null);
  const [street,setStreet]=useState<StreetPayload|null>(null);
  const [selHousehold,setSelHousehold]=useState<HouseholdSel|null>(null);
  const [pop,setPop]=useState<HouseholdPopover|null>(null);
  const [moveTick,setMoveTick]=useState(0);
  const mapRef=useRef<MapHandle>(null);
  const paneRef=useRef<HTMLDivElement>(null);
  const dataReady=useRef(false);
  const householdRequest=useRef(0);
  const pendingFocus=useRef<{id:string;nonce:number}|null>(null);

  const {state,loadingLevel,error,retry}=useLevelData(meta,level);
  const areas=state?.areas??null;
  const geo=state?.geo??null;
  const loadedLevel=state?.level??level;
  dataReady.current=!!areas&&!loadingLevel;

  // ---- URL <-> state ------------------------------------------------------------------------------------------------
  useEffect(()=>{
    replaceQuery({index:group==='ovr'?null:group,level:level==='block'?null:level,sel:selected});
  },[group,level,selected]);
  // Only navigation changes the route snapshot; replaceQuery does not emit it.
  // Restore state on Back/Forward as well as searches without undoing local controls.
  useEffect(()=>{
    const fKey=route.search.get('f');
    const l=readLevel(route.search);const sel=route.search.get('sel');
    const g=fKey&&!route.search.has('index')?group:readGroup(route.search);
    setGroup(g);
    setLevel(l);setSelected(sel);setSelHousehold(null);setPop(null);begin('map');
    householdRequest.current++;
    pendingFocus.current=sel?{id:sel,nonce:Number(fKey)||Date.now()}:null;
    if(!sel&&meta)setFocus({bounds:meta.bbox,nonce:Date.now()});
    // Normalize even when a repeated search leaves every state value unchanged.
    replaceQuery({f:null,index:g==='ovr'?null:g,level:l==='block'?null:l,sel});
  },[route]);                                                           // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(()=>{getDistrict().then(setDistrict).catch(()=>{});},[]);

  // ---- derived map data --------------------------------------------------------------------------------------------------
  const layer:Layer=measured?'measured':'share';
  const palette=!measured&&group==='lean'?LEAN_RAMP:SHARE_RAMP;
  const bins=useMemo(()=>{
    if(!areas||!geo||!meta)return null;
    return binsInGeometryOrder(binsFor(areas,group,layer,meta),areas,geo,BIN_PENDING);
  },[areas,geo,group,layer,meta]);

  const idIndex=useMemo(()=>new Map((areas?.ids??[]).map((id,i)=>[id,i])),[areas]);
  const selIndex=selected?idIndex.get(selected):undefined;
  const areaV=useMemo(()=>areas&&selIndex!==undefined?areaView(areas,selIndex,group):null,[areas,selIndex,group]);
  // the district summary is the score store's own means when the server sends them (they include withheld areas);
  // otherwise it is the people-weighted mean of the published areas
  const summary=useMemo(()=>{
    const d=meta?.district_mean;
    if(d)return {ovr:d.score.ovr,cua:d.score.cua,part:d.score.part,soi:d.score.soi,lean:d.lean,measured:d.measured.ovr,measuredBy:d.measured,people:meta!.people,mix:meta!.district_registration};
    return areas?districtSummary(areas):null;
  },[meta,areas]);

  // the first camera is the whole district (meta.bbox), known before any values arrive, so the cold skeleton already
  // shows the district and the view never jumps when the values land
  const initialBounds=useMemo<[number,number,number,number]|null>(()=>meta?meta.bbox:null,[meta]);
  const firstBounds=useRef<[number,number,number,number]|null>(null);
  if(!firstBounds.current&&initialBounds)firstBounds.current=initialBounds;
  const roadBounds=useMemo<[number,number,number,number]|null>(()=>{
    const b=firstBounds.current;if(!b)return null;
    const dx=(b[2]-b[0])*0.4,dy=(b[3]-b[1])*0.4;return [b[0]-dx,b[1]-dy,b[2]+dx,b[3]+dy];
  },[initialBounds]);

  // focus requests (search) once the right level is on screen
  useEffect(()=>{
    const f=pendingFocus.current;if(!f||!geo||loadedLevel!==level)return;
    const feat=geo.fc.features.find(x=>x.properties.id===f.id);if(!feat)return;
    pendingFocus.current=null;
    setFocus({bounds:featureBounds(feat),nonce:f.nonce});
  },[geo,loadedLevel,level]);

  // neighboring areas for rail variant B: the selected area plus the 6 nearest published by centroid, in code order
  const neighbors=useMemo<Neighbour[]|null>(()=>{
    if(!areas||!geo||!selected||areaV?.withheld||level==='block'||loadedLevel!==level||selIndex===undefined)return null;
    const cand:{id:string;x:number;y:number;v:number|null}[]=[];
    const cosLat=Math.cos(38.8*Math.PI/180);
    for(const f of geo.fc.features){
      const i=idIndex.get(f.properties.id);if(i===undefined||areas.status[i]!=='ok')continue;
      cand.push({id:f.properties.id,x:f.properties.lx*cosLat,y:f.properties.ly,v:areas.groups.ovr.mean[i]});
    }
    const me=cand.find(c=>c.id===selected);if(!me)return null;
    const near=cand.filter(c=>c.id!==selected).sort((a,b)=>Math.hypot(a.x-me.x,a.y-me.y)-Math.hypot(b.x-me.x,b.y-me.y)).slice(0,6);
    const short=(id:string)=>level==='precinct'?precinctShort(id):level==='tract'?tractShort(id):zipLabel(id).replace('ZIP ','');
    return [me,...near].sort((a,b)=>a.id<b.id?-1:1).map(c=>({id:c.id,short:short(c.id),value:c.v,selected:c.id===selected}));
  },[areas,geo,selected,areaV,level,loadedLevel,selIndex,idIndex]);

  // precinct ids on the map (render 04); tract and ZIP levels draw no labels
  const labels=useMemo<MapLabel[]>(()=>{
    if(!geo||!areas||loadedLevel!=='precinct'||level!=='precinct')return [];
    return geo.fc.features.map(f=>{
      const b=featureBounds(f);
      const k=f.properties.i??0;
      return {id:f.properties.id,text:precinctShort(f.properties.id),lon:f.properties.lx,lat:f.properties.ly,widthDeg:b[2]-b[0],bin:bins?bins[k]:BIN_PENDING};
    });
  },[geo,areas,loadedLevel,level,bins]);

  // ---- street level ---------------------------------------------------------------------------------------------------
  const streetActive=!!view&&view.zoom>=STREET_ZOOM;
  useEffect(()=>{
    if(!view||view.zoom<STREET_ZOOM){setStreet(null);setSelHousehold(null);setPop(null);return;}
    const ac=new AbortController();
    const t=setTimeout(()=>{
      getStreet(view.bounds,view.zoom,ac.signal).then(setStreet).catch(()=>{});
    },120);
    return ()=>{clearTimeout(t);ac.abort();};
  },[view]);

  const onSelectHousehold=useCallback((h:HouseholdSel)=>{
    begin('map');setSelHousehold(h);setPop(null);
    const request=++householdRequest.current;
    getHousehold(h.id).then(p=>{if(request===householdRequest.current)setPop(p);}).catch(()=>{if(request===householdRequest.current)setPop(null);});
  },[]);

  // ---- handlers ---------------------------------------------------------------------------------------------------------
  const onGroup=(g:Group)=>{if(g===group)return;begin('map');setGroup(g);};
  const onLevel=(l:Level)=>{if(l===level)return;begin('map');setLevel(l);setSelected(null);setSelHousehold(null);setPop(null);};
  const onSelectArea=useCallback((id:string|null)=>{begin('map');setSelected(id);setSelHousehold(null);setPop(null);},[]);
  const onIdle=useCallback(()=>{if(dataReady.current)end('map');},[]);
  const onView=useCallback((v:MapView)=>setView(v),[]);
  const onMove=useCallback(()=>setMoveTick(t=>t+1),[]);

  const popPos=useMemo(()=>{
    void moveTick;
    if(!selHousehold)return null;
    return mapRef.current?.project(selHousehold.lon,selHousehold.lat)??null;
  },[selHousehold,moveTick]);
  void moveTick;
  const pane=paneRef.current;

  return <div className="screen map-screen" data-street={streetActive?'1':'0'}>
    <MapHeader meta={meta} busy={!error&&(!areas||!!loadingLevel)} index={group} level={level} onIndex={onGroup} onLevel={onLevel}/>
    <div className="map-body">
      <div className="map-pane" ref={paneRef}>
        {!areas&&!error&&<LoadingTerrier label="Loading map…"/>}
        <MapCanvas ref={mapRef}
          geometry={geo?geo.fc:null} bins={bins} palette={palette}
          selectedId={selected} focus={focus} initialBounds={firstBounds.current} district={district} roadBounds={roadBounds}
          households={street?.points??null} selectedHousehold={selHousehold}
          onSelectArea={onSelectArea} onSelectHousehold={onSelectHousehold} onView={onView} onMove={onMove} onIdle={onIdle}/>
        {labels.length>0&&pane&&!streetActive&&<AreaLabels items={labels} palette={palette} selectedId={selected} project={(lo,la)=>mapRef.current?.project(lo,la)??null}
          zoom={mapRef.current?.view()?.zoom??0} width={pane.clientWidth} height={pane.clientHeight}/>}
        <ZoomControl onZoom={d=>mapRef.current?.zoomBy(d)}/>
        {streetActive?<HouseholdLegend/>:<Legend group={group} measured={measured} onMeasured={v=>{begin('map');setMeasured(v);}} loading={!areas} floors={meta?.floors}/>}
        <Readout/>
        {selHousehold&&popPos&&pane&&<Popover data={pop} x={popPos.x} y={popPos.y} paneW={pane.clientWidth} paneH={pane.clientHeight}
          onClose={()=>{setSelHousehold(null);setPop(null);}}/>}
        {error&&<div className="map-error" role="alert"><span>{error}</span> <button type="button" onClick={retry}>Retry</button></div>}
      </div>
      {streetActive
        ?<StreetRail stats={street?.stats??null} truncated={street?.truncated}/>
        :<AreaRail meta={meta} level={loadedLevel} group={group} view={areaV} loading={!areas} summary={summary} neighbors={neighbors}/>}
    </div>
  </div>;
}
