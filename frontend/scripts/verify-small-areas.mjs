import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {terrierBase} from './terrier-base.mjs';

// Nothing is withheld, checked in a browser on the SYNTHETIC world of tools/make_walk_world.py (port 8768 by default; 8767 is always refused):
//   <venv python> tools/make_walk_world.py <world>
//   CU_DATA_DIR=<world> CU_DEMO=0 CU_COOKIE_PREFIX=cu_walk_ <venv python> manage.py serve --port 8768
//   node frontend/scripts/verify-small-areas.mjs                 (TERRIER_URL points it at another port)
// The world has 15 blocks, so the map is redrawn in flight, as the earlier screenshot lanes did: the areas, geometry and bounding box of
// public/fixtures/v3 (invented values on the real block shapes) answer the area and geometry requests, with a cluster of small blocks injected
// (1 to 45 people, values kept) and three blocks with no registered people. Every small block must be colored, an empty one must be the plain pale
// fill, the rails must say so, and nothing may scroll sideways at 375 px. Screenshots go to _build/small-areas/ next to checks.json (SHOTS_DIR overrides).
const base=terrierBase('verify-small-areas.mjs');
const out=process.env.SHOTS_DIR?process.env.SHOTS_DIR.replace(/[\\/]+$/,'')+'/':fileURLToPath(new URL('../../_build/small-areas/',import.meta.url));
await mkdir(out,{recursive:true});
const NONE_FILL='#FAF8FC';
const DESKTOP={width:1440,height:900},PHONE={width:375,height:812};
const SIZES=[3,1,7,12,null,null,null,19,5,9,14,2,45];       // people of the 13 blocks nearest the middle of the district; null = no registered people
const SMALL_AREA={min_n:20,min_households:10};
const SHARES=[0.02,0.065,0.10,0.15,0.21];                  // top-10% shares given to the small blocks in turn, so the screenshot shows every color class
const FIXTURES=new URL('../public/fixtures/v3/',import.meta.url);
const report={checks:[],shots:[],consoleErrors:[],pageErrors:[],failure:null};
const browser=await chromium.launch({channel:'chrome',headless:true,args:['--enable-unsafe-swiftshader']});

const fixture=async path=>JSON.parse(await readFile(new URL(path,FIXTURES),'utf8'));
const COLS=g=>Object.keys(g).filter(k=>Array.isArray(g[k]));
/** Answers the area, geometry, district and meta requests of a context from the fixture world with the cluster injected; returns what was injected. */
async function wire(context){
  const [block,precinct,tract,zip,geo,fixtureMeta,district]=await Promise.all(['areas/block.json','areas/precinct.json','areas/tract.json','areas/zip.json',
    'geometry/block.coarse.json','meta.json','district.json'].map(fixture));
  const [w,s,e,n]=fixtureMeta.bbox,cx=(w+e)/2,cy=(s+n)/2,k=Math.cos(cy*Math.PI/180);
  const near=geo.features.map(f=>({id:f.properties.id,d:Math.hypot((f.properties.lx-cx)*k,f.properties.ly-cy)})).sort((a,b)=>a.d-b.d).slice(0,SIZES.length).map(x=>x.id);
  const at=new Map(block.ids.map((id,i)=>[id,i]));
  const small=[],empty=[];let wide=null;
  near.forEach((id,j)=>{
    const i=at.get(id),size=SIZES[j];
    if(size===null){
      block.status[i]='empty';block.n[i]=0;block.households[i]=0;
      for(const g of Object.values(block.groups))for(const c of COLS(g))g[c][i]=null;
      empty.push(id);return;
    }
    block.n[i]=size;block.households[i]=size===45?6:Math.max(1,Math.round(size*0.6));
    for(const g of Object.values(block.groups)){if(g.se)g.se[i]=14;if(g.top10_se)g.top10_se[i]=0.08;}
    if(size===45)wide=id;else{block.groups.ovr.top10[i]=SHARES[small.length%SHARES.length];small.push(id);}
  });
  const pi=precinct.ids.indexOf(block.precinct[at.get(small[0])]);
  precinct.n[pi]=7;precinct.households[pi]=4;
  const levels={block,precinct,tract,zip};
  await context.route(/\/api\/v3\/areas\/[^/]+\/registration\/\w+\.json/,route=>route.fulfill({json:levels[/registration\/(\w+)\.json/.exec(route.request().url())[1]]}));
  await context.route(/\/api\/v3\/geometry\/[^/]+\/\w+\/\w+\.json/,async route=>{
    const [,level,variant]=/geometry\/[^/]+\/(\w+)\/(\w+)\.json/.exec(route.request().url());
    await route.fulfill({json:await fixture(`geometry/${level}.${variant}.json`)});
  });
  await context.route(/\/api\/geometry\?level=district/,route=>route.fulfill({json:district}));
  await context.route(/\/api\/v3\/meta$/,async route=>{
    const r=await route.fetch();const m=await r.json();
    m.bbox=fixtureMeta.bbox;delete m.floors;m.small_area=SMALL_AREA;       // the new server's key
    await route.fulfill({response:r,json:m});
  });
  const labelOf=(level,id)=>level==='block'?'Block '+id.slice(-4):'Precinct '+id.slice(-3);
  const ref=(level,payload,id)=>{const i=payload.ids.indexOf(id);return {level,id,label:labelOf(level,id),pct:payload.groups.ovr.top10_pct[i],top10:payload.groups.ovr.top10[i],n:payload.n[i],households:payload.households[i]};};
  return {small,empty,wide,precinct:precinct.ids[pi],geo,context:{block:ref('block',block,small[0]),precinct:ref('precinct',precinct,precinct.ids[pi])}};
}
/** The middle of the small blocks of the injected cluster, from the label points of the geometry file. */
const middle=w=>{const at=new Map(w.geo.features.map(f=>[f.properties.id,f.properties]));const p=w.small.map(id=>at.get(id));
  return {x:p.reduce((s,q)=>s+q.lx,0)/p.length,y:p.reduce((s,q)=>s+q.ly,0)/p.length};};
