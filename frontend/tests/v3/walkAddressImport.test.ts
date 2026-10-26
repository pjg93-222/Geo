import { test } from 'node:test';
import assert from 'node:assert/strict';
import { zipSync, unzipSync, strToU8 } from 'fflate';
import { parseAddressText, readAddressFile } from '../../src/v3/lib/walkAddressImport.ts';
import {validateWalkWorkbook} from '../../src/v3/lib/walkWorkbookBounds.ts';
import {assertWalkImages,extractWalkWorkbookImages,readWalkImageFile} from '../../src/v3/lib/walkImageImport.ts';

test('quoted CSV addresses keep units, city, state, ZIP and source row', () => {
  const read = parseAddressText('\ufeffName,Address,Unit,City,State,ZIP\r\nExample,123 Main St,2,Hartford,CT,06103\r\nOther,"125 Main St Apt 4, Hartford, CT 06103",,,,\r\n', 'doors.csv');
  assert.equal(read.sourceRows, 2);
  assert.deepEqual(read.issues, []);
  assert.deepEqual(read.addresses.map(a => [a.address, a.house, a.street, a.unit, a.city, a.state, a.zip, a.sourceRow]), [
    ['123 Main St #2, Hartford, CT 06103', '123', 'Main St', '2', 'Hartford', 'CT', '06103', 2],
    ['125 Main St Apt 4, Hartford, CT 06103', '125', 'Main St', 'Apt 4', 'Hartford', 'CT', '06103', 3],
  ]);
  assert.equal(read.addresses[0].sourceFile, 'doors.csv');
});

test('structured house and street fields work with TSV and semicolon CSV', () => {
  const tsv = parseAddressText('House Number\tPre Direction\tStreet Name\tStreet Type\tUnit\tCity\tState\tPostal Code\n104\tN\tMain\tSt\tB\tPortland\tOR\t97201', 'doors.tsv');
  assert.deepEqual(tsv.issues, []);
  assert.equal(tsv.addresses[0].address, '104 N Main St #B, Portland, OR 97201');
  const csv = parseAddressText('address;city;state;zip\n106 Oak Rd;Salem;OR;97301', 'doors.csv');
  assert.equal(csv.addresses[0].address, '106 Oak Rd, Salem, OR 97301');
  const splitAddress = parseAddressText('house_number,address\n108,Oak Rd', 'doors.csv');
  assert.equal(splitAddress.addresses[0].address, '108 Oak Rd');
  const headerless = parseAddressText('110 Oak Rd,Salem,OR,97301', 'doors.csv');
  assert.deepEqual(headerless.issues, []);
  assert.equal(headerless.addresses[0].address, '110 Oak Rd, Salem, OR 97301');
});

test('plain text reads whole lines with commas, preserves duplicates and skips only empty lines', () => {
  const read = parseAddressText('123 Main St, Hartford, CT 06103\n\n123 Main St, Hartford, CT 06103\n125 Main St, Unit 7, Hartford, CT 06103');
  assert.equal(read.sourceRows, 3);
  assert.equal(read.addresses.length, 3);
  assert.deepEqual(read.addresses.map(a => a.sourceRow), [1, 3, 4]);
  assert.equal(read.addresses[0].address, read.addresses[1].address);
  assert.equal(read.addresses[2].unit, 'Unit 7');
});

test('street requests remain distinct and each unreadable or missing address is reported', () => {
  const read = parseAddressText('address,name\n123 Main St,One\n,Two\nMain St,Three\nPO Box 123,Four\n125 Main St,Five', 'doors.csv');
  assert.equal(read.sourceRows, 5);
  assert.equal(read.addresses.length, 2);
  assert.deepEqual(read.issues.map(issue => issue.row), [3, 5]);
  assert.deepEqual(read.streets?.map(street=>[street.street,street.sourceRow]),[['Main St',4]]);
  assert.equal(read.addresses.length + (read.streets?.length??0) + read.issues.length, read.sourceRows);
});

