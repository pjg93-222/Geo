import {useEffect,useMemo,useRef,useState} from 'react';
import {postJson} from '../api/client';
import {parseAddressText} from '../lib/walkAddressImport';
import type {AddressImportResult,ImportedWalkAddress,ImportedWalkStreet} from '../lib/walkAddressImport';
import type {ImportedWalkImage} from '../lib/walkImageImport';
import {fitImageAlignment,locationToImagePixel,projectImageLocation} from '../lib/walkImageAlignment';
import type {ImageAlignment,ImageControlPoint} from '../lib/walkImageAlignment';
import type {WalkImageLine} from '../lib/walkImageOcr';

type Candidate={id:number;label:string;city:string;state:string;zip:string;zip_filter:string;doors:number};
type Match={row:number;input:string;status:string;method?:string;selected_id?:number;candidates:Candidate[];truncated:boolean};
type Matches={district:string;snapshot:string;rows:Match[]};
type Context={district:string;snapshot:string;roads:GeoJSON.FeatureCollection<GeoJSON.LineString|GeoJSON.MultiLineString>;junctions:{id:number|string;lon:number;lat:number;labels:string[]}[];bounds:[number,number,number,number]|null};
export type WalkImageEvidence={imageId:string;name:string;district:string;snapshot:string;automatic?:boolean;scope?:{sourceFile:string;sourceSheet?:string;fromRow:number;toRow:number};streets:{id:number;zip:string;label:string;city:string;input?:string}[];alignment?:{points?:ImageControlPoint[];x?:number[];y?:number[];rmsMeters:number;maxErrorMeters:number}};
type ImageResolution={district:string;snapshot:string;status:'matched'|'review';reason?:string;fit?:ImageAlignment;bounds?:[number,number,number,number];resolutions?:{text:string;street_id?:number;city?:string;status:string;reason?:string;distance_m?:number;road_only?:boolean}[]};
type Props={images:ImportedWalkImage[];known:{id:number;zip:string}[];anchors:ImportedWalkAddress[];requests:ImportedWalkStreet[];disabled:boolean;contextVersion:number;beginWork:(id:string)=>boolean;endWork:(id:string)=>void;onEvidence:(id:string,evidence:WalkImageEvidence|null)=>void;onAppend:(result:AddressImportResult)=>void};
const keyOf=(c:Candidate)=>`${c.id}:${c.zip_filter||''}`;
const labelKey=(value:string)=>value.toLowerCase().replace(/[^a-z0-9]/g,'');
const manualKey=(row:Match,result:Matches)=>`${result.district}|${result.snapshot}|${labelKey(row.input)}`;
const automaticChoice=(row:Match)=>{const candidate=row.status==='matched'?(row.candidates.find(c=>c.id===row.selected_id)??(row.candidates.length===1?row.candidates[0]:undefined)):undefined;return candidate?keyOf(candidate):'';};
const linesOf=(geometry:GeoJSON.LineString|GeoJSON.MultiLineString)=>geometry.type==='LineString'?[geometry.coordinates]:geometry.coordinates;
const roadName=(feature:GeoJSON.Feature)=>String(feature.properties?.name??feature.properties?.label??feature.properties?.display??'Road');
function likelyAddressLines(lines:{text:string;confidence:number}[]):string{
  const seen=new Set<string>(),out:string[]=[];
  for(const {text,confidence} of lines){
    const value=text.replace(/[|“”]/g,' ').replace(/\s+/g,' ').trim();
    if(confidence<25||!/[a-z]/i.test(value)||value.length>180)continue;
    let readable=false;try{const parsed=parseAddressText(value,'image.txt');readable=!!parsed.addresses.length||!!parsed.streets?.length;}catch{/* Unreadable OCR remains in the raw text panel. */}if(!readable)continue;
    const key=value.toLowerCase().replace(/[^a-z0-9]/g,'');if(seen.has(key))continue;seen.add(key);out.push(value);
  }
  return out.join('\n');
}
export function WalkImageReview(props:Props){return <section className="walk-image-review" aria-label="Read images and align maps">
  <h3>Images in this upload</h3>
  <p className="walk-hint">Read printed addresses or map labels locally, then review the text. Map labels can help locate your listed streets. They become walk requests only when you add them below.</p>
  {props.images.map((image,i)=><ImageReview key={image.id} {...props} image={image} index={i}/>)}</section>;}

