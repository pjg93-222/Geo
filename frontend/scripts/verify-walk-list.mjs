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

// ---- helpers ----------------------------------------------------------------------------------------------------------------
const DESKTOP={width:1440,height:900},PHONE={width:390,height:844};
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
    const [found]=await Promise.all([page.waitForResponse(r=>isGet(r,'/api/v3/walk/streets')).then(r=>r.json()),
      page.getByRole('combobox',{name:'Add a street',exact:true}).fill('Cedar')]);
    assert.ok(found.streets.some(s=>s.label==='Cedar Heights Dr'),'Search for "Cedar" does not offer Cedar Heights Dr');
    await page.getByRole('listbox',{name:'Streets',exact:true}).getByRole('option',{name:/^Cedar Heights Dr/}).first().click();
    await page.getByRole('button',{name:/^Remove Cedar Heights Dr/}).first().waitFor();
    return 'Searching "Cedar" offers Cedar Heights Dr; choosing it adds a chip';
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
    Object.assign(report.walk,{streets:t.streets,stops:t.stops,doors:t.doors,voters:t.voters,walk_m:t.walk_m,minutes:t.minutes});
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
    const posted=page.waitForResponse(r=>isPost(r,'/api/v3/walk/dispositions'));
    await choose(groups().first(),'S');
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
    return 'A refused result stays marked and flagged "not saved" (screen and CSV), the note names its door and the server\'s reason, it is not retried, and another saved list opens straight away';
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
      await generate();
      assert.ok(await fits(),'The list makes the page wider than the phone');
      const region=await tables().first().evaluate(t=>{
        for(let e=t.parentElement;e&&e!==document.body;e=e.parentElement){
          const o=getComputedStyle(e).overflowX;
          if(o==='auto'||o==='scroll')return {overflow:o,client:e.clientWidth,scroll:e.scrollWidth};
        }
        return null;
      });
      assert.ok(region,'The table sits in no scrolling region');
      assert.ok(region.scroll>region.client,'The table fits without scrolling, so the columns are not all there');
      await shot('mobile-list.png');
      return `At 390 px the page does not scroll sideways; the table scrolls in its own region (${region.scroll} px of ${region.client})`;
    }finally{await page.setViewportSize(DESKTOP);}
  });

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
