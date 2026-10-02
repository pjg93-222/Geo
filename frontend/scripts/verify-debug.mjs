import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
const output=new URL('../../_build/debug-verification/',import.meta.url);await mkdir(output,{recursive:true});
const browser=await chromium.launch({channel:'chrome',headless:true,args:['--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:1440,height:900},reducedMotion:'reduce'});
const report={checks:[],errors:[]};page.on('pageerror',e=>report.errors.push(e.message));
const base='http://127.0.0.1:8767';
try {
  await page.goto(base+'/?index=cua&level=tract');await page.locator('.railA .rail-title').waitFor();
  assert.equal(await page.getByRole('button',{name:'Tract',exact:true}).getAttribute('aria-pressed'),'true');
  const search=page.getByRole('combobox',{name:'Search areas or people'});await search.fill('Precinct 214');await page.getByRole('option').first().waitFor();await search.press('Enter');
  await page.locator('.railB').filter({hasText:'Precinct 214'}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Credit Union Alignment',exact:true}).getAttribute('aria-pressed'),'true');
  await page.goBack();await page.waitForFunction(()=>document.querySelector('.seg.level .on')?.textContent==='Tract');await page.locator('.railA .rail-title').waitFor();assert.ok(page.url().includes('level=tract'));
  await page.goForward();await page.waitForFunction(()=>document.querySelector('.seg.level .on')?.textContent==='Precinct');await page.locator('.railB').filter({hasText:'Precinct 214'}).waitFor();
  await search.fill('Precinct 214');await page.getByRole('option').first().waitFor();await search.press('Enter');
  await page.waitForFunction(()=>!new URLSearchParams(location.search).has('f'));assert.equal(new URL(page.url()).searchParams.get('index'),'cua');
  report.checks.push('map Back/Forward restores layer, selected area and index; repeated search preserves index');
  await page.goto(base+'/people?lean=-2&balanced=0');await page.locator('.tbody .trow').first().waitFor();assert.equal(await page.getByLabel('Outlook',{exact:true}).inputValue(),'-2');
  await page.getByRole('link',{name:'People',exact:true}).click();await page.waitForFunction(()=>document.querySelector('select[aria-label="Outlook"]').value==='all');await page.waitForFunction(()=>document.querySelector('[role="switch"]')?.getAttribute('aria-checked')==='false');
  await page.goBack();await page.waitForFunction(()=>document.querySelector('select[aria-label="Outlook"]').value==='-2');await page.waitForFunction(()=>document.querySelector('[role="switch"]')?.getAttribute('aria-checked')==='false');
  await page.goForward();await page.waitForFunction(()=>document.querySelector('select[aria-label="Outlook"]').value==='all');
  await page.getByLabel('Outlook',{exact:true}).selectOption('2');await page.waitForFunction(()=>new URLSearchParams(location.search).get('lean')==='2');assert.equal(await page.getByLabel('Outlook',{exact:true}).inputValue(),'2');
  report.checks.push('People link/Back/Forward reset and restore filters; direct control changes stay stable');
  await page.route('**/assets/MapScreen-*.js',r=>r.abort());await page.goto(base+'/');await page.getByRole('button',{name:'Reload',exact:true}).waitFor();assert.deepEqual(report.errors,[]);
  await page.unroute('**/assets/MapScreen-*.js');await page.getByRole('button',{name:'Reload',exact:true}).click();await page.locator('.railA .rail-title').waitFor();
  report.checks.push('failed map chunk shows Reload, recovers, and has no unhandled preload rejection');
  const stalled=await browser.newPage({reducedMotion:'no-preference'});await stalled.clock.install();
  let release;const gate=new Promise(r=>release=r);await stalled.route('**/brand/terrier-loading-hq.mp4',async r=>{await gate;await r.abort();});
  await stalled.goto(base+'/method');await stalled.locator('.terrier-startup video').waitFor();await stalled.clock.runFor(10100);await stalled.locator('.terrier-startup').waitFor({state:'detached'});await stalled.locator('.method').waitFor();release();await stalled.close();
  report.checks.push('stalled media watchdog opens the app within 10 seconds');
  const motion=await browser.newPage({reducedMotion:'no-preference'});await motion.goto(base+'/method');await motion.locator('.terrier-startup video').waitFor();await motion.emulateMedia({reducedMotion:'reduce'});await motion.locator('.terrier-startup').waitFor({state:'detached'});assert.equal(await motion.locator('video').count(),0);await motion.close();
  report.checks.push('enabling reduced motion during startup immediately removes animation');
  assert.deepEqual(report.errors,[]);
} catch(e){report.failure=e.stack;throw e;}
finally {await writeFile(new URL('checks.json',output),JSON.stringify(report,null,2));console.log(JSON.stringify(report));await browser.close();}
