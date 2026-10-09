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

test('defaultWeights and planWalkPath: an older server still gets 0.5/0.5, and the button opens the Walk tab on the precinct', () => {
  assert.deepEqual(U.defaultWeights(null), D); assert.deepEqual(U.defaultWeights(undefined), D); assert.deepEqual(U.defaultWeights({}), D);
  assert.deepEqual(U.defaultWeights({potential: W(0.4, 0.75)}), W(0.4, 0.75));
  assert.equal(U.planWalkPath('41005-101'), '/walk?precinct=41005-101&plan=1');
  assert.equal(new URL(U.planWalkPath('a b&c'), 'http://x').searchParams.get('precinct'), 'a b&c', 'the id travels whole');
});

// ---- the client: an ETag per level, a 304 hands back the same object --------------------------------------------------------------------
const C = await import('../../src/v3/api/client.ts');
const realFetch = globalThis.fetch;
type Call = {url: string; headers: Record<string, string>};
function fake(answers: {status: number; body?: unknown; etag?: string}[]) {
  const calls: Call[] = [];
  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    calls.push({url, headers: {...(init?.headers as Record<string, string>)}});
    const a = answers[Math.min(calls.length - 1, answers.length - 1)];
    return new Response(a.status === 304 ? null : JSON.stringify(a.body ?? {}), {status: a.status, headers: a.etag ? {ETag: a.etag} : {}});
  }) as typeof fetch;
  return calls;
}

test('getUntapped: asks for its level, sends the last ETag back, and a 304 returns the very same object', async () => {
  const one = payload({level: 'state_senate'}), two = payload({level: 'state_senate', people: [1, 1]});
  const calls = fake([{status: 200, body: one, etag: '"v1"'}, {status: 304, etag: '"v1"'}, {status: 200, body: two, etag: '"v2"'}, {status: 304, etag: '"v2"'}]);
  try {
    const first = await C.getUntapped('state_senate');
    assert.equal(calls[0].url, '/api/v3/untapped?level=state_senate'); assert.equal(calls[0].headers['If-None-Match'], undefined, 'nothing seen yet');
    assert.deepEqual(first, one);
    const again = await C.getUntapped('state_senate');
    assert.equal(calls[1].headers['If-None-Match'], '"v1"'); assert.equal(again, first, 'unchanged: the same object, so a screen holding it does not redraw');
    const changed = await C.getUntapped('state_senate');
    assert.equal(calls[2].headers['If-None-Match'], '"v1"'); assert.deepEqual(changed, two); assert.notEqual(changed, first);
    assert.equal(await C.getUntapped('state_senate'), changed); assert.equal(calls[3].headers['If-None-Match'], '"v2"');
    await C.getUntapped('precinct').catch(() => {});
    assert.equal(calls[4].url, '/api/v3/untapped?level=precinct'); assert.equal(calls[4].headers['If-None-Match'], undefined, 'each level keeps its own');
  } finally { globalThis.fetch = realFetch; }
});

test('getUntapped: the refusals of the endpoint come back as ApiError with the server\'s words; an answer with no ETag is never reused', async () => {
  const calls = fake([{status: 404, body: {error: 'No candidate is set for this district.'}}]);
  try {
    await assert.rejects(C.getUntapped('zip'), (e: unknown) => e instanceof C.ApiError && e.status === 404 && e.answered && e.message === 'No candidate is set for this district.');
    globalThis.fetch = (async () => new Response('<html>', {status: 502})) as typeof fetch;
    await assert.rejects(C.getUntapped('zip'), (e: unknown) => e instanceof C.ApiError && e.status === 502 && !e.answered);
    const plain = fake([{status: 200, body: payload({level: 'tract'})}, {status: 200, body: payload({level: 'tract'})}]);
    await C.getUntapped('tract'); await C.getUntapped('tract');
    assert.equal(plain[1].headers['If-None-Match'], undefined, 'no ETag given, none sent back');
    assert.equal(calls.length, 1);
  } finally { globalThis.fetch = realFetch; }
});

// ---- wiring read from the source, as the walk tests do -----------------------------------------------------------------------------------
import {readFileSync} from 'node:fs';
const src = (f: string) => readFileSync(new URL(`../../src/v3/${f}`, import.meta.url), 'utf8');

test('wiring: Untapped panel on the rail, Plan walk list on precinct rows and the precinct block, refetch on mount and focus', () => {
  const panel = src('components/UntappedPanel.tsx'), screen = src('components/MapScreen.tsx'), rail = src('components/AreaRail.tsx');
  assert.ok(panel.includes("p.level==='precinct'&&p.canPlan") && panel.includes('Plan walk list'), 'rows: precinct level only, and only with the street plan');
  assert.ok(/p\.precinct&&p\.canPlan/.test(panel), 'the block: the same rule');
  assert.ok(panel.includes("addEventListener('focus'") && panel.includes("'visibilitychange'") && /\bload\(\);/.test(panel), 'fetched on mount, again on focus');
  assert.ok(panel.includes('ranked(p.data,w)') && panel.includes('DEFAULTS_NOTE') && panel.includes('UNDECIDED_WORDS') && panel.includes('SUPPORTER_WORDS') && panel.includes('>Defaults<'));
  assert.ok(screen.includes('navigate(planWalkPath(precinct))') && screen.includes('canPlan=!!features?.roi'), 'one click goes to the Walk tab; the button needs features.roi');
  assert.ok(screen.includes('<UntappedPanel') && screen.includes('top={untappedOn?') && screen.includes('untapped={untappedOn?'), 'panel and block only while the layer is on');
  assert.ok(rail.includes('{p.top}') && rail.includes('<UntappedBlock'));
  assert.ok(screen.includes("readWeights(features.district,defaults)") && screen.includes('writeWeights(features.district,w)'), 'sliders are remembered per district');
  assert.ok(screen.includes("getGeometry(meta,polyLevel,'coarse')"), 'State House and Senate outlines come from the coarse geometry');
});

test('wiring: the Colorado line sits beside the Index buttons and only when the district has no candidate', () => {
  const header = src('components/Header.tsx');
  assert.ok(header.includes('{p.features&&!p.features.candidate&&<span className="untapped-note">Untapped needs a candidate: see Oregon District 5 or Connecticut District 1.</span>}'));
  assert.ok(header.indexOf('untapped-note') > header.indexOf('label="Index"') && header.indexOf('untapped-note') < header.indexOf('label="Level"'));
});

test('wiring: the canvas swaps the level fills for the outlines while they are set, and never draws them at street zoom', () => {
  const canvas = src('components/MapCanvas.tsx');
  assert.ok(canvas.includes("id:'fill-poly'") && canvas.includes("id:'poly-line'") && /maxzoom:STREET_ZOOM,layout:\{visibility:'none'\}/.test(canvas));
  assert.ok(canvas.includes("['fill-a','fill-b','areas-line','sel-glow','sel-ring','sel-line']") && canvas.includes("on?'none':'visible'"));
});
