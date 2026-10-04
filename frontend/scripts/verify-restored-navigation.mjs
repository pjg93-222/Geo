import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {terrierBase} from './terrier-base.mjs';

// Navigation checks for the eight sections against the SYNTHETIC world of tools/make_walk_world.py, served on 8768 (TERRIER_URL overrides):
//   <venv python> tools/make_walk_world.py <world>
//   CU_DATA_DIR=<world> CU_DEMO=0 CU_COOKIE_PREFIX=cu_walk_ <venv python> manage.py serve --port 8768
//   node frontend/scripts/verify-restored-navigation.mjs [--screenshots]
// It only reads, but it refuses port 8767 and any data set that is not small (screenshots go to _build/restored-workspace/). The client
// is live-only: it refuses a demo-mode server, so the world runs with CU_DEMO=0 and every page calls /api/. The run fails on any /api/
// response of 400 or more. The Contact sheet and Team & snapshots pages need a world that passes the source-store checks, which the
// walk world does not, so make two changes to YOUR COPY of the world (never to real data) before serving it:
//   scores.sqlite3 metadata key w1: set status 'fixture' to 'verified' (terrier_contacts.verified_stores);
//   the active research_snapshot manifest: set source_sha256 to the contacts store's source_sha256 (terrier_workspace.workspace).
const base=terrierBase('verify-restored-navigation.mjs');
const out=fileURLToPath(new URL('../../_build/restored-workspace/',import.meta.url));await mkdir(out,{recursive:true});
const browser=await chromium.launch({channel:'chrome',headless:true,args:['--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:1440,height:900},reducedMotion:'reduce'});
const report={checks:[],errors:[],apiRequests:0,failedApi:[],screenshots:[]};
page.on('pageerror',e=>report.errors.push(e.message));
page.on('response',r=>{const path=new URL(r.url()).pathname;if(!path.startsWith('/api/'))return;report.apiRequests++;if(r.status()>=400)report.failedApi.push(r.status()+' '+path);});
const sections=[['Map','/','.railA'],['People','/people','.trow:not(.sk-row)'],['Contact sheet','/contacts','.contact-person'],['Criteria audit','/criteria','.work-table tbody tr'],['Data & sources','/sources','.work-facts dd'],['Team & snapshots','/workspace','.work-save-form'],['Method','/method','.method'],['Walk list','/walk','.walk-screen']];
async function capture(name){if(!process.argv.includes('--screenshots'))return;await page.screenshot({path:out+name+'.png',fullPage:false});report.screenshots.push(name+'.png');}
try{
  const meta=await (await fetch(base+'/api/v3/meta')).json();
  assert.ok(!meta.demo&&meta.source==='live'&&meta.people>0&&meta.people<100000,'Not the synthetic walk world: this script runs only against a small data set in real mode.');
  await page.goto(base);await page.locator('.railA').waitFor();await page.waitForFunction(()=>window.__cuMap?.isStyleLoaded());
  await capture('map-desktop');
  for(const [label,path,ready] of sections){
    const nav=page.getByRole('navigation',{name:'Sections'});assert.equal(await nav.getByRole('link').count(),8);
    await nav.getByRole('link',{name:label,exact:true}).click();await page.locator(ready).first().waitFor();
    assert.equal(new URL(page.url()).pathname,path);assert.equal(await nav.locator('[aria-current="page"]').innerText(),label);
    const response=await page.reload();assert.equal(response.status(),200);await page.locator(ready).first().waitFor();
    assert.equal(await page.title(),'Terrier');assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    if(['People','Contact sheet','Criteria audit','Team & snapshots'].includes(label))await capture(path.slice(1)+'-desktop');
  }
  report.checks.push('all eight sections reachable, direct reloadable, correctly highlighted and named Terrier');
  await page.goto(base+'/people');await page.locator('.trow:not(.sk-row)').first().waitFor();await page.locator('a.trow').first().click();await page.locator('.person-name').waitFor();const profilePath=new URL(page.url()).pathname;
  assert.ok((await page.locator('.opinion-facts').innerText()).includes('Affiliation'));await capture('profile-desktop');
  await page.getByRole('link',{name:'Contact details',exact:true}).click();await page.locator('.contact-person').first().waitFor();assert.equal(await page.locator('.contact-person').count(),1);
  report.checks.push('profile contact link selects the same exact person');
  await page.goto(base+'/?index=lean&level=zip');await page.locator('.railA').waitFor();
  await page.getByRole('button',{name:'Tract',exact:true}).click();
  await page.waitForFunction(()=>new URLSearchParams(location.search).get('level')==='tract');
  const saveHref=await page.getByRole('link',{name:'Save view',exact:true}).getAttribute('href');
  assert.equal(new URL(new URL(saveHref,base).searchParams.get('from'),base).searchParams.get('level'),'tract');
  await page.getByRole('link',{name:'Save view',exact:true}).click();await page.locator('.work-save-form').waitFor();
  const savedPath=await page.getByLabel('Terrier page and filters',{exact:true}).inputValue();assert.equal(new URL(savedPath,base).searchParams.get('index'),'lean');assert.equal(new URL(savedPath,base).searchParams.get('level'),'tract');
  report.checks.push('Save view captures current map index and geography');
  await page.setViewportSize({width:390,height:844});
  for(const [label,path,ready] of sections){
    await page.getByLabel('Open section',{exact:true}).selectOption(path==='/'?'map':path.slice(1));await page.locator(ready).first().waitFor();
    assert.equal(new URL(page.url()).pathname,path);
    if(path==='/')await page.waitForFunction(()=>window.__cuMap?.isStyleLoaded()&&window.__cuMap?.queryRenderedFeatures({layers:['fill-a','fill-b']}).length>0);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),label+' mobile overflow');
    if(['People','Contact sheet','Criteria audit','Map'].includes(label))await capture((path==='/'?'map':path.slice(1))+'-mobile');
  }
  await page.goto(base+profilePath);await page.locator('.person-name').waitFor();await capture('profile-mobile');assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  report.checks.push('all eight sections accessible from the mobile section selector; pages fit390px');
  assert.deepEqual(report.errors,[]);assert.deepEqual(report.failedApi,[]);assert.ok(report.apiRequests>0,'the live client made no API requests');
  await writeFile(out+'checks.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{await browser.close();}
