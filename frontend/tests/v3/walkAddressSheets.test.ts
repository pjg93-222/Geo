import {test} from 'node:test';
import assert from 'node:assert/strict';
import {addressReviewEntries,applyEvidenceDecisions,buildImportedWalkList,evidenceReviewRows} from '../../src/v3/lib/walkAddressSheets.ts';
import {buildWalkSheetsCsv,splitWalkSheets} from '../../src/v3/lib/walkSheets.ts';
import type {ImportedWalkSelection} from '../../src/v3/lib/walkAddressSheets.ts';
import type {WalkEvidence,WalkListPayload,WalkVoter} from '../../src/v3/api/walkTypes.ts';
import {importSections,prepareImportedGroups} from '../../src/v3/lib/walkImportGroups.ts';
import type {ImportedWalkImage} from '../../src/v3/lib/walkImageImport.ts';
const voter=(person_id:string):WalkVoter=>({person_id,name:`Resident ${person_id}`,phone:'5550100',pri:{voted:2,eligible:3},gen:{voted:4,eligible:5},abs:{voted:1,eligible:5},party:'',age:null,gender:'',reg_date:'',last:null});
const payload:WalkListPayload={schema:'walk-list-1',generated_on:'2026-10-16',list:{key:null,title:'Import',kind:'standard',created_at:null},query:{split_sides:'1'},window:{primaries:['2024'],generals:['2024'],abs_rule:'Recorded history'},bbox:[-105,38,-104,39],context:{bbox:[-105,38,-104,39],roads:[{k:'S1400',n:'Oak St',c:[[-105,38],[-104,39]]}],precincts:[],homes:[[-104.5,38.5]]},totals:{streets:1,stops:2,doors:2,voters:3,walk_m:100},runs:[
{stop:1,street_id:10,street:'Oak St',side:'even',precinct:'P1',from_house:'2',to_house:'2',walk_m:50,lr:1,path:[[-105,38],[-104,39]],approach:[],doors:[{seq:1,house:'2',unit:'A',lon:-104.5,lat:38.5,placed_by:'geocode',voters:[voter('a'),voter('b')]}]},
{stop:2,street_id:10,street:'Oak St',side:'odd',precinct:'P2',from_house:'3',to_house:'3',walk_m:50,lr:-1,path:[[-105,38],[-104,39]],approach:[],doors:[{seq:2,house:'3',unit:'',lon:null,lat:null,placed_by:'unlocated',voters:[voter('c')]}]}
]};
const selection=(personIds:string[]=['a','b','c'],sourceRow=4):ImportedWalkSelection=>({source:{sourceRow,sourceFile:'input.xlsx',sourceSheet:'Sheet2',sourceCell:`K${sourceRow}`,sourceText:'Oak St'},payload,personIds,streetId:10,city:'Example',state:'CO',zip:''});
const exact={sourceRow:1,sourceFile:'input.xlsx',sourceCell:'A1',address:'10 Pine Rd, Example, CO 80000',house:'10',street:'Pine Rd',unit:'',city:'Example',state:'CO',zip:'80000'};

