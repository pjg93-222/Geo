"""Checks of the synthetic walk world (walk_testkit.py): the stores open and line up with the source file, the oracle agrees
with an independent recomputation from the files and the geometry, and the output is deterministic.
Plain unittest, no Django; run from the repo root:  python -m unittest server.research.test_walk_testkit"""
import ast
import csv
import hashlib
import json
import math
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path

from tools import scores_schema as S

from . import walk_testkit as K

ROOT = Path(__file__).resolve().parents[2]
AS_OF = date(2026, 9, 4)             # the source file's date (statara_co_cd005_20260904.csv): ages are taken then
PRIMARIES = {'2014p': '2014-06-24', '2016p': '2016-06-28', '2018p': '2018-06-26', '2020p': '2020-06-30',
             '2022p': '2022-06-28', '2024p': '2024-06-25'}
GENERALS = {'2014g': '2014-11-04', '2016g': '2016-11-08', '2018g': '2018-11-06', '2020g': '2020-11-03',
            '2022g': '2022-11-08', '2024g': '2024-11-05'}
# (name, declared type) per table of the real stores, as PRAGMA table_info reports them (schema read once, no rows)
REAL_COLUMNS = {
    ('contacts', 'contacts'): [('id', 'TEXT'), ('name', 'TEXT'), ('email', 'TEXT'), ('email_match', 'TEXT'),
                               ('phone', 'TEXT'), ('wireless', 'TEXT'), ('wireless_match', 'TEXT'),
                               ('registration', 'TEXT'), ('residential', 'TEXT')],
    ('contacts', 'metadata'): [('key', 'TEXT'), ('value', 'TEXT')],
    ('scores', 'person'): [('pos', 'INTEGER'), ('person_id', 'TEXT'), ('hh', 'INTEGER'), ('age', 'INTEGER'),
                           ('party', 'TEXT'), ('reg_precinct', 'TEXT')],
    ('scores', 'metadata'): [('key', 'TEXT'), ('value', 'TEXT')],
    ('street', 'households'): [('id', 'TEXT'), ('basis', 'TEXT'), ('longitude', 'REAL'), ('latitude', 'REAL'),
                               ('address', 'TEXT'), ('count', 'INTEGER')],
    ('street', 'people'): [('household_id', 'TEXT'), ('id', 'TEXT'), ('name', 'TEXT')],
    ('street', 'metadata'): [('key', 'TEXT'), ('value', 'TEXT')],
    ('roads', 'roads'): [('id', 'INTEGER'), ('name', 'TEXT'), ('kind', 'TEXT'), ('geometry', 'TEXT')],
    ('roads', 'extents'): [('id', 'INT'), ('west', 'REAL'), ('east', 'REAL'), ('south', 'REAL'), ('north', 'REAL')],
}