test('physical source rows account for blank lines and multiline quoted cells', () => {
  const read = parseAddressText('Address\n"123 Main St\nApt 3"\n\nNo house\n125 Main St', 'doors.csv');
  assert.equal(read.addresses[0].address, '123 Main St Apt 3');
  assert.equal(read.addresses[1].sourceRow, 6);
  assert.equal(read.issues[0].row, 5);
});

test('conflicting address locations and units are reported', () => {
  const read = parseAddressText('address,unit,city\n"123 Main St Apt 2, Salem, OR 97301",3,Salem\n"125 Main St, Salem, OR 97301",,Portland', 'doors.csv');
  assert.equal(read.addresses.length, 0);
  assert.equal(read.issues.length, 2);
  assert.match(read.issues[0].message, /unit.*conflicts/);
  assert.match(read.issues[1].message, /city.*conflicts/);
  const house = parseAddressText('address,house_number\n123 Main St,125', 'doors.csv');
  assert.match(house.issues[0].message, /house number.*conflicts/);
});

test('malformed CSV, bad headers and unsupported binaries fail clearly', () => {
  assert.throws(() => parseAddressText('address\n"123 Main St', 'doors.csv'), /closing quote/);
  assert.throws(() => parseAddressText('address\n"123 Main St"x', 'doors.csv'), /closing quote/);
  assert.throws(() => parseAddressText('address,address\n123 Main St,125 Main St', 'doors.csv'), /More than one/);
  assert.throws(() => parseAddressText('city,state\nPortland,OR', 'doors.csv'), /column named address/);
  assert.throws(() => parseAddressText('123 Main St', 'doors.pdf'), /CSV, TSV, TXT, JSON or XLSX/);
  assert.throws(() => parseAddressText('\0binary', 'doors.txt'), /binary or unreadable/);
  const malformed = parseAddressText('address,city\n123 Main St,Salem,OR', 'doors.csv');
  assert.match(malformed.issues[0].message, /more values than the header/);
});

test('JSON strings and field objects retain every entry and flag invalid entries', () => {
  const read = parseAddressText(JSON.stringify({ addresses: [
    '123 Main St, Hartford, CT 06103',
    { house_number: 125, street_name: 'Main St', unit: '4', city: 'Hartford', state: 'CT', zip: '06103', name: 'Unused source value' },
    null,
    { address: '127 Main St', city: { nested: 'invalid' } },
  ] }), 'doors.json');
  assert.equal(read.sourceRows, 4);
  assert.equal(read.addresses.length, 2);
  assert.equal(read.addresses[1].unit, '4');
  assert.equal(read.addresses[1].zip, '06103');
  assert.deepEqual(read.issues.map(issue => issue.row), [3, 4]);
  assert.throws(() => parseAddressText('{invalid', 'doors.json'), /not valid JSON/);
});

// Minimal real XLSX fixture exercises the lazy loader, XML parser, all-sheet iteration and source provenance.
function workbook(): Uint8Array {
  const xml = (text: string): Uint8Array => strToU8(text);
  const sheet = (address: string, row: number): string => `<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Address</t></is></c></row><row r="${row}"><c r="A${row}" t="inlineStr"><is><t>${address}</t></is></c></row></sheetData></worksheet>`;
  return zipSync({
    '[Content_Types].xml': xml('<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>'),
    'xl/workbook.xml': xml('<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="North" sheetId="1" r:id="rId1"/><sheet name="South" sheetId="2" r:id="rId2"/></sheets></workbook>'),
    'xl/_rels/workbook.xml.rels': xml('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/></Relationships>'),
    'xl/worksheets/sheet1.xml': xml(sheet('123 Main St', 2)),
    'xl/worksheets/sheet2.xml': xml(sheet('125 Main St Apt 4', 4)),
  });
}

