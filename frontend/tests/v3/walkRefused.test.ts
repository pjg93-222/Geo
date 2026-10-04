// Refused marks and the refusal note kept across a reload, in this tab's sessionStorage. Run with: node --test tests/v3/walkRefused.test.ts
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {REFUSED_KEY, readRefused, refusedStore, tabStorage, writeRefused} from '../../src/v3/lib/walkRefused.ts';
import type {RefusedStorage} from '../../src/v3/lib/walkRefused.ts';
import {listIdentity, marksWithRefused, refusalHeadline} from '../../src/v3/lib/walk.ts';
import type {WalkCode, WalkListPayload} from '../../src/v3/api/walkTypes.ts';

const storage = () => {
  const data = new Map<string, string>(); let writes = 0;
  const s: RefusedStorage & {data: Map<string, string>; writes: () => number} = {
    data, writes: () => writes,
    getItem: k => data.get(k) ?? null, setItem: (k, v) => { writes++; data.set(k, v); }, removeItem: k => { writes++; data.delete(k); },
  };
  return s;
};
const throwing = (): RefusedStorage => ({
  getItem: () => { throw new Error('SecurityError'); }, setItem: () => { throw new Error('QuotaExceededError'); }, removeItem: () => { throw new Error('SecurityError'); },
});
const visits = (...pairs: [string, WalkCode | null][]) => new Map(pairs.map(([id, code]) => [id, {code, on: '2026-10-04'}]));
const KEYED = listIdentity('k1'), QUERY = listIdentity(null, {streets: '7:both', precinct: '02012'});
const DOORS = {a: '4102 N Cedar Heights Dr', b: '4110 N Cedar Heights Dr', c: '12 Pine St'};
const stored = (s: ReturnType<typeof storage>) => JSON.parse(s.data.get(REFUSED_KEY) ?? 'null');
// one voter per door, enough for marksWithRefused
const listOf = (ids: string[], key: string | null = 'k1'): Pick<WalkListPayload, 'runs' | 'list'> => ({
  list: {key, title: 'Saturday', kind: 'standard', created_at: null},
  runs: [{stop: 1, street_id: 7, street: 'N Cedar Heights Dr', side: 'even', precinct: '', from_house: '1', to_house: '9', walk_m: 1, lr: 1, path: [], approach: [],
    doors: ids.map((person_id, i) => ({seq: i + 1, house: String(i + 1), unit: '', placed_by: 'geocode', lon: 0, lat: 0,
      voters: [{person_id, name: 'X', phone: '', pri: {voted: 0, eligible: 0}, gen: {voted: 0, eligible: 0}, abs: {voted: 0, eligible: 0}, party: '', age: null, gender: '', reg_date: '', last: null}]}))}],
});

test('round trip: the marks by list and voter and the note come back from a new load of the page, apart from each other', () => {
  const s = storage();
  const first = refusedStore(s);
  first.hold(KEYED, 'Saturday', visits(['a', 'S'], ['b', null]), DOORS, 'Some of those voters are not on this list.');
  first.hold(QUERY, 'Cedar Heights Dr', visits(['c', 'NH']), DOORS, 'This page is out of date. Reload it, then save the list again.', true);
  const again = refusedStore(s);   // the page reloaded: the module reads the tab's storage
  assert.deepEqual([...again.marks].map(([l, m]) => [l, [...m]]), [[KEYED, [['a', 'S'], ['b', null]]], [QUERY, [['c', 'NH']]]], 'list identity, person id and code, a clearing kept as null');
  assert.deepEqual(again.note, first.note);
  assert.equal(refusalHeadline(again.note), '3 results were not saved:', 'the note keeps whether the page refused some itself');
  const shown = marksWithRefused(listOf(['a', 'b', 'z']), again.marks, KEYED);
  assert.deepEqual(shown.results, {a: 'S', b: null}); assert.deepEqual([...shown.unsaved], ['a', 'b'], 'the flags come back when their list is shown');
  // one key, the two parts apart, and nothing beyond what the screen shows again
  assert.deepEqual([...s.data.keys()], [REFUSED_KEY]); assert.equal(REFUSED_KEY, 'terrier.walk.refused.v1');
  const raw = stored(s);
  assert.deepEqual(Object.keys(raw), ['marks', 'note']);
  assert.deepEqual(raw.marks, {[KEYED]: {a: 'S', b: null}, [QUERY]: {c: 'NH'}});
  assert.deepEqual(raw.note, {lists: [{title: 'Saturday', doors: {a: '4102 N Cedar Heights Dr (S)', b: '4110 N Cedar Heights Dr (cleared)'}}, {title: 'Cedar Heights Dr', doors: {c: '12 Pine St (NH)'}}],
    reasons: ['Some of those voters are not on this list.', 'This page is out of date. Reload it, then save the list again.'], local: true});
});

