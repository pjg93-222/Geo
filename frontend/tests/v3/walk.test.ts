// Walk list text rules, CSV in and out, and the door script. Run with: node --test tests/v3/walk.test.ts
import {test} from 'node:test';
import assert from 'node:assert/strict';
import type {WalkDoor, WalkListPayload, WalkRun, WalkVoter} from '../../src/v3/api/walkTypes.ts';
import * as walkLib from '../../src/v3/lib/walk.ts';
import {
  CODE_LABEL, CSV_COLUMNS, MAX_UPLOAD_ROWS, WALK_CODES, areaText, buildWalkCsv, codeLegend, countText, csvValue, distanceText, doorAddress,
  fullDate, houseSpan, isWalkCode, parseWalkCsv, phoneText, regDate, runHeader, scoreColumns, sideFromText, sideLabel, splitCsv, tallyText,
  totalsLine, unitText, walkCsvRows, walkFileName
} from '../../src/v3/lib/walk.ts';
import {DOOR_SCRIPT, choiceText, fillScript, stepById, stepNumber} from '../../src/v3/lib/walkScript.ts';

const BOM = String.fromCharCode(0xfeff);
const voter = (over: Partial<WalkVoter> = {}): WalkVoter => ({
  person_id: 'p1', name: 'Dana Whitfield', phone: '7195550142', pri: {voted: 4, eligible: 6}, gen: {voted: 5, eligible: 6}, abs: {voted: 2, eligible: 12},
  party: 'D', age: 44, gender: 'F', reg_date: '1994-09-29', last: null, ...over
});
const door = (seq: number, house: string, voters: WalkVoter[], unit = ''): WalkDoor => ({seq, house, unit, placed_by: 'geocode', lon: -104.8, lat: 38.8, voters});
const run = (over: Partial<WalkRun> = {}): WalkRun => ({
  stop: 1, street_id: 7, street: 'N Cedar Heights Dr', side: 'even', precinct: '02012', from_house: '4102', to_house: '4172', walk_m: 410, lr: 1,
  path: [[-104.8, 38.8], [-104.801, 38.801]], approach: [], doors: [door(1, '4102', [voter()])], ...over
});
const payload = (runs: WalkRun[]): WalkListPayload => ({
  schema: 'walk-list-1', generated_on: '2026-10-02', list: {key: null, title: 'Saturday', kind: 'standard', created_at: null}, query: {precinct: '02012'},
  window: {primaries: ['2014'], generals: ['2014'], abs_rule: 'Abs = mail ballots'},
  totals: {streets: 1, stops: runs.length, doors: 1, voters: 1, walk_m: 410}, bbox: [-104.81, 38.79, -104.79, 38.81], runs
});

test('tallies: voted over eligible, a lone slash when nothing was eligible', () => {
  assert.equal(tallyText({voted: 4, eligible: 6}), '4/6'); assert.equal(tallyText({voted: 0, eligible: 12}), '0/12');
  assert.equal(tallyText({voted: 0, eligible: 0}), '/'); assert.equal(tallyText(null), '/'); assert.equal(tallyText(undefined), '/');
});

test('dates: m/d/yy for registration, m/d/yyyy for the page date, empty when unusable', () => {
  assert.equal(regDate('1994-09-29'), '9/29/94'); assert.equal(regDate('2005-04-14'), '4/14/05'); assert.equal(regDate('2013-01-03'), '1/3/13');
  assert.equal(regDate('2000-12-31'), '12/31/00');
  for (const bad of ['', 'n/a', '1994-13-01', '1994-00-10', '1994-09-32', '09/29/1994']) assert.equal(regDate(bad), '', bad);
  assert.equal(fullDate('2026-10-02'), '10/2/2026'); assert.equal(fullDate(''), '');
});

test('phones: ten digits formatted, a leading 1 dropped, odd shapes kept as typed', () => {
  assert.equal(phoneText('7195550142'), '(719) 555-0142'); assert.equal(phoneText('17195550142'), '(719) 555-0142');
  assert.equal(phoneText('(719) 555-0142'), '(719) 555-0142'); assert.equal(phoneText('719.555.0142'), '(719) 555-0142'); assert.equal(phoneText('+1 719-555-0142'), '(719) 555-0142');
  assert.equal(phoneText('5550142'), '555-0142'); assert.equal(phoneText(''), ''); assert.equal(phoneText('  12345 '), '12345');
});

test('sides, units and door addresses', () => {
  assert.equal(sideLabel('even'), 'Even'); assert.equal(sideLabel('odd'), 'Odd'); assert.equal(sideLabel('both'), 'Both');
  assert.equal(unitText('204'), '#204'); assert.equal(unitText('B'), '#B'); assert.equal(unitText('Apt 204'), 'Apt 204'); assert.equal(unitText('UNIT B'), 'UNIT B');
  assert.equal(unitText('#5'), '#5'); assert.equal(unitText('Aptos'), '#Aptos'); assert.equal(unitText(''), '');
  assert.equal(doorAddress({house: '4102', unit: ''}), '4102'); assert.equal(doorAddress({house: '4102', unit: '204'}), '4102 #204'); assert.equal(doorAddress({house: '4102', unit: 'Apt 3'}), '4102 Apt 3');
});

test('run header: stop, house span and door count', () => {
  const r = run({doors: Array.from({length: 11}, (_, i) => door(i + 1, String(4102 + 2 * i), [voter()]))});
  assert.equal(runHeader(r, 6), 'Stop 1 of 6 · 4102 → 4172 · 11 doors');
  assert.equal(runHeader(run({stop: 3, from_house: '15', to_house: '15'}), 6), 'Stop 3 of 6 · 15 · 1 door');
  assert.equal(houseSpan({from_house: '', to_house: ''}), ''); assert.equal(houseSpan({from_house: '9', to_house: ''}), '9');
});

test('totals line and distance: counts and kilometres, with no time estimate', () => {
  assert.equal(totalsLine({streets: 4, stops: 6, doors: 58, voters: 104, walk_m: 2900}), '4 streets · 6 stops · 58 doors · 104 voters · 2.9 km');
  assert.equal(totalsLine({streets: 1, stops: 1, doors: 1, voters: 1, walk_m: 60}), '1 street · 1 stop · 1 door · 1 voter · 0.1 km');
  assert.equal(totalsLine({streets: 12, stops: 30, doors: 1200, voters: 1500, walk_m: 14000}), '12 streets · 30 stops · 1,200 doors · 1,500 voters · 14.0 km');
  // A list saved before `minutes` was dropped still carries it; the line ignores it and nothing rejects the extra field.
  for (const minutes of [0, 2, 160, 3000, -3, NaN]) { const old = {streets: 4, stops: 6, doors: 58, voters: 104, walk_m: 2900, minutes}; assert.equal(totalsLine(old), '4 streets · 6 stops · 58 doors · 104 voters · 2.9 km', String(minutes)); }
  assert.ok(!('durationText' in walkLib), 'no walking-time formatter is left to call');
  assert.equal(distanceText(2850), '2.9 km'); assert.equal(distanceText(2849), '2.8 km'); assert.equal(distanceText(0), '0.0 km'); assert.equal(distanceText(1000), '1.0 km');
  assert.equal(distanceText(NaN), '0.0 km'); assert.equal(distanceText(-50), '0.0 km');
  assert.equal(countText(1, 'door'), '1 door'); assert.equal(countText(0, 'door'), '0 doors'); assert.equal(countText(2, 'street'), '2 streets');
});

test('result codes: five codes with labels, and a one-line legend', () => {
  assert.deepEqual([...WALK_CODES], ['S', 'U', 'O', 'NH', 'MV']);
  assert.deepEqual(WALK_CODES.map(c => CODE_LABEL[c]), ['Supporter', 'Undecided', 'Opposed', 'Not home', 'Moved']);
  assert.equal(codeLegend(), 'S Supporter · U Undecided · O Opposed · NH Not home · MV Moved');
  assert.ok(isWalkCode('NH')); assert.ok(!isWalkCode('nh')); assert.ok(!isWalkCode('YS')); assert.ok(!isWalkCode(null));
});

test('area text and file names', () => {
  assert.equal(areaText({precinct: '02012'}), 'Precinct 02012'); assert.equal(areaText({ss: '12', sh: '45', streets: '1:even'}), 'Senate district 12 · House district 45');
  assert.equal(areaText(undefined), '');
  assert.equal(walkFileName('Precinct 02012', '2026-10-02', 'pdf'), 'walk-list-precinct-02012-2026-10-02.pdf');
  assert.equal(walkFileName('Senate district 12 · House district 45', '2026-10-02T08:00:00Z', 'csv'), 'walk-list-senate-district-12-house-district-45-2026-10-02.csv');
  assert.match(walkFileName('', 'oops', 'pdf'), /^walk-list-list-\d{4}-\d{2}-\d{2}\.pdf$/);
});

test('upload CSV: header row, any column order, case and punctuation ignored', () => {
  assert.deepEqual(parseWalkCsv('Street,Side,City,ZIP\nCedar Heights Dr,even,Colorado Springs,80918\nOak St,Odd,,\n'), [
    {street: 'Cedar Heights Dr', side: 'even', city: 'Colorado Springs', zip: '80918'}, {street: 'Oak St', side: 'odd'}
  ]);
  assert.deepEqual(parseWalkCsv('ZIP Code,STREET NAME,Even/Odd\n80918,Main St,E\n'), [{street: 'Main St', side: 'even', zip: '80918'}]);
  assert.deepEqual(parseWalkCsv('street\nMain St\nElm Ave'), [{street: 'Main St'}, {street: 'Elm Ave'}]);
});

test('upload CSV: quoted fields, doubled quotes, embedded commas and line breaks', () => {
  const text = 'street,side,city\r\n"Main St, North",odd,"Colorado ""Springs"""\r\n"Elm\nAve",even,\r\n';
  assert.deepEqual(parseWalkCsv(text), [{street: 'Main St, North', side: 'odd', city: 'Colorado "Springs"'}, {street: 'Elm Ave', side: 'even'}]);
  assert.deepEqual(splitCsv('a,"b,c",d\n"e""f",g,'), [['a', 'b,c', 'd'], ['e"f', 'g', '']]);
});

test('upload CSV: BOM, every line ending, blank lines and rows without a street', () => {
  assert.deepEqual(parseWalkCsv(BOM + 'street,side\r\nMain St,odd\r\n\r\n\r\nElm Ave,even\r\n'), [{street: 'Main St', side: 'odd'}, {street: 'Elm Ave', side: 'even'}]);
  assert.deepEqual(parseWalkCsv('street,side\rMain St,odd\rElm Ave,even'), [{street: 'Main St', side: 'odd'}, {street: 'Elm Ave', side: 'even'}]);
  assert.deepEqual(parseWalkCsv('street,side\n,odd\n  ,  \nMain St,\n,,\n'), [{street: 'Main St'}]);
  assert.deepEqual(parseWalkCsv(''), []); assert.deepEqual(parseWalkCsv('\n\n  \n'), []); assert.deepEqual(parseWalkCsv('street,side\n'), []);
});

test('upload CSV: no header row reads street, side, city, zip in that order', () => {
  assert.deepEqual(parseWalkCsv('Cedar Heights Dr,even\nOak St\n"Main St, North",odd,Colorado Springs,80918'), [
    {street: 'Cedar Heights Dr', side: 'even'}, {street: 'Oak St'}, {street: 'Main St, North', side: 'odd', city: 'Colorado Springs', zip: '80918'}
  ]);
  assert.deepEqual(parseWalkCsv('Oak St'), [{street: 'Oak St'}]);
});

test('upload CSV: other delimiters and spacing', () => {
  assert.deepEqual(parseWalkCsv('street;side\nMain St;odd\n'), [{street: 'Main St', side: 'odd'}]);
  assert.deepEqual(parseWalkCsv('street\tside\nMain St\todd\n'), [{street: 'Main St', side: 'odd'}]);
  assert.deepEqual(parseWalkCsv('street,side\n"  Main   St ", odd \n'), [{street: 'Main St', side: 'odd'}]);
});

test('side words: synonyms map to even, odd or both and unknown words pass through', () => {
  const want: [string, string][] = [['Even', 'even'], ['EVENS', 'even'], ['e', 'even'], ['E.', 'even'], ['even side', 'even'], ['Even numbers', 'even'],
    ['Odd', 'odd'], ['odds', 'odd'], ['O', 'odd'], ['odd side', 'odd'], ['Both', 'both'], ['all', 'both'], ['Both sides', 'both'], ['b', 'both']];
  for (const [text, side] of want) assert.equal(sideFromText(text), side, text);
  assert.equal(sideFromText('north'), null); assert.equal(sideFromText(''), null); assert.equal(sideFromText('constructor'), null);
  assert.deepEqual(parseWalkCsv('street,side\nMain St,Evens\nElm Ave,north\n'), [{street: 'Main St', side: 'even'}, {street: 'Elm Ave', side: 'north'}]);
});

test('upload CSV: a header without a street column is an error, and so is a file over the row limit', () => {
  assert.throws(() => parseWalkCsv('side,city\nodd,Colorado Springs\n'), /street/);
  const rows = (n: number) => 'street\n' + Array.from({length: n}, (_, i) => `Street ${i}`).join('\n');
  assert.equal(parseWalkCsv(rows(MAX_UPLOAD_ROWS)).length, 500);
  assert.throws(() => parseWalkCsv(rows(MAX_UPLOAD_ROWS + 1)), /500/);
  assert.equal(parseWalkCsv('street\n' + '\n'.repeat(900) + 'Main St').length, 1, 'blank lines do not count toward the limit');
  assert.throws(() => parseWalkCsv('street\n' + 'x'.repeat(1_000_001)), /1 MB/);
});

test('CSV value guard prefixes a quote before formula characters', () => {
  for (const s of ['=SUM(A1)', '+1', '-2', '@cmd', '\tx', '\rx', '\nx']) assert.equal(csvValue(s), "'" + s, JSON.stringify(s));
  for (const s of ['4 of 6', 'Dana', '(719) 555-0142', 'a=b', '']) assert.equal(csvValue(s), s);
  assert.equal(csvValue(null), ''); assert.equal(csvValue(undefined), ''); assert.equal(csvValue(44), '44');
});

test('CSV build: BOM, CRLF, every field quoted, fixed columns', () => {
  const csv = buildWalkCsv(payload([run()]));
  assert.ok(csv.startsWith(BOM + '"Stop","Precinct","Street","Side","Address","Unit","Name","Phone","Pri","Gen","Abs","Party","Age","Gender","Reg. Date","Result"\r\n'));
  assert.equal(CSV_COLUMNS.length, 16);
  const lines = csv.slice(1).split('\r\n'); assert.equal(lines.length, 3, 'header, one voter, trailing line break'); assert.equal(lines[2], '');
  assert.equal(lines[1], '"1","02012","N Cedar Heights Dr","Even","4102","","Dana Whitfield","(719) 555-0142","4 of 6","5 of 6","2 of 12","D","44","F","1994-09-29",""');
  assert.ok(!/\n(?!$)/.test(csv.replace(/\r\n/g, '')), 'no bare line breaks inside the file');
});

test('CSV build: quotes doubled, formula guard applied, line breaks flattened, rows in walking order', () => {
  const r1 = run({stop: 1, doors: [door(1, '4102', [voter({name: 'Dana "Dee" Whitfield', phone: ''}), voter({person_id: 'p2', name: '=HYPERLINK("x")', age: null, pri: {voted: 0, eligible: 0}})], '204')]});
  const r2 = run({stop: 2, side: 'odd', street: 'Oak St', precinct: '02013', doors: [door(2, '15', [voter({person_id: 'p3', name: 'Lee\nStone', party: '-', phone: '+1 (719) 555-0199'})])]});
  const rows = walkCsvRows(payload([r1, r2]));
  assert.equal(rows.length, 3); assert.deepEqual(rows.map(r => r[0]), ['1', '1', '2']); assert.deepEqual(rows.map(r => r[3]), ['Even', 'Even', 'Odd']);
  assert.equal(rows[0][5], '204'); assert.equal(rows[0][7], ''); assert.equal(rows[1][6], '=HYPERLINK("x")'); assert.equal(rows[1][8], '0 of 0'); assert.equal(rows[1][12], '');
  const csv = buildWalkCsv(payload([r1, r2]));
  assert.ok(csv.includes('"Dana ""Dee"" Whitfield"')); assert.ok(csv.includes('"\'=HYPERLINK(""x"")"')); assert.ok(csv.includes('"Lee Stone"')); assert.ok(csv.includes('"\'-"'));
  assert.ok(csv.includes('"(719) 555-0199"'), 'the phone is reformatted before it reaches the file');
});

test('CSV build: no runs gives a header only', () => {
  assert.equal(buildWalkCsv(payload([])).split('\r\n').length, 2);
  assert.equal(walkCsvRows(payload([])).length, 0);
});

