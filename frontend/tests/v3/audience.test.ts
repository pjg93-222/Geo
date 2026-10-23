import {test} from 'node:test';
import assert from 'node:assert/strict';
import {unzipSync,strFromU8} from 'fflate';
import type {AudienceOptions,AudienceStreet} from '../../src/v3/api/audience.ts';
import {isVoterFileUnavailable} from '../../src/v3/api/audience.ts';
import {ApiError} from '../../src/v3/api/client.ts';
import {DEFAULT_FILTER,NO_MATCHING_DOORS,VOTER_BASIS,VOTER_HEADER,VOTER_SCOPE_WARNING,VOTER_ZIP,filterInWords,groupVoterStreets,keyHandle,moveHandle,nearerHandle,presetMatches,registeredLabel,scoreAt,supportEnds,supportPresets,toggleChoice,voterExportFiles,voterGroupsCsv} from '../../src/v3/lib/audienceGroups.ts';
import type {AudienceFilter,VoterExportInfo} from '../../src/v3/lib/audienceGroups.ts';
import {PUBLIC_ZIP} from '../../src/v3/lib/publicStreetGroups.ts';
import {readWalkCsv} from '../../src/v3/lib/walk.ts';
import {buildZip} from '../../src/v3/lib/walkZip.ts';

const options:AudienceOptions={district:'OR-05',candidate_party:'D',support_coverage:true,
  party:[{code:'D',label:'Democrat',count:10},{code:'G',label:'Green',count:4},{code:'L',label:'Libertarian',count:3},{code:'none',label:'No party code',count:1}],
  canvass:[{code:'S',label:'Supporter',count:5},{code:'O',label:'Opposed',count:2},{code:'none',label:'No result yet',count:50}]};
const filter=(over:Partial<AudienceFilter>={}):AudienceFilter=>({...DEFAULT_FILTER,...over});
const street=(id:string,matching_doors:number,x=0,y=0):AudienceStreet=>({id,label:`Street ${id}`,center:[x,y],matching_doors,voter_doors:matching_doors+5,dropped_by_exclude:1});

