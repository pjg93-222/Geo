// Display rules of FIDELITY_SPEC section 4. Run with: node --test tests/v3/   (Node 24 strips the types itself)
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {
  RANGE,binOf,blockLabel,districtHref,districtOptions,leanBand,leanBandByCode,leanCellPos,leanLinearPos,likelyRange,likelyShare,ordinal,
  precinctLabel,rateText,round,seconds,shareText,signed,titleName,tractLabel,zipLabel
} from '../../src/v3/lib/format.ts';

test('display rounding: half goes away from zero (framework_config display.rounding_rule)', () => {
  const cases: [number, number][] = [[0.5, 1], [1.5, 2], [2.5, 3], [49.5, 50], [99.5, 100], [-0.5, -1], [-24.5, -25], [-75.5, -76], [17.5, 18], [-17.5, -18], [0.49, 0], [-0.49, 0]];
  for (const [x, want] of cases) assert.equal(round(x), want, `round(${x})`);
  assert.equal(Object.is(round(-0.2), -0), false, 'never negative zero');
});

test('ordinals: st nd rd th with 11th 12th 13th', () => {
  const want: Record<number, string> = {0: '0th', 1: '1st', 2: '2nd', 3: '3rd', 4: '4th', 11: '11th', 12: '12th', 13: '13th', 21: '21st', 22: '22nd', 23: '23rd', 84: '84th', 91: '91st', 93: '93rd', 100: '100th', 101: '101st', 111: '111th'};
  for (const [n, s] of Object.entries(want)) assert.equal(ordinal(Number(n)), s);
  assert.equal(ordinal(83.5), '84th', 'ordinal of the rounded standing');
});

test('lean bands: |lean| < 25 Center, 25..75 Lean, >= 75 Strong; D above zero, R below', () => {
  assert.equal(leanBand(0).label, 'Center'); assert.equal(leanBand(24.99).label, 'Center'); assert.equal(leanBand(-24.99).label, 'Center');
  assert.equal(leanBand(25).label, 'Lean D'); assert.equal(leanBand(-25).label, 'Lean R');
  assert.equal(leanBand(74.99).label, 'Lean D'); assert.equal(leanBand(75).label, 'Strong D'); assert.equal(leanBand(-75).label, 'Strong R');
  assert.equal(leanBand(12).label, 'Center', '+12 is Center, not Lean D (render defect D2)');
  assert.equal(leanBand(9).label, 'Center', '+9 is Center (render defect D2)');
  assert.deepEqual([-2, -1, 0, 1, 2].map(c => leanBandByCode(c).label), ['Strong R', 'Lean R', 'Center', 'Lean D', 'Strong D']);
});

test('signed values use U+2212 for negatives and nothing for zero', () => {
  assert.equal(signed(18), '+18'); assert.equal(signed(-31), '−31'); assert.equal(signed(0), '0'); assert.equal(signed(0.4), '0'); assert.equal(signed(-0.4), '0');
  assert.equal(signed(-0.5), '−1'); assert.equal(signed(0.5), '+1');
});

test('shares: one decimal, ".0" dropped only for the district rate', () => {
  assert.equal(shareText(0.164), '16.4'); assert.equal(shareText(0.1), '10.0'); assert.equal(rateText(0.1), '10'); assert.equal(rateText(0.0989), '9.9');
});

test('likely range text uses an en dash and display rounding', () => {
  assert.equal(RANGE(63.4, 77.6), '63–78'); assert.equal(RANGE(12.5, 20.5), '13–21');
});

test('bin edges: a value equal to a break goes up', () => {
  const share = [0.05, 0.08, 0.12, 0.18];
  assert.equal(binOf(0.0499, share), 0); assert.equal(binOf(0.05, share), 1); assert.equal(binOf(0.0799, share), 1); assert.equal(binOf(0.08, share), 2);
  assert.equal(binOf(0.12, share), 3); assert.equal(binOf(0.18, share), 4); assert.equal(binOf(0.9, share), 4);
  const hh = [20, 40, 60, 80];
  assert.equal(binOf(19.99, hh), 0); assert.equal(binOf(20, hh), 1); assert.equal(binOf(80, hh), 4); assert.equal(binOf(100, hh), 4);
});