test('door script: every step reachable, every link and code valid, placeholders present', () => {
  const ids = DOOR_SCRIPT.map(s => s.id); assert.equal(new Set(ids).size, ids.length); assert.equal(ids[0], 'intro');
  const marks = new Set<string>();
  for (const s of DOOR_SCRIPT) for (const c of s.choices) {
    if (c.next) { assert.ok(stepById(c.next), `${s.id} -> ${c.next}`); assert.ok(stepNumber(c.next) > stepNumber(s.id), 'steps only move forward'); }
    if (c.mark) { assert.ok(isWalkCode(c.mark), c.mark); marks.add(c.mark); }
    assert.ok(c.next || c.mark, `${s.id}: ${c.say} leads somewhere`);
  }
  assert.deepEqual([...marks].sort(), [...WALK_CODES].sort(), 'each result code is reachable from the script');
  const seen = new Set<string>(['intro']); for (let i = 0; i < DOOR_SCRIPT.length; i++) for (const id of [...seen]) for (const c of stepById(id)?.choices ?? []) if (c.next) seen.add(c.next);
  assert.equal(seen.size, DOOR_SCRIPT.length, 'no step is left out');
  const all = DOOR_SCRIPT.map(s => s.line).join(' '); assert.ok(all.includes('[your name]') && all.includes('[campaign]') && all.includes('[voter name]'));
});

test('door script: placeholders fill in and answers read as actions', () => {
  assert.equal(fillScript('Hi, I am [your name] with [campaign].', {'your name': 'Sam', campaign: 'Maple Street'}), 'Hi, I am Sam with Maple Street.');
  assert.equal(fillScript('Is this [voter name]?', {'voter name': '  '}), 'Is this [voter name]?');
  assert.equal(choiceText({say: 'Not sure yet', mark: 'U', next: 'issue'}), 'Not sure yet: mark U, go to 3');
  assert.equal(choiceText({say: 'No one answers', mark: 'NH'}), 'No one answers: mark NH');
  assert.equal(choiceText({say: 'Yes', next: 'support'}), 'Yes: go to 2');
});

test('door script copy follows the neutral-wording gate', () => {
  const ADVISORY = /\b(prospect|prospects|target|targets|targeting|best|recommend|recommended|priority|priorities|should|ideal|worth|likely to join|persuad|persuasion|convert|opportunity|high[- ]value|insufficient evidence)\b/i;
  const text = DOOR_SCRIPT.flatMap(s => [s.title, s.line, ...s.choices.map(c => c.say)]).concat(codeLegend());
  assert.deepEqual(text.filter(t => ADVISORY.test(t)), []);
});

// --- walk screen helpers: streets in the list, titles, history key, map shading, results in the CSV -------------------
import {
  MAX_WALK_STREETS, defaultListTitle, doorBreaks, doorsOnSide, historyKey, initialResults, listLabel, majorParty, mergePick, nextSide,
  parseStreetsParam, partyName, sideWords, streetsParam, yearRange
} from '../../src/v3/lib/walk.ts';

const pick = (id: number, side: 'even' | 'odd' | 'both' = 'both') => ({id, side, label: `Street ${id}`, city: ''});

test('streets param: written as id:side and read back, tolerant of junk', () => {
  assert.equal(streetsParam([pick(12, 'even'), pick(15)]), '12:even,15:both'); assert.equal(streetsParam([]), '');
  assert.deepEqual(parseStreetsParam('12:even,15:both,7'), [{id: 12, side: 'even'}, {id: 15, side: 'both'}, {id: 7, side: 'both'}]);
  assert.deepEqual(parseStreetsParam(' 3:odd , x:even,4:north,,-5,6:'), [{id: 3, side: 'odd'}]);
  assert.deepEqual(parseStreetsParam(''), []); assert.deepEqual(parseStreetsParam(null), []); assert.deepEqual(parseStreetsParam(undefined), []);
  assert.deepEqual(parseStreetsParam('9:even,9:odd,9:even'), [{id: 9, side: 'both'}], 'a repeat with the other side becomes both');
  const many = Array.from({length: 60}, (_, i) => i + 1).join(',');
  assert.equal(parseStreetsParam(many).length, MAX_WALK_STREETS);
  assert.equal(streetsParam(parseStreetsParam('1:odd,2,3:even')), '1:odd,2:both,3:even');
});

test('merge pick: new streets append, the other side widens to both, no change keeps the array', () => {
  const a = [pick(1, 'even')];
  assert.deepEqual(mergePick(a, pick(2, 'odd')).map(p => p.id), [1, 2]);
  assert.equal(mergePick(a, pick(1, 'even')), a);
  assert.deepEqual(mergePick(a, pick(1, 'odd')).map(p => p.side), ['both']);
  assert.deepEqual(mergePick([pick(1, 'both')], pick(1, 'even')).map(p => p.side), ['both']);
  assert.equal(a[0].side, 'even', 'the input list is not changed');
});

test('sides cycle both, even, odd and read as words; door counts follow the side', () => {
  assert.deepEqual(['both', 'even', 'odd'].map(s => nextSide(s as 'both')), ['even', 'odd', 'both']);
  assert.deepEqual(['both', 'even', 'odd'].map(s => sideWords(s as 'both')), ['both sides', 'even side', 'odd side']);
  const street = {doors: 33, even: 22, odd: 11};
  assert.deepEqual((['both', 'even', 'odd'] as const).map(s => doorsOnSide(street, s)), [33, 22, 11]);
});

test('list label and default title: area first, else the first streets', () => {
  const base = {generated_on: '2026-10-02', runs: [run({street: 'Cedar Heights Dr'}), run({stop: 2, street: 'Oak St'}), run({stop: 3, street: 'Cedar Heights Dr'}), run({stop: 4, street: 'Pine St'}), run({stop: 5, street: 'Elm Ave'})]};
  assert.equal(listLabel({...base, query: {precinct: '02012'}}), 'Precinct 02012');
  assert.equal(listLabel({...base, query: {streets: '1:even'}}), 'Cedar Heights Dr, Oak St and 2 more');
  assert.equal(listLabel({...base, runs: base.runs.slice(0, 2), query: {}}), 'Cedar Heights Dr and Oak St');
  assert.equal(listLabel({...base, runs: base.runs.slice(0, 1), query: {}}), 'Cedar Heights Dr'); assert.equal(listLabel({runs: [], query: {}}), '');
  assert.equal(defaultListTitle({...base, query: {precinct: '02012'}}), 'Precinct 02012 · 10/2/2026');
  assert.equal(defaultListTitle({runs: [], query: {}, generated_on: '2026-10-02'}), 'Walk list · 10/2/2026');
  assert.equal(defaultListTitle({runs: [], query: {}, generated_on: ''}), 'Walk list');
  assert.ok(defaultListTitle({...base, runs: [], query: {precinct: 'x'.repeat(300)}}).length <= 120);
});

test('history key names the election years and the mail-ballot rule', () => {
  assert.equal(yearRange(['2014p', '2016p', '2024p']), '2014–2024'); assert.equal(yearRange(['2024']), '2024'); assert.equal(yearRange([]), ''); assert.equal(yearRange(undefined), '');
  const key = historyKey({primaries: ['2014-06-24', '2024-06-25'], generals: ['2014-11-04', '2024-11-05'], abs_rule: 'Abs = mail ballots'});
  assert.ok(key.includes('(primaries 2014–2024, generals 2014–2024)')); assert.ok(key.includes('A lone slash means none were eligible.')); assert.ok(key.includes('Abs = mail ballots: ballots cast by mail'));
  assert.ok(historyKey(undefined).includes('Abs = mail ballots')); assert.ok(!historyKey(undefined).includes('('));
});

