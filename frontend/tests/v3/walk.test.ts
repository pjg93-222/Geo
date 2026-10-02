// Walk list text rules, CSV in and out, and the door script. Run with: node --test tests/v3/walk.test.ts
import {test} from 'node:test';
import assert from 'node:assert/strict';
import type {WalkDoor, WalkListPayload, WalkRun, WalkVoter} from '../../src/v3/api/walkTypes.ts';
import * as walkLib from '../../src/v3/lib/walk.ts';
import {
  CODE_LABEL, CSV_COLUMNS, MAX_UPLOAD_ROWS, WALK_CODES, areaText, buildWalkCsv, codeLegend, countText, csvValue, distanceText, doorAddress,
  fullDate, houseSpan, isWalkCode, parseWalkCsv, phoneText, regDate, runHeader, sideFromText, sideLabel, splitCsv, tallyText,
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
  totals: {streets: 1, stops: runs.length, doors: 1, voters: 1, walk_m: 410, minutes: 7}, bbox: [-104.81, 38.79, -104.79, 38.81], runs
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
  // The totals still carry the server's `minutes`; the line leaves it out whatever it holds.
  assert.equal(totalsLine({streets: 4, stops: 6, doors: 58, voters: 104, walk_m: 2900, minutes: 160}), '4 streets · 6 stops · 58 doors · 104 voters · 2.9 km');
  assert.equal(totalsLine({streets: 1, stops: 1, doors: 1, voters: 1, walk_m: 60, minutes: 2}), '1 street · 1 stop · 1 door · 1 voter · 0.1 km');
  assert.equal(totalsLine({streets: 12, stops: 30, doors: 1200, voters: 1500, walk_m: 14000, minutes: 3000}), '12 streets · 30 stops · 1,200 doors · 1,500 voters · 14.0 km');
  for (const minutes of [0, 2, 160, 3000, -3, NaN]) assert.equal(totalsLine({streets: 4, stops: 6, doors: 58, voters: 104, walk_m: 2900, minutes}), '4 streets · 6 stops · 58 doors · 104 voters · 2.9 km', String(minutes));
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
  parseStreetsParam, sideWords, streetsParam, yearRange
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
  assert.equal(majorParty({d: 5, r: 2, u: 1, o: 0}), 'd'); assert.equal(majorParty({d: 0, r: 0, u: 3, o: 1}), 'u'); assert.equal(majorParty({d: 0, r: 0, u: 0, o: 2}), 'o');
  assert.equal(majorParty({d: 3, r: 3, u: 1, o: 0}), 'mixed'); assert.equal(majorParty({d: 1, r: 1, u: 1, o: 1}), 'mixed'); assert.equal(majorParty({d: 0, r: 0, u: 0, o: 0}), 'none');
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
  // The totals carry the server's `minutes` (160 here); nothing the walker reads or prints may turn it into "about 2 h 40 min" or the like.
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
  for (const ctx of [context(), tight, many, undefined]) {
    const {map} = await buildWalkPdf(mapPayload(ctx)), f = map.frame;
    assert.ok(map.labels.length >= 1);
    for (const l of map.labels) for (const [x, y] of l.corners) assert.ok(x >= f.x && x <= f.x + f.w && y >= f.y && y <= f.y + f.h, `${l.kind} ${l.text} inside the frame`);
    if (ctx === context() || ctx === undefined) for (let i = 0; i < map.labels.length; i++) for (let j = i + 1; j < map.labels.length; j++) assert.ok(!boxesHit(map.labels[i].corners, map.labels[j].corners), `${map.labels[i].text} / ${map.labels[j].text}`);
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
  assert.ok(/const warn=\(e:BeforeUnloadEvent\)=>\{if\(outbox\.unconfirmed\(\)\|\|saving\.current\)/.test(src), 'the unload guard asks the outbox');
  assert.ok(/startPlan\(init\)/.test(src) && !/init\.picks\.length\)generate/.test(src), 'startup follows the plan, which includes an area alone');
  assert.ok(/const current=\(\)=>uploadRef\.current\?\.id===id/.test(src) && /if\(!current\(\)\)return;/.test(src), 'a rematch answer is dropped once its upload is gone');
  assert.ok(/pickerOf\(p\)/.test(src) && !/k in q/.test(src), 'a saved list restores the picker only through pickerOf');
  assert.ok(/readWalkCsv\(/.test(src) && /leftOutNote\(skipped\)/.test(src) && !/parseWalkCsv/.test(src), 'rows left out of an upload are reported');
  assert.ok(!/takeResults|pending\.current/.test(src), 'results go through the outbox');
});
