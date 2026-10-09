// Untapped potential on the client (lib/untapped.ts): the area formula, the ranked list, the person formula (the S1 hand-checked
// values), weight limits and storage. Run with: node --test tests/v3/untapped.test.ts   (Node 24 strips the types itself)
import {test} from 'node:test';
import assert from 'node:assert/strict';
import type {UntappedAreas, UntappedWeights} from '../../src/v3/lib/untapped.ts';

const U = await import('../../src/v3/lib/untapped.ts');
const near = (a: number, b: number, msg?: string) => assert.ok(Math.abs(a - b) < 1e-9, `${msg ?? ''} ${a} vs ${b}`);
const W = (wp: number, wi: number): UntappedWeights => ({wp, wi});
const D = W(0.5, 0.5);

const payload = (over: Partial<UntappedAreas> = {}): UntappedAreas => ({
  schema: 'v3-untapped-1', level: 'precinct', ids: ['41005-101', '41005-102'], labels: ['Precinct 101', 'Precinct 102'],
  people: [100, 80], knocked: [3, 0], undecided: [10, 2], supporter: [4, 20], undecided_i: [6, 1], supporter_i: [2, 10],
  defaults: D, t_source: {sai: 180, record: 0, even: 0}, results: {version: '0-0', people_knocked: 3}, ...over,
});

test('areaAt: the endpoint formula on two hand areas, parts that add up to the value', () => {
  const u = payload();
  // wp .5, wi .5: undecided .5 x (10 + .5 x 6) = 6.5, supporter .5 x (4 + .5 x 2) = 2.5; second area 1.25 and 12.5
  const a = U.areaAt(u, 0, D), b = U.areaAt(u, 1, D);
  near(a.undecided, 6.5); near(a.supporter, 2.5); near(a.value, 9);
  near(b.undecided, 1.25); near(b.supporter, 12.5); near(b.value, 13.75);
  assert.deepEqual([a.id, a.label, a.people, a.knocked], ['41005-101', 'Precinct 101', 100, 3]);
  // the ends of the balance slider with no influence: all undecided, all supporters
  near(U.areaAt(u, 0, W(1, 0)).value, 10); near(U.areaAt(u, 0, W(0, 0)).value, 4);
  near(U.areaAt(u, 1, W(1, 0)).value, 2); near(U.areaAt(u, 1, W(0, 0)).value, 20);
  // full influence weight: the influence sums count once more
  near(U.areaAt(u, 0, W(0, 1)).value, 6); near(U.areaAt(u, 1, W(0, 1)).value, 30);
  for (const w of [D, W(0.3, 1), W(1, 0), W(0, 0.25), W(0.85, 0.7)]) for (const k of [0, 1]) {
    const r = U.areaAt(u, k, w);
    near(r.undecided + r.supporter, r.value, `parts of area ${k}`);
  }
});

test('areaAt equals the sum of personPotential over the people the sums came from', () => {
  const people: [number, number, number][] = [[0.5, 1, 0], [1, 0, 1], [0.75, 0.8, 0.4], [0.25, 0.5, 1], [0, 0.9, 0.3], [1, 1, 0.6]];
  const sum = (f: (s: number, t: number, i: number) => number) => people.reduce((x, [s, t, i]) => x + f(s, t, i), 0);
  const u = payload({
    ids: ['x'], labels: ['x'], people: [people.length], knocked: [0],
    undecided: [sum((s, t) => (1 - Math.abs(2 * s - 1)) * t)], supporter: [sum((s, t) => s * (1 - t))],
    undecided_i: [sum((s, t, i) => (1 - Math.abs(2 * s - 1)) * t * i)], supporter_i: [sum((s, t, i) => s * (1 - t) * i)],
  });
  for (const w of [D, W(0.3, 1), W(1, 0), W(0, 1), W(0.65, 0.2)])
    near(U.areaAt(u, 0, w).value, people.reduce((x, [s, t, i]) => x + U.personPotential(s, t, i, w), 0), `weights ${w.wp}/${w.wi}`);
});

test('personPotential: the hand-checked values of the server formula', () => {
  near(U.personPotential(0.5, 1, 0, D), 0.5, 'undecided who always votes');
  near(U.personPotential(1, 0, 1, D), 0.75, 'the maximum at the defaults');
  for (const [t, i] of [[0, 0], [0.5, 0.5], [1, 1]]) near(U.personPotential(0, t, i, D), 0, 'no support, no potential');
  near(U.personPotential(1, 1, 0.7, D), 0, 'a sure supporter who always votes');
  near(U.personPotential(0.75, 0.8, 0.4, D), 0.33);
  near(U.personPotential(0.25, 0.5, 1, W(0.3, 1)), 0.325);
  near(U.personPotential(0.75, 0.8, 0.9, W(1, 0)), (1 - Math.abs(2 * 0.75 - 1)) * 0.8, 'wp 1 and wi 0 is the undecided term alone');
  near(U.personPotential(0.75, 0.8, 0.9, W(0, 0)), 0.75 * (1 - 0.8), 'wp 0 and wi 0 is the supporter term alone');
  // a 0.05 grid: never negative, never above max(wp, 1 - wp) x (1 + wi)
  for (let s = 0; s <= 20; s++) for (let t = 0; t <= 20; t++) for (const w of [D, W(0.3, 1), W(1, 0), W(0, 0.45)]) {
    const v = U.personPotential(s / 20, t / 20, 1, w);
    assert.ok(v >= -1e-12 && v <= Math.max(w.wp, 1 - w.wp) * (1 + w.wi) + 1e-12, `${s}/${t}: ${v}`);
  }
});