test('shared import payload preserves selected people, apartments, precincts, history and geometry',()=>{
 const output=buildImportedWalkList([], [selection()],{sourceName:'input.xlsx',splitSides:true});
 assert.equal(output.payload.totals.voters,3);assert.equal(output.payload.totals.doors,2);
 assert.deepEqual(output.payload.runs.map(run=>run.precinct),['P1','P2']);
 assert.deepEqual(output.payload.runs[0].doors[0].voters,payload.runs[0].doors[0].voters);
 assert.equal(output.payload.runs[0].doors[0].unit,'A');assert.deepEqual(output.payload.context,payload.context);
 assert.equal(output.addresses[0].sourceCell,'K4');assert.equal(output.addresses[0].zip,'','street summary ZIP is not used as a door ZIP');
 assert.notStrictEqual(output.payload.runs[0].doors[0].voters[0],payload.runs[0].doors[0].voters[0]);
});
test('exact supplied rows and repeated street requests remain complete under caps and parity toggle',()=>{
 for(const splitSides of [true,false]){
  const output=buildImportedWalkList([exact,{...exact}], [selection(),selection(['a','b','c'],8)],{sourceName:'input.xlsx',splitSides});
  const sheets=splitWalkSheets(output.payload,1);
  assert.equal(sheets.length,6);assert.equal(sheets.reduce((n,s)=>n+s.totals.voters,0),6);
  assert.deepEqual(output.payload.runs.flatMap(run=>run.doors.map(door=>door.seq)),[1,2,3,4,5,6]);
  assert.equal(output.payload.runs[0].doors[0].voters.length,0);
  assert.equal(output.payload.runs[0].doors[0].lon,null);assert.equal(output.payload.runs[0].doors[0].placed_by,'unlocated');
  assert.equal(output.addresses.filter(row=>row.sourceCell==='K4').length,2);assert.equal(output.addresses.filter(row=>row.sourceCell==='K8').length,2);
  if(!splitSides)assert.ok(output.payload.runs.every(run=>run.side==='both'));
 }
});
test('selection filters preserve only explicit ZIP membership and reject missing or duplicate IDs',()=>{
 const selected=buildImportedWalkList([], [selection(['b'])],{sourceName:'input.xlsx',splitSides:true});
 assert.equal(selected.payload.totals.voters,1);assert.equal(selected.payload.runs[0].doors[0].voters[0].person_id,'b');
 for(const ids of [[],['a','a'],['missing']])assert.throws(()=>buildImportedWalkList([], [selection(ids)],{sourceName:'input.xlsx',splitSides:true}));
 assert.throws(()=>buildImportedWalkList([], [{...selection(),streetId:99}],{sourceName:'input.xlsx',splitSides:true}),/different street/);
 const doubled={...payload,runs:[...payload.runs,payload.runs[0]]};
 assert.throws(()=>buildImportedWalkList([], [{...selection(),payload:doubled}],{sourceName:'input.xlsx',splitSides:true}),/repeated person/);
});
test('unlocated exact-only imports never invent map bounds or person records',()=>{
 const output=buildImportedWalkList([exact],[],{sourceName:'input.csv',splitSides:false,date:'2026-10-16'});
 assert.deepEqual(output.payload.bbox,[0,0,0,0]);assert.equal(output.payload.context,undefined);
 assert.equal(output.payload.totals.voters,0);assert.equal(output.payload.runs[0].precinct,'');
 assert.equal(output.addresses[0].address,exact.address);assert.deepEqual(output.payload.window,{primaries:[],generals:[],abs_rule:''});
});

test('calculated route order survives reverse source order, with only valid predecessor connectors',()=>{
 const first=payload.runs[0],second={...payload.runs[1],street_id:20,street:'Pine Rd',approach_m:13,approach:[[-104.5,38.5],[-104.4,38.6]] as [number,number][]};
 const routed={...payload,runs:[first,second]};
 const later={...selection(['c'],8),streetId:20,payload:routed};
 const earlier={...selection(['a','b']),payload:routed};
 const output=buildImportedWalkList([], [later,earlier],{sourceName:'route.xlsx',splitSides:false});
 assert.deepEqual(output.payload.runs.map(run=>run.street),['Oak St','Pine Rd']);
 assert.deepEqual(output.payload.runs[1].approach,second.approach);assert.equal(output.payload.totals.walk_m,113);
 const repeated=buildImportedWalkList([], [earlier,earlier,later],{sourceName:'route.xlsx',splitSides:false});
 assert.deepEqual(repeated.payload.runs.map(run=>run.street),['Oak St','Oak St','Pine Rd']);
 assert.deepEqual(repeated.payload.runs[1].approach,[]);
 const alone=buildImportedWalkList([], [later],{sourceName:'route.xlsx',splitSides:false});assert.deepEqual(alone.payload.runs[0].approach,[]);assert.equal(alone.payload.runs[0].approach_m,0);assert.equal(alone.payload.totals.walk_m,50);
});
test('door locality and warnings retain recorded ZIP conflicts, and exact-only locality identifies the town',()=>{
 const warned={...payload,runs:[{...payload.runs[0],doors:[{...payload.runs[0].doors[0],city:'Actual Town',state:'CO',zip:'',address:'2 Oak St Apt A, Actual Town, CO',warnings:['Multiple ZIP codes recorded for this door; left blank.']}]}]};
 const output=buildImportedWalkList([exact],[{...selection(['a','b']),payload:warned,zip:'80000'}],{sourceName:'input.xlsx',splitSides:true});
 assert.equal(output.payload.runs[0].locality,'Example CO 80000');
 assert.equal(output.addresses[1].city,'Actual Town');assert.equal(output.addresses[1].zip,'');
 assert.equal(output.warnings.length,1);assert.match(output.addresses[1].note!,/Multiple ZIP/);
});