const newPage=async(context,viewport)=>{
  const page=await context.newPage();await page.setViewportSize(viewport);
  page.on('console',m=>{if(m.type()==='error')report.consoleErrors.push(m.text());});
  page.on('pageerror',e=>report.pageErrors.push(e.message));
  return page;
};
const mapReady=page=>page.waitForFunction(()=>window.__cuMap?.isStyleLoaded()&&window.__cuTiming?.map!=null);
const idle=page=>page.evaluate(()=>new Promise(r=>{const m=window.__cuMap;if(m.loaded()&&!m.isMoving())r();else m.once('idle',()=>r());}));
const shot=async(page,name)=>{await page.screenshot({path:out+name+'.png'});report.shots.push(name+'.png');};
const fits=page=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1&&document.body.scrollWidth<=innerWidth+1);
const look=(page,c,zoom)=>page.evaluate(([c,zoom])=>window.__cuMap.jumpTo({center:[c.x,c.y],zoom}),[c,zoom]).then(()=>idle(page));
/** The color an area is painted: the match expression of the fill layer, looked up for the feature's index. */
const paint=(page,id)=>page.evaluate(id=>{
  const m=window.__cuMap;const f=m.querySourceFeatures('areas',{filter:['==',['get','id'],id]})[0];
  if(!f)return {found:false};
  const e=m.getPaintProperty('fill-a','fill-color');const bin=e[1][2][1][f.properties.i];
  const pairs=e.slice(2,-1);let color=e[e.length-1];for(let q=0;q<pairs.length;q+=2)if(pairs[q]===bin)color=pairs[q+1];
  return {found:true,bin,color};
},id);
const contrast=(page,sel)=>page.evaluate(sel=>{
  const el=document.querySelector(sel);const lum=c=>{const [r,g,b]=c.match(/\d+(\.\d+)?/g).slice(0,3).map(Number).map(v=>{v/=255;return v<=0.03928?v/12.92:((v+0.055)/1.055)**2.4;});return 0.2126*r+0.7152*g+0.0722*b;};
  let bg=el;while(bg&&/rgba\(0, 0, 0, 0\)|transparent/.test(getComputedStyle(bg).backgroundColor))bg=bg.parentElement;
  const a=lum(getComputedStyle(el).color),b=lum(getComputedStyle(bg??document.body).backgroundColor);
  return (Math.max(a,b)+0.05)/(Math.min(a,b)+0.05);
},sel);

