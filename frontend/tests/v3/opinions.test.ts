import {test} from 'node:test';
import assert from 'node:assert/strict';
import {MARGIN_LABELS,describeOpinions,marginBin,outlookLabel} from '../../src/v3/lib/opinions.ts';
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
test('two neighbouring outlook flags show the more extreme one',()=>{
  const fields=[{field:'sci_ct_political_outlk_vconserv',value:'Yes'},{field:'sci_ct_political_outlk_swconserv',value:'Yes'}];
  assert.equal(describeOpinions(fields).outlook,'Very conservative');
});
test('neighbouring outlook flags collapse to the more extreme; runs across middle or with a gap stay multiple',()=>{
  const want:[number[],string][]=[[[],'Unknown'],[[0],'Very conservative'],[[2],'Middle outlook'],[[4],'Very liberal'],
    [[0,1],'Very conservative'],[[3,4],'Very liberal'],[[1,2],'Somewhat conservative'],[[2,3],'Somewhat liberal'],
    [[2,3,4],'Very liberal'],[[0,1,2],'Very conservative'],
    [[1,2,3],'Multiple responses'],[[0,1,2,3,4],'Multiple responses'],[[0,4],'Multiple responses'],[[0,2],'Multiple responses'],[[2,4],'Multiple responses']];
  for(const [flags,label] of want){assert.equal(outlookLabel(flags),label,flags.join());assert.equal(outlookLabel([...flags].reverse()),label,flags.join());}
});

test('a conservative Democrat and an unaffiliated Democratic identifier preserve supplied responses',()=>{
  const fields=[{field:'svi_party_registration',value:'Unaffiliated'},{field:'sci_ct_political_partyaff_democrat',value:'Yes'},{field:'sci_ct_political_outlk_swconserv',value:'Yes'}];
  assert.deepEqual(describeOpinions(fields),{affiliation:'Democrat',outlook:'Somewhat conservative'});
});
test('registration map: five steps of D share minus R share, breaks at 5 and 15 points either way',()=>{
  assert.deepEqual([...MARGIN_LABELS],['R +15 or more','R +5 to 15','Within 5','D +5 to 15','D +15 or more']);
  for(const [d,r,want] of [[.10,.40,0],[.20,.35,0],[.20,.34,1],[.30,.40,1],[.30,.35,1],[.30,.34,2],[.40,.40,2],[.34,.30,2],[.35,.30,3],[.40,.30,3],[.34,.20,3],[.35,.20,4],[.60,.10,4],[0,0,2],[1,0,4],[0,1,0]] as const)
    assert.equal(marginBin(d,r),want,`${d} - ${r}`);
  assert.equal(marginBin(.2,.15),3,'exactly 5 points is the lean step, whatever the floating point');
  assert.equal(marginBin(.15,.2),1);
});
test('registration map: an area where unaffiliated is the largest group still shows its D and R lean',()=>{
  assert.equal(marginBin(.18,.30),1,'U 50%: R +12 is the R lean step, not a grey Unaffiliated');
  assert.equal(marginBin(.25,.08),4,'U 60%: D +17');
  assert.equal(marginBin(.05,.05),2,'U 85%, D and R even: the centre');
});
