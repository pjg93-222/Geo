// Built-asset browser regression. District replies and images are synthetic; OCR runs locally.
import assert from 'node:assert/strict';
import {importListResponse} from './walk-import-fixture.mjs';
import {zipSync,unzipSync,strToU8,strFromU8} from 'fflate';
import {createServer} from 'node:http';
import {readFile,mkdir,writeFile,unlink} from 'node:fs/promises';
import {extname,resolve,sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';

const root=fileURLToPath(new URL('../dist/',import.meta.url));
const output=fileURLToPath(new URL('../../_build/image-import-verification/',import.meta.url));
const workbook=process.env.TERRIER_IMAGE_WORKBOOK||'C:/Users/pgoff/Downloads/CT Door Knock Streets 1.xlsx';
const types={'.html':'text/html','.js':'text/javascript','.css':'text/css','.png':'image/png','.mp4':'video/mp4','.woff2':'font/woff2','.wasm':'application/wasm','.gz':'application/gzip'};
const server=createServer(async(req,res)=>{
  try{
    const path=new URL(req.url,'http://localhost').pathname;
    const file=resolve(root,'.'+(extname(path)?path:'/index.html'));
    if(!file.startsWith(root.endsWith(sep)?root:root+sep)){res.writeHead(403);res.end();return;}
    const bytes=await readFile(file),range=/bytes=(\d+)-(\d*)/.exec(req.headers.range??'');
    const headers={'Content-Type':types[extname(file)]??'application/octet-stream','Cache-Control':'no-store'};
    if(range){const first=Number(range[1]),last=Math.min(Number(range[2]||bytes.length-1),bytes.length-1);res.writeHead(206,{...headers,'Content-Range':`bytes ${first}-${last}/${bytes.length}`});res.end(bytes.subarray(first,last+1));}
    else{res.writeHead(200,headers);res.end(bytes);}
  }catch{res.writeHead(404);res.end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const base=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({channel:'chrome',headless:true,args:['--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:1500,height:1000}});page.setDefaultTimeout(20000);
const errors=[],external=[],api=[],report={checks:[],errors,external};
page.on('pageerror',error=>errors.push(error.message));
await page.addInitScript(()=>{window.desktop={districts:async()=>({schema:'districts-1',current:'co-05',districts:[{id:'co-05',label:'Synthetic Colorado',short_label:'CO-05',state:'CO',port:0,ready:true,center:[-104.8,38.8],zoom:10}]}),rendererReady:async()=>{}};});
await page.context().route('**/*',async route=>{
  const url=route.request().url();if(url.startsWith(base)||url.startsWith('blob:')||url.startsWith('data:'))return route.continue();
  external.push(url);await route.abort();
});
const snapshot='a'.repeat(64),district='co-05';
const candidate=(id,label,city='Example')=>({id,label,city,state:'CO',zip:'80000',zip_filter:'',doors:1});
const canonical=value=>value.toLowerCase().replace(/[^a-z0-9]/g,'');
const choices={pinerd:[candidate(2,'Pine Rd')],oakst:[candidate(12,'Oak St','Elsewhere'),candidate(11,'Oak St')],elmst:[candidate(22,'Elm St','Elsewhere'),candidate(21,'Elm St')],cedarln:[candidate(32,'Cedar Ln','Elsewhere'),candidate(31,'Cedar Ln')]};
const streetById=new Map(Object.values(choices).flat().map(row=>[row.id,row]));
let workbookMode=false;
const junctions=[{id:1,lon:-104.808,lat:38.808,labels:['Northwest junction']},{id:2,lon:-104.792,lat:38.808,labels:['Northeast junction']},{id:3,lon:-104.808,lat:38.792,labels:['Southwest junction']},{id:4,lon:-104.792,lat:38.792,labels:['Southeast junction']}];
const context={district,snapshot,bounds:[-104.81,38.79,-104.79,38.81],junctions,roads:{type:'FeatureCollection',features:[
  {type:'Feature',properties:{name:'Pine Rd'},geometry:{type:'LineString',coordinates:[[-104.808,38.808],[-104.792,38.808]]}},
  {type:'Feature',properties:{name:'Oak St'},geometry:{type:'LineString',coordinates:[[-104.808,38.808],[-104.808,38.792]]}},
  {type:'Feature',properties:{name:'Elm St'},geometry:{type:'LineString',coordinates:[[-104.808,38.792],[-104.792,38.792]]}},
]}};
const mercator=(lon,lat)=>[6378137*lon*Math.PI/180,6378137*Math.log(Math.tan(Math.PI/4+lat*Math.PI/360))];
const [west,north]=mercator(-104.81,38.81),[east,south]=mercator(-104.79,38.79);
await page.route(`${base}/api/**`,async route=>{
  const path=new URL(route.request().url()).pathname,request=route.request().method()==='POST'?route.request().postDataJSON():null;
  api.push({path,body:request});let body={};
  if(path==='/api/auth/session')body={authenticated:true,hosted:false,email:null,csrf:'qa'};
  else if(path==='/api/v3/meta')body={schema:'v3-meta-1',snapshot:'synthetic-check',demo:false,people:5,households:5,district_label:'Synthetic Colorado',district_rate:{ovr:50,cua:50,part:50,soi:50},share_breaks:[0,.1,.2,.3],lean_cuts:[-.2,.2],measured_breaks:[0,.25,.5,.75],household_breaks:[0,10],weights:{cua:.4,part:.35,soi:.25},levels:['block','precinct'],bbox:context.bounds};
  else if(path==='/api/v3/features')body={schema:'v3-features-1',district,industries:[],industry:'',candidate:null,party_codes:{},overlay:{density:false,layers:['ovr','cua','part','soi','lean']},roi:null};
  else if(path==='/api/v3/walk/address-match')body={district,snapshot,rows:request.rows.map((row,index)=>{
    const candidates=workbookMode?[candidate(index+101,row.street)]:choices[canonical(row.street)]??[];
    candidates.forEach(item=>streetById.set(item.id,item));
    return {row:index+1,input:row.street,status:candidates.length===1?'matched':candidates.length?'pick':'none',method:candidates.length===1?'exact':'review',selected_id:candidates.length===1?candidates[0].id:undefined,candidates,truncated:false};
  })};
  else if(path==='/api/v3/walk/image-context')body=context;
  else if(path==='/api/v3/walk/image-resolve')body={district,snapshot,status:'matched',reason:'Synthetic clear layout fit.',bounds:context.bounds,fit:{x:[(east-west)/request.image.width,0,west],y:[0,(south-north)/request.image.height,north],rmsMeters:8,maxErrorMeters:14,coverage:.5,verified:true,reason:'Estimated layout fit from four distinct labels.'},resolutions:request.labels.map(line=>({text:line.text,street_id:{pinerd:2,oakst:11,elmst:21,cedarln:31}[canonical(line.text)],city:'Example',status:'matched',distance_m:8,road_only:false}))};
  else if(path==='/api/v3/walk/address-list')body=importListResponse(request.streets,id=>streetById.get(id),{district,snapshot});
  else if(path==='/api/v3/walk/lists')body={lists:[]};
  else if(path==='/api/v3/walk/options')body={options:[]};
  else if(path==='/api/v3/walk/streets')body={streets:[]};
  else body={type:'FeatureCollection',features:[]};
  await route.fulfill({json:body});
});
const waitChoice=(index,value)=>page.waitForFunction(({index,value})=>document.querySelector(`[aria-label="Match for street ${index}"]`)?.value===value,{index,value});
const card=(index=1)=>page.locator('.walk-image-card').nth(index-1);
const openCard=async(index=1)=>{if(await card(index).getAttribute('open')===null)await card(index).locator('summary').first().click();};
function scopedWorkbook(png){
  const xml=strToU8;
  const cells=[[3,'Pine Rd'],[4,'Oak St'],[23,'Pine Rd'],[24,'Oak St']].map(([row,text])=>`<row r="${row}"><c r="K${row}" t="inlineStr"><is><t>${text}</t></is></c></row>`).join('');
  const marker=(kind,row,column)=>`<xdr:${kind}><xdr:col>${column}</xdr:col><xdr:row>${row}</xdr:row></xdr:${kind}>`;
  return zipSync({
    '[Content_Types].xml':xml('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>'),
    'xl/workbook.xml':xml('<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sections" sheetId="1" r:id="sheet1"/></sheets></workbook>'),
    'xl/_rels/workbook.xml.rels':xml('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="sheet1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'),
    'xl/worksheets/sheet1.xml':xml(`<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheetData>${cells}</sheetData><drawing r:id="drawing1"/></worksheet>`),
    'xl/worksheets/_rels/sheet1.xml.rels':xml('<Relationships><Relationship Id="drawing1" Target="../drawings/drawing1.xml" Type="drawing"/></Relationships>'),
    'xl/drawings/drawing1.xml':xml(`<xdr:wsDr>${[2,22].map((row,i)=>`<xdr:twoCellAnchor>${marker('from',row,0)}${marker('to',row+7,8)}<xdr:pic><a:blip r:embed="image${i+1}"/></xdr:pic></xdr:twoCellAnchor>`).join('')}</xdr:wsDr>`),
    'xl/drawings/_rels/drawing1.xml.rels':xml('<Relationships><Relationship Id="image1" Target="../media/image1.png" Type="image"/><Relationship Id="image2" Target="../media/image2.png" Type="image"/></Relationships>'),
    'xl/media/image1.png':png,'xl/media/image2.png':png,
  });
}
const unzipDownload=async(name)=>{
  const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'Download all lists ZIP',exact:true}).click()]);
  const path=resolve(output,name);await download.saveAs(path);const files=unzipSync(await readFile(path));
  return {files,manifest:JSON.parse(strFromU8(files['manifest.json']))};
};
try{
  await mkdir(output,{recursive:true});
  await Promise.all(['failure.png','failure.txt'].map(name=>unlink(resolve(output,name)).catch(error=>{if(error.code!=='ENOENT')throw error;})));
  await page.goto(base+'/walk');await page.getByRole('button',{name:'Address file',exact:true}).click();
  await page.getByLabel('Split even and odd',{exact:true}).uncheck();
  const png=Buffer.from(await page.evaluate(()=>{
    const canvas=document.createElement('canvas');canvas.width=1000;canvas.height=800;const ctx=canvas.getContext('2d');
    ctx.fillStyle='white';ctx.fillRect(0,0,1000,800);ctx.fillStyle='black';ctx.font='bold 60px Arial';
    for(const [name,x,y] of [['Oak St',80,140],['Pine Rd',580,140],['Elm St',80,640],['Cedar Ln',580,640]])ctx.fillText(name,x,y);
    return canvas.toDataURL('image/png').split(',')[1];
  }),'base64');
  await writeFile(resolve(output,'synthetic-map.png'),png);
  const picture={name:'synthetic-map.png',mimeType:'image/png',buffer:png};
  workbookMode=true;
  await page.getByLabel('Upload an address file',{exact:true}).setInputFiles(workbook);
  await page.waitForFunction(()=>document.querySelectorAll('.walk-image-card').length===3);
  await page.getByText('Found 1 exact address and 33 street requests.',{exact:true}).waitFor();
  assert.equal(api.filter(call=>call.path==='/api/v3/walk/image-resolve').length,0);
  const workbookMatch=api.find(call=>call.path==='/api/v3/walk/address-match');assert.equal(workbookMatch.body.rows.length,33);assert.ok(workbookMatch.body.rows.every(row=>!row.city));
  await page.screenshot({path:resolve(output,'workbook-three-images.png')});
  report.checks.push('Actual workbook preserves 33 text street requests and the separate address, with three embedded maps; map labels do not silently become requests');

  workbookMode=false;
  await page.getByLabel('Upload an address file',{exact:true}).setInputFiles(picture);
  await page.getByText(/^Found 0 exact address(?:es|s) and 0 street requests\.$/).waitFor();await openCard();
  await page.getByLabel('Reviewed image text 1',{exact:true}).fill('Oak St\nPine Rd\nElm St\nCedar Ln');
  await page.getByRole('button',{name:'Match image labels 1',exact:true}).click();
  await page.getByLabel('Geographic road map 1',{exact:true}).waitFor();
  assert.equal(api.filter(call=>call.path==='/api/v3/walk/image-resolve').length,0,'manually typed labels have no fabricated OCR boxes');
  await page.getByText(/^Found 0 exact address(?:es|s) and 0 street requests\.$/).waitFor();
  assert.equal(api.filter(call=>call.path==='/api/v3/walk/address-doors').length,0,'viewing image context is read-only');
  for(const [index,value] of [[1,'11:'],[2,'2:'],[3,'21:'],[4,'31:']])await page.getByLabel(`Image 1 street ${index}`,{exact:true}).selectOption(value);
  await page.getByLabel('Image 1 street 2',{exact:true}).selectOption('');
  await page.getByRole('button',{name:'Match image labels 1',exact:true}).click();await page.getByLabel('Geographic road map 1',{exact:true}).waitFor();
  assert.equal(await page.getByLabel('Image 1 street 1',{exact:true}).inputValue(),'11:','manual image match survives a same-context recheck');
  assert.equal(await page.getByLabel('Image 1 street 2',{exact:true}).inputValue(),'','explicitly excluded image evidence is not restored by an exact match');
  await page.getByLabel('Image 1 street 2',{exact:true}).selectOption('2:');
  report.checks.push('Manual image choices and explicit evidence exclusions survive same-context image matching');
  const source=page.getByLabel('Image alignment 1',{exact:true});
  for(const [index,x,y] of [[1,.1,.1],[2,.9,.1],[3,.1,.9]]){
    const box=await source.boundingBox();assert.ok(box);await source.click({position:{x:box.width*x,y:box.height*y}});await page.getByLabel('Matching junction 1',{exact:true}).selectOption(String(index));
  }
  const confirm=page.getByRole('checkbox',{name:'I checked that the road overlay follows the source map.'});
  assert.equal(await confirm.isDisabled(),true);await page.getByText(/Add a fourth (?:distinct )?point to check the fit/).waitFor();
  const duplicateBox=await source.boundingBox();await source.click({position:{x:duplicateBox.width*.1,y:duplicateBox.height*.1}});await page.getByLabel('Matching junction 1',{exact:true}).selectOption('1');
  assert.equal(await confirm.isDisabled(),true,'repeating a junction does not provide a fourth independent control point');
  await page.getByRole('button',{name:'Remove point 4',exact:true}).click();
  const box=await source.boundingBox();await source.click({position:{x:box.width*.9,y:box.height*.9}});await page.getByLabel('Matching junction 1',{exact:true}).selectOption('4');
  await confirm.check();await page.getByRole('button',{name:'Use reviewed image locations',exact:true}).click();
  await card().locator('summary').filter({hasText:'Location evidence applied'}).waitFor();
  report.checks.push('Manual labels load only geographic context; three control points or a duplicated fourth cannot confirm a fit, while four distinct spread points can');
  await page.getByRole('button',{name:'Add reviewed text to walk requests',exact:true}).click();
  await page.getByText(/^Found 0 exact address(?:es|s) and 4 street requests\.$/).waitFor();await waitChoice(1,'11:');
  await page.getByText('Matched using image surroundings',{exact:true}).first().waitFor();
  await page.getByRole('button',{name:'Create sheets from selected streets',exact:true}).click();
  const manual=await unzipDownload('manual-image-evidence.zip');
  assert.equal(manual.manifest.doors,4);assert.equal(manual.manifest.imageEvidence.length,1);assert.equal(manual.manifest.imageEvidence[0].alignment.points.length,4);
  assert.equal(manual.manifest.streetMatches.filter(match=>match.method==='image-layout').length,3);assert.equal(manual.manifest.streetMatches.find(match=>match.street==='Pine Rd').method,'exact');assert.ok(manual.manifest.sourceRows.some(row=>row.note?.includes('Image text reviewed by user')));assert.equal(manual.manifest.voters,4);
  await page.screenshot({path:resolve(output,'manual-alignment.png')});
  report.checks.push('Only explicitly added reviewed image text becomes four walk requests; ZIP preserves all four doors, source notes and checked alignment evidence');
  await page.getByRole('button',{name:'Remove point 4',exact:true}).click();await waitChoice(1,'');
  assert.equal(await page.getByRole('button',{name:'Download all lists ZIP',exact:true}).count(),0);
  await page.getByLabel('Reviewed image text 1',{exact:true}).fill('Oak St\nPine Rd');
  assert.equal(await page.getByLabel('Geographic road map 1',{exact:true}).count(),0);
  report.checks.push('Removing a control point or editing reviewed text invalidates dependent image choices and prevents stale sheet export');

  const sections=scopedWorkbook(png);await writeFile(resolve(output,'two-map-sections.xlsx'),sections);
  await page.getByLabel('Upload an address file',{exact:true}).setInputFiles({name:'two-map-sections.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:Buffer.from(sections)});
  await page.waitForFunction(()=>document.querySelectorAll('.walk-image-card').length===2);await waitChoice(1,'2:');await waitChoice(3,'2:');
  for(const [image,selected] of [[1,'11:'],[2,'12:']]){
    await openCard(image);await page.getByLabel(`Reviewed image text ${image}`,{exact:true}).fill('Oak St');await page.getByRole('button',{name:`Match image labels ${image}`,exact:true}).click();
    await page.getByLabel(`Image ${image} street 1`,{exact:true}).selectOption(selected);await card(image).getByRole('button',{name:'Use reviewed image locations',exact:true}).click();
    await waitChoice(image===1?2:4,selected);
    if(image===1)assert.equal(await page.getByLabel('Match for street 4',{exact:true}).inputValue(),'','image one cannot pick the same-name street beside image two');
  }
  await waitChoice(2,'11:');await waitChoice(4,'12:');
  await page.getByRole('button',{name:'Create sheets from selected streets',exact:true}).click();const scoped=await unzipDownload('scoped-workbook-evidence.zip');
  assert.equal(scoped.manifest.doors,4);assert.equal(scoped.manifest.imageEvidence.length,2);
  assert.equal(scoped.manifest.streetMatches.find(row=>row.sourceCell==='K4').streetId,11);assert.equal(scoped.manifest.streetMatches.find(row=>row.sourceCell==='K24').streetId,12);
  report.checks.push('Same-name streets beside two workbook maps retain separate image choices and ZIP provenance without leaking between sections');
  await page.getByRole('button',{name:'Read image 1',exact:true}).click();
  assert.equal(await page.getByRole('button',{name:'Read image 2',exact:true}).isDisabled(),true,'only one image can own the OCR job');
  await card(1).getByRole('button',{name:'Cancel image reading',exact:true}).click();
  await page.waitForFunction(()=>![...document.querySelectorAll('button')].filter(button=>/^Read image [12]$/.test(button.textContent)).some(button=>button.disabled));
  report.checks.push('A shared OCR job lock disables another image while reading and cancellation releases both cards');

  const fourStreets={name:'four-streets.txt',mimeType:'text/plain',buffer:Buffer.from('Pine Rd\nOak St\nElm St\nCedar Ln')};
  await page.getByLabel('Upload an address file',{exact:true}).setInputFiles(fourStreets);
  await waitChoice(1,'2:');await page.getByLabel('Add a map or address image',{exact:true}).setInputFiles(picture);await openCard();
  const beforeOcr=api.length;
  await page.getByRole('button',{name:'Read image 1',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('[aria-label="Match for street 2"]')?.value==='11:',null,{timeout:120000});
  const resolveCall=api.slice(beforeOcr).find(call=>call.path==='/api/v3/walk/image-resolve');assert.ok(resolveCall,'real OCR produces sufficient positioned labels for automatic layout matching');
  assert.ok(resolveCall.body.labels.length>=4);assert.ok(resolveCall.body.labels.every(line=>Number.isFinite(line.x)&&Number.isFinite(line.y)&&line.width>0&&line.height>0));
  assert.ok(resolveCall.body.street_ids.every(id=>id===2),'automatic layout anchors come only from independent exact matches');
  assert.equal(resolveCall.body.image.width,1000);assert.equal(resolveCall.body.image.height,800);
  await page.getByText(/^Found 0 exact address(?:es|s) and 4 street requests\.$/).waitFor();
  await page.getByRole('button',{name:'Create sheets from selected streets',exact:true}).click();
  const automatic=await unzipDownload('automatic-image-evidence.zip');
  assert.equal(automatic.manifest.doors,4);assert.equal(automatic.manifest.imageEvidence.length,1);assert.equal(automatic.manifest.imageEvidence[0].automatic,true);assert.equal(automatic.manifest.imageEvidence[0].alignment.x.length,3);
  assert.equal(automatic.manifest.streetMatches.filter(match=>match.method==='image-layout').length,3);assert.equal(automatic.manifest.streetMatches.find(match=>match.street==='Pine Rd').method,'exact');
  assert.ok(api.slice(beforeOcr).filter(call=>call.path==='/api/v3/walk/address-match').every(call=>(call.body.selected_streets??[]).every(street=>street.id===2)),'automatic image choices are not recycled as independent proximity anchors');
  await page.screenshot({path:resolve(output,'automatic-image-layout.png')});
  report.checks.push('Real local OCR sends positioned labels to mocked layout matching; clear image choices recheck existing requests without expanding their scope or chaining inferred anchors');
  report.checks.push('Automatic image fit and selected street provenance are recorded in the ZIP manifest');
  await page.getByLabel('Match for street 1',{exact:true}).selectOption('__exclude');await waitChoice(2,'');
  await page.waitForFunction(()=>document.querySelector('[aria-label="Image alignment 1"]')?.querySelectorAll('polyline').length===0);
  assert.equal(await card().locator('summary').filter({hasText:'Location evidence applied'}).count(),0);
  assert.equal(await page.getByRole('button',{name:'Download all lists ZIP',exact:true}).count(),0);
  report.checks.push('Excluding an independent parent street anchor clears the previous image fit, evidence badge and dependent street choices');
  await page.getByLabel('Match for street 1',{exact:true}).selectOption('2:');await waitChoice(1,'2:');
  await page.getByRole('button',{name:'Match image labels 1',exact:true}).click();await waitChoice(2,'11:');
  await page.getByLabel('Reviewed image text 1',{exact:true}).fill('Oak St\nPine Rd');await waitChoice(2,'');
  assert.equal(await page.getByRole('button',{name:'Download all lists ZIP',exact:true}).count(),0);
  report.checks.push('Editing OCR text removes automatic evidence and clears dependent street choices');
  assert.deepEqual(external,[],'image reading never sends image bytes or OCR text to an external service');assert.deepEqual(errors,[]);
  console.log(JSON.stringify({status:'passed',...report}));
}catch(error){report.failure=error.stack;await page.screenshot({path:resolve(output,'failure.png')}).catch(()=>{});await writeFile(resolve(output,'failure.txt'),await page.locator('body').innerText().catch(()=>''));throw error;}
finally{
  await writeFile(resolve(output,'checks.json'),JSON.stringify(report,null,2));await writeFile(resolve(output,'api-calls.json'),JSON.stringify(api,null,2));
  await browser.close();await new Promise(resolve=>{server.close(resolve);server.closeAllConnections();});
}
