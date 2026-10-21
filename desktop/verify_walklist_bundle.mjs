/** Verify an already-built or installed frontend bundle; no source compilation or real profile/data access. */
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {mkdir,readFile,readdir,realpath,writeFile} from 'node:fs/promises';
import {dirname,extname,isAbsolute,relative,resolve,sep} from 'node:path';

const options={};
for(let index=2;index<process.argv.length;index+=2){
  const name=process.argv[index];
  if(!['--dist','--output','--screenshot','--version','--browser-channel'].includes(name)||!process.argv[index+1])
    throw new Error('Usage: node desktop/verify_walklist_bundle.mjs --dist <frontend/dist> --output <proof.json> [--version <release>] [--screenshot <path>] [--browser-channel chrome|chromium]');
  options[name.slice(2)]=process.argv[index+1];
}
assert.ok(options.dist&&options.output,'--dist and --output are required');
const dist=await realpath(resolve(options.dist));
const require=createRequire(new URL('../frontend/package.json',import.meta.url));
const {chromium}=require('@playwright/test');
const sha256=bytes=>createHash('sha256').update(bytes).digest('hex');
async function inventory(directory,prefix=''){
  const files=[];
  for(const entry of await readdir(directory,{withFileTypes:true})){
    assert.ok(!entry.isSymbolicLink(),'Bundle must not contain symbolic links');
    const path=prefix+entry.name,absolute=resolve(directory,entry.name);
    if(entry.isDirectory())files.push(...await inventory(absolute,path+'/'));
    else if(entry.isFile()){const bytes=await readFile(absolute);files.push({file:path,bytes:bytes.length,sha256:sha256(bytes)});}
  }
  return files.sort((a,b)=>Buffer.compare(Buffer.from(a.file),Buffer.from(b.file)));
}
// Matches Python json.dumps(catalogue, sort_keys=True, separators=(',', ':'), ensure_ascii=True).
const catalogueBytes=files=>JSON.stringify(files.map(({file,bytes,sha256})=>({bytes,file,sha256}))).replace(/[\u007f-\uffff]/g,char=>'\\u'+char.charCodeAt(0).toString(16).padStart(4,'0'));
const files=await inventory(dist);
assert.ok(files.some(file=>file.file==='index.html')&&files.some(file=>file.file.endsWith('.js')),'A built frontend index and JavaScript assets are required');
const distSha256=sha256(catalogueBytes(files));
const report={schemaVersion:1,schema:'terrier-walklist-bundle-check-1',version:options.version??null,versionSource:options.version?'argument':'not_embedded_in_frontend',
  startedAt:new Date().toISOString(),frontendCatalogueSha256:distSha256,distPath:dist,dist:{sha256:distSha256,fileCount:files.length,files},
  fixturesOnly:true,sourceCompilation:false,checks:{paste:false,optIns:false,authenticatedOwnerSessionWithoutPasswordForm:false,inlinePasteReviewBuild:false,
    recordedOptInsDisplay:false,importedOptInsReadOnly:false,manualOptInWrite:false,singleKindWrite:false,savedRosterBeforeWrite:false,
    permissionFailurePreservesState:false,githubDataSourceUi:false,githubDataSourceRefresh:false,githubDataSourceProgress:false,
    githubDataSourceCacheOffline:false,githubDataSourceBoundary:false,noUnhandledPageErrors:false,noUnexpectedApiOrExternalRequests:false,distBytesUnchanged:false},
  browser:null,passed:false,error:null};