test('parcel precision and correction evidence survive local groups, parity and door caps without changing records',()=>{
 const original=payload.runs[0].doors[0];
 const evidence={original:{lon:original.lon,lat:original.lat,placed_by:original.placed_by},location:{lon:-104.6,lat:38.6,kind:'municipal_parcel'},source:{url:'https://example.test/municipal-parcels',sha256:'evidence-hash'}};
 const corrected:WalkListPayload={...payload,runs:[{...payload.runs[0],doors:[{...original,lon:-104.6,lat:38.6,placed_by:'parcel',location_evidence:evidence,warnings:['Municipal parcel location; entrance not confirmed.']}]}]};
 for(const splitSides of [true,false]){
  const selected={...selection(['a','b']),payload:corrected};
  const prepared=prepareImportedGroups([{key:'section',label:'Local',sources:[selected.source],exact:[]}],[{key:'local',sectionKey:'section',label:'Local',reason:'Nearby',selections:[selected]}],{sourceName:'input.xlsx',splitSides});
  const sheets=prepared.payloads.flatMap(sheet=>splitWalkSheets(sheet,1));
  const door=sheets[0].runs[0].doors[0];
  assert.equal(door.placed_by,'parcel');assert.deepEqual(door.location_evidence,evidence);
  assert.deepEqual(door.voters,original.voters);assert.equal(door.house,original.house);assert.equal(door.unit,original.unit);
  assert.match(prepared.addresses[0].note!,/entrance not confirmed/);assert.equal(sheets[0].totals.voters,2);
 }
});

test('an audited recovered house number follows numeric parity while its original blank value remains in evidence',()=>{
 const original={...payload.runs[1].doors[0],house:''};
 const audit={registration_recovery:{original:{house:''},recovered:{house:'15'},method:'concordant_source_addresses'},original:{lon:original.lon,lat:original.lat,placed_by:original.placed_by}};
 const corrected:WalkListPayload={...payload,runs:[{...payload.runs[1],side:'both',doors:[{...original,house:'15',placed_by:'parcel',lon:-104.6,lat:38.6,location_evidence:audit,warnings:['House number recovered from matching source address fields and municipal parcel evidence.']}]}]};
 const output=buildImportedWalkList([],[{...selection(['c']),payload:corrected}],{sourceName:'input.xlsx',splitSides:true});
 const sheets=splitWalkSheets(output.payload,1);const run=sheets[0].runs[0];
 assert.equal(run.side,'odd');assert.equal(run.doors[0].house,'15');assert.deepEqual(run.doors[0].location_evidence,audit);
 assert.equal(original.house,'');assert.deepEqual(run.doors[0].voters,original.voters);assert.match(output.addresses[0].note!,/House number recovered/);
});

test('source sections follow image row bands or file gaps without hardcoded workbook positions',()=>{
 const source=(row:number,column=3)=>({sourceFile:'varied.xlsx',sourceSheet:'Routes',sourceRow:row,sourceColumn:column});
 const rows=[source(2),source(4),source(12),source(13),source(30,7)];
 const image={id:'first-map',name:'map',sourceFile:'varied.xlsx',sourceSheet:'Routes',anchor:{fromRow:1,toRow:8,fromColumn:1,toColumn:2}} as ImportedWalkImage;
 const sections=importSections(rows,[{...exact,sourceFile:'varied.xlsx',sourceSheet:'Routes',sourceRow:1}],[image]);
 assert.deepEqual(sections.map(section=>section.sources.map(row=>row.sourceRow)),[[2,4],[12,13],[30]]);
 assert.equal(sections[0].exact.length,1);
 assert.equal(importSections([{...rows[0],sourceImageId:'first-map'},{...rows[1],sourceImageId:'first-map',sourceRow:80}],[],[image]).length,1);
});

