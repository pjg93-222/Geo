// Map overlay modes (Phase 4D): single layer, weighted blend and two-layer bivariate bins, legends, URL, and the header pieces that come from
// /api/v3/features. Run with: node --test tests/v3/overlay.test.ts   (Node 24 strips the types itself)
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {registerHooks} from 'node:module';
import type {AreasPayload, Density, Features, Meta} from '../../src/v3/types.ts';

// areas.ts imports its neighbours without a file extension, so node needs a resolve hook to load it straight from source.
registerHooks({resolve: (spec, ctx, next) => ctx.parentURL?.includes('/src/v3/') && /^\.{1,2}\//.test(spec) && !/\.[cm]?[jt]sx?$/.test(spec) ? next(spec + '.ts', ctx) : next(spec, ctx)});
const O = await import('../../src/v3/lib/overlay.ts');
const {BIN_PENDING, BIN_NONE, binsFor} = await import('../../src/v3/lib/areas.ts');

// ---- a synthetic area table: 23 blocks, two with no registered people, every score a permutation of 0..22 so standings never tie ----------------------
const N = 23, EMPTY_AT = [3, 17];
const ids = Array.from({length: N}, (_, i) => `b${String(i).padStart(2, '0')}`);
const status = ids.map((_, i) => (EMPTY_AT.includes(i) ? 'empty' : 'ok')) as ('ok' | 'empty')[];
const col = (g: (i: number) => number) => ids.map((_, i) => (status[i] === 'ok' ? g(i) : null));
const cell = (k: number) => ({
  mean: col(i => 20 + ((i * k) % N)), se: col(() => 2), measured: col(i => 10 + ((i * k) % N) * 3), pct: col(i => (100 * ((i * k) % N)) / (N - 1)),
  top10: col(i => ((i * k) % N) / 100), top10_se: col(() => 0.01), top10_pct: col(i => (100 * ((i * k) % N)) / (N - 1)),
});
const areas = (withSup = true): AreasPayload => ({
  schema: 'v3-areas-1', snapshot: 's', basis: 'registration', level: 'block', ids, labels: ids, status,
  n: col(() => 50), households: col(() => 20), parent: ids.map(() => null), precinct: ids.map(() => null), tract: ids.map(() => null), blocks: ids.map(() => null),
  groups: {
    ovr: cell(5), cua: cell(9), part: cell(7), soi: cell(13),
    lean: {mean: col(i => ((i * 5) % N) * 8 - 88), se: col(() => 3), measured: col(() => 90), share_d: col(i => 0.2 + (i % 5) / 10), share_r: col(() => 0.3), share_u: col(() => 0.2)},
    ...(withSup ? {sup: cell(11)} : {}),
  },
});
const meta = {share_breaks: [0.05, 0.08, 0.12, 0.18], measured_breaks: [20, 40, 60, 80]} as unknown as Meta;
// density.json: b02 and b10 have no population density (no land), b05 is missing from the file, voter density is null for the empty blocks
const density = (): Density => {
  const keep = ids.filter(id => id !== 'b05');
  return {
    schema: 'density-1', district: 'x', blocks_source: {file: 'blocks.zip', sha256: '0'}, join: {rate: 0.995},
    levels: {block: {ids: keep, land_km2: keep.map(() => 1), pop_density: keep.map((id, i) => (i === 2 || i === 9 ? null : 10 * (i + 1) + (i % 3))),
      voter_density: keep.map((id, i) => (EMPTY_AT.includes(ids.indexOf(id)) ? null : 5 * (i + 1) + (i % 4)))}},
  };
};
const single = (layer: string): import('../../src/v3/lib/overlay.ts').Overlay => ({mode: 'single', layer: layer as never, blend: {}, pair: ['cua', 'part']});
const counts = (bins: Uint8Array, upTo = 5) => Array.from({length: upTo}, (_, c) => Array.from(bins.subarray(0, N)).filter(b => b === c).length);

test('layer standings: payload pct for index layers, midrank for lean and density, nothing for areas with no registered people', () => {
  const a = areas();
  assert.deepEqual(O.layerPct(a, null, 'part'), a.groups.part.pct);
  assert.equal(O.layerPct(a, null, 'part')[3], null);
  const lean = O.layerPct(a, null, 'lean');
  assert.equal(lean[3], null); assert.equal(lean[17], null);
  assert.equal(Math.min(...lean.filter((x): x is number => x != null)), 100 * 0.5 / 21, 'lowest of 21 areas with people');
  const pop = O.layerPct(a, density(), 'pop_density');
  assert.equal(pop[5], null, 'an area density.json does not hold');
  assert.equal(pop[3], null, 'an area with no registered people');
  assert.equal(pop.filter(x => x == null).length, 5, 'empty (2), absent from the file (1), no land (2)');
  assert.deepEqual(O.midrank([5, 5, 1, null, 9]), [50, 50, 12.5, null, 87.5], 'ties share their midrank');
  assert.deepEqual(O.layerPct(a, null, 'voter_density'), a.ids.map(() => null), 'no density.json: nothing to show');
});

test('blend: the weighted mean of the standings, in fifths of areas', () => {
  const a = areas(), p = O.layerPct(a, null, 'part'), s = O.layerPct(a, null, 'sup');
  const score = O.blendScores(a, null, {part: 3, sup: 1});
  score.forEach((x, i) => {
    if (p[i] == null) return assert.equal(x, null);
    assert.ok(Math.abs(x! - (3 * p[i]! + s[i]!) / 4) < 1e-9, `area ${i}`);
  });
  assert.equal(new Set(score.filter(x => x != null)).size, 21, 'fixture: no ties');
  const r = O.overlayBins({mode: 'blend', layer: 'ovr', blend: {part: 3, sup: 1}, pair: ['cua', 'part']}, a, null, meta);
  const c = counts(r.bins);
  assert.equal(c.reduce((x, y) => x + y, 0), 21); assert.ok(Math.max(...c) - Math.min(...c) <= 1, `fifths ${c}`);
  assert.equal(r.bins[3], BIN_NONE); assert.equal(r.bins[N], BIN_PENDING);
  // weights are free numbers: 3:1 and 30:10 are the same blend
  assert.deepEqual(O.overlayBins({mode: 'blend', layer: 'ovr', blend: {part: 30, sup: 10}, pair: ['cua', 'part']}, a, null, meta).bins, r.bins);
  // a layer with no value for an area (no density.json here) leaves that area plain pale
  const none = O.overlayBins({mode: 'blend', layer: 'ovr', blend: {part: 1, pop_density: 1}, pair: ['cua', 'part']}, a, null, meta);
  assert.ok(Array.from(none.bins.subarray(0, N)).every(b => b === BIN_NONE));
});

test('two layers: thirds at 100/3 and 200/3, class = 3 * first + second; no value or an empty area stays plain pale', () => {
  assert.equal(O.biClass(10, 90), 2); assert.equal(O.biClass(90, 10), 6); assert.equal(O.biClass(0, 0), 0); assert.equal(O.biClass(100, 100), 8);
  assert.equal(O.biClass(100 / 3, 50), 4, 'a standing at the break goes up'); assert.equal(O.biClass(100 / 3 - 0.01, 200 / 3), 2);
  assert.equal(O.biClass(null, 5), null); assert.equal(O.biClass(5, null), null);
  const a = areas(), r = O.overlayBins({mode: 'bi', layer: 'ovr', blend: {}, pair: ['part', 'sup']}, a, null, meta);
  const p = O.layerPct(a, null, 'part'), s = O.layerPct(a, null, 'sup');
  ids.forEach((_, i) => assert.equal(r.bins[i], p[i] == null ? BIN_NONE : O.biClass(p[i], s[i])));
  assert.equal(r.bins[N], BIN_PENDING); assert.equal(r.palette.length, 9);
  // a density layer with no value for an area (b05 absent, b02 and b10 without land) leaves it plain pale
  const d = O.overlayBins({mode: 'bi', layer: 'ovr', blend: {}, pair: ['part', 'pop_density']}, a, density(), meta);
  assert.equal(d.bins[5], BIN_NONE); assert.equal(d.bins[3], BIN_NONE);
  assert.equal(d.bins[ids.indexOf('b10')], BIN_NONE, 'null population density');
  assert.ok(d.bins[0] < 9);
});

test('colorado default: one overall layer gives the same bins and palette as today', () => {
  const a = areas(false);
  for (const measured of [false, true]) {
    const r = O.overlayBins(single('ovr'), a, null, meta, measured);
    assert.deepEqual(r.bins, binsFor(a, 'ovr', measured ? 'measured' : 'share', meta));
    assert.deepEqual(r.palette, O.SHARE_RAMP); assert.equal(r.legend, null, 'the classic legend');
  }
  assert.equal(O.overlayBins(single('lean'), a, null, meta).palette, O.LEAN_RAMP);
  assert.deepEqual(O.overlayBins(single('lean'), a, null, meta).bins, binsFor(a, 'lean', 'share', meta));
  // a candidate layer the payload does not carry is plain pale, not a crash
  assert.ok(Array.from(O.overlayBins(single('sup'), a, null, meta).bins.subarray(0, N)).every(b => b === BIN_NONE));
  assert.deepEqual(O.overlayBins(single('sup'), areas(), null, meta).bins, binsFor(areas(), 'sup', 'share', meta));
});

test('density layers: fifths of the values, the breaks named in the legend', () => {
  const a = areas(), d = density();
  const r = O.overlayBins(single('pop_density'), a, d, meta);
  const v = Array.from(r.bins.subarray(0, N));
  assert.equal(v.filter(b => b === BIN_NONE).length, 5, 'empty (2) + absent (1) + no land (2)');
  const used = v.filter(b => b < 5), c = [0, 1, 2, 3, 4].map(k => used.filter(b => b === k).length);
  assert.ok(Math.max(...c) - Math.min(...c) <= 1, `fifths ${c}`);
  assert.equal(r.legend!.title, 'Population density'); assert.equal(r.legend!.sub, 'People per km² · fifths of areas');
  assert.equal(r.legend!.labels.length, 5); assert.equal(r.legend!.ramp.length, 5);
  assert.match(r.legend!.labels[0], /^Under [\d.]+k?$/); assert.match(r.legend!.labels[4], /^[\d.]+k?\+$/); assert.match(r.legend!.labels[2], /^[\d.]+k?–[\d.]+k?$/);
  const label = (x: number) => O.overlayBins(single('pop_density'), a, {...d, levels: {block: {...d.levels.block!, pop_density: d.levels.block!.ids.map(() => x)}}}, meta).legend!.labels[0];
  assert.deepEqual([8.44, 196.4, 1049, 1174, 3513, 12400].map(label), ['Under 8.4', 'Under 196', 'Under 1k', 'Under 1.2k', 'Under 3.5k', 'Under 12k']);
  assert.equal(O.overlayBins(single('voter_density'), a, d, meta).legend!.sub, 'Registered voters per km² · fifths of areas');
  assert.ok(Array.from(O.overlayBins(single('voter_density'), a, d, meta).bins.subarray(0, N)).every((b, i) => status[i] === 'ok' || b === BIN_NONE), 'voter density is plain pale where the area has no registered people');
  assert.deepEqual(O.fifths([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]), [3, 5, 7, 9]); assert.deepEqual(O.fifths([null]), []);
});

test('legends: one per mode, with plain text for every part', () => {
  const a = areas(), d = density();
  const blend = O.overlayBins({mode: 'blend', layer: 'ovr', blend: {part: 50, sup: 50}, pair: ['cua', 'part']}, a, d, meta).legend!;
  assert.equal(blend.title, 'Blend'); assert.equal(blend.sub, 'Participation 50%, Candidate support 50% · fifths of areas');
  assert.deepEqual(blend.labels, ['Lowest', 'Low', 'Middle', 'High', 'Highest']); assert.equal(blend.ramp.length, 5);
  assert.equal(O.overlayBins({mode: 'blend', layer: 'ovr', blend: {lean: 1, soi: 3}, pair: ['cua', 'part']}, a, d, meta).legend!.sub,
    'Sphere of Influence 75%, Partisan lean 25% · fifths of areas. Partisan lean: higher is more Democratic');
  assert.equal(O.overlayBins({mode: 'blend', layer: 'ovr', blend: {}, pair: ['cua', 'part']}, a, d, meta).legend!.sub, 'Choose at least one layer');
  const bi = O.overlayBins({mode: 'bi', layer: 'ovr', blend: {}, pair: ['part', 'voter_density']}, a, d, meta).legend!;
  assert.equal(bi.title, 'Participation and Voter density'); assert.deepEqual(bi.axes, ['Participation', 'Voter density']);
  assert.deepEqual(bi.labels, ['Low third', 'Middle third', 'High third']); assert.equal(bi.ramp.length, 9);
  assert.equal(O.overlayBins(single('cua'), a, d, meta).legend, null);
});

test('palettes: five or nine colors, and the sentinels never collide with a class', () => {
  assert.equal(O.SHARE_RAMP.length, 5); assert.equal(O.LEAN_RAMP.length, 5); assert.equal(O.BI_PALETTE.length, 9);
  assert.ok(BIN_NONE > O.BI_PALETTE.length - 1 && BIN_PENDING > O.BI_PALETTE.length - 1 && BIN_NONE !== BIN_PENDING);
  assert.ok(BIN_NONE <= 255 && BIN_PENDING <= 255, 'they live in a Uint8Array');
  const a = areas(), d = density();
  for (const r of [O.overlayBins({mode: 'bi', layer: 'ovr', blend: {}, pair: ['part', 'sup']}, a, d, meta), O.overlayBins(single('pop_density'), a, d, meta), O.overlayBins(single('ovr'), a, d, meta)]) {
    const classes = Array.from(r.bins).filter(b => b !== BIN_NONE && b !== BIN_PENDING);
    assert.ok(classes.every(b => b < r.palette.length), 'every class has a color');
  }
});

test('bivariate palette: the lowest cell is 3:1 from the other corners, nine distinct colors, and every step darkens along both axes', () => {
  const lum = O.BI_PALETTE.map(h => O.luminance(h)), ratio = (x: number, y: number) => (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  for (const corner of [2, 6, 8]) assert.ok(ratio(lum[0], lum[corner]) >= 3, `corner ${corner}: ${ratio(lum[0], lum[corner]).toFixed(2)}`);
  assert.equal(new Set(O.BI_PALETTE).size, 9);
  for (let first = 0; first < 3; first++) for (let second = 0; second < 2; second++) assert.ok(lum[3 * first + second] > lum[3 * first + second + 1], 'down the second layer');
  for (let first = 0; first < 2; first++) for (let second = 0; second < 3; second++) assert.ok(lum[3 * first + second] > lum[3 * (first + 1) + second], 'across the first layer');
  assert.equal(O.luminance(undefined), 1); assert.ok(O.luminance('#000000') < 0.001);
});

test('URL: the default overlay writes nothing, every mode round-trips, junk falls back', () => {
  const q = (o: object) => new URLSearchParams(Object.entries(O.overlayQuery(o as never)).filter(([, v]) => v !== null) as [string, string][]);
  assert.equal(q(O.DEFAULT_OVERLAY).toString(), ''); assert.deepEqual(O.readOverlay(new URLSearchParams('')), O.DEFAULT_OVERLAY);
  for (const o of [single('sup'), single('voter_density'), {mode: 'blend', layer: 'ovr', blend: {sup: 50, part: 25}, pair: ['cua', 'part']}, {mode: 'bi', layer: 'ovr', blend: O.DEFAULT_OVERLAY.blend, pair: ['sup', 'pop_density']}] as O.Overlay[]) {
    const back = O.readOverlay(q(o));
    assert.equal(back.mode, o.mode);
    if (o.mode === 'single') assert.equal(back.layer, o.layer);
    if (o.mode === 'blend') assert.deepEqual(back.blend, o.blend);
    if (o.mode === 'bi') assert.deepEqual(back.pair, o.pair);
  }
  assert.equal(q({mode: 'blend', layer: 'ovr', blend: {sup: 50, part: 25}, pair: ['cua', 'part']}).get('blend'), 'part:25,sup:50', 'stable order');
  const junk = O.readOverlay(new URLSearchParams('index=nope&mode=weird&blend=part:-3,x:5,sup:abc,soi:Infinity&a=zzz&b=zzz'));
  assert.deepEqual(junk, O.DEFAULT_OVERLAY);
});

test('offered layers: only what features lists, density only once density.json is loaded; the rest fall back', () => {
  const feat = (layers: string[], dens: boolean) => ({overlay: {layers, density: dens}}) as unknown as Features;
  const co = feat(['ovr', 'cua', 'part', 'soi', 'lean'], false), or = feat([...co.overlay.layers, 'sup', 'pop_density', 'voter_density'], true);
  assert.deepEqual(O.offeredLayers(null, false), O.BASE_LAYERS); assert.deepEqual(O.offeredLayers(co, false), O.BASE_LAYERS);
  assert.deepEqual(O.offeredLayers(or, false), [...O.BASE_LAYERS, 'sup'], 'density layers wait for the file');
  assert.deepEqual(O.offeredLayers(or, true), O.ALL_LAYERS);
  const wanted: O.Overlay = {mode: 'bi', layer: 'sup', blend: {sup: 50}, pair: ['sup', 'voter_density']};
  assert.deepEqual(O.usable(wanted, O.BASE_LAYERS), {mode: 'bi', layer: 'ovr', blend: O.DEFAULT_OVERLAY.blend, pair: ['cua', 'part']});
  assert.equal(O.usable(wanted, O.ALL_LAYERS).layer, 'sup'); assert.deepEqual(O.usable(wanted, O.ALL_LAYERS).pair, ['sup', 'voter_density']);
  assert.deepEqual(O.usable(O.DEFAULT_OVERLAY, O.BASE_LAYERS), O.DEFAULT_OVERLAY);
});

test('industry menu appears with two industries and not with one; the race badge names the candidate and the mode', () => {
  const one = {industries: [{id: 'cua', label: 'Credit Union Alignment', fields: []}]}, two = {industries: [...one.industries, {id: 'ins', label: 'Insurance Alignment', fields: []}]};
  assert.equal(O.industryChoices(one), null); assert.equal(O.industryChoices(null), null);
  assert.deepEqual(O.industryChoices(two), [{id: 'cua', label: 'Credit Union Alignment'}, {id: 'ins', label: 'Insurance Alignment'}]);
  assert.equal(O.raceBadge({name: 'Janelle Bynum', party: 'D', party_label: 'Democrat', race_mode: 'general'}), 'Bynum (D) · General election');
  assert.equal(O.raceBadge({name: 'Luke Bronin', party: 'D', party_label: 'Democrat', race_mode: 'primary'}), 'Bronin (D) · Primary election');
  assert.equal(O.raceBadge(null), null); assert.equal(O.raceBadge(undefined), null);
});

test('wording: no advisory word in any string the overlay shows (same pattern as tools/field_labels.ADVISORY_PATTERN)', () => {
  const ADVISORY = /\b(prospect|prospects|target|targets|targeting|best|recommend|recommended|priority|priorities|should|ideal|worth|likely to join|persuad|persuasion|convert|opportunity|high[- ]value|insufficient evidence)\b/i;
  const dir = new URL('../../src/v3/', import.meta.url);
  const strings: string[] = [...Object.values(O.LAYER_NAME)];
  for (const file of ['lib/overlay.ts', 'components/MapParts.tsx', 'components/Header.tsx', 'components/MapScreen.tsx']) {
    const text = readFileSync(new URL(file, dir), 'utf8').replace(/\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '').replace(/\be\.target\b/g, '');
    for (const m of text.matchAll(/'([^'\n]{3,})'|"([^"\n]{3,})"|`([^`\n]{3,})`|>([^<>{}\n]{3,})</g)) strings.push(m[1] ?? m[2] ?? m[3] ?? m[4]);
  }
  assert.ok(strings.length > 60, `scanned ${strings.length} strings`);
  assert.deepEqual(strings.filter(s => ADVISORY.test(s)), []);
});