function ImageReview({image,index,known,anchors,requests,disabled,contextVersion,beginWork,endWork,onEvidence,onAppend}:Props&{image:ImportedWalkImage;index:number}){
  const [url,setUrl]=useState(''),[size,setSize]=useState({width:0,height:0}),[text,setText]=useState(''),[raw,setRaw]=useState('');
  const [progress,setProgress]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[matches,setMatches]=useState<Matches|null>(null),[choices,setChoices]=useState<string[]>([]);
  const [context,setContext]=useState<Context|null>(null),[points,setPoints]=useState<ImageControlPoint[]>([]),[pending,setPending]=useState<{x:number;y:number}|null>(null),[opacity,setOpacity]=useState(.85),[confirmed,setConfirmed]=useState(false),[used,setUsed]=useState(false);
  const [lon,setLon]=useState(''),[lat,setLat]=useState('');const sequence=useRef(0);
  const [ocrLines,setOcrLines]=useState<WalkImageLine[]>([]),[estimated,setEstimated]=useState<ImageResolution|null>(null);const abort=useRef<AbortController|null>(null);
  const active=useRef<number|null>(null),manualChoices=useRef(new Map<string,string>()),version=useRef(contextVersion),release=useRef(endWork);
  version.current=contextVersion;release.current=endWork;
  const scope=image.anchor?{sourceFile:image.sourceFile,sourceSheet:image.sourceSheet,fromRow:image.anchor.fromRow,toRow:image.anchor.toRow}:undefined;
  function current(id:number,revision:number){return id===sequence.current&&revision===version.current;}
  function claim(continuing?:number):number|null{
    if(active.current!==null&&active.current!==continuing||!beginWork(image.id))return null;
    const id=++sequence.current;active.current=id;return id;
  }
  function finish(id:number){if(active.current===id){active.current=null;setBusy(false);release.current(image.id);}}
  function cancel(){sequence.current++;abort.current?.abort();active.current=null;release.current(image.id);setBusy(false);}
  function remembered(row:Match,result:Matches):string|undefined{
    const key=manualKey(row,result);if(!manualChoices.current.has(key))return undefined;
    const choice=manualChoices.current.get(key)!;
    return choice&&row.candidates.some(candidate=>keyOf(candidate)===choice)?choice:'';
  }
  useEffect(()=>{const value=URL.createObjectURL(image.blob);setUrl(value);return()=>{sequence.current++;abort.current?.abort();active.current=null;release.current(image.id);URL.revokeObjectURL(value);};},[image.blob,image.id]);
  useEffect(()=>{
    sequence.current++;abort.current?.abort();active.current=null;release.current(image.id);manualChoices.current.clear();
    setBusy(false);setMatches(null);setChoices([]);setContext(null);setEstimated(null);setUsed(false);setPoints([]);setPending(null);setConfirmed(false);setProgress('');setError('');
  },[contextVersion,image.id]);
  function clearEvidence(){setUsed(false);onEvidence(image.id,null);}
  function editText(value:string){sequence.current++;setText(value);setMatches(null);setChoices([]);setContext(null);setPoints([]);setPending(null);setConfirmed(false);setEstimated(null);clearEvidence();}
  const alignment=useMemo(()=>{if(points.length<3)return {fit:null,error:''};try{return {fit:fitImageAlignment(points,size.width,size.height),error:''};}catch(e){return {fit:null,error:(e as Error).message};}},[points,size]);
  async function read(){
    const id=claim();if(id===null)return;const revision=contextVersion,controller=new AbortController();abort.current=controller;setBusy(true);setError('');setProgress('Starting image reader…');clearEvidence();
    try{const {recognizeWalkImage}=await import('../lib/walkImageOcr');if(!current(id,revision)||controller.signal.aborted)return;const result=await recognizeWalkImage(image.blob,message=>{if(current(id,revision))setProgress(message);},controller.signal);if(!current(id,revision))return;
      const clean=likelyAddressLines(result.lines);setRaw(result.lines.map(line=>line.text).join('\n'));setText(clean);setOcrLines(result.lines);setSize({width:result.width,height:result.height});setMatches(null);setChoices([]);setContext(null);setPoints([]);setConfirmed(false);setEstimated(null);
      if(clean)await match(clean,result.lines,{width:result.width,height:result.height},id);else setProgress('No clear address labels were found. Inspect the recognized text and enter any readable labels below.');
    }catch(e){if(current(id,revision))setError((e as Error).message);}finally{finish(id);}
  }
  async function match(value=text,labels=ocrLines,dimensions=size,continuing?:number){
    const id=claim(continuing);if(id===null)return;const revision=contextVersion;setBusy(true);setError('');setProgress('Matching image labels to district streets…');clearEvidence();setContext(null);setPoints([]);setConfirmed(false);
    try{
      const parsed=parseAddressText(value,`${image.name}.txt`),rows=parsed.streets??[];
      if(parsed.issues.length)throw new Error('Correct the unreadable text before matching. Use one street or complete address per line.');
      if(!rows.length)throw new Error('No street-only labels found. Complete addresses can be added directly to the walk list.');
      if(rows.length>100)throw new Error('Match up to 100 street labels per image. Split the text into smaller images or files.');
      const nearbyRequests=requests.filter(row=>row.sourceImageId===image.id||!image.anchor||row.sourceFile===image.sourceFile&&row.sourceSheet===image.sourceSheet&&row.sourceRow>=image.anchor.fromRow&&row.sourceRow<=image.anchor.toRow).map(({street,city,state,zip})=>({street,city,state,zip}));
      if(nearbyRequests.length>100)throw new Error('Use at most 100 street requests alongside each image. Split this file into smaller groups.');
      const query=[...new Map([...rows.map(({street,city,state,zip})=>({street,city,state,zip})),...nearbyRequests].map(row=>[JSON.stringify(row).toLowerCase(),row])).values()];
      if(query.length>500)throw new Error('Use fewer than 500 combined image and file street labels.');
      const first=await postJson<Matches>('/api/v3/walk/address-match',{rows:query,proximity:false});if(!current(id,revision))return;
      const selected=[...new Map([...known,...first.rows.flatMap(row=>{const choice=remembered(row,first),candidate=choice!==undefined?row.candidates.find(c=>keyOf(c)===choice):row.status==='matched'&&row.candidates.length===1?row.candidates[0]:undefined;return candidate?[{id:candidate.id,zip:candidate.zip_filter||''}]:[];})].map(row=>[`${row.id}:${row.zip}`,row])).values()];
      if(selected.length>5000||anchors.length>500)throw new Error('This upload has more location context than the image reader can check at once (5,000 streets or 500 exact addresses). Use a smaller source file.');
      const second=await postJson<Matches>('/api/v3/walk/address-match',{rows:query,proximity:true,snapshot:first.snapshot,selected_streets:selected,anchors:anchors.map(({house,street,unit,city,state,zip})=>({house,street,unit,city,state,zip}))});if(!current(id,revision))return;
      if(second.snapshot!==first.snapshot||second.district!==first.district)throw new Error('The district changed. Match the image again.');
      setMatches(second);setChoices(second.rows.map(row=>remembered(row,second)??automaticChoice(row)));
      const ids=[...new Set([...selected.map(s=>s.id),...second.rows.flatMap(row=>row.candidates.map(c=>c.id))])];
      let resolution:ImageResolution|null=null;
      const requested=new Set(rows.map(row=>row.street.toLowerCase().replace(/[^a-z0-9]/g,'')));
      const positioned=labels.filter(line=>requested.has(line.text.toLowerCase().replace(/[^a-z0-9]/g,'')));
      if(positioned.length>80||selected.length>40||ids.length>200)throw new Error('This image has too many possible locations for a reliable automatic fit. Use fewer than 80 labels, 40 known streets and 200 street choices by adding towns or narrowing the source file.');
      if(positioned.length>=4&&selected.length){
        resolution=await postJson<ImageResolution>('/api/v3/walk/image-resolve',{snapshot:first.snapshot,street_ids:selected.map(s=>s.id),image:dimensions,labels:positioned,requests:nearbyRequests});if(!current(id,revision))return;
        if(resolution.snapshot!==first.snapshot||resolution.district!==first.district)throw new Error('The district changed. Match the image again.');
        setEstimated(resolution);
        if(resolution.status==='matched'){
          const resolved=(resolution.resolutions??[]).filter(row=>row.status==='matched'&&!row.road_only).flatMap(row=>{const match=second.rows.find(m=>labelKey(m.input)===labelKey(row.text)&&m.candidates.some(c=>c.id===row.street_id));if(!match||remembered(match,second)!==undefined)return [];const c=match.candidates.find(c=>c.id===row.street_id);return c?[{id:c.id,zip:c.zip_filter||'',label:c.label,city:c.city,input:row.text}]:[];});
          if(resolved.length){setChoices(second.rows.map(row=>{const manual=remembered(row,second);if(manual!==undefined)return manual;const found=resolved.find(street=>labelKey(street.input??'')===labelKey(row.input));const c=row.candidates.find(c=>c.id===found?.id);return c?keyOf(c):automaticChoice(row);}));onEvidence(image.id,{imageId:image.id,name:image.name,district:first.district,snapshot:first.snapshot,automatic:true,scope,streets:resolved,...resolution.fit?{alignment:{x:resolution.fit.x,y:resolution.fit.y,rmsMeters:resolution.fit.rmsMeters,maxErrorMeters:resolution.fit.maxErrorMeters}}:{}});setUsed(true);}
        }
      }
      const names=[...new Set(query.map(row=>row.street))];if(names.length>100)throw new Error('Use at most 100 distinct street labels for the geographic overlay.');
      const geo=await postJson<Context>('/api/v3/walk/image-context',{snapshot:first.snapshot,street_ids:ids,street_names:names,...(resolution?.status==='matched'&&resolution.bounds?{bounds:resolution.bounds}:{})});if(!current(id,revision))return;
      if(geo.snapshot!==first.snapshot||geo.district!==first.district)throw new Error('The district changed. Match the image again.');
      setContext(geo);setProgress(resolution?.status==='matched'?'Known streets and surrounding road positions narrowed the image location. Review the estimated overlay and street choices.':resolution?.reason||'Review the street choices. Add matching road junctions below when automatic location evidence is insufficient.');
    }catch(e){if(current(id,revision))setError((e as Error).message);}finally{finish(id);}
  }
  function chosenStreets(){return matches?.rows.flatMap((row,i)=>{const c=row.candidates.find(c=>keyOf(c)===choices[i]);return c?[{id:c.id,zip:c.zip_filter||'',label:c.label,city:c.city}]:[];})??[];}
  function useEvidence(){if(!matches)return;onEvidence(image.id,{imageId:image.id,name:image.name,district:matches.district,snapshot:matches.snapshot,scope,streets:chosenStreets(),...(confirmed&&alignment.fit?{alignment:{points,rmsMeters:alignment.fit.rmsMeters,maxErrorMeters:alignment.fit.maxErrorMeters}}:{})});setUsed(true);}
  function append(){try{const parsed=parseAddressText(text,`${image.name}.txt`);if(!parsed.addresses.length&&!parsed.streets?.length)throw new Error('Enter at least one readable street or complete address.');for(const row of [...parsed.addresses,...parsed.streets??[]]){row.sourceFile=image.sourceFile;row.sourceSheet=image.sourceSheet;row.sourceImageId=image.id;row.note='Image text reviewed by user';row.sourceText=row.sourceText??row.street;}onAppend(parsed);setProgress('Reviewed text added to the walk requests below.');}catch(e){setError((e as Error).message);}}
  function addPoint(at:{lon:number;lat:number;label?:string}){if(!pending)return;if(points.length>=20){setError('Use up to 20 alignment points.');return;}setPoints([...points,{...pending,...at}]);setPending(null);setConfirmed(false);clearEvidence();}
  const fit=alignment.fit??(estimated?.status==='matched'?estimated.fit??null:null);
  const geoView=useMemo(()=>{
    if(!context?.bounds)return null;const [w,s,e,n]=context.bounds,[left,bottom]=projectImageLocation(w,s),[right,top]=projectImageLocation(e,n),spanX=Math.max(1,right-left),spanY=Math.max(1,top-bottom),scale=Math.min(960/spanX,660/spanY);
    return {at:(lon:number,lat:number):[number,number]=>{const [x,y]=projectImageLocation(lon,lat);return [20+(x-left)*scale,20+(top-y)*scale];},width:spanX*scale+40,height:spanY*scale+40};
  },[context]);
  return <details className="walk-image-card" open={undefined}>
    <summary>{image.name}{image.sourceSheet?` · ${image.sourceSheet}`:''}{image.sourceCell?` · ${image.sourceCell}`:''}{used?' · Location evidence applied':''}</summary>
    <div className="walk-actions"><button type="button" className="walk-button" disabled={busy||disabled} onClick={()=>void read()}>Read image {index+1}</button>{busy&&<button type="button" className="walk-button" onClick={()=>{cancel();setProgress('Image reading stopped.');}}>Cancel image reading</button>}<span role="status">{busy?progress:''}</span></div>
    {error&&<p role="alert" className="walk-note">{error}</p>}
    <div className="walk-image-panels">
      <div><h4>Source image</h4><p className="walk-hint">{context?'Click a road junction here, then choose the same junction on the geographic map.':'Read the image or type its labels below to load matching roads.'}</p>
        <div className="walk-image-source">{url&&<img src={url} alt={`Source map or address image ${index+1}`} onLoad={e=>setSize({width:e.currentTarget.naturalWidth,height:e.currentTarget.naturalHeight})}/>}
          {!!size.width&&<svg viewBox={`0 0 ${size.width} ${size.height}`} aria-label={`Image alignment ${index+1}`} onClick={e=>{if(!context||disabled||busy)return;const rect=e.currentTarget.getBoundingClientRect();setPending({x:(e.clientX-rect.left)/rect.width*size.width,y:(e.clientY-rect.top)/rect.height*size.height});}}>
            {fit&&context?.roads.features.flatMap((feature,i)=>linesOf(feature.geometry).map((line,j)=><polyline key={`${i}-${j}`} points={line.map(at=>locationToImagePixel(fit,at[0],at[1]).join(',')).join(' ')} fill="none" stroke="#00eaff" strokeWidth={3} opacity={opacity} vectorEffect="non-scaling-stroke"><title>{roadName(feature)}</title></polyline>))}
            {points.map((point,i)=><g key={i}><circle cx={point.x} cy={point.y} r={8} fill="#fce96a" stroke="#222"/><text x={point.x+12} y={point.y-10} fill="white" stroke="#222" paintOrder="stroke" strokeWidth={3} fontSize={22}>{i+1}</text></g>)}
            {pending&&<circle cx={pending.x} cy={pending.y} r={12} fill="none" stroke="#ffda35" strokeWidth={4}/>}
          </svg>}
        </div>
      </div>
      {context&&<div><h4>District road coordinates</h4><p className="walk-hint">{pending?'Choose the corresponding junction.':'Road lines come from the selected district. Dots mark junctions.'}</p>
        {geoView&&<svg className="walk-image-geography" viewBox={`0 0 ${geoView.width} ${geoView.height}`} aria-label={`Geographic road map ${index+1}`}>
          {context.roads.features.flatMap((feature,i)=>linesOf(feature.geometry).map((line,j)=><polyline key={`${i}-${j}`} points={line.map(at=>geoView.at(at[0],at[1]).join(',')).join(' ')} fill="none" stroke="#796b93" strokeWidth={3} vectorEffect="non-scaling-stroke"><title>{roadName(feature)}</title></polyline>))}
          {context.junctions.map(junction=>{const [x,y]=geoView.at(junction.lon,junction.lat);return <circle key={junction.id} cx={x} cy={y} r={pending?7:4} fill={pending?'#b45c15':'#254f70'} role="button" tabIndex={pending?0:-1} aria-label={`${junction.labels.join(' / ')} at ${junction.lat.toFixed(5)}, ${junction.lon.toFixed(5)}`} onClick={()=>addPoint({lon:junction.lon,lat:junction.lat,label:junction.labels.join(' / ')})} onKeyDown={e=>{if(e.key==='Enter')addPoint({lon:junction.lon,lat:junction.lat,label:junction.labels.join(' / ')});}}><title>{junction.labels.join(' / ')}</title></circle>;})}
          {points.map((p,i)=>{const [x,y]=geoView.at(p.lon,p.lat);return <text key={i} x={x+10} y={y-10} fontSize={18} fill="#111">{i+1}</text>;})}
        </svg>}
        {pending&&<><label>Matching junction <select aria-label={`Matching junction ${index+1}`} value="" onChange={e=>{const at=context.junctions.find(j=>String(j.id)===e.target.value);if(at)addPoint({lon:at.lon,lat:at.lat,label:at.labels.join(' / ')});}}><option value="">Choose the same place…</option>{context.junctions.map(j=><option key={j.id} value={j.id}>{j.labels.join(' / ')} · {j.lat.toFixed(5)}, {j.lon.toFixed(5)}</option>)}</select></label>
          <details><summary>Enter known coordinates</summary><label>Longitude <input aria-label={`Point longitude ${index+1}`} value={lon} onChange={e=>setLon(e.target.value)}/></label><label>Latitude <input aria-label={`Point latitude ${index+1}`} value={lat} onChange={e=>setLat(e.target.value)}/></label><button type="button" className="walk-button" disabled={!lon.trim()||!lat.trim()||!Number.isFinite(Number(lon))||!Number.isFinite(Number(lat))||Math.abs(Number(lon))>180||Math.abs(Number(lat))>=85} onClick={()=>addPoint({lon:Number(lon),lat:Number(lat),label:'Entered coordinates'})}>Add coordinate point</button></details></>}
        {!!points.length&&<><ol>{points.map((p,i)=><li key={i}>{p.label||'Point'} · {p.lat.toFixed(5)}, {p.lon.toFixed(5)} <button type="button" onClick={()=>{setPoints(points.filter((_,j)=>j!==i));setConfirmed(false);clearEvidence();}}>Remove point {i+1}</button></li>)}</ol><button type="button" className="walk-button" onClick={()=>{setPoints([]);setPending(null);setConfirmed(false);clearEvidence();}}>Clear alignment</button></>}
        {alignment.error&&<p className="walk-note">{alignment.error}</p>}
        {fit&&<><p className="walk-hint">{!alignment.fit?'Estimated image alignment. ':''}{fit.reason} Fit disagreement: {fit.rmsMeters.toFixed(1)} m average, {fit.maxErrorMeters.toFixed(1)} m maximum. This measures alignment agreement, not address accuracy.</p><label>Road overlay opacity <input aria-label={`Road overlay opacity ${index+1}`} type="range" min="0" max="1" step=".05" value={opacity} onChange={e=>setOpacity(Number(e.target.value))}/></label><label className="walk-image-confirm"><input type="checkbox" checked={confirmed} disabled={!alignment.fit?.verified} onChange={e=>{setConfirmed(e.target.checked);clearEvidence();}}/> I checked that the road overlay follows the source map.</label></>}
      </div>}
    </div>
    <label className="walk-image-text">Street names or full addresses read from this image<textarea aria-label={`Reviewed image text ${index+1}`} rows={7} value={text} disabled={busy||disabled} onChange={e=>editText(e.target.value)} placeholder="One street name or complete address per line"/></label>
    {raw&&<details><summary>All recognized text (may contain mistakes)</summary><pre>{raw}</pre></details>}
    <div className="walk-actions"><button type="button" className="walk-button" disabled={busy||disabled||!text.trim()} onClick={()=>void match()}>Match image labels {index+1}</button><button type="button" className="walk-button" disabled={busy||disabled||!text.trim()} onClick={append}>Add reviewed text to walk requests</button></div>
    {matches&&<><div className="walk-address-table-wrap"><table className="walk-address-table"><thead><tr><th>Image text</th><th>Recorded street</th></tr></thead><tbody>{matches.rows.map((row,i)=><tr key={i}><td>{row.input}</td><td><select aria-label={`Image ${index+1} street ${i+1}`} value={choices[i]||''} disabled={busy||disabled} onChange={e=>{manualChoices.current.set(manualKey(row,matches),e.target.value);setChoices(choices.map((value,j)=>j===i?e.target.value:value));clearEvidence();}}><option value="">Do not use as location evidence</option>{row.candidates.map(c=><option key={keyOf(c)} value={keyOf(c)}>{c.label} · {c.city} {c.zip} · {c.doors} doors</option>)}</select>{manualChoices.current.has(manualKey(row,matches))&&<p className="walk-hint">{choices[i]?'Selected by you.':'Left out of location evidence by you.'}</p>}{!row.candidates.length&&<p className="walk-hint">No recorded doors matched this label. A road may still appear on the geographic map.</p>}{row.method==='proximity'&&<p className="walk-hint">Suggested using nearby established locations. Review before applying.</p>}{row.truncated&&<p className="walk-hint">More choices exist. Add a town to this line and match again.</p>}</td></tr>)}</tbody></table></div><button type="button" className="walk-button walk-primary" disabled={busy||disabled||!chosenStreets().length} onClick={useEvidence}>Use reviewed image locations</button><p className="walk-hint">This rechecks the existing walk requests against the selected streets. It does not add incidental map labels or invent missing house numbers.{used?' Image locations are now in use.':''}</p></>}
    {!busy&&progress&&<p role="status" className="walk-hint">{progress}</p>}
  </details>;
}