test('an OCR exact address follows its image section instead of its OCR line number',()=>{
 const source=(sourceRow:number)=>({sourceFile:'maps.xlsx',sourceSheet:'Routes',sourceRow,sourceColumn:9});
 const image=(id:string,fromRow:number,toRow:number)=>({id,sourceFile:'maps.xlsx',sourceSheet:'Routes',anchor:{fromRow,toRow,fromColumn:1,toColumn:5}} as ImportedWalkImage);
 const images=[image('first',2,20),image('later',40,60)];
 const row={...exact,...source(1),sourceImageId:'later'};
 const sections=importSections([source(4),source(47)],[row],images);
 assert.equal(sections[0].exact.length,0);assert.equal(sections[1].exact[0].sourceImageId,'later');
 const unmatched=importSections([source(4)],[{...row,sourceImageId:'separate'}],images);
 assert.equal(unmatched[0].exact.length,0);assert.equal(unmatched[1].sources.length,0);assert.equal(unmatched[1].exact.length,1);
});

test('local group adaptation never merges map bounds and retains duplicated source occurrences',()=>{
 const first={...payload,bbox:[-105,38,-104.8,38.2] as [number,number,number,number],runs:[payload.runs[0]],totals:{...payload.totals,doors:1,voters:2,stops:1}};
 const second={...payload,bbox:[-104,39,-103.8,39.2] as [number,number,number,number],runs:[payload.runs[1]],totals:{...payload.totals,doors:1,voters:1,stops:1}};
 const selected={...selection(['a','b']),payload:first};
 const sourceSection={key:'section-1',label:'First section',sources:[selected.source],exact:[exact]};
 const prepared=prepareImportedGroups([sourceSection],[
  {key:'north',sectionKey:'section-1',label:'North',reason:'Local geometry',selections:[selected,selected]},
  {key:'south',sectionKey:'section-1',label:'South',reason:'Separate geometry',selections:[{...selection(['c']),payload:second}]},
 ],{sourceName:'varied.xlsx',splitSides:false});
 assert.equal(prepared.payloads.length,2);assert.equal(prepared.addresses.length,4);assert.equal(prepared.voters,5);
 assert.deepEqual(prepared.payloads.map(p=>p.bbox),[first.bbox,second.bbox]);
 assert.deepEqual(prepared.payloads.flatMap(p=>p.runs.flatMap(r=>r.doors.map(d=>d.seq))),[1,2,3,4]);
 assert.deepEqual(prepared.payloads.map(p=>p.query.import_group),['north','south']);
 assert.ok(prepared.payloads.every(p=>p.list.title==='Walk list'&&p.query.imported==='1'));
 const capped=prepared.payloads.flatMap(p=>splitWalkSheets(p,1));
 assert.equal(capped.length,4);assert.equal(capped.reduce((sum,p)=>sum+p.totals.voters,0),5);
});

test('exact-only locality stays printable in a separate group without assigning a map point',()=>{
 const section={key:'section-1',label:'Source rows',sources:[selection().source],exact:[{...exact,city:'Another town'}]};
 const prepared=prepareImportedGroups([section],[{key:'local',sectionKey:'section-1',label:'Local',reason:'Nearby',selections:[selection()]}],{sourceName:'addresses.csv',splitSides:true});
 assert.equal(prepared.payloads.length,2);
 const supplied=prepared.payloads[1];assert.equal(supplied.totals.voters,0);assert.deepEqual(supplied.bbox,[0,0,0,0]);
 assert.equal(supplied.runs[0].doors[0].city,'Another town');assert.equal(supplied.runs[0].doors[0].lon,null);
});