test('XLSX imports all worksheets and retains blank row offsets', async () => {
  const bytes = workbook();
  const read = await readAddressFile({ name: 'doors.xlsx', size: bytes.length,
    text: async () => { throw new Error('binary workbook must not be decoded as text'); },
    arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer });
  assert.equal(read.sourceRows, 2);
  assert.deepEqual(read.issues, []);
  assert.deepEqual(read.addresses.map(a => [a.address, a.sourceFile, a.sourceSheet, a.sourceRow]), [
    ['123 Main St', 'doors.xlsx', 'North', 2], ['125 Main St Apt 4', 'doors.xlsx', 'South', 4],
  ]);
});

test('file reader rejects oversized and unreadable workbooks', async () => {
  const file = { name: 'doors.xlsx', text: async () => '', arrayBuffer: async () => new ArrayBuffer(0) };
  await assert.rejects(readAddressFile(file), /could not be read/);
  await assert.rejects(readAddressFile({ ...file, size: 21 * 1024 * 1024 }), /20 MB/);
});

test('titles before headers and common field aliases retain provenance without applying heading locations',()=>{
  const read=parseAddressText('October team assignment\nTown: Hartford\nSitus Address;Town / City;State;ZIP Code 5\n123 Main St;Hartford;CT;06103\n125 Main St;Hartford;CT;06103','doors.csv');
  assert.deepEqual(read.issues,[]);
  assert.equal(read.addresses.length,2);
  assert.equal(read.addresses[0].sourceRow,4);
  assert.equal(read.addresses[0].sourceCell,'A4');
  assert.equal(read.notes?.length,2);
  assert.equal(read.addresses[0].zip,'06103');
});

test('headerless rows can contain several independent street requests and exact addresses',()=>{
  const read=parseAddressText('Main St\tOak Rd\n123 Pine Ln\t125 Cedar Dr\n\nUnknown instructions','streets.tsv');
  assert.equal(read.streets?.length,2);
  assert.equal(read.addresses.length,2);
  assert.equal(read.sourceRows,5);
  assert.deepEqual(read.streets?.map(street=>street.sourceCell),['A1','B1']);
  assert.deepEqual(read.addresses.map(address=>address.sourceCell),['A2','B2']);
  assert.equal(read.issues[0].sourceCell,'A4');
});

test('street fields carry their own locality, business context remains separate and duplicates stay',()=>{
  const read=parseAddressText(JSON.stringify({addresses:['888 Queen St, Southington, CT 06489 Example Credit Union'],streets:[{street:'Main St',town:'Hartford',state:'CT',zip:'06103'},{street:'Main St'},{street:'Main St'}]}),'mixed.json');
  assert.deepEqual(read.issues,[]);
  assert.equal(read.addresses[0].address,'888 Queen St, Southington, CT 06489');
  assert.equal(read.addresses[0].note,'Example Credit Union');
  assert.equal(read.streets?.length,3);
  assert.deepEqual(read.streets?.map(street=>street.city),['Hartford','','']);
  assert.equal(read.streets?.[0].zip,'06103');
});

test('full addresses without commas infer only an unambiguous street and town boundary',()=>{
  const read=parseAddressText('123 Main St Hartford CT 06103\nMain St Hartford CT 06103\n125 Broadway New York NY 10001\n127 St Charles Ave Hartford CT 06103');
  assert.deepEqual(read.addresses.map(address=>[address.street,address.city,address.state,address.zip]),[['Main St','Hartford','CT','06103']]);
  assert.deepEqual(read.streets?.map(street=>[street.street,street.city,street.zip]),[['Main St','Hartford','06103']]);
  assert.deepEqual(read.issues.map(issue=>issue.row),[3,4]);
  assert.match(read.issues[0].message,/street and town are unclear/);
  const explicit=parseAddressText('address,city\n125 Broadway New York NY 10001,New York','doors.csv');
  assert.equal(explicit.addresses[0].street,'Broadway');
});

