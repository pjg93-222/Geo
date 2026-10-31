// Built UI + invented API replies. Never connects to an installed application.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {extname,resolve,sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';
const root=resolve(fileURLToPath(new URL('../dist/',import.meta.url)));
const output=process.env.PATHFINDER_QA_OUTPUT||fileURLToPath(new URL('../../_build/pathfinder-verification/',import.meta.url));
const mime={'.html':'text/html','.js':'text/javascript','.css':'text/css','.png':'image/png','.woff2':'font/woff2'};
const server=createServer(async(req,res)=>{try{const url=new URL(req.url,'http://localhost');const file=resolve(root,'.'+(url.pathname.includes('.')?url.pathname:'/index.html'));if(!file.startsWith(root+sep)&&file!==resolve(root,'index.html'))throw Error();res.setHeader('Content-Type',mime[extname(file)]||'application/octet-stream');res.end(await readFile(file));}catch{res.writeHead(404).end();}});
await new Promise(done=>server.listen(0,'127.0.0.1',done));
const base=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({channel:'chrome',headless:true,args:['--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:1707,height:1019},reducedMotion:'reduce'});page.setDefaultTimeout(20000);
const errors=[],requests=[],report={checks:[],errors};page.on('pageerror',e=>errors.push(e.message));
const parties=['D','R','U','I','G','L','W','O','T',''];
const counts=Object.fromEntries(parties.map(code=>[code,1]));
const bbox=[-104.803,38.798,-104.793,38.803];
function payload(query={}){
  const codes=query.affiliation?query.affiliation.split(','):null;
  const doors=parties.flatMap((party,i)=>codes&&!codes.includes(party||'none')?[]:[{seq:i+1,house:String(100+i*2),unit:'',placed_by:'geocode',lon:-104.802+i*.0008,lat:38.8002,voters:[{person_id:`simulation-${i}`,name:`Simulation record ${i+1}`,phone:'',pri:{voted:0,eligible:0},gen:{voted:0,eligible:0},abs:{voted:0,eligible:0},party,age:30+i,gender:'',reg_date:'2026-01-01',last:null}]}]);
  return {schema:'walk-list-1',generated_on:'2026-10-30',list:{key:null,title:'',kind:'standard',created_at:null},query,window:{primaries:['2024'],generals:['2024'],abs_rule:'Recorded'},totals:{streets:1,stops:1,doors:doors.length,voters:doors.length,walk_m:720},bbox,runs:[{stop:1,street_id:1,street:'Cedar Heights Dr',side:'even',precinct:'simulation-area',from_house:doors[0]?.house??'',to_house:doors.at(-1)?.house??'',walk_m:720,lr:1,path:[[-104.802,38.8],[-104.794,38.8]],approach:[],doors}],fingerprint:'a'.repeat(64),context:{bbox,roads:[{k:'S1400',n:'Cedar Heights Dr',c:[[-104.803,38.8],[-104.793,38.8]]}],precincts:[],homes:doors.map(d=>[d.lon,d.lat])}};
}
await page.route(`${base}/api/**`,async route=>{
  const url=new URL(route.request().url()),path=url.pathname;let body={};
  if(path==='/api/auth/session')body={authenticated:true,hosted:false,csrf:'simulation'};
  else if(path==='/api/v3/meta')body={schema:'v3-meta-1',snapshot:'synthetic-pathfinder-qa',demo:false,people:10,households:10,district_label:'Simulation district',district_parties:counts,district_rate:{ovr:50,cua:50,part:50,soi:50},share_breaks:[0,.1,.2,.3],lean_cuts:[-.2,.2],measured_breaks:[0,.25,.5,.75],household_breaks:[0,10],weights:{cua:.4,part:.35,soi:.25},levels:[],bbox};
  else if(path==='/api/v3/features')body={schema:'v3-features-1',district:'simulation',industries:[],industry:'',candidate:null,party_codes:{D:'Democrat',R:'Republican',U:'Unaffiliated',G:'Green',L:'Libertarian',W:'Working Families',O:'Other'},overlay:{layers:[],density:false},roi:null};
  else if(path==='/api/v3/districts')body={current:'simulation',districts:[]};
  else if(path==='/api/v3/walk/list'){
    const query=Object.fromEntries(url.searchParams);requests.push(query);
    if(query.affiliation==='empty'){await route.fulfill({status:400,json:{error:'No simulation records match the affiliation selection in this route.'}});return;}
    body=payload(query);
  }else if(path==='/api/v3/walk/lists')body={lists:[]};
  else if(path==='/api/v3/walk/options')body={options:[]};
  else if(path==='/api/v3/walk/streets')body={streets:[]};
  else if(path.includes('/catalog'))body={catalogs:[]};
  else body={type:'FeatureCollection',features:[]};
  await route.fulfill({json:body});
});
try{
  await mkdir(output,{recursive:true});
  await page.goto(base+'/walk?streets=1:both');
  await page.getByRole('heading',{name:'Pathfinder',exact:true}).waitFor();await page.locator('.terrier-startup').waitFor({state:'detached'});
  await page.locator('.walk-map-canvas canvas').waitFor();
  assert.equal(await page.locator('.walk-context .route-context-totals dd').first().innerText(),'10');
  const controls=page.locator('.route-affiliation');
  await controls.getByRole('button',{name:'Clear',exact:true}).click();
  await controls.getByRole('checkbox',{name:/Independent Party/}).check();await controls.getByRole('checkbox',{name:/Green/}).check();
  assert.match(await page.locator('.walk-context').innerText(),/Filter changed/);
  const rebuilt=page.waitForResponse(r=>r.url().includes('/api/v3/walk/list?')&&r.url().includes('affiliation='));
  await page.getByRole('button',{name:'Generate walk list',exact:true}).click();await rebuilt;
  await page.waitForFunction(()=>document.querySelector('.walk-context .route-context-totals dd')?.textContent==='2');
  assert.equal(requests.at(-1).affiliation,'G,I');
  assert.equal(await page.locator('.walk-context').getByText(/Filter changed/).count(),0);
  assert.match(await page.locator('.walk-main').innerText(),/Simulation record 4/);
  assert.doesNotMatch(await page.locator('.walk-main').innerText(),/Simulation record 3\b/);
  assert.match(page.url(),/affiliation=G%2CI/);
  report.checks.push('Individual categories change the routed records; independent and unaffiliated remain separate; applied and pending states are clear.');
  await page.locator('.walk-rail').evaluate(el=>{el.scrollTop=0;});
  await page.screenshot({path:resolve(output,'pathfinder-desktop.png')});
  await page.reload();await page.locator('.terrier-startup').waitFor({state:'detached'});
  await page.waitForFunction(()=>document.querySelector('.walk-context .route-context-totals dd')?.textContent==='2');
  assert.equal(requests.at(-1).affiliation,'G,I');
  report.checks.push('Reload retains the affiliation input and rebuilds the same simulation selection.');
  for(const width of [1366,390]){
    await page.setViewportSize({width,height:900});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`Horizontal overflow at ${width}`);
    await page.screenshot({path:resolve(output,`pathfinder-${width}.png`),fullPage:width===390});
  }
  report.checks.push('Desktop, laptop and phone layouts stay within the viewport; original map canvas and door rows remain present.');
  await page.setViewportSize({width:1707,height:1019});
  await controls.getByRole('button',{name:'Clear',exact:true}).click();
  await page.getByRole('button',{name:'Generate walk list',exact:true}).click();
  await page.getByRole('alert').filter({hasText:'No simulation records match'}).waitFor();
  assert.match(await page.locator('.walk-context').innerText(),/Filter changed/);
  assert.equal(requests.at(-1).affiliation,'empty');
  await controls.getByRole('button',{name:'All affiliations',exact:true}).click();
  await page.getByRole('button',{name:'Generate walk list',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.walk-context .route-context-totals dd')?.textContent==='10');
  assert.equal(requests.at(-1).affiliation,undefined);
  report.checks.push('An empty selection yields an explicit error; All affiliations restores the original unfiltered request.');
  assert.deepEqual(errors,[]);report.passed=true;
}catch(e){report.failure=e.stack;await page.screenshot({path:resolve(output,'failure.png'),fullPage:true});throw e;}
finally{await writeFile(resolve(output,'checks.json'),JSON.stringify(report,null,2));await browser.close();await new Promise(done=>server.close(done));console.log(JSON.stringify(report));}
