// Walk list map: its own MapLibre instance, drawn like the main map (flat land, white roads with a casing, roads loaded for the view).
// Blocks of the area are shaded by door count or by largest registration group, the route runs over them on the walked side of each
// street, and a numbered DOM marker sits at the first door of every stop (no glyph files offline). The map supplements the list, so the
// canvas is an image for assistive technology and the list below it stays the way to work through every door.
import {useEffect,useMemo,useRef,useState} from 'react';
import * as maplibregl from 'maplibre-gl';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import 'maplibre-gl/dist/maplibre-gl.css';
import type {WalkListPayload,WalkOverviewProps,WalkRun} from '../api/walkTypes';
import {getRoads} from '../api/client';
import {getWalkOverview,type WalkOverview} from '../api/walk';
import {ZoomControl} from './MapParts';
import {cx} from './ui';
import {countText,doorBreaks,majorParty} from '../lib/walk';
import {uPx} from '../lib/units';
import {areaParts,cleanLine,doorPoints,isListed,labelPoint,lineLength,listedPrecincts,spotAlong,usableBounds} from '../lib/walkGeo';

maplibregl.setWorkerUrl(workerUrl);

export type WalkShade='doors'|'party';
export interface WalkMapProps{payload:WalkListPayload;shade:WalkShade;onStop:(stop:number)=>void}

type Box=[number,number,number,number];
type Expr=maplibregl.ExpressionSpecification;
const empty:GeoJSON.FeatureCollection={type:'FeatureCollection',features:[]};
const WALK_LINE='#57447B';
// The colours come from the shared tokens (tokens.css); these are the same values for when a token cannot be read.
const token=(name:string,fallback:string):string=>getComputedStyle(document.documentElement).getPropertyValue(name).trim()||fallback;
const RAMP=[['--s1','#E6F1EC'],['--s2','#C2E0DB'],['--s3','#8BC2BC'],['--s4','#54979B'],['--s5','#286D73']] as const;
const PARTY=[['d','--reg-d','#95ADCE','Dem.'],['r','--reg-r','#E7A490','Rep.'],['u','--reg-u','#C6C2D3','Unaff.'],['o','--reg-o','#F5F1E9','Other'],['mixed','--centre','#E5DFDA','Mixed']] as const;

/** Shade for each door-count band, spread over the five-step ramp (one band uses the middle shade). */
function doorColor(breaks:number[],ramp:string[]):Expr{
  const n=breaks.length+1;
  const shade=(i:number)=>ramp[n===1?2:Math.round(i*(ramp.length-1)/(n-1))];
  if(n===1)return shade(0) as unknown as Expr;
  const e:unknown[]=['step',['get','doors'],shade(0)];
  breaks.forEach((b,i)=>e.push(b,shade(i+1)));
  return e as Expr;
}
function partyColor(colors:Record<string,string>):Expr{
  return ['match',['get','maj'],...Object.entries(colors).flat(),'rgba(0,0,0,0)'] as unknown as Expr;
}

interface Prepared{fc:GeoJSON.FeatureCollection;breaks:number[]}
function prepare(data:WalkOverview):Prepared{
  const features:GeoJSON.Feature[]=[];const doors:number[]=[];
  for(const f of data?.features??[]){
    if(f?.geometry?.type!=='LineString')continue;
    const p=(f.properties??{}) as Partial<WalkOverviewProps>;
    const count={d:Number(p.d)||0,r:Number(p.r)||0,u:Number(p.u)||0,o:Number(p.o)||0};
    const n=Number(p.doors)||0;
    features.push({type:'Feature',geometry:f.geometry,properties:{doors:n,maj:majorParty(count)}});
    doors.push(n);
  }
  return {fc:{type:'FeatureCollection',features},breaks:doorBreaks(doors)};
}

const runLine=(r:WalkRun):[number,number][]=>{
  const own=(r.path??[]).filter(p=>Number.isFinite(p[0])&&Number.isFinite(p[1]));
  return own.length>1?own:r.doors.map((d):[number,number]=>[d.lon,d.lat]).filter(p=>Number.isFinite(p[0])&&Number.isFinite(p[1]));
};
function boundsOf(payload:WalkListPayload):Box|null{
  const b=payload.bbox;
  if(Array.isArray(b)&&b.length===4&&b.every(Number.isFinite)&&b[0]<=b[2]&&b[1]<=b[3])return b;
  let w=Infinity,s=Infinity,e=-Infinity,n=-Infinity;
  for(const r of payload.runs)for(const p of [...runLine(r),...(r.approach??[])]){w=Math.min(w,p[0]);e=Math.max(e,p[0]);s=Math.min(s,p[1]);n=Math.max(n,p[1]);}
  return Number.isFinite(w)?[w,s,e,n]:null;
}

