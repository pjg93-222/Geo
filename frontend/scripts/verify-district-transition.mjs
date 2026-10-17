// Browser regression against built assets and synthetic API replies; no stored people or district records are read.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {extname,resolve,sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';

const root=fileURLToPath(new URL('../dist/',import.meta.url));
const output=fileURLToPath(new URL('../../_build/district-transition-verification/',import.meta.url));
const types={'.html':'text/html','.js':'text/javascript','.css':'text/css','.png':'image/png','.mp4':'video/mp4','.woff2':'font/woff2'};
const server=createServer(async(req,res)=>{
  try{
    const path=new URL(req.url,'http://localhost').pathname;
    const target=resolve(root,'.'+(extname(path)?path:'/index.html'));
    if(!target.startsWith(root.endsWith(sep)?root:root+sep)){res.writeHead(403);res.end();return;}
    const data=await readFile(target),range=/bytes=(\d+)-(\d*)/.exec(req.headers.range??'');
    const headers={'Content-Type':types[extname(target)]??'application/octet-stream','Cache-Control':'no-store'};
    if(range){const first=Number(range[1]),last=Math.min(Number(range[2]||data.length-1),data.length-1);res.writeHead(206,{...headers,'Content-Range':`bytes ${first}-${last}/${data.length}`});res.end(data.subarray(first,last+1));}
    else{res.writeHead(200,headers);res.end(data);}
  }catch{res.writeHead(404);res.end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const base=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({channel:'chrome',headless:true,args:['--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:1440,height:900}});page.setDefaultTimeout(15000);
const errors=[];page.on('pageerror',error=>errors.push(error.message));
const report={checks:[],errors};
const deferred=()=>{let release;const promise=new Promise(resolve=>release=resolve);return {promise,release};};
let sessionGate=deferred(),metaGate=deferred(),panelGate=null,contactGate=null,failMeta=false,target='co-05',candidate=false;
const districts=[{id:'co-05',label:'Synthetic Colorado',short_label:'CO-05',state:'CO',port:0,ready:true,center:[-104.8,38.8],zoom:10},{id:'or-05',label:'Synthetic Oregon',short_label:'OR-05',state:'OR',port:0,ready:true,center:[-104.8,38.8],zoom:10}];
await page.addInitScript(({districts})=>{
  window.__switchCalls=[];window.__readyCount=0;
  window.desktop={
    districts:async()=>({schema:'districts-1',current:sessionStorage.getItem('qa.district')||'co-05',districts}),
    switchDistrict:id=>{window.__switchCalls.push(id);return new Promise((resolve,reject)=>{window.__resolveSwitch=resolve;window.__rejectSwitch=reject;});},
    rendererReady:async()=>{window.__readyCount++;},
  };
},{districts});
const meta=()=>({schema:'v3-meta-1',snapshot:'synthetic-check',demo:false,people:5,households:5,district_label:districts.find(d=>d.id===target).label,district_rate:{ovr:50,cua:50,part:50,soi:50},share_breaks:[0,.1,.2,.3],lean_cuts:[-.2,.2],measured_breaks:[0,.25,.5,.75],household_breaks:[0,10],weights:{cua:.4,part:.35,soi:.25},levels:['block','precinct'],bbox:[-104.81,38.79,-104.79,38.81]});
const score={mean:[50],se:[1],measured:[.5],pct:[50],top10:[.1],top10_se:[.01],top10_pct:[50]};
const areas=level=>({schema:'v3-areas-1',snapshot:'synthetic-check',basis:'registration',level,ids:['qa-area'],labels:['Synthetic area'],status:['ok'],n:[5],households:[5],parent:[null],precinct:[null],tract:[null],blocks:[1],groups:{ovr:score,cua:score,part:score,soi:score,lean:{mean:[0],se:[.1],measured:[.5],share_d:[.4],share_r:[.4],share_u:[.2]}}});
const geometry={type:'FeatureCollection',features:[{type:'Feature',properties:{id:'qa-area',lx:-104.8,ly:38.8},geometry:{type:'Polygon',coordinates:[[[-104.805,38.795],[-104.795,38.795],[-104.795,38.805],[-104.805,38.805],[-104.805,38.795]]]}}]};
await page.route(`${base}/api/**`,async route=>{
  const url=new URL(route.request().url()),path=url.pathname;let body={};
  if(path==='/api/auth/session'){if(sessionGate)await sessionGate.promise;body={authenticated:true,hosted:false,csrf:'qa'};}
  else if(path==='/api/v3/meta'){if(metaGate)await metaGate.promise;if(failMeta){await route.fulfill({status:503,json:{error:'Synthetic metadata failure'}});return;}body=meta();}
  else if(path==='/api/v3/features')body={schema:'v3-features-1',district:target,industries:[],industry:'',candidate:null,party_codes:{},overlay:{density:false,layers:['ovr','cua','part','soi','lean',...(candidate?['untapped']:[])]},roi:null};
  else if(path==='/api/v3/untapped'){
    const level=url.searchParams.get('level');if(level==='precinct'&&panelGate)await panelGate.promise;
    body={schema:'v3-untapped-1',level,ids:['qa-area'],labels:['Synthetic area'],people:[5],knocked:[0],undecided:[1],supporter:[1],undecided_i:[.5],supporter_i:[.5],defaults:{wp:.5,wi:.5},t_source:{sai:0,record:0,even:0},results:{version:'qa',people_knocked:0}};
  }
  else if(path.includes('/areas/'))body=areas(path.includes('/precinct.')?'precinct':'block');
  else if(path.includes('/parties/'))body={ids:['qa-area'],parties:[{}]};
  else if(path.includes('/geometry/'))body=geometry;
  else if(path==='/api/v3/contacts'){if(contactGate)await contactGate.promise;body={rows:[],total:0,page:1,pages:1,coverage:{records:0,email:0,phone:0,wireless:0}};}
  else if(path==='/api/v3/walk/lists')body={lists:[]};
  else if(path==='/api/v3/walk/options')body={options:[]};
  else if(path==='/api/v3/walk/streets')body={streets:[]};
  else body={type:'FeatureCollection',features:[]};
  await route.fulfill({json:body});
});
try{
  const media=await fetch(base+'/brand/terrier-loading-hq.mp4',{headers:{Range:'bytes=0-31'}});assert.equal(media.status,206);assert.equal((await media.arrayBuffer()).byteLength,32);
  await page.goto(base+'/method');
  await page.locator('.terrier-startup video').waitFor();
  await page.waitForFunction(()=>document.querySelector('.terrier-startup video')?.readyState>=2);
  const quality=await page.locator('.terrier-startup video').evaluate(v=>({width:v.videoWidth,display:v.getBoundingClientRect().width,muted:v.muted,inline:v.playsInline,loop:v.loop}));
  assert.equal(quality.width,960);assert.ok(quality.width>=quality.display*2);assert.ok(quality.muted&&quality.inline&&quality.loop);
  await mkdir(output,{recursive:true});await page.screenshot({path:resolve(output,'startup.png')});
  assert.match(await page.locator('.terrier-startup').innerText(),/Terrier/);
  assert.equal(await page.getByText('Checking your session…',{exact:true}).count(),0);
  await page.evaluate(()=>{window.__startupVideo=document.querySelector('.terrier-startup video');window.__startupVideo.pause();});
  sessionGate.release();sessionGate=null;
  await page.waitForRequest('**/api/v3/meta');
  assert.equal(await page.evaluate(()=>window.__startupVideo===document.querySelector('.terrier-startup video')),true);
  assert.equal(await page.locator('.app-stage').getAttribute('inert'),'');
  metaGate.release();metaGate=null;
  await page.locator('.terrier-startup').waitFor({state:'detached'});await page.locator('.method').waitFor();
  assert.equal(await page.evaluate(()=>window.__readyCount),1);
  report.checks.push('One branded animation spans delayed auth and metadata; paused video does not delay ready content.');

  await page.getByLabel('District',{exact:true}).selectOption('or-05');
  await page.getByRole('status',{name:'Opening Synthetic Oregon…',exact:true}).waitFor();
  assert.equal(await page.locator('#root').getAttribute('inert'),'');
  assert.equal(await page.locator('#root').getAttribute('aria-hidden'),'true');
  assert.equal(await page.getByLabel('District',{exact:true}).isDisabled(),true);
  await page.evaluate(()=>document.querySelector('.district-select').dispatchEvent(new Event('change',{bubbles:true})));
  assert.deepEqual(await page.evaluate(()=>window.__switchCalls),['or-05']);
  await page.evaluate(()=>window.__rejectSwitch(new Error('Synthetic host failure')));
  await page.getByRole('alert').filter({hasText:'The district could not be opened'}).waitFor();
  assert.equal(await page.locator('.terrier-startup').count(),0);assert.equal(await page.getByLabel('District',{exact:true}).inputValue(),'co-05');
  assert.equal(await page.locator('#root').getAttribute('inert'),null);
  await page.getByLabel('District',{exact:true}).selectOption('or-05');await page.evaluate(()=>window.__resolveSwitch(false));
  await page.getByRole('alert').filter({hasText:'District change cancelled'}).waitFor();
  report.checks.push('Switch cover hides old data, disables duplicate switches, and resets cleanly on rejection or cancellation.');

  metaGate=deferred();target='or-05';
  await page.getByLabel('District',{exact:true}).selectOption('or-05');
  const navigation=page.waitForEvent('domcontentloaded');
  await page.evaluate(()=>{sessionStorage.setItem('qa.district','or-05');window.__resolveSwitch(true);setTimeout(()=>location.assign('/walk'),0);});
  await navigation;await page.locator('.terrier-startup').waitFor();
  assert.equal(await page.locator('.app-stage').getAttribute('inert'),'');
  metaGate.release();metaGate=null;
  await page.locator('.terrier-startup').waitFor({state:'detached'});await page.locator('.walk-screen').waitFor();
  assert.equal(await page.getByLabel('District',{exact:true}).inputValue(),'or-05');assert.equal(await page.evaluate(()=>window.__readyCount),1);
  report.checks.push('Successful same-window document navigation retains branded startup until destination content is ready.');

  contactGate=deferred();await page.goto(base+'/contacts');await page.waitForRequest('**/api/v3/contacts*');
  assert.equal(await page.locator('.terrier-startup').count(),1);contactGate.release();contactGate=null;
  await page.locator('.terrier-startup').waitFor({state:'detached'});await page.getByText('No matching contacts',{exact:true}).waitFor();
  report.checks.push('Destination contact data readiness controls reveal, including a valid empty result.');

  candidate=true;panelGate=deferred();await page.goto(base+'/');
  await page.waitForFunction(()=>typeof window.__cuTiming?.map==='number');
  assert.equal(await page.locator('.terrier-startup').count(),1,'late side panel must keep startup covered after map paint');
  panelGate.release();panelGate=null;
  await page.locator('.terrier-startup').waitFor({state:'detached'});await page.locator('.up-list').waitFor();
  assert.equal(await page.evaluate(()=>window.__readyCount),1);
  report.checks.push('Untapped map and late panel readiness are separate: map paint alone never reveals an unfinished district.');

  failMeta=true;await page.goto(base+'/method');await page.getByRole('button',{name:'Retry',exact:true}).waitFor();
  assert.equal(await page.locator('.terrier-startup').count(),0);failMeta=false;
  await page.getByRole('button',{name:'Retry',exact:true}).click();await page.locator('.method').waitFor();
  report.checks.push('Metadata failures reveal retry controls; retry recovers.');
  await page.emulateMedia({reducedMotion:'reduce'});await page.goto(base+'/method');await page.locator('.method').waitFor();await page.locator('.terrier-startup').waitFor({state:'detached'});
  assert.equal(await page.locator('video').count(),0);assert.deepEqual(errors,[]);
  report.checks.push('Reduced motion uses still image and waits only for data.');
  report.passed=true;
}catch(error){report.failure=error.stack;console.error(await page.locator('body').innerText().catch(()=>''));throw error;}
finally{
  sessionGate?.release();metaGate?.release();panelGate?.release();contactGate?.release();
  await mkdir(output,{recursive:true});await writeFile(resolve(output,'checks.json'),JSON.stringify(report,null,2));
  await browser.close();await new Promise(resolve=>server.close(resolve));console.log(JSON.stringify(report));
}
