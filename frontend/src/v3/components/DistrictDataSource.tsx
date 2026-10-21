import {useCallback,useEffect,useRef,useState} from 'react';
import type {DesktopDataBridge,DesktopDistrictData} from '../api/desktopData';
import {DATA_DISTRICT_LABEL,dataBytes,dataDistrict,districtDataBusy,districtDataReceiver,districtDataSummary} from '../lib/desktopData';
import './DistrictDataSource.css';

export function useDistrictDataSource(district?:string){
  const [data,setData]=useState<DesktopDistrictData|null>(null),[refreshing,setRefreshing]=useState(false),[refreshError,setRefreshError]=useState(false);
  const session=useRef<{receiver:ReturnType<typeof districtDataReceiver>;bridge:DesktopDataBridge;live:boolean;refreshing:boolean}|null>(null);
  useEffect(()=>{
    setData(null);setRefreshing(false);setRefreshError(false);
    const bridge=window.desktop;
    if(!dataDistrict(district)||typeof bridge?.districtData!=='function')return;
    const receiver=districtDataReceiver(district),current={receiver,bridge,live:true,refreshing:false};session.current=current;
    const initial=receiver.read();let unsubscribe:unknown;
    const apply=(value:DesktopDistrictData|null|undefined)=>{if(current.live&&value!==undefined)setData(value);};
    try{unsubscribe=bridge.onDistrictData?.(value=>apply(receiver.event(value)));}catch{/* an unavailable bridge retains the local-source presentation */}
    Promise.resolve().then(()=>bridge.districtData!()).then(value=>apply(initial(value))).catch(()=>{});
    return()=>{current.live=false;receiver.close();if(session.current===current)session.current=null;if(typeof unsubscribe==='function')try{unsubscribe();}catch{/* the native window may already be gone */}};
  },[district]);
  const refresh=useCallback(async()=>{
    const current=session.current;
    if(!current?.live||current.refreshing||!data?.canRefresh||districtDataBusy(data)||typeof current.bridge.refreshDistrictData!=='function')return;
    current.refreshing=true;setRefreshing(true);setRefreshError(false);const accept=current.receiver.read();
    try{const value=accept(await current.bridge.refreshDistrictData());if(current.live&&value!==undefined)setData(value);}
    catch{if(current.live)setRefreshError(true);}
    finally{current.refreshing=false;if(current.live)setRefreshing(false);}
  },[data]);
  return {data:data?.district===district?data:null,refresh,refreshing,refreshError};
}

export function DistrictDataSource({district}:{district?:string}){
  const {data,refresh,refreshing,refreshError}=useDistrictDataSource(district);
  if(!data)return null;
  const busy=refreshing||districtDataBusy(data),progress=data.phase==='downloading'||data.phase==='verifying';
  return <section className="district-data-source" aria-labelledby="district-data-source-title" data-phase={data.phase} data-district={data.district}>
    <div className="district-data-source-heading"><div><h2 id="district-data-source-title">GitHub district data</h2><p>{DATA_DISTRICT_LABEL[data.district]} · {data.repository}</p></div>
      {data.canRefresh&&<button type="button" className="work-button" onClick={()=>void refresh()} disabled={busy} aria-busy={busy}>{refreshing?'Checking GitHub…':'Check GitHub now'}</button>}</div>
    <p className="district-data-source-status" role="status">{districtDataSummary(data)}</p>
    <p className="district-data-source-explanation">Terrier checks GitHub when opening or changing a district and while the app is open. Queries use verified files on this computer. Downloaded updates wait for the usual district transition or a reopen, so work already on screen stays in place.</p>
    {progress&&<div className="district-data-source-progress"><progress aria-label="GitHub district data download" {...(data.receivedBytes!==undefined&&data.totalBytes!==undefined?{value:data.receivedBytes,max:data.totalBytes}:{})}/>
      {data.receivedBytes!==undefined&&<span>{dataBytes(data.receivedBytes)}{data.totalBytes!==undefined?` of ${dataBytes(data.totalBytes)}`:' received'}</span>}</div>}
    <dl className="district-data-source-facts">
      <div><dt>Source</dt><dd>GitHub release{data.releaseTag&&<> · <a href={`https://github.com/pjg93-222/Geo/releases/tag/${encodeURIComponent(data.releaseTag)}`} rel="noreferrer">{data.releaseTag}</a></>}</dd></div>
      <div><dt>On this computer</dt><dd>{data.cached?'Verified local cache':data.phase==='ready'?'Downloaded and verified':'No existing verified cache'}</dd></div>
      {data.catalogueSequence!==undefined&&<div><dt>Dataset revision</dt><dd>{data.catalogueSequence}</dd></div>}
      <div><dt>Last checked</dt><dd>{data.checkedAt?<time dateTime={data.checkedAt}>{new Date(data.checkedAt).toLocaleString()}</time>:'Not checked yet'}</dd></div>
      {data.verifiedAt&&<div><dt>Files verified</dt><dd><time dateTime={data.verifiedAt}>{new Date(data.verifiedAt).toLocaleString()}</time></dd></div>}
    </dl>
    {refreshError&&<p className="district-data-source-error" role="alert">The GitHub check could not be started. Try again.</p>}
  </section>;
}
