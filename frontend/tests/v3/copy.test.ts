// Neutral-copy gate: no advisory or ranking-intent wording in any string literal or JSX text of the v3 UI.
// The pattern is tools/field_labels.py ADVISORY_PATTERN (T17(d)). Strings are pulled out of the source with simple scanners.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readdirSync, readFileSync, statSync} from 'node:fs';
import path from 'node:path';

const ADVISORY = /\b(prospect|prospects|target|targets|targeting|best|recommend|recommended|priority|priorities|should|ideal|worth|likely to join|persuad|persuasion|convert|opportunity|high[- ]value|insufficient evidence)\b/i;
const root = path.resolve(import.meta.dirname, '../../src/v3');
const files: string[] = [];
(function walk(d: string) {
  for (const n of readdirSync(d)) {
    const p = path.join(d, n);
    if (statSync(p).isDirectory()) walk(p); else if (/\.(ts|tsx)$/.test(n)) files.push(p);
  }
})(root);

function strings(src: string): string[] {
  const noComments = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`\\])\/\/.*$/gm, '$1');
  const out: string[] = [];
  for (const m of noComments.matchAll(/'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)"|`((?:[^`\\]|\\.)*)`/g)) out.push(m[1] ?? m[2] ?? m[3] ?? '');
  for (const m of noComments.matchAll(/>([^<>{}=;()]+)</g)) out.push(m[1]);
  return out.map(s => s.trim()).filter(Boolean);
}

test('no advisory wording in v3 UI strings', () => {
  assert.ok(files.length > 10, 'source files found');
  const bad: string[] = [];
  for (const f of files) for (const s of strings(readFileSync(f, 'utf8'))) if (ADVISORY.test(s)) bad.push(`${path.relative(root, f)}: ${s.slice(0, 80)}`);
  assert.deepEqual(bad, []);
});

test('the required static strings are present verbatim', () => {
  const all = files.map(f => readFileSync(f, 'utf8')).join('\n');
  for (const s of ['Terrier', 'Search areas or people', 'Not part of overall standing',
    'Zoom out for block, precinct and tract values.', 'Balanced by registration', 'Next page ready', 'Household mean standing', 'Field evidence', 'Area context', 'Registration mix',
    'Standing across neighboring', 'Illustrative district indices', 'Share of people in the', 'View profile'])
    assert.ok(all.includes(s), `missing: ${s}`);
});

test('no skew copy', () => {
  const all = files.map(f => readFileSync(f, 'utf8')).join('\n').toLowerCase();
  for (const w of ['skew', 'republican district', 'undersampled', 'not representative']) assert.ok(!all.includes(w), `found: ${w}`);
});

test('test-dummy labeling and no-floor legend',()=>{
 const header=readFileSync(path.join(root,'components/Header.tsx'),'utf8');
 assert.ok(header.includes('Test-dummy preview'));
 const legend=readFileSync(path.join(root,'components/MapParts.tsx'),'utf8');
 assert.ok(!legend.includes('under 20 people'));
});
