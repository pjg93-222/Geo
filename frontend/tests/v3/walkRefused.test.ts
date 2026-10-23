// Refused marks and the refusal note kept across a reload, in this tab's sessionStorage. Run with: node --test tests/v3/walkRefused.test.ts
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {REFUSED_KEY, readRefused, refusedStore, resetRefused, tabStorage, writeRefused} from '../../src/v3/lib/walkRefused.ts';
import type {RefusedStorage} from '../../src/v3/lib/walkRefused.ts';
import {flaggedOnList, listIdentity, marksWithRefused, refusalHeadline} from '../../src/v3/lib/walk.ts';
import {localJson, postJson} from '../../src/v3/api/client.ts';
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
  const doors = again.note?.lists[0].doors ?? {};
  assert.deepEqual(Object.keys(doors), ['gone', 'ra', 'rb'], 'the note\'s doors are renamed with the marks, so saving them later takes them out of it');
  assert.equal(doors.ra, '4102 N Cedar Heights Dr (S)', 'the renamed door replaces the one already under the new id, as the mark does');
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
  const read = (f: string) => readFileSync(path.join(import.meta.dirname, '../../src/v3/lib', f), 'utf8').split('\n').filter(l => !l.trim().startsWith('//')).join('\n');
  const code = read('walkRefused.ts') + read('walkRefusedReset.ts');
  assert.ok(/sessionStorage/.test(code) && !/localStorage|location|history\.|replaceQuery|URLSearchParams/.test(code));
});

// --- review findings: the note pruned on save, forgotten when the session ends, failed writes, the unload guard, odd person ids -----------

test('a saved mark leaves the note as well; a note with no door left goes (and with it the unload prompt); one write each', () => {
  const s = storage(), store = refusedStore(s);
  store.hold(KEYED, 'Saturday', visits(['a', 'S'], ['b', 'NH']), DOORS, 'why');
  store.hold(QUERY, 'Cedar', visits(['c', 'U']), DOORS, 'other');
  let writes = s.writes();
  store.clear([KEYED], ['a']);
  assert.equal(s.writes(), writes + 1, 'one write');
  assert.deepEqual(store.note?.lists.map(l => [l.title, Object.keys(l.doors)]), [['Saturday', ['b']], ['Cedar', ['c']]]);
  assert.equal(refusalHeadline(store.note), 'The server refused 2 results, so they were not saved. Record these doors again:', 'the count follows');
  store.clear([KEYED], ['b']);
  assert.deepEqual(store.note?.lists.map(l => l.title), ['Cedar'], 'a list with no door left goes');
  assert.deepEqual(store.note?.reasons, ['why', 'other'], 'the reasons stay while any door does');
  store.clear([QUERY], ['c']);
  assert.equal(store.note, null, 'every door saved: no note');
  assert.equal(s.data.has(REFUSED_KEY), false, 'no marks and no note: the key is gone');
  // a voter refused on two lists keeps the door until both marks are saved
  const t = storage(), two = refusedStore(t);
  two.hold(KEYED, 'Saturday', visits(['a', 'S']), DOORS, 'why'); two.hold(QUERY, 'Cedar', visits(['a', 'O']), DOORS, 'why');
  two.clear([KEYED], ['a']);
  assert.deepEqual(Object.keys(two.note?.lists[0].doors ?? {}), ['a'], 'still refused on the other list');
  two.clear([QUERY], ['a']); assert.equal(two.note, null);
  // a save under new person ids: the door renamed with the mark is taken out when the voter is saved
  const r = storage(), moved = refusedStore(r);
  moved.hold(QUERY, 'Saturday', visits(['a', 'S'], ['b', 'U']), DOORS, 'why');
  moved.adopt(QUERY, KEYED, new Map([['a', 'ra']]));
  writes = r.writes(); moved.clear([KEYED, QUERY], ['ra']);
  assert.equal(r.writes(), writes + 1);
  assert.deepEqual(Object.keys(moved.note?.lists[0].doors ?? {}), ['b']);
  assert.deepEqual(stored(r), {marks: {[KEYED]: {b: 'U'}}, note: {lists: [{title: 'Saturday', doors: {b: '4110 N Cedar Heights Dr (U)'}}], reasons: ['why']}});
});

