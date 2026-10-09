// Block plan helpers: the blocks that fit one list, the request, the doors asked for, the planned blocks in the page address, the scores batches.
// Run with: node --test tests/v3/plan.test.ts
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {DOORS_DEFAULT, DOORS_MAX, DOORS_MIN, MAX_LIST_PEOPLE, MAX_PLAN_SPANS, POTENTIAL_DEFAULT, PRESET_WORDS, clampDoors, idChunks, parseSpansParam, pickUnderCap, planOptions, potentialOf, raceWords, readDoors, spansParam, turfOf, weightValue, writeDoors} from '../../src/v3/lib/plan.ts';
import {weightsQuery} from '../../src/v3/lib/untapped.ts';

const rows = (people: number[]) => people.map((n, i) => ({span: 100 + i, rank: i + 1, people: n}));

test('pickUnderCap: the first ranks whose people fit, stopping at the first block that would go over', () => {
  assert.deepEqual(pickUnderCap(rows([500, 500, 500, 10])), [100, 101, 102], 'exactly the cap is allowed, one person more is not');
  assert.deepEqual(pickUnderCap(rows([900, 700, 5, 5])), [100], 'a lower rank never takes the place of a higher one');
  assert.deepEqual(pickUnderCap(rows([1501, 3])), [], 'a first block over the cap leaves nothing');
  assert.deepEqual(pickUnderCap(rows([20, 20, 20]), 40), [100, 101]);
  assert.deepEqual(pickUnderCap([]), []);
});

