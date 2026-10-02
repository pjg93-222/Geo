import {test} from 'node:test';
import assert from 'node:assert/strict';
import {describeOpinions,registrationBin} from '../../src/v3/lib/opinions.ts';
test('distinct registration and opinion are not averaged into a middle position',()=>{
  const fields=[{field:'svi_party_registration',value:'Democrat'},{field:'sci_ct_political_partyaff_gop',value:'Yes'},{field:'sci_ct_political_outlk_vconserv',value:'Yes'}];
  assert.deepEqual(describeOpinions(fields),{affiliation:'Republican',outlook:'Very conservative'});
});
test('missing responses stay unknown rather than middle or other',()=>{
  assert.deepEqual(describeOpinions([]),{affiliation:'Unknown',outlook:'Unknown'});
});
test('conflicting category evidence remains explicit',()=>{
  const fields=[{field:'sci_ct_political_partyaff_gop',value:'Yes'},{field:'sci_ct_political_partyaff_democrat',value:'Yes'},{field:'sci_ct_political_outlk_vconserv',value:'Yes'},{field:'sci_ct_political_outlk_vliberal',value:'Yes'}];
  assert.deepEqual(describeOpinions(fields),{affiliation:'Multiple responses',outlook:'Multiple responses'});
});

test('a conservative Democrat and an unaffiliated Democratic identifier preserve supplied responses',()=>{
  const fields=[{field:'svi_party_registration',value:'Unaffiliated'},{field:'sci_ct_political_partyaff_democrat',value:'Yes'},{field:'sci_ct_political_outlk_swconserv',value:'Yes'}];
  assert.deepEqual(describeOpinions(fields),{affiliation:'Democrat',outlook:'Somewhat conservative'});
});
test('registration map preserves largest category and ties without a partisan average',()=>{
  assert.equal(registrationBin({d:.3,r:.4,u:.2,o:.1}),1);
  assert.equal(registrationBin({d:.4,r:.4,u:.1,o:.1}),4);
  assert.equal(registrationBin({d:.05,r:.05,u:.85,o:.05}),2);
});