export function WalkMap({payload,shade,onStop}:WalkMapProps){
  const holder=useRef<HTMLDivElement>(null);
  const mapRef=useRef<maplibregl.Map|null>(null);
  const [ready,setReady]=useState(false);const [failed,setFailed]=useState(false);
  const [overview,setOverview]=useState<WalkOverview|null>(null);
  const stop=useRef(onStop);stop.current=onStop;
  const area={ss:payload.query?.ss??'',sh:payload.query?.sh??'',precinct:payload.query?.precinct??''};
  const areaKey=`${area.ss}|${area.sh}|${area.precinct}`;
  const prepared=useMemo(()=>overview?prepare(overview):null,[overview]);
  const colors=useMemo(()=>({ramp:RAMP.map(([n,f])=>token(n,f)),party:Object.fromEntries(PARTY.map(([k,n,f])=>[k,token(n,f)])) as Record<string,string>}),[]);

  // ---- create ------------------------------------------------------------------------------------------------------------
  useEffect(()=>{
    const el=holder.current;if(!el)return;
    const u=uPx();
    let m:maplibregl.Map;
    try{
      m=new maplibregl.Map({
        container:el,style:{version:8,sources:{},layers:[{id:'land',type:'background',paint:{'background-color':token('--land','#EEE9DF')}}]},
        center:[-104.82,38.86],zoom:12,minZoom:9,maxZoom:19,attributionControl:false,fadeDuration:0,dragRotate:false,pitchWithRotate:false,
      });
    }catch{setFailed(true);return;}
    mapRef.current=m;
    (window as unknown as {__cuWalkMap?:maplibregl.Map}).__cuWalkMap=m;   // handle for the screenshot harness
    m.touchZoomRotate.disableRotation();
    m.getCanvas().setAttribute('tabindex','-1');   // the canvas sits inside an image role, so it stays out of the tab order
    const road=(base:number,extra=0)=>['interpolate',['exponential',2],['zoom'],10,base+extra,12,base*1.5+extra,14,base*3+extra,16,base*7+extra,18,base*22+extra] as unknown as Expr;
    // The walk line sits beside the street centre; the offset grows with the zoom so it stays on the walked side of the road.
    const side=(px:[number,number,number,number,number])=>['interpolate',['linear'],['zoom'],13,['*',['get','side'],px[0]],15,['*',['get','side'],px[1]],16,['*',['get','side'],px[2]],17,['*',['get','side'],px[3]],19,['*',['get','side'],px[4]]] as unknown as Expr;
    m.on('load',()=>{
      for(const id of ['roads','c-roads','c-areas','c-homes','overview','doors','approach','route'])m.addSource(id,{type:'geojson',data:empty,tolerance:0.3});
      // under everything: the list's precincts, then the streets of the list's surroundings (c-) and of the view, all casings under all fills
      m.addLayer({id:'c-fill',type:'fill',source:'c-areas',filter:['==',['get','mine'],1],paint:{'fill-color':token('--paper','#F7EDE4')}});
      const kinds:[string,number][]=[['S1400',1.5*u*1.15],['S1200',3*u],['S1100',4*u]];
      const sources:[string,string][]=[['c-','c-roads'],['','roads']];
      for(const [kind,base] of kinds)for(const [pre,src] of sources){
        if(pre&&kind==='S1100')continue;
        m.addLayer({id:pre+'casing-'+kind,type:'line',source:src,minzoom:13,filter:['==',['get','kind'],kind],layout:{'line-cap':'round','line-join':'round'},
          paint:{'line-color':token('--road-casing','#E2DCD3'),'line-width':road(base,3*u)}});
      }
      m.addLayer({id:'c-link',type:'line',source:'c-roads',filter:['==',['get','kind'],'V'],layout:{'line-cap':'butt','line-join':'round'},
        paint:{'line-color':'#C8C2B9','line-width':['interpolate',['linear'],['zoom'],12,0.6,16,1.2,19,2] as unknown as number,'line-dasharray':[2,2]}});
      for(const [kind,base] of kinds)for(const [pre,src] of sources){
        if(pre&&kind==='S1100')continue;
        m.addLayer({id:pre+'road-'+kind,type:'line',source:src,filter:['==',['get','kind'],kind],layout:{'line-cap':'round','line-join':'round'},
          paint:{'line-color':token('--road','#FFFFFF'),'line-width':road(base)}});
      }
      m.addLayer({id:'c-line',type:'line',source:'c-areas',layout:{'line-join':'round'},
        paint:{'line-color':WALK_LINE,'line-opacity':0.8,'line-width':['interpolate',['linear'],['zoom'],10,0.8,14,1.2,18,1.8] as unknown as number,'line-dasharray':[3,2]}});
      m.addLayer({id:'c-homes',type:'circle',source:'c-homes',minzoom:14,
        paint:{'circle-color':'#B4ADA3','circle-radius':['interpolate',['linear'],['zoom'],14,1.2,15,2,17,3,19,4] as unknown as number}});
      m.addLayer({id:'overview',type:'line',source:'overview',layout:{'line-cap':'round','line-join':'round'},paint:{'line-color':colors.ramp[2],'line-width':road(2.1*u)}});
      m.addLayer({id:'doors',type:'circle',source:'doors',
        paint:{'circle-color':WALK_LINE,'circle-radius':['interpolate',['linear'],['zoom'],13,2,16,3.4,19,5] as unknown as number,'circle-stroke-color':'#FFFFFF','circle-stroke-width':1}});
      m.addLayer({id:'approach',type:'line',source:'approach',layout:{'line-cap':'butt','line-join':'round'},
        paint:{'line-color':WALK_LINE,'line-opacity':0.7,'line-width':['interpolate',['linear'],['zoom'],12,1.2,16,2.2,19,3.2] as unknown as number,'line-dasharray':[2,2]}});
      m.addLayer({id:'route-halo',type:'line',source:'route',layout:{'line-cap':'round','line-join':'round'},
        paint:{'line-color':'#FFFFFF','line-opacity':0.9,'line-width':['interpolate',['linear'],['zoom'],12,3.4,16,5.4,19,8] as unknown as number,'line-offset':side([1.5,3.5,6,9,20])}});
      m.addLayer({id:'route',type:'line',source:'route',layout:{'line-cap':'round','line-join':'round'},
        paint:{'line-color':WALK_LINE,'line-width':['interpolate',['linear'],['zoom'],12,1.6,16,2.6,19,4.2] as unknown as number,'line-offset':side([1.5,3.5,6,9,20])}});
      setReady(true);
    });
    const ro=new ResizeObserver(()=>m.resize());ro.observe(el);
    return ()=>{ro.disconnect();m.remove();mapRef.current=null;setReady(false);};
  },[colors]);

  // ---- roads for the view --------------------------------------------------------------------------------------------------
  useEffect(()=>{
    const m=mapRef.current;if(!m||!ready)return;
    let have:Box|null=null;let t:ReturnType<typeof setTimeout>|null=null;let ac:AbortController|null=null;
    const load=async()=>{
      const b=m.getBounds();const w=b.getWest(),s=b.getSouth(),e=b.getEast(),n=b.getNorth();
      if(have&&have[0]<=w&&have[1]<=s&&have[2]>=e&&have[3]>=n&&(have[2]-have[0])<(e-w)*4.5)return;
      const dx=(e-w)*0.35,dy=(n-s)*0.35;const want:Box=[w-dx,s-dy,e+dx,n+dy];
      ac?.abort();ac=new AbortController();
      try{
        const fc=await getRoads(want,Math.max(12,m.getZoom()),ac.signal);
        (m.getSource('roads') as maplibregl.GeoJSONSource).setData(fc);have=want;
      }catch{/* roads are decoration; a failure leaves the previous set on screen */}
    };
    const schedule=()=>{if(t)clearTimeout(t);t=setTimeout(load,220);};
    m.on('moveend',schedule);schedule();
    return ()=>{if(t)clearTimeout(t);m.off('moveend',schedule);ac?.abort();};
  },[ready]);

  // ---- blocks of the area --------------------------------------------------------------------------------------------------
  useEffect(()=>{
    if(!area.ss&&!area.sh&&!area.precinct){setOverview(null);return;}
    const ac=new AbortController();
    getWalkOverview(area,ac.signal).then(setOverview).catch(()=>{if(!ac.signal.aborted)setOverview(null);});
    return ()=>ac.abort();
  },[areaKey]);
  useEffect(()=>{
    const m=mapRef.current;if(!m||!ready)return;
    (m.getSource('overview') as maplibregl.GeoJSONSource).setData(prepared?.fc??empty);
  },[ready,prepared]);
  useEffect(()=>{
    const m=mapRef.current;if(!m||!ready)return;
    m.setPaintProperty('overview','line-color',shade==='doors'?doorColor(prepared?.breaks??[],colors.ramp):partyColor(colors.party));
    m.setFilter('overview',shade==='doors'?['>',['get','doors'],0]:['!=',['get','maj'],'none']);
  },[ready,shade,prepared,colors]);

  // ---- route and stops -----------------------------------------------------------------------------------------------------
  useEffect(()=>{
    const m=mapRef.current;if(!m||!ready)return;
    const route:GeoJSON.Feature[]=[],approach:GeoJSON.Feature[]=[];
    for(const r of payload.runs){
      const line=runLine(r);
      if(line.length>1)route.push({type:'Feature',properties:{stop:r.stop,side:-(r.lr||0)},geometry:{type:'LineString',coordinates:line}});
      const lead=(r.approach??[]).filter(p=>Number.isFinite(p[0])&&Number.isFinite(p[1]));
      if(lead.length>1)approach.push({type:'Feature',properties:{stop:r.stop},geometry:{type:'LineString',coordinates:lead}});
    }
    (m.getSource('route') as maplibregl.GeoJSONSource).setData({type:'FeatureCollection',features:route});
    (m.getSource('approach') as maplibregl.GeoJSONSource).setData({type:'FeatureCollection',features:approach});
    const doors=payload.runs.flatMap(r=>doorPoints(r)).map((c):GeoJSON.Feature=>({type:'Feature',properties:{},geometry:{type:'Point',coordinates:c}}));
    (m.getSource('doors') as maplibregl.GeoJSONSource).setData({type:'FeatureCollection',features:doors});
    const markers=payload.runs.flatMap((r,i)=>{
      const d=r.doors[0];if(!d||!Number.isFinite(d.lon)||!Number.isFinite(d.lat))return [];
      const el=document.createElement('div');
      el.className=cx('walk-stop',i===payload.runs.length-1&&'is-last');
      el.textContent=String(r.stop);el.title=`Stop ${r.stop}: ${r.street}`;el.setAttribute('aria-hidden','true');
      el.addEventListener('click',()=>stop.current(r.stop));
      return [new maplibregl.Marker({element:el,anchor:'center'}).setLngLat([d.lon,d.lat]).addTo(m)];
    });
    const b=boundsOf(payload);
    if(b)m.fitBounds([[b[0],b[1]],[b[2],b[3]]],{padding:{top:40,right:40,bottom:40,left:64},maxZoom:17.5,duration:0});
    return ()=>{for(const k of markers)k.remove();};
  },[ready,payload.runs,payload.bbox]);

  // ---- surroundings of the list: streets, precinct lines and homes (the list may come without them) -------------------------------
  const context=payload.context;
  const hasAreas=Array.isArray(context?.precincts)&&context.precincts.length>0,hasHomes=Array.isArray(context?.homes)&&context.homes.length>0;
  useEffect(()=>{
    const m=mapRef.current;if(!m||!ready)return;
    const listed=listedPrecincts(payload.runs),roads:GeoJSON.Feature[]=[],areas:GeoJSON.Feature[]=[];
    const roadList=context?.roads,areaList=context?.precincts;
    for(const r of Array.isArray(roadList)?roadList:[]){
      const c=cleanLine(r?.c);
      if(c.length>1&&(r.k==='S1200'||r.k==='S1400'||r.k==='V'))roads.push({type:'Feature',properties:{kind:r.k,name:r.n??''},geometry:{type:'LineString',coordinates:c}});
    }
    for(const a of Array.isArray(areaList)?areaList:[]){
      const parts=areaParts(a?.rings);
      if(parts.length)areas.push({type:'Feature',properties:{mine:isListed(a,listed)?1:0},geometry:{type:'MultiPolygon',coordinates:parts}});
    }
    const homes=cleanLine(context?.homes).map((c):GeoJSON.Feature=>({type:'Feature',properties:{},geometry:{type:'Point',coordinates:c}}));
    const set=(id:string,features:GeoJSON.Feature[])=>(m.getSource(id) as maplibregl.GeoJSONSource).setData({type:'FeatureCollection',features});
    set('c-roads',roads);set('c-areas',areas);set('c-homes',homes);
  },[ready,context,payload.runs]);

  // ---- names: one per street of the list (along the line when it runs straight) and one per precinct; any that would sit on another or on a stop are hidden
  useEffect(()=>{
    const m=mapRef.current,root=holder.current;if(!m||!ready||!root)return;
    const marks:maplibregl.Marker[]=[];
    const add=(text:string,at:[number,number],cls:string,rank:number,angle=0)=>{
      const el=document.createElement('div');el.className=cls;el.dataset.rank=String(rank);el.setAttribute('aria-hidden','true');
      const s=document.createElement('span');s.textContent=text;if(angle)s.style.transform=`rotate(${angle}deg)`;el.appendChild(s);
      marks.push(new maplibregl.Marker({element:el,anchor:'center'}).setLngLat(at).addTo(m));
    };
    const longest=new Map<number,{r:WalkRun;len:number}>();
    for(const r of payload.runs){
      const line=runLine(r);if(line.length<2)continue;
      const kx=Math.cos(line[0][1]*Math.PI/180),len=lineLength(line.map((p):[number,number]=>[p[0]*kx,p[1]]));
      const cur=longest.get(r.street_id);if(!cur||len>cur.len)longest.set(r.street_id,{r,len});
    }
    for(const {r,len} of longest.values()){
      const line=runLine(r),kx=Math.cos(line[0][1]*Math.PI/180),sp=spotAlong(line.map((p):[number,number]=>[p[0]*kx,p[1]]),len/2,len*0.3);
      let angle=sp.straight?-Math.atan2(sp.u[1],sp.u[0])*180/Math.PI:0;
      if(angle>90)angle-=180;else if(angle<=-90)angle+=180;
      add(r.street,[sp.at[0]/kx,sp.at[1]],'walk-name',len,angle);
    }
    const view=usableBounds(context?.bbox)??usableBounds(payload.bbox),areaList=context?.precincts;
    if(view)for(const a of Array.isArray(areaList)?areaList:[]){
      const p=labelPoint(a?.rings,view);
      if(p&&(a.label||a.id))add(String(a.label||a.id),p,'walk-area',0);
    }
    const declutter=()=>{
      const taken=[...root.querySelectorAll('.walk-stop')].map(e=>e.getBoundingClientRect());
      const names=[...root.querySelectorAll<HTMLElement>('.walk-name,.walk-area')].sort((p,q)=>Number(q.dataset.rank)-Number(p.dataset.rank));
      for(const e of names)e.classList.remove('is-hidden');
      for(const e of names){
        const r=e.getBoundingClientRect();
        if(taken.some(t=>r.left<t.right&&t.left<r.right&&r.top<t.bottom&&t.top<r.bottom))e.classList.add('is-hidden');else taken.push(r);
      }
    };
    m.on('moveend',declutter);
    const frame=requestAnimationFrame(declutter);
    return ()=>{cancelAnimationFrame(frame);m.off('moveend',declutter);for(const k of marks)k.remove();};
  },[ready,payload.runs,payload.bbox,context]);

  const label=`Map of the route: ${countText(payload.totals?.stops??payload.runs.length,'stop')} on ${countText(payload.totals?.streets??0,'street')}. The list below has every door.`;
  const shading=!!prepared&&prepared.fc.features.length>0;
  return <div className="walk-map">
    <div ref={holder} className="walk-map-canvas" role="img" aria-label={label}/>
    {failed
      ?<p className="walk-map-fail" role="status">The map could not be drawn here. The list below has every stop.</p>
      :<ZoomControl onZoom={d=>{const m=mapRef.current;if(m)m.easeTo({zoom:m.getZoom()+d,duration:220});}}/>}
    <section className="walk-legend" aria-label="Map key">
      {shading&&(shade==='doors'
        ?<div className="walk-legend-group"><p className="walk-legend-title">Doors per block</p>
          <div className="walk-ramp"><span>fewer</span><ul className="walk-swatches" aria-hidden="true">{colors.ramp.map((c,i)=><li key={i}><i style={{background:c}}/></li>)}</ul><span>more</span></div></div>
        :<div className="walk-legend-group"><p className="walk-legend-title">Largest party per block</p>
          <ul className="walk-swatches party">{PARTY.map(([k,,,name])=><li key={k}><i style={{background:colors.party[k]}}/><span>{name}</span></li>)}</ul></div>)}
      <ul className="walk-lines">
        <li><i className="walk-sample solid"/>Walk</li>
        <li><i className="walk-sample dashed"/>Between stops</li>
        <li><i className="walk-sample dot"/>Doors</li>
        {hasHomes&&<li><i className="walk-sample home"/>Homes</li>}
        {hasAreas&&<li><i className="walk-sample area"/>Precinct</li>}
      </ul>
    </section>
  </div>;
}
