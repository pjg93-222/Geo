// CSV export guard of lib/csv.ts (same rule as csv_value in server/research/terrier_contacts.py).
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {csvText, csvValue} from '../../src/v3/lib/csv.ts';

test('a leading = + - @, tab, carriage return or line feed gets an apostrophe', () => {
  for (const value of ['=1+1', '+1', '-1', '@SUM(A1)', '\t=1', '\tplain', '\rplain', '\nplain'])
    assert.equal(csvValue(value), "'" + value, JSON.stringify(value));
});

test('a formula behind leading spaces, invisible marks or control characters gets an apostrophe', () => {
  const prefixes = [' ', '   ', '\u00a0', '\u200b', '\u200d', '\u200f', '\u2060', '\ufeff', '\u0000', '\u001f', '\u007f', '\u0085', '\u2028', '\u3000',
    ' \t', '\ufeff\u200b \u00a0'];
  for (const prefix of prefixes) {
    const value = prefix + '=1+1';
    assert.equal(csvValue(value), "'" + value, JSON.stringify(value));
  }
});

test('other text is unchanged; null and undefined become empty', () => {
  for (const value of ['Jane Doe', 'a=b', 'x =1', '12.5%', 'Not scored', '  name', '\u00a0Main St', '\u200bname', ''])
    assert.equal(csvValue(value), value, JSON.stringify(value));
  assert.equal(csvValue(null), '');
  assert.equal(csvValue(undefined), '');
});

test('csvText quotes and guards every cell and joins rows with CRLF', () => {
  const rows = [['Field', 'Weight'], ['=HYPERLINK("https://example.invalid","x")', '12.5%'], [' @SUM(1)', 'Not scored']];
  assert.equal(csvText(rows), "\"Field\",\"Weight\"\r\n\"'=HYPERLINK(\"\"https://example.invalid\"\",\"\"x\"\")\",\"12.5%\"\r\n\"' @SUM(1)\",\"Not scored\"");
});

test('ordinary rows serialize exactly as the earlier inline criteria export did', () => {
  const inline = (rows: string[][]) => rows.map(row=>row.map(value=>`"${value.replaceAll('"','""')}"`).join(',')).join('\r\n');
  const rows = [
    ['Field', 'Group', 'Input', 'Weight within index', 'Included in overall standing'],
    ['sci_charitable_donor', 'Sphere of Influence', 'Yes / No', '11.25%', 'Yes'],
    ['svi_party_registration', 'Political views', 'Registration category', 'Not scored', 'No'],
    ['say "hi", then go', 'a,b', 'two\r\nlines', '', '0%'],
  ];
  assert.equal(csvText(rows), inline(rows));
  assert.equal(csvText([['a "b"', 'c,d'], ['', 'e']]), '"a ""b""","c,d"\r\n"","e"');
  assert.ok(!csvText(rows).endsWith('\n'), 'no trailing line break');
});

// Full-width triggers and bidi/format prefixes. FULL_WIDTH, BIDI_FORMAT, SKIPPED and TRIGGERS are copied verbatim into
// server/research/test_terrier_contacts_csv.py, so both guards are pinned to the same code points.
const FULL_WIDTH = ['\uff1d', '\uff0b', '\uff0d', '\uff20'];
const BIDI_FORMAT = ['\u202a', '\u202b', '\u202c', '\u202d', '\u202e', '\u2066', '\u2067', '\u2068', '\u2069', '\u00ad', '\u061c', '\u180e'];
const SKIPPED = [[0x0000, 0x0020], [0x007f, 0x00a0], [0x00ad, 0x00ad], [0x061c, 0x061c], [0x1680, 0x1680], [0x180e, 0x180e],
  [0x2000, 0x200f], [0x2028, 0x202f], [0x205f, 0x2060], [0x2066, 0x2069], [0x3000, 0x3000], [0xfeff, 0xfeff]];
const TRIGGERS = [0x002b, 0x002d, 0x003d, 0x0040, 0xff0b, 0xff0d, 0xff1d, 0xff20];

test('a full-width = + - @ gets an apostrophe, first or behind any skipped prefix', () => {
  for (const trigger of FULL_WIDTH)
    for (const prefix of ['', ' ', '\u00a0', '\u200b', '\ufeff', '\t', '\u3000', '\u202e', '\u2066\u00ad'])
      for (const value of [prefix + trigger + '1+1', prefix + trigger + 'HYPERLINK("https://example.invalid","x")']) {
        assert.equal(csvValue(value), "'" + value, JSON.stringify(value));
      }
});

test('a formula behind bidi controls, a soft hyphen, the Arabic letter mark or the Mongolian vowel separator gets an apostrophe', () => {
  for (const prefix of [...BIDI_FORMAT, '\u202e\u2066', '\u202b \u200f', '\u00ad\u00ad\u00a0'])
    for (const trigger of ['=', '+', '-', '@', ...FULL_WIDTH]) {
      const value = prefix + trigger + '1+1';
      assert.equal(csvValue(value), "'" + value, JSON.stringify(value));
    }
});

test('bidi or format characters before ordinary text, and full-width triggers later in the text, are unchanged', () => {
  const values = [...BIDI_FORMAT.map(c => c + 'Jane Doe'), '\u202eenaJ', '\u2066Main St\u2069', 'a\uff1db', 'x \uff0b1', '\uff03 5', '\uff11\uff0b\uff11',
    '\u00ad', '\u202e', 'Jane\uff20example.com'];
  for (const value of values) assert.equal(csvValue(value), value, JSON.stringify(value));
  assert.equal(csvText([['\u202e=1', '\uff1d1', 'plain']]), "\"'\u202e=1\",\"'\uff1d1\",\"plain\"");
});

test('the guard skips exactly the SKIPPED code points and fires on exactly the TRIGGERS (same list as the Python test)', () => {
  const inRanges = (cp: number, ranges: number[][]) => ranges.some(([a, b]) => a <= cp && cp <= b);
  const firstOnly = [0x09, 0x0a, 0x0d];
  for (let cp = 0; cp <= 0x10ffff; cp++) {
    if (cp >= 0xd800 && cp <= 0xdfff) continue;
    const c = String.fromCodePoint(cp);
    const triggers = TRIGGERS.includes(cp) || firstOnly.includes(cp);
    if ((csvValue(c + '1') !== c + '1') !== triggers) assert.fail(`U+${cp.toString(16)} as the first character`);
    if ((csvValue(c + '=1') !== c + '=1') !== (triggers || inRanges(cp, SKIPPED))) assert.fail(`U+${cp.toString(16)} before =`);
  }
});
