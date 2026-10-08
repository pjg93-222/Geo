// Candidate support on the People, Profile and Method screens (Phase 4 Lane SD): the overall's terms and the Method paragraph.
// Run with: node --test tests/v3/support-ui.test.ts   (Node 24 strips the types itself)
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {PRESET_WORDS} from '../../src/v3/lib/plan.ts';
import {indicesText, raceModeText, supportMethodText, weightTerms, weightsText} from '../../src/v3/lib/format.ts';

// the T17(d) style gate of tools/field_labels.py, read from its source so the two cannot drift
const source = readFileSync(new URL('../../../tools/field_labels.py', import.meta.url), 'utf8');
const block = /ADVISORY_PATTERN = re\.compile\(([\s\S]*?), re\.I\)/.exec(source)![1];
const ADVISORY = new RegExp([...block.matchAll(/r'([^']*)'/g)].map(m => m[1]).join(''), 'i');

const CO = {cua: 40, part: 35, soi: 25};
const FOUR = {cua: 30, sup: 25, part: 25, soi: 20};
const SIGNALS = {partisan: 1, presidential: 1, registration: 1, primary_ballot: 1};

test('Colorado keeps the strings it had: the People line and the Method sentence', () => {
  assert.equal(weightsText(CO), '40% Alignment + 35% Participation + 25% Sphere of Influence');
  assert.equal(weightsText(undefined), weightsText(CO), 'before the meta has loaded the screens show Colorado’s weights');
  assert.equal(indicesText(CO), 'three indices: 40% Credit Union Alignment, 35% Participation and 25% Sphere of Influence');
  assert.equal(indicesText(undefined), indicesText(CO));
});

test('a candidate district’s overall has four terms, Candidate support second, whatever the key order of the meta', () => {
  assert.equal(weightsText(FOUR), '30% Alignment + 25% Candidate support + 25% Participation + 20% Sphere of Influence');
  assert.equal(weightsText({soi: 20, part: 25, sup: 25, cua: 30}), weightsText(FOUR));
  assert.equal(indicesText(FOUR), 'four indices: 30% Credit Union Alignment, 25% Candidate support, 25% Participation and 20% Sphere of Influence');
  assert.equal(weightTerms(FOUR).length, 4);
});

test('the race mode is named in plain words', () => {
  assert.equal(raceModeText('general'), 'General-election mode');
  assert.equal(raceModeText('primary'), 'Primary mode');
});

test('the Method paragraph names the inputs, the mode and what is left out', () => {
  const text = supportMethodText('Janelle Bynum', 'Democrat', 'general', SIGNALS);
  for (const part of ['General-election mode', 'Janelle Bynum', 'Democrat', 'partisan score', 'presidential race', 'party registration',
    'most recent primary ballot', 'counted equally', 'No turnout count is an input', 'plain mean']) assert.ok(text.includes(part), part);
  assert.equal(supportMethodText('A B', 'Democrat', 'primary', SIGNALS).startsWith('Primary mode.'), true);
});

test('a signal without a weight is not named, and unequal weights are shown', () => {
  const off = supportMethodText('A B', 'Democrat', 'general', {...SIGNALS, primary_ballot: 0});
  assert.ok(!off.includes('primary ballot') && off.includes('counted equally'));
  const uneven = supportMethodText('A B', 'Democrat', 'general', {...SIGNALS, partisan: 2});
  assert.ok(uneven.includes('(weight 2)') && uneven.includes('weighted as shown') && !uneven.includes('counted equally'));
});

test('none of the new strings uses advisory wording', () => {
  assert.ok(ADVISORY.test('a priority target'), 'the gate itself works');
  const strings = [weightsText(FOUR), indicesText(FOUR), raceModeText('general'), raceModeText('primary'), 'Candidate support', 'Sort', 'Overall standing',
    'Sorted by candidate support', supportMethodText('Janelle Bynum', 'Democrat', 'general', SIGNALS),
    supportMethodText('Luke Bronin', 'Democrat', 'primary', {...SIGNALS, partisan: 2})];
  for (const text of strings) assert.ok(!ADVISORY.test(text), text);
});

test('Needs a push on the People and Profile screens: a column and a sort for candidate districts only, the two parts in plain words', () => {
  const read = (f: string) => readFileSync(path.join(import.meta.dirname, '../../src/v3/components/' + f), 'utf8');
  const people = read('PeopleScreen.tsx'), profile = read('ProfileScreen.tsx');
  assert.ok(/\{id:'push',label:'Needs a push'\}/.test(people) && /\{sup&&<Select label="Sort"/.test(people), 'the sort is offered with the other candidate sort');
  assert.ok(/\{sup&&<span className="r" title=\{PRESET_WORDS\.push\}>Needs a push<\/span>\}/.test(people) && /\{sup&&<span className="c-num r">\{r\.push==null\?'':round\(r\.push\)\}<\/span>\}/.test(people), 'the column shows only where candidate support does');
  assert.ok(/undecided and likely to vote, \{round\(push\.supporter\)\} supporter who may not vote/.test(profile), 'the profile gives both parts in words');
  const strings = ['Needs a push', 'Sorted by Needs a push', PRESET_WORDS.push, 'undecided and likely to vote', 'supporter who may not vote'];
  for (const text of strings) assert.ok(!ADVISORY.test(text), text);
});
