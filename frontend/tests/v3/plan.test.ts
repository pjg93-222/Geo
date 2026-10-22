// Block plan helpers: the blocks that fit one list, the request, the doors asked for, the planned blocks in the page address, the scores batches.
// Run with: node --test tests/v3/plan.test.ts
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, readdirSync, statSync} from 'node:fs';
import path from 'node:path';
import {DOORS_DEFAULT, DOORS_MAX, DOORS_MIN, MAX_LIST_PEOPLE, MAX_PLAN_SPANS, MAX_SCORE_IDS, POTENTIAL_DEFAULT, PRESET_WORDS, clampDoors, idChunks, parseSpansParam, parseTrim, pickUnderCap, planOptions, potentialOf, raceWords, readDoors, spansParam, trimFor, trimQuery, turfOf, weightValue, writeDoors} from '../../src/v3/lib/plan.ts';
import {weightsQuery} from '../../src/v3/lib/untapped.ts';

const rows = (people: number[]) => people.map((n, i) => ({span: 100 + i, rank: i + 1, people: n}));

test('pickUnderCap: the first ranks whose people fit, stopping at the first block that would go over', () => {
  assert.deepEqual(pickUnderCap(rows([5000, 4000, 1000, 10])), [100, 101, 102], 'exactly the cap is allowed, one person more is not');
  assert.deepEqual(pickUnderCap(rows([6000, 4500, 5, 5])), [100], 'a lower rank never takes the place of a higher one');
  assert.deepEqual(pickUnderCap(rows([10001, 3])), [], 'a first block over the cap leaves nothing');
  assert.deepEqual(pickUnderCap(rows([500, 500, 500, 10]), 1500), [100, 101, 102]);
  assert.deepEqual(pickUnderCap(rows([20, 20, 20]), 40), [100, 101]);
  assert.deepEqual(pickUnderCap([]), []);
});

