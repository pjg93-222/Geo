import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

// Walk list checks against the SYNTHETIC world of tools/make_walk_world.py, served on 8768 (TERRIER_URL overrides):
//   <venv python> tools/make_walk_world.py <world>
//   CU_DATA_DIR=<world> CU_DEMO=0 CU_COOKIE_PREFIX=cu_walk_ <venv python> manage.py serve --port 8768
//   node frontend/scripts/verify-walk-list.mjs
// The run saves a list and a re-knock list and records results, so it refuses the real app (8767) and any data set that is
// not small; rebuild the world with --force to start clean. Synthetic screenshots go to _build/walk-list/ next to checks.json.
// Each check passes or fails on its own (a check whose prerequisite failed is listed under "skipped"); exit code 1 on any failure.
const output=new URL('../../_build/walk-list/',import.meta.url);await mkdir(output,{recursive:true});
const base=(process.env.TERRIER_URL||'http://127.0.0.1:8768').replace(/\/+$/,'');
const report={checks:[],failures:[],skipped:[],pageErrors:0,apiRequests:0,fixtureRequests:0,walk:{}};
const written=()=>writeFile(new URL('checks.json',output),JSON.stringify(report,null,2));
if(new URL(base).port==='8767'){
  const message='Refusing to run against port 8767: this script saves lists and records results. Use the synthetic world on 8768.';
  report.failures.push({check:'setup',message});await written();console.error(message);process.exit(2);
}

const work=await mkdtemp(join(tmpdir(),'walk-list-'));
const browser=await chromium.launch({channel:'chrome',headless:true,args:['--enable-unsafe-swiftshader']});
const context=await browser.newContext({viewport:{width:1440,height:900},reducedMotion:'reduce',acceptDownloads:true});
const page=await context.newPage();page.setDefaultTimeout(30000);
page.on('pageerror',()=>report.pageErrors++);
page.on('request',r=>{const path=new URL(r.url()).pathname;if(path.startsWith('/fixtures/'))report.fixtureRequests++;if(path.startsWith('/api/'))report.apiRequests++;});
// Every dialog the page opens is accepted (a beforeunload prompt then lets the navigation go on) and listed with the step it came in. A step
// that expects one takes it off the list (reload() below); any left at the end were not expected and fail the run.
const dialogs=[];let current='';
page.on('dialog',d=>{dialogs.push({type:d.type(),step:current});d.accept().catch(()=>{});});

// ---- helpers ----------------------------------------------------------------------------------------------------------------
const DESKTOP={width:1440,height:900},PHONE={width:375,height:812};
const COLUMNS=['Address','Name','Phone','Pri','Gen','Abs','Party','Age','Gender','Reg. date','Result'];
const CSV_NEEDS=['Address','Name','Phone','Pri','Gen','Abs','Party','Age','Gender','Result'];   // in this order, other columns may sit between
const code=c=>new RegExp(`\\(${c}\\)$|^${c}$`);                  // "Supporter (S)" or a bare "S"
const pathOf=r=>new URL(r.url()).pathname;
const isGet=(r,path)=>pathOf(r)===path&&r.request().method()==='GET';
const isPost=(r,path)=>pathOf(r)===path&&r.request().method()==='POST';
// the options request for one level, optionally for one parent (the request carries ss, and for precincts sh, of the parent chosen)
const isOptions=(level,parent={})=>r=>{
  const q=new URL(r.url()).searchParams;
  return isGet(r,'/api/v3/walk/options')&&q.get('level')===level&&Object.entries(parent).every(([k,v])=>q.get(k)===v);
};
const count=(n,one)=>`${n.toLocaleString('en-US')} ${one}${n===1?'':'s'}`;
const collapse=a=>a.filter((v,i)=>i===0||v!==a[i-1]);
const flat=p=>p.runs.flatMap(run=>run.doors.flatMap(door=>door.voters.map(v=>({...v,house:door.house,unit:door.unit,seq:door.seq,stop:run.stop}))));
const text=s=>s.replace(/\s+/g,' ').trim();
const api=async path=>{const r=await context.request.get(base+path);assert.ok(r.ok(),`GET ${path} answered ${r.status()}`);return r.json();};
const until=async(fn,what,ms=20000)=>{const end=Date.now()+ms;for(;;){const v=await fn();if(v)return v;if(Date.now()>end)throw new Error('Timed out waiting for '+what);await page.waitForTimeout(100);}};
const shot=async name=>{try{await page.screenshot({path:fileURLToPath(new URL(name,output)),fullPage:true});}catch{/* a screenshot is only evidence */}};
const fits=()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1&&document.body.scrollWidth<=innerWidth+1);
// The refused marks and the refusal note of the tab (lib/walkRefused.ts). A step that should not meet another step's refusals forgets them,
// then loads a page (a page already loaded keeps its own copy in memory).
const REFUSED_KEY='terrier.walk.refused.v1';
const refusedKept=()=>page.evaluate(k=>{const raw=sessionStorage.getItem(k);return {session:raw===null?null:JSON.parse(raw),local:localStorage.getItem(k)};},REFUSED_KEY);
const forgetRefused=()=>page.evaluate(k=>sessionStorage.removeItem(k),REFUSED_KEY);
// Reloads the page; answers with the types of the dialogs that came with it (a beforeunload prompt is accepted)
async function reload(){
  const n=dialogs.length;
  await page.reload();await heading().waitFor();
  return dialogs.splice(n).map(d=>d.type);
}

const heading=()=>page.getByRole('heading',{name:'Build a walk list',exact:true});
const generateButton=()=>page.getByRole('button',{name:'Generate walk list',exact:true});
const tables=()=>page.getByRole('table');
const groups=()=>page.getByRole('radiogroup',{name:/^Result for /});
const heads=()=>tables().evaluateAll(ts=>ts.map(t=>[...t.querySelectorAll('thead th')].map(th=>th.textContent.replace(/\s+/g,' ').trim())));
// voter rows only: the rows of eleven cells (a footnote row of one wide cell is skipped)
const rows=()=>tables().evaluateAll(ts=>ts.map(t=>[...t.querySelectorAll('tbody tr')].map(tr=>[...tr.children]).filter(c=>c.length===11)
  .map(c=>c.map(td=>td.textContent.replace(/\s+/g,' ').trim()))));

// one street from the search box into the chips
async function addStreet(query,label){
  await page.getByRole('combobox',{name:'Add a street',exact:true}).fill(query);
  await page.getByRole('listbox',{name:'Streets',exact:true}).getByRole('option',{name:new RegExp('^'+label)}).first().click();
  await page.getByRole('button',{name:new RegExp('^Remove '+label)}).first().waitFor();
}
// Generate and hand back the list the screen was given; the table of the first stop is on screen when this returns
async function generate(){
  const [response]=await Promise.all([page.waitForResponse(r=>isGet(r,'/api/v3/walk/list')),generateButton().click()]);
  assert.equal(response.status(),200,'The list request failed');
  const payload=await response.json();
  await tables().first().waitFor();
  return payload;
}
// set one voter's result; a result already shown is toggled off first so the click always records a change
async function choose(group,c){
  const radio=group.getByRole('radio',{name:code(c)});
  if(await radio.getAttribute('aria-checked')==='true')await radio.click();
  await radio.click();
  await until(async()=>(await radio.getAttribute('aria-checked'))==='true',`${c} to show as chosen`);
}
const stripBom=s=>s.charCodeAt(0)===0xfeff?s.slice(1):s;            // the download starts with a UTF-8 byte order mark
// RFC 4180 reader for the downloaded CSV
function csvRows(src){
  const out=[];let row=[],cell='',quoted=false;
  for(let i=0;i<src.length;i++){
    const ch=src[i];
    if(quoted){if(ch!=='"')cell+=ch;else if(src[i+1]==='"'){cell+='"';i++;}else quoted=false;}
    else if(ch==='"')quoted=true;
    else if(ch===','){row.push(cell);cell='';}
    else if(ch==='\n'||ch==='\r'){if(ch==='\r'&&src[i+1]==='\n')i++;row.push(cell);cell='';if(row.some(c=>c!==''))out.push(row);row=[];}
    else cell+=ch;
  }
  if(cell!==''||row.length){row.push(cell);out.push(row);}
  return out;
}