test('contradictory street columns are not ignored',()=>{
  const read=parseAddressText('Address,Street\n123 Main St,Oak St\n125 Main St,Main St','doors.csv');
  assert.equal(read.addresses.length,1);
  assert.match(read.issues[0].message,/street.*conflicts/);
});

test('a unit without a house number never expands to a whole street',()=>{
  const read=parseAddressText('street,unit\nMain St,2','doors.csv');
  assert.equal(read.streets?.length,0);
  assert.equal(read.addresses.length,0);
  assert.match(read.issues[0].message,/unit needs an exact house number/);
  const inline=parseAddressText(JSON.stringify([{street:'Main St Apt 2'}]),'doors.json');
  assert.equal(inline.streets?.length,0);
  assert.match(inline.issues[0].message,/unit needs an exact house number/);
});

test('separate direction and street type fields cannot contradict a full address',()=>{
  const read=parseAddressText('address,pre direction,street type,post direction\n123 Main St,N,,\n125 Main St,,Road,\n127 Main St,,,W\n129 N Main Street,,St,','doors.csv');
  assert.equal(read.issues.length,3);
  assert.equal(read.addresses.length,1);
  assert.match(read.issues[0].message,/pre direction/);
  assert.match(read.issues[1].message,/street type/);
  assert.match(read.issues[2].message,/post direction/);
});

function sparseWorkbook():Uint8Array{
  const xml=(text:string)=>strToU8(text);
  const cell=(at:string,text:string)=>`<c r="${at}" t="inlineStr"><is><t>${text}</t></is></c>`;
  const rows=[`<row r="1">${cell('A1','888 Queen St, Southington, CT 06489 Example Credit Union')}</row>`];
  const offsets=[...Array.from({length:8},(_,i)=>i+4),...Array.from({length:9},(_,i)=>i+26),...Array.from({length:16},(_,i)=>i+47)];
  offsets.forEach((row,i)=>rows.push(`<row r="${row}">${cell(`${row<47?'K':'L'}${row}`,`Example ${i+1} Rd`)}</row>`));
  return zipSync({
    '[Content_Types].xml':xml('<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>'),
    'xl/workbook.xml':xml('<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Layout" sheetId="1" r:id="rId1"/></sheets></workbook>'),
    'xl/_rels/workbook.xml.rels':xml('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'),
    'xl/worksheets/sheet1.xml':xml(`<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>${rows.join('')}</sheetData></worksheet>`),
  });
}

test('sparse XLSX reads all 33 scattered street cells and a separate anchor with exact cell provenance',async()=>{
  const bytes=sparseWorkbook();
  const read=await readAddressFile({name:'sparse.xlsx',arrayBuffer:async()=>bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength) as ArrayBuffer,text:async()=>''});
  assert.deepEqual(read.issues,[]);
  assert.equal(read.addresses.length,1);
  assert.equal(read.addresses[0].sourceCell,'A1');
  assert.equal(read.addresses[0].note,'Example Credit Union');
  assert.equal(read.streets?.length,33);
  assert.equal(read.streets?.[0].sourceCell,'K4');
  assert.equal(read.streets?.at(-1)?.sourceCell,'L62');
  assert.ok(read.streets?.every(street=>street.sourceSheet==='Layout'&&street.city===''&&street.state===''&&street.zip===''));
  assert.equal(read.sourceRows,34);
});