test('primary source sections precede additional geographic groups with stable row provenance',()=>{
 const selected=selection(),later={...selection(['c'],30)};
 const sections=[{key:'first',label:'First',sources:[selected.source],exact:[]},{key:'next',label:'Next',sources:[later.source],exact:[]}];
 const groups=[
  {key:'main-first',sectionKey:'first',label:'Main first',reason:'Nearby',selections:[{...selected,personIds:['a']}]},
  {key:'far-first',sectionKey:'first',label:'Far first',reason:'Distant',selections:[{...selected,personIds:['b']}]},
  {key:'main-next',sectionKey:'next',label:'Main next',reason:'Nearby',selections:[later]},
 ];
 const prepared=prepareImportedGroups(sections,groups,{sourceName:'sections.xlsx',splitSides:false});
 assert.deepEqual(prepared.payloads.map(p=>p.query.import_group),['main-first','main-next','far-first']);
 assert.deepEqual(prepared.addresses.map(row=>row.sourceRow),[4,30,4]);
 assert.deepEqual(prepared.payloads.flatMap(p=>p.runs.flatMap(r=>r.doors.map(d=>d.seq))),[1,2,3]);
 assert.equal(prepared.voters,3);
});

// --- per-address evidence review -------------------------------------------------------------------------------------
const evidence=(over:Partial<WalkEvidence>={}):WalkEvidence=>({version:1,address_status:'independently_confirmed',location_precision:'parcel',decision:'review',reason_codes:['parcel_representative_point'],source_placement:'geocode',original_house:null,...over});
/** Review doors on Brook Rd: parcel, recovered blank number, conflicting (no pin); plus one clean source-exact door. */
const evidencePayload=():WalkListPayload=>{
 const door=(seq:number,house:string,over:Partial<WalkListPayload['runs'][0]['doors'][0]>)=>({seq,house,unit:'',placed_by:'geocode' as const,lon:-104.5,lat:38.5,voters:[voter(`p${seq}`)],...over});
 const doors=[
  door(1,'2',{placed_by:'parcel',lon:-104.6,lat:38.6,evidence:evidence()}),
  door(2,'4',{placed_by:'parcel',lon:-104.7,lat:38.7,evidence:evidence({address_status:'recovered_number',original_house:'',reason_codes:['number_recovered','parcel_representative_point']})}),
  door(3,'6',{placed_by:'unlocated',lon:null,lat:null,evidence:evidence({address_status:'conflicting',location_precision:'unlocated',reason_codes:['conflicting_parcel_elsewhere']})}),
  door(4,'8',{evidence:evidence({address_status:'source_exact',location_precision:'source_point',decision:'accepted',reason_codes:[]})}),
 ];
 return {...payload,query:{split_sides:'1'},bbox:[-105,38,-104,39],context:undefined,runs:[{...payload.runs[0],street:'Brook Rd',from_house:'2',to_house:'8',path:[],doors}],totals:{...payload.totals,streets:1,stops:1,doors:4,voters:4}};
};
const evidenceGroup=(source:ImportedWalkSelection['source'],evidenced:WalkListPayload,splitSides:boolean)=>prepareImportedGroups(
 [{key:'section',label:'Local',sources:[source],exact:[]}],
 [{key:'local',sectionKey:'section',label:'Local',reason:'Nearby',selections:[{source,payload:evidenced,personIds:['p1','p2','p3','p4'],streetId:10,city:'Example',state:'CO',zip:''}]}],
 {sourceName:'input.xlsx',splitSides});

test('evidence survives local groups, parity and door caps with exact door and voter conservation',()=>{
 for(const splitSides of [true,false])for(const cap of [null,1,3]){
  const prepared=evidenceGroup(selection().source,evidencePayload(),splitSides);
  const sheets=prepared.payloads.flatMap(sheet=>splitWalkSheets(sheet,cap));
  const doors=sheets.flatMap(sheet=>sheet.runs.flatMap(run=>run.doors));
  assert.deepEqual(doors.map(d=>d.house).sort(),['2','4','6','8']);
  assert.equal(sheets.reduce((n,sheet)=>n+sheet.totals.doors,0),4);assert.equal(sheets.reduce((n,sheet)=>n+sheet.totals.voters,0),4);
  assert.deepEqual(doors.map(d=>d.voters.length),doors.map(()=>1));
  assert.deepEqual(doors.find(d=>d.house==='4')!.evidence,evidencePayload().runs[0].doors[1].evidence);
  assert.equal(doors.find(d=>d.house==='6')!.evidence!.address_status,'conflicting');
  assert.ok(sheets.every(sheet=>sheet.runs.every(run=>run.street==='Brook Rd')),'source street text stays exact, never renamed');
 }
});

