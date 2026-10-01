// Method page (not rendered in the approved set; tokens only). Plain neutral text of what the screens show.
import {PageHeader} from './Header';
import type {Meta} from '../types';
import {int} from '../lib/format';

export function MethodScreen({meta}:{meta:Meta|null}){
  const w=meta?.weights;
  return <div className="screen method-screen">
    <PageHeader meta={meta} active="method"/>
    <main className="method">
      <h1 className="page-title">Method</h1>
      <p className="demo-note">Test-dummy preview. People, scores, households and household positions are generated. Public geographic boundaries and road lines provide map context.</p>
      <p className="page-sub">{meta?`${int(meta.people)} people ${'·'} ${int(meta.households)} households`:' '}</p>
      <section>
        <h2 className="section-title">Overall standing</h2>
        <p>Overall standing combines three indices: {w?.cua??40}% Credit Union Alignment, {w?.part??35}% Participation and {w?.soi??25}% Sphere of Influence. Sphere of Influence is 45% giving, 45% volunteering and 10% household. Recession sensitivity counts as a positive.</p>
      </section>
      <section>
        <h2 className="section-title">Likely ranges</h2>
        <p>Each standing carries a likely range, shown as a band on a 0 to 100 track with a tick at the standing. Area statistics are generated independently and do not summarize the displayed individual profiles. Their likely ranges use the synthetic spread. They are not estimates about residents.</p>
      </section>
      <section>
        <h2 className="section-title">Political views</h2>
        <p>Registration, affiliation and outlook are separate categorical responses. They are not combined into a displayed belief score, standardized, ranked, or included in overall standing. The Outlook filter uses the original response. The registration map shows the largest registration group, with category proportions in the detail panel; Mixed means a tie.</p>
        <p>These test profiles currently supply every opinion response. Missing or conflicting categories display as Unknown or Multiple responses; no missing response is assigned a middle position. Ethical beliefs are not collected or scored in this preview. The other three indices retain their existing synthetic score and percentile conventions.</p>
      </section>
      <section>
        <h2 className="section-title">Measured and estimated</h2>
        <p>Each person has 40 framework fields: 12 in Credit Union Alignment, 10 in Participation, 7 in Sphere of Influence and 11 in Political views. A field is observed or estimated; estimated values are shown with their probability. Opinion fields display their original responses without score contributions. % measured is an illustrative evidence indicator in this test data.</p>
      </section>
      <section>
        <h2 className="section-title">Areas</h2>
        <p>Blocks, precincts, tracts and ZIPs are built on the registration basis. Every synthetic area is shown, including areas containing one test person. No minimum-count floor applies to these generated fixtures. Grades exist for each person and each index; they are not given to areas.</p>
      </section>
    </main>
  </div>;
}
