import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const out=fileURLToPath(new URL('../../_build/contact-verification/',import.meta.url));await mkdir(out,{recursive:true});
const browser=await chromium.launch({channel:'chrome',headless:true,args:['--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});page.setDefaultTimeout(60000);
const report={errors:[],apiRequests:[],checks:[]};
page.on('pageerror',error=>report.errors.push(error.message));page.on('request',request=>{if(new URL(request.url()).pathname.startsWith('/api/'))report.apiRequests.push(request.url());});
const base='http://127.0.0.1:8767';
async function rows(){await page.locator('.contacts-table tbody tr').first().waitFor();}
try{
  await page.goto(base+'/contacts');await rows();assert.equal(await page.locator('.contacts-table tbody tr').count(),50);
  const first=await page.locator('.contact-person').first().innerText();
  await page.getByRole('button',{name:'Next',exact:true}).click();await page.waitForURL('**/contacts?page=2');await rows();
  assert.notEqual(await page.locator('.contact-person').first().innerText(),first);
  await page.goBack();await rows();assert.equal(await page.locator('.contact-person').first().innerText(),first);
  report.checks.push('50 rows, next page and browser back restore');
  const profileHref=await page.locator('.contact-profile').first().getAttribute('href');const id=profileHref.split('/').at(-1);
  await page.getByLabel('Search the contact sheet',{exact:true}).fill(id);await page.getByRole('button',{name:'Search',exact:true}).click();await rows();
  assert.equal(await page.locator('.contacts-table tbody tr').count(),1);
  const downloadPromise=page.waitForEvent('download');await page.getByRole('button',{name:'Export matching CSV'}).click();const download=await downloadPromise;
  const csv=await readFile(await download.path(),'utf8');assert.equal(csv.trimEnd().split('\r\n').length,2);assert.ok(csv.includes(id));
  await page.locator('.contact-person').first().click();await page.getByRole('dialog').waitFor();
  assert.equal(await page.locator('#contact-detail-title').innerText(),first);
  await page.getByRole('link',{name:'Open profile',exact:true}).click();await page.locator('.person-name').waitFor();assert.equal(await page.locator('.person-name').innerText(),first);
  await page.goBack();await rows();assert.equal(await page.getByLabel('Search the contact sheet',{exact:true}).inputValue(),id);
  await page.locator('.contact-person').first().click();const mapLink=page.getByRole('link',{name:'View census block'});const block=new URL(await mapLink.getAttribute('href'),base).searchParams.get('sel');
  await mapLink.click();await page.locator('.rail-title').waitFor();assert.equal(new URL(page.url()).searchParams.get('level')??'block','block');assert.equal(new URL(page.url()).searchParams.get('sel'),block);
  report.checks.push('exact ID search, matching CSV, details dialog, profile identity and map area link');
  await page.goto(base+'/contacts?channel=none');await rows();
  assert.ok((await page.locator('.contacts-table tbody tr').first().innerText()).includes('Not supplied'));
  await page.getByLabel('Search the contact sheet',{exact:true}).fill('ZZZ-MISSING-CONTACT');await page.getByRole('button',{name:'Search',exact:true}).click();await page.getByRole('heading',{name:'No matching contacts'}).waitFor();
  assert.ok(await page.getByRole('button',{name:'Export matching CSV'}).isDisabled());
  report.checks.push('missing-channel filter and empty state');
  await page.setViewportSize({width:390,height:844});await page.goto(base+'/contacts');await rows();
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'No mobile body overflow');
  assert.ok(await page.locator('.contacts-table-wrap').evaluate(element=>element.scrollHeight>element.clientHeight&&element.scrollWidth>element.clientWidth),'Table scrolls internally');
  await page.locator('.contact-person').first().click();await page.getByRole('dialog').waitFor();await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'hidden'});
  report.checks.push('mobile internal table scrolling and keyboard dialog close');
  let fail=true;await page.route('**/fixtures/v3/areas/block.json',route=>fail?route.fulfill({status:503,body:'unavailable'}):route.continue());
  await page.goto(base+'/contacts');await page.getByRole('button',{name:'Retry contact sheet'}).waitFor();fail=false;await page.getByRole('button',{name:'Retry contact sheet'}).click();await rows();
  report.checks.push('failed contact source recovers with Retry');
  assert.deepEqual(report.errors,[]);assert.deepEqual(report.apiRequests,[]);
}catch(error){report.failure=error.stack;throw error;}
finally{await writeFile(out+'contacts-ui-checks.json',JSON.stringify(report,null,2));await browser.close();console.log(JSON.stringify(report));}
