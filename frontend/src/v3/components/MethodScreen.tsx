import {PageHeader} from './Header';
import type {Meta} from '../types';
import {int} from '../lib/format';

export function MethodScreen({meta}:{meta:Meta|null}){
  const w=meta?.weights;
  return <div className="screen method-screen">
    <PageHeader meta={meta} active="method"/>
    <main className="method">
      <h1 className="page-title">Method</h1>
      <p className="demo-note">Terrier reads the active local dataset. Missing fields remain unavailable; the application does not create replacement people, contacts or responses.</p>
      <p className="page-sub">{meta?`${int(meta.people)} people · ${int(meta.households)} households`:' '}</p>
      <section><h2 className="section-title">Overall standing</h2><p>Overall standing combines three indices: {w?.cua??40}% Credit Union Alignment, {w?.part??35}% Participation and {w?.soi??25}% Sphere of Influence. Sphere of Influence is 45% giving, 45% volunteering and 10% household. Recession sensitivity counts as a positive. The existing local scores and field values are included.</p>{meta?.overall_note&&<p>{meta.overall_note}</p>}</section>
      <section><h2 className="section-title">Standings and ranges</h2><p>Standings are percentiles within the stored population. Person ranges come from the local score store. Area values aggregate the active snapshot and use its stored uncertainty. Area shares are shrunk toward the parent tract or district where the framework specifies it.</p></section>
      <section><h2 className="section-title">Political views</h2><p>Registration, affiliation and outlook remain separate categorical fields. They are not combined into a displayed belief score, standardized, ranked, or included in overall standing. The Outlook filter uses stored outlook responses. The registration map shows the largest registration group, with category proportions in the detail panel; Mixed means a tie.</p><p>Missing categories display as Unknown and conflicting positive responses as Multiple responses. No missing opinion is assigned a middle position. Differences between registration, affiliation and outlook are preserved. Ethical beliefs are not part of this framework.</p></section>
      <section><h2 className="section-title">Field evidence</h2><p>The framework has 40 fields: 12 in Credit Union Alignment, 10 in Participation, 7 in Sphere of Influence and 11 political categories. Profiles retain the stored values and their available evidence details. Political fields show no score contribution. Data grades and measured percentages summarize field coverage.</p></section>
      <section><h2 className="section-title">Households</h2><p>Household membership and locations come from the local household store. Listed members are people in the dataset, which may not contain every household resident. Supplied household facts are preserved; membership is not rewritten to force them to agree.</p></section>
      <section><h2 className="section-title">Areas</h2><p>The map uses the active snapshot’s registration geography, public boundaries and local road data. Areas with fewer than {meta?.floors.min_n??20} people or {meta?.floors.min_households??10} households are withheld. Grades belong to people and indices, not areas.</p></section>
    </main>
  </div>;
}
