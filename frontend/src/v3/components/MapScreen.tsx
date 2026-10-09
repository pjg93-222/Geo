// Map screen: renders 01 (Block overview), 04 (Precinct / Tract / ZIP detail) and 05 (street-level households).
import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {MapHeader,useFeatures} from './Header';
import {LoadingTerrier} from './TerrierMark';
import {MapCanvas,STREET_ZOOM,type HouseholdSel,type MapHandle,type MapView} from './MapCanvas';
import {AreaRail,type Neighbour} from './AreaRail';
import {UntappedPanel,useUntapped,type PanelLevel} from './UntappedPanel';
import {AreaLabels,HouseholdLegend,Legend,LayersPanel,Popover,Readout,StreetRail,ZoomControl,type MapLabel} from './MapParts';
import type {AreaGeometry,Density,Group,HouseholdPopover,Meta,OverlayLayer,StreetPayload} from '../types';
import {BIN_PENDING,areaView,districtSummary,partyShares} from '../lib/areas';
import {SHARE_RAMP,districtDefault,industryChoices,isGroup,offeredLayers,overlayBins,overlayQuery,readOverlay,untappedBins,usable,type Overlay,type UntappedInput} from '../lib/overlay';
import {areaAt,clampWeights,defaultWeights,planWalkPath,readWeights,writeWeights,type UntappedWeights} from '../lib/untapped';
import {binsInGeometryOrder,featureBounds,useLevelData,useParties} from '../lib/useLevelData';
import {LEVELS,precinctShort,tractShort,zipLabel,type Level} from '../lib/format';
import {navigate,replaceQuery,type Route} from '../lib/router';
import {begin,end} from '../lib/timing';
import {getDensity,getDistrict,getGeometry,getHousehold,getStreet} from '../api/client';

function readLevel(q:URLSearchParams):Level{const v=q.get('level');return LEVELS.includes(v as Level)?(v as Level):'block';}