test('area likely ranges are clipped normal 90% intervals', () => {
  const [lo, hi] = likelyRange(50, 10); assert.ok(Math.abs(lo - 33.551) < 1e-3 && Math.abs(hi - 66.449) < 1e-3);
  const [l2, h2] = likelyRange(2, 5); assert.equal(l2, 0); assert.ok(Math.abs(h2 - 10.2245) < 1e-3);
  const [a, b] = likelyShare(0.164, 0.02); assert.ok(a < 0.164 && b > 0.164 && a >= 0 && b <= 1);
  const [l3, h3] = likelyShare(0.01, 0.2); assert.equal(l3, 0); assert.ok(Math.abs(h3 - 0.3398) < 1e-3);
});

test('lean bar positions: linear 1:2:2:2:1 and piecewise cells', () => {
  assert.equal(leanLinearPos(-100), 0); assert.equal(leanLinearPos(0), 0.5); assert.equal(leanLinearPos(100), 1); assert.equal(leanLinearPos(500), 1);
  assert.equal(leanCellPos(-100), 0); assert.equal(leanCellPos(-75), 0.2); assert.equal(leanCellPos(0), 0.5); assert.equal(leanCellPos(75), 0.8); assert.equal(leanCellPos(100), 1);
  const c = leanCellPos(12); assert.ok(c > 0.4 && c < 0.6, '+12 sits inside the Center cell');
});

test('area ids: block, tract, precinct and ZIP labels', () => {
  assert.equal(blockLabel('080410030013017'), 'Block 3017');
  assert.equal(tractLabel('08041004102'), 'Tract 41.02'); assert.equal(tractLabel('08041000200'), 'Tract 2'); assert.equal(tractLabel('08041000700'), 'Tract 7');
  assert.equal(precinctLabel('5091421214'), 'Precinct 214'); assert.equal(zipLabel('80918'), 'ZIP 80918');
});

test('names: contacts store upper case; title case keeps Mc, JR and apostrophes', () => {
  assert.equal(titleName('DANA WHITFIELD'), 'Dana Whitfield'); assert.equal(titleName("MARY-ANN O'NEIL"), "Mary-Ann O'Neil");
  assert.equal(titleName('JOHN SMITH JR'), 'John Smith JR'); assert.equal(titleName('RYAN MCDONALD'), 'Ryan McDonald');
});

test('readout seconds: one decimal, minimum 0.1', () => {
  assert.equal(seconds(0), '0.1'); assert.equal(seconds(400), '0.4'); assert.equal(seconds(1449), '1.4'); assert.equal(seconds(1451), '1.5');
});

test('district selector: a district page is the same host at its own port', () => {
  assert.equal(districtHref({port: 8775}, {protocol: 'http:', hostname: '127.0.0.1'}), 'http://127.0.0.1:8775/');
  assert.equal(districtHref({port: 8777}, {protocol: 'https:', hostname: 'terrier.local'}), 'https://terrier.local:8777/');
});

test('district selector: a hosted server gives each district its own address, which wins over host and port', () => {
  assert.equal(districtHref({port: 8775, url: 'https://or05.example.org/'}, {protocol: 'https:', hostname: 'co05.example.org'}), 'https://or05.example.org/');
});

test('district selector: not-built districts are disabled and say so; the current one stays enabled', () => {
  const list = [
    {id: 'co-05', label: 'Colorado · District 05', ready: true},
    {id: 'or-05', label: 'Oregon · District 05', ready: false},
    {id: 'ct-01', label: 'Connecticut · District 01', ready: false},
  ];
  assert.deepEqual(districtOptions(list, 'co-05'), [
    {id: 'co-05', label: 'Colorado · District 05', disabled: false},
    {id: 'or-05', label: 'Oregon · District 05 (not built yet)', disabled: true},
    {id: 'ct-01', label: 'Connecticut · District 01 (not built yet)', disabled: true},
  ]);
  const onOregon = districtOptions(list, 'or-05');
  assert.deepEqual(onOregon[1], {id: 'or-05', label: 'Oregon · District 05', disabled: false}, 'the served district is never disabled, even without a snapshot');
  assert.equal(onOregon[2].disabled, true);
});
