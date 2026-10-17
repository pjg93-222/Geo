import {test} from 'node:test';
import assert from 'node:assert/strict';
import type {WalkListPayload,WalkRun,WalkVoter} from '../../src/v3/api/walkTypes.ts';
import {buildWalkSheetsCsv,doorCap,splitWalkSheets} from '../../src/v3/lib/walkSheets.ts';
import {addressSheetsCsv,splitAddressSheets} from '../../src/v3/lib/walkAddressSheets.ts';
import {parseAddressText} from '../../src/v3/lib/walkAddressImport.ts';
import {walkExportNames,walkSummaryCsv} from '../../src/v3/lib/walkExportNames.ts';
import {buildWalkCsv} from '../../src/v3/lib/walk.ts';

const voter=(id:string):WalkVoter=>({person_id:id,name:`Resident ${id}`,phone:'',pri:{voted:0,eligible:0},gen:{voted:0,eligible:0},abs:{voted:0,eligible:0},party:'',age:null,gender:'',reg_date:'',last:null});
const run=(stop:number,n:number):WalkRun=>({stop,street_id:stop,street:`Street ${stop}`,side:'even',precinct:'',from_house:'2',to_house:String(n*2),walk_m:100,lr:1,path:[],approach:[],doors:Array.from({length:n},(_,i)=>({seq:stop*100+i,house:String((i+1)*2),unit:i===0?'A':'',placed_by:'geocode',lon:-104,lat:39,voters:[voter(`${stop}-${i}-a`),voter(`${stop}-${i}-b`)]}))});
const runs=[run(1,4),run(2,3)];
const payload:WalkListPayload={schema:'walk-list-1',generated_on:'2026-10-16',list:{key:null,title:'Test',kind:'standard',created_at:null},query:{},window:{primaries:[],generals:[],abs_rule:''},bbox:[-105,38,-104,39],totals:{streets:2,stops:2,doors:7,voters:14,walk_m:200},runs};

test('door caps reject fractions, zero, negative and oversized input; blank is unlimited',()=>{
  assert.equal(doorCap(''),null);assert.equal(doorCap('  '),null);assert.equal(doorCap('1'),1);assert.equal(doorCap('10000'),10000);
  for(const text of ['0','-1','1.5','1e2','10001','x'])assert.throws(()=>doorCap(text));
});

test('caps partition every complete door exactly once and preserve all voters and units',()=>{
  for(const cap of [1,2,3,4,6,7,20]){
    const sheets=splitWalkSheets(payload,cap);
    assert.equal(sheets.length,Math.ceil(7/cap));
    assert.ok(sheets.every(s=>s.totals.doors<=cap));
    assert.deepEqual(sheets.flatMap(s=>s.runs.flatMap(r=>r.doors)),payload.runs.flatMap(r=>r.doors));
    assert.equal(sheets.reduce((n,s)=>n+s.totals.voters,0),14);
  }
  assert.strictEqual(splitWalkSheets(payload,null)[0],payload);
  assert.equal(payload.runs.length,2);assert.equal(payload.runs[0].doors.length,4);
});

test('all-sheet CSV contains every voter and sheet number, with only one header',()=>{
  const csv=buildWalkSheetsCsv(splitWalkSheets(payload,3));
  const lines=csv.replace(/^\uFEFF/,'').trimEnd().split('\r\n');
  assert.equal(lines.length,15);assert.match(lines[0],/^"List","Stop"/);
  assert.equal(lines.filter(l=>l.startsWith('"1",')).length,6);
  assert.equal(lines.filter(l=>l.startsWith('"2",')).length,6);
  assert.equal(lines.filter(l=>l.startsWith('"3",')).length,2);
  assert.match(csv,/Resident 2-2-b/);
});

test('split toggle keeps parity on separate lists even without a door cap',()=>{
  const mixed={...payload,runs:[runs[0],{...runs[1],side:'odd' as const}]};
  const sheets=splitWalkSheets(mixed,null);
  assert.equal(sheets.length,2);
  assert.ok(sheets.every(s=>new Set(s.runs.map(r=>r.side)).size===1));
  assert.equal(splitWalkSheets({...mixed,query:{split_sides:'0'}},null).length,1);
  assert.deepEqual(splitWalkSheets(mixed,3).map(s=>s.totals.doors),[3,1,3]);
});

test('address sheets preserve duplicates and apartments, cap every list, toggle parity grouping',()=>{
  const parsed=parseAddressText('address,city,zip\n101 Oak St,Sample,01234\n104 Oak St,Sample,01234\n102 Oak St Apt 2,Sample,01234\n103 Oak St,Sample,01234\n101 Oak St,Sample,01234','sample.csv');
  assert.equal(parsed.issues.length,0);
  const evenOdd=splitAddressSheets(parsed.addresses,2,true),mixed=splitAddressSheets(parsed.addresses,2,false);
  assert.deepEqual(evenOdd.map(s=>s.length),[2,2,1]);
  assert.deepEqual(evenOdd.flat().map(a=>a.house),['102','104','101','101','103']);
  assert.deepEqual(mixed.flat().map(a=>a.house),['101','101','102','103','104']);
  assert.equal(mixed.flat().length,parsed.addresses.length);
  assert.ok(mixed.flat().every(a=>a.side==='both'));
  assert.deepEqual(splitAddressSheets(parsed.addresses,null,true).map(s=>s.length),[2,3]);
  assert.ok(evenOdd.every(s=>new Set(s.map(a=>a.side)).size===1));
  const csv=addressSheetsCsv(evenOdd);
  assert.equal(csv.trimEnd().split('\r\n').length,6);
  assert.match(csv,/01234/);assert.match(csv,/Apt 2/i);assert.match(csv,/"3","1"/);
});