export function MapScreen({meta,route}:{meta:Meta|null;route:Route}){
  const [o,setO]=useState<Overlay>(()=>readOverlay(route.search));
  const [industry,setIndustry]=useState(()=>route.search.get('industry')??'');
  const [density,setDensity]=useState<Density|null>(null);
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
  const parties=useParties(meta,loadedLevel);
  dataReady.current=!!areas&&!loadingLevel;

  // ---- this district's layers (GET /api/v3/features, density once it is listed) -----------------------------------------------
  const features=useFeatures();
  useEffect(()=>{if(features?.overlay.density)getDensity().then(setDensity).catch(()=>{});},[features]);
  const layers=useMemo(()=>offeredLayers(features,!!density),[features,density]);
  const ov=useMemo(()=>usable(o,layers),[o,layers]);
  const single=ov.mode==='single';
  const industryId=features?.industries.some(i=>i.id===industry)?industry:features?.industry??'';
  const industryParam=industryChoices(features)&&industryId!==features?.industry?industryId:null;

  // ---- Untapped: the district opens on it; the sliders re-weigh the server's sums here --------------------------------------
  const dflt=districtDefault(layers);
  const followDefault=useRef(true);   // true while the layer was never chosen: the district's own default (it arrives with /features) applies
  useEffect(()=>{if(followDefault.current)setO(cur=>cur.layer===dflt?cur:{...cur,layer:dflt});},[dflt]);
  const defaults=useMemo(()=>defaultWeights(features?.roi),[features]);
  const saved=useMemo(()=>features?readWeights(features.district,defaults):defaults,[features,defaults]);
  const [custom,setCustom]=useState<UntappedWeights|null>(null);
  const weights=custom??saved;
  const onWeights=(next:UntappedWeights)=>{const w=clampWeights(next,defaults);setCustom(w);if(features)writeWeights(features.district,w);};
  const [panelLevel,setPanelLevel]=useState<PanelLevel>('precinct');
  const untappedOn=single&&ov.layer==='untapped';
  const canPlan=!!features?.roi;
  const planWalk=(precinct:string)=>navigate(planWalkPath(precinct));

  // ---- URL <-> state ------------------------------------------------------------------------------------------------
  const oq=(x:Overlay)=>{const q=overlayQuery(x,dflt);if(!features)delete q.index;return q;};
  useEffect(()=>{
    replaceQuery({...oq(o),level:level==='block'?null:level,sel:selected,industry:industryParam});
  },[o,level,selected,industryParam,dflt,features]);                    // eslint-disable-line react-hooks/exhaustive-deps
  // Only navigation changes the route snapshot; replaceQuery does not emit it.
  // Restore state on Back/Forward as well as searches without undoing local controls.
  useEffect(()=>{
    const fKey=route.search.get('f');
    const l=readLevel(route.search);const sel=route.search.get('sel');
    const keep=!!fKey&&!route.search.has('index')&&!route.search.has('mode');
    const next=keep?o:readOverlay(route.search,dflt);
    if(!keep)followDefault.current=!route.search.has('index');
    setO(next);if(!keep)setIndustry(route.search.get('industry')??'');
    setLevel(l);setSelected(sel);setSelHousehold(null);setPop(null);begin('map');
    householdRequest.current++;
    pendingFocus.current=sel?{id:sel,nonce:Number(fKey)||Date.now()}:null;
    if(!sel&&meta)setFocus({bounds:meta.bbox,nonce:Date.now()});
    // Normalize even when a repeated search leaves every state value unchanged.
    replaceQuery({f:null,...oq(next),level:l==='block'?null:l,sel});
  },[route]);                                                           // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(()=>{getDistrict().then(setDistrict).catch(()=>{});},[]);

  // ---- Untapped sums: the level on the map, and the panel's level when it is another one ------------------------------------
  const sumsMap=useUntapped(untappedOn?loadedLevel:null);
  const sumsOther=useUntapped(untappedOn&&panelLevel!==loadedLevel?panelLevel:null);
  const sumsPanel=panelLevel===loadedLevel?sumsMap:sumsOther;
  const untappedIn=useMemo<UntappedInput|null>(()=>sumsMap.data?{u:sumsMap.data,w:weights}:null,[sumsMap.data,weights]);
  // State House and State Senate are drawn from their own outlines while the panel ranks them
  const polyLevel=untappedOn&&panelLevel!=='precinct'?panelLevel:null;
  const [polys,setPolys]=useState<{level:PanelLevel;fc:AreaGeometry}|null>(null);
  useEffect(()=>{
    if(!polyLevel||!meta)return;
    let live=true;
    getGeometry(meta,polyLevel,'coarse').then(fc=>{fc.features.forEach((f,k)=>{f.properties.i=k;});if(live)setPolys({level:polyLevel,fc});}).catch(()=>{});
    return ()=>{live=false;};
  },[polyLevel,meta]);
  const polyFc=polyLevel&&polys?.level===polyLevel?polys.fc:null;
  const polyResult=useMemo(()=>polyFc?untappedBins(polyFc.features.map(f=>f.properties.id),sumsPanel.data?{u:sumsPanel.data,w:weights}:null):null,[polyFc,sumsPanel.data,weights]);

  // ---- derived map data --------------------------------------------------------------------------------------------------
  const result=useMemo(()=>areas&&meta?overlayBins(ov,areas,density,meta,measured,untappedIn):null,[areas,meta,ov,density,measured,untappedIn]);
  const palette=result?.palette??SHARE_RAMP;
  const bins=useMemo(()=>result&&areas&&geo?binsInGeometryOrder(result.bins,areas,geo,BIN_PENDING):null,[result,areas,geo]);
  // the rail reads the five Colorado groups; any other layer or mode leaves it on the overall standing
  const group:Group=single&&isGroup(ov.layer)?ov.layer:'ovr';

  const idIndex=useMemo(()=>new Map((areas?.ids??[]).map((id,i)=>[id,i])),[areas]);
  const selIndex=selected?idIndex.get(selected):undefined;
  const untappedArea=useMemo(()=>{
    const u=sumsMap.data;if(!u||!untappedOn||!selected)return null;
    const k=u.ids.indexOf(selected);return k<0?null:areaAt(u,k,weights);
  },[sumsMap.data,untappedOn,selected,weights]);
  const areaV=useMemo(()=>areas&&selIndex!==undefined?areaView(areas,selIndex,group,parties?.get(areas.ids[selIndex])):null,[areas,selIndex,group,parties]);
  // the district summary is the score store's own means when the server sends them;
  // otherwise it is the people-weighted mean of the areas with registered people
  const summary=useMemo(()=>{
    const d=meta?.district_mean;
    if(d)return {ovr:d.score.ovr,cua:d.score.cua,part:d.score.part,soi:d.score.soi,lean:d.lean,measured:d.measured.ovr,measuredBy:d.measured,people:meta!.people,mix:meta!.district_parties?partyShares(meta!.district_parties):undefined};
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

  // neighboring areas for rail variant B: the selected area plus the 6 nearest with registered people by centroid, in code order
  const neighbors=useMemo<Neighbour[]|null>(()=>{
    if(!areas||!geo||!selected||areaV?.empty||level==='block'||loadedLevel!==level||selIndex===undefined)return null;
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
    if(!geo||!areas||loadedLevel!=='precinct'||level!=='precinct'||polyFc)return [];
    return geo.fc.features.map(f=>{
      const b=featureBounds(f);
      const k=f.properties.i??0;
      return {id:f.properties.id,text:precinctShort(f.properties.id),lon:f.properties.lx,lat:f.properties.ly,widthDeg:b[2]-b[0],bin:bins?bins[k]:BIN_PENDING};
    });
  },[geo,areas,loadedLevel,level,bins,polyFc]);

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
  const onGroup=(g:OverlayLayer)=>{if(single&&g===ov.layer)return;begin('map');followDefault.current=false;setO({...o,mode:'single',layer:g});};
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
    <MapHeader meta={meta} busy={!error&&(!areas||!!loadingLevel)} index={single?ov.layer:null} level={level} onIndex={onGroup} onLevel={onLevel}
      features={features} layers={layers} industry={industryId} onIndustry={setIndustry}/>
    <div className="map-body">
      <div className="map-pane" ref={paneRef}>
        {!areas&&!error&&<LoadingTerrier label="Loading map…"/>}
        <MapCanvas ref={mapRef}
          geometry={geo?geo.fc:null} bins={bins} palette={palette}
          selectedId={selected} focus={focus} initialBounds={firstBounds.current} district={district} roadBounds={roadBounds}
          overlayGeometry={polyFc} overlayBins={polyResult?.bins??null} overlayPalette={polyResult?.palette??SHARE_RAMP}
          households={street?.points??null} selectedHousehold={selHousehold}
          onSelectArea={onSelectArea} onSelectHousehold={onSelectHousehold} onView={onView} onMove={onMove} onIdle={onIdle}/>
        {labels.length>0&&pane&&!streetActive&&<AreaLabels items={labels} palette={palette} selectedId={selected} project={(lo,la)=>mapRef.current?.project(lo,la)??null}
          zoom={mapRef.current?.view()?.zoom??0} width={pane.clientWidth} height={pane.clientHeight}/>}
        <ZoomControl onZoom={d=>mapRef.current?.zoomBy(d)}/>
        <LayersPanel o={ov} layers={layers} hidden={streetActive} onChange={next=>{begin('map');setO(next);}}/>
        {streetActive?<HouseholdLegend/>:<Legend group={ov.layer} measured={measured} onMeasured={v=>{begin('map');setMeasured(v);}} loading={!areas} overlay={(polyFc&&polyResult?polyResult:result)?.legend}/>}
        <Readout/>
        {selHousehold&&popPos&&pane&&<Popover data={pop} x={popPos.x} y={popPos.y} paneW={pane.clientWidth} paneH={pane.clientHeight}
          onClose={()=>{setSelHousehold(null);setPop(null);}}/>}
        {error&&<div className="map-error" role="alert"><span>{error}</span> <button type="button" onClick={retry}>Retry</button></div>}
      </div>
      {streetActive
        ?<StreetRail stats={street?.stats??null} truncated={street?.truncated}/>
        :<AreaRail meta={meta} level={loadedLevel} group={group} view={areaV} loading={!areas} summary={summary} neighbors={neighbors} names={features?.party_codes}
          top={untappedOn?<UntappedPanel level={panelLevel} onLevel={setPanelLevel} data={sumsPanel.data} error={sumsPanel.error}
            weights={weights} defaults={defaults} onWeights={onWeights} canPlan={canPlan} onPlan={planWalk}/>:null}
          untapped={untappedOn?{area:untappedArea,loading:!sumsMap.data&&!sumsMap.error,precinct:loadedLevel==='precinct',canPlan,onPlan:planWalk}:null}/>}
    </div>
  </div>;
}
