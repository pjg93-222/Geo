import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';
import {build} from 'esbuild';
import {importListResponse} from './walk-import-fixture.mjs';

// Isolated component browser check. All API requests are synthetic and intercepted;
// no application service, profile, account database or voter store is opened.
const compiled=await build({
  stdin:{contents:`import {useState} from 'react';import {createRoot} from 'react-dom/client';
    import {WalkAddressSheets} from './src/v3/components/WalkAddressSheets.tsx';
    import './src/v3/styles/walk.css';
    function Check(){const [show,setShow]=useState(false);return <><label><input type="checkbox" checked={show} onChange={event=>setShow(event.target.checked)}/>Show recorded opt-ins</label><WalkAddressSheets capText="1" splitSides={false} hosted={false} showOptIns={show}/></>;}
    createRoot(document.getElementById('check')).render(<Check/>);`,loader:'tsx',resolveDir:fileURLToPath(new URL('../',import.meta.url))},
  bundle:true,write:false,format:'esm',platform:'browser',jsx:'automatic',outdir:'walk-paste-check',
  define:{'process.env.NODE_ENV':'"production"'},external:['tesseract.js','read-excel-file/universal','jspdf','jspdf-autotable'],
  // The upload glyph has no role in intake or export behavior; avoid bundling the entire icon catalogue.
  plugins:[{name:'upload-glyph',setup(bundle){
    bundle.onResolve({filter:/^lucide-react$/},()=>({path:'upload-glyph',namespace:'check'}));
    bundle.onLoad({filter:/.*/,namespace:'check'},()=>({contents:'export const Upload=()=>null;',loader:'js'}));
  }}]
});
const javascript=compiled.outputFiles.find(file=>file.path.endsWith('.js')).contents;
const css=compiled.outputFiles.find(file=>file.path.endsWith('.css')).contents;
const server=createServer((request,response)=>{
  if(request.url==='/check.js'){response.setHeader('Content-Type','text/javascript');response.end(javascript);}
  else if(request.url==='/check.css'){response.setHeader('Content-Type','text/css');response.end(css);}
  else if(request.url==='/walk-paste-check'){response.setHeader('Content-Type','text/html');response.end('<!doctype html><html><head><title>Paste check</title><link rel="stylesheet" href="/check.css"></head><body><div id="check"></div><script type="module" src="/check.js"></script></body></html>');}
  else{response.statusCode=404;response.end();}
});
let browser;
try{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const address=server.address();
  assert.ok(address&&typeof address==='object');
  browser=await chromium.launch({channel:'chrome',headless:true});
  const page=await browser.newPage({viewport:{width:1100,height:900},acceptDownloads:true});
  page.setDefaultTimeout(30000);
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  page.on('console',message=>{if(message.type()==='error')console.error(message.text());});
  const oak={id:11,label:'Oak Rd',city:'Hartford',state:'CT',zip:'06103',zip_filter:'',doors:1};
  const pine={id:22,label:'Pine St',city:'Hartford',state:'CT',zip:'06103',zip_filter:'',doors:1};
  const other={...pine,id:23,city:'Another Town'};
  const matched=[];
  await page.route(url=>url.pathname.startsWith('/api/'),async route=>{
    const path=new URL(route.request().url()).pathname;
    let body;
    if(path==='/api/auth/session')body={authenticated:true,csrf:'synthetic-check'};
    else if(path==='/api/v3/walk/address-match'){
      const request=route.request().postDataJSON();matched.push(request);
      body={district:'ct-01',snapshot:'a'.repeat(64),rows:request.rows.map((row,index)=>({row:index+1,input:row.street,status:row.street==='Oak Rd'?'matched':'pick',method:row.street==='Oak Rd'?'exact':'review',truncated:false,candidates:row.street==='Oak Rd'?[oak]:[pine,other]}))};
    }else if(path==='/api/v3/walk/address-list'){
      const request=route.request().postDataJSON();
      body=importListResponse(request.streets,id=>[oak,pine].find(street=>street.id===id),{district:'ct-01'});
      body.payload.runs[0].doors[0].voters[0].opt_ins={donor:{status:'opted_in',recorded_at:'2026-10-21T12:00:00Z',source:'recorded_manually'}};
    }else throw new Error(`Unexpected API request: ${path}`);
    await route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
  });
  await page.goto(`http://127.0.0.1:${address.port}/walk-paste-check`,{waitUntil:'domcontentloaded'});
  await page.getByRole('heading',{name:'Sheets from your addresses'}).waitFor();
  await page.getByText('Paste addresses or street names',{exact:true}).click();
  const paste=page.getByRole('textbox',{name:'Addresses or street names',exact:true});
  const review=page.getByRole('button',{name:'Review pasted list',exact:true});
  assert.ok(await review.isDisabled(),'empty text cannot replace an import');
  const input='123 Main St, Hartford, CT 06103\n123 Main St, Hartford, CT 06103\nOak Rd\nPine St\nUnclear entry';
  await paste.fill(input);await review.click();
  await page.getByText('Found 2 exact addresses and 2 street requests · 1 entry needs review.',{exact:true}).waitFor();
  assert.equal(await page.getByRole('checkbox',{name:'Include 123 Main St, Hartford, CT 06103',exact:true}).count(),2,'duplicate supplied doors remain explicit');
  const create=page.getByRole('button',{name:'Create sheets from selected streets',exact:true});
  assert.ok(await create.isDisabled(),'unreadable rows and ambiguous streets need review');
  await page.getByRole('checkbox',{name:'Leave out this entry',exact:true}).check();
  assert.ok(await create.isDisabled(),'an unresolved street still blocks generation');
  await page.getByRole('combobox',{name:'Match for street 2',exact:true}).selectOption('22:');
  await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(button=>button.textContent==='Create sheets from selected streets'&&!button.disabled));
  await create.click();
  await page.getByText(/4 door rows and 2 recorded people covered across 4 lists/).waitFor();
  assert.ok(matched.every(request=>request.rows.length===2&&request.rows.every(row=>['Oak Rd','Pine St'].includes(row.street))),'exact supplied addresses never become whole-street requests');
  const preview=page.getByRole('combobox',{name:'Address list preview',exact:true});
  await preview.selectOption('2');
  assert.equal(await page.getByLabel('Recorded opt-ins for Resident 11',{exact:true}).count(),0,'recorded badges stay hidden until requested');
  await page.getByRole('checkbox',{name:'Show recorded opt-ins',exact:true}).check();
  await page.getByText('Donor: Opted in',{exact:true}).waitFor();
  assert.equal(await page.locator('.walk-address-table .has-recorded-opt-in').count(),1);
  assert.equal(await page.getByRole('combobox',{name:/Record .* opt-in for/}).count(),0,'import previews display records without editing an unsaved roster');
  await preview.selectOption('3');
  await page.getByText('Donor: Unknown',{exact:true}).waitFor();
  assert.equal(await page.locator('.walk-address-table .has-recorded-opt-in').count(),0,'missing records never imply an opt-in');
  await preview.selectOption('0');
  assert.equal(await page.getByLabel(/^Recorded opt-ins for /).count(),0,'unmatched exact addresses have no inferred opt-ins');
  const downloadCsv=page.getByRole('button',{name:'Download all lists CSV',exact:true});
  const [download]=await Promise.all([page.waitForEvent('download'),downloadCsv.click()]);
  const csv=await readFile(await download.path(),'utf8');
  assert.equal(csv.split('\r\n').filter(line=>line.includes('"Main St"')&&line.includes('"123"')).length,2,'both supplied doors survive the separate street and house columns');
  assert.ok(csv.includes('Resident 11')&&csv.includes('Resident 22'),'selected recorded street members reach the export');
  await paste.fill('99 Elm Rd');await review.click();
  await page.getByText('Found 1 exact address and 0 street requests.',{exact:true}).waitFor();
  await page.getByText(/1 door row and 0 recorded people covered across 1 list/).waitFor();
  assert.equal(await create.count(),0,'a replacement clears the old street choices');
  const [replacement]=await Promise.all([page.waitForEvent('download'),downloadCsv.click()]);
  const replacementCsv=await readFile(await replacement.path(),'utf8');
  assert.ok(replacementCsv.split('\r\n').some(line=>line.includes('"Elm Rd"')&&line.includes('"99"')));
  assert.ok(!/Main St|Resident 11|Resident 22/.test(replacementCsv),'a new paste exports only its reviewed entries');
  await paste.fill('bad\u0000text');await review.click();
  await page.getByRole('alert').filter({hasText:'binary or unreadable text'}).waitFor();
  assert.equal(await paste.inputValue(),'bad\u0000text','failed parsing keeps text available to correct');
  assert.equal(await downloadCsv.count(),0,'failed intake cannot export stale lists');
  assert.deepEqual(errors,[]);
  console.log('Walk paste browser check passed: mixed entries, duplicate doors, explicit review, street membership, capped export, recorded opt-in display, replacement and parse failure.');
}finally{
  await browser?.close();await new Promise(resolve=>server.close(resolve));
}