const snapshot='a'.repeat(64),district='co-05';
const unknown=()=>({status:'unknown',recorded_at:null,source:null});
const recorded=status=>({status,recorded_at:'2026-10-21T18:00:00Z',source:'recorded_manually'});
const oak={id:11,label:'Oak Rd',city:'Hartford',state:'CT',zip:'06103',zip_filter:'',doors:1};
const pine={...oak,id:22,label:'Pine St'},other={...pine,id:23,city:'Another Town'};
function listPayload(streets=[{id:11,zip:'06103'}],key=null){
  const runs=streets.map((selection,index)=>{
    const street=[oak,pine].find(row=>row.id===selection.id);assert.ok(street,'Unknown synthetic street');
    const lon=-104.8+index*.0002,lat=38.8;
    return {stop:index+1,street_id:street.id,street:street.label,side:'even',precinct:'P1',from_house:'2',to_house:'2',walk_m:25,lr:1,
      path:[[lon,lat],[lon+.0001,lat]],approach:[],doors:[{seq:index+1,house:'2',unit:'',city:street.city,state:'CT',zip:'06103',
        address:`2 ${street.label}, Hartford, CT 06103`,warnings:[],lon,lat,placed_by:'geocode',voters:[{person_id:`synthetic-${street.id}`,
          name:`Resident ${street.id}`,phone:'5550101000',pri:{voted:1,eligible:2},gen:{voted:2,eligible:3},abs:{voted:1,eligible:3},
          party:'',age:30,gender:'',reg_date:'2020-01-01',last:null,opt_ins:{donor:street.id===11?recorded('opted_in'):unknown(),volunteer:unknown()}}]}]};
  });
  return {schema:'walk-list-1',generated_on:'2026-10-21',fingerprint:'synthetic-frozen-roster',list:{key,title:'Synthetic bundle check',kind:'standard',created_at:key?'2026-10-21':null},
    query:{streets:'11:both',split_sides:'0'},window:{primaries:['2024'],generals:['2024'],abs_rule:'Synthetic history'},
    bbox:[-104.81,38.79,-104.78,38.81],totals:{streets:runs.length,stops:runs.length,doors:runs.length,voters:runs.length,walk_m:runs.length*25},runs};
}
function importedResponse(streets){
  const payload=listPayload(streets),selections=streets.map(street=>({id:street.id,zip:street.zip||'',person_ids:[`synthetic-${street.id}`]}));
  return {district,snapshot,payload,selections,groups:[{key:'synthetic-local',payload,selections,reason:'Synthetic nearby streets.'}],warnings:[],map_placement_omissions:[]};
}
const contentTypes={'.html':'text/html','.js':'text/javascript','.css':'text/css','.json':'application/json','.svg':'image/svg+xml',
  '.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.woff2':'font/woff2','.webm':'video/webm','.mp4':'video/mp4','.wasm':'application/wasm'};