test('sample bundle names keep the same letter across parity and distinguish capped parts',()=>{
  const sample={...payload,query:{precinct:'5101521412'},runs:[runs[0],{...runs[1],side:'odd' as const}]};
  const sheets=splitWalkSheets(sample,3),names=walkExportNames(sheets);
  assert.deepEqual(names.map(x=>x.stem),[
    'Even/A - Precinct 5101521412 - Even - Part 1',
    'Even/A - Precinct 5101521412 - Even - Part 2',
    'Odd/A - Precinct 5101521412 - Odd',
  ]);
  const summary=walkSummaryCsv(sheets);
  assert.equal(summary.trimEnd().split('\r\n').length,4);
  assert.match(summary,/"List","Side","Precinct","Streets","Doors","Voters","File","Street names"/);
  assert.match(summary,/Odd\/A - Precinct 5101521412 - Odd.pdf/);
});

test('combined parity retains one canvassing list even when street segments cross precincts',()=>{
  const mixed={...payload,query:{split_sides:'0'},runs:[{...runs[0],precinct:'101'},{...runs[1],side:'both' as const,precinct:'102'}]};
  const sheets=splitWalkSheets(mixed,null);
  assert.equal(sheets.length,1);
  assert.deepEqual(walkExportNames(sheets),[{list:'A',side:'Both',precinct:'',stem:'Both/A - Multiple precincts - Both'}]);
  assert.equal(sheets[0].totals.doors,7);
});

test('a door with no voter data has one blank CSV row and no fabricated person details',()=>{
  const missing={...runs[0].doors[0],voters:[]};
  const csv=buildWalkSheetsCsv([{...payload,runs:[{...runs[0],doors:[missing]}]}]);
  const lines=csv.trimEnd().split('\r\n');
  assert.equal(lines.length,2);
  assert.equal(lines[1].split(',').length,16);
  assert.match(lines[1],/^"1","","Street 1","Even","2","A","",""/);
  assert.doesNotMatch(lines[1],/0 of 0|Resident|unknown/i);
});

test('parity and cap partitions never keep a connector from an omitted preceding stop',()=>{
  const connector:[number,number][]=[[-104,39],[-104.01,39.01]];
  const pieces=[run(1,2),{...run(2,2),side:'odd' as const,approach:connector},{...run(3,2),approach:connector}];
  const sheets=splitWalkSheets({...payload,runs:pieces,totals:{...payload.totals,doors:6}},null);
  assert.equal(sheets[0].runs.length,2);
  assert.deepEqual(sheets[0].runs[1].approach,[]);
  assert.deepEqual(sheets[1].runs[0].approach,[]);
  const combined=splitWalkSheets({...payload,query:{split_sides:'0'},runs:pieces,totals:{...payload.totals,doors:6}},3);
  assert.deepEqual(combined[1].runs[0].approach,[]);
  assert.deepEqual(combined[1].runs[1].approach,connector);
});

test('imported recorded-only CSVs retain locality in every parity and capped part',()=>{
  const p={...payload,query:{imported:'1',split_sides:'1'},runs:payload.runs.map(r=>({...r,doors:r.doors.map(d=>({...d,city:'Southington',state:'CT',zip:'06489'}))}))};
  const sheets=splitWalkSheets(p,3);
  for(const sheet of sheets){
    const rows=buildWalkCsv(sheet).trimEnd().split('\r\n');
    assert.ok(rows.every(row=>row.split(',').length===19));
    assert.ok(rows.slice(1).every(row=>row.endsWith('"Southington","CT","06489"')));
    assert.equal(sheet.list.title,'Walk list');
  }
  assert.equal(buildWalkSheetsCsv(sheets).trimEnd().split('\r\n').length,15);
});

test('local imported groups have stable letters across even and odd and capped parts',()=>{
  const local=(group:string,label:string,side:'even'|'odd')=>({...payload,query:{imported:'1',import_group:group,import_group_label:label,split_sides:'1'},runs:[{...runs[0],side}]});
  const names=walkExportNames([local('one','Southington Section 1','even'),local('one','Southington Section 1','even'),local('one','Southington Section 1','odd'),local('two','Southington Section 2','even'),local('two','Southington Section 2','odd')]);
  assert.deepEqual(names.map(item=>item.stem),[
    'Even/A - Southington Section 1 - Even - Part 1','Even/A - Southington Section 1 - Even - Part 2',
    'Odd/A - Southington Section 1 - Odd','Even/B - Southington Section 2 - Even','Odd/B - Southington Section 2 - Odd',
  ]);
});