def ro(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def read_source(path):
    with open(path, encoding='cp1252', newline='') as stream:
        reader = csv.reader(stream)
        header = next(reader)
        return header, [dict(zip(header, row)) for row in reader]


def squash(sql):
    return ' '.join(re.sub(r'IF NOT EXISTS ', '', sql).replace('"', "'").strip().rstrip(';').split())


def builder_sql(source, kind, name):
    """The DDL of `name` as the real builder's source text spells it (compared without importing the builder)."""
    text = (ROOT / source).read_text(encoding='utf-8')
    tail = r'\)\s*;' if kind != 'INDEX' else r'\)(?=["\'])'
    match = re.search(r'CREATE (?:VIRTUAL )?%s (?:IF NOT EXISTS )?%s\b.*?%s' % (kind, name, tail), text, re.S)
    assert match, (source, name)
    return squash(match.group(0))


def house_number(row):
    return int(re.match(r'\d+', row['svi_registration_addressrange']).group())


def area_of(row):
    return (row['svi_registration_state_senate_district'], row['svi_registration_state_house_district'],
            row['svi_registration_precinct_code'])


def age_on(day, born):
    return day.year - born.year - ((day.month, day.day) < (born.month, born.day))


def tallies(row):
    """{'pri','gen','abs': (voted, eligible)} of one source row by the contract rule, from the file's own text."""
    registered, born = date.fromisoformat(row['svi_registration_date']), date.fromisoformat(row['sii_dob'])
    out, mail = {}, 0
    for label, elections in (('pri', PRIMARIES), ('gen', GENERALS)):
        voted = eligible = 0
        for election, when in elections.items():
            day, code = date.fromisoformat(when), row['svi_vh_' + election]
            voted += bool(code)
            eligible += bool(code) or (registered <= day and age_on(day, born) >= 18)
            mail += bool(code) and code[0] in 'MA'
        out[label] = (voted, eligible)
    out['abs'] = (mail, out['pri'][1] + out['gen'][1])
    return out


def locate(points, p):
    """(distance, metres along, is left of the drawn direction) of p from a polyline; all in metres."""
    best, walked = None, 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        t = max(0.0, min(1.0, ((p[0] - x0) * dx + (p[1] - y0) * dy) / length ** 2))
        fx, fy = x0 + t * dx, y0 + t * dy
        d = math.hypot(p[0] - fx, p[1] - fy)
        if best is None or d < best[0] - 1e-9:
            best = (d, walked + t * length, dx * (p[1] - fy) - dy * (p[0] - fx) > 0, (p[0] - fx, p[1] - fy))
        walked += length
    return best


class WalkTestkitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temporary.name) / 'world'
        cls.stores = K.write_minimal_stores(cls.directory)
        cls.case = cls.stores.case
        cls.expected = cls.case.expected
        cls.header, cls.source_rows = read_source(cls.stores.source_csv)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def metric(self):
        try:
            from pyproj import Transformer
        except ImportError:
            self.skipTest('pyproj is not installed')
        return Transformer.from_crs(4326, 26913, always_xy=True).transform

    def block_points(self, block, to_metres):
        rows = {r[0]: r for r in K.road_rows()}
        points = []
        for ident in block['rows']:
            for lon, lat in json.loads(rows[ident][3])['coordinates']:
                p = to_metres(lon, lat)
                if not points or points[-1] != p:
                    points.append(p)
        return points

    # ---- stores ---------------------------------------------------------------------------------------------
    def test_stores_open_read_only_with_the_real_table_layout(self):
        for name in ('contacts', 'scores', 'street', 'roads'):
            with closing(ro(self.directory / (name + '.sqlite3'))) as connection:
                self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                for (store, table), columns in REAL_COLUMNS.items():
                    if store == name:
                        info = connection.execute('PRAGMA table_info(%s)' % table).fetchall()
                        self.assertEqual([(r[1], r[2]) for r in info], columns, (store, table))
        self.assertEqual(sorted(p.name for p in self.directory.iterdir()),
                         ['contacts.sqlite3', 'roads.sqlite3', 'scores.sqlite3', 'source.csv', 'street.sqlite3'])

    def test_table_definitions_are_the_ones_the_real_builders_write(self):
        contacts, street, roads = 'tools/build_contacts.py', 'tools/build_street.py', 'tools/build_road_context.py'
        wanted = {'contacts': [(contacts, 'TABLE', 'contacts'), (contacts, 'TABLE', 'metadata'),
                               (contacts, 'INDEX', 'contacts_name')],
                  'street': [(street, 'TABLE', 'metadata'), (street, 'TABLE', 'households'), (street, 'TABLE', 'people'),
                             (street, 'INDEX', 'household_longitude'), (street, 'INDEX', 'household_latitude'),
                             (street, 'INDEX', 'household_basis_id'), (contacts, 'INDEX', 'people_identifier')],
                  'roads': [(roads, 'TABLE', 'roads'), (roads, 'TABLE', 'extents')]}
        for store, items in wanted.items():
            with closing(ro(self.directory / (store + '.sqlite3'))) as connection:
                for source, kind, name in items:
                    mine = connection.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()
                    self.assertIsNotNone(mine, (store, name))
                    self.assertEqual(squash(mine[0]), builder_sql(source, kind, name), (store, name))
        with closing(ro(self.stores.scores_path)) as connection:
            subset = [c for c in S.PERSON_COLUMNS if c[0] in ('pos', 'person_id', 'hh', 'age', 'party', 'reg_precinct')]
            sql = dict(connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE name IN ('person','metadata','person_id')"))
            self.assertEqual(squash(sql['person']), squash(S.table_sql('person', subset)))
            self.assertEqual(squash(sql['metadata']), squash(S.TABLES[0]))
            self.assertIn(squash(sql['person_id']), [squash(i) for i in S.INDEXES])

    def test_every_store_names_the_same_source_file(self):
        sha = hashlib.sha256(self.stores.source_csv.read_bytes()).hexdigest()
        self.assertEqual(self.stores.source_sha256, sha)
        for name, key in (('contacts', 'source_sha256'), ('street', 'source_sha256'), ('scores', 'contacts_source_sha256')):
            with closing(ro(self.directory / (name + '.sqlite3'))) as connection:
                meta = {k: json.loads(v) for k, v in connection.execute('SELECT key, value FROM metadata')}
            self.assertEqual(meta[key], sha, name)
        with closing(ro(self.stores.contacts_path)) as connection:
            meta = {k: json.loads(v) for k, v in connection.execute('SELECT key, value FROM metadata')}
        self.assertEqual(meta['schema'], 'contacts-v1')
        self.assertEqual(meta['totals']['records'], len(self.source_rows))
        with closing(ro(self.stores.scores_path)) as connection:
            meta = {k: json.loads(v) for k, v in connection.execute('SELECT key, value FROM metadata')}
        self.assertEqual((meta['schema'], meta['n_people'], meta['w1']['status']),
                         (S.SCHEMA, len(self.source_rows), 'fixture'))

    def test_csv_row_i_is_contacts_rowid_i_plus_1_is_the_same_person_everywhere(self):
        n = len(self.source_rows)
        self.assertEqual(n, len(self.case.rows))
        contacts, scores, street = (ro(p) for p in (self.stores.contacts_path, self.stores.scores_path,
                                                    self.stores.street_path))
        with closing(contacts), closing(scores), closing(street):
            self.assertEqual(contacts.execute('SELECT COUNT(*), MIN(rowid), MAX(rowid) FROM contacts').fetchone(), (n, 1, n))
            self.assertEqual(scores.execute('SELECT COUNT(*), COUNT(DISTINCT person_id) FROM person').fetchone(), (n, n))
            for i, row in enumerate(self.source_rows):
                ident = K.opaque_id(K.SALT, 'p', row['sii_persist_id'])
                self.assertRegex(ident, r'^p_[0-9a-f]{64}$')
                found = contacts.execute('SELECT id, name FROM contacts WHERE rowid=?', (i + 1,)).fetchone()
                self.assertEqual(found[0], ident, i)
                self.assertEqual(found[1], ' '.join(row[f] for f in K.NAME_FIELDS if row[f]))
                self.assertEqual(self.case.rows[i]['person_id'], ident)
                person = scores.execute('SELECT age, party, reg_precinct FROM person WHERE person_id=?', (ident,)).fetchall()
                self.assertEqual(person, [(int(row['sii_age']), self.case.rows[i]['party'],
                                           row['svi_registration_precinct_code'])])
                mapped = street.execute("SELECT COUNT(*) FROM people p JOIN households h ON h.id=p.household_id "
                                        "WHERE p.id=? AND h.basis='registration'", (ident,)).fetchone()[0]
                self.assertEqual(mapped, 0 if self.case.rows[i]['lon'] is None else 1, i)
                self.assertEqual(street.execute("SELECT COUNT(*) FROM people p JOIN households h ON h.id=p.household_id "
                                                "WHERE p.id=? AND h.basis='residential'", (ident,)).fetchone()[0], 1)
            # pos follows the opaque id, not the source row
            order = [r[0] for r in scores.execute('SELECT person_id FROM person ORDER BY pos')]
            self.assertEqual(order, sorted(order))
            self.assertNotEqual(order, [r['person_id'] for r in self.case.rows])

    def test_street_households_follow_the_registration_address_and_coordinates(self):
        by_id = {r['person_id']: r for r in self.case.rows}
        with closing(ro(self.stores.street_path)) as street:
            rows = street.execute("SELECT h.id, h.longitude, h.latitude, h.address, h.count, p.id FROM households h "
                                  "JOIN people p ON p.household_id=h.id WHERE h.basis='registration'").fetchall()
            self.assertEqual(len(rows), self.expected['counts']['geocoded_people'])
            for ident, lon, lat, address, _, person in rows:
                row = by_id[person]
                self.assertEqual((lon, lat, address), (row['lon'], row['lat'], row['csv']['svi_registration_address']))
                self.assertEqual(self.expected['people'][person]['street_household_id'], ident)
            counts = dict(street.execute("SELECT id, count FROM households WHERE basis='registration'"))
            self.assertEqual(sum(counts.values()), self.expected['counts']['geocoded_people'])
            # the household count is the number of its people rows
            for ident, count in counts.items():
                members = street.execute('SELECT COUNT(*) FROM people WHERE household_id=?', (ident,)).fetchone()[0]
                self.assertEqual(members, count)
            self.assertEqual(street.execute("SELECT COUNT(*) FROM households WHERE basis='registration'").fetchone()[0],
                             self.expected['counts']['households'] - 1)
            # the household with no coordinates has none, and its residential location is far from every road
            lost = self.expected['doors'][self.expected['interpolated']['door']]['people'][0]
            self.assertIsNone(self.expected['people'][lost]['street_household_id'])
            self.assertEqual(street.execute("SELECT COUNT(*) FROM people p JOIN households h ON h.id=p.household_id "
                                            "WHERE p.id=? AND h.basis='registration'", (lost,)).fetchone()[0], 0)
            lon, lat = street.execute("SELECT h.longitude, h.latitude FROM people p JOIN households h ON h.id=p.household_id "
                                      "WHERE p.id=? AND h.basis='residential'", (lost,)).fetchone()
            self.assertGreater(abs(lon - K.ORIGIN[0]) * K.M_LON, 4000)

    def test_roads_store_holds_the_rows_and_a_matching_rtree(self):
        with closing(ro(self.stores.roads_path)) as roads:
            stored = roads.execute('SELECT id, name, kind, geometry FROM roads ORDER BY id').fetchall()
            self.assertEqual(stored, K.road_rows())
            for ident, name, kind, geometry in stored:
                coords = json.loads(geometry)['coordinates']
                west, east, south, north = roads.execute('SELECT west, east, south, north FROM extents WHERE id=?',
                                                         (ident,)).fetchone()
                self.assertAlmostEqual(west, min(c[0] for c in coords), places=4)
                self.assertAlmostEqual(east, max(c[0] for c in coords), places=4)
                self.assertAlmostEqual(south, min(c[1] for c in coords), places=4)
                self.assertAlmostEqual(north, max(c[1] for c in coords), places=4)
            # a hair around the origin touches only the two rows of Cedar Heights Dr that meet there (x = 0)
            hit = {r[0] for r in roads.execute('SELECT id FROM extents WHERE west<=? AND east>=? AND south<=? AND north>=?',
                                               (-104.79995, -104.80005, 38.85005, 38.84995))}
            self.assertEqual(hit, {2, 3})

    # ---- the source file ----------------------------------------------------------------------------------
    def test_source_file_has_the_real_column_names_and_decodes_as_cp1252(self):
        self.assertEqual(self.header, K.CSV_COLUMNS)
        self.assertEqual(len(set(self.header)), len(self.header))
        raw = self.stores.source_csv.read_bytes()
        self.assertFalse(raw.startswith(b'\xef\xbb\xbf'))
        self.assertIn('MUÑOZ'.encode('cp1252'), raw)
        self.assertNotIn('MUÑOZ'.encode('utf-8'), raw)
        for column in ('sii_gender', 'sii_dob', 'svi_voter_id', 'svi_registration_date', 'svi_registration_addressrange',
                       'svi_registration_predirection', 'svi_registration_streetname', 'svi_registration_suffix',
                       'svi_registration_postdirection', 'svi_registration_suitename', 'svi_registration_suiterange',
                       'svi_registration_city', 'svi_registration_zip', 'svi_registration_state_senate_district',
                       'svi_registration_state_house_district', 'svi_registration_precinct_code'):
            self.assertIn(column, self.header)
        for year in range(2014, 2025, 2):
            self.assertIn('svi_vh_%dp' % year, self.header)
            self.assertIn('svi_vh_%dg' % year, self.header)
        self.assertEqual([c for c in self.header if c.endswith('pp')],
                         ['svi_vh_2012pp', 'svi_vh_2016pp', 'svi_vh_2020pp', 'svi_vh_2024pp'])
        # what the real contacts builder would read from this file is there
        tree = ast.parse((ROOT / 'tools' / 'build_contacts.py').read_text(encoding='utf-8'))
        fields = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                      and any(getattr(t, 'id', '') == 'FIELDS' for t in n.targets))
        self.assertTrue(set(fields) <= set(self.header))
        for row in self.source_rows:
            self.assertEqual(len(row), len(self.header))

    def test_date_format_changes_only_the_date_columns(self):
        with tempfile.TemporaryDirectory() as other:
            stores = K.write_minimal_stores(other, date_format='%m/%d/%Y')
            header, rows = read_source(stores.source_csv)
        self.assertEqual(header, self.header)
        for plain, variant in zip(self.source_rows, rows):
            for column in header:
                if column in K.DATE_COLUMNS:
                    self.assertEqual(date.fromisoformat(plain[column]).strftime('%m/%d/%Y'), variant[column])
                else:
                    self.assertEqual(plain[column], variant[column], column)

    def test_no_real_looking_contact_data(self):
        for row in self.source_rows:
            for column in ('sii_phone', 'sii_phone_wireless'):
                if row[column]:
                    self.assertRegex(row[column], r'^(\(719\) |719-)555-01\d\d$')
            if row['sii_email']:
                self.assertTrue(row['sii_email'].endswith('@example.invalid'))
            self.assertRegex(row['svi_voter_id'], r'^91\d{7}$')
            self.assertEqual(row['svi_registration_state'], 'CO')

    # ---- the oracle against the files ---------------------------------------------------------------------------
    def test_tallies_match_an_independent_recomputation_from_the_file(self):
        self.assertEqual(K.PRIMARIES, PRIMARIES)
        self.assertEqual(K.GENERALS, GENERALS)
        self.assertEqual(K.AS_OF, AS_OF)
        people = self.expected['people']
        self.assertEqual(len(people), len(self.source_rows))
        for row in self.source_rows:
            ident = K.opaque_id(K.SALT, 'p', row['sii_persist_id'])
            want = tallies(row)
            entry = people[ident]
            for label in ('pri', 'gen', 'abs'):
                self.assertEqual((entry[label]['voted'], entry[label]['eligible']), want[label], (entry['key'], label))
            self.assertEqual(entry['age'], age_on(AS_OF, date.fromisoformat(row['sii_dob'])))
            self.assertEqual(int(row['sii_age']), entry['age'])
            self.assertEqual(row['svi_voter_id'], entry['voter_id'])
            self.assertEqual(entry['reg_date'], row['svi_registration_date'])

    def test_the_cases_the_tallies_are_meant_to_cover_are_there(self):
        people = self.expected['people']
        by_key = {p['key']: (pid, p) for pid, p in people.items()}
        # registered in 2025: nothing eligible, shown as "/", although a 2025 vote is on the file
        new = by_key['p25'][1]
        self.assertEqual([new[k]['eligible'] for k in ('pri', 'gen', 'abs')], [0, 0, 0])
        self.assertEqual([new[k] for k in ('pri_text', 'gen_text', 'abs_text')], ['/', '/', '/'])
        self.assertEqual(self.case.rows[self.expected['row_of']['p25']]['csv']['svi_vh_2025g'], 'MC')
        # 17 at the 2020 primary and voted in it; the day after / the day of the 18th birthday; registration day
        self.assertEqual((by_key['p11'][1]['pri']['eligible'], by_key['p11'][1]['pri']['voted']), (3, 1))
        self.assertEqual(by_key['p38'][1]['gen'], {'voted': 2, 'eligible': 2})
        self.assertEqual(by_key['p23'][1]['gen'], {'voted': 2, 'eligible': 3})
        # registered after elections they voted in
        self.assertEqual(by_key['p24'][1]['gen'], {'voted': 5, 'eligible': 5})
        # votes the window must ignore exist on the file: presidential primary, odd years, before 2014
        ignored = ('svi_vh_2016pp', 'svi_vh_2020pp', 'svi_vh_2024pp', 'svi_vh_2019g', 'svi_vh_2021g', 'svi_vh_2023g',
                   'svi_vh_2025g', 'svi_vh_2012g')
        for column in ignored:
            self.assertTrue(any(r[column] for r in self.source_rows), column)
        # vote codes: mail/absentee start with M or A; blanks are non-votes
        codes = {r[c] for r in self.source_rows for c in K.VOTE_COLUMNS}
        self.assertEqual(codes, {'', 'MC', 'PC', 'AP', 'AC', 'MP', 'PP', 'EP', 'YP', 'QP'})
        # names: accents, apostrophe, hyphen, Mc, suffixes
        shown = {p['name'] for p in people.values()}
        for name in ('José Muñoz', 'Zoë Brontë', "Beatriz O'Brien", 'Linh T Nguyen-Park', 'Donald McAllister JR',
                     'Walter B Strickland III'):
            self.assertIn(name, shown)
        self.assertEqual(self.expected['keys'], {key: pid for key, (pid, _) in by_key.items()})

    def test_display_names_are_what_scoresdb_title_name_makes_of_the_contact_names(self):
        try:
            from .scoresdb import title_name
        except Exception as error:           # Django or numpy missing: nothing to compare with
            self.skipTest('scoresdb is not importable: %s' % error)
        with closing(ro(self.stores.contacts_path)) as contacts:
            names = dict(contacts.execute('SELECT id, name FROM contacts'))
        for ident, entry in self.expected['people'].items():
            self.assertEqual(title_name(names[ident]), entry['name'])

    def test_street_parts_reduce_to_the_same_key_as_the_road_names(self):
        try:
            from .walk_names import display_street, house_int, name_key, street_key
        except Exception as error:
            self.skipTest('walk_names is not importable: %s' % error)
        roads = {name for _, name, _, _ in K.road_rows()}
        for street in self.expected['streets']:
            key = street_key('', street['streetname'], street['suffix'], '')
            self.assertIn(key, {name_key(n) for n in roads}, street['street'])
            self.assertEqual(display_street('', street['streetname'], street['suffix'], ''), street['street'])
        for door in self.expected['doors'].values():
            self.assertEqual(house_int(door['house']), door['house_int'])

    def test_the_window_is_the_one_the_shared_config_names(self):
        try:
            from . import walk_names as config
            mine = (('PRIMARIES', K.PRIMARIES), ('GENERALS', K.GENERALS), ('ABS_PREFIXES', K.ABS_PREFIXES))
            theirs = [getattr(config, name) for name, _ in mine]
        except Exception as error:
            self.skipTest('the shared window config is not importable: %s' % error)
        for (name, value), other in zip(mine, theirs):
            self.assertEqual(value, other, name)

    def test_doors_stops_streets_and_counts_recounted_from_the_file(self):
        counts, doors, stops = self.expected['counts'], self.expected['doors'], self.expected['stops']
        triple = lambda r: (r['svi_registration_city'], r['svi_registration_streetname'], r['svi_registration_addressrange'])
        self.assertEqual(len({triple(r) + (r['svi_registration_suiterange'],) for r in self.source_rows}), counts['doors'])
        self.assertEqual(len({triple(r) for r in self.source_rows}), counts['stops'])
        self.assertEqual(len({r['sii_household_id'] for r in self.source_rows}), counts['households'])
        self.assertEqual(len(doors), counts['doors'])
        self.assertEqual(sorted(k for ks in stops.values() for k in ks), sorted(doors))
        self.assertEqual(stops['d12'], ['d12', 'd13'])                       # one house, units A and B
        self.assertEqual([doors[k]['unit'] for k in stops['d12']], ['A', 'B'])
        self.assertEqual(doors['d02']['households'], 2)                       # two households, one door
        self.assertEqual(len(doors['d02']['people']), 3)
        for door in doors.values():
            self.assertEqual(door['parity'], 'even' if door['house_int'] % 2 == 0 else 'odd')
            self.assertEqual(door['house_int'], int(re.match(r'\d+', door['house']).group()))
        # the two Pine Sts: same house number, different cities and zips, kilometres apart
        pines = [d for d in doors.values() if d['street'] == 'Pine St' and d['house'] == '514']
        self.assertEqual(sorted(d['city'] for d in pines), ['COLORADO SPRINGS', 'FOUNTAIN'])
        self.assertEqual(len({d['stop'] for d in pines}), 2)
        by_street = {(s['street'], s['city']): s for s in self.expected['streets']}
        self.assertEqual(len(by_street), counts['streets'])
        for (label, city), entry in by_street.items():
            mine = [r for r in self.source_rows if r['svi_registration_city'] == city
                    and r['svi_registration_streetname'] == entry['streetname']]
            self.assertEqual(entry['people'], len(mine))
            self.assertEqual(entry['people_even'], sum(1 for r in mine if house_number(r) % 2 == 0))
            self.assertEqual(entry['people_odd'], len(mine) - entry['people_even'])
            self.assertEqual(entry['doors'], entry['doors_even'] + entry['doors_odd'])
        self.assertEqual(sum(s['doors'] for s in by_street.values()), counts['doors'])
        self.assertEqual(sum(s['people'] for s in by_street.values()), counts['people'])
        self.assertEqual(counts['geocoded_people'] + counts['ungeocoded_people'], counts['people'])

    def test_areas_precincts_and_combos(self):
        label = {(s['streetname'], s['city']): s['street'] for s in self.expected['streets']}
        precincts = {}
        for row in self.source_rows:
            precincts.setdefault(row['svi_registration_precinct_code'], set()).add(
                (row['svi_registration_state_senate_district'], row['svi_registration_state_house_district']))
        self.assertEqual(len(precincts), self.expected['counts']['precincts'])
        self.assertTrue(all(len(v) == 1 for v in precincts.values()))                 # a precinct lies in one district pair
        self.assertEqual(len({s for v in precincts.values() for s, _ in v}), 2)      # two senate districts
        self.assertEqual(sum(c['people'] for c in self.expected['combos']), self.expected['counts']['people'])
        self.assertEqual(sum(c['doors'] for c in self.expected['combos']), self.expected['counts']['doors'])
        for combo in self.expected['combos']:
            mine = [r for r in self.source_rows if area_of(r) == (combo['ss'], combo['sh'], combo['precinct'])
                    and r['svi_registration_city'] == combo['city']
                    and label[(r['svi_registration_streetname'], r['svi_registration_city'])] == combo['street']]
            self.assertEqual(len(mine), combo['people'], combo)
        # a precinct boundary crosses Cedar Heights Dr: both sides change precinct once, at the middle block
        runs = [r for r in self.expected['runs'] if r['street'] == 'Cedar Heights Dr']
        self.assertEqual([(r['parity'], len(r['doors'])) for r in runs], [('even', 7), ('even', 3), ('odd', 3), ('odd', 3)])
        self.assertEqual(len({r['precinct'] for r in runs}), 2)
        self.assertEqual(sum(len(r['doors']) for r in self.expected['runs']), self.expected['counts']['doors'])

    def test_positions_sides_and_the_parity_mismatch_against_the_geometry(self):
        to_metres = self.metric()
        doors = self.expected['doors']
        for block in self.expected['blocks']:
            points = self.block_points(block, to_metres)
            length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))
            self.assertAlmostEqual(block['length_m'], length, delta=0.5)
            along = []
            for key in block['doors']:
                door = doors[key]
                if not door['geocoded']:
                    continue
                distance, s, left, offset = locate(points, to_metres(*door['lonlat']))
                self.assertTrue(13.5 <= distance <= 17.5, (key, distance))
                along.append(s)
                compass = max(('N', (0, 1)), ('S', (0, -1)), ('E', (1, 0)), ('W', (-1, 0)),
                              key=lambda c: offset[0] * c[1][0] + offset[1] * c[1][1])[0]
                self.assertEqual(compass, door['side'], key)
                even = door['parity'] == 'even'
                expect_left = bool(block['even_left']) == even
                self.assertEqual(left, expect_left != (key in block['mismatched']), key)
                self.assertEqual(door['side_matches_parity'], key not in block['mismatched'])
            self.assertEqual(along, sorted(along), block['id'])                     # doors are listed by position
            self.assertEqual(block['consistent'] + block['inconsistent'], len(along))
        self.assertEqual([b['mismatched'] for b in self.expected['blocks'] if b['mismatched']], [['d07']])
        self.assertEqual(doors['d07']['parity'], 'even')
        self.assertEqual(doors['d07']['side'], 'S')
        self.assertEqual({b['id']: b['even_left'] for b in self.expected['blocks'] if b['even_left'] is not None},
                         {'chd1': 1, 'chd2': 1, 'chd3': 1, 'jun_s': 0, 'jun_n1': 0, 'rdg': 0, 'pine_n': 1, 'pine_f': 0})

    def test_even_left_for_judges_either_drawing_direction(self):
        for block in self.expected['blocks']:
            forward = K.even_left_for(block, block['from'], block['to'])
            self.assertEqual(forward, block['even_left'])
            if forward is not None:
                self.assertEqual(K.even_left_for(block, block['to'], block['from']), 1 - forward)

    def test_the_door_without_coordinates_sits_between_its_neighbours_in_proportion(self):
        to_metres = self.metric()
        info, doors = self.expected['interpolated'], self.expected['doors']
        lost = doors[info['door']]
        self.assertFalse(lost['geocoded'])
        self.assertEqual((lost['street'], lost['house'], lost['placed_by']), ('Cedar Heights Dr', '1136', 'interpolated'))
        block = next(b for b in self.expected['blocks'] if b['id'] == info['block'])
        points = self.block_points(block, to_metres)
        before, after = doors[info['before']], doors[info['after']]
        self.assertEqual((before['house'], after['house']), ('1132', '1152'))
        s_before = locate(points, to_metres(*before['lonlat']))[1]
        s_after = locate(points, to_metres(*after['lonlat']))[1]
        fraction = (lost['house_int'] - before['house_int']) / (after['house_int'] - before['house_int'])
        self.assertAlmostEqual(info['fraction'], fraction)
        self.assertNotAlmostEqual(fraction, 0.5)                                   # not just the midpoint
        self.assertAlmostEqual(info['s_m'], s_before + fraction * (s_after - s_before), delta=0.3)
        self.assertAlmostEqual(locate(points, to_metres(*info['centreline']))[1], info['s_m'], delta=0.3)
        self.assertEqual(info['order'], ['d01', 'd02', 'd03', 'd04', 'd07'])
        numbers = [doors[k]['house_int'] for k in info['order']]
        self.assertEqual(numbers, sorted(numbers))
        self.assertEqual(info['order'], [k for k in block['doors'] if doors[k]['parity'] == 'even'])

    def test_road_topology_recounted_from_the_roads_table(self):
        topology = self.expected['topology']
        with closing(ro(self.stores.roads_path)) as roads:
            kept = roads.execute("SELECT id, name, geometry FROM roads WHERE kind<>'S1100' ORDER BY id").fetchall()
            dropped = [r[0] for r in roads.execute("SELECT id FROM roads WHERE kind='S1100'")]
        self.assertEqual(dropped, topology['dropped_rows'])
        self.assertEqual([r[0] for r in kept], topology['kept_rows'])
        ends, names = {}, {i: n for i, n, _ in kept}
        for ident, _, geometry in kept:
            coords = json.loads(geometry)['coordinates']
            for end in (tuple(coords[0]), tuple(coords[-1])):
                ends.setdefault(end, []).append(ident)
        self.assertEqual(sorted((e, len(v)) for e, v in ends.items()),
                         sorted((tuple(n['lonlat']), n['degree']) for n in topology['nodes']))
        # components of the kept network; spans = rows chained through degree-2 nodes where the name stays the same
        network, chain = {i: i for i in names}, {i: i for i in names}

        def root(table, i):
            while table[i] != i:
                i = table[i]
            return i
        for ids in ends.values():
            for other in ids[1:]:
                network[root(network, other)] = root(network, ids[0])
            if len(ids) == 2 and names[ids[0]] and names[ids[0]] == names[ids[1]]:
                chain[root(chain, ids[1])] = root(chain, ids[0])
        self.assertEqual(len({root(network, i) for i in names}), topology['components'])
        spans = {}
        for i in names:
            spans.setdefault(root(chain, i), set()).add(i)
        self.assertEqual(len(spans), topology['spans'])
        self.assertEqual({frozenset(b['rows']) for b in self.expected['blocks']}, {frozenset(s) for s in spans.values()})

    def test_the_interstate_crosses_academy_blvd_without_a_shared_vertex(self):
        try:
            from shapely.geometry import LineString
        except ImportError:
            self.skipTest('shapely is not installed')
        rows = {r[0]: r for r in K.road_rows()}
        lines = {i: LineString(json.loads(rows[i][3])['coordinates']) for i in (12, 14)}
        self.assertTrue(lines[12].crosses(lines[14]))
        self.assertFalse(set(lines[12].coords) & set(lines[14].coords))
        self.assertEqual(rows[14][1:3], ('I- 625', 'S1100'))
        self.assertEqual(rows[11][1:3], ('Academy Blvd', 'S1200'))
        self.assertEqual(rows[13][1:3], ('', 'S1400'))

    def test_the_two_pine_streets_are_kilometres_apart_and_unconnected(self):
        to_metres = self.metric()
        rows = {r[0]: r for r in K.road_rows()}
        near = [to_metres(*c) for i in (9, 10) for c in json.loads(rows[i][3])['coordinates']]
        far = [to_metres(*c) for c in json.loads(rows[15][3])['coordinates']]
        gap = min(math.hypot(a[0] - b[0], a[1] - b[1]) for a in near for b in far)
        self.assertTrue(2500 < gap < 3500, gap)
        self.assertEqual(self.expected['topology']['components'], 2)

    # ---- determinism --------------------------------------------------------------------------------------------
    def test_oracle_is_json_and_repeatable(self):
        again = K.hand_case()
        self.assertEqual(json.dumps(self.expected, sort_keys=True), json.dumps(again.expected, sort_keys=True))
        self.assertEqual(self.case.rows, again.rows)
        rows, expected = again
        self.assertIs(rows, again.rows)
        self.assertIs(expected, again.expected)
        self.assertEqual(json.loads(json.dumps(self.expected)), self.expected)

    def test_two_writes_are_byte_identical_and_a_rewrite_replaces_in_place(self):
        names = ('source.csv', 'contacts.sqlite3', 'scores.sqlite3', 'street.sqlite3', 'roads.sqlite3')
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            a, b = K.write_minimal_stores(first), K.write_minimal_stores(second)
            self.assertEqual(a.source_sha256, b.source_sha256)
            for name in names:
                self.assertEqual((Path(first) / name).read_bytes(), (Path(second) / name).read_bytes(), name)
                self.assertEqual((Path(first) / name).read_bytes(), (self.directory / name).read_bytes(), name)
            K.write_minimal_stores(first)                                         # over the existing files
            for name in names:
                self.assertEqual((Path(first) / name).read_bytes(), (Path(second) / name).read_bytes(), name)
            self.assertEqual(sorted(p.name for p in Path(first).iterdir()), sorted(names))

    def test_a_case_can_be_passed_in(self):
        rows = [r for r in self.case.rows if r['key'] != 'p01']
        with tempfile.TemporaryDirectory() as directory:
            stores = K.write_minimal_stores(directory, K.Case(rows, self.expected))
            with closing(ro(stores.contacts_path)) as contacts:
                self.assertEqual(contacts.execute('SELECT COUNT(*) FROM contacts').fetchone()[0], len(rows))
            self.assertEqual(len(read_source(stores.source_csv)[1]), len(rows))


if __name__ == '__main__':
    unittest.main()