test('review rows list only doors the service marked for review, with labelled choices and original/sheet addresses',()=>{
 const rows=evidenceReviewRows(evidenceGroup(selection().source,evidencePayload(),false).payloads);
 assert.equal(rows.length,3);
 assert.deepEqual(rows.map(row=>row.sheet),['2 Brook Rd','4 Brook Rd','6 Brook Rd']);
 assert.equal(rows[1].original,'(blank) Brook Rd');assert.equal(rows[0].original,'2 Brook Rd');
 assert.deepEqual(rows[0].options.map(o=>o.label),['Accept parcel location','Remove map pin']);
 assert.deepEqual(rows[1].options.map(o=>o.label),['Accept parcel location and recovered number','Remove map pin']);
 assert.deepEqual(rows[2].options,[{decision:'accepted',label:'Keep printable without map pin'}]);
 assert.equal(rows[2].status,'Conflicting records');assert.equal(rows[2].location,'No map pin');assert.deepEqual(rows[2].reasons,['A parcel with this number is elsewhere']);
 assert.equal(rows[0].location,'Parcel point – entrance not confirmed');
 assert.equal(evidenceReviewRows([{...evidencePayload(),runs:[]}]).length,0);
});

test('a rejected parcel door becomes unlocated without map bounds, keeps its house and people, and reverts when unchosen',()=>{
 const built=evidenceGroup(selection().source,evidencePayload(),false).payloads;
 const [parcel,recovered,conflict]=evidenceReviewRows(built);
 const before=JSON.stringify(built);
 const rejected=applyEvidenceDecisions(built,{[parcel.key]:'rejected',[recovered.key]:'rejected'});
 assert.equal(JSON.stringify(built),before,'input payloads are never mutated');
 const doors=rejected[0].runs.flatMap(run=>run.doors);
 for(const house of ['2','4']){
  const door=doors.find(d=>d.house===house)!;
  assert.equal(door.lon,null);assert.equal(door.lat,null);assert.equal(door.placed_by,'unlocated');
  assert.equal(door.evidence!.location_precision,'unlocated');assert.equal(door.evidence!.decision,'rejected');assert.equal(door.voters.length,1);
 }
 assert.equal(doors.find(d=>d.house==='4')!.evidence!.original_house,'');
 assert.equal(doors.length,4);assert.equal(rejected[0].totals.voters,4);
 assert.deepEqual(rejected[0].bbox,[-104.5,38.5,-104.5,38.5],'only the remaining pin bounds the map; removed parcel points do not');
 const every=applyEvidenceDecisions(built,{[parcel.key]:'rejected',[recovered.key]:'rejected'}).map(p=>p.runs[0].doors.filter(d=>d.lon!==null).length);assert.deepEqual(every,[1]);
 assert.equal(rejected[0].runs[0].doors.find(d=>d.house==='6')!.evidence!.decision,'review','undecided rows are untouched');
 assert.deepEqual(applyEvidenceDecisions(built,{}),built);
 assert.ok(conflict.key!==parcel.key);
});

test('an accepted door keeps its placement and only changes its decision',()=>{
 const built=evidenceGroup(selection().source,evidencePayload(),true).payloads;
 const [parcel,,conflict]=evidenceReviewRows(built);
 const accepted=applyEvidenceDecisions(built,{[parcel.key]:'accepted',[conflict.key]:'accepted'});
 const all=accepted.flatMap(p=>p.runs.flatMap(r=>r.doors)),was=built.flatMap(p=>p.runs.flatMap(r=>r.doors));
 for(const house of ['2','6']){
  const now=all.find(d=>d.house===house)!,old=was.find(d=>d.house===house)!;
  assert.equal(now.evidence!.decision,'accepted');assert.equal(now.lon,old.lon);assert.equal(now.lat,old.lat);assert.equal(now.placed_by,old.placed_by);
  assert.equal(now.evidence!.location_precision,old.evidence!.location_precision);
 }
 assert.deepEqual(accepted.map(p=>p.bbox),built.map(p=>p.bbox));
});