test('Sign out: reset() and resetRefused() forget the marks and the note in memory and in the tab, and never throw', () => {
  const s = storage(), store = refusedStore(s);
  store.hold(KEYED, 'Saturday', visits(['a', 'S']), DOORS, 'why');
  store.reset();
  assert.equal(store.marks.size, 0); assert.equal(store.note, null); assert.equal(s.data.has(REFUSED_KEY), false);
  assert.equal(refusedStore(s).note, null, 'a reload finds nothing');
  const t = storage(), live = refusedStore(t), u = storage();
  live.hold(KEYED, 'Saturday', visits(['a', 'S']), DOORS, 'why'); u.data.set(REFUSED_KEY, t.data.get(REFUSED_KEY) ?? '');
  resetRefused(u);   // what Sign out calls: every loaded store, and the given storage
  assert.equal(live.marks.size, 0); assert.equal(live.note, null);
  assert.equal(t.data.has(REFUSED_KEY), false, 'the loaded store removes its own key'); assert.equal(u.data.has(REFUSED_KEY), false, 'and the tab\'s key goes too');
  assert.doesNotThrow(() => resetRefused(throwing())); assert.doesNotThrow(() => refusedStore(throwing()).reset()); assert.doesNotThrow(() => resetRefused(null));
});

test('a 401 (most often a session timing out mid-canvass) keeps the refusals: a walker\'s mark is never erased; only Sign out forgets them', async () => {
  const s = storage(), store = refusedStore(s), original = globalThis.fetch;
  const kept = () => { assert.deepEqual([...store.marks.get(KEYED) ?? []], [['a', 'S']]); assert.ok(store.note); assert.deepEqual(stored(s).marks, {[KEYED]: {a: 'S'}}); };
  try {
    store.hold(KEYED, 'Saturday', visits(['a', 'S']), DOORS, 'why');
    for (const status of [401, 403, 500]) {
      globalThis.fetch = async () => new Response(JSON.stringify({error: status === 401 ? 'Sign in to continue' : 'No.'}), {status});
      await assert.rejects(localJson('/api/v3/walk/lists'));
      kept();
    }
    globalThis.fetch = async (url) => String(url).includes('/api/auth/session') ? new Response(JSON.stringify({csrf: 't'}), {status: 200}) : new Response(JSON.stringify({error: 'Sign in to continue'}), {status: 401});
    await assert.rejects(postJson('/api/v3/walk/dispositions', {}), /Sign in to continue/);
    kept();   // a write answered 401 keeps them too
  } finally { globalThis.fetch = original; }
  resetRefused(storage());   // Sign out still forgets them
  assert.equal(store.marks.size, 0); assert.equal(store.note, null); assert.equal(s.data.has(REFUSED_KEY), false);
});

test('a write that fails (a full quota) removes the key: a reload never brings back a mark since saved or a note since dismissed', () => {
  const s = storage(); let ok = 1;
  const quota: RefusedStorage = {getItem: k => s.getItem(k), removeItem: k => s.removeItem(k), setItem: (k, v) => { if (ok-- <= 0) throw new Error('QuotaExceededError'); s.setItem(k, v); }};
  const store = refusedStore(quota);
  store.hold(KEYED, 'Saturday', visits(['a', 'S'], ['b', 'U']), DOORS, 'why');   // the one write that succeeds
  assert.ok(s.data.has(REFUSED_KEY));
  store.clear([KEYED], ['a']);                                                      // a saved; the write fails
  assert.equal(s.data.has(REFUSED_KEY), false, 'the older copy (a still refused) is gone');
  assert.deepEqual([...store.marks.get(KEYED) ?? []], [['b', 'U']], 'memory stays right for this page');
  assert.equal(refusedStore(s).marks.size, 0, 'a reload starts empty rather than with a mark since saved');
  const d = storage(); let once = 1;
  const full: RefusedStorage = {getItem: k => d.getItem(k), removeItem: k => d.removeItem(k), setItem: (k, v) => { if (once-- <= 0) throw new Error('QuotaExceededError'); d.setItem(k, v); }};
  const dismissing = refusedStore(full);
  dismissing.hold(KEYED, 'Saturday', visits(['a', 'S']), DOORS, 'why'); dismissing.dismiss();
  assert.equal(refusedStore(d).note, null, 'a dismissed note never comes back');
  assert.equal(writeRefused({marks: new Map([[KEYED, new Map<string, WalkCode | null>([['a', 'S']])]]), note: null}, full), false);
});

