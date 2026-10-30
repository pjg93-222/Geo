import {test} from 'node:test';
import assert from 'node:assert/strict';
import {affiliationChoices,affiliationCodes,affiliationText,affiliationWire,normalizeAffiliation,routeAffiliationCounts} from '../../src/v3/lib/routeAffiliation.ts';
import type {WalkListPayload} from '../../src/v3/api/walkTypes.ts';

test('independent, unaffiliated, third party and blank categories survive round trip',()=>{
  assert.deepEqual(affiliationCodes(affiliationWire(['U','I','G','none'])),['G','I','U','none']);
  assert.equal(affiliationCodes(''),null);assert.deepEqual(affiliationCodes('empty'),[]);
  assert.equal(affiliationText('I,U'),'Independent Party, Unaffiliated');
  const rows=affiliationChoices({I:4,U:7,G:3,T:2,'':1});
  assert.equal(rows.find(r=>r.code==='I')?.count,4);assert.equal(rows.find(r=>r.code==='U')?.count,7);
  assert.equal(rows.find(r=>r.code==='T')?.label,'Code T');assert.equal(rows.find(r=>r.code==='none')?.count,1);
});
test('invalid URL input is reported, never silently converted to all',()=>{
  for(const value of ['I,I','I,','I, R','i','none,none'])assert.throws(()=>affiliationCodes(value));
  assert.match(affiliationText('bad'),/Invalid/);
  assert.equal(normalizeAffiliation('I,G'),'G,I');assert.equal(normalizeAffiliation('bad'),'bad');
});
test('source taxonomy wins and a selected code missing from counts stays visible',()=>{
  const rows=affiliationChoices({I:3},{I:'Independent test category'},['X']);
  assert.equal(rows.find(r=>r.code==='I')?.label,'Independent test category');
  assert.equal(rows.find(r=>r.code==='X')?.count,0);
});
test('composition counts every displayed record, retaining mixed household categories',()=>{
  const payload={runs:[{doors:[{voters:[{party:'I'},{party:'U'},{party:'G'},{party:''}]}]},{doors:[{voters:[{party:'I'}]}]}]} as WalkListPayload;
  assert.deepEqual({...routeAffiliationCounts(payload)},{I:2,U:1,G:1,'':1});
  assert.deepEqual({...routeAffiliationCounts(null)},{});
});