test('malformed data is ignored and removed; the well-formed parts are kept', () => {
  for (const bad of ['broken', '[]', 'null', '42', '"text"']) {
    const s = storage(); s.data.set(REFUSED_KEY, bad);
    const st = readRefused(s);
    assert.equal(st.marks.size, 0); assert.equal(st.note, null);
    assert.equal(s.data.has(REFUSED_KEY), false, `${bad} is removed`);
  }
  const s = storage();
  s.data.set(REFUSED_KEY, JSON.stringify({
    marks: {[KEYED]: {a: 'S', b: 'X', '': 'U', c: null, d: 3}, 'other:1': {a: 'S'}, [QUERY]: 'S', 'key:empty': {}},
    note: {lists: [{title: 'Saturday', doors: {a: '4102 N Cedar Heights Dr (S)', b: 7}}, {title: '', doors: {c: 'x'}}, 'nope'], reasons: ['why', 3]},
    extra: 'dropped',
  }));
  const st = readRefused(s);
  assert.deepEqual([...st.marks].map(([l, m]) => [l, [...m]]), [[KEYED, [['a', 'S'], ['c', null]]]], 'only real codes (or null) under key:/query: identities');
  assert.deepEqual(st.note, {lists: [{title: 'Saturday', doors: {a: '4102 N Cedar Heights Dr (S)'}}], reasons: ['why']});
  assert.deepEqual(stored(s), {marks: {[KEYED]: {a: 'S', c: null}}, note: {lists: [{title: 'Saturday', doors: {a: '4102 N Cedar Heights Dr (S)'}}], reasons: ['why']}}, 'rewritten without the malformed parts');
  const n = storage(); n.data.set(REFUSED_KEY, JSON.stringify({marks: {[KEYED]: {a: 'U'}}, note: {lists: 'x', reasons: []}}));
  assert.equal(readRefused(n).note, null, 'a malformed note is dropped, the marks stay');
  assert.deepEqual(stored(n), {marks: {[KEYED]: {a: 'U'}}, note: null});
  const clean = storage(); writeRefused({marks: new Map([[KEYED, new Map<string, WalkCode | null>([['a', 'S']])]]), note: null}, clean);
  const before = clean.writes(); readRefused(clean);
  assert.equal(clean.writes(), before, 'well-formed data is read without writing');
});

test('dismissing the note keeps the marks, flagged, and stops asking on close', () => {
  const s = storage(), store = refusedStore(s);
  store.hold(KEYED, 'Saturday', visits(['a', 'O']), DOORS, 'why');
  store.dismiss();
  assert.equal(store.note, null);
  const again = refusedStore(s);
  assert.equal(again.note, null, 'the note stays dismissed after a reload');
  assert.deepEqual([...again.marks.get(KEYED) ?? []], [['a', 'O']], 'the mark is never erased by dismissing');
  assert.deepEqual([...marksWithRefused(listOf(['a']), again.marks, KEYED).unsaved], ['a']);
  assert.deepEqual(stored(s), {marks: {[KEYED]: {a: 'O'}}, note: null});
  // a later refusal starts a new note, with the earlier marks still there
  again.hold(KEYED, 'Saturday', visits(['b', 'U']), DOORS, 'why');
  assert.deepEqual(Object.keys(refusedStore(s).note?.lists[0].doors ?? {}), ['b']);
  assert.deepEqual([...refusedStore(s).marks.get(KEYED) ?? []], [['a', 'O'], ['b', 'U']]);
});

test('a mark saved since is cleared from storage too; nothing left removes the key', () => {
  const s = storage(), store = refusedStore(s);
  store.hold(KEYED, 'Saturday', visits(['a', 'S'], ['b', 'U']), DOORS, 'why');
  store.hold(QUERY, 'Cedar', visits(['a', 'NH']), DOORS, 'why');
  store.clear([KEYED, QUERY], new Map([['a', 1]]).keys());   // an iterator: read once, applied to every identity of the list
  assert.deepEqual(stored(s).marks, {[KEYED]: {b: 'U'}});
  store.clear([KEYED], ['b']);store.dismiss();
  assert.equal(s.data.has(REFUSED_KEY), false, 'no marks and no note: the key is gone');
  const writes = s.writes(); store.clear([KEYED], ['b']); store.clear([KEYED], []);
  assert.equal(s.writes(), writes, 'a save that clears nothing writes nothing');
});