const done=new Set();
async function step(name,fn,needs=[]){
  const missing=needs.filter(n=>!done.has(n));
  if(missing.length){report.skipped.push({check:name,needs:missing});return;}
  current=name;
  try{
    const said=await fn();
    done.add(name);report.checks.push(said);
  }catch(error){
    report.failures.push({check:name,type:error.name,message:text(String(error.message).split('\n')[0]).slice(0,300)});
    await shot('failed-'+name.replace(/\W+/g,'-')+'.png');
  }
}

let listing=null;     // the list payload the screen is showing after "generate"
let savedKey='';      // the key of the list saved by the first recorded result
let reknockKey='';    // the key of the re-knock list made from it
const csvPath=join(work,'streets.csv');
// a matched row, a street with two places, one that matches nothing, and a second matched row
await writeFile(csvPath,'street\r\nCedar Heights Drive\r\nRidgeview Court\r\nPine St\r\nZzyzx Rd\r\n');

try{
  // ---- the synthetic world is what we are talking to ----------------------------------------------------------------------
  const meta=await api('/api/v3/meta');
  assert.ok(!meta.demo&&meta.source==='live'&&meta.people>0&&meta.people<100000,'Not the synthetic walk world: this script runs only against a small data set in real mode.');
  report.walk.people=meta.people;
  const probe=await context.request.get(base+'/api/v3/walk/options?level=ss');
  assert.ok(probe.ok(),`The walk API is not available: GET /api/v3/walk/options?level=ss answered ${probe.status()}.`);
  await page.goto(base+'/walk');
  await heading().waitFor();

  // ---- 1. the tab and the heading ------------------------------------------------------------------------------------------
  await step('tab',async()=>{
    const nav=page.getByRole('navigation',{name:'Sections',exact:true});
    const tab=nav.getByRole('link',{name:'Walk list',exact:true});
    await tab.waitFor();
    assert.equal(await tab.getAttribute('aria-current'),'page');
    assert.equal(await nav.locator('[aria-current="page"]').innerText(),'Walk list');
    assert.equal(await nav.getByRole('link').last().innerText(),'Walk list','Walk list is the last tab');
    assert.equal(new URL(page.url()).pathname,'/walk');
    assert.equal(await page.title(),'Terrier');
    return 'Walk list is the last tab, active at /walk';
  });
  await step('heading',async()=>{
    await page.getByRole('heading',{level:1,name:'Build a walk list',exact:true}).waitFor();
    await page.getByText('Pick an area, add streets, generate.',{exact:true}).waitFor();
    return 'Heading "Build a walk list" and its one-line instruction are on the page';
  });

  // ---- 2. Senate -> House -> Precinct ------------------------------------------------------------------------------------
  await step('cascade',async()=>{
    const ss=page.getByLabel('Senate district',{exact:true}),sh=page.getByLabel('House district',{exact:true}),pr=page.getByLabel('Precinct',{exact:true});
    const loaded=async(select,options)=>until(async()=>await select.isEnabled()&&(await select.locator('option').count())===options.length+1,'choices to load');
    const top=(await api('/api/v3/walk/options?level=ss')).options;
    assert.ok(top.length>0,'The server lists no Senate districts');
    await loaded(ss,top);
    const [houses]=await Promise.all([page.waitForResponse(isOptions('sh',{ss:top[0].id})).then(r=>r.json()),ss.selectOption({index:1})]);
    assert.equal(await ss.inputValue(),top[0].id);
    assert.ok(houses.options.length>0,'No House districts inside the chosen Senate district');
    await loaded(sh,houses.options);
    assert.ok((await sh.locator('option').nth(1).textContent()).includes(houses.options[0].label),'House choice shows the server label');
    const [precincts]=await Promise.all([page.waitForResponse(isOptions('precinct',{ss:top[0].id,sh:houses.options[0].id})).then(r=>r.json()),sh.selectOption({index:1})]);
    assert.ok(precincts.options.length>0,'No precincts inside the chosen House district');
    await loaded(pr,precincts.options);
    await pr.selectOption({index:1});
    assert.equal(await pr.inputValue(),precincts.options[0].id);
    await ss.selectOption('');                                     // back to every area for the street checks
    await until(async()=>(await sh.inputValue())===''&&(await pr.inputValue())==='','House and Precinct to reset');
    return `Senate (${top.length}) -> House (${houses.options.length}) -> Precinct (${precincts.options.length}) choices load from the server and reset with their parent`;
  });

  // ---- 3. street search -> chip ----------------------------------------------------------------------------------------------
  await step('search',async()=>{
    const box=page.getByRole('combobox',{name:'Add a street',exact:true});
    await box.fill('C');
    await page.getByRole('status').filter({hasText:'Type at least 2 letters'}).waitFor();     // one letter: the search says what it needs
    await box.fill('');
    const [found]=await Promise.all([page.waitForResponse(r=>isGet(r,'/api/v3/walk/streets')).then(r=>r.json()),
      page.getByRole('combobox',{name:'Add a street',exact:true}).fill('Cedar')]);
    assert.ok(found.streets.some(s=>s.label==='Cedar Heights Dr'),'Search for "Cedar" does not offer Cedar Heights Dr');
    await page.getByRole('listbox',{name:'Streets',exact:true}).getByRole('option',{name:/^Cedar Heights Dr/}).first().click();
    await page.getByRole('button',{name:/^Remove Cedar Heights Dr/}).first().waitFor();
    return 'One letter shows "Type at least 2 letters"; searching "Cedar" offers Cedar Heights Dr; choosing it adds a chip';
  });

  // ---- 4. CSV upload: matched, pick a place, no match; resolving the pick ---------------------------------------------------
  await step('upload',async()=>{
    const [chooser]=await Promise.all([page.waitForEvent('filechooser'),page.getByRole('button',{name:'Upload CSV',exact:true}).click()]);
    await chooser.setFiles(csvPath);
    await page.getByText(/streets\.csv · 4 rows/).first().waitFor();
    const row=(name,state)=>page.getByRole('listitem').filter({hasText:name}).filter({hasText:state});
    await row('Cedar Heights Drive',/\b\d+ doors?\b/).first().waitFor();
    await row('Ridgeview Court',/\b\d+ doors?\b/).first().waitFor();
    await row('Zzyzx Rd',/No match/).first().waitFor();
    const pine=row('Pine St','Pick a place').first();
    await pine.waitFor();
    const places=await pine.getByRole('button').allInnerTexts();
    assert.ok(places.some(t=>/Colorado Springs/.test(t))&&places.some(t=>/Fountain/.test(t)),'Pine St should offer Colorado Springs and Fountain');
    await pine.getByRole('button',{name:/Colorado Springs/}).click();
    await row('Pine St',/\b\d+ doors?\b/).first().waitFor();
    assert.equal(await page.getByText('Pick a place',{exact:true}).count(),0,'The pick is still open');
    await page.getByRole('button',{name:/^Remove Pine St/}).first().waitFor();
    return 'CSV upload: two matched rows with door counts, Pine St asks for a place (Colorado Springs or Fountain) and the pick adds the street, Zzyzx Rd says No match';
  });

  // ---- 5. generate: summary, one panel per stop, columns, walk order, map -------------------------------------------------
  await step('generate',async()=>{
    listing=await generate();
    assert.equal(listing.schema,'walk-list-1');
    assert.match(listing.fingerprint??'',/^[0-9a-f]{64}$/,'the list carries the fingerprint of what it shows');
    const t=listing.totals,doors=listing.runs.flatMap(r=>r.doors),voters=flat(listing);
    assert.ok(listing.runs.length>0&&voters.length>0,'The list is empty');
    assert.equal(t.stops,listing.runs.length,'totals.stops');
    assert.equal(t.doors,doors.length,'totals.doors');
    assert.equal(t.voters,voters.length,'totals.voters');
    assert.deepEqual(doors.map(d=>d.seq),doors.map((_,i)=>i+1),'door seq runs 1..n in walking order');
    const line=[count(t.streets,'street'),count(t.stops,'stop'),count(t.doors,'door'),count(t.voters,'voter')].join(' · ');
    const summaries=await page.getByText(/\d+ streets? · \d+ stops? · \d+ doors? · \d+ voters?/).allInnerTexts();
    assert.ok(summaries.some(s=>s.includes(line)),`The summary line does not read "${line}"`);
    assert.equal(await tables().count(),listing.runs.length,'One panel per stop');
    Object.assign(report.walk,{streets:t.streets,stops:t.stops,doors:t.doors,voters:t.voters,walk_m:t.walk_m});
    return `Generate: "${line}", one panel per stop (${listing.runs.length})`;
  });
  await step('columns and order',async()=>{
    for(const h of await heads())assert.deepEqual(h,COLUMNS,'Column headings');
    const shown=(await rows()).flat(),voters=flat(listing);
    assert.equal(shown.length,voters.length,'One row per voter');
    assert.deepEqual(shown.map(r=>r[1]),voters.map(v=>text(v.name)),'Names are not in the order of the list');
    assert.deepEqual(collapse(shown.map(r=>r[0].split(' ')[0]).filter(Boolean)),collapse(voters.map(v=>v.house)),'Houses are not in walking order');
    for(const r of shown){
      assert.ok(r[1]&&!/sentinel/i.test(r[1]),'A voter has no usable name');
      for(const i of [3,4,5])assert.match(r[i],/^(\d+\/\d+|\/)$/,'Pri, Gen and Abs read voted/eligible, or a lone slash');
      if(r[2])assert.match(r[2],/^\(\d{3}\) \d{3}-\d{4}$/,'Phone format');
    }
    assert.ok(shown.some(r=>r[2]),'No voter shows a phone');
    for(const run of listing.runs)assert.ok(['even','odd'].includes(run.side),'Each run is one side of a street');
    const stops=await page.getByText(/^Stop \d+ of \d+/).count();
    assert.ok(stops>=listing.runs.length,'Each panel says which stop it is');
    return `Every panel has Address...Result; ${shown.length} voters appear in the order of the list with names, phones and tallies read from the stores`;
  },['generate']);
  await step('map',async()=>{
    const map=page.getByRole('img',{name:/map/i}).or(page.getByRole('region',{name:/map/i})).first();
    await map.waitFor();
    await page.locator('canvas').first().waitFor();
    assert.equal(await page.getByText(/could not be (drawn|loaded)/).count(),0,'The map did not load');
    const party=page.getByRole('button',{name:'Party',exact:true}),doors=page.getByRole('button',{name:'Doors',exact:true});
    await party.click();
    await until(async()=>(await party.getAttribute('aria-pressed'))==='true','Party shading');
    await doors.click();
    await until(async()=>(await doors.getAttribute('aria-pressed'))==='true','Doors shading');
    report.walk.markers=await page.locator('.maplibregl-marker').count();
    assert.ok(report.walk.markers>0,'No numbered stop markers on the map');
    await shot('desktop-list.png');
    return 'The map region draws (canvas, stop markers) and switches between Doors and Party shading';
  },['generate']);

  // ---- 6. exports ----------------------------------------------------------------------------------------------------------------
  await step('pdf',async()=>{
    const [download]=await Promise.all([page.waitForEvent('download',{timeout:90000}),page.getByRole('button',{name:'Export PDF',exact:true}).click()]);
    const file=join(work,'list.pdf');await download.saveAs(file);
    const bytes=await readFile(file);
    assert.equal(bytes.subarray(0,4).toString('latin1'),'%PDF','Not a PDF');
    assert.ok(bytes.length>2000,'The PDF is empty');
    assert.match(download.suggestedFilename(),/\.pdf$/i);
    report.walk.pdfBytes=bytes.length;
    return `Export PDF downloads ${download.suggestedFilename()} (%PDF, ${bytes.length} bytes)`;
  },['generate']);
  await step('csv',async()=>{
    const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'Download CSV',exact:true}).click()]);
    const file=join(work,'list.csv');await download.saveAs(file);
    const table=csvRows(stripBom(await readFile(file,'utf8')));
    assert.match(download.suggestedFilename(),/\.csv$/i);
    const header=table[0].map(h=>h.toLowerCase());
    let at=-1;
    for(const name of CSV_NEEDS){const i=header.indexOf(name.toLowerCase(),at+1);assert.ok(i>at,`CSV header lacks ${name} (in order)`);at=i;}
    const voters=flat(listing);
    assert.equal(table.length-1,voters.length,'One CSV row per voter');
    assert.equal(table[1][header.indexOf('name')],voters[0].name,'First CSV row is the first voter');
    return `Download CSV: ${download.suggestedFilename()} with its header row and ${voters.length} voters in walking order`;
  },['generate']);

  // ---- 7. a result persists in the saved list --------------------------------------------------------------------------------
  await step('result persists',async()=>{
    const before=new Set((await api('/api/v3/walk/lists')).lists.map(l=>l.key));
    const voter=flat(listing)[0];
    const saving=page.waitForResponse(r=>isPost(r,'/api/v3/walk/lists'));
    const posted=page.waitForResponse(r=>isPost(r,'/api/v3/walk/dispositions'));
    await choose(groups().first(),'S');
    const save=await saving;
    assert.equal(save.request().postDataJSON().fingerprint,listing.fingerprint,'the save names the list shown by its fingerprint');
    assert.equal((await save.json()).changed,false,'the list saved is the one shown');
    assert.equal((await posted).status(),200,'Saving the result failed');
    const fresh=await until(async()=>{const l=(await api('/api/v3/walk/lists')).lists.filter(x=>!before.has(x.key));return l.length===1?l:null;},'the saved list to appear');
    savedKey=fresh[0].key;
    assert.equal(fresh[0].kind,'standard');
    await until(async()=>flat(await api('/api/v3/walk/lists/'+savedKey)).find(v=>v.person_id===voter.person_id)?.last?.code==='S','the S on file');
    await page.reload();
    const byAddress=await tables().first().waitFor({timeout:10000}).then(()=>true,()=>false);
    if(!byAddress){
      await page.getByLabel('Saved lists',{exact:true}).selectOption(savedKey);
      await tables().first().waitFor();
    }
    const radio=groups().first().getByRole('radio',{name:code('S')});
    await until(async()=>(await radio.getAttribute('aria-checked'))==='true','S to show after the reload');
    assert.equal(await tables().count(),listing.runs.length,'The saved list has the same stops');
    report.walk.reopenedByAddress=byAddress;
    return `Result S is recorded, kept in the saved list and shown again after a reload (${byAddress?'the address reopens the list':'reopened from Saved lists'})`;
  },['generate']);

  // ---- 8. Not home again -> a list of the NH voters only --------------------------------------------------------------------
  await step('not home again',async()=>{
    const voters=flat(await api('/api/v3/walk/lists/'+savedKey));         // the saved list, in the order it is on screen
    assert.ok(voters.length>=4,'The list needs four voters for this check');
    await choose(groups().nth(1),'NH');await choose(groups().nth(2),'NH');await choose(groups().nth(3),'U');
    const wanted=[voters[1],voters[2]];
    const [response]=await Promise.all([page.waitForResponse(r=>isPost(r,'/api/v3/walk/lists')),
      page.getByRole('button',{name:'Not home again',exact:true}).click()]);
    assert.equal(response.status(),201,'Creating the re-knock list failed');
    const again=await response.json();
    assert.equal(again.list.kind,'reknock');
    assert.deepEqual(flat(again).map(v=>v.person_id).sort(),wanted.map(v=>v.person_id).sort(),'The re-knock list is not exactly the Not home voters');
    await tables().first().waitFor();
    const shown=(await rows()).flat().map(r=>r[1]).sort();
    assert.deepEqual(shown,wanted.map(v=>text(v.name)).sort(),'The screen does not show the Not home voters');
    const mine=(await api('/api/v3/walk/lists')).lists.find(l=>l.key===again.list.key);
    assert.ok(mine&&mine.kind==='reknock'&&mine.voters===wanted.length,'The saved lists do not hold the re-knock list');
    report.walk.reknockVoters=wanted.length;reknockKey=again.list.key;
    return `Not home again: a new re-knock list holds only the ${wanted.length} voters marked NH (not the S or U voters)`;
  },['result persists']);

  // ---- 8b. a result whose save fails is not lost by leaving the tab; it is sent, with its visit day, on return -----------------------
  await step('result survives leaving',async()=>{
    const voter=flat(await api('/api/v3/walk/lists/'+reknockKey))[0];
    const isSave=url=>url.pathname==='/api/v3/walk/dispositions';
    const down=route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Verification source unavailable'})});
    await page.route(isSave,down);
    try{
      await choose(groups().first(),'MV');
      await page.getByRole('alert').filter({hasText:'Results not saved'}).first().waitFor();
      const nav=page.getByRole('navigation',{name:'Sections',exact:true});
      await nav.getByRole('link').first().click();                      // another tab of the app: the walk screen goes away
      await heading().waitFor({state:'detached'});
      await page.waitForTimeout(800);                                   // the send on leaving fails too: the result stays on the page's shelf
    }finally{await page.unroute(isSave,down);}
    const today=await page.evaluate(()=>{const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;});
    const [sent]=await Promise.all([page.waitForRequest(q=>new URL(q.url()).pathname==='/api/v3/walk/dispositions'&&q.method()==='POST'),
      page.getByRole('navigation',{name:'Sections',exact:true}).getByRole('link',{name:'Walk list',exact:true}).click()]);
    const body=sent.postDataJSON();
    assert.equal(body.list,reknockKey,'sent for the list it was set on');
    assert.deepEqual(body.entries,[{person_id:voter.person_id,code:'MV'}],'the result set before leaving is sent on return');
    assert.equal(body.on,today,'with the day of the visit');
    await until(async()=>{const v=flat(await api('/api/v3/walk/lists/'+reknockKey)).find(x=>x.person_id===voter.person_id);return v?.last?.code==='MV'&&v.last.list===reknockKey;},'the MV on file');
    return 'A result whose save failed survives leaving the Walk list tab and is saved on return, for its own list and visit day';
  },['not home again']);

  // ---- 8c. the first match of an uploaded file answers after a saved list replaced the picker: the answer changes nothing --------------
  await step('late file match',async()=>{
    await heading().waitFor();
    const late=join(work,'late.csv');await writeFile(late,'street\r\nJuniper Lane\r\n');
    const isMatch=url=>url.pathname==='/api/v3/walk/match';
    let release;const held=new Promise(r=>{release=r;});
    const slow=async route=>{await held;await route.continue().catch(()=>{});};
    await page.route(isMatch,slow);
    try{
      const [chooser]=await Promise.all([page.waitForEvent('filechooser'),page.getByRole('button',{name:'Upload CSV',exact:true}).click()]);
      await chooser.setFiles(late);
      await page.getByRole('button',{name:'Matching…',exact:true}).waitFor();
      await Promise.all([page.waitForResponse(r=>isGet(r,'/api/v3/walk/lists/'+savedKey)),page.getByLabel('Saved lists',{exact:true}).selectOption(savedKey)]);
      await tables().first().waitFor();
      const chips=await page.getByRole('list',{name:'Streets in this walk list'}).innerText();
      release();await page.waitForTimeout(1500);
      assert.equal(await page.getByRole('list',{name:'Streets in this walk list'}).innerText(),chips,'the late answer changed the saved list\'s streets');
      assert.equal(await page.getByRole('button',{name:/^Remove Juniper Ln/}).count(),0,'Juniper Ln was added to another list\'s picker');
      assert.equal(await page.getByText(/late\.csv/).count(),0,'the late file is shown as the upload');
      await page.getByRole('button',{name:'Upload CSV',exact:true}).waitFor();
    }finally{release();await page.unroute(isMatch,slow);}
    return 'An uploaded file still being matched when a saved list is opened adds nothing to that list\'s picker';
  },['result persists']);

  // ---- 8d. a result the server refuses stays the walker's mark, flagged and named; it does not block opening another list --------------
  await step('refused result',async()=>{
    const saved=await api('/api/v3/walk/lists/'+savedKey);
    const run=saved.runs.findLast(r=>r.doors.some(d=>d.voters.length)),door=run.doors.findLast(d=>d.voters.length);   // the last voter on screen
    const isSave=url=>url.pathname==='/api/v3/walk/dispositions';
    let posts=0;
    const refuse=route=>{posts++;return route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'Some of those voters are not on this list.'})});};
    const group=()=>groups().last();
    await page.route(isSave,refuse);
    try{
      await choose(group(),'O');
      const note=page.getByRole('alert').filter({hasText:/The server refused 1 result, so it was not saved\. Record that door again:/}).first();
      await note.waitFor();
      const said=text(await note.innerText());
      assert.ok(said.includes(`${door.house}`)&&said.includes(`${run.street} (O)`),'the note names the door and its code');
      assert.ok(said.includes('Some of those voters are not on this list.'),'the note gives the server\'s reason');
      assert.equal(await group().getByRole('radio',{name:code('O')}).getAttribute('aria-checked'),'true','the walker\'s O stays marked');
      await page.getByText('O (not saved)',{exact:true}).last().waitFor();
      const described=await group().getAttribute('aria-describedby');
      assert.ok(described&&(await page.locator(`[id="${described}"]`).innerText())==='O (not saved)','the result group is described by its "not saved" text');
      assert.equal(await page.getByRole('alert').filter({hasText:'Results not saved'}).count(),0,'a refusal offers no Retry');
      const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'Download CSV',exact:true}).click()]);
      const file=join(work,'refused.csv');await download.saveAs(file);
      const table=csvRows(stripBom(await readFile(file,'utf8'))),result=table[0].map(h=>h.toLowerCase()).indexOf('result');
      assert.equal(table.at(-1)[result],'O (not saved)','the CSV keeps the refused O, flagged');
      const before=posts;
      await Promise.all([page.waitForResponse(r=>isGet(r,'/api/v3/walk/lists/'+reknockKey)),page.getByLabel('Saved lists',{exact:true}).selectOption(reknockKey)]);
      await tables().first().waitFor();
      assert.equal(posts,before,'the refused result is not sent again');
      assert.equal(await page.getByText('Some results are not saved yet',{exact:false}).count(),0,'opening another list is blocked');
      await Promise.all([page.waitForResponse(r=>isGet(r,'/api/v3/walk/lists/'+savedKey)),page.getByLabel('Saved lists',{exact:true}).selectOption(savedKey)]);
      await tables().first().waitFor();
      await until(async()=>(await group().getByRole('radio',{name:code('O')}).getAttribute('aria-checked'))==='true','the refused O to come back with its list');
      await page.getByText('O (not saved)',{exact:true}).last().waitFor();
      await page.getByRole('button',{name:'Dismiss',exact:true}).click();
      await note.waitFor({state:'detached'});
    }finally{await page.unroute(isSave,refuse);}
    // chosen again once the server takes it: saved, so nothing flagged is left to hold the next page change up
    await group().getByRole('radio',{name:code('O')}).click();
    await until(async()=>(await page.getByText('O (not saved)',{exact:true}).count())===0,'the O to be saved when chosen again');
    return 'A refused result stays marked and flagged "not saved" (screen and CSV), the note names its door and the server\'s reason, it is not retried, another saved list opens straight away, and choosing it again saves it';
  },['not home again']);

  // ---- 8d2. a refused mark and the note come back after a reload; dismissed, the note stays gone, the flag stays, and closing does not ask ---
  await step('refused survives reload',async()=>{
    await forgetRefused();                                                // 8d's dismissed O is still kept for this list
    await page.goto(base+'/walk?list='+savedKey);await heading().waitFor();await tables().first().waitFor();
    const saved=await api('/api/v3/walk/lists/'+savedKey);
    const run=saved.runs.findLast(r=>r.doors.some(d=>d.voters.length)),door=run.doors.findLast(d=>d.voters.length),voter=door.voters.at(-1);
    const isSave=url=>url.pathname==='/api/v3/walk/dispositions';
    let posts=0;
    const refuse=route=>{posts++;return route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'Some of those voters are not on this list.'})});};
    const group=()=>groups().last(),note=page.locator('.walk-refused');
    const marked=what=>until(async()=>(await group().getByRole('radio',{name:code('U')}).getAttribute('aria-checked'))==='true',what);
    await page.route(isSave,refuse);
    try{
      await choose(group(),'U');
      await note.filter({hasText:/The server refused 1 result, so it was not saved\./}).waitFor();
      const kept=await refusedKept();
      assert.deepEqual(kept.session?.marks,{['key:'+savedKey]:{[voter.person_id]:'U'}},'the tab keeps the mark by list and voter');
      assert.match(kept.session?.note?.lists?.[0]?.doors?.[voter.person_id]??'',/\(U\)$/,'and the note, apart from the marks');
      assert.equal(kept.local,null,'nothing in localStorage');
      assert.ok(!page.url().includes(voter.person_id)&&!/refused/i.test(page.url()),'nothing in the address');
      const before=posts;
      assert.deepEqual(await reload(),['beforeunload'],'closing the page with the note up asks first');
      await tables().first().waitFor();
      await marked('the refused U to come back after the reload');
      await page.getByText('U (not saved)',{exact:true}).last().waitFor();
      await note.waitFor();
      const said=text(await note.innerText());
      assert.ok(said.startsWith('The server refused 1 result')&&said.includes(`${door.house}`)&&said.includes(`${run.street} (U)`),'the note comes back naming the door and its code');
      assert.ok(said.includes('Some of those voters are not on this list.'),'with the server\'s reason');
      await page.waitForTimeout(800);
      assert.equal(posts,before,'a refused mark is not sent again after the reload');
      await note.getByRole('button',{name:'Dismiss',exact:true}).click();
      await note.waitFor({state:'detached'});
      assert.equal((await refusedKept()).session?.note,null,'dismissing removes the note from the tab');
      assert.deepEqual(await reload(),['beforeunload'],'the list shown still has a mark flagged "not saved": the reload still asks');
      await tables().first().waitFor();
      await marked('the refused U to stay after the note was dismissed');
      await page.getByText('U (not saved)',{exact:true}).last().waitFor();
      assert.equal(await note.count(),0,'the dismissed note does not come back');
    }finally{await page.unroute(isSave,refuse);}
    // chosen again once the server takes it: saved, the flag and the tab's copy go, and closing no longer asks
    await group().getByRole('radio',{name:code('U')}).click();
    await until(async()=>(await page.getByText('U (not saved)',{exact:true}).count())===0,'the U to be saved when chosen again');
    assert.equal((await refusedKept()).session,null,'nothing left in the tab');
    assert.deepEqual(await reload(),[],'nothing flagged and no note: no prompt');
    await tables().first().waitFor();
    assert.equal(await page.getByText('U (not saved)',{exact:true}).count(),0);
    return 'A refused U and its note survive a reload (which asks first; the tab\'s sessionStorage holds the mark by list and voter and the note apart, nothing in localStorage or the address); after Dismiss a reload brings back the flag but not the note and still asks, since the list shown has a flagged mark; once the U is saved the tab holds nothing and a reload does not ask';
  },['not home again']);

  // ---- 8d3. a list save refused as out of date: the page says to reload, and the reload brings the marks back flagged ------------------
  await step('out of date survives reload',async()=>{
    await forgetRefused();
    await page.goto(base+'/walk');await heading().waitFor();
    await addStreet('Juniper','Juniper Ln');
    const shown=await generate(),voters=flat(shown);
    assert.ok(voters.length>=2,'The list needs two voters for this check');
    const stale='This page is out of date. Reload it, then save the list again.';
    const isLists=url=>url.pathname==='/api/v3/walk/lists',isSave=url=>url.pathname==='/api/v3/walk/dispositions';
    let saves=0,posts=0;
    const refuse=route=>{
      if(route.request().method()!=='POST')return route.continue();
      saves++;return route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:stale})});
    };
    const counted=route=>{posts++;return route.continue();};
    const note=page.locator('.walk-refused');
    const both=async what=>{
      await until(async()=>(await groups().first().getByRole('radio',{name:code('S')}).getAttribute('aria-checked'))==='true'
        &&(await groups().nth(1).getByRole('radio',{name:code('NH')}).getAttribute('aria-checked'))==='true',what);
      await page.getByText('S (not saved)',{exact:true}).first().waitFor();await page.getByText('NH (not saved)',{exact:true}).first().waitFor();
    };
    await page.route(isLists,refuse);await page.route(isSave,counted);
    try{
      await choose(groups().first(),'S');await choose(groups().nth(1),'NH');
      await note.filter({hasText:/The server refused 2 results, so they were not saved\./}).waitFor();
      assert.ok(text(await note.innerText()).includes(stale),'the note gives the server\'s words: reload');
      await both('both marks flagged');
      assert.ok(!new URL(page.url()).searchParams.get('list'),'the list was not saved');
      const before=saves;
      assert.deepEqual(await reload(),['beforeunload'],'the reload asks first while the note is up');
      await tables().first().waitFor();
      await both('both marks to come back flagged after the reload');
      await note.filter({hasText:/The server refused 2 results/}).waitFor();
      await page.waitForTimeout(800);
      assert.equal(saves,before,'the reload sends nothing');assert.equal(posts,0,'no result is posted');
    }finally{await page.unroute(isLists,refuse);await page.unroute(isSave,counted);}
    // marked again once the server takes the save: saved, the flag goes from the screen and from the tab; the other mark stays flagged
    const saving=page.waitForResponse(r=>isPost(r,'/api/v3/walk/lists'));
    await groups().first().getByRole('radio',{name:code('S')}).click();
    const saved=await saving;
    assert.equal(saved.status(),201,'the list is saved once the server takes it');
    const key=(await saved.json()).list.key;
    await until(async()=>(await page.getByText('S (not saved)',{exact:true}).count())===0,'the S flag to go once saved');
    await page.getByText('NH (not saved)',{exact:true}).first().waitFor();
    const kept=(await refusedKept()).session;
    assert.deepEqual(kept?.marks,{['key:'+key]:{[voters[1].person_id]:'NH'}},'the saved S has left the tab\'s storage; the NH moved with the list to its key');
    // the address now names the saved list: a reload opens it by its key, and the NH refused before the save is there, flagged
    await until(()=>new URL(page.url()).searchParams.get('list')===key,'the address to name the saved list');
    assert.deepEqual(await reload(),['beforeunload'],'the note is still up, so the reload asks first');
    await tables().first().waitFor();
    await until(async()=>(await groups().nth(1).getByRole('radio',{name:code('NH')}).getAttribute('aria-checked'))==='true','the NH to come back on the saved list');
    await page.getByText('NH (not saved)',{exact:true}).first().waitFor();
    assert.equal(await page.getByText('S (not saved)',{exact:true}).count(),0,'the saved S is not flagged');
    assert.equal(await groups().first().getByRole('radio',{name:code('S')}).getAttribute('aria-checked'),'true','the saved S comes back from the list');
    const left=text(await note.innerText());
    assert.ok(left.startsWith('The server refused 1 result')&&left.includes('(NH)')&&!left.includes('(S)'),'the saved S has left the note; the NH is still named');
    // the last flagged mark saved: the note goes by itself, the tab holds nothing, and closing does not ask
    await groups().nth(1).getByRole('radio',{name:code('NH')}).click();
    await until(async()=>(await page.getByText('NH (not saved)',{exact:true}).count())===0,'the NH to be saved when chosen again');
    await note.waitFor({state:'detached'});
    assert.equal(await page.locator('.walk-refused-bar').count(),0,'and the phone bar with it');
    assert.equal((await refusedKept()).session,null,'nothing left in the tab');
    assert.deepEqual(await reload(),[],'every refused mark saved: no prompt');
    await tables().first().waitFor();
    assert.equal(await note.count(),0,'no note after the reload');
    return 'A list save refused as out of date keeps both marks flagged with the note; the reload it asks for (after a prompt) brings both back flagged with the note and sends nothing; marking S again saves the list and takes S out of the flags, the tab and the note; the NH moves to the list\'s key and is still flagged after a reload that opens ?list=<key>; saving it too removes the note, empties the tab, and a reload does not ask';
  },['generate']);

  // ---- 8d4. a session timing out mid-canvass (the server answers 401) keeps the refused marks and the note: they are the walker's only copy -----
  //          Only Sign out forgets them (the legacy page's; the v3 app has none), and closing the tab.
  await step('session timeout keeps refusals',async()=>{
    await forgetRefused();
    await page.goto(base+'/walk?list='+savedKey);await heading().waitFor();await tables().first().waitFor();
    const saved=await api('/api/v3/walk/lists/'+savedKey);
    const voter=saved.runs.findLast(r=>r.doors.some(d=>d.voters.length)).doors.findLast(d=>d.voters.length).voters.at(-1);
    const isSave=url=>url.pathname==='/api/v3/walk/dispositions',isLists=url=>url.pathname==='/api/v3/walk/lists';
    const refuse=route=>route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'Some of those voters are not on this list.'})});
    let timedOut=0;
    const expired=route=>{if(route.request().method()!=='GET')return route.continue();timedOut++;return route.fulfill({status:401,contentType:'application/json',body:JSON.stringify({error:'Sign in to continue'})});};
    const note=page.locator('.walk-refused');
    const flagged=async what=>{
      await until(async()=>(await groups().last().getByRole('radio',{name:code('MV')}).getAttribute('aria-checked'))==='true',what);
      await page.getByText('MV (not saved)',{exact:true}).last().waitFor();
    };
    await page.route(isSave,refuse);
    try{
      await choose(groups().last(),'MV');
      await note.waitFor();
      await page.route(isLists,expired);                                    // the reload's look at the saved lists finds the session timed out
      assert.deepEqual(await reload(),['beforeunload'],'the note is up: the reload asks first');
      await tables().first().waitFor();
      await until(()=>timedOut>0,'the 401 on the saved lists');
      await page.waitForTimeout(500);
      assert.deepEqual((await refusedKept()).session?.marks,{['key:'+savedKey]:{[voter.person_id]:'MV'}},'the 401 keeps the mark in the tab');
      await note.waitFor();
      assert.ok(text(await note.innerText()).includes('(MV)'),'and the note');
      await flagged('the flagged MV to stay after the 401');
    }finally{await page.unroute(isSave,refuse);await page.unroute(isLists,expired);}
    // the walker signs in again (here: the session is back) and chooses it again: saved, nothing kept, and closing does not ask
    await groups().last().getByRole('radio',{name:code('MV')}).click();
    await until(async()=>(await page.getByText('MV (not saved)',{exact:true}).count())===0,'the MV to be saved when chosen again');
    await note.waitFor({state:'detached'});
    assert.equal((await refusedKept()).session,null,'nothing left in the tab');
    assert.deepEqual(await reload(),[],'nothing flagged and no note: no prompt');
    await tables().first().waitFor();
    return 'A 401 on the saved lists during a reload (a session timing out mid-canvass) keeps the refused MV in the tab, with its note and its "not saved" flag; once it is chosen again and saved, nothing is kept and a reload does not ask';
  },['not home again']);

  // ---- 8e. the voter data changed between showing a list and saving it: the saved list is shown with a note, and a mark for a voter
  //          it no longer has is refused and named, never sent. The save answer is edited here as a rebuild that dropped the last voter would. ----
  await step('list changed before saving',async()=>{
    await page.goto(base+'/walk');await heading().waitFor();
    await addStreet('Juniper','Juniper Ln');
    const shown=await generate(),voters=flat(shown),keep=voters[0],drop=voters.at(-1);
    assert.ok(voters.length>=2&&keep.person_id!==drop.person_id,'The list needs two voters for this check');
    const isLists=url=>url.pathname==='/api/v3/walk/lists';
    let release,asked=null;const held=new Promise(r=>{release=r;});
    const changed=async route=>{
      if(route.request().method()!=='POST')return route.continue();
      asked=route.request().postDataJSON();
      const response=await route.fetch(),saved=await response.json();
      await held;
      for(const run of saved.runs)for(const door of run.doors)door.voters=door.voters.filter(v=>v.person_id!==drop.person_id);
      for(const run of saved.runs)run.doors=run.doors.filter(d=>d.voters.length);
      saved.runs=saved.runs.filter(r=>r.doors.length);
      Object.assign(saved.totals,{stops:saved.runs.length,doors:saved.runs.flatMap(r=>r.doors).length,voters:flat(saved).length});
      return route.fulfill({status:201,contentType:'application/json',body:JSON.stringify({...saved,changed:true})});
    };
    const sent=[];const watch=r=>{if(pathOf(r)==='/api/v3/walk/dispositions'&&r.method()==='POST')sent.push(...r.postDataJSON().entries.map(e=>e.person_id));};
    page.on('request',watch);await page.route(isLists,changed);
    try{
      await choose(groups().first(),'S');
      await until(()=>asked,'the list to be saved');
      assert.equal(asked.fingerprint,shown.fingerprint,'the save names the list shown');
      await choose(groups().last(),'NH');                                  // marked while the save is on its way
      release();
      const replaced='The voter data was updated since this list was shown; showing the saved version.';
      await page.locator('p.walk-note[role="status"]',{hasText:replaced}).waitFor();            // the note on screen (the announcement says it too)
      assert.equal(await page.locator('.walk-sr[role="status"]').innerText(),replaced,'the change is announced');
      const note=page.getByRole('alert').filter({hasText:'the saved list does not have these voters'}).first();
      await note.waitFor();
      assert.ok(text(await note.innerText()).startsWith('1 result was not saved:'),'the page refused it, so the note does not say the server did');
      const door=shown.runs.flatMap(r=>r.doors.map(d=>({...d,street:r.street}))).find(d=>d.voters.some(v=>v.person_id===drop.person_id));
      assert.ok(text(await note.innerText()).includes(`${door.street} (NH)`),'the note names the door of the dropped voter with the mark');
      assert.equal((await rows()).flat().length,voters.length-1,'the saved list is on screen');
      assert.equal(await groups().first().getByRole('radio',{name:code('S')}).getAttribute('aria-checked'),'true','the mark of a voter still on the list stays');
      await until(()=>sent.includes(keep.person_id),'the S to be sent');
      await page.waitForTimeout(800);
      assert.ok(!sent.includes(drop.person_id),'the mark of the voter the saved list lacks is never sent');
      assert.equal(await page.getByRole('alert').filter({hasText:'Results not saved'}).count(),0,'nothing waits for Retry');
      await page.getByRole('button',{name:'Dismiss',exact:true}).click();
    }finally{release();page.off('request',watch);await page.unroute(isLists,changed);}
    return 'A list saved after the voter data changed: the save sends the fingerprint, the saved list replaces the one shown with a note, marks on voters it still has stay and are saved, and a mark on a voter it lacks is named as not saved and never sent';
  },['generate']);

  // ---- 8f. the list saved has new person ids for the same voters (the contact stores were rebuilt): marks follow the voters by door and name ----
  //          The save answer is given new ids here; the results the page sends under them are mapped back for the server, which knows the old ones.
  await step('ids changed before saving',async()=>{
    await page.goto(base+'/walk');await heading().waitFor();
    await addStreet('Cedar','Cedar Heights Dr');
    const shown=await generate(),voters=flat(shown);
    assert.ok(voters.length>=2,'The list needs two voters for this check');
    const isLists=url=>url.pathname==='/api/v3/walk/lists',isSave=url=>url.pathname==='/api/v3/walk/dispositions';
    let release,asked=false,key='';const held=new Promise(r=>{release=r;});
    const renamed=async route=>{
      if(route.request().method()!=='POST')return route.continue();
      asked=true;
      const response=await route.fetch(),saved=await response.json();
      await held;key=saved.list.key;
      for(const run of saved.runs)for(const door of run.doors)for(const v of door.voters)v.person_id='x'+v.person_id;
      return route.fulfill({status:201,contentType:'application/json',body:JSON.stringify({...saved,changed:true})});
    };
    const sent=[];
    const back=route=>{
      const body=route.request().postDataJSON();sent.push(...body.entries.map(e=>e.person_id));
      return route.continue({postData:JSON.stringify({...body,entries:body.entries.map(e=>({...e,person_id:e.person_id.replace(/^x/,'')}))})});
    };
    await page.route(isLists,renamed);await page.route(isSave,back);
    try{
      await choose(groups().first(),'S');
      await until(()=>asked,'the list to be saved');
      await choose(groups().nth(1),'U');                                    // marked while the save is on its way
      release();
      await page.locator('p.walk-note[role="status"]',{hasText:'showing the saved version'}).waitFor();
      await until(()=>sent.includes('x'+voters[0].person_id)&&sent.includes('x'+voters[1].person_id),'both results to be sent under the new ids');
      assert.ok(sent.every(id=>id.startsWith('x')),'nothing is sent under an old id');
      assert.equal(await page.getByRole('alert').filter({hasText:'not saved'}).count(),0,'no result is refused');
      assert.equal(await groups().first().getByRole('radio',{name:code('S')}).getAttribute('aria-checked'),'true','the S stays on its voter');
      assert.equal(await groups().nth(1).getByRole('radio',{name:code('U')}).getAttribute('aria-checked'),'true','the U stays on its voter');
      await until(async()=>{const on=flat(await api('/api/v3/walk/lists/'+key));return on.find(v=>v.person_id===voters[0].person_id)?.last?.code==='S'&&on.find(v=>v.person_id===voters[1].person_id)?.last?.code==='U';},'both results on file');
    }finally{release();await page.unroute(isLists,renamed);await page.unroute(isSave,back);}
    return 'A list saved with new person ids for the same voters: both marks (one sent while the save was on its way) stay on their voters, go out under the new ids, and none is refused';
  },['generate']);

  // ---- 8g. a re-knock list whose voters the data partly no longer has says how many ------------------------------------------------
  await step('re-knock lost voters',async()=>{
    await page.goto(base+'/walk?list='+savedKey);await heading().waitFor();await tables().first().waitFor();
    const isLists=url=>url.pathname==='/api/v3/walk/lists';
    const lost=async route=>{
      if(route.request().method()!=='POST')return route.continue();
      const response=await route.fetch(),again=await response.json();
      return route.fulfill({status:201,contentType:'application/json',body:JSON.stringify({...again,missing:1})});
    };
    await page.route(isLists,lost);
    try{
      await page.getByRole('button',{name:'Not home again',exact:true}).click();
      await page.getByRole('status').filter({hasText:'1 voter marked Not home is no longer in the voter data.'}).waitFor();
    }finally{await page.unroute(isLists,lost);}
    return 'A re-knock list answered with missing: 1 says "1 voter marked Not home is no longer in the voter data."';
  },['not home again']);

  // ---- 9. phone width: no sideways page scroll, tables scroll in their own region ---------------------------------------------
  await step('mobile',async()=>{
    await page.setViewportSize(PHONE);
    try{
      await page.goto(base+'/walk');
      await heading().waitFor();
      assert.equal(await page.getByLabel('Open section',{exact:true}).inputValue(),'walk','The section menu is on Walk list');
      assert.ok(await fits(),'The builder is wider than the phone');
      await addStreet('Cedar','Cedar Heights Dr');
      const shown=await generate();
      assert.ok(await fits(),'The list makes the page wider than the phone');
      const cards=await page.evaluate(()=>{
        const W=innerWidth,bad=[];let rows=0;
        for(const wrap of document.querySelectorAll('.walk-table-wrap'))if(wrap.scrollWidth>wrap.clientWidth+1)bad.push('a table scrolls sideways');
        for(const tr of document.querySelectorAll('.walk-table tbody tr')){
          rows++;
          const row=tr.getBoundingClientRect(),name=tr.querySelector('td.walk-name'),n=name.getBoundingClientRect(),group=tr.querySelector('[role=radiogroup]');
          if(n.width===0||n.left<0||n.right>W+0.5||n.top<row.top||n.bottom>row.bottom||name.scrollWidth>name.clientWidth+1)bad.push('name hidden: '+name.textContent);
          for(const b of group.querySelectorAll('button')){
            const r=b.getBoundingClientRect();
            if(r.left<0||r.right>W+0.5||r.height<44||r.top<n.bottom-1||r.bottom>row.bottom)bad.push(`button ${b.textContent} of ${name.textContent} at ${Math.round(r.left)}-${Math.round(r.right)}, ${Math.round(r.height)} px`);
          }
        }
        return {rows,bad:bad.slice(0,5)};
      });
      assert.equal(cards.rows,flat(shown).length,'one card per voter');
      assert.deepEqual(cards.bad,[],'every name and result button is in view, below its name, buttons 44 px tall');
      assert.equal(await page.getByRole('table').count(),shown.runs.length,'each stop is still a table to a screen reader');
      assert.equal(await groups().count(),flat(shown).length,'each voter still has its named result group');
      const first=page.waitForResponse(r=>isPost(r,'/api/v3/walk/dispositions'));
      await choose(groups().first(),'NH');                                 // a tap records the result without any sideways scrolling
      assert.equal((await first).status(),200,'the first result was saved before any refusal');
      await shot('mobile-list.png');
      // a refusal far above the cards: a bar fixed to the bottom of the screen says so, points at the note, and stays clear of the last card
      const isSave=url=>url.pathname==='/api/v3/walk/dispositions';
      const refuse=route=>route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'Some of those voters are not on this list.'})});
      const note=page.locator('.walk-refused'),bar=page.getByRole('region',{name:'Results not saved',exact:true});
      const inView=r=>r.top>=0&&r.bottom<=innerHeight+0.5&&r.width>0;
      await page.route(isSave,refuse);
      try{
        await page.evaluate(()=>scrollTo(0,document.documentElement.scrollHeight));
        await choose(groups().last(),'O');
        await note.waitFor();
        await bar.waitFor();
        assert.equal(text(await bar.innerText()),'! 1 result not saved View','the bar names the count of the note');
        assert.equal(await bar.getAttribute('aria-live'),null);assert.equal(await bar.evaluate(el=>el.closest('[role=alert],[role=status],[aria-live]')),null,'the bar is not a live region');
        await page.evaluate(()=>scrollTo(0,document.documentElement.scrollHeight));
        const fixed=await bar.evaluate(el=>{const r=el.getBoundingClientRect(),b=el.querySelector('button').getBoundingClientRect();return {top:r.top,bottom:r.bottom,height:r.height,view:innerHeight,position:getComputedStyle(el).position,button:b.height};});
        assert.ok(fixed.position==='fixed'&&fixed.bottom<=fixed.view+0.5&&fixed.top>=0,'the bar is fixed within the screen');
        assert.ok(fixed.button>=44,'the View button is 44 px tall');
        const clear=await groups().last().evaluate((g,barTop)=>{const r=g.getBoundingClientRect();return {bottom:r.bottom,barTop,top:r.top};},fixed.top);
        assert.ok(clear.top>=0&&clear.bottom<=fixed.top-0.5,`the bar covers the last card's result buttons (buttons end ${Math.round(clear.bottom)}, bar starts ${Math.round(fixed.top)})`);
        assert.equal(await note.evaluate(el=>{const r=el.getBoundingClientRect();return r.bottom>0&&r.top<innerHeight;}),false,'the note is out of sight at the bottom of the page');
        await bar.getByRole('button',{name:'View',exact:true}).click();
        await until(()=>note.evaluate((el)=>{const r=el.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight&&document.activeElement===el;}),'the note to come into view with focus');
        await page.evaluate(()=>scrollTo(0,document.documentElement.scrollHeight));
        await page.setViewportSize(DESKTOP);                                 // wide again with the note up: no bar
        await note.waitFor();
        assert.equal(await page.locator('.walk-refused-bar').evaluate(el=>getComputedStyle(el).display),'none','no bar on a desktop');
        assert.equal(await bar.count(),0,'the hidden bar is not offered to a screen reader');
        await page.setViewportSize(PHONE);
        await bar.waitFor();
        await note.scrollIntoViewIfNeeded();
        await note.getByRole('button',{name:'Dismiss',exact:true}).click();
        await note.waitFor({state:'detached'});
        await bar.waitFor({state:'detached'});
        assert.equal(await page.locator('.walk-refused-bar').count(),0,'dismissing the note removes the bar');
      }finally{await page.unroute(isSave,refuse);}
      await groups().last().getByRole('radio',{name:code('O')}).click();      // saved when chosen again: nothing flagged holds the next page change up
      await until(async()=>(await page.getByText('O (not saved)',{exact:true}).count())===0,'the O to be saved when chosen again');
      return `At${PHONE.width}x${PHONE.height} the page does not scroll sideways and each of the ${cards.rows} voters is a card: name and all five 44 px result buttons in view, table roles kept; a refusal shows a bottom bar ("! 1 result not saved") clear of the last card, View brings the note into view with focus, Dismiss removes both, a desktop shows no bar`;
    }finally{await page.setViewportSize(DESKTOP);}
  });

  // ---- 9b. a save without a fingerprint is refused, in words that tell the walker what to do -------------------------------------
  await step('save needs fingerprint',async()=>{
    const before=(await api('/api/v3/walk/lists')).lists.length;
    const answer=await page.evaluate(async query=>{     // the same session token and header the page itself uses
      const {csrf}=await (await fetch('/api/auth/session',{credentials:'same-origin'})).json();
      const r=await fetch('/api/v3/walk/lists',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':csrf},
        body:JSON.stringify({title:'No fingerprint',query})});
      return {status:r.status,body:await r.json()};
    },listing.query);
    assert.equal(answer.status,400,'a list save without a fingerprint was not refused');
    assert.equal(answer.body.error,'This page is out of date. Reload it, then save the list again.','the refusal does not tell the walker to reload');
    assert.equal((await api('/api/v3/walk/lists')).lists.length,before,'a refused save still made a list');
    return 'A direct list save without a fingerprint answers 400 "This page is out of date. Reload it, then save the list again." and saves nothing';
  },['generate']);

  // ---- 10. fault injection: the list request fails, Retry recovers ---------------------------------------------------------
  await step('fault',async()=>{
    await page.goto(base+'/walk');
    await heading().waitFor();
    let failing=true;
    const isList=url=>url.pathname==='/api/v3/walk/list';            // a URL object; /walk/lists is another route
    const handler=route=>failing
      ?route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Verification source unavailable'})})
      :route.continue();
    await page.route(isList,handler);
    try{
      await addStreet('Juniper','Juniper Ln');
      await generateButton().click();
      const alert=page.getByRole('alert').filter({has:page.getByRole('button',{name:'Retry',exact:true})}).first();
      await alert.waitFor();
      assert.equal(await tables().count(),0,'A failed request left a list on screen');
      failing=false;
      await alert.getByRole('button',{name:'Retry',exact:true}).click();
      await tables().first().waitFor();
      await alert.waitFor({state:'hidden'});
    }finally{await page.unroute(isList,handler);}
    return 'A 503 on the list request shows an alert with Retry and no list; Retry recovers the list';
  });

  assert.equal(report.fixtureRequests,0);
  if(dialogs.length)report.failures.push({check:'unexpected dialogs',message:dialogs.map(d=>`${d.type} in ${d.step||'setup'}`).join('; ')});
  if(report.pageErrors)report.failures.push({check:'page errors',message:`${report.pageErrors} uncaught page error(s)`});
}catch(error){
  report.failures.push({check:'run',type:error.name,message:text(String(error.message).split('\n')[0]).slice(0,300)});
  await shot('failed-run.png');
}finally{
  await context.close();await browser.close();
  await rm(work,{recursive:true,force:true});
  if(report.failures.length)process.exitCode=1;
  await written();
  console.log(JSON.stringify(report));
}