let stage='start';
try{
  const context=await browser.newContext({viewport:DESKTOP,deviceScaleFactor:1,reducedMotion:'reduce'});
  const w=await wire(context);
  report.world={small:w.small.length,empty:w.empty.length,wide:w.wide,precinct:w.precinct};
  const centre=middle(w);
  const page=await newPage(context,DESKTOP);

  stage='desktop: a small block';
  await page.goto(base+'/?sel='+w.small[0]);await page.locator('.railA .rail-title').waitFor();await mapReady(page);
  assert.equal(await page.evaluate(()=>window.__cuMap.getLayer('hatch')===undefined&&!window.__cuMap.hasImage('hatch')),true,'no hatch layer or image');
  await look(page,centre,14.2);
  for(const id of [...w.small,w.wide]){const p=await paint(page,id);assert.ok(p.found,'on screen: '+id);assert.ok(p.bin<5,`${id} has a class color (bin ${p.bin})`);assert.notEqual(p.color,NONE_FILL,`${id} is not the pale fill`);}
  for(const id of w.empty){const p=await paint(page,id);assert.ok(p.found&&p.bin===250&&p.color===NONE_FILL,`${id} with no registered people is the plain pale fill`);}
  assert.equal(await page.locator('.small-note').innerText(),'Small area: 3 people in 2 households');
  assert.equal(await page.locator('.headline-cap').innerText(),'in the overall top 10%, shrunk toward the tract');
  assert.ok((await page.locator('.legend').innerText()).includes('No registered people'));
  assert.equal(await page.locator('body').getByText(/withheld/i).count(),0);
  report.noteContrast=Math.round(100*await contrast(page,'.small-note'))/100;assert.ok(report.noteContrast>=4.5,'the small-area note is readable (contrast '+report.noteContrast+':1)');
  report.checks.push('small blocks are colored by their own values; empty blocks are the plain pale fill; the rail carries the note');
  await shot(page,'01-map-small-areas-desktop');

  stage='desktop: an empty block';
  await page.goto(base+'/?sel='+w.empty[0]);await page.locator('.railA .rail-title').waitFor();await mapReady(page);
  await look(page,centre,14.2);
  assert.equal(await page.locator('.headline-cap').innerText(),'No registered people');
  assert.equal(await page.locator('.small-note').count(),0);
  assert.equal(await page.locator('.registration-shares').count(),0,'no invented registration mix');
  assert.equal(await page.locator('.facts dd').first().innerText(),'0');
  await shot(page,'02-map-empty-area-desktop');
  report.checks.push('an area with no registered people says so, with no values and no note');

  stage='desktop: a wider block with few households';
  await page.goto(base+'/?sel='+w.wide);await page.locator('.railA .rail-title').waitFor();
  assert.equal(await page.locator('.small-note').innerText(),'Small area: 45 people in 6 households');

  stage='desktop: a small precinct (rail B)';
  await page.goto(base+'/?level=precinct&sel='+w.precinct);await page.locator('.railB .rb-title').waitFor();await mapReady(page);
  assert.equal(await page.locator('.railB .small-note').innerText(),'Small area: 7 people in 4 households');
  assert.ok((await page.locator('.railB .rb-headline').innerText()).endsWith('%'),'the headline is a value, not blank');
  assert.notEqual(await page.locator('.railB .rb-value').first().innerText(),'');
  await shot(page,'03-precinct-small-desktop');
  report.checks.push('a small precinct shows its values and the note in the second rail');

  stage='phone: map';
  const phone=await browser.newContext({viewport:PHONE,deviceScaleFactor:2,reducedMotion:'reduce',isMobile:true,hasTouch:true});
  await wire(phone);
  const pp=await newPage(phone,PHONE);
  await pp.goto(base+'/?sel='+w.small[0]);await pp.locator('.railA .rail-title').waitFor();await mapReady(pp);
  await look(pp,centre,14);
  assert.ok(await fits(pp),'375 px: nothing scrolls sideways (map)');
  await shot(pp,'04-map-small-areas-375');
  await pp.locator('.small-note').scrollIntoViewIfNeeded();await shot(pp,'05-rail-small-area-375');
  await pp.goto(base+'/?sel='+w.empty[0]);await pp.locator('.railA .rail-title').waitFor();await mapReady(pp);
  await look(pp,centre,14);
  assert.equal(await pp.locator('.headline-cap').innerText(),'No registered people');
  assert.ok(await fits(pp),'375 px: nothing scrolls sideways (empty area)');
  await shot(pp,'06-map-empty-area-375');
  report.checks.push('375 px: no sideways scroll; small and empty areas read the same');

  stage='phone: profile';
  await phone.route(/\/api\/v3\/person\?/,async route=>{
    const r=await route.fetch();const p=await r.json();p.area_context=w.context;await route.fulfill({response:r,json:p});
  });
  await pp.goto(base+'/people');await pp.locator('.tbody a.trow').first().waitFor();await pp.locator('.tbody a.trow').first().click();
  await pp.locator('.ctx-row').first().waitFor();
  const rows=await pp.locator('.ctx-row').allInnerTexts();
  assert.match(rows[0],/Small area: 3 people in 2 households/,'the profile block row carries the note');
  assert.match(rows[1],/Small area: 7 people in 4 households/,'and so does the precinct row');
  assert.equal(await pp.locator('.ctx-row .hatch').count(),0);
  assert.ok(await fits(pp),'375 px: nothing scrolls sideways (profile)');
  await pp.locator('.ctx-row').first().scrollIntoViewIfNeeded();await shot(pp,'07-profile-small-block-375');
  report.checks.push('the profile block and precinct rows carry the note and fit 375 px');

  stage='done';
  assert.deepEqual(report.pageErrors,[]);
  assert.deepEqual(report.consoleErrors.filter(m=>!/Failed to load resource/.test(m)),[]);
}catch(error){report.failure={stage,message:String(error.message).slice(0,300)};console.error('Small-area browser verification failed at '+stage+': '+report.failure.message);process.exitCode=1;}
finally{await browser.close();await writeFile(out+'checks.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));}