test('ranked: value descending, ties by id, at most the limit, and it follows the weights', () => {
  const u = payload({
    ids: ['d', 'b', 'a', 'c', 'e'], labels: ['D', 'B', 'A', 'C', 'E'], people: [1, 1, 1, 1, 1], knocked: [0, 0, 0, 0, 0],
    undecided: [5, 5, 5, 1, 9], supporter: [0, 0, 0, 0, 0], undecided_i: [0, 0, 0, 0, 0], supporter_i: [0, 0, 0, 0, 0],
  });
  assert.deepEqual(U.ranked(u, W(1, 0)).map(a => a.id), ['e', 'a', 'b', 'd', 'c'], 'ties a, b, d by id');
  assert.deepEqual(U.ranked(u, W(1, 0), 2).map(a => a.id), ['e', 'a']);
  assert.equal(U.ranked(u, W(1, 0), 0).length, 0);
  assert.equal(U.ranked(payload(), D).length, 2, 'fewer areas than the limit of 25');
  const v = payload();
  assert.deepEqual(U.ranked(v, W(1, 0)).map(a => a.id), ['41005-101', '41005-102'], 'all undecided: 10 beats 2');
  assert.deepEqual(U.ranked(v, W(0, 0)).map(a => a.id), ['41005-102', '41005-101'], 'all supporters: 20 beats 4');
  near(U.ranked(v, D)[0].value, 13.75);
  assert.equal(U.ranked(payload({ids: [], labels: [], people: [], knocked: [], undecided: [], supporter: [], undecided_i: [], supporter_i: []}), D).length, 0);
});

test('clampWeights: 0 to 1 in steps of 0.05, a missing or non-numeric weight takes the default', () => {
  assert.deepEqual(U.clampWeights({wp: 0.33, wi: 0.12}, D), {wp: 0.35, wi: 0.1});
  assert.deepEqual(U.clampWeights({wp: -1, wi: 2}, D), {wp: 0, wi: 1});
  assert.deepEqual(U.clampWeights({wp: NaN, wi: Infinity}, W(0.4, 0.2)), {wp: 0.4, wi: 0.2});
  assert.deepEqual(U.clampWeights({}, W(0.4, 0.2)), {wp: 0.4, wi: 0.2});
  assert.deepEqual(U.clampWeights({wp: '0.9' as never, wi: null as never}, D), D, 'not numbers');
  assert.deepEqual(U.clampWeights({wp: 0.15, wi: 0.95}, D), {wp: 0.15, wi: 0.95}, 'a step stays where it is');
  for (const x of [0, 0.05, 0.15, 0.3, 0.55, 0.85, 1]) assert.equal(U.clampWeights({wp: x}, D).wp, x, `${x} is not moved by float error`);
});

test('weights storage: a district key, defaults when nothing usable is saved, never throws', () => {
  const mem = new Map<string, string>();
  const store = {getItem: (k: string) => mem.get(k) ?? null, setItem: (k: string, v: string) => void mem.set(k, v)};
  assert.deepEqual(U.readWeights('or-05', D, store), D, 'nothing saved');
  U.writeWeights('or-05', W(0.3, 0.8), store);
  assert.deepEqual([...mem.keys()], ['terrier.untapped.weights.or-05']);
  assert.deepEqual(U.readWeights('or-05', D, store), W(0.3, 0.8));
  assert.deepEqual(U.readWeights('ct-01', D, store), D, 'another district keeps its own');
  mem.set('terrier.untapped.weights.or-05', '{"wp":0.33,"wi":9}');
  assert.deepEqual(U.readWeights('or-05', D, store), W(0.35, 1), 'a saved value is held to the limits');
  mem.set('terrier.untapped.weights.or-05', '{"wp":0.6}');
  assert.deepEqual(U.readWeights('or-05', W(0.5, 0.25), store), W(0.6, 0.25), 'a missing weight takes the default');
  for (const junk of ['not json', '[1,2]', '"x"', 'null', '7', '']) {
    mem.set('terrier.untapped.weights.or-05', junk);
    assert.deepEqual(U.readWeights('or-05', D, store), D, `junk: ${junk}`);
  }
  const broken = {getItem: () => { throw new Error('storage is off'); }, setItem: () => { throw new Error('storage is off'); }};
  assert.deepEqual(U.readWeights('or-05', D, broken), D);
  assert.doesNotThrow(() => U.writeWeights('or-05', D, broken));
  // with no storage given it uses the browser's; where that does not exist (node) it still answers with the defaults
  assert.deepEqual(U.readWeights('or-05', D), D);
  assert.doesNotThrow(() => U.writeWeights('or-05', D));
});

test('weightsQuery and the shared words', () => {
  assert.deepEqual(U.weightsQuery(W(0.5, 0.05)), {wp: '0.50', wi: '0.05'});
  assert.deepEqual(U.weightsQuery(W(1, 0)), {wp: '1.00', wi: '0.00'});
  assert.equal(U.UNDECIDED_WORDS, 'Undecided, usually votes'); assert.equal(U.SUPPORTER_WORDS, 'Supporter, may not vote');
  assert.equal(U.DEFAULTS_NOTE, 'Defaults: balance 50/50, influence 0.5 (top influence counts 1.5 times).');
  const ADVISORY = /\b(prospect|prospects|target|targets|targeting|best|recommend|recommended|priority|priorities|should|ideal|worth|likely to join|persuad|persuasion|convert|opportunity|high[- ]value|insufficient evidence)\b/i;
  for (const s of [U.UNDECIDED_WORDS, U.SUPPORTER_WORDS, U.DEFAULTS_NOTE]) assert.ok(!ADVISORY.test(s), s);
});
