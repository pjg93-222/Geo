import {useEffect,useMemo,useRef,useState} from 'react';
import type {ReactNode} from 'react';
import {queryAudienceStreets} from '../api/audience.ts';
import type {AudienceOptions,AudienceStreets} from '../api/audience.ts';
import type {PublicPoint} from '../api/publicStreets.ts';
import {NO_MATCHING_DOORS,VOTER_BASIS,VOTER_NOTE,VOTER_SCOPE_WARNING,VOTER_ZIP,filterInWords,groupVoterStreets,voterExportFiles} from '../lib/audienceGroups.ts';
import type {AudienceFilter,VoterExportInfo} from '../lib/audienceGroups.ts';
import {buildZip,downloadZip} from '../lib/walkZip.ts';
import {AudienceFilterControls} from './AudienceFilter.tsx';
const count=(n:number)=>n.toLocaleString('en-US');
const voterDoors=(n:number)=>`${count(n)} voter ${n===1?'door':'doors'}`;
const message=(e:unknown)=>e instanceof Error?e.message:'Voter-file counts could not be loaded.';
const DEBOUNCE_MS=300;

/** Voter-file mode: the Audience filter, per-street matching doors for the drawn area, and groups formed from them. */
export function VoterAudience({options,polygon,filter,onFilter,minText,maxText,limits,onNote}:{
  options:AudienceOptions;polygon:PublicPoint[]|null;filter:AudienceFilter;onFilter:(f:AudienceFilter)=>void;minText:string;maxText:string;limits:ReactNode;onNote:(text:string)=>void;
}){
  const [result,setResult]=useState<{polygon:PublicPoint[];filter:AudienceFilter;value:AudienceStreets}|null>(null),[loading,setLoading]=useState(false),[error,setError]=useState(''),[retry,setRetry]=useState(0);
  const epoch=useRef(0),filterKey=JSON.stringify(filter);
  // Any filter or area change asks again after a short pause: one request in flight, and an answer for an earlier question is dropped.
  useEffect(()=>{
    if(!polygon)return;
    const mine=++epoch.current,ac=new AbortController();setLoading(true);setError('');
    const timer=setTimeout(()=>{
      const s=filter.support;
      queryAudienceStreets({polygon,include:filter.include,exclude:filter.exclude,support:{low:s.low,high:s.high,include_no_score:s.includeNoScore}},ac.signal).then(value=>{
        if(ac.signal.aborted||mine!==epoch.current)return;
        setResult({polygon,filter,value});setLoading(false);
      }).catch(e=>{if(!ac.signal.aborted&&mine===epoch.current){setResult(null);setError(message(e));setLoading(false);}});
    },DEBOUNCE_MS);
    return ()=>{clearTimeout(timer);ac.abort();};
  },[polygon,filterKey,retry]);
  const data=result&&result.polygon===polygon?result:null;
  const grouping=useMemo(()=>{
    try{
      if(!/^\d+$/.test(minText)||!/^\d+$/.test(maxText))throw new Error('Enter whole-number minimum and maximum voter doors.');
      return {value:data?groupVoterStreets(data.value.streets,Number(minText),Number(maxText)):null,error:''};
    }catch(e){return {value:null,error:message(e)};}
  },[data,minText,maxText]);
  const groups=grouping.value,totals=data?.value.totals,coverage=data?.value.support_coverage??options.support_coverage;
  function download(){
    if(!data||!groups?.groups.length)return;
    const info:VoterExportInfo={district:options.district,filter:filterInWords(data.filter,options),polygon:data.polygon,min:Number(minText),max:Number(maxText),generatedAt:new Date().toISOString(),totals:data.value.totals,supportCoverage:data.value.support_coverage};
    try{downloadZip(VOTER_ZIP,buildZip(voterExportFiles(groups,info)));}catch(e){onNote(message(e));}
  }
  return <div className="voter-audience">
    <p className="public-street-basis"><strong>{VOTER_BASIS}.</strong> Filter: {filterInWords(filter,options)}.</p>
    <AudienceFilterControls options={options} filter={filter} onChange={onFilter} supportCoverage={coverage}/>
    {limits}
    <div aria-live="polite">{loading&&<p role="status">Counting voter doors for this filter…</p>}</div>
    {error&&<p className="public-street-alert" role="alert">{error} <button type="button" onClick={()=>setRetry(n=>n+1)}>Retry count</button></p>}
    {data&&totals&&<div aria-busy={loading}>
      <ul className="voter-totals" role="status">
        <li><strong>{voterDoors(totals.matching_doors)}</strong> match the filter</li>
        <li>{voterDoors(totals.voter_doors)} in the drawn area</li>
        <li>{voterDoors(totals.dropped_by_exclude)} dropped by exclude in mixed households</li>
        <li>{count(totals.unplaced)} unplaced (no map position, not grouped)</li>
      </ul>
      {!data.value.streets.length&&<p>No streets with voters were found in this area.</p>}
      {grouping.error&&<p role="alert" className="public-street-alert">{grouping.error}</p>}
      {groups&&<>
        {!!groups.zero.length&&<details className="voter-zero"><summary>{NO_MATCHING_DOORS}: {count(groups.zero.length)} streets, not grouped</summary><p>{groups.zero.map(s=>s.label).join('; ')}</p></details>}
        {!!groups.oversized.length&&<p className="public-street-alert">Over maximum and unassigned: {groups.oversized.map(s=>`${s.label} (${voterDoors(s.doors)})`).join('; ')}. Raise the limit or define smaller source street entries.</p>}
        {groups.reason&&<p className="public-street-alert">{groups.reason}</p>}
        {!!groups.leftovers.length&&<p className="public-street-alert">Unassigned balance: {groups.leftovers.map(s=>`${s.label} (${voterDoors(s.doors)})`).join('; ')}. These streets are listed in not-grouped.csv, not in a group file.</p>}
        <ol className="public-street-groups">{groups.groups.map((g,i)=><li key={i}><strong>Group {i+1} · {voterDoors(g.doors)}</strong> <span className="voter-badge">Voter file</span><p>{g.streets.map(s=>s.label).join(' · ')}</p>{g.regrouped&&<small>Formed from leftovers; streets may be far apart.</small>}</li>)}</ol>
        {!groups.groups.length&&<p>No groups meet the selected limits.</p>}
        <p className="public-street-alert">{VOTER_NOTE}</p>
        <p className="public-street-alert">{VOTER_SCOPE_WARNING}</p>
        <button type="button" className="public-street-download" onClick={download} disabled={!groups.groups.length||loading}>Download voter-file street groups ZIP</button>
      </>}
    </div>}
  </div>;
}