test('map shading: door-count edges give each shade about the same number of blocks', () => {
  assert.deepEqual(doorBreaks([1, 1, 1, 2, 2, 3, 5, 8, 13, 21]), [2, 5, 13]);
  assert.deepEqual(doorBreaks([4, 4, 4, 4]), [], 'one value, one shade');
  assert.deepEqual(doorBreaks([]), []); assert.deepEqual(doorBreaks([0, 0, NaN]), []);
  const edges = doorBreaks(Array.from({length: 100}, (_, i) => i + 1)); assert.deepEqual(edges, [21, 41, 61, 81]);
  assert.deepEqual(doorBreaks([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 3), [4, 7]);
  for (let n = 0; n < 40; n++) { const e = doorBreaks(Array.from({length: n}, (_, i) => (i * 7) % 11)); assert.deepEqual(e, [...e].sort((a, b) => a - b)); assert.equal(new Set(e).size, e.length); }
});

test('map shading: largest party on a block, ties are mixed', () => {
  assert.equal(majorParty({D: 5, R: 2, U: 1}), 'D'); assert.equal(majorParty({R: 1, U: 3, L: 1}), 'U'); assert.equal(majorParty({D: 1, W: 2}), 'W');
  assert.equal(majorParty({D: 1, R: 0, '': 2}), ''); assert.equal(majorParty({D: 3, R: 3, U: 1}), 'mixed'); assert.equal(majorParty({G: 1, L: 1}), 'mixed');
  assert.equal(majorParty({}), 'none'); assert.equal(majorParty({D: 0, R: 0}), 'none');
  assert.equal(partyName('W', {W: 'Working Families'}), 'W Working Families'); assert.equal(partyName('T', {W: 'Working Families'}), 'Code T'); assert.equal(partyName('', {}), 'No code');
});

test('CSV results: the code on file, the one set on screen, and a cleared one', () => {
  const v1 = voter({person_id: 'a', last: {code: 'NH', on: '2026-09-30'}});
  const v2 = voter({person_id: 'b', name: 'Lee Stone', last: {code: 'S', on: '2026-09-30'}});
  const v3 = voter({person_id: 'c', name: 'Pat Moss', last: null});
  const p = payload([run({doors: [door(1, '4102', [v1, v2, v3])]})]);
  const col = (rows: string[][]) => rows.map(r => r[15]);
  assert.deepEqual(col(walkCsvRows(p)), ['NH', 'S', ''], 'without screen results the code on file is used');
  assert.deepEqual(col(walkCsvRows(p, {})), ['NH', 'S', '']);
  assert.deepEqual(col(walkCsvRows(p, {a: 'MV', c: 'U'})), ['MV', 'S', 'U'], 'results set on screen win');
  assert.deepEqual(col(walkCsvRows(p, {a: null})), ['', 'S', ''], 'a cleared result leaves the cell empty');
  assert.ok(buildWalkCsv(p, {c: 'O'}).includes('"1994-09-29","O"\r\n'));
  assert.deepEqual(initialResults(p), {a: 'NH', b: 'S'}); assert.deepEqual(initialResults(payload([])), {});
});

// --- review additions: spreadsheet guard after leading spaces, the PDF builder -------------------------------------------
test('CSV value guard also catches a formula character after leading spaces or invisible marks', () => {
  for (const s of [' =1+1', '   +cmd', '\u{a0}@x', ' \t-1', '\u{200b}=HYPERLINK("x")', '\u0000=1', '\u{feff}=1', '\u{2003}-2']) assert.equal(csvValue(s), "'" + s, JSON.stringify(s));
  for (const s of ['a =1', 'Dana -Lee', ' Dana', '   ', ' 4 of 6', '\u{200b}Dana', '(719) 555-0142', ' ']) assert.equal(csvValue(s), s, JSON.stringify(s));
  const csv = buildWalkCsv(payload([run({doors: [door(1, '4102', [voter({name: " =cmd|'/c calc'!A1"})])]})]));
  assert.ok(csv.includes(`"' =cmd|'/c calc'!A1"`), 'a name with a leading space stays text in the file');
});

test('walk CSV guard also catches full-width = + - @ and a formula behind bidi controls', () => {
  for (const s of ['\u{ff1d}1+1', '\u{ff0b}1', '\u{ff0d}1', '\u{ff20}SUM(1)', '\u{202e}=1', '\u{2066}+1', '\u{ad}-1', '\u{61c}@x', '\u{180e}\u{ff1d}1'])
    assert.equal(csvValue(s), "'" + s, JSON.stringify(s));
  for (const s of ['\u{202e}Dana', 'Dana\u{ff1d}Lee', '\u{2066}4 of 6\u{2069}']) assert.equal(csvValue(s), s, JSON.stringify(s));
  const csv = buildWalkCsv(payload([run({doors: [door(1, '4102', [voter({name: '\u{202e}\u{ff1d}cmd|A1'})])]})]));
  assert.ok(csv.includes(`"'\u{202e}\u{ff1d}cmd|A1"`), 'a full-width formula behind a bidi control stays text in the file');
});

test('dates: a day is read as written, so no viewer time zone moves a date or a local-time stamp', () => {
  const was = process.env.TZ;
  try {
    for (const zone of ['America/Denver', 'Pacific/Auckland', 'UTC']) {
      process.env.TZ = zone;
      assert.equal(fullDate('2026-10-02'), '10/2/2026', zone); assert.equal(fullDate('2026-10-02T21:15:00-06:00'), '10/2/2026', zone); assert.equal(regDate('1994-09-29'), '9/29/94', zone);
    }
  } finally { if (was === undefined) delete process.env.TZ; else process.env.TZ = was; }
});

// walkPdf.ts imports its neighbours without a file extension, so node needs a resolve hook to load it straight from source.
import {registerHooks} from 'node:module';
import {readdirSync, readFileSync} from 'node:fs';
import path from 'node:path';
registerHooks({resolve: (spec, ctx, next) => ctx.parentURL?.includes('/src/v3/') && /^\.{1,2}\//.test(spec) && !/\.[cm]?[jt]sx?$/.test(spec) ? next(spec + '.ts', ctx) : next(spec, ctx)});
const {buildWalkPdf} = await import('../../src/v3/lib/walkPdf.ts');
type PdfDoc = Awaited<ReturnType<typeof buildWalkPdf>>['doc'];

/** The strings a page shows, read back from its content stream and decoded as Windows-1252 (what the standard fonts use). */
const pdfTexts = (doc: PdfDoc, page: number): string[] => {
  const win = new TextDecoder('windows-1252');
  const stream = ((doc as unknown as {internal: {pages: (string[] | undefined)[]}}).internal.pages[page] ?? []).join('\n');
  return [...stream.matchAll(/\(((?:[^()\\]|\\.)*)\) Tj/g)].map(m => win.decode(Buffer.from(m[1].replace(/\\([()\\])/g, '$1'), 'latin1')));
};
const pdfPages = (doc: PdfDoc) => Array.from({length: doc.getNumberOfPages()}, (_, i) => pdfTexts(doc, i + 1));
const manyDoors = (n: number, tag = 'a') => Array.from({length: n}, (_, i) => door(i + 1, String(100 + 2 * i), Array.from({length: 1 + (i % 5)}, (_, k) => voter({person_id: `${tag}${i}-${k}`, name: `Voter ${tag}${i}-${k}`}))));

test('PDF: landscape letter, a header on every list page, (cont.) only after the first page of a run, page numbers and legend', async () => {
  const {doc, fileName} = await buildWalkPdf(payload([run({doors: manyDoors(40)}), run({stop: 2, street_id: 9, street: 'Short St', doors: [door(50, '5', [voter()])]})]));
  const pages = pdfPages(doc), n = pages.length;
  assert.equal(fileName, 'walk-list-precinct-02012-2026-10-02.pdf');
  assert.equal(doc.internal.pageSize.getWidth(), 792); assert.equal(doc.internal.pageSize.getHeight(), 612);
  assert.ok(n >= 4, 'cover, several pages for the long run, one for the short run');
  assert.ok(pages[0].includes('Saturday') && pages[0].includes('Door script'));
  pages.forEach((t, i) => {
    assert.ok(t.includes(`Page ${i + 1} of ${n}`), `page ${i + 1} number`);
    assert.ok(t.includes('S Supporter · U Undecided · O Opposed · NH Not home · MV Moved · Abs = mail ballots'), `page ${i + 1} legend`);
    assert.ok(t.includes('10/2/2026'), `page ${i + 1} date`);
    if (i > 0) for (const h of ['Address', 'Name', 'Phone', 'Pri', 'Gen', 'Abs', 'Party', 'Age', 'Gender', 'Reg. Date', 'VOTER HISTORY', 'VOTER INFORMATION']) assert.ok(t.includes(h), `page ${i + 1} ${h}`);
  });
  assert.ok(pages[1].includes('Street: N Cedar Heights Dr (Even)') && pages[1].includes('Precinct: 02012'));
  assert.deepEqual(pages.map(t => t.includes('(cont.)')), [false, false, ...Array(n - 3).fill(true), false]);
  assert.ok(pages[n - 1].includes('Street: Short St (Even)'));
});

test('no time estimate in any output: totals line, run header, CSV and every PDF string have no minutes, hours or "about"', async () => {
  // A list saved earlier still carries the server's `minutes` (160 here); nothing the walker reads or prints may turn it into "about 2 h 40 min" or the like.
  const ESTIMATE = /\babout\s+\d|\b\d+\s*(?:h|hrs?|hours?|mins?|minutes?)\b|\bminutes?\b|\bmins?\b|\bhours?\b|\bestimated time\b|\bwalking time\b|\bETA\b/i;
  const ABOUT = /\babout\b/i;
  const totals = {streets: 1, stops: 2, doors: 2, voters: 2, walk_m: 2900, minutes: 160}, line = '1 street · 2 stops · 2 doors · 2 voters · 2.9 km';
  const runs = [run({doors: [door(1, '4102', [voter()])]}), run({stop: 2, street_id: 9, street: 'Short St', doors: [door(2, '5', [voter({person_id: 'p2'})])]})];
  const p = {...payload(runs), totals};
  const clean = (label: string, texts: string[]) => assert.deepEqual(texts.filter(t => ESTIMATE.test(t) || ABOUT.test(t)), [], label);
  assert.equal(totalsLine(totals), line);
  clean('totals line', [totalsLine(totals), totalsLine({...totals, minutes: 0}), totalsLine({...totals, minutes: 3000})]);
  clean('run headers', runs.map(r => runHeader(r, 2)));
  clean('CSV columns', [...CSV_COLUMNS]);
  clean('CSV rows', walkCsvRows(p).flat().concat(buildWalkCsv(p).split('\r\n')));
  // The door script on the cover says "neighbors about the coming election": that copy is exempt, every other PDF string is not.
  const flat = (s: string) => s.replace(/[“”]/g, '').replace(/\s+/g, ' ').trim();
  const script = flat(DOOR_SCRIPT.flatMap(s => [s.title, s.line, ...s.choices.map(choiceText)]).join(' '));
  const {doc} = await buildWalkPdf(p), strings = pdfPages(doc).flat();
  assert.ok(pdfTexts(doc, 1).includes(line), 'the cover shows the totals line as it is');
  assert.ok(strings.length > 50, 'the whole document was read');
  assert.deepEqual(strings.filter(t => ESTIMATE.test(t)), [], 'PDF time estimates');
  assert.deepEqual(strings.filter(t => ABOUT.test(t) && !script.includes(flat(t))), [], 'PDF "about" outside the door script');
});

test('PDF: accents print as they are, missing glyphs fall back, nothing turns into garbage', async () => {
  const names = ['José Muñoz-Peña', 'Zoe\u{301} O’Brien', 'Jose\u{301} Mun\u{303}oz', 'Łukasz Đurđević', 'Nguyễn Văn Ánh', '李小龍 Lee', 'Emoji \u{1f600} Person', 'Line\nBreak', 'Tab\tName', 'Mary\u{2011}Ann', 'x\u{301}y'];
  const doors = names.map((name, i) => door(i + 1, String(10 + 2 * i), [voter({name})]));
  const {doc} = await buildWalkPdf({...payload([run({doors, street: 'Calle Año Nuevo – Östra'})]), list: {key: null, title: 'Ünïcode “list” – Tést', kind: 'standard', created_at: null}});
  const list = pdfPages(doc)[1];
  for (const want of ['José Muñoz-Peña', 'Zoé O’Brien', 'José Muñoz', 'Lukasz Durdevic', 'Nguyen Van Ánh', '??? Lee', 'Emoji ? Person', 'Line Break', 'Tab Name', 'Mary-Ann', 'xy', 'Street: Calle Año Nuevo – Östra (Even)']) assert.ok(list.includes(want), want);
  assert.ok(pdfPages(doc)[0].includes('Ünïcode “list” – Tést'));
});

test('PDF: empty lists, runs without a path, doors without voters, odd coordinates and very long text all build', async () => {
  assert.equal((await buildWalkPdf(payload([]))).doc.getNumberOfPages(), 1, 'a list with no runs is just the cover');
  const long = 'Alexandria Catherine Montgomery-Featherstonehaugh-Cholmondeley The Third';
  const odd = [
    run({path: [], approach: [], doors: [{...door(1, '10', [voter()]), lon: NaN, lat: NaN}]}),
    run({stop: 2, street_id: 8, path: [[-104.8, 38.8]], approach: [[-104.8, 38.8]], doors: [door(2, '12', [])]}),
    run({stop: 3, street_id: 9, doors: []}),
    run({stop: 4, street_id: 10, street: 'North Extraordinarily Long Boulevard Name Parkway Extension West', precinct: 'PRECINCT-OF-A-VERY-LONG-NAME-0012345',
      doors: [door(3, '4102', [voter({name: long, phone: '+1 (719) 555-0142 ext. 99999 and more digits'})], 'Building C Suite 204 West Wing Basement Level')]}),
  ];
  const {doc} = await buildWalkPdf({...payload(odd), list: {key: null, title: 'A very long title '.repeat(10), kind: 'reknock', created_at: null}, generated_on: ''});
  const pages = pdfPages(doc);
  assert.equal(pages.length, 3, 'cover, the first run and the long one; no page for a door without voters or a run without doors');
  assert.ok(pages[2].some(t => t.startsWith('Alexandria') && t.endsWith('...') && t.length < long.length), 'a long name is cut with an ellipsis');
  assert.ok(pages[2].some(t => t.startsWith('4102 Building') && t.endsWith('...')), 'a long unit is cut with an ellipsis');
  assert.ok(pages[2].some(t => t.startsWith('Street: North Extraordinarily') && t.endsWith('...')), 'a long street label is cut with an ellipsis');
  assert.ok(pages[2].some(t => t.startsWith('Precinct: PRECINCT-OF') && t.endsWith('...')), 'a long precinct label is cut with an ellipsis');
});

test('PDF: 1,500 voters all print, in walking order', async () => {
  let n = 0;
  const runs = Array.from({length: 6}, (_, r) => run({stop: r + 1, street_id: r + 1, street: `Street ${r + 1}`, doors: Array.from({length: 125}, (_, d) =>
    door(r * 125 + d, String(100 + 2 * d), Array.from({length: 2}, () => { const i = n++; return voter({person_id: `p${i}`, name: `Voter ${String(i).padStart(4, '0')}`}); })))}));
  const {doc} = await buildWalkPdf(payload(runs));
  const names = pdfPages(doc).flat().filter(t => /^Voter \d{4}$/.test(t));
  assert.equal(names.length, 1500); assert.deepEqual(names, [...names].sort(), 'names appear in the order they were given');
  assert.ok(doc.getNumberOfPages() > 30);
});

test('PDF libraries load on demand: nothing under src/v3 imports jspdf except as a type or through import()', () => {
  const root = path.resolve(import.meta.dirname, '../../src/v3'), files: string[] = [];
  (function walk(d: string) { for (const e of readdirSync(d, {withFileTypes: true})) { const p = path.join(d, e.name); if (e.isDirectory()) walk(p); else if (/\.tsx?$/.test(e.name)) files.push(p); } })(root);
  const staticImport = /^\s*(?:import|export)\s+(?!type\b)[^;'"]*?from\s*['"]jspdf[^'"]*['"]|^\s*import\s*['"]jspdf[^'"]*['"]/m;
  assert.deepEqual(files.filter(f => staticImport.test(readFileSync(f, 'utf8'))).map(f => path.relative(root, f)), []);
  const pdf = readFileSync(path.join(root, 'lib/walkPdf.ts'), 'utf8');
  assert.ok(/import\(\s*'jspdf'\s*\)/.test(pdf) && /import\(\s*'jspdf-autotable'\s*\)/.test(pdf), 'walkPdf.ts loads both libraries with import()');
});

// --- map overlay: roads, precinct lines, homes and names round the route on the cover map ------------------------------------
import {boxesHit, fitFrame, labelPoint, metresPerDegreeLat, viewBounds} from '../../src/v3/lib/walkGeo.ts';
import type {WalkContext} from '../../src/v3/api/walkTypes.ts';

type LL = [number, number];
const at = (x: number, y: number): LL => [-104.82 + x / 86500, 38.83 + y / 111000];   // metres east and north of a point near Colorado Springs
const boxOf = (a: LL, b: LL): [number, number, number, number] => [a[0], a[1], b[0], b[1]];
const context = (): WalkContext => ({
  bbox: boxOf(at(-300, -300), at(300, 300)),
  roads: [
    {k: 'S1400', n: 'Oak St', c: [at(-250, 0), at(250, 0)]}, {k: 'S1400', n: 'Elm Ave', c: [at(0, -250), at(0, 250)]},
    {k: 'S1200', n: 'Mock Blvd', c: [at(-280, 150), at(280, 150)]}, {k: 'V', n: '', c: [at(100, 0), at(100, 100)]},
  ],
  precincts: [
    {id: '02012', label: '02012', rings: [[at(-400, -400), at(100, -400), at(100, 400), at(-400, 400), at(-400, -400)]]},
    {id: '02013', label: '02013', rings: [[at(100, -400), at(400, -400), at(400, 400), at(100, 400), at(100, -400)]]},
  ],
  homes: [at(-50, 20), at(-20, 20), at(10, 20), at(40, -20)],
});
const mapRun = () => run({path: [at(-200, 0), at(200, 0)], doors: [{...door(1, '4102', [voter()]), lon: at(-100, 14)[0], lat: at(-100, 14)[1]}]});
const mapPayload = (ctx?: WalkContext): WalkListPayload => ({...payload([mapRun()]), bbox: boxOf(at(-200, -14), at(200, 14)), ...(ctx ? {context: ctx} : {})});
/** Page 1 as one string; the operators of the cover map (moves, strokes, fills, colours) can be counted in it. */
const cover = (doc: PdfDoc) => ((doc as unknown as {internal: {pages: string[][]}}).internal.pages[1]).join('\n');
const count = (text: string, re: RegExp) => (text.match(re) ?? []).length;

test('PDF map: builds with and without a geographic context, and draws roads, precinct lines, homes and names when it has one', async () => {
  const bare = await buildWalkPdf(mapPayload()), full = await buildWalkPdf(mapPayload(context()));
  assert.equal(bare.doc.getNumberOfPages(), full.doc.getNumberOfPages());
  assert.deepEqual([bare.map.from, bare.map.roads, bare.map.precincts, bare.map.homes, bare.map.doors, bare.map.stops], ['list', {S1200: 0, S1400: 0, V: 0}, 0, 0, 1, 1]);
  assert.deepEqual([full.map.from, full.map.roads, full.map.precincts, full.map.homes, full.map.doors, full.map.stops], ['context', {S1200: 1, S1400: 2, V: 1}, 2, 4, 1, 1]);
  const a = cover(bare.doc), b = cover(full.doc);
  // each road is a casing and a white line, each home a dot, each precinct an outline and the listed one a fill; the frame clips it all
  assert.ok(count(b, / m\n/g) - count(a, / m\n/g) >= 2 * 4 - 1 + 2 + 1 + 4, 'moves added by the context');
  assert.ok(/\nW\nn\n/.test(b) && /\nW\nn\n/.test(a), 'drawing is clipped to the frame');
  assert.ok(b.includes('0.71 0.68 0.64 RG') && !a.includes('0.71 0.68 0.64 RG'), 'homes are #B4ADA3 dots, only with a context');
  assert.ok(b.includes('0.89 0.86 0.83 RG') && b.includes('0.97 0.93 0.89 rg') && b.includes('0.93 0.91 0.87 rg'), 'road casing, listed-precinct fill and land colours');
  assert.equal(count(b, /\nf\*\n/g), 1, 'one precinct is on the list, so one is shaded');
  assert.ok(/\[3\.2 2\.4\] 0\.? d/.test(b) && b.includes('0.34 0.27 0.48 RG'), 'precinct lines are dashed in the walk purple');
  const kinds = (m: typeof full.map) => m.labels.map(l => `${l.kind}:${l.text}`).sort();
  assert.deepEqual(kinds(full.map), ['precinct:02012', 'precinct:02013', 'road:Mock Blvd', 'street:N Cedar Heights Dr']);
  assert.deepEqual(kinds(bare.map), ['street:N Cedar Heights Dr']);
  const texts = pdfTexts(full.doc, 1);
  for (const t of ['02012', '02013', 'Mock Blvd', 'N Cedar Heights Dr', 'Walk', 'Between stops', 'Doors', 'Homes', 'Precinct', 'Stop']) assert.ok(texts.includes(t), t);
  assert.ok(!pdfTexts(bare.doc, 1).includes('Homes') && !pdfTexts(bare.doc, 1).includes('Precinct'), 'the legend names only what the map shows');
});

test('PDF map: the box is the context box, else the list box, else the route, and always holds the whole route', () => {
  const p = mapPayload(context());
  assert.deepEqual(viewBounds(p), {bounds: context().bbox, from: 'context'});
  assert.deepEqual(viewBounds({...p, context: undefined}), {bounds: p.bbox, from: 'list'});
  const route = viewBounds({...p, context: undefined, bbox: [0, 0, 0, 0]});
  assert.equal(route?.from, 'route'); assert.ok(route && route.bounds[0] <= at(-200, 0)[0] && route.bounds[2] >= at(200, 0)[0]);
  const small = viewBounds({...p, context: {...context(), bbox: boxOf(at(-10, -10), at(10, 10))}});
  assert.ok(small && small.from === 'context' && small.bounds[0] <= at(-200, 0)[0] && small.bounds[2] >= at(200, 0)[0], 'a context box that misses part of the route is widened');
  assert.equal(viewBounds({...p, context: undefined, bbox: [1, 2, 0, 3], runs: []}), null);
});

test('PDF map: one scale in both directions, east-west degrees shrunk by cos(latitude)', () => {
  const frame = {x: 36, y: 158, w: 410, h: 388}, b: [number, number, number, number] = [-104.82, 38.83, -104.81, 38.836];
  const fit = fitFrame(b, frame, 4), sw = fit.project([b[0], b[1]]), se = fit.project([b[2], b[1]]), nw = fit.project([b[0], b[3]]);
  const lat = (b[1] + b[3]) / 2, east = se[0] - sw[0], north = sw[1] - nw[1];
  assert.ok(Math.abs(east / north - (0.01 * Math.cos(lat * Math.PI / 180)) / 0.006) < 1e-9, 'the shape of the box is kept');
  // 100 m east and 100 m north are the same length on the page
  const m = 111320 * Math.cos(lat * Math.PI / 180), o = fit.project([-104.815, 38.833]);
  const e = fit.project([-104.815 + 100 / m, 38.833]), n = fit.project([-104.815, 38.833 + 100 / metresPerDegreeLat(38.833)]);
  assert.ok(Math.abs((e[0] - o[0]) / (o[1] - n[1]) - 1) < 5e-3, '100 m east and north match to the 0.3% that spherical and ellipsoidal degrees differ by');
  assert.ok(Math.abs(fit.metresPerUnit * (e[0] - o[0]) - 100) < 0.5, 'the scale bar length follows from the same scale');
  const [w, s, ee, nn] = fit.view, c1 = fit.project([w, nn]), c2 = fit.project([ee, s]);
  assert.ok(Math.abs(c1[0] - frame.x) < 1e-6 && Math.abs(c1[1] - frame.y) < 1e-6 && Math.abs(c2[0] - frame.x - frame.w) < 1e-6 && Math.abs(c2[1] - frame.y - frame.h) < 1e-6, 'the view is what the frame shows');
  assert.ok(sw[0] >= frame.x + 4 - 1e-9 && se[0] <= frame.x + frame.w - 4 + 1e-9 && sw[1] <= frame.y + frame.h - 4 + 1e-9 && nw[1] >= frame.y + 4 - 1e-9, 'the pad is kept');
});

test('PDF map: every name stays inside the frame and none sit on another', async () => {
  const tight = {...context(), bbox: boxOf(at(-60, -20), at(60, 20))};   // a tight box puts the street name against an edge
  const many = {...context(), precincts: Array.from({length: 6}, (_, i) => ({id: `0${i}`, label: `PRECINCT-${i}-WITH-A-LONG-CODE`, rings: [[at(-500, -500 + i * 40), at(500, -500 + i * 40), at(500, 600), at(-500, 600), at(-500, -500 + i * 40)]]}))};
  // every fixture is named and checked for overlaps; `least` is how many names it must put down, so the pairs checked are real ones
  const cases: [string, WalkContext | undefined, number][] = [['ordinary', context(), 4], ['tight', tight, 4], ['many', many, 7], ['none', undefined, 1]];
  for (const [name, ctx, least] of cases) {
    const {map} = await buildWalkPdf(mapPayload(ctx)), f = map.frame;
    assert.ok(map.labels.length >= least, `${name}: ${map.labels.length} names`);
    for (const l of map.labels) for (const [x, y] of l.corners) assert.ok(x >= f.x && x <= f.x + f.w && y >= f.y && y <= f.y + f.h, `${name}: ${l.kind} ${l.text} inside the frame`);
    for (let i = 0; i < map.labels.length; i++) for (let j = i + 1; j < map.labels.length; j++) assert.ok(!boxesHit(map.labels[i].corners, map.labels[j].corners), `${name}: ${map.labels[i].text} / ${map.labels[j].text}`);
  }
  const {map} = await buildWalkPdf(mapPayload(context()));
  for (let i = 0; i < map.labels.length; i++) for (let j = i + 1; j < map.labels.length; j++) assert.ok(!boxesHit(map.labels[i].corners, map.labels[j].corners), `${map.labels[i].text} / ${map.labels[j].text}`);
});

test('PDF map: precinct label points fall inside the part of the area that shows, even for a bent shape', () => {
  const ring = (pts: [number, number][]): [number, number][] => [...pts, pts[0]];
  const view: [number, number, number, number] = [0, 0, 10, 10];
  assert.deepEqual(labelPoint([ring([[-10, -10], [5, -10], [5, 5], [-10, 5]])], view), [2.5, 2.5], 'the shown quarter of a bigger area');
  const bent = labelPoint([ring([[0, 0], [10, 0], [10, 2], [2, 2], [2, 10], [0, 10]])], view);
  assert.ok(bent && bent[0] <= 2 && bent[1] > 2, 'a point inside the L, not in its notch');
  assert.equal(labelPoint([ring([[20, 20], [30, 20], [30, 30], [20, 30]])], view), null, 'an area out of view has no label');
  assert.equal(labelPoint(undefined, view), null);
});

test('PDF map: odd context data is skipped and the PDF still builds', async () => {
  const odd = {
    bbox: [NaN, 0, 1, 1], roads: [{k: 'S1400', n: 'No Points', c: []}, {k: 'S9999', n: 'Unknown', c: [at(0, 0), at(10, 0)]}, {k: 'S1400', n: 'Bad', c: [[NaN, 1], [2, NaN]]}, null, {k: 'S1400', n: 'Good St', c: [at(-100, 0), at(100, 0)]}],
    precincts: [{id: 'x', label: 'x', rings: []}, {id: 'y', label: 'y'}, null], homes: [[NaN, 1], null, 'x', at(0, 5)],
  } as unknown as WalkContext;
  const {map} = await buildWalkPdf(mapPayload(odd));
  assert.deepEqual([map.roads, map.precincts, map.homes], [{S1200: 0, S1400: 1, V: 0}, 0, 1]);
  for (const junk of [null, {roads: 'no', precincts: 5, homes: {}}, {}]) assert.equal((await buildWalkPdf(mapPayload(junk as unknown as WalkContext))).map.roads.S1400, 0);
});

// --- upload field limits, re-knock lists, city case, unload guard, area-only reload, upload identity -------------------------
import {
  MATCH_LIMITS, chipNames, drainResults, isReknock, leftOutNote, patchUploadRow, pickName, pickerOf, readWalkAddress, readWalkCsv, reknockParent,
  resultOutbox, savedLine, startPlan
} from '../../src/v3/lib/walk.ts';

test('upload limits: the server takes 120, 40, 60 and 20 characters', () => {
  assert.deepEqual({...MATCH_LIMITS}, {street: 120, side: 40, city: 60, zip: 20});
});

test('upload limits: a cell over its limit leaves that row out with a message and the other rows go on', () => {
  const text = ['street,side,city,zip', 'Main St,odd,Denver,80918', `${'x'.repeat(121)},even,,`, 'Oak St,,,', `Elm Ave,,${'c'.repeat(61)},`, `Pine St,,,${'9'.repeat(21)}`, `Fir St,${'n'.repeat(41)},,`].join('\n');
  const got = readWalkCsv(text);
  assert.deepEqual(got.rows, [{street: 'Main St', side: 'odd', city: 'Denver', zip: '80918'}, {street: 'Oak St'}]);
  assert.deepEqual(got.problems, [
    {row: 3, message: 'Row 3: street name is too long'}, {row: 5, message: 'Row 5: city is too long'},
    {row: 6, message: 'Row 6: ZIP code is too long'}, {row: 7, message: 'Row 7: side is too long'}
  ]);
  assert.deepEqual(parseWalkCsv(text), got.rows, 'parseWalkCsv gives the rows that can be sent');
});

test('upload limits: values exactly at a limit pass; spaces around or inside a value do not count; a long side word that reads as a side is sent short', () => {
  const at = `${'s'.repeat(120)},${'n'.repeat(40)},${'c'.repeat(60)},${'9'.repeat(20)}`;
  const exact = readWalkCsv('street,side,city,zip\n' + at);
  assert.deepEqual(exact.problems, []); assert.equal(exact.rows.length, 1);
  const padded = readWalkCsv(`street,city\n"${' '.repeat(9)}${'s'.repeat(120)}${' '.repeat(9)}","${' '.repeat(30)}Denver${' '.repeat(30)}"\n"a${' '.repeat(300)}b",`);
  assert.deepEqual(padded.problems, []); assert.deepEqual(padded.rows.map(r => r.street.length), [120, 3]); assert.equal(padded.rows[0].city, 'Denver');
  const side = readWalkCsv('street,side\nMain St,even' + '-'.repeat(60) + 'side');
  assert.deepEqual(side.problems, []); assert.equal(side.rows[0].side, 'even');
});

test('upload limits: characters are counted the way the server counts them, one per code point', () => {
  const face = '\u{1F600}';   // two UTF-16 units, one character
  assert.equal(readWalkCsv('street,city\nMain St,' + face.repeat(60)).problems.length, 0);
  assert.deepEqual(readWalkCsv('street,city\nMain St,' + face.repeat(61)).problems.map(p => p.message), ['Row 2: city is too long']);
});

test('upload limits: every long field of a row is named', () => {
  const x = 'x'.repeat(121), c = 'c'.repeat(61), z = '9'.repeat(21);
  const got = readWalkCsv(`street,city,zip\n${x},${c},1\n${x},${c},${z}\n${x},Denver,1`);
  assert.deepEqual(got.problems.map(p => p.message), ['Row 2: street name and city are too long', 'Row 3: street name, city and ZIP code are too long', 'Row 4: street name is too long']);
});

test('upload limits: row numbers are the ones a spreadsheet shows, header, blank lines and multi-line cells included', () => {
  const long = 'x'.repeat(121);
  assert.deepEqual(readWalkCsv(`street\nMain St\n\n"Elm\nAve"\n${long}\n`).problems, [{row: 5, message: 'Row 5: street name is too long'}]);
  assert.deepEqual(readWalkCsv(`Main St\n${long}`).problems.map(p => p.row), [2], 'without a header the first line is row 1');
  assert.deepEqual(readWalkCsv(`\n\nstreet\n${long}`).problems.map(p => p.row), [4], 'blank lines above the header count');
  assert.deepEqual(readWalkCsv(`street,city\n,${'c'.repeat(61)}\nMain St,Denver`).problems, [], 'a row with no street is skipped, not reported');
  assert.deepEqual(splitCsv('a\n\nb'), [['a'], ['b']], 'splitCsv still drops blank lines');
});

test('upload limits: only rows that can be sent count toward the 500-row limit, and a file of long rows sends nothing', () => {
  const ok = Array.from({length: MAX_UPLOAD_ROWS}, (_, i) => `Street ${i}`), bad = Array.from({length: 7}, () => 'x'.repeat(121));
  const got = readWalkCsv(['street', ...ok, ...bad].join('\n'));
  assert.equal(got.rows.length, MAX_UPLOAD_ROWS); assert.equal(got.problems.length, 7);
  assert.throws(() => readWalkCsv(['street', ...ok, 'One more'].join('\n')), /500/);
  const none = readWalkCsv(['street', ...bad].join('\n'));
  assert.deepEqual(none.rows, []); assert.equal(none.problems.length, 7);
});

test('upload limits: the note names the first rows and counts the rest', () => {
  assert.equal(leftOutNote([]), null);
  const many = Array.from({length: 8}, (_, i) => ({row: i + 2, message: `Row ${i + 2}: city is too long`}));
  assert.deepEqual(leftOutNote(many.slice(0, 1)), {headline: '1 row left out of this upload:', reasons: ['Row 2: city is too long']});
  const note = leftOutNote(many);
  assert.equal(note?.headline, '8 rows left out of this upload:'); assert.equal(note?.reasons.length, 6); assert.equal(note?.reasons[0], 'Row 2: city is too long'); assert.equal(note?.reasons[5], 'and 3 more');
  assert.deepEqual(leftOutNote(many.slice(0, 5))?.reasons.length, 5);
});

const listOf = (over: Partial<WalkListPayload['list']> = {}, query: Record<string, string> = {}): Pick<WalkListPayload, 'list' | 'query'> =>
  ({list: {key: 'k1', title: 'Saturday', kind: 'standard', created_at: '2026-10-03T09:00:00-06:00', ...over}, query});

test('re-knock lists: both query shapes are recognised and neither puts streets or an area back in the picker', () => {
  const standard = listOf({}, {streets: '12:even', precinct: '02012'});
  const fresh = listOf({kind: 'reknock', title: 'Not home again · Saturday'}, {from: 'abc123', reknock: '1'});
  const legacy = listOf({kind: 'reknock'}, {streets: '12:even', precinct: '02012'});
  const odd = listOf({kind: 'standard'}, {from: 'abc123', streets: '12:even'});
  const all = [standard, fresh, legacy, odd];
  assert.deepEqual(all.map(isReknock), [false, true, true, true]); assert.deepEqual(all.map(reknockParent), ['', 'abc123', '', 'abc123']);
  assert.deepEqual(pickerOf(standard), {area: {ss: '', sh: '', precinct: '02012'}, streets: '12:even'});
  assert.equal(pickerOf(fresh), null); assert.equal(pickerOf(legacy), null); assert.equal(pickerOf(odd), null);
  assert.deepEqual(pickerOf(listOf({}, {ss: '12'})), {area: {ss: '12', sh: '', precinct: ''}, streets: ''}, 'an area alone is a picker setting');
  assert.equal(pickerOf(listOf({}, {})), null); assert.equal(pickerOf({list: standard.list, query: undefined as unknown as Record<string, string>}), null);
});

test('re-knock lists: the line above a list names the list it came from, by title or else by key', () => {
  const lists = [{key: 'abc123', title: 'Saturday'}, {key: 'zzz', title: 'Other'}];
  const fresh = listOf({kind: 'reknock', title: 'Not home again · Saturday'}, {from: 'abc123', reknock: '1'});
  assert.equal(savedLine(fresh, lists), 'Not home again · from Saturday · 10/3/2026');
  assert.equal(savedLine(fresh, []), 'Not home again · from abc123 · 10/3/2026'); assert.equal(savedLine(fresh), 'Not home again · from abc123 · 10/3/2026');
  assert.equal(savedLine(fresh, [{key: 'abc123', title: 'Precinct 02012 · 10/3/2026'}]), 'Not home again · from Precinct 02012 · 10/3/2026', 'a date already in the title is not repeated');
  assert.equal(savedLine(listOf({}, {precinct: '02012'}), lists), 'Saved list · Saturday · 10/3/2026');
  assert.equal(savedLine(listOf({title: 'Precinct 02012 · 10/3/2026'})), 'Saved list · Precinct 02012 · 10/3/2026');
  assert.equal(savedLine(listOf({created_at: null})), 'Saved list · Saturday');
  assert.equal(savedLine(listOf({kind: 'reknock', title: 'Not home again · Saturday'}, {streets: '1:odd'})), 'Re-knock list · Not home again · Saturday · 10/3/2026', 'an older re-knock list has no parent key to name');
});

test('city case: chips add the city only when two share a label, labels compare without case, a city shows as the server spelled it', () => {
  const c = (id: number, label: string, city: string) => ({id, label, city});
  assert.deepEqual(chipNames([c(1, 'Pine St', 'Colorado Springs'), c(2, 'Pine St', 'Fountain'), c(3, 'Oak St', 'Colorado Springs')]), ['Pine St (Colorado Springs)', 'Pine St (Fountain)', 'Oak St']);
  assert.deepEqual(chipNames([c(1, 'Pine St', 'Colorado Springs'), c(2, 'PINE ST', 'COLORADO SPRINGS')]), ['Pine St (Colorado Springs)', 'PINE ST (COLORADO SPRINGS)']);
  assert.deepEqual(chipNames([c(1, 'Pine St', ''), c(2, 'pine st', '')]), ['Pine St', 'pine st'], 'with no city there is nothing to add');
  assert.deepEqual(chipNames([c(7, '', ''), c(8, 'Oak St', 'Fountain')]), ['Street 7', 'Oak St']); assert.equal(pickName(c(7, '', '')), 'Street 7');
  assert.deepEqual(parseWalkCsv('street,city\nPine St,COLORADO SPRINGS\nPine St,colorado springs'), [{street: 'Pine St', city: 'COLORADO SPRINGS'}, {street: 'Pine St', city: 'colorado springs'}], 'a typed city goes to the server as typed');
});

test('unsaved results: a batch counts until its request is answered, so a closing page is warned while one is in flight', async () => {
  const outbox = resultOutbox<string, string | null>();
  assert.ok(!outbox.unconfirmed()); outbox.set('a', 'S'); assert.ok(outbox.unconfirmed(), 'waiting counts');
  const states: string[] = []; let release!: () => void;
  const done = drainResults(outbox, {ready: () => true, send: () => new Promise<void>(r => { release = r; }), report: s => states.push(s)});
  assert.equal(outbox.waiting.size, 0, 'the request has taken the result'); assert.ok(outbox.unconfirmed(), 'and it is still unconfirmed while the request is open');
  release(); assert.equal(await done, true);
  assert.ok(!outbox.unconfirmed()); assert.deepEqual(states, ['saving', 'saved']);
});

test('unsaved results: a failed request puts its results back, keeps newer ones, and the page stays unconfirmed', async () => {
  const outbox = resultOutbox<string, string | null>(); outbox.set('a', 'S'); outbox.set('b', 'NH');
  const reports: [string, string | undefined][] = []; let fail!: (e: Error) => void;
  const done = drainResults(outbox, {ready: () => true, send: () => new Promise<void>((_, rej) => { fail = rej; }), report: (s, m) => reports.push([s, m])});
  outbox.set('a', 'U'); assert.ok(outbox.unconfirmed());
  fail(new Error('offline')); assert.equal(await done, false);
  assert.deepEqual([...outbox.waiting], [['a', 'U'], ['b', 'NH']], 'the newer U for a wins over the S that failed');
  assert.ok(outbox.unconfirmed()); assert.deepEqual(reports, [['saving', undefined], ['error', 'offline']]);
  const retry = await drainResults(outbox, {ready: () => true, send: async () => {}, report: () => {}});
  assert.equal(retry, true); assert.ok(!outbox.unconfirmed());
});

test('unsaved results: at most 200 a request, one request at a time, nothing sent before a list is on screen, a cleared outbox forgets what waits', async () => {
  const outbox = resultOutbox<string, string | null>();
  for (let i = 0; i < 450; i++) outbox.set(`p${i}`, 'S');
  assert.equal(await drainResults(outbox, {ready: () => false, send: async () => { throw new Error('no list'); }, report: () => {}}), true);
  assert.equal(outbox.waiting.size, 450);
  const sizes: number[] = [], unconfirmed: boolean[] = [];
  assert.equal(await drainResults(outbox, {ready: () => true, send: async b => { sizes.push(b.size); unconfirmed.push(outbox.unconfirmed()); }, report: () => {}}), true);
  assert.deepEqual(sizes, [200, 200, 50]); assert.ok(unconfirmed.every(Boolean)); assert.ok(!outbox.unconfirmed());
  outbox.set('x', null); outbox.clear(); assert.ok(!outbox.unconfirmed());
  outbox.set('y', 'S'); const taken = outbox.take(); outbox.clear(); assert.ok(outbox.unconfirmed(), 'a request already sent still counts after clear'); outbox.confirm(taken); assert.ok(!outbox.unconfirmed());
  assert.equal(outbox.take().size, 0); assert.ok(!outbox.unconfirmed(), 'taking nothing leaves nothing open');
});

test('page address: an area alone, streets alone or a saved list rebuilds on load', () => {
  const plan = (qs: string) => startPlan(readWalkAddress(new URLSearchParams(qs)));
  for (const qs of ['precinct=02012', 'ss=12', 'sh=45&precinct=02012', 'streets=12:even', 'streets=12:even&precinct=02012']) assert.equal(plan(qs), 'build', qs);
  for (const qs of ['list=ab12cd34ef', 'list=ab12cd34ef&precinct=02012&streets=1:odd']) assert.equal(plan(qs), 'saved', qs);
  for (const qs of ['', 'precinct=%20%20', 'streets=x:y', 'list=bad/key', 'other=1']) assert.equal(plan(qs), 'none', qs);
  assert.deepEqual(readWalkAddress(new URLSearchParams('ss=12&precinct=02012&streets=3:odd&list=k_1-a')), {area: {ss: '12', sh: '', precinct: '02012'}, picks: [{id: 3, side: 'odd', label: '', city: ''}], list: 'k_1-a'});
});

test('upload identity: an answer for an earlier upload changes nothing (rematch on A, clear, load B, A answers late)', () => {
  type Row = {street: string; busy: boolean}; type Up = {id: number; name: string; rows: Row[]};
  const a: Up = {id: 1, name: 'a.csv', rows: [{street: 'Pine St', busy: true}]};
  const b: Up = {id: 2, name: 'b.csv', rows: [{street: 'Oak St', busy: false}]};
  let shown: Up | null = a;                                                       // the screen shows A; a rematch of its row 0 is open
  shown = null; shown = b;                                                        // A is cleared and B is loaded
  assert.equal(patchUploadRow(shown, 1, 0, {street: 'Pine St Rd', busy: false}), b, 'the late answer leaves B as it was');
  assert.equal(patchUploadRow(null, 1, 0, {busy: false}), null, 'a cleared upload stays cleared');
  const now = patchUploadRow(a, 1, 0, {busy: false});
  assert.deepEqual(now, {id: 1, name: 'a.csv', rows: [{street: 'Pine St', busy: false}]}); assert.equal(a.rows[0].busy, true, 'the input is not changed');
  assert.deepEqual(patchUploadRow(a, 1, 5, {busy: false}), a, 'a row that is not there changes nothing');
});

test('walk screen wiring: the unload guard, the startup plan, the rematch check and the CSV report use the helpers above', () => {
  const src = readFileSync(path.join(import.meta.dirname, '../../src/v3/components/WalkScreen.tsx'), 'utf8');
  assert.ok(/const warn=\(e:BeforeUnloadEvent\)=>\{if\(outbox\.unconfirmed\(\)\|\|saving\.current\|\|refusals\.note\|\|shownFlagged\.current\.size>0\)/.test(src), 'the unload guard asks the outbox, and while a refusal note is up or the list shown has a flagged mark');
  assert.ok(/startPlan\(init\)/.test(src) && !/init\.picks\.length\)generate/.test(src), 'startup follows the plan, which includes an area alone');
  assert.ok(/const current=\(\)=>uploadRef\.current\?\.id===id/.test(src) && /if\(!current\(\)\)return;/.test(src), 'a rematch answer is dropped once its upload is gone');
  assert.ok(/pickerOf\(p\)/.test(src) && !/k in q/.test(src), 'a saved list restores the picker only through pickerOf');
  assert.ok(/readWalkCsv\(/.test(src) && /leftOutNote\(skipped\)/.test(src) && !/parseWalkCsv/.test(src), 'rows left out of an upload are reported');
  assert.ok(!/takeResults|pending\.current/.test(src), 'results go through the outbox');
});

// --- review round 4: results that outlive the screen, history apart from marks, one answer at a time, visit days, enclave labels -----
import {
  MAX_RESULTS_PER_REQUEST, confirmedClears, dropCleared, flushList, historyOf, lastLabel, lastOf, lastText, latestJob, listMarks, markAfterClick, resultShelf, takeResults, visitRequest
} from '../../src/v3/lib/walk.ts';
import type {WalkHistory, WalkVisit} from '../../src/v3/lib/walk.ts';
import {inArea, pointInRing} from '../../src/v3/lib/walkGeo.ts';
import type {Bounds, XY} from '../../src/v3/lib/walkGeo.ts';
import type {WalkCode} from '../../src/v3/api/walkTypes.ts';

const visit = (code: WalkVisit['code'], on = '2026-10-02'): WalkVisit => ({code, on});
const calm = {ready: () => true, report: () => {}};
const tick = () => new Promise<void>(r => setTimeout(r, 0));

test('visit days: a result keeps the day it was set, and a request holds one day, the oldest first', () => {
  const outbox = resultOutbox<string, WalkVisit>(v => v.on);
  outbox.set('a', visit('S')); outbox.set('b', visit('NH')); outbox.set('c', visit(null, '2026-10-03')); outbox.set('d', visit('U'));
  const first = outbox.take();
  assert.deepEqual([...first.keys()], ['a', 'b', 'd'], 'the oldest result sets the day; the next day waits for a request of its own');
  assert.deepEqual(visitRequest(first), {on: '2026-10-02', entries: [{person_id: 'a', code: 'S'}, {person_id: 'b', code: 'NH'}, {person_id: 'd', code: 'U'}]});
  assert.deepEqual(visitRequest(outbox.take()), {on: '2026-10-03', entries: [{person_id: 'c', code: null}]});
  assert.equal(outbox.take().size, 0);
  assert.throws(() => visitRequest(new Map([['a', visit('S')], ['b', visit('S', '2026-10-03')]])), /one day/);
  assert.deepEqual(visitRequest(new Map()), {on: '', entries: []});
  // without a grouping one request holds whatever waits (what the other outbox tests rely on); with one it never mixes groups, at the limit too
  const parity = () => new Map(Array.from({length: 450}, (_, i) => [`p${i}`, i % 2] as [string, number]));
  assert.equal(takeResults(parity()).size, MAX_RESULTS_PER_REQUEST);
  const batch = takeResults(parity(), MAX_RESULTS_PER_REQUEST, v => String(v));
  assert.equal(batch.size, 200); assert.ok([...batch.values()].every(v => v === 0));
});

test('visit days: a request that fails keeps its day for the retry, even after midnight, and a later result brings its own day', async () => {
  const outbox = resultOutbox<string, WalkVisit>(v => v.on); outbox.set('a', visit('S', '2026-10-02'));
  const sent: [string, string[]][] = []; let online = false;
  const io = {...calm, send: async (batch: Map<string, WalkVisit>) => { if (!online) throw new Error('offline'); const r = visitRequest(batch); sent.push([r.on, r.entries.map(e => e.person_id)]); }};
  assert.equal(await drainResults(outbox, io), false);
  outbox.set('b', visit('U', '2026-10-03'));   // the walker carries on after midnight
  online = true;
  assert.equal(await drainResults(outbox, io), true);
  assert.deepEqual(sent, [['2026-10-02', ['a']], ['2026-10-03', ['b']]], 'a request for each day, each with the day of its visits');
  assert.ok(!outbox.unconfirmed());
  outbox.set('a', visit('S', '2026-10-02')); outbox.set('a', visit('O', '2026-10-03'));   // a voter marked again on a later day is one visit of that day
  assert.deepEqual(visitRequest(outbox.take()), {on: '2026-10-03', entries: [{person_id: 'a', code: 'O'}]});
});

test('visit days: when the second day fails the first stays saved, and only the second is sent again', async () => {
  const outbox = resultOutbox<string, WalkVisit>(v => v.on); outbox.set('a', visit('S', '2026-10-02')); outbox.set('b', visit('U', '2026-10-03'));
  const sent: string[] = []; let failSecond = true;
  const io = {...calm, send: async (batch: Map<string, WalkVisit>) => { const {on} = visitRequest(batch); if (on === '2026-10-03' && failSecond) throw new Error('offline'); sent.push(on); }};
  assert.equal(await drainResults(outbox, io), false); assert.deepEqual(sent, ['2026-10-02']);
  assert.deepEqual([...outbox.waiting.keys()], ['b']); assert.ok(outbox.unconfirmed());
  failSecond = false;
  assert.equal(await drainResults(outbox, io), true); assert.deepEqual(sent, ['2026-10-02', '2026-10-03']);
});

test("unsaved results: holds() says whether a voter's result is still on its way, waiting or in a request", () => {
  const outbox = resultOutbox<string, WalkVisit>(v => v.on);
  assert.ok(!outbox.holds('a')); outbox.set('a', visit('S')); assert.ok(outbox.holds('a') && !outbox.holds('b'));
  const batch = outbox.take(); assert.ok(outbox.holds('a'), 'in a request that has not been answered');
  outbox.confirm(batch); assert.ok(!outbox.holds('a'));
  outbox.set('a', visit(null)); const again = outbox.take(); outbox.restore(again); assert.ok(outbox.holds('a'), 'back to waiting after a failure');
});

test('results outlive the screen: a save that fails after the walker leaves stays on the shelf, and coming back sends it', async () => {
  const shelf = resultShelf<string, WalkVisit>(v => v.on);
  const list = shelf.open({meta: null, title: 'Precinct 02012 · 10/2/2026', query: {precinct: '02012'}});
  assert.ok(!shelf.unconfirmed() && !shelf.held().length, 'a list that has come on screen holds nothing yet');
  shelf.keep(list); list.box.set('a', visit('S')); list.box.set('b', visit(null));
  assert.ok(shelf.unconfirmed(), 'results waiting count');
  // the screen is left, and the request that goes out as it closes fails
  assert.equal(await flushList(list, {...calm, send: async () => { throw new Error('offline'); }}), false);
  assert.deepEqual(shelf.held(), [list]); assert.ok(shelf.unconfirmed(), 'the page still has results to warn about');
  assert.deepEqual([...list.box.waiting], [['a', visit('S')], ['b', visit(null)]], 'both results are back in the outbox, with their days');
  assert.equal(list.title, 'Precinct 02012 · 10/2/2026'); assert.deepEqual(list.query, {precinct: '02012'}); assert.equal(list.meta, null);   // what saving the list again needs
  // another list is opened and marked meanwhile
  const other = shelf.open({meta: null, title: 'Other', query: {}}); shelf.keep(other); other.box.set('z', visit('U', '2026-10-03'));
  assert.deepEqual(shelf.held(), [list, other]);
  // back on the walk screen: the entries that are not on screen are sent again
  const sent: string[] = [];
  assert.equal(await flushList(list, {...calm, send: async b => { sent.push(`${visitRequest(b).on}:${[...b.keys()]}`); }}), true);
  assert.deepEqual(sent, ['2026-10-02:a,b']); assert.deepEqual(shelf.held(), [other], 'a list whose results are saved leaves the shelf');
  assert.equal(await flushList(other, {...calm, send: async () => {}}), true); assert.ok(!shelf.unconfirmed());
});

test('results outlive the screen: a result set while a request is in flight is not lost when that request fails', async () => {
  const shelf = resultShelf<string, WalkVisit>(v => v.on), list = shelf.open({meta: null, title: 't', query: {}}); shelf.keep(list);
  list.box.set('a', visit('S')); let fail!: (e: Error) => void;
  const done = flushList(list, {...calm, send: () => new Promise<void>((_, rej) => { fail = rej; })});
  await tick();
  assert.ok(list.box.unconfirmed(), 'the request holds the result'); list.box.set('a', visit('O')); list.box.set('c', visit('NH'));
  fail(new Error('offline')); assert.equal(await done, false);
  assert.deepEqual([...list.box.waiting], [['a', visit('O')], ['c', visit('NH')]], 'the newer O wins over the S that failed');
  assert.ok(shelf.unconfirmed());
});

test('results outlive the screen: flushes of one list run one after another, so a second request never overtakes the first', async () => {
  const shelf = resultShelf<string, WalkVisit>(v => v.on), list = shelf.open({meta: null, title: 't', query: {}}); shelf.keep(list);
  const log: string[] = []; let release!: () => void, calls = 0;
  list.box.set('a', visit('S'));
  const first = flushList(list, {...calm, send: b => { log.push(`first ${[...b.keys()]}`); return calls++ ? Promise.resolve() : new Promise<void>(r => { release = r; }); }});
  await tick();
  list.box.set('b', visit('U'));   // set while the first request is in flight
  const second = flushList(list, {...calm, send: async b => { log.push(`second ${[...b.keys()]}`); }});
  await tick();
  assert.deepEqual(log, ['first a'], 'the second flush waits for the request in flight');
  release(); assert.deepEqual([await first, await second], [true, true]);
  assert.deepEqual(log, ['first a', 'first b'], 'the flush in flight sends what came meanwhile, in order, and the second flush finds nothing left'); assert.ok(!shelf.unconfirmed());
  // a flush that fails does not stop the one after it
  list.box.set('c', visit('S'));
  assert.equal(await flushList(list, {...calm, send: async () => { throw new Error('offline'); }}), false);
  assert.equal(await flushList(list, {...calm, send: async () => {}}), true); assert.ok(!shelf.unconfirmed());
});

test('history apart from marks: what is on file shows with its day and is never a mark; the code on file can be recorded again', () => {
  const a = voter({person_id: 'a', last: {code: 'NH', on: '2026-09-30', list: 'k0'}}), b = voter({person_id: 'b', last: null});
  const p = payload([run({doors: [door(1, '4102', [a, b])]})]);
  assert.equal(lastText(a.last), 'NH 9/30/2026'); assert.equal(lastLabel(a.last), 'Not home on 9/30/2026'); assert.equal(lastText(null), ''); assert.equal(lastText({code: 'S', on: '', list: null}), 'S');
  assert.deepEqual(historyOf(p), {a: {code: 'NH', on: '2026-09-30', list: 'k0'}, b: null}); assert.deepEqual(historyOf(payload([])), {});
  assert.deepEqual(lastOf(a), a.last); assert.equal(lastOf(b), null);
  assert.deepEqual(lastOf(a, {a: {code: 'MV', on: '2026-10-01', list: 'k1'}}), {code: 'MV', on: '2026-10-01', list: 'k1'}, 'a fresher look at the list wins');
  assert.equal(lastOf(a, {a: null}), null, 'nothing on file after all'); assert.deepEqual(lastOf(a, {b: null}), a.last, 'no fresher look at this voter: what the list came with');
  // the history is not a mark: choosing the code on file records a visit, and only a code that is marked is taken back
  assert.equal(markAfterClick(undefined, 'NH'), 'NH', 'last seen Not home and not home again: another Not home is recorded'); assert.equal(markAfterClick(null, 'NH'), 'NH');
  assert.equal(markAfterClick('NH', 'NH'), null); assert.equal(markAfterClick('NH', 'S'), 'S');
  // initialResults is the history as codes, whichever list it was recorded on (the screen starts from listMarks instead)
  assert.deepEqual(initialResults(p), {a: 'NH'});
});

test('marks come back with the list: a result recorded on this list is selected after a reload, one from another list is history', () => {
  const here = voter({person_id: 'a', last: {code: 'S', on: '2026-10-02', list: 'k1'}});
  const parent = voter({person_id: 'b', name: 'Lee Stone', last: {code: 'NH', on: '2026-09-30', list: 'k0'}});
  const none = voter({person_id: 'c', name: 'Pat Moss', last: null});
  const unsure = voter({person_id: 'd', name: 'Ann Poe', last: {code: 'U', on: '2026-09-01', list: null}});
  const saved = (key: string | null): WalkListPayload => ({...payload([run({doors: [door(1, '4102', [here, parent, none, unsure])]})]), list: {key, title: 'Saturday', kind: 'standard', created_at: null}});
  assert.deepEqual(listMarks(saved('k1')), {a: 'S'}, 'only the result recorded on this list is a mark');
  assert.deepEqual(listMarks(saved('k0')), {b: 'NH'}, 'on the list that holds the Not home, that one is the mark');
  assert.deepEqual(listMarks(saved('k9')), {}, 'a list that none of the results belong to starts with no marks');
  assert.deepEqual(listMarks(saved(null)), {}, 'a list that is not saved has no results of its own');
  assert.deepEqual(listMarks(payload([])), {}); assert.deepEqual(listMarks({list: saved('k1').list, runs: undefined as unknown as WalkRun[]}), {});
  assert.deepEqual(historyOf(saved('k1')), {a: here.last, b: parent.last, c: null, d: unsure.last}, 'every voter keeps the history the list came with');
  // a re-knock list made from k0: the Not home of k0 is history there, so choosing Not home records a new visit and does not clear anything
  const reknock: WalkListPayload = {...saved('k5'), list: {key: 'k5', title: 'Not home again · Saturday', kind: 'reknock', created_at: null}};
  assert.deepEqual(listMarks(reknock), {}); assert.equal(markAfterClick(listMarks(reknock).b, 'NH'), 'NH');
  // after the Not home is recorded on k5 and the list is opened again, it is the mark of k5 and the line for it is gone
  const again = {...reknock, runs: [run({doors: [door(1, '4102', [{...parent, last: {code: 'NH', on: '2026-10-02', list: 'k5'}}])]})]};
  assert.deepEqual(listMarks(again), {b: 'NH'}); assert.equal(markAfterClick(listMarks(again).b, 'NH'), null, 'and choosing it again takes it back');
  assert.ok(Object.keys(initialResults(saved('k1'))).length === 3, 'initialResults still reads every code on file, from any list');
});

test('history apart from marks: the CSV Result is the mark, else the latest result on file, as fresh as the last look at the list', () => {
  const a = voter({person_id: 'a', last: {code: 'NH', on: '2026-09-30'}}), b = voter({person_id: 'b', name: 'Lee Stone', last: {code: 'S', on: '2026-09-30'}});
  const p = payload([run({doors: [door(1, '4102', [a, b])]})]), col = (rows: string[][]) => rows.map(r => r[15]);
  assert.deepEqual(col(walkCsvRows(p)), ['NH', 'S']);
  assert.deepEqual(col(walkCsvRows(p, {a: 'MV'})), ['MV', 'S'], 'a mark wins');
  assert.deepEqual(col(walkCsvRows(p, {a: null})), ['', 'S'], 'a cleared mark is empty until the server has confirmed the clearing');
  // the clearing is confirmed and the list is looked at again: a's older result from another list is back, b has nothing on file
  const fresh: WalkHistory = {a: {code: 'U', on: '2026-09-01', list: 'k0'}, b: null};
  assert.deepEqual(col(walkCsvRows(p, {}, fresh)), ['U', ''], 'the fresh look replaces what the list came with');
  assert.deepEqual(col(walkCsvRows(p, {a: 'MV'}, fresh)), ['MV', '']);
  assert.ok(buildWalkCsv(p, {}, fresh).includes('"1994-09-29","U"\r\n'));
  const marks: Record<string, WalkCode | null> = {a: null, b: 'S', c: null};
  assert.deepEqual(confirmedClears(marks, id => id === 'c'), ['a'], 'a cleared mark counts once no result for the voter is still on its way');
  assert.deepEqual(confirmedClears(marks, () => false), ['a', 'c']); assert.deepEqual(confirmedClears({b: 'S'}, () => false), []);
  assert.deepEqual(dropCleared(marks, ['a']), {b: 'S', c: null}, 'a confirmed clearing leaves; one still on its way stays');
  assert.deepEqual(dropCleared({a: 'U'}, ['a']), {a: 'U'}, 'a voter marked again since stays marked');
  const same = {b: 'S' as WalkCode};
  assert.equal(dropCleared(same, ['b', 'z']), same, 'nothing to drop: the same object');
  assert.deepEqual(marks, {a: null, b: 'S', c: null}, 'the input is not changed');
});

test('one answer at a time: a later job, a cancel or an end settles the earlier job, and a stale job cannot change anything', async () => {
  const jobs = latestJob();
  const a = jobs.start(); assert.ok(a.live() && !a.signal.aborted);
  const b = jobs.start(); assert.ok(!a.live() && a.signal.aborted, 'starting b makes a stale and aborts its request'); assert.ok(b.live() && !b.signal.aborted);
  assert.equal(jobs.cancel(), true); assert.ok(!b.live() && b.signal.aborted); assert.equal(jobs.cancel(), false, 'nothing is open');
  const c = jobs.start(); c.end(); assert.ok(!c.live() && !c.signal.aborted, 'a job that finished was not cancelled'); assert.equal(jobs.cancel(), false);
  const d = jobs.start(); a.end(); b.end(); assert.ok(d.live(), 'a stale job ending does not end the live one');
  // initial matching of file A, then a saved list is adopted (its picker replaces the one A would add to), then A answers late
  const picker: string[] = [], matching = latestJob();
  const match = async (streets: string[], wait: number) => {
    const job = matching.start(); await new Promise(r => setTimeout(r, wait));
    if (!job.live()) return false;
    picker.push(...streets); job.end(); return true;
  };
  const slow = match(['pine st'], 20);
  matching.cancel(); picker.splice(0, picker.length, 'oak st');   // cleared, or left behind by a saved list that restores its own streets
  assert.equal(await slow, false); assert.deepEqual(picker, ['oak st'], 'the late answer of the file adds nothing to the other list');
  const first = match(['elm ave'], 30), second = match(['cedar dr'], 5);
  assert.deepEqual([await first, await second], [false, true]); assert.deepEqual(picker, ['oak st', 'cedar dr'], 'only the file chosen last is answered');
});

// --- precinct names: inside the fill, never on an enclave ---------------------------------------------------------------------
const sq = (cx: number, cy: number, r: number): XY[] => [[cx - r, cy - r], [cx + r, cy - r], [cx + r, cy + r], [cx - r, cy + r], [cx - r, cy - r]];

test('precinct label points: inside the fill of the area, never in a hole where another precinct lies', () => {
  const view: Bounds = [0, 0, 100, 100];
  // A is a square ring around a square hole that B fills (the server sends both rings of A): the middle of A's outer ring is in B
  const A = [sq(50, 50, 48), sq(50, 50, 30)], B = [sq(50, 50, 30)];
  const a = labelPoint(A, view);
  assert.ok(a && inArea(a, A) && !pointInRing(a, B[0]), `A is named at ${a}`);
  assert.deepEqual(labelPoint(B, view), [50, 50], 'B is named at its middle');
  assert.deepEqual(labelPoint([...A].reverse(), view), a, 'the order of the rings does not matter');
  const fills: [string, XY[][]][] = [
    ['a hole off centre', [sq(50, 50, 48), sq(35, 50, 25)]],
    ['two holes with fill between them', [sq(50, 50, 48), sq(25, 50, 15), sq(75, 50, 15)]],
    ['a hole cut by the edge of the view', [sq(50, 50, 200), sq(0, 50, 30)]],
    ['a thin ring', [sq(50, 50, 48), sq(50, 50, 47)]],
    ['a piece of the area inside the hole', [sq(50, 50, 48), sq(50, 50, 40), sq(50, 50, 20)]],
    ['two pieces, one with a hole', [sq(25, 25, 20), sq(25, 25, 10), sq(80, 80, 10)]],
  ];
  for (const [what, rings] of fills) { const p = labelPoint(rings, view); assert.ok(p && inArea(p, rings), `${what}: ${p}`); }
  assert.equal(labelPoint([sq(50, 50, 48), sq(50, 50, 40)], [30, 30, 70, 70]), null, 'a view that lies wholly in the hole shows none of the area');
  const parts = labelPoint([sq(25, 25, 20), sq(25, 25, 10), sq(80, 80, 10)], view);
  assert.ok(parts && parts[0] < 50 && parts[1] < 50, `the bigger piece, the one with the hole, is named: ${parts}`);
});

test('PDF map: the code of a precinct that surrounds another is written in its own fill, and the enclave keeps its own', async () => {
  const hole = [at(-150, -150), at(150, -150), at(150, 150), at(-150, 150), at(-150, -150)];   // B, centred on the map; 150 m from the middle to each side
  const A = {id: '02012', label: '02012', rings: [[at(-400, -400), at(400, -400), at(400, 400), at(-400, 400), at(-400, -400)], hole]};
  const B = {id: '02013', label: '02013', rings: [hole]};
  const {map} = await buildWalkPdf(mapPayload({...context(), precincts: [A, B]}));
  const f = map.frame, half = 150 / map.metresPerPoint;
  const away = (l: {corners: XY[]}) => Math.max(Math.abs(l.corners.reduce((s, c) => s + c[0], 0) / 4 - (f.x + f.w / 2)), Math.abs(l.corners.reduce((s, c) => s + c[1], 0) / 4 - (f.y + f.h / 2)));
  const a = map.labels.find(l => l.kind === 'precinct' && l.text === '02012'), b = map.labels.find(l => l.kind === 'precinct' && l.text === '02013');
  assert.ok(a && b, 'both codes are written');
  assert.ok(away(a) > half, `A (${away(a).toFixed(0)} pt from the middle) is outside the ${half.toFixed(0)} pt enclave`);
  assert.ok(away(b) < half, `B (${away(b).toFixed(0)} pt from the middle) is inside it`);
  for (const l of map.labels) for (const [x, y] of l.corners) assert.ok(x >= f.x && x <= f.x + f.w && y >= f.y && y <= f.y + f.h, `${l.text} inside the frame`);
});

test('walk screen wiring (round 4): the shelf outlives the screen, a file is matched as the latest job, the visit day is taken when a result is set, focus returns after the save render', () => {
  const src = readFileSync(path.join(import.meta.dirname, '../../src/v3/components/WalkScreen.tsx'), 'utf8');
  assert.ok(/^const outbox=resultShelf<string,WalkVisit>\(v=>v\.on\);/m.test(src), 'one shelf for the page, outside the component, split into requests by day');
  assert.ok(!/outbox\.clear\(\)/.test(src), 'showing another list does not forget results that were not saved');
  assert.ok(/window\.addEventListener\('beforeunload',warnHeld\)/.test(src) && /const warnHeld=\(e:BeforeUnloadEvent\)=>\{if\(outbox\.unconfirmed\(\)\|\|refusals\.note\|\|shownFlagged\.current\.size>0\)/.test(src), 'closing the page asks while the screen is away too');
  assert.ok(/settleEarlier\(\)/.test(src) && /outbox\.held\(\)/.test(src), 'results kept from an earlier visit are sent again');
  assert.ok(/entry\.box\.set\(id,\{code,on:localToday\(\)\}\)/.test(src) && !/saveWalkDispositions\([^)]*localToday/.test(src), 'the day is taken when a result is set, never when it is sent');
  assert.ok(/matchJobs\.start\(\)/.test(src) && /matchWalkStreets\(inputs,area,job\.signal\)/.test(src) && /if\(!job\.live\(\)\)return;/.test(src), 'the first answer for a file is dropped once the file is replaced, cleared or left behind');
  assert.ok(/if\(!next&&matchJobs\.cancel\(\)\)/.test(src), 'clearing or replacing the upload drops a file still being matched');
  const save = /async function save\(title:string\)\{[\s\S]*?\n  \}/.exec(src)?.[0] ?? '';
  assert.ok(save.includes('ensureSaved(title)') && !save.includes('.focus()'), 'save() does not focus the button it has just disabled');
  assert.ok(/if\(refocusSave\.current&&busy===''&&!saveOpen\)\{refocusSave\.current=false;saveButton\.current\?\.focus\(\);\}/.test(src), 'focus returns to Save after the render that cleared busy');
  assert.ok(/markAfterClick\(p\.value,c,p\.unsaved\)/.test(src) && !/initialResults/.test(src), 'a code on file is history, not a mark');
  assert.ok(/const marks=marksWithRefused\(p,/.test(src) && /setResults\(marks\.results\)/.test(src), 'a list opens with the results recorded on it selected');
  assert.ok(/confirmedClears\(resultsRef\.current,id=>entry\.box\.holds\(id\)\)/.test(src) && /setResults\(r=>dropCleared\(r,ids\)\)/.test(src), 'a confirmed clearing is reconciled with what the server holds');
});
// --- a refused batch: the server will not take it however often it is sent, so it must not hold up the screen, nor erase what the walker saw ---
import {
  ResultsRefused, addRefusal, clearRefused, doorsByPerson, keepRefused, listIdentity, marksWithRefused, refusalBarText, refusalCount, refusalHeadline, refusalOf, refusedStatus, unsavedText
} from '../../src/v3/lib/walk.ts';
import type {RefusalNote, RefusedMarks} from '../../src/v3/lib/walk.ts';
import {ApiError} from '../../src/v3/api/client.ts';
const {saveWalkDispositions} = await import('../../src/v3/api/walk.ts');

/** A server that refuses any request holding one of `bad` (all or nothing), as walk_views.dispositions does for a voter not on the list. */
const refuser = (bad: string[], log: string[][] = []) => async (batch: Map<string, WalkVisit>) => {
  log.push([...batch.keys()]);
  if ([...batch.keys()].some(k => bad.includes(k))) throw new ResultsRefused('Some of those voters are not on this list.');
};
const answered = (status: number, message = 'Some of those voters are not on this list.') => new ApiError(message, status, true);

test('refusals: only the walk API refusing in its own JSON words, with 400 or 404, is a refusal; everything else is retried', () => {
  assert.deepEqual([400, 404].filter(refusedStatus), [400, 404]);
  for (const s of [0, 403, 408, 409, 410, 413, 422, 429, 500, 502, 503]) assert.ok(!refusedStatus(s), `${s} is sent again`);
  const r400 = refusalOf(answered(400), 'results'), r404 = refusalOf(answered(404, 'That saved list was not found.'), 'results');
  assert.ok(r400 instanceof ResultsRefused && !r400.whole && r400.message === 'Some of those voters are not on this list.', 'a 400 on results: split to find the refused ones');
  assert.ok(r404 instanceof ResultsRefused && r404.whole, 'a 404 on results: the list is gone, so the whole batch, unsplit');
  const list400 = refusalOf(answered(400, 'No voters live on those streets in this area.'), 'list');
  assert.ok(list400 instanceof ResultsRefused && list400.whole, 'a 400 on saving the list refuses all its results');
  assert.equal(refusalOf(answered(404), 'list'), null, 'saving a list is refused only on a 400');
  assert.equal(refusalOf(answered(409, 'The data is being updated.'), 'list'), null, 'a 409 while the store is swapped is retried');
  for (const s of [403, 409, 410, 413, 422, 429, 500, 503]) assert.equal(refusalOf(answered(s), 'results'), null, String(s));
  assert.equal(refusalOf(new ApiError('The local data service returned an unreadable response.', 400), 'results'), null, 'an HTML 400 (a proxy, Django) is not the walk API speaking');
  assert.equal(refusalOf(new Error('Failed to fetch'), 'results'), null); assert.equal(refusalOf(null, 'results'), null);
});

test('refusals through the real client: an HTML 400 and a failed session fetch are retried, a JSON 400 is refused', async () => {
  const real = globalThis.fetch;
  let session: Response, post: Response;
  globalThis.fetch = (async (url: string) => String(url).includes('/api/auth/session') ? session : post) as typeof fetch;
  const json = (status: number, body: unknown) => new Response(JSON.stringify(body), {status, headers: {'Content-Type': 'application/json'}});
  const send = (batch: Map<string, WalkVisit>) => { const {on, entries} = visitRequest(batch); return saveWalkDispositions('k1', on, entries).catch((e: unknown) => { throw refusalOf(e, 'results') ?? e; }); };
  const run = async () => {
    const outbox = resultOutbox<string, WalkVisit>(v => v.on); outbox.set('a', visit('S'));
    const refused: string[] = [];
    const ok = await drainResults(outbox, {...calm, send, refuse: r => refused.push(...r.keys())});
    return {ok, refused, waiting: [...outbox.waiting.keys()]};
  };
  try {
    session = json(400, {error: 'Bad session.'}); post = json(200, {saved: 1});
    assert.deepEqual(await run(), {ok: false, refused: [], waiting: ['a']}, 'the session fetch failing is not the endpoint refusing: retried');
    session = json(200, {csrf: 't'}); post = new Response('<!doctype html><h1>Bad Request (400)</h1>', {status: 400, headers: {'Content-Type': 'text/html'}});
    assert.deepEqual(await run(), {ok: false, refused: [], waiting: ['a']}, 'an HTML 400 is retried, never refused');
    post = json(422, {error: 'Unprocessable.'});
    assert.deepEqual(await run(), {ok: false, refused: [], waiting: ['a']}, 'a JSON 422 is retried');
    post = json(400, {error: 'Some of those voters are not on this list.'});
    assert.deepEqual(await run(), {ok: true, refused: ['a'], waiting: []}, 'a JSON 400 from the endpoint is refused');
  } finally { globalThis.fetch = real; }
});

test('refusals: a refused request is split until the refused result stands alone; the rest are saved and the outbox is clear', async () => {
  const outbox = resultOutbox<string, WalkVisit>(v => v.on);
  for (const id of ['a', 'b', 'c', 'd', 'e']) outbox.set(id, visit('S'));
  const log: string[][] = [], refused: [string[], string][] = [], states: string[] = [];
  const ok = await drainResults(outbox, {ready: () => true, send: refuser(['c'], log), report: s => states.push(s), refuse: (r, why) => refused.push([[...r.keys()], why])});
  assert.equal(ok, true, 'a refusal is not a failure: nothing is left to retry');
  assert.deepEqual(refused, [[['c'], 'Some of those voters are not on this list.']]);
  assert.deepEqual(log.filter(b => !b.includes('c')).flat().sort(), ['a', 'b', 'd', 'e'], 'every other result is saved');
  assert.ok(log.length <= 1 + 2 * Math.ceil(Math.log2(5)), `a few requests, not one per result: ${log.length}`);
  assert.ok(!outbox.unconfirmed() && !outbox.holds('c'), 'the refused result is not sent again'); assert.equal(states.at(-1), 'saved');
});

test('refusals: splitting is bounded: 200 results refused throughout cost three requests, and a list that is gone costs one', async () => {
  const outbox = resultOutbox<string, WalkVisit>(v => v.on);
  for (let i = 0; i < 200; i++) outbox.set(`p${i}`, visit('S'));
  const sizes: number[] = []; let refused = 0;
  assert.equal(await drainResults(outbox, {...calm, send: async b => { sizes.push(b.size); throw new ResultsRefused('The visit date must be a real day, not in the future.'); }, refuse: r => { refused += r.size; }}), true);
  assert.deepEqual(sizes, [200, 100, 100], 'the batch, then its two halves; both refused, so the split stops'); assert.equal(refused, 200);
  for (let i = 0; i < 200; i++) outbox.set(`p${i}`, visit('S'));
  const gone: number[] = []; refused = 0;
  assert.equal(await drainResults(outbox, {...calm, send: async b => { gone.push(b.size); throw refusalOf(answered(404, 'That saved list was not found.'), 'results'); }, refuse: r => { refused += r.size; }}), true);
  assert.deepEqual(gone, [200], 'a 404 refuses the whole batch unsplit'); assert.equal(refused, 200); assert.ok(!outbox.unconfirmed());
});

test('refusals: the stuck screen: a refused batch no longer fails every flush, so loading another list is not blocked', async () => {
  const shelf = resultShelf<string, WalkVisit>(v => v.on), list = shelf.open({meta: null, title: 'Saturday', query: {}}); shelf.keep(list);
  list.box.set('gone', visit('S'));
  const sends: string[][] = [];
  assert.equal(await flushList(list, {...calm, send: refuser(['gone'], sends)}), true, 'the first flush settles');
  assert.equal(await flushList(list, {...calm, send: refuser(['gone'], sends)}), true, 'and so does the next, without sending it again');
  assert.equal(sends.length, 1); assert.deepEqual(shelf.held(), [], 'the shelf lets the list go, so returning to the screen is not blocked either');
  list.box.set('gone', visit('U', '2026-10-02')); list.box.set('ok', visit('NH', '2026-10-03')); shelf.keep(list);
  const days: string[] = [];
  assert.equal(await flushList(list, {...calm, send: async b => { days.push(visitRequest(b).on); if (b.has('gone')) throw new ResultsRefused('no'); }}), true);
  assert.deepEqual(days, ['2026-10-02', '2026-10-03'], 'a refusal of one day does not hold up the next'); assert.ok(!shelf.unconfirmed());
});

test('refusals: a list the server will not save refuses all its results at once; nothing is split or sent again', async () => {
  const outbox = resultOutbox<string, WalkVisit>(v => v.on); for (const id of ['a', 'b', 'c']) outbox.set(id, visit('S'));
  let sends = 0; const refused: string[] = [], states: string[] = [];
  const ok = await drainResults(outbox, {ready: () => true, send: async () => { sends++; throw new ResultsRefused('No voters live on those streets in this area.', true); },
    report: s => states.push(s), refuse: r => refused.push(...r.keys())});
  assert.equal(ok, true); assert.equal(sends, 1, 'the list is asked to be saved once'); assert.deepEqual(refused, ['a', 'b', 'c']);
  assert.ok(!outbox.unconfirmed()); assert.deepEqual(states, ['saving', 'idle'], 'nothing was saved, so the status does not say Saved');
});

test('refusals: a failure partway through a split keeps only what was neither saved nor refused, and a newer mark wins', async () => {
  const outbox = resultOutbox<string, WalkVisit>(v => v.on); for (const id of ['a', 'b', 'c', 'd']) outbox.set(id, visit('S'));
  const refused: string[] = [], reports: [string, string | undefined][] = [];
  let calls = 0;
  const send = async (b: Map<string, WalkVisit>) => {
    calls++;
    if (calls === 1) throw new ResultsRefused('no');           // a,b,c,d refused: split
    if (calls === 2) { outbox.set('a', visit('O')); return; }   // a,b saved; meanwhile the walker marks a again
    if (b.size > 1) throw new ResultsRefused('no');              // c,d refused: split
    if (b.has('c')) throw new ResultsRefused('Some of those voters are not on this list.');
    throw new Error('offline');                                  // d: the connection drops
  };
  assert.equal(await drainResults(outbox, {ready: () => true, send, report: (s, m) => reports.push([s, m]), refuse: r => refused.push(...r.keys())}), false);
  assert.deepEqual(refused, ['c']);
  assert.deepEqual([...outbox.waiting], [['a', visit('O')], ['d', visit('S')]], 'd waits again; a waits with its newer mark; b is saved and gone');
  assert.deepEqual(reports.at(-1), ['error', 'offline']);
});

test('refusals never erase a mark: a refused mark stays the walker\'s code, flagged not saved, on screen and in the CSV, until marked again and saved', () => {
  const a = voter({person_id: 'a', last: {code: 'NH', on: '2026-09-30', list: 'k1'}}), b = voter({person_id: 'b', name: 'Lee Stone'}), c = voter({person_id: 'c', name: 'Pat Moss'});
  const p: WalkListPayload = {...payload([run({doors: [door(1, '4102', [a, b]), door(2, '4106', [c], '2')]})]), list: {key: 'k1', title: 'Saturday', kind: 'standard', created_at: null}};
  const store: RefusedMarks = new Map(), id = listIdentity('k1', p.query);
  keepRefused(store, id, new Map([['a', visit('S')], ['b', visit(null)]]));
  const shown = marksWithRefused(p, store, id);
  assert.deepEqual(shown.results, {a: 'S', b: null}, 'the walker\'s S over the NH on file, and the clearing, both kept'); assert.deepEqual([...shown.unsaved].sort(), ['a', 'b']);
  const col = walkCsvRows(p, shown.results, {}, shown.unsaved).map(r => r[15]);
  assert.deepEqual(col, ['S (not saved)', 'cleared (not saved)', ''], 'the CSV, the walker\'s fallback copy, keeps the code with the flag');
  assert.ok(buildWalkCsv(p, shown.results, {}, shown.unsaved).includes('"S (not saved)"'));
  assert.equal(unsavedText('NH'), 'NH (not saved)');
  assert.equal(markAfterClick('S', 'S', true), 'S', 'choosing a refused code again records it again rather than taking it back'); assert.equal(markAfterClick('S', 'S'), null);
  assert.deepEqual(marksWithRefused(p, store, listIdentity('k9', p.query)).unsaved.size, 0, 'another list does not take these marks');
  assert.deepEqual(marksWithRefused({...p, list: {...p.list, key: null}}, store, listIdentity(null, p.query)).unsaved.size, 0, 'an unsaved list is named by its query');
  clearRefused(store, id, ['a']);
  assert.deepEqual(marksWithRefused(p, store, id), {results: {a: 'NH', b: null}, unsaved: new Set(['b'])}, 'a saved: back to what is on file; b still flagged');
  clearRefused(store, id, ['b']); assert.equal(store.size, 0);
  assert.equal(listIdentity(null, {streets: '1:odd', precinct: '02012'}), listIdentity(null, {precinct: '02012', streets: '1:odd'}), 'the order of the query does not matter');
  assert.deepEqual(doorsByPerson(p), {a: '4102 N Cedar Heights Dr', b: '4102 N Cedar Heights Dr', c: `${doorAddress(door(2, '4106', [], '2'))} N Cedar Heights Dr`});
});

test('refusals: the note adds up across batches, days and lists until dismissed, and names each door with its code', () => {
  let note: RefusalNote | null = null;
  note = addRefusal(note, 'Saturday', new Map([['a', visit('S')]]), {a: '4102 N Cedar Heights Dr'}, 'Some of those voters are not on this list.');
  note = addRefusal(note, 'Saturday', new Map([['b', visit('NH', '2026-10-03')], ['a', visit('O')]]), {a: '4102 N Cedar Heights Dr', b: '4106 N Cedar Heights Dr'}, 'Some of those voters are not on this list.');
  note = addRefusal(note, 'Not home again · Saturday', new Map([['z', visit(null)]]), {}, 'That saved list was not found.');
  assert.equal(refusalCount(note), 3, 'a door refused twice counts once, with its latest code');
  assert.deepEqual(note.lists, [
    {title: 'Saturday', doors: {a: '4102 N Cedar Heights Dr (O)', b: '4106 N Cedar Heights Dr (NH)'}},
    {title: 'Not home again · Saturday', doors: {z: 'A door on this list (cleared)'}},
  ]);
  assert.deepEqual(note.reasons, ['Some of those voters are not on this list.', 'That saved list was not found.']);
  assert.equal(refusalHeadline(note), 'The server refused 3 results, so they were not saved. Record these doors again:');
  assert.equal(refusalHeadline(addRefusal(null, '', new Map([['a', visit('S')]]), {}, '')), 'The server refused 1 result, so it was not saved. Record that door again:');
});

test('walk screen wiring (refusals): narrow classification, marks kept and flagged, the note at page level', () => {
  const src = readFileSync(path.join(import.meta.dirname, '../../src/v3/components/WalkScreen.tsx'), 'utf8');
  const client = readFileSync(path.join(import.meta.dirname, '../../src/v3/api/client.ts'), 'utf8');
  assert.ok(/throw refusalOf\(e,step\)\?\?e;/.test(src) && /await refusing\(save\(\),'list'\)/.test(src) && /await refusing\(saveWalkDispositions\(list,on,entries\),'results'\)/.test(src), 'both requests are classified by refusalOf');
  assert.ok(!/takeBack|refusedStatus\(e\.status\)/.test(src), 'nothing takes a mark off the screen');
  assert.ok(/refuse:\(refused,why\)=>refuse\(entry,refused,why\)/.test(src) && /setUnsaved\(u=>new Set\(\[\.\.\.u,\.\.\.refused\.keys\(\)\]\)\)/.test(src), 'a refused mark on screen is flagged, not removed');
  assert.ok(/marksWithRefused\(p,refusals\.marks,listIdentity\(p\.list\?\.key,p\.query\?\?\{\}\)\)/.test(src), 'a list opened again shows its refused marks, flagged');
  assert.ok(/buildWalkCsv\(p,resultsRef\.current,historyRef\.current,unsavedRef\.current,scores\)/.test(src), 'the CSV carries the flag, read when the file is made (scores may have been asked for first)');
  assert.ok(/refusals\.clear\(identities\(entry\),batch\.keys\(\)\)/.test(src), 'marked again and saved: the flag goes');
  assert.ok(/^const refusals=refusedStore\(\);/m.test(src) && /useState<RefusalNote\|null>\(\(\)=>refusals\.note\)/.test(src), 'marks and note outlive the screen, and a reload (restored when the module loads)');
  assert.ok(/onClick=\{\(\)=>\{refusals\.dismiss\(\);showRefusal\(null\);\}\}>Dismiss</.test(src) && /return refusals\.hold\(identities\(entry\)\[0\],/.test(src), 'dismissing and refusing go through the store, which writes them through');
  assert.ok(!/localStorage|refusedMarks|refusalNote=/.test(src), 'no copy of the marks or note outside the store, and nothing in localStorage');
  assert.ok(/token=\(fresh:boolean\)=>csrf\(fresh,signal\)\.catch\(\(e:unknown\)=>\{throw e instanceof ApiError\?new ApiError\(e\.message,e\.status\):e;\}\)/.test(client), 'a session failure is never an answer of the endpoint');
  assert.ok(/catch\{throw new ApiError\('The local data service returned an unreadable response\.',response\.status\);\}/.test(client), 'an unreadable body is not answered');
});

// --- a list saved after the voter data changed: the server saves the list it builds now, says `changed`, and the page shows that one ----------
import {LIST_REPLACED, OFF_LIST, adoptSaved, splitOffRoster} from '../../src/v3/lib/walk.ts';
import type {WalkSavedPayload} from '../../src/v3/api/walkTypes.ts';
const {saveWalkList} = await import('../../src/v3/api/walk.ts');

test('a list shown without a fingerprint is refused as a whole list, with the server words for it', () => {
  const stale = 'This page is out of date. Reload it, then save the list again.';
  const r = refusalOf(new ApiError(stale, 400, true), 'list');
  assert.ok(r && r.whole && r.message === stale, 'a 400 on saving the list refuses the whole batch and keeps the message for the note');
});

test('saving a list sends the fingerprint of the list shown, and only what the server takes', async () => {
  const real = globalThis.fetch, bodies: unknown[] = [];
  const json = (status: number, body: unknown) => new Response(JSON.stringify(body), {status, headers: {'Content-Type': 'application/json'}});
  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    if (String(url).includes('/api/auth/session')) return json(200, {csrf: 't'});
    bodies.push(JSON.parse(String(init?.body))); return json(201, {list: {key: 'k1'}, changed: false});
  }) as typeof fetch;
  try {
    await saveWalkList('Saturday', {precinct: '02012'}, 'f'.repeat(64));
    assert.deepEqual(bodies, [{title: 'Saturday', query: {precinct: '02012'}, fingerprint: 'f'.repeat(64)}],
      'the fingerprint goes with the list it was shown for (the server refuses a save without one, so the call cannot leave it out)');
  } finally { globalThis.fetch = real; }
});

test('a saved list that differs from the one shown: the entry takes it, its voters and their doors; an unchanged one changes nothing else', () => {
  const shelf = resultShelf<string, WalkVisit>(v => v.on);
  const a = voter({person_id: 'a'}), b = voter({person_id: 'b'}), c = voter({person_id: 'c'});
  const shown = payload([run({doors: [door(1, '4102', [a, b])]})]);
  const same = shelf.open({meta: null, title: 'Saturday', query: shown.query, where: doorsByPerson(shown), fingerprint: 'f1'});
  assert.equal(same.fingerprint, 'f1'); assert.equal(same.roster, null); assert.equal(same.replaced, null);
  const meta = {key: 'k1', title: 'Saturday', kind: 'standard' as const, created_at: '2026-10-02T09:00:00-06:00'};
  assert.deepEqual(adoptSaved(same, {...shown, list: meta, changed: false}), meta);
  assert.deepEqual([same.meta, same.roster, same.replaced], [meta, null, null], 'the list saved is the one shown');
  const entry = shelf.open({meta: null, title: 'Saturday', query: shown.query, where: doorsByPerson(shown), fingerprint: 'f1'});
  const saved: WalkSavedPayload = {...payload([run({doors: [door(1, '4102', [b]), door(2, '4110', [c])]})]), list: meta, fingerprint: 'f2', changed: true};
  assert.deepEqual(adoptSaved(entry, saved), meta);
  assert.equal(entry.replaced, saved, 'the page shows the list that was saved');
  assert.deepEqual([...(entry.roster ?? [])].sort(), ['b', 'c']);
  assert.deepEqual(entry.where, {a: '4102 N Cedar Heights Dr', b: '4102 N Cedar Heights Dr', c: '4110 N Cedar Heights Dr'}, 'a voter no longer on the list keeps the door the walker saw');
  assert.ok(!/should|best/i.test(LIST_REPLACED + OFF_LIST));
});

test('results for voters the saved list does not have are refused here, never sent, and cost no other result', async () => {
  const off = splitOffRoster(new Set(['b', 'c']), new Map([['a', visit('S')], ['b', visit('U')], ['c', visit('NH')], ['d', visit(null)]]));
  assert.deepEqual([[...off.send.keys()], [...off.off.keys()]], [['b', 'c'], ['a', 'd']]);
  const all = new Map([['a', visit('S')]]);
  assert.equal(splitOffRoster(null, all).send, all, 'no roster (the list saved is the one shown): the batch is sent as it is');
  // through the drain, against a server that refuses a request holding a voter not on its list
  const outbox = resultOutbox<string, WalkVisit>(v => v.on);
  for (const id of ['a', 'b', 'c', 'd']) outbox.set(id, visit('S'));
  const posted: string[][] = [], refused: [string[], string][] = [];
  const io = (filter: boolean) => ({...calm, refuse: (r: Map<string, WalkVisit>, why: string) => refused.push([[...r.keys()], why]),
    send: async (batch: Map<string, WalkVisit>) => {
      const {send, off: gone} = filter ? splitOffRoster(new Set(['b', 'c']), batch) : {send: batch, off: new Map<string, WalkVisit>()};
      if (gone.size) refused.push([[...gone.keys()], OFF_LIST]);
      if (send.size) await refuser(['a', 'd'], posted)(send);
    }});
  assert.equal(await drainResults(outbox, io(true)), true);
  assert.deepEqual(posted, [['b', 'c']], 'only the voters on the saved list are sent, in one request');
  assert.deepEqual(refused, [[['a', 'd'], OFF_LIST]], 'the others are refused with the reason, and kept as the walker\'s marks by the caller');
  assert.ok(!outbox.unconfirmed(), 'nothing waits, so nothing blocks the screen');
  for (const id of ['a', 'b', 'c', 'd']) outbox.set(id, visit('S'));
  posted.length = 0; refused.length = 0;
  await drainResults(outbox, io(false));
  assert.deepEqual(refused.flatMap(([ids]) => ids).sort(), ['a', 'b', 'c', 'd'], 'without the roster the split refuses good results with the bad (why it is filtered first)');
});

test('walk screen wiring (list saved after the voter data changed): fingerprint sent, saved list shown with a note, off-list results refused', () => {
  const src = readFileSync(path.join(import.meta.dirname, '../../src/v3/components/WalkScreen.tsx'), 'utf8');
  assert.ok(/saveWalkList\(entry\.title,entry\.query,fingerprint\)/.test(src) && /adoptSaved\(entry,saved\)/.test(src), 'the save sends the fingerprint and the entry takes the answer');
  assert.ok(/fingerprint\?saveWalkList\([^)]*\):Promise\.reject\(new ApiError\(OUT_OF_DATE,400,true\)\)/.test(src), 'a list shown without a fingerprint is refused here as the server would, never sent without one');
  assert.ok(/fingerprint:p\.fingerprint/.test(src), 'each list on screen carries its fingerprint');
  assert.ok(/const next=entry\.replaced\?\?\{\.\.\.base,list\}/.test(src) && /setReplaced\(true\)/.test(src) && /\{LIST_REPLACED\}/.test(src), 'the saved list replaces the one shown, with the note');
  assert.ok(/splitOffRoster\(entry\.roster,visits,entry\.moved\)/.test(src) && /offList\(off,OFF_LIST\)/.test(src), 'results for voters not on the saved list are refused, not sent');
  assert.ok(/\(r,why\)=>refuse\(entry,r,why,true\)/.test(src) && /sendBatch\(entry,batch,held\(true\)\)/.test(src), 'those refusals are the page\'s own, so the note does not blame the server');
  assert.ok(/setResults\(r=>rekeyed\(r,moved\)\)/.test(src) && /refusals\.adopt\(listIdentity\(null,entry\.query\),listIdentity\(list\.key,entry\.query\),entry\.moved\)/.test(src) && /seen:seenBy\(p\)/.test(src), 'marks follow a voter whose person id changed');
  assert.ok(/setReplaced\(false\)/.test(src), 'the note goes when another list comes on screen');
});

// --- round 2: person ids that change while the voters do not, a re-knock that lost voters, the page's own refusals, the phone layout ----------
import {missingText, movedIds, rekeyRefused, rekeyed, seenBy} from '../../src/v3/lib/walk.ts';

test('a list saved with new person ids for the same voters: marks follow them by door and name, are sent under the new ids, and none is refused', async () => {
  const shelf = resultShelf<string, WalkVisit>(v => v.on);
  const shown = payload([run({doors: [door(1, '4102', [voter({person_id: 'a'}), voter({person_id: 'b', name: 'Lee Stone'})]), door(2, '4110', [voter({person_id: 'c', name: 'Pat Moss'})], '2')]})]);
  const saved: WalkSavedPayload = {...payload([run({doors: [door(1, '4102', [voter({person_id: 'ra', name: 'DANA  Whitfield'}), voter({person_id: 'rb', name: 'Lee Stone'})]),
    door(2, '4110', [voter({person_id: 'rc', name: 'Pat Moss'})], '2')]})]), list: {key: 'k1', title: 'Saturday', kind: 'standard', created_at: null}, changed: true};
  assert.deepEqual([...movedIds(seenBy(shown), saved)], [['a', 'ra'], ['b', 'rb'], ['c', 'rc']], 'case and spacing of a name do not matter');
  const entry = shelf.open({meta: null, title: 'Saturday', query: shown.query, where: doorsByPerson(shown), seen: seenBy(shown)}); shelf.keep(entry);
  entry.box.set('a', visit('S')); entry.box.set('c', visit('NH')); entry.box.set('b', visit('U', '2026-10-03'));   // b waits for a request of its own
  const posted: string[][] = [], refused: string[] = [];
  const ok = await flushList(entry, {...calm, refuse: r => refused.push(...r.keys()), send: async batch => {
    if (!entry.meta) adoptSaved(entry, saved);                     // the first request saves the list, as sendBatch does
    const {send, off} = splitOffRoster(entry.roster, batch, entry.moved);
    refused.push(...off.keys()); posted.push([...send.keys()]);
  }});
  assert.equal(ok, true);
  assert.deepEqual(posted, [['ra', 'rc'], ['rb']], 'the request already taken and the one still waiting both go out under the new ids');
  assert.deepEqual(refused, [], 'nothing is refused: every voter is on the saved list');
  assert.ok(!entry.box.unconfirmed());
  assert.deepEqual(rekeyed({a: 'S', c: null, z: 'O'}, entry.moved), {ra: 'S', rc: null, z: 'O'}, 'the marks on screen follow too');
  const marks: RefusedMarks = new Map([['query:x', new Map<string, WalkCode | null>([['b', 'O']])]]);
  rekeyRefused(marks, 'query:x', entry.moved); assert.deepEqual([...marks.get('query:x') ?? []], [['rb', 'O']], 'and so does a refused mark');
  const same = {a: 'S'}; assert.equal(rekeyed(same, new Map()), same);
});

test('re-keying never guesses: two alike at one door, a changed name or a voter who is gone stay refused; an unchanged id needs no match', () => {
  const shown = payload([run({doors: [door(1, '4102', [voter({person_id: 'x1', name: 'Sam Lee'}), voter({person_id: 'x2', name: 'Sam Lee'}), voter({person_id: 'y', name: 'Ana Ruiz'})]),
    door(2, '4110', [voter({person_id: 'n', name: 'Jo Park'}), voter({person_id: 'g', name: 'Al Gone'}), voter({person_id: 'same', name: 'Kim Day'})])]})]);
  const saved = payload([run({doors: [door(1, '4102', [voter({person_id: 'rx1', name: 'Sam Lee'}), voter({person_id: 'rx2', name: 'Sam Lee'}), voter({person_id: 'ry', name: 'Ana Ruiz'})]),
    door(2, '4110', [voter({person_id: 'rn', name: 'Jo Parker'}), voter({person_id: 'same', name: 'Kim Day'})])]})]);
  const moved = movedIds(seenBy(shown), saved);
  assert.deepEqual([...moved], [['y', 'ry']]);
  const roster = new Set(Object.keys(seenBy(saved)));
  const batch = new Map(['x1', 'x2', 'y', 'n', 'g', 'same'].map(id => [id, visit('S')]));
  const {send, off} = splitOffRoster(roster, batch, moved);
  assert.deepEqual([[...send.keys()], [...off.keys()]], [['ry', 'same'], ['x1', 'x2', 'n', 'g']]);
  const elsewhere = payload([run({doors: [door(1, '4106', [voter({person_id: 'ry', name: 'Ana Ruiz'})])]})]);
  assert.equal(movedIds(seenBy(shown), elsewhere).size, 0, 'another door is another voter as far as the page can tell');
});

test('the page\'s own refusals do not say the server refused them; a re-knock names the voters it lost', () => {
  const server = addRefusal(null, 'Saturday', new Map([['a', visit('S')]]), {a: '4102 N Cedar Heights Dr'}, 'Some of those voters are not on this list.');
  assert.equal(refusalHeadline(server), 'The server refused 1 result, so it was not saved. Record that door again:'); assert.equal(server.local, undefined);
  const local = addRefusal(null, 'Saturday', new Map([['b', visit('NH')], ['c', visit('U')]]), {}, OFF_LIST, true);
  assert.equal(refusalHeadline(local), '2 results were not saved:'); assert.deepEqual(local.reasons, [OFF_LIST], 'the reason line stays');
  assert.equal(refusalHeadline(addRefusal(server, 'Saturday', new Map([['b', visit('NH')]]), {}, OFF_LIST, true)), '2 results were not saved:', 'mixed: neutral');
  assert.equal(addRefusal(local, 'Saturday', new Map([['d', visit('S')]]), {}, 'x').local, true, 'stays neutral until dismissed');
  assert.equal(missingText(1), '1 voter marked Not home is no longer in the voter data.');
  assert.equal(missingText(3), '3 voters marked Not home are no longer in the voter data.');
});

test('outbox re-key: waiting results take the new ids in their order; a request already taken keeps its own', () => {
  const box = resultOutbox<string, WalkVisit>(v => v.on);
  box.set('a', visit('S')); box.set('b', visit('U')); const taken = box.take(1); box.set('c', visit('O'));
  box.rekey(new Map([['a', 'ra'], ['b', 'rb'], ['c', 'rc']]));
  assert.deepEqual([...taken.keys()], ['a']); assert.deepEqual([...box.waiting.keys()], ['rb', 'rc']); assert.ok(box.holds('a') && box.holds('rb'));
});

test('walk screen wiring (round 2): re-knock losses told, refused marks described, a one-letter search explained, table roles for the phone cards', () => {
  const src = readFileSync(path.join(import.meta.dirname, '../../src/v3/components/WalkScreen.tsx'), 'utf8');
  const css = readFileSync(path.join(import.meta.dirname, '../../src/v3/styles/walk.css'), 'utf8');
  assert.ok(/if\(q\.missing\)setActionNote\(\{text:missingText\(q\.missing\)/.test(src), 're-knock says how many voters it lost');
  assert.ok(/aria-describedby=\{p\.note\}/.test(src) && /<span id=\{note\} className="walk-unsaved"/.test(src), 'a refused mark is part of its group\'s description');
  assert.ok(/text\.length===1&&<div className="walk-find-pop walk-find-empty" role="status">Type at least 2 letters</.test(src), 'one letter asks for another');
  assert.ok(/<table className="walk-table" role="table">/.test(src) && /<tr role="row"/.test(src) && /role="columnheader"/.test(src), 'roles survive the card layout');
  const phone = css.slice(css.indexOf('@media(max-width:640px)'));
  assert.ok(/\.walk-res button\{[^}]*height:44px/.test(phone) && /\.walk-table-wrap\{overflow-x:visible\}/.test(phone), 'phone: 44 px buttons, no sideways table');
  assert.ok(!/@media[^{]*print/.test(css), 'the printed list is the PDF, not this stylesheet');
});

test('refusal bar on phones: a count that matches the note, a labelled region that is not a live region, View focuses the note, shown only at 640 px and below', () => {
  const src = readFileSync(path.join(import.meta.dirname, '../../src/v3/components/WalkScreen.tsx'), 'utf8');
  const css = readFileSync(path.join(import.meta.dirname, '../../src/v3/styles/walk.css'), 'utf8');
  const note = (n: number) => { let r: RefusalNote | null = null; for (let i = 0; i < n; i++) r = addRefusal(r, 'List', new Map([[`p${i}`, visit('S')]]), {[`p${i}`]: `${i + 1} Oak St`}, 'why'); return r; };
  assert.equal(refusalBarText(note(1)), '1 result not saved'); assert.equal(refusalBarText(note(3)), '3 results not saved');
  assert.equal(refusalBarText(note(2)).split(' ')[0], String(refusalCount(note(2))), 'the same count as the note');
  const bar = /\{refusal&&<div className="walk-refused-bar"[^>]*>[\s\S]*?<\/button><\/div>\}/.exec(src)?.[0] ?? '';
  assert.ok(/role="region" aria-label="Results not saved"/.test(bar) && !/role="(alert|status)"|aria-live/.test(bar), 'findable, but the note is the only announcement');
  assert.ok(/refusedNote\.current/.test(bar) && /\.scrollIntoView\(/.test(bar) && /\.focus\(\{preventScroll:true\}\)/.test(bar) && />View<\/button>/.test(bar), 'View scrolls to the note and focuses it');
  assert.ok(/className="walk-note walk-refused" role="alert" tabIndex=\{-1\} ref=\{refusedNote\}/.test(src) && /refusal&&'has-refusal'/.test(src), 'the note can take focus; the page knows the bar is up');
  assert.ok(/\n\.walk-refused-bar\{display:none\}/.test(css), 'hidden unless the phone block shows it');
  const phone = css.slice(css.lastIndexOf('@media(max-width:640px)'));
  assert.ok(/\.walk-refused-bar\{position:fixed;[^}]*bottom:0;[^}]*display:flex[^}]*env\(safe-area-inset-bottom\)/.test(phone) && /\.walk-refused-bar button\{[^}]*min-height:44px/.test(phone), 'phone: fixed to the bottom, 44 px button, safe area kept');
  assert.ok(/\.walk-screen\.has-refusal\{padding-bottom:calc\(\d+px \+ env\(safe-area-inset-bottom\)\)\}/.test(phone), 'room under the last card');
});

// --- Phase 4 WU: printed scores (default OFF = raw) and planned streets ----------------------------------------------------------
import {createHash} from 'node:crypto';

const pdfStreams = (doc: PdfDoc): string => (doc as unknown as {internal: {pages: (string[] | undefined)[]}}).internal.pages.slice(1).map(x => (x ?? []).join('\n')).join('\n--page--\n');
const scoredList = () => payload([run({doors: manyDoors(12)}), run({stop: 2, street_id: 9, street: 'Short St', doors: [door(50, '5', [voter({person_id: 'p2'})])]})]);

test('PDF: with no scores the pages are exactly what they were before the Print scores toggle existed (hash recorded before the change)', async () => {
  const {doc} = await buildWalkPdf(scoredList());
  assert.equal(createHash('sha256').update(pdfStreams(doc)).digest('hex'), '47eb65c3e20067e9ac8a5c34439334b8b794ebbe439a78ea07186f429c7fb7d9');
});

const SCORED = {'a0-0': {ovr: 71, sup: 40, part: 88}, 'a1-0': {ovr: 5, sup: 0, part: 100}, p2: {ovr: 12, part: 5}};   // p2 has no support score; every other voter has no score row

test('PDF with scores: a SCORES group with Ovr, Sup and Part after Reg. Date, a line on the cover that says what they are, the numbers on the list pages', async () => {
  const {doc} = await buildWalkPdf(scoredList(), {scores: SCORED});
  const pages = pdfPages(doc);
  assert.ok(pages[0].some(t => t === 'Scores run 0 to 100, higher is more: Ovr overall, Sup candidate support, Part participation.'), 'the cover names the columns');
  for (const t of pages.slice(1)) for (const h of ['SCORES', 'Ovr', 'Sup', 'Part', 'Reg. Date', 'VOTER INFORMATION', 'Address', 'Name']) assert.ok(t.includes(h), h);
  assert.ok(['71', '40', '88', '100'].every(n => pages[1].includes(n)), 'the first run shows its scores');
  assert.ok(pages[pages.length - 1].includes('12') && pages[pages.length - 1].includes('5'), 'a person without a support score still shows the others');
  assert.ok(pages.slice(1).flat().every(t => !/\babout\b/i.test(t)) && !pages[0].some(t => /Scores run.*\babout\b/i.test(t)), 'no estimate wording in what the scores add (the door script on the cover is exempt)');
  const {doc: plain} = await buildWalkPdf(scoredList());
  assert.notEqual(pdfStreams(doc), pdfStreams(plain));
  assert.ok(pdfPages(plain).flat().every(t => !['SCORES', 'Ovr', 'Sup', 'Part'].includes(t)), 'with no scores there is no such column');
});

test('PDF with scores in a district without a candidate (no support numbers): Ovr and Part only', async () => {
  const {doc} = await buildWalkPdf(scoredList(), {scores: {'a0-0': {ovr: 71, part: 88}}});
  const pages = pdfPages(doc);
  assert.ok(pages[0].includes('Scores run 0 to 100, higher is more: Ovr overall, Part participation.'));
  assert.ok(pages[1].includes('Ovr') && pages[1].includes('Part') && !pages[1].includes('Sup'));
  assert.equal((await buildWalkPdf(scoredList(), {scores: {}})).doc.getNumberOfPages(), doc.getNumberOfPages(), 'an empty answer still prints the columns, blank');
});

test('CSV with scores: Overall, Candidate support and Participation after Result; two columns without support; the default file is unchanged', () => {
  const p = payload([run({doors: [door(1, '4102', [voter({person_id: 'a'}), voter({person_id: 'b'}), voter({person_id: 'c'})])]})]);
  const lines = (c: string) => c.slice(1).split('\r\n').filter(Boolean);
  const plain = lines(buildWalkCsv(p));
  assert.equal(buildWalkCsv(p, {}, {}, undefined, undefined), buildWalkCsv(p, {}, {}), 'no scores: the file is the one it always was');
  assert.equal(plain[0].split('","').length, CSV_COLUMNS.length);
  const withSup = lines(buildWalkCsv(p, undefined, undefined, undefined, {a: {ovr: 71, sup: 40, part: 88}, b: {ovr: 12, part: 5}}));
  assert.ok(withSup[0].endsWith('"Result","Overall","Candidate support","Participation"'));
  assert.deepEqual(withSup.map((l, i) => l.startsWith(plain[i].slice(0, -1)) || l.startsWith(plain[i])), [true, true, true, true], 'every default column is where it was');
  assert.ok(withSup[1].endsWith('"","71","40","88"') && withSup[2].endsWith('"","12","","5"') && withSup[3].endsWith('"","","",""'), 'a person with no score row has blank cells');
  const colorado = lines(buildWalkCsv(p, undefined, undefined, undefined, {a: {ovr: 71, part: 88}}));
  assert.ok(colorado[0].endsWith('"Result","Overall","Participation"') && colorado[1].endsWith('"","71","88"'));
  assert.deepEqual(walkCsvRows(p, undefined, undefined, undefined, {a: {ovr: 1, part: 2}})[0].slice(-3), ['', '1', '2']);
  assert.equal(scoreColumns(undefined).length, 0); assert.deepEqual(scoreColumns({}), ['ovr', 'part']);
});

test('page address: planned streets ride along, are enough to rebuild a list, and come back from a saved list', () => {
  const read = (qs: string) => readWalkAddress(new URLSearchParams(qs));
  assert.deepEqual(read('precinct=02012&spans=15,12,15,x,99999999999').spans, [12, 15], 'sorted, once each, junk skipped');
  assert.ok(!('spans' in read('precinct=02012')), 'no planned streets: the address is read as it always was');
  assert.equal(startPlan(read('spans=12,15')), 'build'); assert.equal(startPlan(read('spans=x')), 'none'); assert.equal(startPlan(read('spans=12&list=k1')), 'saved');
  assert.deepEqual(pickerOf(listOf({}, {precinct: '02012', spans: '12,15'})), {area: {ss: '', sh: '', precinct: '02012'}, streets: '', spans: '12,15'});
  assert.deepEqual(pickerOf(listOf({}, {spans: '12'})), {area: {ss: '', sh: '', precinct: ''}, streets: '', spans: '12'}, 'planned streets alone are a picker setting');
  assert.equal(pickerOf(listOf({kind: 'reknock'}, {from: 'abc', spans: '12'})), null, 'a re-knock list does not restore the picker');
});

test('walk screen wiring: the street plan shows only with features.roi, builds with spans, and the print toggle asks for scores only while on', () => {
  const src = readFileSync(path.join(import.meta.dirname, '../../src/v3/components/WalkScreen.tsx'), 'utf8');
  assert.ok(/features\?\.roi&&<StreetPlan /.test(src), 'no street plan without features.roi (Colorado)');
  assert.ok(/getWalkList\(streets,where,sig,planned\)/.test(src) && /onClick=\{\(\)=>generate\(\)\}/.test(src), 'the list is built from the picks and the planned streets, and the click is not passed in as the streets');
  assert.ok(/spans:spansParam\(spans\)\|\|null/.test(src), 'the page address carries the planned streets');
  assert.ok(/if\(!printScores\)return undefined;/.test(src) && /useState\(false\);?\s*\/\/ the Print scores toggle/.test(src), 'Print scores starts off and asks for nothing while off');
  assert.ok(/exportWalkPdf\(p,\{area:listLabel\(p\),scores\}\)/.test(src) && /if\(scores===null\)return;/.test(src), 'a failed scores request stops the export and says so');
  assert.ok(/for\(const chunk of idChunks\(ids\)\)/.test(src), 'a list longer than one request is asked for in pieces');
});