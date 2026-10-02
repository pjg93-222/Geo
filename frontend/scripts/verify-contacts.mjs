import {build} from 'esbuild';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {join} from 'node:path';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('../',import.meta.url));
const out=join(root,'../_build/contact-verification');await mkdir(out,{recursive:true});
const bundled=join(out,'contact-fixtures.mjs');
await build({entryPoints:[join(root,'src/v3/api/contact-fixtures.ts')],bundle:true,platform:'node',format:'esm',outfile:bundled});
let fail=true;const paths=[];
globalThis.fetch=async url=>{
  assert.ok(String(url).startsWith('/fixtures/v3/'),'Contact registry never reads a live source');paths.push(String(url));
  if(fail){fail=false;return new Response('',{status:503});}
  return new Response(await readFile(join(root,'public',String(url))),{headers:{'content-type':'application/json'}});
};
const f=await import(pathToFileURL(bundled));
const query=f.contactQuery(new URLSearchParams());
await assert.rejects(()=>f.getContacts(query),/missing/,'Failed loading can be retried');
const first=await f.getContacts(query);assert.equal(first.total,109150);assert.equal(first.rows.length,50);assert.equal(first.first,1);assert.equal(first.last,50);
const second=await f.getContacts({...query,page:2});assert.equal(second.first,51);assert.equal(second.last,100);
assert.ok(!first.rows.some(a=>second.rows.some(b=>a.id===b.id)),'Pages must not overlap');
assert.deepEqual(await f.getContacts(query),first,'Repeated pages are deterministic');
const last=await f.getContacts({...query,page:999999});assert.equal(last.page,last.pages);assert.equal(last.last,109150);
assert.equal(f.contactQuery(new URLSearchParams('page=Infinity')).page,1);assert.equal(f.contactQuery(new URLSearchParams('page=-1')).page,1);
assert.deepEqual(f.contactQuery(new URLSearchParams(f.contactUrl({...query,text:'Alma Ainsley',channel:'email',page:2,basis:'residential'}).split('?')[1])),{text:'Alma Ainsley',channel:'email',page:2,basis:'residential'});
for(const row of [...first.rows,...second.rows]){
  const page=await f.getContacts({...query,text:row.id});assert.equal(page.total,1);assert.deepEqual(page.rows[0],row);
  if(row.phone){const found=await f.getContacts({...query,text:row.phone.replace(/\D/g,'')});assert.ok(found.rows.every(x=>[x.phone,x.wireless].some(phone=>phone?.replace(/\D/g,'').includes(row.phone.replace(/\D/g,'')))));}
}
const email=await f.getContacts({...query,channel:'email'});assert.equal(email.total,first.coverage.email);assert.ok(email.rows.every(row=>row.email));
const missing=await f.getContacts({...query,channel:'none'});assert.equal(missing.total,first.coverage.withoutChannel);assert.ok(missing.rows.every(row=>!row.email&&!row.phone&&!row.wireless));
const zero=await f.getContacts({...query,text:'ZZZ-MISSING-CONTACT'});assert.equal(zero.total,0);assert.equal(zero.first,0);assert.equal(zero.last,0);assert.equal(zero.pages,1);
const abort=new AbortController();abort.abort();await assert.rejects(()=>f.getContacts(query,abort.signal),{name:'AbortError'});
const all=await f.exportContacts(query);assert.equal(all.count,109150);assert.ok(all.csv.startsWith('\uFEFF"Data type"'));
const lines=all.csv.trimEnd().split('\r\n');assert.equal(lines.length,109151);
const ids=new Set();const totals={email:0,phone:0,wireless:0,withoutChannel:0};
for(const line of lines.slice(1)){
  const row=[...line.matchAll(/"((?:[^"]|"")*)"/g)].map(match=>match[1].replace(/""/g,'"'));
  assert.equal(row.length,8);assert.equal(row[0],'Synthetic test dummy');assert.ok(!ids.has(row[1]));ids.add(row[1]);
  if(row[3]){assert.match(row[3],/^[a-z.]+\.p_[0-9a-z]{8}@example\.test$/);totals.email++;}
  if(row[4]){assert.match(row[4],/^\(202\) 555-01\d{2}$/);totals.phone++;}
  if(row[5]){assert.match(row[5],/^\(303\) 555-01\d{2}$/);totals.wireless++;}
  if(!row[3]&&!row[4]&&!row[5])totals.withoutChannel++;
  if(row[6])assert.match(row[6],/^Test household \d+ · Example Lane$/);
}
for(const key of Object.keys(totals))assert.equal(totals[key],first.coverage[key]);
const filtered=await f.exportContacts({...query,channel:'none'});assert.equal(filtered.count,missing.total);
assert.ok(paths.every(path=>path==='/fixtures/v3/areas/block.json'));
const report={people:ids.size,coverage:first.coverage,checks:['full population with unique profile IDs','reserved email and fictional phone namespaces','reversible pagination and page bounds','search and channel filters','CSV full and filtered totals','failed-load retry','cancellation','fixture-only source']};
await writeFile(join(out,'contacts-checks.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