test('pickUnderCap: at most 200 blocks, and ranks are taken in rank order whatever order they arrive in', () => {
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

test('planned blocks in the page address: whole numbers, sorted, once each, junk skipped, at most 200', () => {
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
  assert.deepEqual(Object.keys(PRESET_WORDS).sort(), ['identify', 'mixed', 'push', 'turnout']);
  assert.equal(PRESET_WORDS.push, 'Untapped potential: undecided people who usually vote and supporters who may not vote, each counted up to 1.5 times by sphere of influence. Doors with a recorded result are left out.');
  assert.ok(PRESET_WORDS.mixed.endsWith('times their chance of voting (the mean of five turnout scores).'), 'chance of voting is the mean of the five turnout scores');
  assert.ok(PRESET_WORDS.turnout.endsWith('times their chance of not voting (one minus the mean of five turnout scores).'));
  for (const [id, text] of Object.entries(PRESET_WORDS)) assert.ok(text.length > 40 && !ADVISORY.test(text), id);
  const screen = readFileSync(path.join(import.meta.dirname, '../../src/v3/components/StreetPlan.tsx'), 'utf8');
  const words = [...screen.matchAll(/'([^'\n]{12,})'|>([^<>{}]{12,})</g)].map(m => m[1] ?? m[2]);
  assert.ok(words.length > 5 && words.every(w => !ADVISORY.test(w)), 'the block plan screen uses neutral words');
});

test('block plan screen: ranks only on request, shows nothing it did not rank for this area, and the walk button needs a list that fits', () => {
  const src = readFileSync(path.join(import.meta.dirname, '../../src/v3/components/StreetPlan.tsx'), 'utf8');
  assert.ok(/result\?\.key===areaKey\(p\.area\)\?result\.plan:null/.test(src), 'a plan ranked for another area is not shown');
  assert.ok(/disabled=\{!picked\.length\|\|over\|\|p\.busy\}/.test(src) && /onClick=\{\(\)=>p\.onWalk\(picked\.map\(s=>s\.span\)\)\}/.test(src), 'only checked blocks that fit go on');
  assert.ok(/pop:p\.density\?w\.pop:'0'/.test(src), 'population density is never asked for before it is built');
  assert.ok(/aria-label="Ranked blocks"/.test(src) && /role="columnheader"/.test(src) && /aria-label=\{`Walk \$\{s\.street\}, rank \$\{s\.rank\}`\}/.test(src), 'a labelled table with a labelled checkbox on every block');
  assert.ok(/job\.current\?\.abort\(\)/.test(src) && /useEffect\(\(\)=>\(\)=>job\.current\?\.abort\(\),\[\]\)/.test(src), 'an older request is dropped when a newer one starts or the screen goes');
});

// --- one click: doors, the settings an older server leaves out, the request, and the words of the plan --------------------------------------
test('doors: whole numbers from 10 to 500, blank or unreadable text takes the default, and the default is held to the range too', () => {
  for (const [v, want] of [[60, 60], ['75', 75], [' 75 ', 75], [9, 10], ['9', 10], [0, 10], [-5, 10], [501, 500], ['100000', 500], [74.6, 75], ['74.4', 74], ['1e2', 100],
    ['', 60], ['abc', 60], [NaN, 60], [Infinity, 60], [null, 60], [undefined, 60]] as const) assert.equal(clampDoors(v), want, String(v));
  assert.equal(clampDoors('', 80), 80); assert.equal(clampDoors('x', 5), 10); assert.equal(clampDoors('', 900), 500); assert.equal(clampDoors('', NaN), 60);
  assert.deepEqual([DOORS_MIN, DOORS_MAX, DOORS_DEFAULT], [10, 500, 60]);
});

test('doors are kept per viewer and district, and a storage that throws changes nothing', () => {
  const kept = new Map<string, string>();
  const store = {getItem: (k: string) => kept.get(k) ?? null, setItem: (k: string, v: string) => { kept.set(k, v); }};
  assert.equal(readDoors('or-05', 60, store), 60, 'nothing saved: the district default');
  writeDoors('or-05', 120, store); writeDoors('ct-01', 25, store);
  assert.equal(readDoors('or-05', 60, store), 120); assert.equal(readDoors('ct-01', 60, store), 25);
  assert.deepEqual([...kept.keys()].sort(), ['terrier.untapped.doors.ct-01', 'terrier.untapped.doors.or-05']);
  writeDoors('or-05', 9999, store); assert.equal(readDoors('or-05', 60, store), 500, 'what is written is held to the range');
  kept.set('terrier.untapped.doors.or-05', 'junk'); assert.equal(readDoors('or-05', 80, store), 80, 'unreadable text gives the default');
  const broken = {getItem: (): string | null => { throw new Error('blocked'); }, setItem: (): void => { throw new Error('blocked'); }};
  assert.equal(readDoors('or-05', 80, broken), 80); assert.doesNotThrow(() => writeDoors('or-05', 80, broken));
});

test('settings an older server does not send: balance and influence 0.5, 60 doors and a 400 m hop', () => {
  assert.deepEqual(POTENTIAL_DEFAULT, {wp: 0.5, wi: 0.5});
  assert.deepEqual(potentialOf(null), {wp: 0.5, wi: 0.5}); assert.deepEqual(potentialOf({}), {wp: 0.5, wi: 0.5});
  assert.deepEqual(potentialOf({potential: {wp: 0.3, wi: 1}}), {wp: 0.3, wi: 1});
  assert.deepEqual(turfOf(null), {doors: 60, jump_m: 400}); assert.deepEqual(turfOf({}), {doors: 60, jump_m: 400});
  assert.deepEqual(turfOf({turf: {doors: 80, jump_m: 250}}), {doors: 80, jump_m: 250});
});

test('the plan request carries the weights as text, and the walk list request carries fresh only when asked', () => {
  assert.deepEqual(weightsQuery({wp: 0.5, wi: 1}), {wp: '0.50', wi: '1.00'});
  assert.deepEqual({preset: 'push', ...weightsQuery({wp: 0.3, wi: 0.25}), doors: 80}, {preset: 'push', wp: '0.30', wi: '0.25', doors: 80});
  const api = readFileSync(path.join(import.meta.dirname, '../../src/v3/api/walk.ts'), 'utf8');
  assert.ok(/spans\?:number\[\],fresh\?:boolean\)/.test(api) && /fresh:fresh\?1:undefined/.test(api), 'fresh is sent as fresh=1, and left out otherwise');
  const types = readFileSync(path.join(import.meta.dirname, '../../src/v3/api/walkTypes.ts'), 'utf8');
  assert.ok(/t_mean\?: number \| null/.test(types) && !/p_mean/.test(types), 'rows carry t_mean, the mean chance of voting');
  assert.ok(/stopped: 'full' \| 'exhausted' \| 'far' \| 'cap'/.test(types) && /asked: number/.test(types), 'the turf names its own words');
});

test('the block plan says blocks, never streets, in its own words, and the walk screen says planned blocks', () => {
  const read = (f: string) => readFileSync(path.join(import.meta.dirname, '../../src/v3/components/' + f), 'utf8');
  const phrases = (src: string) => {
    const bare = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`\\])\/\/.*$/gm, '$1').replace(/\$\{[^}]*\}/g, '');
    const out: string[] = [];
    for (const m of bare.matchAll(/'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)"|`((?:[^`\\]|\\.)*)`/g)) out.push(m[1] ?? m[2] ?? m[3] ?? '');
    for (const m of bare.matchAll(/>([^<>{}=;()]+)</g)) out.push(m[1]);
    return out.map(s => s.trim()).filter(s => /\s/.test(s));   // words, not class names
  };
  const plan = phrases(read('StreetPlan.tsx'));
  assert.ok(plan.length > 20, 'the plan screen has its words');
  assert.deepEqual(plan.filter(s => /\bstreets?\b/i.test(s)), [], 'no plan string says street');
  assert.ok(plan.some(s => /Rank blocks|Walk the checked blocks/.test(s)) && plan.includes('Adjust plan'), 'the summary and the buttons');
  assert.deepEqual(Object.entries(PRESET_WORDS).filter(([, text]) => /\bstreets?\b/i.test(text)), [], 'the preset words do not say street');
  const screen = read('WalkScreen.tsx');
  assert.ok(!/planned street/i.test(phrases(screen).join('\n')) && /'planned block'/.test(screen) && /Remove planned blocks/.test(screen) && /Planned blocks removed\./.test(screen));
});

test('the block plan screen: the two sliders are the map\'s, the vote chance is a column, and the one-click blocks start checked', () => {
  const src = readFileSync(path.join(import.meta.dirname, '../../src/v3/components/StreetPlan.tsx'), 'utf8');
  assert.ok(/<summary>Adjust plan<\/summary>/.test(src) && !/<details[^>]* open/.test(src), 'collapsed, as Adjust plan');
  assert.ok(/Balance: undecided voters ↔ supporters/.test(src) && /Influence weight/.test(src) && /DEFAULTS_NOTE/.test(src), 'the two sliders and the defaults note');
  assert.ok(/clampWeights\(\{\.\.\.weights,\[k\]:x\},defaults\)/.test(src) && /onClick=\{\(\)=>p\.onWeights\(defaults\)\}/.test(src), 'steps of 0.05 within 0 to 1, and a Defaults button');
  assert.ok(/weightsQuery\(weights\)/.test(src) && /p\.area\.precinct\?\{doors:p\.doors\}:\{\}/.test(src), 'the request carries the weights, and the doors in a precinct only');
  assert.ok(/next\.turf\?\.spans\.length\?next\.turf\.spans:pickUnderCap\(next\.spans\)/.test(src), 'the blocks the click picks start checked');
  assert.ok(/'Vote chance'\]/.test(src) && /data-label="Vote chance">\{s\.t_mean==null\?'':`\$\{percent\(s\.t_mean\)\}%`\}/.test(src), 'the mean chance of voting, as a percent, empty when a server sends none');
});
