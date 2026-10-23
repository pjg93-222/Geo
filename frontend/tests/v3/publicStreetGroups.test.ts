import {test} from 'node:test';
import assert from 'node:assert/strict';
import type {PublicStreet} from '../../src/v3/api/publicStreets.ts';
import {unzipSync,strFromU8} from 'fflate';
import {boundsPolygon,groupPublicStreets,polygonBounds,publicExportFiles,publicGroupsCsv,validatePublicPolygon,PUBLIC_SCOPE_WARNING} from '../../src/v3/lib/publicStreetGroups.ts';
import type {PublicExportInfo} from '../../src/v3/lib/publicStreetGroups.ts';
import {isLocalOnlyDenial} from '../../src/v3/api/publicStreets.ts';
import {ApiError} from '../../src/v3/api/client.ts';
import {buildZip} from '../../src/v3/lib/walkZip.ts';
import {readWalkCsv} from '../../src/v3/lib/walk.ts';

function street(id:string,estimated_doors:number,x=0,y=0):PublicStreet{
  return {id,label:`Street ${id}`,estimated_doors,address_points:[[x,y,Number(id)||1]],geometry:{type:'MultiLineString',coordinates:[[[x,y],[x+.00001,y+.00001]]]}};
}
const coverage:[number,number,number,number]=[-123,45,-122,46];
test('keyboard and drawn areas close valid rings and reject outside, crossed, touching and zero-area polygons',()=>{
  const polygon=validatePublicPolygon([[-122.9,45.1],[-122.1,45.1],[-122.2,45.9]],coverage);
  assert.deepEqual(polygon[0],polygon.at(-1));assert.equal(polygon.length,4);
  assert.deepEqual(polygonBounds(boundsPolygon(coverage)),coverage);
  for(const points of [
    [[-124,45.1],[-122.5,45.1],[-122.5,45.9]],
    [[-122.9,45.1],[-122.1,45.9],[-122.9,45.9],[-122.1,45.1]],
    [[-122.9,45.1],[-122.5,45.1],[-122.1,45.1]],
    [[-122.9,45.1],[-122.1,45.1],[-122.9,45.1],[-122.5,45.9]],
    [[NaN,45.1],[-122.1,45.1],[-122.5,45.9]],
  ])assert.throws(()=>validatePublicPolygon(points as [number,number][],coverage));
});
test('inclusive limits retain every whole street exactly once, without mutating input',()=>{
  const rows=[street('1',50,-122.51,45.45),street('2',50,-122.5101,45.45),street('3',75,-122.52,45.46),street('4',75,-122.5201,45.46)];
  const before=JSON.stringify(rows),result=groupPublicStreets(rows,100,150);
  assert.deepEqual(result.groups.map(g=>g.estimated_doors).sort(),[100,150]);
  assert.equal(result.leftovers.length,0);assert.equal(result.reason,null);
  assert.deepEqual(result.groups.flatMap(g=>g.streets.map(s=>s.id)).sort(),['1','2','3','4']);
  assert.equal(JSON.stringify(rows),before);
});
test('compact grouping keeps distant neighborhoods apart when capacities permit',()=>{
  const rows=Array.from({length:12},(_,i)=>street(String(i+1),25,i<6?-122.9+i*.00001:-122.1+i*.00001,i<6?45.1:45.9));
  const result=groupPublicStreets(rows,100,150);
  assert.equal(result.leftovers.length,0);assert.equal(result.groups.length,2);
  assert.ok(result.groups.every(g=>g.streets.every(s=>Number(s.id)<=6)||g.streets.every(s=>Number(s.id)>6)));
});
test('oversized, zero estimates and balancing leftovers remain explicit and excluded from valid groups',()=>{
  const result=groupPublicStreets([street('1',151),street('2',0),street('3',70),street('4',70),street('5',70)],100,150);
  assert.deepEqual(result.oversized.map(s=>s.id),['1']);assert.deepEqual(result.zero.map(s=>s.id),['2']);
  assert.equal(result.groups.length,1);assert.equal(result.groups[0].estimated_doors,140);
  assert.equal(result.leftovers.length,1);assert.match(result.reason??'',/whole streets/);
  const ids=[...result.groups.flatMap(g=>g.streets),...result.leftovers,...result.zero,...result.oversized].map(s=>s.id);
  assert.equal(new Set(ids).size,5);assert.equal(ids.length,5);
  assert.match(groupPublicStreets([street('1',60)],100,150).reason??'',/totals cannot/);
});
test('limits and estimates reject invalid values rather than inventing counts',()=>{
  for(const [min,max] of [[0,150],[150,100],[100,1001],[1.5,100],[NaN,150]])assert.throws(()=>groupPublicStreets([],min,max));
  for(const n of [-1,1.2,Infinity,NaN])assert.throws(()=>groupPublicStreets([street('1',n)],100,150));
  assert.throws(()=>groupPublicStreets([street('1',50),street('1',50)],100,150));
});
const info:PublicExportInfo={catalogId:'cat-1',catalog:'Local catalog',sourceDate:'2026-01-02',basis:'Residential address-point estimate',polygon:[[-122.1234567891,45.1],[-122.2,45.1],[-122.2,45.2],[-122.1234567891,45.1]],min:100,max:150,generatedAt:'2026-10-22T00:00:00.000Z',sources:[{label:'County',url:'https://example.org/a'}]};
test('CSV preserves public scope/count provenance, quotes multiline names, and protects formula cells',()=>{
  const row={...street('1',100),label:'=HYPERLINK("x")\nMain Street'};
  const csv=publicGroupsCsv([{group:1,status:'grouped',street:row}],info);
  assert.ok(csv.startsWith('\uFEFF'));assert.ok(csv.includes('\r\n'));
  assert.ok(csv.startsWith('\uFEFF"group","street","street_id","estimated_doors","status","count_basis","catalog","catalog_id","source_date","min_doors","max_doors","generated_at","scope"\r\n'));
  assert.match(csv,/'=HYPERLINK\(""x""\)/);assert.ok(!csv.includes(PUBLIC_SCOPE_WARNING)&&!csv.includes('-122.2'));assert.ok(csv.includes('Public estimate for the drawn area only; see README.txt'));
  assert.ok(csv.includes('"cat-1","2026-01-02","100","150","2026-10-22T00:00:00.000Z","Public'));
  assert.ok(csv.includes('Residential address-point estimate'));
});
test('export bundle has one CSV per group, not-grouped rows with statuses, and a README',()=>{
  const rows=[street('1',80,-122.51,45.45),street('2',80,-122.5101,45.45),street('3',70,-122.9,45.9),street('4',70,-122.9001,45.9),street('5',151),street('6',0),street('7',10)];
  const grouping=groupPublicStreets(rows,100,150);
  const files=unzipSync(buildZip(publicExportFiles(grouping,info)));
  assert.deepEqual(Object.keys(files),['group-01.csv','group-02.csv','not-grouped.csv','README.txt']);
  assert.match(strFromU8(files['group-01.csv']),/"1","Street \d","\d","\d+","grouped"/);
  const rest=strFromU8(files['not-grouped.csv']);
  assert.match(rest,/"","Street 7","7","10","unassigned"/);assert.match(rest,/"","Street 5","5","151","over_max"/);assert.match(rest,/"","Street 6","6","0","zero"/);
  const readme=strFromU8(files['README.txt']);
  for(const text of [PUBLIC_SCOPE_WARNING,'[[-122.123457,45.1],[-122.2,45.1],[-122.2,45.2],[-122.123457,45.1]]','cat-1','2026-01-02','100 to 150','https://example.org/a','whole streets','voter-based'])assert.ok(readme.includes(text),text);
  assert.ok(!Object.keys(unzipSync(buildZip(publicExportFiles(groupPublicStreets([street('1',100)],100,150),info)))).includes('not-grouped.csv'));
});
test('regrouping leftovers pairs streets a single sweep strands',()=>{
  // 80s together and 70s together: one sweep pairs only one 80 with one 70; the stranded 80 and 70 form the second group.
  for(const rows of [
    [street('1',80,-122.5,45.45),street('2',80,-122.5001,45.45),street('3',70,-122.4,45.45),street('4',70,-122.4001,45.45)],
    [street('1',80,-122.5,45.45),street('2',80,-122.5001,45.45),street('3',70,-122.5002,45.45),street('4',70,-122.5003,45.45)],
  ]){
    const result=groupPublicStreets(rows,100,150);
    assert.equal(result.groups.length,2);assert.equal(result.leftovers.length,0);assert.equal(result.reason,null);
    assert.deepEqual(result.groups.flatMap(g=>g.streets.map(s=>s.id)).sort(),['1','2','3','4']);
    assert.deepEqual(groupPublicStreets(rows,100,150),result);
  }
});
test('walk CSV import accepts one public group with a scope warning and strips the formula guard',()=>{
  const row={...street('1',100),label:'-Lost Rd'};
  const one=publicGroupsCsv([{group:1,status:'grouped',street:row},{group:1,status:'grouped',street:street('2',50)}],info);
  const read=readWalkCsv(one);
  assert.deepEqual(read.rows.map(r=>r.street),['-Lost Rd','Street 2']);assert.equal(read.warning,PUBLIC_SCOPE_WARNING);
  assert.equal(readWalkCsv('street\n\'-Plain\n').rows[0].street,"'-Plain");   // ordinary files keep their text
  assert.equal(readWalkCsv('street\nMain St\n').warning,undefined);
});
test('walk CSV import rejects public files with several groups or ungrouped streets',()=>{
  const merged=publicGroupsCsv([{group:1,status:'grouped',street:street('1',100)},{group:2,status:'grouped',street:street('2',100)}],info);
  assert.throws(()=>readWalkCsv(merged),/several groups\. Import one group file at a time/);
  const rest=publicGroupsCsv([{group:'',status:'unassigned',street:street('3',10)}],info);
  assert.throws(()=>readWalkCsv(rest),/not grouped can't be imported/);
  const mixed=publicGroupsCsv([{group:1,status:'grouped',street:street('1',100)},{group:'',status:'over_max',street:street('2',200)}],info);
  assert.throws(()=>readWalkCsv(mixed),/not grouped/);
});
test('only a 403 hides the panel',()=>{
  assert.equal(isLocalOnlyDenial(new ApiError('no',403)),true);
  for(const e of [new ApiError('down',503),new Error('x'),null])assert.equal(isLocalOnlyDenial(e),false);
});
test('bounded grouping handles thousands of streets, conserving every estimate',()=>{
  const rows=Array.from({length:1200},(_,i)=>street(String(i+1),1,-122.5+(i%40)*.0001,45.4+Math.floor(i/40)*.0001));
  const result=groupPublicStreets(rows,100,150);
  assert.equal(result.leftovers.length,0);assert.ok(result.groups.every(g=>g.estimated_doors>=100&&g.estimated_doors<=150));
  assert.equal(result.groups.reduce((s,g)=>s+g.estimated_doors,0),1200);
  assert.equal(new Set(result.groups.flatMap(g=>g.streets.map(s=>s.id))).size,1200);
});
test('a full-width formula label is guarded on export and unguarded on import; a plain apostrophe street keeps it',()=>{
  const label='＝HYPERLINK("x")';
  const csv=publicGroupsCsv([{group:1,status:'grouped',street:{...street('1',100),label}},{group:1,status:'grouped',street:{...street('2',50),label:"'abc"}}],info);
  assert.ok(csv.includes(`"'${label.replaceAll('"','""')}"`));
  assert.deepEqual(readWalkCsv(csv).rows.map(r=>r.street),[label,"'abc"]);
});
test('an ordinary CSV with status and catalog id columns is not read as a public export',()=>{
  const read=readWalkCsv('street,status,catalog id\nMain St,done,abc\n');
  assert.deepEqual(read.rows.map(r=>r.street),['Main St']);assert.equal(read.warning,undefined);
});
test('group file names pad to the group count and sort in order past 99 groups',()=>{
  const grouping=groupPublicStreets(Array.from({length:120},(_,i)=>street(String(i+1),1,-122.5+i*.001,45.4)),1,1);
  assert.equal(grouping.groups.length,120);
  const names=publicExportFiles(grouping,info).map(f=>f.name).filter(n=>n.startsWith('group-'));
  assert.equal(names[0],'group-001.csv');assert.equal(names.at(-1),'group-120.csv');assert.deepEqual([...names].sort(),names);
  assert.equal(publicExportFiles(groupPublicStreets([street('1',100)],100,150),info)[0].name,'group-01.csv');
});
test('a 199-corner polygon on 500 streets stays small per group file because the polygon lives only in the README',()=>{
  const polygon:[number,number][]=Array.from({length:199},(_,i)=>[-122.5+Math.cos(i/199*2*Math.PI)*.123456789,45.5+Math.sin(i/199*2*Math.PI)*.123456789]);
  const big={...info,polygon:[...polygon,polygon[0]]};
  const csv=publicGroupsCsv(Array.from({length:500},(_,i)=>({group:1,status:'grouped' as const,street:street(String(i+1),1)})),big);
  assert.ok(csv.length<200_000,String(csv.length));
});
test('groups formed from leftovers are flagged regrouped; first-pass groups are not',()=>{
  const result=groupPublicStreets([street('1',80,-122.5,45.45),street('2',80,-122.5001,45.45),street('3',70,-122.4,45.45),street('4',70,-122.4001,45.45)],100,150);
  assert.deepEqual(result.groups.map(g=>!!g.regrouped).sort(),[false,true]);
});
