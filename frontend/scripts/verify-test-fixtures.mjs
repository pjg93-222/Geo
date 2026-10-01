import {build} from 'esbuild';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {join} from 'node:path';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../',import.meta.url));
const out=join(root,'../_build/test-dummy-verification');await mkdir(out,{recursive:true});
const bundled=join(out,'fixture-engine.mjs');
await build({entryPoints:[join(root,'src/v3/api/fixtures.ts')],bundle:true,platform:'node',format:'esm',outfile:bundled});
globalThis.fetch=async url=>{
  assert.ok(String(url).startsWith('/fixtures/v3/'),'Only synthetic fixture files may be read');
  return new Response(await readFile(join(root,'public',String(url))),{headers:{'content-type':'application/json'}});
};
const f=await import(pathToFileURL(bundled));
const report={profiles:0,fields:0,layers:{},checks:[]};
for(let i=0;i<200;i++){
  const hh=1+(i*223)%45000;const id=f.personId(hh,0);const p=await f.fixtureProfile(id);
  assert.deepEqual(p,await f.fixtureProfile(id),'Deterministic profile');
  assert.equal(p.groups.reduce((n,g)=>n+g.n_fields,0),40);
  assert.equal(p.household.members.length,p.household_size);
  assert.ok(['cua','part','soi'].every(g=>p.header.grade>=p.cards[g].grade),'Overall grade cannot exceed the lowest component grade');
  assert.equal(p.household.members.filter(m=>m.self).length,1);
  for(const group of p.groups){
    const sum=group.fields.reduce((n,r)=>n+r.contribution,0);
    const value=group.id==='lean'?p.cards.lean.lean:p.cards[group.id].score;
    assert.ok(Math.abs(sum-value)<1e-6,`Field contributions reconcile: ${group.id}`);
    for(const r of group.fields){assert.ok(r.exposure>=0&&r.exposure<=1);assert.ok(r.p===null||(r.p>=0&&r.p<=1));report.fields++;}
    if(group.id==='lean'){
      for(const r of group.fields){
        assert.ok(r.exposure===0||r.exposure===1,'Opinion responses cannot be fractionally back-fitted');
        assert.equal(r.status,'observed');assert.equal(r.p,null);
        if(r.field!=='svi_party_registration')assert.equal(r.value,r.exposure===1?'Yes':'No');
      }
      assert.equal(group.fields.filter(r=>r.field.startsWith('sci_ct_political_outlk_')).reduce((n,r)=>n+r.exposure,0),1,'Exactly one raw outlook category');
    }
  }
  assert.ok(Math.abs(p.header.pct-(.4*p.cards.cua.pct+.35*p.cards.part.pct+.25*p.cards.soi.pct))<1e-8);
  const h=await f.fixtureHousehold(f.householdId(hh));
  assert.equal(h.n_people,p.household_size);assert.equal(h.mean_pct,p.household.mean_pct);
  report.profiles++;
}
await assert.rejects(()=>f.fixtureProfile('p_00000000'));
await assert.rejects(()=>f.fixtureProfile(f.personId(45001,0)));
await assert.rejects(()=>f.fixtureProfile(f.personId(1,9)));
const q={reg:'all',grade:'ABCD',lean:'all',balanced:false,level:'',area:'',after:'',dir:'next'};
const anchor=await f.fixtureProfile(f.personId(19,0));
const first=await f.fixturePeople(q);assert.equal(first.total,f.fixtureTotals().people);
const second=await f.fixturePeople({...q,after:first.next});
assert.ok(!second.rows.some(r=>first.rows.some(a=>r.person_id===a.person_id)));
assert.deepEqual((await f.fixturePeople({...q,after:second.prev,dir:'prev'})).rows,first.rows);
assert.equal((await f.fixturePeople({...q,balanced:true})).total,100);
assert.deepEqual(await f.fixtureProfile(anchor.person_id),anchor,'Loading the full cohort or balanced list never re-scales a profile');
for(const level of ['block','precinct','tract','zip']){
  const a=JSON.parse(await readFile(join(root,`public/fixtures/v3/areas/${level}.json`)));
  const g=JSON.parse(await readFile(join(root,`public/fixtures/v3/geometry/${level}.fine.json`)));
  assert.equal(new Set(a.ids).size,a.ids.length);assert.ok(a.status.every(s=>s==='ok'));
  assert.ok(a.n.every(n=>Number.isInteger(n)&&n>=1));
  const ids=new Set(g.features.map(x=>x.properties.id));assert.ok(a.ids.every(id=>ids.has(id)));
  for(const cell of Object.values(a.groups))for(const column of Object.values(cell)){
    assert.equal(column.length,a.ids.length);assert.ok(column.every(Number.isFinite));
  }
  if(level!=='zip'){assert.equal(a.n.reduce((s,n)=>s+n,0),f.fixtureTotals().people);assert.equal(a.households.reduce((s,n)=>s+n,0),f.fixtureTotals().households);}
  report.layers[level]={areas:a.ids.length,minimumPeople:Math.min(...a.n),withheld:0};
}
report.checks=['200 deterministic profiles','8000 fields and contributions','raw categorical opinion responses without back-fitting','opinion/profile invariance after cohort and balanced selection','40/35/25 composition','household reconciliation','invalid IDs rejected','complete dummy population and reversible pagination','100-row balanced view','all four no-floor synthetic layers with geometry'];
report.totals=f.fixtureTotals();
await writeFile(join(out,'fixture-checks.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