const server=createServer(async(request,response)=>{
  try{
    const url=new URL(request.url,'http://bundle'),pathname=decodeURIComponent(url.pathname);
    const file=resolve(dist,'.'+(pathname==='/walk'||pathname==='/sources'||pathname==='/'?'/index.html':pathname));
    const rel=relative(dist,file);
    if(rel==='..'||rel.startsWith('..'+sep)||isAbsolute(rel)){response.writeHead(403);response.end();return;}
    const bytes=await readFile(file);response.setHeader('Content-Type',contentTypes[extname(file)]??'application/octet-stream');
    response.setHeader('Cache-Control','no-store');response.end(bytes);
  }catch{response.writeHead(404);response.end();}
});
let browser;
try{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const base=`http://127.0.0.1:${server.address().port}`;
  const channel=options['browser-channel']??process.env.TERRIER_BROWSER_CHANNEL??'chrome';
  browser=await chromium.launch({...(channel==='chromium'?{}:{channel}),headless:true,args:['--enable-unsafe-swiftshader']});
  report.browser={channel,version:browser.version()};
  const context=await browser.newContext({viewport:{width:1600,height:1000},reducedMotion:'reduce'});
  await context.addInitScript(()=>{
    const listeners=new Set();
    let state={provider:'github',repository:'pjg93-222/Geo',district:'co-05',phase:'ready',cached:true,canRefresh:true,
      releaseTag:'district-testsets-2026-10-21',catalogueSequence:1,checkedAt:'2026-10-21T18:00:00Z',verifiedAt:'2026-10-21T17:59:00Z'};
    window.__districtRefreshCount=0;window.__districtRefreshFailure=false;
    window.__pushDistrictData=value=>{state=value;for(const listener of listeners)listener(structuredClone(value));};
    window.desktop={districts:async()=>({schema:'desktop-districts-1',current:'co-05',districts:[]}),switchDistrict:async()=>false,rendererReady:async()=>{},
      districtData:async()=>structuredClone(state),onDistrictData:callback=>{listeners.add(callback);return()=>listeners.delete(callback);},
      refreshDistrictData:async()=>{window.__districtRefreshCount++;if(window.__districtRefreshFailure)throw new Error('C:\\private-fixture\\do-not-display');
        window.__pushDistrictData({...state,phase:'checking'});return structuredClone(state);}};
  });
  const page=await context.newPage();page.setDefaultTimeout(30000);
  const pageErrors=[],unexpected=[],writes=[],events=[],matches=[];let deny=false;
  let latest={donor:recorded('opted_in'),volunteer:unknown()};
  page.on('pageerror',error=>pageErrors.push(error.message));
  page.on('dialog',dialog=>dialog.accept().catch(()=>{}));
  await context.route('**/*',async route=>{
    try{
    const url=new URL(route.request().url()),path=url.pathname;
    if(url.origin!==base){unexpected.push({kind:'external',url:url.href});await route.abort();return;}
    if(!path.startsWith('/api/')){await route.continue();return;}
    let body,status=200;
    if(path==='/api/auth/session')body={authenticated:true,administrator:true,username:'admin',password_lock_disabled:true,csrf:'synthetic-bundle-csrf'};
    else if(path==='/api/v3/meta')body={schema:'v3-meta-1',snapshot,demo:false,people:2,households:2,district_label:'Synthetic district',store:{status:'ok'},
      district_rate:{ovr:50,cua:50,part:50,soi:50},share_breaks:[],lean_cuts:[-.3,.3],measured_breaks:[],household_breaks:[],weights:{cua:.4,part:.3,soi:.3},levels:[],bbox:[-104.81,38.79,-104.78,38.81]};
    else if(path==='/api/v3/features')body={schema:'v3-features-1',district,industries:[],industry:'',candidate:null,party_codes:{},overlay:{layers:['ovr','cua','part','soi','lean'],density:false},roi:null};
    else if(path==='/api/v3/districts')body={schema:'districts-1',current:district,districts:[]};
    else if(path==='/api/v3/workspace')body={mode:'local',source_sha256:snapshot,people:2,households:2,snapshot,basis:'registration',availableSnapshots:1,fields:[],layers:[],stores:[]};
    else if(path==='/api/v3/walk/options')body={level:url.searchParams.get('level'),options:[]};
    else if(path==='/api/v3/walk/lists'&&route.request().method()==='GET')body={lists:[]};
    else if(path==='/api/v3/walk/address-match'){
      const request=route.request().postDataJSON();matches.push(request);
      body={district,snapshot,rows:request.rows.map((row,index)=>({row:index+1,input:row.street,status:row.street==='Oak Rd'?'matched':'pick',
        method:row.street==='Oak Rd'?'exact':'review',truncated:false,candidates:row.street==='Oak Rd'?[oak]:[pine,other]}))};
    }else if(path==='/api/v3/walk/address-list')body=importedResponse(route.request().postDataJSON().streets);
    else if(path==='/api/v3/walk/list')body=listPayload();
    else if(path==='/api/v3/walk/lists'){
      events.push('save-list');assert.equal(route.request().postDataJSON().fingerprint,'synthetic-frozen-roster');body=listPayload(undefined,'synthetic-saved');
    }else if(path==='/api/v3/walk/opt-ins'){
      const request=route.request().postDataJSON();writes.push(request);events.push('save-opt-in');
      if(deny){status=403;body={error:'Synthetic account cannot record field results.'};}
      else{
        assert.equal(request.list,'synthetic-saved');assert.equal(request.entries.length,1);
        const {person_id,...changed}=request.entries[0];assert.equal(person_id,'synthetic-11');assert.equal(Object.keys(changed).length,1);
        for(const [kind,value] of Object.entries(changed))latest={...latest,[kind]:recorded(value)};
        body={saved:1,opt_ins:{[person_id]:latest}};
      }
    }else if(path==='/api/roads'||path==='/api/v3/walk/overview')body={type:'FeatureCollection',features:[]};
    else{unexpected.push({kind:'api',path});status=500;body={error:'Unexpected synthetic API request'};}
    await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
    }catch(error){
      unexpected.push({kind:'fixture-request-validation',error:error instanceof Error?error.message:String(error)});
      await route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({error:'Synthetic request validation failed'})}).catch(()=>{});
    }
  });
  await page.goto(base+'/walk',{waitUntil:'domcontentloaded'});
  await page.getByRole('heading',{name:'Build a walk list',exact:true}).waitFor();
  assert.equal(await page.locator('input[type=password]').count(),0);
  report.checks.authenticatedOwnerSessionWithoutPasswordForm=true;
  await page.getByRole('button',{name:'Addresses',exact:true}).click();
  await page.getByRole('heading',{name:'Sheets from your addresses',exact:true}).waitFor();
  await page.getByRole('checkbox',{name:'Split even and odd',exact:true}).uncheck();
  await page.getByRole('spinbutton',{name:'Maximum doors per list',exact:true}).fill('1');
  await page.getByText('Paste addresses or street names',{exact:true}).click();
  await page.getByRole('textbox',{name:'Addresses or street names',exact:true}).fill('123 Main St, Hartford, CT 06103\n123 Main St, Hartford, CT 06103\nOak Rd\nPine St');
  await page.getByRole('button',{name:'Review pasted list',exact:true}).click();
  await page.getByText('Found 2 exact addresses and 2 street requests.',{exact:true}).waitFor();
  assert.equal(await page.getByRole('checkbox',{name:'Include 123 Main St, Hartford, CT 06103',exact:true}).count(),2);
  const create=page.getByRole('button',{name:'Create sheets from selected streets',exact:true});
  assert.ok(await create.isDisabled(),'ambiguous streets require explicit review');
  await page.getByRole('combobox',{name:'Match for street 2',exact:true}).selectOption('22:');
  await create.click();await page.getByText(/4 door rows and 2 recorded people covered across 4 lists/).waitFor();
  assert.ok(matches.length>0&&matches.every(request=>request.rows.length===2&&request.rows.every(row=>['Oak Rd','Pine St'].includes(row.street))));
  report.checks.inlinePasteReviewBuild=true;report.checks.paste=true;
  const preview=page.getByRole('combobox',{name:'Address list preview',exact:true});await preview.selectOption('2');
  assert.equal(await page.locator('.walk-opt-ins').count(),0);
  await page.getByRole('checkbox',{name:'Show recorded opt-ins',exact:true}).check();
  await page.getByText('Donor: Opted in',{exact:true}).waitFor();
  assert.equal(await page.locator('.walk-address-table .has-recorded-opt-in').count(),1);
  assert.equal(await page.getByRole('combobox',{name:/Record .* opt-in for/}).count(),0);
  report.checks.importedOptInsReadOnly=true;
  await preview.selectOption('0');assert.equal(await page.locator('.walk-opt-ins').count(),0,'unmatched supplied addresses cannot acquire inferred opt-ins');
  await page.goto(base+'/walk?streets=11:both&split_sides=0',{waitUntil:'domcontentloaded'});
  await page.getByRole('table').waitFor();assert.equal(await page.locator('.walk-opt-ins').count(),0);
  await page.getByRole('checkbox',{name:'Show recorded opt-ins',exact:true}).check();
  await page.getByText('Donor: Opted in',{exact:true}).waitFor();
  assert.equal(await page.locator('tr.has-recorded-opt-in').count(),1);
  assert.equal(await page.locator('.walk-opt-in-source').filter({hasText:'Recorded manually'}).count(),1);
  assert.equal(await page.locator('.walk-opt-in-source time').count(),1);report.checks.recordedOptInsDisplay=true;report.checks.optIns=true;
  const donor=page.getByRole('combobox',{name:'Record donor opt-in for Resident 11',exact:true});
  await page.getByRole('combobox',{name:'Record volunteer opt-in for Resident 11',exact:true}).selectOption('opted_out');
  await page.getByText('Volunteer: Opted out',{exact:true}).waitFor();report.checks.manualOptInWrite=true;
  assert.deepEqual(events.slice(0,2),['save-list','save-opt-in']);report.checks.savedRosterBeforeWrite=true;
  assert.deepEqual(writes.at(-1),{list:'synthetic-saved',entries:[{person_id:'synthetic-11',volunteer:'opted_out'}]});
  assert.equal(await donor.inputValue(),'opted_in');report.checks.singleKindWrite=true;
  if(options.screenshot){await mkdir(dirname(resolve(options.screenshot)),{recursive:true});await page.screenshot({path:resolve(options.screenshot),fullPage:true});}
  deny=true;await donor.selectOption('opted_out');
  await page.getByRole('alert').filter({hasText:'Not saved: Synthetic account cannot record field results.'}).waitFor();
  assert.equal(await donor.inputValue(),'opted_in');assert.equal(await page.locator('tr.has-recorded-opt-in').count(),1);
  report.checks.permissionFailurePreservesState=true;
  await page.goto(base+'/sources',{waitUntil:'domcontentloaded'});
  await page.getByRole('heading',{name:'GitHub district data',exact:true}).waitFor();
  await page.getByText('Using a verified local cache of the GitHub dataset.',{exact:true}).waitFor();
  assert.equal(await page.locator('.district-data-source [datetime="2026-10-21T18:00:00.000Z"]').count(),1);
  assert.equal(await page.locator('.district-data-source [datetime="2026-10-21T17:59:00.000Z"]').count(),1);
  assert.match(await page.locator('.district-data-source').innerText(),/Queries use verified files on this computer/);
  report.checks.githubDataSourceUi=true;
  await page.getByRole('button',{name:'Check GitHub now',exact:true}).click();
  await page.locator('.district-data-source[data-phase="checking"]').waitFor();
  assert.equal(await page.evaluate(()=>window.__districtRefreshCount),1);
  assert.ok(await page.locator('.district-data-source button').isDisabled());report.checks.githubDataSourceRefresh=true;
  const source={provider:'github',repository:'pjg93-222/Geo',district:'co-05',cached:true,canRefresh:true,
    releaseTag:'district-testsets-2026-10-21',catalogueSequence:1,checkedAt:'2026-10-21T18:00:00Z',verifiedAt:'2026-10-21T17:59:00Z'};
  await page.evaluate(value=>window.__pushDistrictData(value),{...source,phase:'downloading',receivedBytes:1048576,totalBytes:2097152});
  const progress=page.getByRole('progressbar',{name:'GitHub district data download',exact:true});await progress.waitFor();
  assert.equal(await progress.getAttribute('value'),'1048576');assert.equal(await progress.getAttribute('max'),'2097152');
  assert.ok(await page.locator('.district-data-source button').isDisabled());
  await page.evaluate(value=>window.__pushDistrictData(value),{...source,phase:'update-ready',updateAvailable:true});
  await page.getByText(/Save your work, then reopen this district or Terrier to apply it/).waitFor();
  assert.equal(new URL(page.url()).pathname,'/sources','a staged dataset must not navigate away from current work');report.checks.githubDataSourceProgress=true;
  await page.evaluate(value=>window.__pushDistrictData(value),{...source,phase:'offline'});
  await page.getByText('GitHub check unavailable. Using the verified local cache.',{exact:true}).waitFor();report.checks.githubDataSourceCacheOffline=true;
  await page.evaluate(value=>window.__pushDistrictData(value),{...source,district:'or-05',phase:'ready'});
  assert.equal(await page.locator('.district-data-source').getAttribute('data-district'),'co-05','foreign district events are ignored');
  await page.evaluate(value=>window.__pushDistrictData(value),{...source,phase:'offline',message:'C:\\private-fixture\\do-not-display',settings:{credential:'never-display-credential'}});
  assert.ok(!/do-not-display|never-display-credential/.test(await page.locator('.district-data-source').innerText()));
  await page.evaluate(()=>{window.__districtRefreshFailure=true;});
  await page.getByRole('button',{name:'Check GitHub now',exact:true}).click();
  await page.getByRole('alert').filter({hasText:'The GitHub check could not be started. Try again.'}).waitFor();
  assert.ok(!/do-not-display/.test(await page.locator('.district-data-source').innerText()));
  await page.evaluate(()=>window.__pushDistrictData(null));
  await page.getByRole('link',{name:'Local dataset Data & sources ↗',exact:true}).waitFor();
  assert.equal(await page.locator('.district-data-source').count(),0,'hosted/recipient null state retains the local-source presentation');
  report.checks.githubDataSourceBoundary=true;
  assert.deepEqual(pageErrors,[]);report.checks.noUnhandledPageErrors=true;
  assert.deepEqual(unexpected,[]);report.checks.noUnexpectedApiOrExternalRequests=true;
  assert.equal(sha256(catalogueBytes(await inventory(dist))),distSha256);report.checks.distBytesUnchanged=true;
  report.passed=Object.values(report.checks).every(Boolean);
}catch(error){report.error=error instanceof Error?error.stack:String(error);process.exitCode=1;}
finally{
  await browser?.close();await new Promise(resolve=>server.close(resolve));
  report.completedAt=new Date().toISOString();await mkdir(dirname(resolve(options.output)),{recursive:true});
  await writeFile(resolve(options.output),JSON.stringify(report,null,2)+'\n');
}
console.log(JSON.stringify({passed:report.passed,version:report.version,distSha256:report.dist.sha256,checks:report.checks,error:report.error}));
