import {useEffect, useState} from 'react';
import './credit-quality.css';

type Metrics = {areas:number;mae_pp:number;within_10pp:number};
type Quality = {
  records:number;audited_at:string;test_tracts:number;
  fields:{label:string;field:string;valid:number;missing:number;invalid:number;zero:number;coverage:number}[];
  models:{key:string;label:string;model:string;baseline_mae_pp:number;metrics:Metrics;larger_areas:Metrics}[];
};
const number = (value:number) => new Intl.NumberFormat('en-US').format(value);
const percent = (value:number) => (value*100).toFixed(1)+'%';

export function CreditQuality({snapshot}:{snapshot:string}) {
  const [data,setData] = useState<Quality|null>(null);
  const [error,setError] = useState('');
  const [retry,setRetry] = useState(0);
  useEffect(()=>{
    const controller = new AbortController();
    setData(null);setError('');
    fetch('/api/credit-quality?'+new URLSearchParams({snapshot}), {signal:controller.signal,credentials:'same-origin'})
      .then(async response=>{const value=await response.json();if(!response.ok)throw new Error(value.error||'The credit audit could not be loaded.');return value;})
      .then(setData).catch(e=>{if(e.name!=='AbortError')setError(e.message);});
    return ()=>controller.abort();
  },[snapshot,retry]);
  return <section className="audit-section credit-quality" aria-labelledby="credit-quality-title">
    <h2 id="credit-quality-title">Credit-field readiness</h2>
    <p>The map shows supplied area summaries. Missing values remain unknown; model results below describe an experimental area-level comparison.</p>
    {error?<div role="alert"><p>{error}</p><button onClick={()=>setRetry(value=>value+1)}>Retry credit audit</button></div>:!data?<p role="status">Loading the credit-field audit…</p>:<>
      <h3>Source completeness</h3>
      <p>{number(data.records)} source records checked. Zero is a valid code and is counted separately from a blank.</p>
      <div className="quality-table-scroll" tabIndex={0} role="region" aria-label="Credit-field completeness table">
        <table><thead><tr><th scope="col">Field</th><th scope="col" className="numeric">Valid records</th><th scope="col" className="numeric">Missing</th><th scope="col" className="numeric">Invalid codes</th><th scope="col" className="numeric">Completeness</th></tr></thead>
          <tbody>{data.fields.map(field=><tr key={field.field}><th scope="row">{field.label}<small>{field.field}</small></th><td className="numeric">{number(field.valid)}</td><td className="numeric">{number(field.missing)}</td><td className="numeric">{number(field.invalid)}</td><td className="numeric">{percent(field.coverage)}</td></tr>)}</tbody></table>
      </div>
      <h3>Area models compared with the baseline</h3>
      <p>Gradient boosting using housing, dwelling and recession aggregates, geography and public Census data. The baseline predicts the training median for every area. Lower average error is better.</p>
      <div className="quality-table-scroll" tabIndex={0} role="region" aria-label="Area model validation table">
        <table><thead><tr><th scope="col">Field</th><th scope="col" className="numeric">Test areas</th><th scope="col" className="numeric">Baseline error</th><th scope="col" className="numeric">Model error</th><th scope="col" className="numeric">Within ±10 points</th></tr></thead>
          <tbody>{data.models.map(model=><tr key={model.key}><th scope="row">{model.label}</th><td className="numeric">{number(model.metrics.areas)}</td><td className="numeric">{model.baseline_mae_pp.toFixed(2)} pp</td><td className="numeric">{model.metrics.mae_pp.toFixed(2)} pp</td><td className="numeric">{percent(model.metrics.within_10pp)}</td></tr>)}</tbody></table>
      </div>
      <p className="quality-caption">pp = percentage points of an area’s positive-code share. This measures area estimates, not individual-cell accuracy. The {data.test_tracts}-tract test split was reused; it is not a fresh blind test.</p>
      <details><summary>Larger areas and interpretation</summary>
        {data.models.map(model=><p key={model.key}><strong>{model.label}:</strong> among {number(model.larger_areas.areas)} test areas with at least 100 source records, average error was {model.larger_areas.mae_pp.toFixed(2)} points and {percent(model.larger_areas.within_10pp)} fell within ±10 points.</p>)}
        <p>Models were selected using geographic folds within the training data. These tests use observed targets. Accuracy at genuinely missing values remains unknown, and predictions have not replaced source values.</p>
      </details>
      <p className="quality-caption">Source-code audit: {new Date(data.audited_at).toLocaleDateString()}. Valid codes follow the observed source domains; vendor behavioral meanings remain unverified.</p>
    </>}
  </section>;
}