test('presets use exact thirds, display 33 and 67, and the selected one matches only its own range',()=>{
  const p=supportPresets('D');
  assert.deepEqual(p.map(x=>x.label),['Leans R','Persuadable','Leans D','Everyone']);
  assert.deepEqual(p.map(x=>[x.low,x.high]),[[0,100/3],[100/3,200/3],[200/3,100],[0,100]]);
  assert.deepEqual(p.map(x=>[Math.round(x.low),Math.round(x.high)]),[[0,33],[33,67],[67,100],[0,100]]);
  assert.deepEqual(p.filter(x=>presetMatches(x,{low:100/3,high:200/3})).map(x=>x.id),['mid']);
  assert.deepEqual(p.filter(x=>presetMatches(x,DEFAULT_FILTER.support)).map(x=>x.id),['all']);
  assert.equal(p.some(x=>presetMatches(x,{low:33,high:67})),false);
});
test('a Republican candidate swaps the first and third names and the end labels; ranges stay',()=>{
  const d=supportPresets('D'),r=supportPresets('R');
  assert.deepEqual(r.map(x=>x.label),['Leans D','Persuadable','Leans R','Everyone']);
  assert.deepEqual(r.map(x=>[x.low,x.high]),d.map(x=>[x.low,x.high]));
  assert.deepEqual(supportEnds('D'),{low:'Republican-leaning',mid:'Middle',high:'Democratic-leaning'});
  assert.deepEqual(supportEnds('R'),{low:'Democratic-leaning',mid:'Middle',high:'Republican-leaning'});
});
test('presets include their edges: a score on a third boundary lies in both neighbours',()=>{
  const [low,mid,high]=supportPresets('D'),inside=(s:number,p:{low:number;high:number})=>p.low<=s&&s<=p.high;
  assert.ok(inside(100/3,low)&&inside(100/3,mid));assert.ok(inside(200/3,mid)&&inside(200/3,high));assert.ok(inside(0,low));assert.ok(inside(100,high));
});
test('handles never cross, stay within 0 to 100 and may meet',()=>{
  const r={low:20,high:60,includeNoScore:true};
  assert.equal(moveHandle(r,'low',80).low,60);assert.equal(moveHandle(r,'high',5).high,20);
  assert.equal(moveHandle(r,'low',-9).low,0);assert.equal(moveHandle(r,'high',500).high,100);
  assert.equal(moveHandle(r,'low',Number.NaN),r);
  assert.equal(moveHandle(r,'low',33).includeNoScore,true);
});
test('arrow keys move a handle by 1, stop at the other handle, and other keys are ignored',()=>{
  const r={low:0,high:100/3,includeNoScore:true};
  assert.equal(keyHandle(r,'low','ArrowRight')!.low,1);assert.equal(keyHandle(r,'low','ArrowUp')!.low,1);assert.equal(keyHandle(r,'low','ArrowLeft')!.low,0);
  assert.equal(keyHandle(r,'high','ArrowLeft')!.high,100/3-1);assert.equal(keyHandle(r,'high','ArrowRight')!.high,100/3+1);
  assert.equal(keyHandle({...r,low:50,high:50},'low','ArrowRight')!.low,50);
  assert.equal(keyHandle({...r,low:50,high:50},'high','ArrowLeft')!.high,50);
  assert.equal(keyHandle(r,'low','End')!.low,100/3);assert.equal(keyHandle(r,'high','Home')!.high,0);
  assert.equal(keyHandle(r,'low','a'),null);
});
test('a press on the track takes the nearer handle and maps to 0 to 100',()=>{
  const r={low:20,high:60,includeNoScore:true};
  assert.equal(nearerHandle(r,10),'low');assert.equal(nearerHandle(r,70),'high');assert.equal(nearerHandle(r,41),'high');
  assert.equal(nearerHandle({...r,low:50,high:50},60),'high');assert.equal(nearerHandle({...r,low:50,high:50},40),'low');
  assert.equal(scoreAt(150,100,200),25);assert.equal(scoreAt(0,100,200),0);assert.equal(scoreAt(900,100,200),100);assert.equal(scoreAt(5,0,0),0);
});
test('chips: a code is included or excluded, never both, and a second press clears it',()=>{
  let f=toggleChoice(DEFAULT_FILTER,'include','party','G');
  assert.deepEqual(f.include.party,['G']);
  f=toggleChoice(f,'exclude','party','G');assert.deepEqual([f.include.party,f.exclude.party],[[],['G']]);
  f=toggleChoice(f,'exclude','party','G');assert.deepEqual(f.exclude.party,[]);
  assert.deepEqual(toggleChoice(DEFAULT_FILTER,'include','canvass','S').include.party,[]);
  assert.deepEqual(DEFAULT_FILTER.include.party,[]);   // never mutated
});
test('registration labels say "Registered ..." and never use spectrum words',()=>{
  const labels=options.party.map(registeredLabel);
  assert.deepEqual(labels,['Registered Democrat','Registered Green','Registered Libertarian','No party code']);
  assert.equal(registeredLabel({code:'U',label:'Registered Unaffiliated',count:1}),'Registered Unaffiliated');
  const ends=([ 'D','R',null ] as const).flatMap(c=>[...Object.values(supportEnds(c)),...supportPresets(c).map(p=>p.label)]);
  const words=[...labels,...options.canvass.map(c=>c.label),...ends].join(' | ');
  assert.doesNotMatch(words,/\b(far left|left|lean left|centrist|lean right|right|far right)\b/i);
});
test('the filter in words matches the spec examples',()=>{
  assert.equal(filterInWords(DEFAULT_FILTER,options),'No filter; every registered voter counts');
  const f=filter({include:{party:['G','L'],canvass:[]},exclude:{party:[],canvass:['O']},support:{low:100/3,high:200/3,includeNoScore:true}});
  assert.equal(filterInWords(f,options),'Include Registered Green or Registered Libertarian; exclude canvass Opposed; support 33 to 67, plus no score');
  assert.equal(filterInWords(filter({support:{low:0,high:100,includeNoScore:false}}),options),'Support 0 to 100');
  assert.equal(filterInWords(filter({exclude:{party:['D'],canvass:['O','none']},support:{low:20,high:45,includeNoScore:false}}),options),'Exclude Registered Democrat or canvass Opposed or No result yet; support 20 to 45');
  assert.equal(filterInWords(filter({include:{party:['G'],canvass:['S']}}),options),'Include Registered Green and canvass Supporter');
});
test('voter streets group on matching doors; streets with none are listed as no matching doors',()=>{
  const rows=[street('1',80,-122.5,45.45),street('2',80,-122.5001,45.45),street('3',70,-122.4,45.45),street('4',70,-122.4001,45.45),street('5',0),street('6',151)];
  const before=JSON.stringify(rows),g=groupVoterStreets(rows,100,150);
  assert.equal(JSON.stringify(rows),before);
  assert.equal(g.basis,'voter-file');assert.ok(g.groups.every(x=>x.basis==='voter-file'));
  assert.deepEqual(g.groups.map(x=>x.doors),[150,150]);
  assert.deepEqual(g.zero.map(s=>s.id),['5']);assert.deepEqual(g.oversized.map(s=>s.id),['6']);
  assert.equal(NO_MATCHING_DOORS,'No matching doors');
  assert.equal(g.groups[0].streets[0].voter_doors,g.groups[0].streets[0].doors+5);
  assert.throws(()=>groupVoterStreets([street('1',-1)],100,150));
});
const info:VoterExportInfo={district:'OR-05',filter:'Include Registered Green; support 33 to 67, plus no score',polygon:[[-122.1234567891,45.1],[-122.2,45.1],[-122.2,45.2],[-122.1234567891,45.1]],min:100,max:150,generatedAt:'2026-10-22T00:00:00.000Z',
  totals:{matching_doors:300,voter_doors:900,dropped_by_exclude:7,unplaced:2},supportCoverage:false};