test('grouped street XLSX preserves park context and group labels while reading Loop streets',async()=>{
  const files=unzipSync(sparseWorkbook());
  const entries=['Happy Valley Nature Park','','Group 1','SE Plover Dr','SE Purple Finch Loop','','Group 4A (North)','SE Emerald Loop','Unreadable entry'];
  files['xl/worksheets/sheet1.xml']=strToU8(`<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>${entries.map((text,i)=>`<row r="${i+1}"><c r="A${i+1}" t="inlineStr"><is><t>${text}</t></is></c></row>`).join('')}</sheetData></worksheet>`);
  const bytes=zipSync(files);
  const read=await readAddressFile({name:'groups.xlsx',arrayBuffer:async()=>bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength) as ArrayBuffer,text:async()=>''});
  assert.deepEqual(read.streets?.map(street=>[street.street,street.sourceCell]),[['SE Plover Dr','A4'],['SE Purple Finch Loop','A5'],['SE Emerald Loop','A8']]);
  assert.deepEqual(read.notes?.map(note=>[note.text,note.sourceCell]),[['Happy Valley Nature Park','A1'],['Group 1','A3'],['Group 4A (North)','A7']]);
  assert.deepEqual(read.issues.map(issue=>[issue.source,issue.sourceCell]),[['Unreadable entry','A9']]);
  assert.equal(read.sourceRows,4);
});

test('street section restrictions require explicit boundaries and never expand to the whole street',()=>{
  for(const input of ['SE 152nd Ave (north section)\nSE 152nd Ave (south section)','street\nSE 152nd Ave (north section)\nSE 152nd Ave (south section)']){
    const read=parseAddressText(input,'sections.txt');
    assert.equal(read.streets?.length,0);
    assert.equal(read.addresses.length,0);
    assert.equal(read.issues.length,2);
    assert.ok(read.issues.every(issue=>/restriction needs exact addresses or defined street boundaries/.test(issue.message)));
    assert.match(read.issues[0].source!,/north section/);
    assert.match(read.issues[1].source!,/south section/);
  }
  const explicit=parseAddressText('street\nHappy Valley Nature Park','street.csv');
  assert.equal(explicit.streets?.[0].street,'Happy Valley Nature Park');
  assert.deepEqual(explicit.notes,[]);
});

test('JSON table arrays and multiline independent entries are supported without losing unit lines',()=>{
  const read=parseAddressText(JSON.stringify({data:[['Delivery list'],['House No','Street Name','City'],[123,'Main St','Hartford'],['','Oak Rd','Hartford']]}),'table.json');
  assert.deepEqual(read.issues,[]);
  assert.equal(read.addresses[0].address,'123 Main St, Hartford');
  assert.equal(read.streets?.[0].street,'Oak Rd');
  assert.equal(read.notes?.[0].text,'Delivery list');
  const multi=parseAddressText('"123 Main St\n125 Oak Rd"','multiple.csv');
  assert.deepEqual(multi.addresses.map(address=>address.address),['123 Main St','125 Oak Rd']);
});

test('workbook preflight bounds declared ZIP expansion and count before decompression',()=>{
  const original=zipSync({'xl/worksheets/sheet1.xml':strToU8('<worksheet/>')});
  const oversized=original.slice(),largeView=new DataView(oversized.buffer,oversized.byteOffset,oversized.byteLength);
  let central=-1;for(let i=0;i<oversized.length-4;i++)if(largeView.getUint32(i,true)===0x02014b50){central=i;break;}
  assert.ok(central>=0);largeView.setUint32(central+24,128*1024*1024+1,true);
  assert.throws(()=>validateWalkWorkbook(oversized),/uncompressed workbook exceeds 128 MiB/);
  const crowded=original.slice(),crowdedView=new DataView(crowded.buffer,crowded.byteOffset,crowded.byteLength),end=crowded.length-22;
  crowdedView.setUint16(end+8,10001,true);crowdedView.setUint16(end+10,10001,true);
  assert.throws(()=>validateWalkWorkbook(crowded),/more than 10,000 internal files/);
  const zip64=original.slice(),zip64View=new DataView(zip64.buffer,zip64.byteOffset,zip64.byteLength);
  zip64View.setUint16(zip64.length-22+10,65535,true);
  assert.throws(()=>validateWalkWorkbook(zip64),/ZIP64/);
});

