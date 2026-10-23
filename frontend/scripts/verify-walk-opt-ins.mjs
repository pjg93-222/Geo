import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';
import {build} from 'esbuild';
import {importListResponse} from './walk-import-fixture.mjs';

// Synthetic component smoke only: intercept every API request and never open real profiles or voter stores.
const frontendRoot=fileURLToPath(new URL('../',import.meta.url));
const compiled=await build({stdin:{contents:`import {createRoot} from 'react-dom/client';
    import {WalkScreen} from './src/v3/components/WalkScreen.tsx';
    import './src/v3/styles/tokens.css';import './src/v3/styles/base.css';import './src/v3/styles/chrome.css';import './src/v3/styles/walk.css';
    createRoot(document.getElementById('check')).render(<WalkScreen meta={{}} route={{name:'walk',pid:'',search:new URLSearchParams(location.search)}}/>);`,loader:'tsx',resolveDir:frontendRoot},
  bundle:true,write:false,format:'esm',platform:'browser',jsx:'automatic',outdir:'walk-opt-ins-check',loader:{'.woff2':'dataurl'},
  define:{'process.env.NODE_ENV':'"production"'},external:['tesseract.js','read-excel-file/universal','jspdf','jspdf-autotable'],plugins:[{
    name:'match-application-design-units',setup(builder){
      builder.onLoad({filter:/[\\/]v3[\\/].*\.css$/},async args=>({loader:'css',resolveDir:dirname(args.path),contents:(await readFile(args.path,'utf8')).replace(/(?<![\w#.\-])(-?\d*\.?\d+)u(?![\w-])/g,(_match,n)=>`calc(${n} * var(--u))`)}));
    }
  },{
    name:'isolated-walk-header-and-map',setup(builder){
      builder.onResolve({filter:/^\.\/(Header|WalkMap)$/},args=>{
        if(args.importer.replaceAll('\\','/').endsWith('/WalkScreen.tsx'))return {path:args.path,namespace:'walk-check'};
      });
      builder.onLoad({filter:/.*/,namespace:'walk-check'},args=>({loader:'tsx',resolveDir:frontendRoot,contents:args.path==='./Header'
        ?`import {useState} from 'react';export function PageHeader(){return null;}
          export function useFeatures(){const [district,setDistrict]=useState('co-05');window.changeFixtureDistrict=setDistrict;return {district,roi:null,candidate:null,overlay:{density:false}};}`
        :`export function WalkMap(){return <div>Isolated route map</div>;}`}));
    }
  }]});
const javascript=compiled.outputFiles.find(file=>file.path.endsWith('.js')).contents;
const css=compiled.outputFiles.find(file=>file.path.endsWith('.css')).contents;
const server=createServer((request,response)=>{
  const path=new URL(request.url,'http://local').pathname;
  if(path==='/check.js'){response.setHeader('Content-Type','text/javascript');response.end(javascript);}
  else if(path==='/check.css'){response.setHeader('Content-Type','text/css');response.end(css);}
  else if(path==='/walk-opt-ins-check'){response.setHeader('Content-Type','text/html');response.end('<!doctype html><html><head><title>Recorded opt-ins — synthetic preview</title><link rel="stylesheet" href="/check.css"></head><body><div id="check"></div><script type="module" src="/check.js"></script></body></html>');}
  else{response.statusCode=404;response.end();}
});
const unknown={status:'unknown',recorded_at:null,source:null};
const recorded=status=>({status,recorded_at:'2026-10-21T18:00:00Z',source:'recorded_manually'});
const street={id:11,label:'Example Road',city:'Example',state:'CO',zip:'80000',doors:1};
function list(key,donor='unknown',volunteer='unknown'){
  const payload=importListResponse([{id:11,zip:'80000'}],()=>street).payload;
  payload.fingerprint='synthetic-roster-11';payload.query={streets:'11:both',split_sides:'0'};
  payload.list={key,title:key?`List ${key}`:'Unsaved fixture',kind:'standard',created_at:key?'2026-10-21':null};
  payload.runs[0].doors[0].voters[0].opt_ins={donor:donor==='unknown'?unknown:recorded(donor),volunteer:volunteer==='unknown'?unknown:recorded(volunteer)};
  return payload;
}
let browser;
let release;
try{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const port=server.address().port;
  browser=await chromium.launch({channel:'chrome',headless:true});
  const page=await browser.newPage({viewport:{width:1600,height:1000}});page.setDefaultTimeout(60000);
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  const writes=[],events=[];let deny=false,hold=false,heldReady;
  let states={donor:recorded('opted_in'),volunteer:unknown};
  await page.route(url=>url.pathname.startsWith('/api/'),async route=>{
    const path=new URL(route.request().url()).pathname;let body,status=200;
    if(path==='/api/auth/session')body={authenticated:true,csrf:'fixture-csrf'};
    else if(path==='/api/v3/walk/options')body={options:[]};
    else if(path==='/api/v3/walk/lists'&&route.request().method()==='GET')body={lists:['one','two'].map(key=>({key,title:`List ${key}`,kind:'standard',created_at:'2026-10-21',doors:1,voters:1}))};
    else if(path.startsWith('/api/v3/walk/lists/')){const key=path.split('/').at(-1);body=list(key,key==='one'?'opted_in':'unknown');}
    else if(path==='/api/v3/walk/list')body=list(null);
    else if(path==='/api/v3/walk/lists'){
      events.push('save-list');assert.equal(route.request().postDataJSON().fingerprint,'synthetic-roster-11');body=list('created');
    }else if(path==='/api/v3/walk/opt-ins'){
      const request=route.request().postDataJSON();writes.push(request);events.push('save-opt-in');
      if(hold)await new Promise(resolve=>{release=resolve;heldReady?.();});
      if(deny){status=403;body={error:'This account cannot record field results.'};}
      else{const {person_id,...changed}=request.entries[0];assert.equal(person_id,'synthetic-11');assert.equal(Object.keys(changed).length,1);
        for(const [kind,value] of Object.entries(changed))states={...states,[kind]:recorded(value)};
        body={saved:1,opt_ins:{[person_id]:states}};
      }
    }else throw new Error(`Unexpected API request: ${path}`);
    await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)}).catch(error=>{if(!route.request().failure())throw error;});
  });
  await page.goto(`http://127.0.0.1:${port}/walk-opt-ins-check?list=one`,{waitUntil:'domcontentloaded'});
  await page.getByRole('table').waitFor();
  assert.equal(await page.locator('.walk-opt-ins').count(),0,'recorded details default to hidden');
  await page.getByRole('checkbox',{name:'Show recorded opt-ins',exact:true}).check();
  await page.getByText('Donor: Opted in',{exact:true}).waitFor();
  assert.equal(await page.locator('tr.has-recorded-opt-in').count(),1);
  assert.equal(await page.locator('.walk-opt-in-source').filter({hasText:'Recorded manually'}).count(),1);
  const donor=page.getByRole('combobox',{name:'Record donor opt-in for Resident 11',exact:true});
  const volunteer=page.getByRole('combobox',{name:'Record volunteer opt-in for Resident 11',exact:true});
  await volunteer.selectOption('opted_out');await page.getByText('Volunteer: Opted out',{exact:true}).waitFor();
  assert.deepEqual(writes.at(-1),{list:'one',entries:[{person_id:'synthetic-11',volunteer:'opted_out'}]});
  assert.equal(await donor.inputValue(),'opted_in','editing volunteer preserves the donor record');
  deny=true;await donor.selectOption('opted_out');
  await page.getByRole('alert').filter({hasText:'Not saved: This account cannot record field results.'}).waitFor();
  assert.equal(await donor.inputValue(),'opted_in','permission failures preserve the confirmed state');deny=false;

  // A server reply for the old list must never apply to the replacement list, even for the same person id.
  hold=true;let held=new Promise(resolve=>{heldReady=resolve;});await volunteer.selectOption('opted_in');await held;
  await page.getByRole('combobox',{name:'Saved lists',exact:true}).selectOption('two');
  await page.getByText('Donor: Unknown',{exact:true}).waitFor();hold=false;release();
  await page.getByText('Volunteer: Unknown',{exact:true}).waitFor();
  assert.equal(await page.locator('tr.has-recorded-opt-in').count(),0,'old-list response cannot highlight the replacement list');

  // A district change invalidates an in-flight request without adopting its late reply.
  hold=true;held=new Promise(resolve=>{heldReady=resolve;});await donor.selectOption('opted_in');await held;
  await page.evaluate(()=>window.changeFixtureDistrict('or-05'));hold=false;release();
  await page.getByText('Donor: Unknown',{exact:true}).waitFor();
  assert.equal(await page.locator('tr.has-recorded-opt-in').count(),0);

  await page.goto(`http://127.0.0.1:${port}/walk-opt-ins-check?streets=11:both&split_sides=0`,{waitUntil:'domcontentloaded'});
  await page.getByRole('table').waitFor();await page.getByRole('checkbox',{name:'Show recorded opt-ins',exact:true}).check();
  states={donor:unknown,volunteer:unknown};events.length=0;
  await page.getByRole('combobox',{name:'Record donor opt-in for Resident 11',exact:true}).selectOption('opted_in');
  await page.getByText('Donor: Opted in',{exact:true}).waitFor();
  assert.deepEqual(events.slice(0,2),['save-list','save-opt-in'],'a new list is frozen before recording an opt-in');
  assert.equal(writes.at(-1).list,'created');
  assert.deepEqual(errors,[]);
  const screenshotIndex=process.argv.indexOf('--screenshot');
  if(screenshotIndex>=0){assert.ok(process.argv[screenshotIndex+1]);await page.screenshot({path:process.argv[screenshotIndex+1],fullPage:true});}
  console.log('Walk opt-in browser check passed: default-off display, explicit statuses/source/date, one-kind writes, permission failure, list/district stale replies and first-save roster.');
}finally{
  release?.();await browser?.close();await new Promise(resolve=>server.close(resolve));
}