test('a list saved: its query bucket moves under the key and is removed, merged with marks already there (the query mark wins); storage is rewritten', () => {
  const s = storage(), store = refusedStore(s);
  store.hold(QUERY, 'Saturday', visits(['a', 'S'], ['b', 'NH']), DOORS, 'why');
  store.hold(KEYED, 'Saturday', visits(['b', 'U'], ['c', 'O']), DOORS, 'why');   // already under the key: b collides, c does not
  const writes = s.writes();
  store.adopt(listIdentity(null, {}), KEYED, null); store.adopt(KEYED, KEYED, null); store.adopt(QUERY, QUERY, new Map());
  assert.equal(s.writes(), writes, 'nothing to move and nothing renamed: nothing written');
  store.adopt(QUERY, KEYED, null);
  assert.deepEqual(stored(s).marks, {[KEYED]: {b: 'NH', c: 'O', a: 'S'}}, 'the query bucket is gone; b takes the newer NH from the query');
  const again = refusedStore(s);   // a reload opens the list by its key: the marks are there, flagged
  const shown = marksWithRefused(listOf(['a', 'b', 'c']), again.marks, KEYED);
  assert.deepEqual(shown.results, {a: 'S', b: 'NH', c: 'O'}); assert.deepEqual([...shown.unsaved].sort(), ['a', 'b', 'c']);
  assert.equal(marksWithRefused(listOf(['a']), again.marks, QUERY).unsaved.size, 0, 'the same streets built again no longer show them');
  assert.deepEqual(again.note, store.note, 'the note is by list title: unchanged');
});

test('a list saved under new person ids: the query marks are renamed (movedIds) and then moved under the key, in one write', () => {
  const s = storage(), store = refusedStore(s);
  store.hold(QUERY, 'Saturday', visits(['a', 'S'], ['b', 'NH'], ['gone', 'U']), DOORS, 'why');
  store.hold(KEYED, 'Saturday', visits(['ra', 'O']), DOORS, 'why');   // the saved list's own id for a: the renamed query mark wins
  const writes = s.writes();
  store.adopt(QUERY, KEYED, new Map([['a', 'ra'], ['b', 'rb']]));
  assert.equal(s.writes(), writes + 1, 'one write');
  assert.deepEqual(stored(s).marks, {[KEYED]: {ra: 'S', rb: 'NH', gone: 'U'}}, 'renamed first, then merged: no mark is left under an old id, a voter not found keeps hers');
  const again = refusedStore(s);
  assert.deepEqual(marksWithRefused(listOf(['ra', 'rb']), again.marks, KEYED).results, {ra: 'S', rb: 'NH'});
  assert.deepEqual(Object.keys(again.note?.lists[0].doors ?? {}), ['a', 'b', 'gone', 'ra'], 'the note names doors, so it is not re-keyed');
  // a list saved without a query bucket still renames marks already under the key
  const k = storage(), only = refusedStore(k);
  only.hold(KEYED, 'Saturday', visits(['a', 'S']), DOORS, 'why');
  only.adopt(QUERY, KEYED, new Map([['a', 'ra']]));
  assert.deepEqual(stored(k).marks, {[KEYED]: {ra: 'S'}});
});

test('storage that throws, or is missing, is tolerated: the page keeps working from memory', () => {
  const store = refusedStore(throwing());
  assert.equal(store.marks.size, 0); assert.equal(store.note, null);
  const note = store.hold(KEYED, 'Saturday', visits(['a', 'S']), DOORS, 'why');
  assert.equal(note, store.note); assert.deepEqual([...store.marks.get(KEYED) ?? []], [['a', 'S']]);
  store.adopt(QUERY, KEYED, new Map([['a', 'ra']])); store.clear([KEYED], ['x']); store.dismiss();
  assert.deepEqual([...store.marks.get(KEYED) ?? []], [['ra', 'S']]); assert.equal(store.note, null);
  assert.equal(writeRefused({marks: store.marks, note: null}, throwing()), false);
  assert.equal(readRefused(throwing()).marks.size, 0);
  const full = storage(); full.data.set(REFUSED_KEY, 'broken'); full.removeItem = () => { throw new Error('SecurityError'); };
  assert.equal(readRefused(full).note, null, 'a malformed value that cannot be removed is still ignored');
  const none = refusedStore(null); none.hold(KEYED, 'Saturday', visits(['a', 'S']), DOORS, 'why'); none.dismiss();
  assert.equal(none.marks.size, 1);
  // the property itself throws where the browser blocks site data
  const own = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage');
  Object.defineProperty(globalThis, 'sessionStorage', {configurable: true, get() { throw new Error('SecurityError'); }});
  try { assert.equal(tabStorage(), null); assert.equal(refusedStore().note, null); }
  finally { if (own) Object.defineProperty(globalThis, 'sessionStorage', own); else delete (globalThis as {sessionStorage?: unknown}).sessionStorage; }
});

test('privacy: this tab\'s sessionStorage only, never localStorage or the address', () => {
  const lib = readFileSync(path.join(import.meta.dirname, '../../src/v3/lib/walkRefused.ts'), 'utf8');
  const code = lib.split('\n').filter(l => !l.trim().startsWith('//')).join('\n');
  assert.ok(/sessionStorage/.test(code) && !/localStorage|location|history\.|replaceQuery|URLSearchParams/.test(code));
});
