import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import {terrierBase} from './terrier-base.mjs';
const base=terrierBase('verify-workspace.mjs');
const output=new URL('../../_build/workspace-verification/',import.meta.url);await mkdir(output,{recursive:true});
const browser=await chromium.launch({channel:'chrome',headless:true});
const context=await browser.newContext({viewport:{width:1440,height:900},reducedMotion:'reduce'});
const page=await context.newPage();
const report={checks:[],errors:[],apiRequests:[]};
page.on('pageerror',e=>report.errors.push(e.message));
page.on('request',r=>{if(new URL(r.url()).pathname.startsWith('/api/'))report.apiRequests.push(r.url());});
try{
  await page.goto(base+'/criteria');await page.getByRole('heading',{name:'Criteria audit',exact:true}).waitFor();
  assert.equal(await page.locator('.work-table tbody tr').count(),40);
  await page.getByLabel('Criteria group',{exact:true}).selectOption('lean');
  assert.equal(await page.locator('.work-table tbody tr').count(),11);
  assert.equal(await page.locator('.work-table tbody .work-tag').filter({hasText:'Not scored'}).count(),11);
  await page.getByRole('searchbox',{name:'Search criteria'}).fill('registration');assert.equal(await page.locator('.work-table tbody tr').count(),1);
  const download=page.waitForEvent('download');await page.getByRole('button',{name:'Export shown fields'}).click();
  const csv=await download;assert.equal(csv.suggestedFilename(),'terrier-criteria.csv');
  const contents=await readFile(await csv.path(),'utf8');assert.ok(contents.includes('svi_party_registration'));assert.ok(contents.includes('Not scored'));assert.equal(contents.split('\r\n').length,2);
  report.checks.push('all 40 criteria; categorical political fields; group/search filtering and matching CSV export');

  await page.goto(base+'/sources');await page.getByRole('heading',{name:'Data & sources',exact:true}).waitFor();
  await page.waitForFunction(()=>document.querySelectorAll('.work-facts>div').length===4);
  assert.ok((await page.locator('.work-main').innerText()).includes('not official election precinct boundaries'));
  assert.ok((await page.locator('.work-main').innerText()).includes('do not aggregate the displayed people'));
  report.checks.push('source ledger and counts load from active fixtures, including synthetic precinct and independent-area caveats');

  const target='/people?reg=D&lean=1&balanced=0';
  await page.goto(base+'/workspace?from='+encodeURIComponent(target));
  await page.getByRole('heading',{name:'Team & snapshots',exact:true}).waitFor();
  assert.equal(await page.getByLabel('Terrier page and filters',{exact:true}).inputValue(),target);
  await page.getByLabel('View name',{exact:true}).fill('Democratic registration, liberal outlook');
  await page.getByRole('button',{name:'Save view',exact:true}).click();
  await page.getByRole('link',{name:'Democratic registration, liberal outlook',exact:true}).waitFor();
  await page.reload();await page.getByRole('link',{name:'Democratic registration, liberal outlook',exact:true}).waitFor();
  assert.ok((await page.getByRole('link',{name:'Democratic registration, liberal outlook',exact:true}).getAttribute('href')).includes('reg=D&lean=1&balanced=0'));
  await page.getByRole('link',{name:'Democratic registration, liberal outlook',exact:true}).click();
  await page.locator('.tbody .trow').first().waitFor();assert.equal(await page.getByLabel('Outlook',{exact:true}).inputValue(),'1');
  await page.goto(base+'/workspace');await page.getByRole('button',{name:'Remove saved view Democratic registration, liberal outlook',exact:true}).click();
  await page.getByRole('heading',{name:'No saved views yet',exact:true}).waitFor();await page.reload();await page.getByRole('heading',{name:'No saved views yet',exact:true}).waitFor();
  report.checks.push('save, reload, open filtered People view and delete bookmark persist correctly');

  await page.getByLabel('View name',{exact:true}).fill('External route');await page.getByLabel('Terrier page and filters',{exact:true}).fill('//example.com');
  await page.getByRole('button',{name:'Save view',exact:true}).click();await page.getByRole('alert').filter({hasText:'Choose a Terrier page'}).waitFor();
  assert.equal(await page.locator('.work-saved-title').count(),0);
  const summaryDownload=page.waitForEvent('download');await page.getByRole('button',{name:'Download snapshot summary'}).click();
  const summary=JSON.parse(await readFile(await (await summaryDownload).path(),'utf8'));
  assert.equal(summary.mode,'synthetic-preview');assert.equal(summary.people,109150);assert.equal(summary.political_views,'Categorical responses; not scored');
  report.checks.push('unsafe bookmark routes rejected; snapshot summary exports actual fixture metadata');

  await page.setViewportSize({width:390,height:844});
  for(const route of ['/criteria','/sources','/workspace']){
    await page.goto(base+route);await page.locator('.work-main h1').waitFor();
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),route+' causes page-level horizontal overflow');
  }
  report.checks.push('all restored workspace pages fit mobile viewport without page-level horizontal overflow');
  assert.deepEqual(report.errors,[]);assert.deepEqual(report.apiRequests,[]);
}catch(e){report.failure=e.stack;throw e;}
finally{await writeFile(new URL('checks.json',output),JSON.stringify(report,null,2));console.log(JSON.stringify(report));await browser.close();}
