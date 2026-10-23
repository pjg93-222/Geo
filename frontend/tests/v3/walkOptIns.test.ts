import {test} from 'node:test';
import assert from 'node:assert/strict';
import {saveWalkOptIns} from '../../src/v3/api/walk.ts';
import type {WalkListPayload,WalkOptIns,WalkOptInState} from '../../src/v3/api/walkTypes.ts';
import {hasRecordedOptIn,optInEntry,optInWriteScope,patchRecordedOptIn,recordedOptIns} from '../../src/v3/lib/walkOptIns.ts';

const state=(status:WalkOptInState['status']):WalkOptInState=>({status,recorded_at:'2026-10-21T18:00:00Z',source:'recorded_manually'});
const empty=():WalkOptIns=>({donor:{status:'unknown',recorded_at:null,source:null},volunteer:{status:'unknown',recorded_at:null,source:null}});
const payload=():WalkListPayload=>({schema:'walk-list-1',generated_on:'2026-10-21',list:{key:'saved-a',title:'List A',kind:'standard',created_at:null},query:{streets:'1:both'},
  window:{primaries:[],generals:[],abs_rule:''},totals:{streets:1,stops:1,doors:1,voters:2,walk_m:30},bbox:[0,0,1,1],fingerprint:'roster-a',
  runs:[{stop:1,street_id:1,street:'Example Street',side:'both',precinct:'',from_house:'10',to_house:'10',walk_m:30,lr:0,path:[],approach:[],doors:[{seq:1,house:'10',unit:'',placed_by:'unlocated',lon:null,lat:null,
    voters:['a','b'].map(person_id=>({person_id,name:`Resident ${person_id}`,phone:'',pri:{voted:0,eligible:0},gen:{voted:0,eligible:0},abs:{voted:0,eligible:0},party:'',age:null,gender:'',reg_date:'',last:null,opt_ins:empty()}))}]}]});

test('only manually recorded statuses produce an opt-in; vendor scores and missing/foreign sources stay unknown',()=>{
  for(const input of [undefined,null,{}, {sci_donor:99,sci_volunteer:100}, {donor:99}, {donor:{status:'opted_in',source:'vendor'}},
    {donor:{status:'opted_in',recorded_at:'2026-10-21'}}, {donor:{status:'predicted',source:'recorded_manually'}}]){
    assert.deepEqual(recordedOptIns(input),empty());assert.equal(hasRecordedOptIn(input),false);
  }
  assert.equal(hasRecordedOptIn({donor:state('opted_out'),volunteer:state('unknown')}),false);
  assert.equal(hasRecordedOptIn({donor:state('unknown'),volunteer:state('opted_in')}),true);
  assert.deepEqual(recordedOptIns({donor:state('opted_out'),volunteer:state('opted_in')}),{donor:state('opted_out'),volunteer:state('opted_in')});
});

test('independent donor and volunteer replies merge without changing roster, route or the other person',()=>{
  const original=payload(),other=original.runs[0].doors[0].voters[1];
  const donor=patchRecordedOptIn(original,'a','donor',state('opted_in'));
  // The donor response could have carried the older volunteer state; callers merge only its edited kind.
  const both=patchRecordedOptIn(donor,'a','volunteer',state('opted_out'));
  assert.deepEqual(both.runs[0].doors[0].voters[0].opt_ins,{donor:state('opted_in'),volunteer:state('opted_out')});
  assert.strictEqual(both.runs[0].doors[0].voters[1],other);
  assert.deepEqual(original.runs[0].doors[0].voters[0].opt_ins,empty());
  assert.deepEqual(both.totals,original.totals);assert.deepEqual(both.query,original.query);assert.equal(both.fingerprint,original.fingerprint);
  assert.deepEqual(both.runs[0].doors[0].voters.map(v=>v.person_id),['a','b']);
});

test('list/district invalidation rejects late responses and old completion cannot clear a new request',()=>{
  const scope=optInWriteScope(),old=scope.begin('same-person','donor');
  assert.equal(scope.pending(),true);scope.reset();
  assert.equal(old.signal.aborted,true);assert.equal(old.current(),false);assert.equal(scope.pending(),false);
  const current=scope.begin('same-person','donor');old.finish();
  assert.equal(current.current(),true);assert.equal(scope.pending(),true);
  const volunteer=scope.begin('same-person','volunteer');
  current.finish();assert.equal(volunteer.current(),true);assert.equal(scope.pending(),true);
  volunteer.finish();assert.equal(scope.pending(),false);
});

test('a superseded write cannot overwrite its replacement even when its fetch answers after abort',()=>{
  const scope=optInWriteScope(),old=scope.begin('a','donor'),latest=scope.begin('a','donor');
  assert.equal(old.signal.aborted,true);assert.equal(old.current(),false);assert.equal(latest.current(),true);
  old.finish();assert.equal(latest.current(),true);latest.finish();assert.equal(scope.pending(),false);
});

test('opt-in API sends the saved list, exactly one requested kind, CSRF and same-origin credentials',async()=>{
  const original=globalThis.fetch,requests:{url:string;init?:RequestInit}[]=[];
  globalThis.fetch=async(input,init)=>{
    requests.push({url:String(input),init});
    return new Response(JSON.stringify(String(input)==='/api/auth/session'?{csrf:'opt-in-csrf'}:{saved:1,opt_ins:{a:{donor:state('opted_in'),volunteer:empty().volunteer}}}));
  };
  try{
    await saveWalkOptIns('saved-a',[optInEntry('a','donor','opted_in')]);
    const write=requests.find(request=>request.url==='/api/v3/walk/opt-ins')!;
    assert.ok(write);assert.equal(write.init?.method,'POST');assert.equal(write.init?.credentials,'same-origin');
    assert.equal((write.init?.headers as Record<string,string>)['X-CSRFToken'],'opt-in-csrf');
    assert.deepEqual(JSON.parse(String(write.init?.body)),{list:'saved-a',entries:[{person_id:'a',donor:'opted_in'}]});
    assert.deepEqual(optInEntry('a','volunteer','unknown'),{person_id:'a',volunteer:'unknown'});
  }finally{globalThis.fetch=original;}
});
