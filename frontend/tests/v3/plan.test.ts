// Street plan helpers: the streets that fit one list, the request, the planned streets in the page address, the scores batches.
// Run with: node --test tests/v3/plan.test.ts
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {MAX_LIST_PEOPLE, MAX_PLAN_SPANS, PRESET_WORDS, idChunks, parseSpansParam, pickUnderCap, planOptions, raceWords, spansParam, weightValue} from '../../src/v3/lib/plan.ts';

const rows = (people: number[]) => people.map((n, i) => ({span: 100 + i, rank: i + 1, people: n}));

test('pickUnderCap: the first ranks whose people fit, stopping at the first street that would go over', () => {
  assert.deepEqual(pickUnderCap(rows([500, 500, 500, 10])), [100, 101, 102], 'exactly the cap is allowed, one person more is not');
  assert.deepEqual(pickUnderCap(rows([900, 700, 5, 5])), [100], 'a lower rank never takes the place of a higher one');
  assert.deepEqual(pickUnderCap(rows([1501, 3])), [], 'a first street over the cap leaves nothing');
  assert.deepEqual(pickUnderCap(rows([20, 20, 20]), 40), [100, 101]);
  assert.deepEqual(pickUnderCap([]), []);
});

test('pickUnderCap: at most 200 streets, and ranks are taken in rank order whatever order they arrive in', () => {
  const many = rows(Array.from({length: 300}, () => 1));
  assert.equal(pickUnderCap(many).length, MAX_PLAN_SPANS); assert.equal(MAX_LIST_PEOPLE, 1500);
  assert.deepEqual(pickUnderCap(many).slice(0, 3), [100, 101, 102]);
  assert.deepEqual(pickUnderCap([...rows([5, 6, 7])].reverse()), [100, 101, 102]);
  const input = rows([5, 6, 7]).reverse(), order = input.map(r => r.span); pickUnderCap(input); assert.deepEqual(input.map(r => r.span), order, 'the input is not reordered');
});

test('weights: what the server reads (six digits, four decimals kept, no sign), blank or unreadable is 0', () => {
  for (const [text, want] of [['1', 1], ['0.5', 0.5], [' 2 ', 2], ['', 0], ['abc', 0], ['-3', 0], ['0', 0], ['1e-9', 0], ['0.00004', 0], ['0.12346', 0.1235], ['1234567', 999999], ['Infinity', 0], ['NaN', 0]] as const)
    assert.equal(weightValue(text), want, text);
  assert.ok(/^\d{1,6}(\.\d{1,6})?$/.test(String(weightValue('0.0001'))), 'the smallest weight is not written with an exponent');
  assert.deepEqual(planOptions('mixed', {value: '1', pop: '', voter: '0.5'}), {preset: 'mixed', w_value: 1, w_pop: 0, w_voter: 0.5});
  assert.deepEqual(planOptions('identify', {value: 'x', pop: '2', voter: '-1'}), {preset: 'identify', w_value: 0, w_pop: 2, w_voter: 0});
});

test('planned streets in the page address: whole numbers, sorted, once each, junk skipped, at most 200', () => {
  assert.deepEqual(parseSpansParam('15,12,15, 7 ,x,,-4,1.5,1234567890'), [7, 12, 15]);
  assert.deepEqual(parseSpansParam(''), []); assert.deepEqual(parseSpansParam(null), []); assert.deepEqual(parseSpansParam(undefined), []);
  assert.equal(parseSpansParam(Array.from({length: 250}, (_, i) => i + 1).join(',')).length, 200);
  assert.equal(spansParam([15, 12]), '12,15'); assert.equal(spansParam([]), '');
  assert.deepEqual(parseSpansParam(spansParam([9, 3, 3])), [3, 9], 'what is written is read back');
});

test('scores come in requests of at most 1,500 ids', () => {
  const ids = Array.from({length: 3200}, (_, i) => 'p' + i);
  assert.deepEqual(idChunks(ids).map(c => c.length), [1500, 1500, 200]);
  assert.deepEqual(idChunks(['a']), [['a']]); assert.deepEqual(idChunks([]), []);
  assert.equal(idChunks(ids, 1000).length, 4);
});

test('wording: the race, and a plain explanation for each preset the server offers (no advisory words)', () => {
  const ADVISORY = /\b(prospect|prospects|target|targets|targeting|best|recommend|recommended|priority|priorities|should|ideal|worth|likely to join|persuad|persuasion|convert|opportunity|high[- ]value|insufficient evidence)\b/i;
  assert.equal(raceWords('general'), 'General election'); assert.equal(raceWords('primary'), 'Primary election'); assert.equal(raceWords(undefined), 'General election');
  assert.deepEqual(Object.keys(PRESET_WORDS).sort(), ['identify', 'mixed', 'turnout']);
  for (const [id, text] of Object.entries(PRESET_WORDS)) assert.ok(text.length > 40 && !ADVISORY.test(text), id);
  const screen = readFileSync(path.join(import.meta.dirname, '../../src/v3/components/StreetPlan.tsx'), 'utf8');
  const words = [...screen.matchAll(/'([^'\n]{12,})'|>([^<>{}]{12,})</g)].map(m => m[1] ?? m[2]);
  assert.ok(words.length > 5 && words.every(w => !ADVISORY.test(w)), 'the street plan screen uses neutral words');
});

test('street plan screen: ranks only on request, shows nothing it did not rank for this area, and the walk button needs a list that fits', () => {
  const src = readFileSync(path.join(import.meta.dirname, '../../src/v3/components/StreetPlan.tsx'), 'utf8');
  assert.ok(/result\?\.key===areaKey\(p\.area\)\?result\.plan:null/.test(src), 'a plan ranked for another area is not shown');
  assert.ok(/disabled=\{!picked\.length\|\|over\|\|p\.busy\}/.test(src) && /onClick=\{\(\)=>p\.onWalk\(picked\.map\(s=>s\.span\)\)\}/.test(src), 'only checked streets that fit go on');
  assert.ok(/pop:p\.density\?w\.pop:'0'/.test(src), 'population density is never asked for before it is built');
  assert.ok(/aria-label="Ranked streets"/.test(src) && /role="columnheader"/.test(src) && /aria-label=\{`Walk \$\{s\.street\}, rank \$\{s\.rank\}`\}/.test(src), 'a labelled table with a labelled checkbox on every street');
  assert.ok(/job\.current\?\.abort\(\)/.test(src) && /useEffect\(\(\)=>\(\)=>job\.current\?\.abort\(\),\[\]\)/.test(src), 'an older request is dropped when a newer one starts or the screen goes');
});
