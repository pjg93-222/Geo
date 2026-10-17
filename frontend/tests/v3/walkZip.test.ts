import {test} from 'node:test';
import assert from 'node:assert/strict';
import {unzipSync,strFromU8} from 'fflate';
import {buildZip,listFileStem} from '../../src/v3/lib/walkZip.ts';
import {addressSheetsCsv,splitAddressSheets} from '../../src/v3/lib/walkAddressSheets.ts';
import {parseAddressText} from '../../src/v3/lib/walkAddressImport.ts';

test('ZIP roundtrip retains every named file including unicode text and PDF bytes',()=>{
  const pdf=new Uint8Array([37,80,68,70,45,49,46,55,0,255]);
  const bytes=buildZip([{name:'lists/list-001.pdf',data:pdf},{name:'all-lists.csv',data:'\uFEFFAddress\r\n10 Rue École\r\n'},{name:'manifest.json',data:'{"lists":1}'}]);
  const files=unzipSync(bytes);
  assert.deepEqual(Object.keys(files),['lists/list-001.pdf','all-lists.csv','manifest.json']);
  assert.deepEqual(files['lists/list-001.pdf'],pdf);
  assert.match(strFromU8(files['all-lists.csv']),/10 Rue École/);
});

test('ZIP rejects traversal, absolute paths, duplicate files and oversized input',()=>{
  for(const name of ['../outside','/absolute','lists/../../outside','C:/outside','lists\\outside','lists//x','lists/./x','file\u0000'])assert.throws(()=>buildZip([{name,data:'x'}]),/Invalid ZIP/);
  assert.throws(()=>buildZip([{name:'same.txt',data:'a'},{name:'same.txt',data:'b'}]),/Duplicate/);
  assert.throws(()=>buildZip([{name:'large.bin',data:new Uint8Array(256*1024*1024+1)}]),/256 MB/);
  assert.throws(()=>buildZip(Array.from({length:65001},(_,i)=>({name:`file-${i}.txt`,data:''}))),/65,000 files/);
});

test('individual address CSVs retain global list numbering and full coverage',()=>{
  const rows=parseAddressText('2 Oak St\n4 Oak St\n6 Oak St\n8 Oak St','addresses.txt').addresses;
  const sheets=splitAddressSheets(rows,2,false);
  const files=unzipSync(buildZip(sheets.map((sheet,i)=>({name:`lists/${listFileStem(i)}.csv`,data:addressSheetsCsv([sheet],i)}))));
  assert.equal(Object.keys(files).length,2);
  assert.match(strFromU8(files['lists/list-002.csv']),/"2","1","6 Oak St"/);
  assert.equal(Object.values(files).flatMap(bytes=>strFromU8(bytes).trimEnd().split('\r\n').slice(1)).length,4);
});
