// Browser regression against built assets and synthetic API replies; no stored people or district records are read.
import assert from 'node:assert/strict';
import {importListResponse} from './walk-import-fixture.mjs';
import {unzipSync,strFromU8} from 'fflate';
import {createServer} from 'node:http';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {extname,resolve,sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';

// The workbook is a private local file: pass its path as the first argument or in TERRIER_IMPORT_WORKBOOK.
const workbook=process.argv[2]||process.env.TERRIER_IMPORT_WORKBOOK;
if(!workbook||!existsSync(workbook)){console.error('Usage: node scripts/verify-file-import.mjs <workbook.xlsx> (or set TERRIER_IMPORT_WORKBOOK). '+(workbook?'Not found: '+workbook:'No workbook given.'));process.exit(2);}
const root=fileURLToPath(new URL('../dist/',import.meta.url));
const output=fileURLToPath(new URL('../../_build/file-import-verification/',import.meta.url));
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
let hostedExport=false,matchInputs=[],matchBodies=[],proximityMode=false,missingPerson=false,limitImportBatch=false,importBatchRequests=[];
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
const qaVoter=i=>({person_id:`qa-${i}`,name:`Test Resident ${i}`,phone:'',pri:{voted:1,eligible:2},gen:{voted:2,eligible:2},abs:{voted:0,eligible:2},party:'',age:null,gender:'',reg_date:'',last:null});
const qaPayload={schema:'walk-list-1',generated_on:'2026-10-16',list:{key:null,title:'QA walk route',kind:'standard',created_at:null},query:{precinct:'P1',streets:'1:both',split_sides:'0'},window:{primaries:[],generals:[],abs_rule:'Abs = mail ballots'},bbox:[-104.805,38.795,-104.795,38.805],totals:{streets:1,stops:1,doors:5,voters:5,walk_m:100},runs:[{stop:1,street_id:1,street:'Oak St',side:'both',precinct:'P1',from_house:'2',to_house:'10',walk_m:100,lr:0,path:[[-104.8,38.8],[-104.799,38.8]],approach:[],doors:Array.from({length:5},(_,i)=>({seq:i+1,house:String((i+1)*2),unit:'',placed_by:'geocode',lon:-104.8+i*.0001,lat:38.8,voters:[qaVoter(i+1)]}))}]};
await page.route(`${base}/api/**`,async route=>{
  const url=new URL(route.request().url()),path=url.pathname;let body={};
  if(path==='/api/auth/session'){if(sessionGate)await sessionGate.promise;body={authenticated:true,hosted:hostedExport,email:hostedExport?'qa@example.invalid':null,csrf:'qa'};}
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
  else if(path==='/api/v3/walk/address-match'){
    const request=route.request().postDataJSON();matchBodies.push(request);matchInputs=request.rows;
    const candidate=(id,label,city='Example')=>({id,label,city,state:'CO',zip:'80000',zip_filter:'',doors:1});
    body={district:'co-05',snapshot:'a'.repeat(64),rows:matchInputs.map((r,i)=>{
      if(proximityMode){
        if(r.street==='Pine Rd')return {row:i+1,input:r.street,status:'matched',method:'exact',selected_id:2,truncated:false,candidates:[candidate(2,'Pine Rd')]};
        const supported=request.proximity!==false&&((request.anchors??[]).some(a=>a.house==='10'&&a.street==='Anchor St')||(request.selected_streets??[]).some(s=>s.id===2));
        const near=r.street==='Oak St'&&supported;
        return {row:i+1,input:r.street,status:near?'matched':'pick',method:near?'proximity':'review',selected_id:near?11:undefined,truncated:false,
          candidates:r.street==='Oak St'?[candidate(12,'Oak St','Elsewhere'),candidate(11,'Oak St')]:[candidate(21,'Elm St'),candidate(22,'Elm St','Elsewhere')],
          proximity:{distance_m:near?180:null,runner_up_distance_m:near?22000:null,anchor_count:supported?1:0,confidence:near?'clear':'review',reason:near?'Clear nearest match to another location in this file.':'Nearby evidence is insufficient or tied.'}};
      }
      return {row:i+1,input:r.street,status:'matched',method:'exact',selected_id:i+1,truncated:false,candidates:[{id:i+1,label:r.street,city:r.city||'Example',state:'CO',zip:r.zip||'80000',zip_filter:r.zip||'',doors:1}]};
    })};
  }
  else if(path==='/api/v3/walk/address-list'){
    const selected=route.request().postDataJSON().streets;importBatchRequests.push(selected.length);
    if(limitImportBatch&&selected.length>1){await route.fulfill({status:400,json:{error:'These selections exceed 10,000 voters. Build fewer streets at a time; no doors have been omitted.'}});return;}
    body=importListResponse(selected,id=>({id,label:matchInputs[id-1].street}));
    if(missingPerson)body.selections[0].person_ids.push('missing-person');
  }
  else if(path==='/api/v3/walk/list')body=qaPayload;
  else if(path==='/api/v3/walk/lists')body={lists:[]};
  else if(path==='/api/v3/walk/options')body={options:[]};
  else if(path==='/api/v3/walk/streets')body={streets:[]};
  else body={type:'FeatureCollection',features:[]};
  await route.fulfill({json:body});
});

const unzipDownload=async(name,click)=>{
  const [download]=await Promise.all([page.waitForEvent('download'),click()]);
  const path=resolve(output,name);await download.saveAs(path);
  const files=unzipSync(await readFile(path));
  return {files,manifest:JSON.parse(strFromU8(files['manifest.json']))};
};
try{
  await mkdir(output,{recursive:true});sessionGate.release();sessionGate=null;metaGate.release();metaGate=null;
  await page.goto(base+'/walk?precinct=P1&streets=1%3Aboth&split_sides=0&max_doors=2');
  const routeZip=await unzipDownload('route-lists.zip',()=>page.getByRole('button',{name:'Download ZIP',exact:true}).click());
  assert.equal(routeZip.manifest.doors,5);assert.deepEqual(routeZip.manifest.lists.map(x=>x.doors),[2,2,1]);
  assert.equal(Object.keys(routeZip.files).filter(x=>x.endsWith('.pdf')).length,3);
  assert.equal(strFromU8(routeZip.files['all-lists.csv']).trimEnd().split('\r\n').length,6);
  report.checks.push('Route ZIP includes all five doors and three individual PDFs');
  await page.goto(base+'/walk');
  await page.getByRole('button',{name:'Address file',exact:true}).click();
  await page.getByLabel('Split even and odd',{exact:true}).uncheck();
  await page.getByLabel('Maximum doors per list').fill('2');
  await page.getByLabel('Upload an address file',{exact:true}).setInputFiles({name:'five-addresses.csv',mimeType:'text/csv',buffer:Buffer.from('address,city,state,zip\n2 Oak St,Example,CO,80000\n4 Oak St,Example,CO,80000\n6 Oak St Apt 2,Example,CO,80000\n8 Oak St,Example,CO,80000\n10 Oak St,Example,CO,80000')});
  const result=await unzipDownload('address-lists.zip',()=>page.getByRole('button',{name:'Download all lists ZIP',exact:true}).click());
  assert.equal(result.manifest.doors,5);assert.deepEqual(result.manifest.lists.map(x=>x.doors),[2,2,1]);
  assert.equal(Object.keys(result.files).filter(x=>x.endsWith('.pdf')).length,3);
  assert.equal(strFromU8(result.files['all-lists.csv']).trimEnd().split('\r\n').length,6);
  const secondCsv=result.manifest.lists[1].file.replace(/\.pdf$/,'.csv');
  assert.match(strFromU8(result.files[secondCsv]),/"Oak St","both","6","Apt 2","",""/i);
  assert.equal(strFromU8(result.files[secondCsv]).replace(/^\uFEFF/,'').split('\r\n')[0],'"Stop","Precinct","Street","Side","Address","Unit","Name","Phone","Pri","Gen","Abs","Party","Age","Gender","Reg. Date","Result","City","State","ZIP"');
  report.checks.push('ZIP preserves all five exact addresses and unit across three capped lists');
  hostedExport=true;
  const hostedZip=await unzipDownload('hosted-address-lists.zip',()=>page.getByRole('button',{name:'Download all lists ZIP',exact:true}).click());
  assert.equal(Object.keys(hostedZip.files).filter(x=>x.endsWith('.csv')).length,0);
  assert.equal(Object.keys(hostedZip.files).filter(x=>x.endsWith('.pdf')).length,3);
  hostedExport=false;
  report.checks.push('ZIP rechecks hosted session and omits CSV exports');
  await page.getByLabel('Upload an address file',{exact:true}).setInputFiles(workbook);
  await page.getByText(/33 street/).first().waitFor();
  await page.screenshot({path:resolve(output,'workbook-review.png')});
  assert.equal(await page.getByRole('button',{name:'Download all lists ZIP',exact:true}).count(),0);
  report.checks.push('Real workbook detects 33 street requests and blocks partial export pending review');
  assert.equal(matchInputs.length,33);assert.ok(matchInputs.every(x=>x.city===''));
  await page.getByRole('checkbox',{name:/Include 888 Queen St/}).uncheck();
  await page.getByRole('button',{name:'Create sheets from selected streets',exact:true}).click();
  const workbookZip=await unzipDownload('workbook-synthetic-doors.zip',()=>page.getByRole('button',{name:'Download all lists ZIP',exact:true}).click());
  assert.equal(workbookZip.manifest.doors,33);assert.equal(workbookZip.manifest.lists.length,17);
  assert.equal(workbookZip.manifest.streetMatches.length,33);assert.ok(workbookZip.manifest.streetMatches.every(match=>match.method==='exact'));
  assert.ok(workbookZip.manifest.sourceRows.some(row=>row.sourceCell==='K4'));
  assert.ok(workbookZip.manifest.sourceRows.some(row=>row.sourceCell==='L62'));
  assert.equal(workbookZip.manifest.voters,33);assert.match(strFromU8(workbookZip.files['all-lists.csv']),/Resident 33/);
  assert.ok(workbookZip.files['Summary.csv']);assert.ok(workbookZip.files['Notes.txt']);
  report.checks.push('Reviewed street expansion ZIP covers 33 source cells; explicit anchor exclusion preserved; district doors mocked for this check');
  await page.getByLabel('Upload an address file',{exact:true}).setInputFiles({name:'invalid.txt',mimeType:'text/plain',buffer:Buffer.from('2 Oak St\nnot an address')});
  await page.getByRole('alert').filter({hasText:'unresolved entry'}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Download all lists ZIP',exact:true}).count(),0);
  report.checks.push('Unreadable entry prevents an incomplete export');
  await page.getByLabel('Upload an address file',{exact:true}).setInputFiles({name:'recorded-streets.txt',mimeType:'text/plain',buffer:Buffer.from('Oak St\nElm St')});
  await page.getByLabel('Match for street 2',{exact:true}).waitFor();
  missingPerson=true;
  await page.getByRole('button',{name:'Create sheets from selected streets',exact:true}).click();
  await page.getByRole('alert').filter({hasText:'incomplete person list'}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Download all lists ZIP',exact:true}).count(),0);
  report.checks.push('Incomplete recorded person membership blocks the entire export');
  missingPerson=false;limitImportBatch=true;importBatchRequests=[];
  await page.getByRole('button',{name:'Create sheets from selected streets',exact:true}).click();
  const batched=await unzipDownload('batched-import.zip',()=>page.getByRole('button',{name:'Download all lists ZIP',exact:true}).click());
  assert.deepEqual(importBatchRequests,[2,1,1]);assert.equal(batched.manifest.doors,2);assert.equal(batched.manifest.voters,2);
  assert.match(strFromU8(batched.files['all-lists.csv']),/Resident 1/);assert.match(strFromU8(batched.files['all-lists.csv']),/Resident 2/);
  limitImportBatch=false;
  report.checks.push('The voter limit splits whole street requests into smaller complete batches without dropping people');
  proximityMode=true;matchBodies=[];
  const nearbyFile={name:'nearby-streets.txt',mimeType:'text/plain',buffer:Buffer.from('10 Anchor St, Example, CO 80000\nOak St\nElm St')};
  await page.getByLabel('Upload an address file',{exact:true}).setInputFiles(nearbyFile);
  await page.waitForFunction(()=>document.querySelector('[aria-label="Match for street 1"]')?.value==='11:');
  await page.getByText(/Inferred by proximity/).first().waitFor();
  assert.equal(await page.getByLabel('Match for street 2',{exact:true}).inputValue(),'','tied evidence stays unresolved');
  assert.ok(matchBodies.some(b=>b.proximity!==false&&b.anchors?.some(a=>a.house==='10'&&a.street==='Anchor St')),'included exact address supplies locality evidence');
  await page.getByLabel('Match for street 1',{exact:true}).selectOption('12:');
  await page.getByRole('button',{name:'Recheck street matches',exact:true}).click();
  await page.waitForFunction(()=>!document.querySelector('[aria-label="Match for street 1"]')?.disabled);
  assert.equal(await page.getByLabel('Match for street 1',{exact:true}).inputValue(),'12:','manual choice survives nearest-match recheck');
  report.checks.push('Clear proximity inference selects the nearest candidate, ties remain reviewable, and manual choices survive recheck');
  await page.getByLabel('Upload an address file',{exact:true}).setInputFiles(nearbyFile);
  await page.waitForFunction(()=>document.querySelector('[aria-label="Match for street 1"]')?.value==='11:');
  await page.getByRole('checkbox',{name:/Include 10 Anchor St/}).uncheck();
  await page.waitForFunction(()=>document.querySelector('[aria-label="Match for street 1"]')?.value==='');
  assert.equal(await page.getByRole('button',{name:'Download all lists ZIP',exact:true}).count(),0);
  assert.ok(!matchBodies.at(-1).anchors?.length,'excluded exact address no longer supplies evidence');
  report.checks.push('Removing the only location anchor clears dependent automatic inference and prevents partial export');
  matchBodies=[];
  await page.getByLabel('Upload an address file',{exact:true}).setInputFiles({name:'nearby-streets-only.txt',mimeType:'text/plain',buffer:Buffer.from('Pine Rd\nOak St')});
  await page.waitForFunction(()=>document.querySelector('[aria-label="Match for street 2"]')?.value==='11:');
  assert.ok(matchBodies.some(b=>b.proximity!==false&&b.selected_streets?.some(s=>s.id===2)),'independent exact street supplies geography');
  assert.ok(matchBodies.every(b=>!(b.selected_streets??[]).some(s=>s.id===11)),'inferred matches never become independent anchors');
  report.checks.push('Exact street evidence supports proximity inference without chaining inferred choices');
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({status:'passed',checks:report.checks,errors}));
}finally{
  await writeFile(resolve(output,'checks.json'),JSON.stringify(report,null,2));
  await browser.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
}