test('export names carry the basis and the voter ZIP mirrors the public structure',()=>{
  assert.equal(VOTER_ZIP,'street-groups-voter-file.zip');assert.equal(PUBLIC_ZIP,'street-groups-public-estimate.zip');
  const g=groupVoterStreets([street('1',80,-122.5,45.45),street('2',80,-122.5001,45.45),street('3',70,-122.4,45.45),street('4',70,-122.4001,45.45),street('5',0),street('6',151),street('7',10,-122.9,45.9)],100,150);
  const files=unzipSync(buildZip(voterExportFiles(g,info)));
  assert.deepEqual(Object.keys(files),['group-01.csv','group-02.csv','not-grouped.csv','README.txt']);
  const first=strFromU8(files['group-01.csv']);
  const header=VOTER_HEADER.map(h=>`"${h}"`).join(',')+'\r\n';
  assert.ok(first.startsWith(header));   // the ZIP decoder drops the byte-order mark
  assert.ok(voterGroupsCsv([],info).startsWith('﻿'+header));
  assert.match(first,/"1","Street \d","\d","\d+","\d+","1","grouped","","voter-file","Include Registered Green/);
  const rest=strFromU8(files['not-grouped.csv']);
  assert.match(rest,/"","Street 5","5","0","5","1","zero","No matching doors","voter-file"/);
  assert.match(rest,/"","Street 6","6","151","156","1","over_max","Over maximum","voter-file"/);
  assert.match(rest,/"","Street 7","7","10","15","1","unassigned","Unassigned balance","voter-file"/);
  const readme=strFromU8(files['README.txt']);
  for(const text of [VOTER_SCOPE_WARNING,VOTER_BASIS,'Filter: '+info.filter,'[[-122.123457,45.1],[-122.2,45.1],[-122.2,45.2],[-122.123457,45.1]]','100 to 150 voter doors','dropped by exclude in mixed households: 7','unplaced: 2','OR-05','No support scores for this district','Counts are exact; small numbers can identify a household. Keep exports on this machine.'])assert.ok(readme.includes(text),text);
  assert.ok(!Object.keys(unzipSync(buildZip(voterExportFiles(groupVoterStreets([street('1',100)],100,150),info)))).includes('not-grouped.csv'));
});
test('the importer takes one voter-file group with a scope warning and strips the formula guard',()=>{
  const g=groupVoterStreets([{...street('1',100),label:'-Lost Rd'},street('2',50)],100,150);
  const one=voterGroupsCsv(g.groups.flatMap(x=>x.streets).map(s=>({group:1,status:'grouped' as const,street:s})),info);
  const read=readWalkCsv(one);
  assert.deepEqual(read.rows.map(r=>r.street).sort(),['-Lost Rd','Street 2']);assert.equal(read.warning,VOTER_SCOPE_WARNING);
});
test('the importer rejects voter-file exports with several groups or ungrouped streets',()=>{
  const s=(id:string,n:number)=>groupVoterStreets([street(id,n)],1,1000).groups[0].streets[0];
  assert.throws(()=>readWalkCsv(voterGroupsCsv([{group:1,status:'grouped',street:s('1',100)},{group:2,status:'grouped',street:s('2',100)}],info)),/voter-file export has several groups\. Import one group file at a time/);
  assert.throws(()=>readWalkCsv(voterGroupsCsv([{group:'',status:'zero',street:s('3',1)}],info)),/not grouped can't be imported/);
  assert.throws(()=>readWalkCsv(voterGroupsCsv([{group:1,status:'grouped',street:s('1',100)},{group:'',status:'unassigned',street:s('2',10)}],info)),/not grouped/);
});
test('a CSV that only resembles the voter export is not read as one',()=>{
  const near=VOTER_HEADER.slice(0,-1).join(',')+'\nMain St'+','.repeat(VOTER_HEADER.length-2)+'\n';
  assert.equal(readWalkCsv(near).warning,undefined);
});
test('only a 403 or 503 hides voter-file mode',()=>{
  for(const status of [403,503])assert.equal(isVoterFileUnavailable(new ApiError('no',status)),true);
  for(const e of [new ApiError('missing',404),new ApiError('bad',400),new Error('x'),null])assert.equal(isVoterFileUnavailable(e),false);
});
