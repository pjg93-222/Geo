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