test('a person id of "__proto__" round-trips as a plain key, and nothing is polluted', () => {
  const s = storage(), store = refusedStore(s);
  store.hold(KEYED, 'Saturday', visits(['__proto__', 'S'], ['constructor', 'U']), {}, 'why');
  const raw = stored(s);
  assert.deepEqual(Object.keys(raw.marks[KEYED]), ['__proto__', 'constructor']); assert.equal(raw.marks[KEYED].__proto__, 'S');
  assert.deepEqual(Object.keys(raw.note.lists[0].doors), ['__proto__', 'constructor']);
  const again = refusedStore(s);
  assert.deepEqual([...again.marks.get(KEYED) ?? []], [['__proto__', 'S'], ['constructor', 'U']]);
  const doors = again.note?.lists[0].doors ?? {};
  assert.ok(Object.hasOwn(doors, '__proto__') && doors.__proto__ === 'A door on this list (S)' && Object.getPrototypeOf(doors) === Object.prototype);
  assert.equal(({} as Record<string, unknown>).S, undefined, 'Object.prototype is untouched');
  again.clear([KEYED], ['__proto__', 'constructor']);
  assert.equal(again.note, null); assert.equal(s.data.has(REFUSED_KEY), false);
});

test('the unload guard: only flagged voters of the list shown count, never a refused mark the list does not show', () => {
  const shown = listOf(['a', 'b']);
  assert.deepEqual([...flaggedOnList(shown, new Set(['a', 'gone']))], ['a'], 'a voter the saved list dropped does not hold the guard up');
  assert.equal(flaggedOnList(shown, new Set(['gone'])).size, 0);
  assert.equal(flaggedOnList(null, new Set(['a'])).size, 0, 'no list shown');
  const src = readFileSync(path.join(import.meta.dirname, '../../src/v3/components/WalkScreen.tsx'), 'utf8').replace(/\r\n/g, '\n');
  assert.ok(/^const shownFlagged:\{current:ReadonlySet<string>\}=\{current:new Set\(\)\};/m.test(src), 'a ref beside the guards, outliving the screen');
  assert.ok(/useEffect\(\(\)=>\{shownFlagged\.current=flaggedOnList\(payload,unsaved\);\},\[payload,unsaved\]\)/.test(src), 'kept to the list shown and its flags');
  for (const guard of [/const warnHeld=\(e:BeforeUnloadEvent\)=>\{if\(outbox\.unconfirmed\(\)\|\|refusals\.note\|\|shownFlagged\.current\.size>0\)/, /const warn=\(e:BeforeUnloadEvent\)=>\{if\(outbox\.unconfirmed\(\)\|\|saving\.current\|\|optInWrites\.pending\(\)\|\|refusals\.note\|\|shownFlagged\.current\.size>0\)/])
    assert.ok(guard.test(src), String(guard));
});

test('wiring: the note shown follows the store after a save, the screen drops its refusals on Sign out, and only Sign out resets (never the API client)', () => {
  // Line endings are normalised: a Windows checkout (core.autocrlf) has CRLF, and the patterns below match LF.
  const src = readFileSync(path.join(import.meta.dirname, '../../src/v3/components/WalkScreen.tsx'), 'utf8').replace(/\r\n/g, '\n');
  const client = readFileSync(path.join(import.meta.dirname, '../../src/v3/api/client.ts'), 'utf8').replace(/\r\n/g, '\n');
  const legacy = readFileSync(path.join(import.meta.dirname, '../../src/main.tsx'), 'utf8').replace(/\r\n/g, '\n');
  assert.ok(/sendBatch\(entry,batch,[^\n]*\n[^\n]*setUnsaved[^\n]*\n\s*showRefusal\(refusals\.note\);/.test(src), 'after a batch is saved the note shown is the store\'s');
  assert.ok(/setEarlier\(message===null\?null:\{message\}\);\n\s*showRefusal\(refusals\.note\);/.test(src), 'and after results from earlier are saved');
  assert.ok(/useEffect\(\(\)=>onRefusedReset\(\(\)=>\{/.test(src) && /showRefusal\(null\);setUnsaved\(new Set\(\)\);/.test(src), 'the screen forgets what it shows of them');
  assert.ok(!/resetRefused|walkRefused|sessionEnded/.test(client), 'the API client never forgets the refusals: a 401 is a timeout, not a sign-out');
  assert.ok(/onClick=\{async\(\)=>\{resetRefused\(\);await post\('auth\/logout',\{\}\);/.test(legacy), 'Sign out forgets them before it signs out');
});
