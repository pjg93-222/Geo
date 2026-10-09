// Small areas and areas with no registered people: nothing is hidden. A small area is colored like any other and carries a plain note;
// an area with no registered people gets the plain pale bin and no values. Run with: node --test tests/v3/areas.test.ts
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {registerHooks} from 'node:module';
import type {AreasPayload, Meta} from '../../src/v3/types.ts';

// areas.ts imports its neighbours without a file extension, so node needs a resolve hook to load it straight from source.
registerHooks({resolve: (spec, ctx, next) => ctx.parentURL?.includes('/src/v3/') && /^\.{1,2}\//.test(spec) && !/\.[cm]?[jt]sx?$/.test(spec) ? next(spec + '.ts', ctx) : next(spec, ctx)});
const A = await import('../../src/v3/lib/areas.ts');

// six blocks: a large one, one person, 19 people (one under the line), 20 people in 9 households (the household line only),
// 14 people in 6 households (both lines), and one with no registered people
const ids = ['b0', 'b1', 'b2', 'b3', 'b4', 'b5'];
const n = [120, 1, 19, 20, 14, 0], households = [48, 1, 12, 9, 6, 0];
const status = ['ok', 'ok', 'ok', 'ok', 'ok', 'empty'] as ('ok' | 'empty')[];
const col = (g: (i: number) => number) => ids.map((_, i) => (status[i] === 'ok' ? g(i) : null));
const cell = (k: number) => ({
  mean: col(i => 20 + 10 * ((i * k) % 5)), se: col(() => 4), measured: col(i => 15 + 20 * ((i * k) % 5)), pct: col(i => 25 * ((i * k) % 5)),
  top10: col(i => 0.02 + 0.05 * ((i * k) % 5)), top10_se: col(() => 0.02), top10_pct: col(i => 25 * ((i * k) % 5)),
});
const payload = (): AreasPayload => ({
  schema: 'v3-areas-1', snapshot: 's', basis: 'registration', level: 'block', ids, labels: ids, status, n: ids.map((_, i) => n[i]), households: ids.map((_, i) => households[i]),
  parent: ids.map(() => null), precinct: ids.map(() => null), tract: ids.map(() => null), blocks: ids.map(() => null),
  groups: {
    ovr: cell(1), cua: cell(2), part: cell(3), soi: cell(4),
    lean: {mean: col(i => 10 * i - 20), se: col(() => 5), measured: col(() => 90), share_d: col(() => 0.4), share_r: col(() => 0.3), share_u: col(() => 0.2)},
  },
});
const meta = {share_breaks: [0.05, 0.08, 0.12, 0.18], measured_breaks: [20, 40, 60, 80]} as unknown as Meta;

test('the small-area note: plain words, the household count only when its own line is crossed', () => {
  assert.equal(A.smallNote(120, 48), null, 'a large area has no note');
  assert.equal(A.smallNote(20, 10), null, 'exactly on the lines is not small');
  assert.equal(A.smallNote(19, 12), 'Small area: 19 people');
  assert.equal(A.smallNote(14, 6), 'Small area: 14 people in 6 households');
  assert.equal(A.smallNote(45, 9), 'Small area: 45 people in 9 households', 'households alone can trigger it');
  assert.equal(A.smallNote(1, 1), 'Small area: 1 person in 1 household', 'singular forms');
  assert.equal(A.smallNote(3, null), 'Small area: 3 people', 'no household count: people only');
  assert.equal(A.smallNote(0, 0), null, 'no registered people is not a small area, it is empty');
  assert.equal(A.smallNote(null, null), null);
  assert.equal(A.smallNote(30, 15, {min_n: 50, min_households: 20}), 'Small area: 30 people in 15 households', 'the lines come from meta.small_area');
  assert.deepEqual(A.SMALL_AREA, {min_n: 20, min_households: 10}, 'the default when an older server sends none');
});

test('colors: every area with registered people is colored, small ones included; only an empty area gets the plain pale bin', () => {
  const p = payload();
  for (const layer of ['share', 'measured'] as const) {
    const bins = A.binsFor(p, 'ovr', layer, meta);
    for (const i of [0, 1, 2, 3, 4]) assert.ok(bins[i] < 5, `${layer}: ${ids[i]} (${n[i]} people) has a class color, bin ${bins[i]}`);
    assert.equal(bins[5], A.BIN_NONE, `${layer}: no registered people`);
    assert.equal(bins[ids.length], A.BIN_PENDING);
  }
  const lean = A.binsFor(p, 'lean', 'share', meta);
  assert.ok([0, 1, 2, 3, 4].every(i => lean[i] < 5), 'registration colors too'); assert.equal(lean[5], A.BIN_NONE);
  assert.ok(A.BIN_NONE >= 5 && A.BIN_PENDING >= 5 && A.BIN_NONE !== A.BIN_PENDING, 'sentinels sit above every class');
});

test('area view: a one-person area has its values, an empty area has none and says so', () => {
  const p = payload();
  const one = A.areaView(p, 1, 'ovr');
  assert.equal(one.empty, false); assert.equal(one.n, 1); assert.equal(one.households, 1);
  assert.equal(one.rows.length, 4); assert.ok(one.share && one.lean && one.mix, 'share, lean and registration mix are all there');
  assert.ok(Number.isFinite(one.rows[0].lo) && Number.isFinite(one.rows[0].hi) && one.rows[0].lo <= one.rows[0].hi);
  const empty = A.areaView(p, 5, 'ovr');
  assert.equal(empty.empty, true); assert.equal(empty.n, 0); assert.equal(empty.households, 0);
  assert.deepEqual(empty.rows, []); assert.equal(empty.share, null); assert.equal(empty.lean, null); assert.equal(empty.mix, null); assert.equal(empty.measured, null);
  assert.equal(A.smallNote(empty.n, empty.households), null, 'an empty area carries the "No registered people" caption, not a small-area note');
  assert.equal(A.smallNote(A.areaView(p, 4, 'ovr').n, A.areaView(p, 4, 'ovr').households), 'Small area: 14 people in 6 households');
});

test('district summary: weighted by every area that has people, the small ones too; an empty area adds nothing', () => {
  const s = A.districtSummary(payload());
  assert.equal(s.people, 120 + 1 + 19 + 20 + 14);
  const p = payload();
  const byHand = [0, 1, 2, 3, 4].reduce((sum, i) => sum + n[i] * p.groups.ovr.mean[i]!, 0) / s.people;
  assert.ok(Math.abs(s.ovr - byHand) < 1e-9, `ovr ${s.ovr} vs ${byHand}`);
});

test('registration mix: every party code is its own share, D R U first, the rest by people, and the shares add up to 1', () => {
  const mix = A.partyShares({D: 40, R: 30, U: 20, W: 5, G: 3, T: 1, '': 1})!;
  assert.deepEqual(mix.map((s: {code: string}) => s.code), ['D', 'R', 'U', 'W', 'G', '', 'T'], 'a tie is listed in code order, the blank code first');
  assert.ok(Math.abs(mix.reduce((a: number, s: {share: number}) => a + s.share, 0) - 1) < 1e-12);
  assert.equal(mix[0].share, 0.4); assert.equal(mix[3].share, 0.05);
  const few = A.partyShares({R: 3, W: 1, G: 0})!;
  assert.deepEqual(few.map((s: {code: string}) => s.code), ['D', 'R', 'U', 'W'], 'D, R and U are always listed; a code nobody holds is not');
  assert.equal(few[0].share, 0); assert.equal(few[1].share, 0.75);
  assert.equal(A.partyShares({}), null); assert.equal(A.partyShares({D: 0}), null);
});

test('area view: per-code counts replace the grouped mix; without them the grouped D R U Other mix stays', () => {
  const p = payload();
  const grouped = A.areaView(p, 0, 'ovr').mix!;
  assert.deepEqual(grouped.map((s: {code: string}) => s.code), ['D', 'R', 'U', 'O']);
  assert.ok(Math.abs(grouped.reduce((a: number, s: {share: number}) => a + s.share, 0) - 1) < 1e-12, 'the grouped shares add up to 1 too');
  const own = A.areaView(p, 0, 'ovr', {D: 48, R: 36, U: 24, W: 8, T: 4}).mix!;
  assert.deepEqual(own.map((s: {code: string}) => s.code), ['D', 'R', 'U', 'W', 'T']);
  assert.equal(own[0].share, 0.4); assert.ok(Math.abs(own.reduce((a: number, s: {share: number}) => a + s.share, 0) - 1) < 1e-12);
  assert.equal(A.areaView(p, 5, 'ovr', {D: 1}).mix, null, 'an area with no registered people has no mix');
  assert.deepEqual(A.districtSummary(p).mix.map((s: {code: string}) => s.code), ['D', 'R', 'U', 'O']);
});