test('a conflicting row keeps the source street text exactly and never offers a rename',()=>{
 const built=evidenceGroup(selection().source,evidencePayload(),false);
 const [,,row]=evidenceReviewRows(built.payloads);
 assert.equal(row.sheet,'6 Brook Rd');assert.ok(!/Brooks/.test(JSON.stringify([row,built.payloads])));
 assert.deepEqual(row.options.map(option=>option.label),['Keep printable without map pin']);
});

test('address CSV adds the four evidence columns only when a door carries evidence',()=>{
 const built=evidenceGroup(selection().source,evidencePayload(),false).payloads[0];
 const lines=(csv:string)=>csv.replace(/^﻿/,'').trimEnd().split('\r\n');
 const csv=lines(buildWalkSheetsCsv([built]));
 assert.match(csv[0],/,"Address status","Location precision","Review decision","Evidence reasons"$/);
 assert.ok(csv.some(line=>line.endsWith('"recovered_number","parcel","review","number_recovered; parcel_representative_point"')));
 assert.ok(csv.some(line=>line.endsWith('"source_exact","source_point","accepted",""')));
 const multi=lines(buildWalkSheetsCsv([...splitWalkSheets(built,1),payload]));
 assert.ok(multi.every(line=>line.split('","').length===multi[0].split('","').length),'multi-sheet CSV keeps one column set');
 assert.ok(!/Address status|Evidence reasons/.test(lines(buildWalkSheetsCsv([payload]))[0]));
});

test('a rejected recovered-number door keeps its recovered number in the CSV with an unlocated, rejected pin',()=>{
 const built=evidenceGroup(selection().source,evidencePayload(),false).payloads;
 const [,recovered]=evidenceReviewRows(built);
 const decided=applyEvidenceDecisions(built,{[recovered.key]:'rejected'});
 const line=buildWalkSheetsCsv(decided).replace(/^﻿/,'').trimEnd().split('\r\n').find(l=>l.includes('"recovered_number"'))!;
 assert.ok(line.endsWith('"recovered_number","unlocated","rejected","number_recovered; parcel_representative_point"'));
 assert.ok(line.includes('"4 Brook Rd"')||line.includes('"4"'),'the recovered house number is still printed');
 assert.equal(decided[0].runs[0].doors.find(d=>d.house==='4')!.placed_by,'unlocated');
});

test('rejecting the only pins keeps the original bbox and context instead of zeroing them',()=>{
 const full=evidenceGroup(selection().source,{...evidencePayload(),context:payload.context},false).payloads;
 const built=full.map(p=>({...p,runs:p.runs.map(r=>({...r,doors:r.doors.filter(d=>d.evidence?.location_precision==='parcel')}))}));
 const keys=evidenceReviewRows(built).map(r=>r.key);
 const out=applyEvidenceDecisions(built,Object.fromEntries(keys.map(k=>[k,'rejected' as const])));
 assert.deepEqual(out[0].bbox,built[0].bbox);assert.deepEqual(out[0].context,built[0].context);
 assert.ok(out[0].runs[0].doors.every(d=>d.lon===null));
});

test('manifest address reviews carry the decision, reason codes and review time',()=>{
 const built=evidenceGroup(selection().source,evidencePayload(),false).payloads;
 const [parcel,,conflict]=evidenceReviewRows(built);
 const at='2026-10-16T12:00:00.000Z';
 const decided=applyEvidenceDecisions(built,{[parcel.key]:'rejected',[conflict.key]:'accepted'});
 const entries=addressReviewEntries(decided,{[parcel.key]:at,[conflict.key]:at});
 assert.deepEqual(entries.map(e=>[e.list,e.door,e.original_house,e.address_status,e.location_precision,e.decision,e.reviewed_at]),[
  [1,1,null,'independently_confirmed','unlocated','rejected',at],
  [1,2,'','recovered_number','parcel','review',null],
  [1,3,null,'conflicting','unlocated','accepted',at],
 ]);
 assert.deepEqual(entries[1].reason_codes,['number_recovered','parcel_representative_point']);
 assert.ok(entries[0].address.includes('Brook Rd'));
 assert.deepEqual(addressReviewEntries([payload]),[]);
});