test('workbook preflight rejects sparse row, column, dimension and total rectangles before allocation',()=>{
  const make=(xml:string)=>zipSync({'xl/worksheets/sheet1.xml':strToU8(xml)});
  assert.throws(()=>validateWalkWorkbook(make('<worksheet><sheetData><row r="100001"/></sheetData></worksheet>')),/100,000 rows/);
  assert.throws(()=>validateWalkWorkbook(make('<worksheet><sheetData><row r="1"><c r="BMM1"/></row></sheetData></worksheet>')),/1,024 columns/);
  assert.throws(()=>validateWalkWorkbook(make('<worksheet><dimension ref="A1:AX50000"/></worksheet>')),/2,000,000 cells/);
  assert.throws(()=>validateWalkWorkbook(make('<worksheet><sheetData><row custom="a&gt;b" r="100001"/></sheetData></worksheet>')),/100,000 rows/);
  const combined=zipSync({'xl/worksheets/one.xml':strToU8('<worksheet><dimension ref="A1:Y50000"/></worksheet>'),'xl/worksheets/two.xml':strToU8('<worksheet><dimension ref="A1:Y50000"/></worksheet>')});
  assert.throws(()=>validateWalkWorkbook(combined),/combined worksheet rectangles/);
  assert.doesNotThrow(()=>validateWalkWorkbook(make('<worksheet><dimension ref="A1:L62"/><sheetData><row r="62"><c r="L62"/></row></sheetData></worksheet>')));
});

test('workbook preflight follows custom worksheet relationships and rejects unsupported XML',()=>{
  const redirected=zipSync({'xl/_rels/workbook.xml.rels':strToU8('<Relationships><Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="custom/data.xml"/></Relationships>'),'xl/custom/data.xml':strToU8('<worksheet><row r="999999"/></worksheet>')});
  assert.throws(()=>validateWalkWorkbook(redirected),/100,000 rows/);
  const entities=zipSync({'xl/worksheets/sheet1.xml':strToU8('<!DOCTYPE worksheet [<!ENTITY x "repeat">]><worksheet/>')});
  assert.throws(()=>validateWalkWorkbook(entities),/unsupported XML/);
  const repeated=zipSync({'xl/workbook.xml':strToU8('<workbook><sheets><sheet r:id="r1"/><sheet r:id="r1"/></sheets></workbook>'),'xl/_rels/workbook.xml.rels':strToU8('<Relationships><Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/one.xml"/></Relationships>'),'xl/worksheets/one.xml':strToU8('<worksheet><dimension ref="A1:Y50000"/></worksheet>')});
  assert.throws(()=>validateWalkWorkbook(repeated),/combined worksheet rectangles/);
});