test('pickUnderCap: at most 200 blocks, and ranks are taken in rank order whatever order they arrive in', () => {
  const many = rows(Array.from({length: 300}, () => 1));
  assert.equal(pickUnderCap(many).length, MAX_PLAN_SPANS); assert.equal(MAX_LIST_PEOPLE, 10000);
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

test('planned blocks in the page address: whole numbers, in the order written, once each, junk skipped, at most 200', () => {
  assert.deepEqual(parseSpansParam('15,12,15, 7 ,x,,-4,1.5,1234567890'), [15, 12, 7], 'pick order is kept: the one-click list is cut at its last block');
  assert.deepEqual(parseSpansParam(''), []); assert.deepEqual(parseSpansParam(null), []); assert.deepEqual(parseSpansParam(undefined), []);
  assert.equal(parseSpansParam(Array.from({length: 250}, (_, i) => i + 1).join(',')).length, 200);
  assert.deepEqual(parseSpansParam(Array.from({length: 250}, (_, i) => 250 - i).join(',')).slice(0, 2), [250, 249], 'the first 200 in the order written');
  assert.equal(spansParam([15, 12]), '15,12'); assert.equal(spansParam([]), '');
  assert.deepEqual(parseSpansParam(spansParam([9, 3, 3])), [9, 3], 'what is written is read back, once each');
  assert.deepEqual(parseSpansParam(spansParam([812, 790, 815])), [812, 790, 815]);
});

test('doors, wp and wi of an address or a saved query: doors 1 to the people ceiling, weights the server reads, anything else is left out', () => {
  assert.deepEqual(parseTrim('60', '0.50', '1.00'), {doors: 60, wp: '0.50', wi: '1.00'});
  assert.deepEqual(parseTrim(' 10 ', '0', '1'), {doors: 10, wp: '0', wi: '1'});
  assert.deepEqual(parseTrim('500', '0.3', '0.0005'), {doors: 500, wp: '0.3', wi: '0.0005'});
  assert.deepEqual(parseTrim('60'), {doors: 60}, 'no weights: the district\'s own');
  assert.deepEqual(parseTrim('60', '1.5', 'x'), {doors: 60}, 'a weight the server would refuse is left out, the doors stay');
  assert.deepEqual(parseTrim('60', '-0.1', '0.12345'), {doors: 60});
  assert.deepEqual(parseTrim('60', '1.0001', '1.0'), {doors: 60, wi: '1.0'});
  for (const [good, n] of [['1', 1], ['9', 9], ['501', 501], ['1000', 1000], ['0060', 60]] as const) assert.deepEqual(parseTrim(good), {doors: n}, good);
  for (const bad of ['0', '000', '1001', '5000', '10000', '1000000', '-60', '6.5', '1e2', '060x', '', 'abc', ' ']) assert.equal(parseTrim(bad, '0.5', '0.5'), null, bad);
  assert.equal(parseTrim(null, '0.5', '0.5'), null); assert.equal(parseTrim(undefined), null);
});

test('the list is cut only when the plan says it can: trim present (null or set) sends doors, wp and wi; absent sends none', () => {
  const q = weightsQuery({wp: 0.3, wi: 1});
  assert.deepEqual(trimFor({trim: {span: 790, kept: 36, of: 144}}, 60, q), {doors: 60, wp: '0.30', wi: '1.00'});
  assert.deepEqual(trimFor({trim: null}, 60, q), {doors: 60, wp: '0.30', wi: '1.00'}, 'nothing cut still asks for the doors, so a saved list rebuilds the same');
  assert.equal(trimFor({}, 60, q), null, 'an older server has no trim and refuses doors on the list');
  assert.deepEqual(trimQuery(null), {doors: null, wp: null, wi: null});
  assert.deepEqual(trimQuery({doors: 60, wp: '0.30', wi: '1.00'}), {doors: '60', wp: '0.30', wi: '1.00'});
  assert.deepEqual(trimQuery({doors: 60}), {doors: '60', wp: null, wi: null});
  const written = trimQuery(trimFor({trim: null}, 75, q));
  assert.deepEqual(parseTrim(written.doors, written.wp, written.wi), {doors: 75, wp: '0.30', wi: '1.00'}, 'what is written is read back');
});

test('scores come in requests of at most 1,500 ids, apart from the people one list can hold', () => {
  assert.equal(MAX_SCORE_IDS, 1500);
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
  const LINED_UP = 'five turnout scores, lined up against people\'s own voting record';
  assert.ok(PRESET_WORDS.mixed.endsWith(`times their chance of voting (${LINED_UP}).`), 'chance of voting: five turnout scores lined up against the voting record');
  assert.ok(PRESET_WORDS.turnout.endsWith(`times their chance of not voting (one minus their chance of voting: ${LINED_UP}).`));
  assert.ok(Object.values(PRESET_WORDS).every(t => !/mean of five/.test(t)), 'no text calls the chance of voting a plain mean');
  for (const [id, text] of Object.entries(PRESET_WORDS)) assert.ok(text.length > 40 && !ADVISORY.test(text), id);
  const screen = readFileSync(path.join(import.meta.dirname, '../../src/v3/components/StreetPlan.tsx'), 'utf8');
  const words = [...screen.matchAll(/'([^'\n]{12,})'|>([^<>{}]{12,})</g)].map(m => m[1] ?? m[2]);
  assert.ok(words.length > 5 && words.every(w => !ADVISORY.test(w)), 'the block plan screen uses neutral words');
});

test('wording: no screen calls the chance of voting a plain mean of turnout scores', () => {
  const root = path.join(import.meta.dirname, '../../src/v3');
  const files: string[] = [];
  (function walk(d: string) { for (const n of readdirSync(d)) { const p = path.join(d, n); if (statSync(p).isDirectory()) walk(p); else if (/\.(ts|tsx)$/.test(n)) files.push(p); } })(root);
  assert.ok(files.length > 10);
  const stale = files.filter(f => /mean of (the )?five turnout|mean chance of voting/i.test(readFileSync(f, 'utf8'))).map(f => path.relative(root, f));
  assert.deepEqual(stale, [], 'five turnout scores, lined up against people\'s own voting record');
});

test('block plan screen: ranks only on request, shows nothing it did not rank for this area, and the walk button needs a list that fits', () => {
  const src = readFileSync(path.join(import.meta.dirname, '../../src/v3/components/StreetPlan.tsx'), 'utf8');
  assert.ok(/result\?\.key===areaKey\(p\.area,p\.profile\)\?result\.plan:null/.test(src), 'a plan ranked for another area or voter profile is not shown');
  assert.ok(/disabled=\{!picked\.length\|\|over\|\|p\.busy\}/.test(src) && /onClick=\{\(\)=>p\.onWalk\(picked\.map\(s=>s\.span\)\)\}/.test(src), 'only checked blocks that fit go on');
  assert.ok(/pop:p\.density\?w\.pop:'0'/.test(src), 'population density is never asked for before it is built');
  assert.ok(/aria-label="Ranked blocks"/.test(src) && /role="columnheader"/.test(src) && /aria-label=\{`Walk \$\{s\.street\}, rank \$\{s\.rank\}`\}/.test(src), 'a labelled table with a labelled checkbox on every block');
  assert.ok(/job\.current\?\.abort\(\)/.test(src) && /useEffect\(\(\)=>\(\)=>job\.current\?\.abort\(\),\[\]\)/.test(src), 'an older request is dropped when a newer one starts or the screen goes');
});

// --- one click: doors, the settings an older server leaves out, the request, and the words of the plan --------------------------------------
test('doors: whole numbers from 1 to 1,000, blank or unreadable text takes the default, and the default is held to the range too', () => {
  for (const [v, want] of [[60, 60], ['75', 75], [' 75 ', 75], [9, 9], ['1', 1], [0, 1], [-5, 1], [501, 501], ['1000', 1000], ['5000', 1000], ['100000', 1000], [74.6, 75], ['74.4', 74], ['1e2', 100],
    ['', 60], ['abc', 60], [NaN, 60], [Infinity, 60], [null, 60], [undefined, 60]] as const) assert.equal(clampDoors(v), want, String(v));
  assert.equal(clampDoors('', 80), 80); assert.equal(clampDoors('x', 5), 5); assert.equal(clampDoors('x', 0), 1); assert.equal(clampDoors('', 900), 900); assert.equal(clampDoors('', 20000), 1000); assert.equal(clampDoors('', NaN), 60);
  assert.deepEqual([DOORS_MIN, DOORS_MAX, DOORS_DEFAULT], [1, 1000, 60]);
});

test('doors are kept per viewer and district, and a storage that throws changes nothing', () => {
  const kept = new Map<string, string>();
  const store = {getItem: (k: string) => kept.get(k) ?? null, setItem: (k: string, v: string) => { kept.set(k, v); }};
  assert.equal(readDoors('or-05', 60, store), 60, 'nothing saved: the district default');
  writeDoors('or-05', 120, store); writeDoors('ct-01', 25, store);
  assert.equal(readDoors('or-05', 60, store), 120); assert.equal(readDoors('ct-01', 60, store), 25);
  assert.deepEqual([...kept.keys()].sort(), ['terrier.untapped.doors.ct-01', 'terrier.untapped.doors.or-05']);
  writeDoors('or-05', 99999, store); assert.equal(readDoors('or-05', 60, store), 1000, 'what is written is held to the range');
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
  assert.ok(/spans\?:number\[\],fresh\?:boolean,trim\?:WalkListTrim,splitSides=true,profile='all'\)/.test(api) && /fresh:fresh\?1:undefined/.test(api), 'fresh is sent as fresh=1, and left out otherwise');
  assert.ok(/doors:trim\?\.doors,wp:trim\?\.wp,wi:trim\?\.wi/.test(api), 'doors, wp and wi go on the list request, and only from a trim');
  const types = readFileSync(path.join(import.meta.dirname, '../../src/v3/api/walkTypes.ts'), 'utf8');
  assert.ok(/t_mean\?: number \| null/.test(types) && !/p_mean/.test(types), 'rows carry t_mean, the mean chance of voting');
  assert.ok(/stopped: 'full' \| 'exhausted' \| 'far' \| 'cap'/.test(types) && /asked: number/.test(types), 'the turf names its own words');
  assert.ok(/trim\?: \{span: number; kept: number; of: number\} \| null/.test(types), 'the last block cut to the doors asked for: absent from an older server, null when nothing was cut');
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