const tinyPng=Uint8Array.from(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1EAAAAASUVORK5CYII=','base64'));
const imageData=`data:image/png;base64,${Buffer.from(tinyPng).toString('base64')}`;
const imageFile=(name:string,bytes:Uint8Array)=>({name,size:bytes.length,arrayBuffer:async()=>bytes.slice().buffer as ArrayBuffer,text:async()=>{throw new Error('Image bytes must not be decoded as address text');}});

test('CSV image data preserves address scope and cell provenance while image references remain notes',()=>{
  const read=parseAddressText(`Address,Image\n123 Main St,"${imageData}"\nOak Rd,https://example.invalid/map.png\nPine St,C:\\maps\\local.jpg`,'maps.csv');
  assert.deepEqual(read.issues,[]);
  assert.equal(read.addresses.length,1);
  assert.deepEqual(read.streets?.map(street=>street.street),['Oak Rd','Pine St']);
  assert.equal(read.images?.length,1);
  assert.deepEqual([read.images?.[0].sourceFile,read.images?.[0].sourceCell,read.images?.[0].width,read.images?.[0].height],['maps.csv','B2',1,1]);
  assert.equal(read.notes?.length,2);
  assert.ok(read.notes?.every(note=>/not opened automatically/.test(note.text)));
  const imagesOnly=parseAddressText(`Name,Image\nNorth area,"${imageData}"`,'pictures.csv');
  assert.deepEqual(imagesOnly.issues,[]);
  assert.equal(imagesOnly.images?.length,1);
  assert.equal(imagesOnly.addresses.length,0);
  assert.equal(imagesOnly.streets?.length,0);
});

test('unsupported or damaged embedded image cells are explicit notes, never address issues',()=>{
  const read=parseAddressText('Image\n"data:image/svg+xml;base64,PHN2Zy8+"\n"data:image/png;base64,?invalid"\n"data:image/png;base64,YmFk"','pictures.csv');
  assert.equal(read.images?.length,0);
  assert.equal(read.notes?.length,3);
  assert.deepEqual(read.issues,[]);
  assert.ok(read.notes?.some(note=>/unsupported format/.test(note.text)));
  assert.ok(read.notes?.some(note=>/invalid base64/.test(note.text)));
  assert.ok(read.notes?.some(note=>/unreadable/.test(note.text)));
});

test('standalone supported raster containers return bounded dimensions before any preview decode',async()=>{
  const gif=Uint8Array.from([...Buffer.from('GIF89a'),2,0,3,0,0,0,0]);
  const bmp=new Uint8Array(54),bmpView=new DataView(bmp.buffer);bmp.set([66,77]);bmpView.setUint32(14,40,true);bmpView.setInt32(18,2,true);bmpView.setInt32(22,-3,true);
  const jpeg=Uint8Array.from([255,216,255,224,0,2,255,192,0,8,8,0,3,0,2,1]);
  const webp=new Uint8Array(30),webpView=new DataView(webp.buffer);webp.set(Buffer.from('RIFF'));webpView.setUint32(4,22,true);webp.set(Buffer.from('WEBPVP8X'),8);webpView.setUint32(16,10,true);webp[24]=1;webp[27]=2;
  for(const [name,bytes,type] of [['tiny.png',tinyPng,'image/png'],['small.gif',gif,'image/gif'],['small.bmp',bmp,'image/bmp'],['small.jpg',jpeg,'image/jpeg'],['small.webp',webp,'image/webp']] as const){
    const read=await readAddressFile(imageFile(name,bytes));
    assert.deepEqual(read.issues,[]);assert.equal(read.addresses.length,0);assert.equal(read.images?.[0].blob.type,type);
    assert.deepEqual([read.images?.[0].width,read.images?.[0].height],name==='tiny.png'?[1,1]:[2,3]);
  }
  await assert.rejects(readWalkImageFile(imageFile('broken.png',strToU8('not an image'))),/could not be read/);
  await assert.rejects(readAddressFile(imageFile('unsupported.svg',strToU8('<svg/>'))),/Use PNG, JPEG, WebP, GIF or BMP/);
  await assert.rejects(readWalkImageFile({...imageFile('large.png',tinyPng),size:40*1024*1024+1}),/40 MiB/);
});

test('oversized encoded image dimensions and aggregate image limits reject before decoding',async()=>{
  const wide=tinyPng.slice(),wideView=new DataView(wide.buffer);wideView.setUint32(16,12001);
  await assert.rejects(readAddressFile(imageFile('wide.png',wide)),/12,000 pixels/);
  const area=tinyPng.slice(),areaView=new DataView(area.buffer);areaView.setUint32(16,5000);areaView.setUint32(20,5000);
  await assert.rejects(readWalkImageFile(imageFile('large.png',area)),/20 million pixels/);
  assert.throws(()=>parseAddressText(`Image\n${Array.from({length:13},()=>`"${imageData}"`).join('\n')}`,'pictures.csv'),/at most 12/);
  const image=await readWalkImageFile(imageFile('tiny.png',tinyPng));
  assert.throws(()=>assertWalkImages([{...image,blob:{size:40*1024*1024+1} as Blob}]),/40 MiB/);
});

function imageWorkbook():Uint8Array{
  const files=unzipSync(workbook()),xml=(text:string)=>strToU8(text);
  const worksheet=new TextDecoder().decode(files['xl/worksheets/sheet1.xml']);
  files['xl/worksheets/sheet1.xml']=xml(worksheet.replace('</worksheet>','<drawing r:id="drawing1"/></worksheet>'));
  files['xl/worksheets/_rels/sheet1.xml.rels']=xml('<Relationships><Relationship Id="drawing1" Target="../drawings/drawing1.xml" Type="drawing"/></Relationships>');
  const marker=(kind:string,row:number,column:number)=>`<xdr:${kind}><xdr:col>${column}</xdr:col><xdr:row>${row}</xdr:row></xdr:${kind}>`;
  files['xl/drawings/drawing1.xml']=xml(`<xdr:wsDr><xdr:twoCellAnchor>${marker('from',3,1)}${marker('to',23,8)}<xdr:pic><a:blip r:embed="image1"/></xdr:pic></xdr:twoCellAnchor><xdr:oneCellAnchor>${marker('from',25,2)}<xdr:pic><a:blip r:embed="image1"/></xdr:pic></xdr:oneCellAnchor><xdr:absoluteAnchor><xdr:pic><a:blip r:link="external"/></xdr:pic></xdr:absoluteAnchor></xdr:wsDr>`);
  files['xl/drawings/_rels/drawing1.xml.rels']=xml('<Relationships><Relationship Id="image1" Target="../media/image1.png" Type="image"/><Relationship Id="external" Target="https://example.invalid/map.png" TargetMode="External" Type="image"/></Relationships>');
  files['xl/media/image1.png']=tinyPng;
  files['xl/media/unsupported.svg']=xml('<svg/>');
  return zipSync(files);
}

test('XLSX drawing extraction retains every image placement and one-based worksheet bounds',async()=>{
  const read=await readAddressFile({...imageFile('maps.xlsx',imageWorkbook()),text:async()=>''});
  assert.equal(read.addresses.length,2);assert.deepEqual(read.issues,[]);
  assert.equal(read.images?.length,2);
  assert.deepEqual(read.images?.map(image=>[image.sourceFile,image.sourceSheet,image.sourceCell,image.anchor]),[
    ['maps.xlsx','North','B4',{fromRow:4,toRow:24,fromColumn:2,toColumn:9}],
    ['maps.xlsx','North','C26',{fromRow:26,toRow:26,fromColumn:3,toColumn:3}],
  ]);
  assert.notEqual(read.images?.[0].id,read.images?.[1].id);
  assert.ok(read.notes?.some(note=>/externally linked/.test(note.text)));
  assert.ok(read.notes?.some(note=>/unsupported.svg.*not a supported raster/.test(note.text)));
});

test('XLSX image extraction keeps archive preflight and media count bounds',()=>{
  const files=unzipSync(workbook());
  for(let i=0;i<13;i++)files[`xl/media/image${i}.png`]=tinyPng;
  assert.throws(()=>extractWalkWorkbookImages(zipSync(files),'many.xlsx'),/at most 12/);
  const oversized=zipSync({'xl/media/large.png':tinyPng}),view=new DataView(oversized.buffer);
  for(let offset=0;offset<oversized.length-46;offset++)if(view.getUint32(offset,true)===0x02014b50){view.setUint32(offset+24,40*1024*1024+1,true);break;}
  assert.throws(()=>extractWalkWorkbookImages(oversized,'oversized.xlsx'),/40 MiB/);
  assert.throws(()=>extractWalkWorkbookImages(strToU8('not a ZIP'),'broken.xlsx'),/could not be read safely/);
  const orphan=extractWalkWorkbookImages(zipSync({...unzipSync(workbook()),'xl/media/orphan.png':tinyPng}),'orphan.xlsx');
  assert.equal(orphan.images.length,1);assert.equal(orphan.images[0].sourceSheet,undefined);
  assert.match(orphan.notes[0].text,/no readable worksheet position/);
});
