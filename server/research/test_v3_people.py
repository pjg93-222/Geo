"""Person-level v3 endpoints: access rules, no-store, exact parameter lists, paging, filters, balanced view, names,
demo mode, and the profile against the SQLite store. Synthetic data only (sentinel names in the fake contact layer)."""
import json
import re
import sqlite3
import tempfile
import zlib
from pathlib import Path

import numpy as np
from django.test import Client, TestCase, override_settings
from shapely.geometry import Point, shape

from tools import field_labels as FL
from tools.demo_names import demo_name

from . import bundles, scoresdb, v3_cache, v3_demo_geo
from .v3_people_views import rank_text
from .v3_testkit import build_world

LOOPBACK = dict(HTTP_HOST='localhost')
FORBIDDEN_KEYS = {'address', 'email', 'phone', 'wireless', 'registration_address', 'residential', 'model_source_row',
                  'sii_persist_id', 'street', 'latitude', 'longitude'}
REAL_MARKERS = re.compile(r'SENTINEL|@example\.invalid|555-\d{4}', re.I)


def walk(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield 'key', key
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)
    else:
        yield 'value', value


@override_settings(LOCAL_ADMIN_ACCESS=True, HOSTED=False, DEMO_MODE=False)
class V3PeopleTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.world = build_world(Path(cls.temporary.name))
        cls.override = override_settings(DATA_DIR=cls.world.directory)
        cls.override.enable()
        scoresdb.reset()
        v3_cache.forget()
        bundles.load.cache_clear()
        bundles.import_bundle(cls.world.zip_path, expected_sha256=cls.world.zip_sha256)
        cls.scores = sqlite3.connect('file:%s?mode=ro' % (cls.world.directory / 'scores.sqlite3').as_posix(), uri=True)
        cls.scores.row_factory = sqlite3.Row
        cls.contacts = sqlite3.connect('file:%s?mode=ro' % (cls.world.directory / 'contacts.sqlite3').as_posix(),
                                       uri=True)
        cls.n = cls.scores.execute('SELECT COUNT(*) FROM person').fetchone()[0]

    @classmethod
    def tearDownClass(cls):
        cls.scores.close()
        cls.contacts.close()
        scoresdb.reset()
        v3_cache.forget()
        cls.override.disable()
        cls.temporary.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.client = Client(**LOOPBACK)

    def get(self, url, client=None, **extra):
        return (client or self.client).get(url, **extra)

    def person_id(self, pos=1):
        return self.scores.execute('SELECT person_id FROM person WHERE pos=?', (pos,)).fetchone()[0]

    def street_id(self):
        return self.scores.execute('SELECT h_id FROM street_household LIMIT 1').fetchone()[0]

    def endpoints(self):
        return ['/api/v3/people?basis=registration', '/api/v3/person?id=' + self.person_id(),
                '/api/v3/household?basis=registration&id=' + self.street_id(),
                '/api/v3/street?bbox=-104.90,38.79,-104.845,38.84&zoom=16&basis=registration',
                '/api/v3/search?q=sentinel']

    # ---- access, headers, parameters --------------------------------------------------------------------------
    def test_local_only_rules(self):
        for url in self.endpoints():
            self.assertEqual(self.get(url).status_code, 200, url)
            self.assertEqual(self.get(url, Client(HTTP_HOST='localhost', REMOTE_ADDR='192.0.2.10')).status_code, 403)
            for header in ('HTTP_FORWARDED', 'HTTP_X_FORWARDED_FOR', 'HTTP_X_FORWARDED_HOST', 'HTTP_X_FORWARDED_PROTO'):
                self.assertEqual(self.get(url, Client(HTTP_HOST='localhost', **{header: '127.0.0.1'})).status_code, 403)
            with override_settings(HOSTED=True):
                self.assertEqual(self.get(url).status_code, 403)
            with override_settings(LOCAL_ADMIN_ACCESS=False):
                self.assertEqual(self.get(url).status_code, 403)
            for method in ('post', 'put', 'patch', 'delete', 'head'):
                response = getattr(self.client, method)(url)
                self.assertEqual(response.status_code, 405, (method, url))
                self.assertEqual(response['Allow'], 'GET')

    def test_responses_are_no_store_json_without_export(self):
        for url in self.endpoints():
            response = self.get(url)
            self.assertEqual(response['Cache-Control'], 'no-store, private')
            self.assertEqual(response['Pragma'], 'no-cache')
            self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
            self.assertTrue(response['Content-Type'].startswith('application/json'))
            self.assertFalse(response.has_header('ETag'))
        for url in ('/api/v3/people?basis=registration&format=csv', '/api/v3/people?basis=registration&limit=500',
                    '/api/v3/people?basis=registration&page_size=1000', '/api/v3/people?export=1',
                    '/api/v3/people/export', '/api/v3/people.csv', '/api/v3/bulk'):
            self.assertIn(self.get(url).status_code, (400, 404), url)

    def test_exact_parameter_whitelists(self):
        pid, hid = self.person_id(), self.street_id()
        bad = [
            '/api/v3/people?foo=1', '/api/v3/people?reg=X', '/api/v3/people?reg=D&reg=R', '/api/v3/people?grade=AA',
            '/api/v3/people?grade=E', '/api/v3/people?grade=', '/api/v3/people?lean=3', '/api/v3/people?balanced=2',
            '/api/v3/people?sort=name', '/api/v3/people?after=-1', '/api/v3/people?after=abc',
            '/api/v3/people?level=tract', '/api/v3/people?area=08041000101',
            '/api/v3/people?level=block&area=123', '/api/v3/people?level=zip&area=80903&basis=other',
            '/api/v3/people?demo=0', '/api/v3/person', '/api/v3/person?id=bad', '/api/v3/person?id=%s&id=%s' % (pid, pid),
            '/api/v3/person?id=%s&extra=1' % pid, '/api/v3/household?id=%s' % hid,
            '/api/v3/household?id=%s&basis=residential' % hid, '/api/v3/household?id=h_bad&basis=registration',
            '/api/v3/street?bbox=-104.9,38.8,-104.8,38.9&zoom=16', '/api/v3/street?bbox=1,2,3&zoom=16&basis=registration',
            '/api/v3/street?bbox=-105,38,-104,39&zoom=16&basis=registration',
            '/api/v3/street?bbox=-104.9,38.8,-104.85,38.85&zoom=14&basis=registration',
            '/api/v3/street?bbox=-104.9,38.8,-104.85,38.85&zoom=abc&basis=registration',
            '/api/v3/street?bbox=-110,38.8,-109.99,38.85&zoom=16&basis=registration',
            '/api/v3/search', '/api/v3/search?q=a', '/api/v3/search?q=' + 'x' * 81, '/api/v3/search?q=ab&q=cd',
            '/api/v3/search?q=%22%20OR%20%22&extra=1',
        ]
        for url in bad:
            self.assertEqual(self.get(url).status_code, 400, url)

    # ---- paging -------------------------------------------------------------------------------------------------
    def test_pages_are_exactly_50_and_keyset_paging_is_consistent(self):
        first = self.get('/api/v3/people?basis=registration').json()
        self.assertEqual(len(first['rows']), 50)
        self.assertEqual((first['total'], first['first'], first['last'], first['page'], first['pages']),
                         (self.n, 1, 50, 1, -(-self.n // 50)))
        self.assertIsNone(first['prev'])
        seen, cursor, pages = [r['person_id'] for r in first['rows']], first['next'], 1
        while cursor:
            page = self.get('/api/v3/people?basis=registration&after=%s&dir=next' % cursor).json()
            self.assertLessEqual(len(page['rows']), 50)
            self.assertEqual(page['page'], pages + 1)
            self.assertEqual(page['first'], len(seen) + 1)
            seen += [r['person_id'] for r in page['rows']]
            cursor, pages = page['next'], pages + 1
        self.assertEqual(len(seen), self.n)
        self.assertEqual(len(set(seen)), self.n)
        expected = [r[0] for r in self.scores.execute('SELECT person_id FROM person ORDER BY pos')]
        self.assertEqual(seen, expected)
        # back one page from page 3
        second = self.get('/api/v3/people?basis=registration&after=%s&dir=next' % first['next']).json()
        third = self.get('/api/v3/people?basis=registration&after=%s&dir=next' % second['next']).json()
        back = self.get('/api/v3/people?basis=registration&after=%s&dir=prev' % third['prev']).json()
        self.assertEqual([r['person_id'] for r in back['rows']], [r['person_id'] for r in second['rows']])
        self.assertEqual(back['prev'], second['prev'])

    def test_rank_strings_and_values_match_the_store(self):
        self.assertEqual(rank_text(1, 1), '1')
        self.assertEqual(rank_text(2418, 3), '=2,418')
        self.assertEqual(rank_text(12345, None), '12,345')
        page = self.get('/api/v3/people?basis=registration').json()
        for row in page['rows']:
            r = self.scores.execute('SELECT * FROM person WHERE person_id=?', (row['person_id'],)).fetchone()
            self.assertEqual(row['rank'], rank_text(r['ovr_rank'], r['ovr_tie_n']))
            self.assertAlmostEqual(row['ovr_pct'], r['ovr_pct'], places=5)
            self.assertAlmostEqual(row['lo'], r['ovr_pct_lo90'], places=5)
            self.assertAlmostEqual(row['hi'], r['ovr_pct_hi90'], places=5)
            self.assertAlmostEqual(row['cua_pct'], r['cua_pct'], places=5)
            self.assertAlmostEqual(row['part_pct'], r['part_pct'], places=5)
            self.assertAlmostEqual(row['soi_pct'], r['soi_pct'], places=5)
            self.assertEqual(row['ovr_grade'], r['ovr_grade'])
            self.assertAlmostEqual(row['within_pct'], r['ovr_pct_within_reg'], places=5)
            expected = self.stored_outlook(r['pos'])
            self.assertEqual(row['lean_code'], expected)
            if expected != 'U':
                self.assertEqual(row['lean_label'], ('Very conservative', 'Somewhat conservative', 'Middle outlook',
                                                   'Somewhat liberal', 'Very liberal')[expected + 2])
            else:
                self.assertIn(row['lean_label'], ('Unknown', 'Multiple responses'))
            self.assertEqual(row['within_group'], FL.REG_GROUP_LABELS[FL.REG_GROUPS[r['reg_group']]])

    def test_ties_get_an_equals_prefix(self):
        path = self.world.directory / 'scores.sqlite3'
        writer = sqlite3.connect(path)
        original = writer.execute('SELECT pos, ovr_tie_n FROM person WHERE pos IN (3, 4)').fetchall()
        try:
            writer.execute('UPDATE person SET ovr_tie_n=2 WHERE pos IN (3, 4)')
            writer.commit()
            scoresdb.reset()

            page = self.get('/api/v3/people?basis=registration').json()
            ranks = [r['rank'] for r in page['rows'][:5]]
            self.assertTrue(ranks[2].startswith('=') and ranks[3].startswith('='), ranks)
            self.assertFalse(ranks[0].startswith('=') or ranks[4].startswith('='), ranks)
        finally:
            writer.executemany('UPDATE person SET ovr_tie_n=? WHERE pos=?', [(t, pos) for pos, t in original])
            writer.commit()
            writer.close()
            scoresdb.reset()

    def test_opinion_categories_preserve_missing_conflicting_and_model_filled_responses(self):
        from types import SimpleNamespace
        info = SimpleNamespace(value_dict={fid: {0: '0', 1: '1'} for fid in range(34, 39)})
        values = np.zeros(40, dtype='<u2')
        self.assertEqual(scoresdb.outlook_fields(scoresdb.outlook_code(info, values)), ('U', 'Unknown'))
        values[34] = 1
        self.assertEqual(scoresdb.outlook_fields(scoresdb.outlook_code(info, values)), (-2, 'Very conservative'))
        values[38] = 1   # very conservative + very liberal skips three steps
        self.assertEqual(scoresdb.outlook_fields(scoresdb.outlook_code(info, values)), ('U', 'Multiple responses'))
        # The category reads values only: it does not discard stored model fills or use a numerical lean score.
        values[34] = 0
        self.assertEqual(scoresdb.outlook_fields(scoresdb.outlook_code(info, values)), (2, 'Very liberal'))

    def test_collapse_outlook_rule(self):
        # positions: 0 very conservative, 1 somewhat conservative, 2 middle, 3 somewhat liberal, 4 very liberal
        U, M = scoresdb.OUTLOOK_UNKNOWN, scoresdb.OUTLOOK_MULTIPLE
        for flags, want in (
                ([], U),
                ([0], -2), ([1], -1), ([2], 0), ([3], 1), ([4], 2),            # one flag is itself
                ([0, 1], -2), ([3, 4], 2),                                      # very + somewhat: the very one
                ([1, 2], -1), ([2, 3], 1),                                      # next to middle: the non-middle one
                ([2, 3, 4], 2), ([0, 1, 2], -2),                                # run to one side: the far end
                ([1, 2, 3], M), ([0, 1, 2, 3], M), ([0, 1, 2, 3, 4], M),        # run across middle
                ([0, 4], M), ([0, 2], M), ([1, 3], M), ([2, 4], M), ([0, 1, 3], M)):   # a gap
            self.assertEqual(scoresdb.collapse_outlook(flags), want, flags)
            self.assertEqual(scoresdb.collapse_outlook(flags[::-1]), want, flags)

    # ---- filters, sorts and the balanced view ------------------------------------------------------------------
    def count(self, where='1=1', *args):
        return self.scores.execute('SELECT COUNT(*) FROM person WHERE ' + where, args).fetchone()[0]

    def stored_outlook(self, pos):
        raw = zlib.decompress(self.scores.execute('SELECT blob FROM evidence WHERE pos=?', (pos,)).fetchone()[0])
        values = np.frombuffer(raw, dtype='<u2', count=40)
        selected = []
        for fid in range(34, 39):
            literal = self.scores.execute('SELECT literal FROM value_dict WHERE fid=? AND code=?',
                                         (fid, int(values[fid]))).fetchone()
            if literal and literal[0] in ('1', '1.0'):
                selected.append(fid - 34)
        code = scoresdb.collapse_outlook(selected)
        return 'U' if abs(code) == 3 else code

    def test_filters_match_sql(self):
        def total(query):
            return self.get('/api/v3/people?basis=registration&' + query).json()['total']
        tract = self.scores.execute('SELECT reg_tract FROM person GROUP BY reg_tract ORDER BY COUNT(*) DESC').fetchone()[0]
        zip_ = self.scores.execute('SELECT reg_zip FROM person GROUP BY reg_zip ORDER BY COUNT(*) DESC').fetchone()[0]
        block = self.scores.execute('SELECT reg_block FROM person GROUP BY reg_block ORDER BY COUNT(*) DESC').fetchone()[0]
        precinct = self.scores.execute('SELECT reg_precinct FROM person GROUP BY reg_precinct '
                                       'ORDER BY COUNT(*) DESC').fetchone()[0]
        for group, code in (('D', 0), ('R', 1), ('U', 2), ('O', 3)):
            self.assertEqual(total('reg=' + group), self.count('reg_group=?', code))
        self.assertEqual(total('grade=AB'), self.count("ovr_grade IN ('A','B')"))
        self.assertEqual(total('grade=D'), self.count("ovr_grade='D'"))
        for band in (-2, -1, 0, 1, 2):
            self.assertEqual(total('lean=%d' % band), sum(self.stored_outlook(pos) == band for pos in range(1, self.n + 1)))
        self.assertEqual(total('lean=U'), sum(self.stored_outlook(pos) == 'U' for pos in range(1, self.n + 1)))
        self.assertEqual(total('level=tract&area=' + tract), self.count('reg_tract=?', tract))
        self.assertEqual(total('level=zip&area=' + zip_), self.count('reg_zip=?', zip_))
        self.assertEqual(total('level=block&area=' + block), self.count('reg_block=?', block))
        self.assertEqual(total('level=precinct&area=' + precinct), self.count('reg_precinct=?', precinct))
        self.assertEqual(total('reg=R&grade=AB&level=zip&area=' + zip_),
                         self.count("reg_group=1 AND ovr_grade IN ('A','B') AND reg_zip=?", zip_))
        self.assertEqual(total('level=tract&area=08041999999'), 0)         # well-formed but absent
        residence = self.scores.execute('SELECT res_zip FROM person GROUP BY res_zip ORDER BY COUNT(*) DESC').fetchone()[0]
        self.assertEqual(self.get('/api/v3/people?basis=residence&level=zip&area=%s' % residence).json()['total'],
                         self.count('res_zip=?', residence))

    def test_sorts_follow_the_stored_ranks(self):
        for sort, column in (('cua', 'cua_rank'), ('part', 'part_rank'), ('soi', 'soi_rank')):
            page = self.get('/api/v3/people?basis=registration&sort=' + sort).json()
            expected = [r[0] for r in self.scores.execute(
                'SELECT person_id FROM person ORDER BY %s, pos LIMIT 50' % column)]
            self.assertEqual([r['person_id'] for r in page['rows']], expected, sort)
        page = self.get('/api/v3/people?basis=registration&sort=within').json()
        expected = [r[0] for r in self.scores.execute(
            'SELECT person_id FROM person ORDER BY ovr_pct_within_reg DESC, pos LIMIT 50')]
        self.assertEqual([r['person_id'] for r in page['rows']], expected)

    def test_balanced_view_reproduces_the_hamilton_allocations(self):
        info = scoresdb.info()
        counts = info.meta['reg_group_counts']
        for k in (100, 4913, 49131):
            frozen = info.meta['balanced']['k'][str(k)]['K_actual']
            mask = info.balanced_mask(k)
            code = info.columns['reg_group']
            got = {g: int(((code == i) & mask).sum()) for i, g in enumerate(FL.REG_GROUPS)}
            self.assertEqual(got, frozen, k)
            # K_actual never goes below the seats (ties are kept) and never above the group
            for g in FL.REG_GROUPS:
                self.assertGreaterEqual(got[g], min(info.seats(k)[g], counts[g]))
        page = self.get('/api/v3/people?basis=registration&balanced=1').json()
        frozen = info.meta['balanced']['k']['100']['K_actual']
        self.assertEqual(page['total'], sum(frozen.values()))
        seen, cursor = list(page['rows']), page['next']
        while cursor:
            more = self.get('/api/v3/people?basis=registration&balanced=1&after=%s' % cursor).json()
            seen += more['rows']
            cursor = more['next']
        by_group = {}
        for row in seen:
            by_group[row['within_group']] = by_group.get(row['within_group'], 0) + 1
        self.assertEqual({FL.REG_GROUP_LABELS[g]: v for g, v in frozen.items()}, by_group)
        # rows keep their global standing order (ranks have gaps, as in the view definition)
        positions = [self.scores.execute('SELECT pos FROM person WHERE person_id=?', (r['person_id'],)).fetchone()[0]
                     for r in seen]
        self.assertEqual(positions, sorted(positions))

    # ---- names, demo mode and privacy --------------------------------------------------------------------------
    def test_real_names_come_from_the_contact_layer_by_person_id(self):
        page = self.get('/api/v3/people?basis=registration').json()
        self.assertFalse(self.get('/api/v3/people?basis=registration').has_header('X-CU-Demo'))
        for row in page['rows']:
            stored = self.contacts.execute('SELECT name FROM contacts WHERE id=?', (row['person_id'],)).fetchone()[0]
            self.assertEqual(row['name'], scoresdb.title_name(stored))
            self.assertTrue(row['name'].startswith('Sentinel_real_name_'))
        profile = self.get('/api/v3/person?id=' + page['rows'][3]['person_id']).json()
        self.assertEqual(profile['name'], page['rows'][3]['name'])
        for member in profile['household']['members']:
            stored = self.contacts.execute('SELECT name FROM contacts WHERE id=?', (member['person_id'],)).fetchone()[0]
            self.assertEqual(member['name'], scoresdb.title_name(stored))
        found = self.get('/api/v3/search?q=sentinel real name 0007').json()['people']
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['name'], 'Sentinel_real_name_0007')

    def demo_urls(self):
        pid = self.person_id()
        return [f'/api/v3/people?basis=registration&after={a}' for a in (0, 50, 700)] + [
            '/api/v3/people?basis=registration&balanced=1', '/api/v3/people?basis=registration&sort=cua',
            '/api/v3/person?id=' + pid, '/api/v3/person?id=' + self.person_id(777),
            '/api/v3/household?basis=registration&id=' + self.street_id(),
            '/api/v3/street?bbox=-104.90,38.79,-104.845,38.84&zoom=16&basis=registration',
            '/api/v3/search?q=a%20b', '/api/v3/search?q=ni', '/api/v3/search?q=sentinel']

    @override_settings(DEMO_MODE=True)
    def test_demo_mode_never_returns_a_real_name_address_email_or_phone(self):
        info = scoresdb.info()
        for url in self.demo_urls():
            response = self.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertEqual(response['X-CU-Demo'], '1')
            text = response.content.decode('utf-8')
            self.assertIsNone(REAL_MARKERS.search(text), url)
            for kind, value in walk(response.json()):
                if kind == 'key':
                    self.assertNotIn(value.lower(), FORBIDDEN_KEYS, url)
        page = self.get('/api/v3/people?basis=registration').json()
        for row in page['rows']:
            hh = self.scores.execute('SELECT hh FROM person WHERE person_id=?', (row['person_id'],)).fetchone()[0]
            self.assertEqual(row['name'], demo_name(info.demo_key, row['person_id'], hh))
        # a real name finds nothing, and the contact layer was never attached on this connection
        self.assertEqual(self.get('/api/v3/search?q=sentinel').json()['people'], [])
        conn = scoresdb.connection()
        self.assertNotIn('contacts', [r[1] for r in conn.execute('PRAGMA database_list')])

    def test_demo_flag_is_display_only_per_request_and_legacy_routes_are_closed(self):
        response = self.get('/api/v3/people?basis=registration&demo=1')
        self.assertEqual(response['X-CU-Demo'], '1')
        self.assertIsNone(REAL_MARKERS.search(response.content.decode('utf-8')))
        for path in ('/api/contacts', '/api/person', '/api/household', '/api/street', '/api/street-preload'):
            self.assertEqual(self.get(path + '?demo=1').status_code, 403, path)
        with override_settings(DEMO_MODE=True):
            for path in ('/api/contacts', '/api/person', '/api/household', '/api/street', '/api/street-preload'):
                self.assertEqual(self.get(path).status_code, 403, path)

    def test_no_person_endpoint_returns_address_email_phone_or_source_row_keys(self):
        for url in self.endpoints():
            for kind, value in walk(self.get(url).json()):
                if kind == 'key':
                    self.assertNotIn(value.lower(), FORBIDDEN_KEYS, url)
        # the real mode returns names but still no contact details
        text = self.get('/api/v3/people?basis=registration').content.decode('utf-8')
        self.assertIsNone(re.search(r'@example\.invalid|555-\d{4}|SENTINEL_ADDRESS|SENTINEL_STREET', text))

    # ---- the profile equals the store --------------------------------------------------------------------------
    def decode(self, pos):
        blob = self.scores.execute('SELECT blob FROM evidence WHERE pos=?', (pos,)).fetchone()[0]
        raw = zlib.decompress(blob)
        self.assertEqual(len(raw), 440)
        return {'values': np.frombuffer(raw, '<u2', 40, 0), 'status': np.frombuffer(raw, 'u1', 40, 80),
                'p': np.frombuffer(raw, '<f4', 40, 120), 'exposure': np.frombuffer(raw, '<f4', 40, 280)}

    def test_every_person_has_a_profile(self):
        self.assertEqual(self.scores.execute('SELECT COUNT(*) FROM evidence').fetchone()[0], self.n)
        for pos in (1, 2, 3, 250, 749, 750, 751, self.n - 1, self.n):
            pid = self.person_id(pos)
            response = self.get('/api/v3/person?id=' + pid)
            self.assertEqual(response.status_code, 200, pos)
            self.assertEqual(sum(g['n_fields'] for g in response.json()['groups']), 40)

    def test_profile_values_equal_the_sqlite_store(self):
        value_dict = {}
        for fid, code, literal in self.scores.execute('SELECT fid, code, literal FROM value_dict'):
            value_dict.setdefault(fid, {})[code] = literal
        for pos in (1, 2, 17, 233, 640, 901, self.n):
            r = self.scores.execute('SELECT * FROM person WHERE pos=?', (pos,)).fetchone()
            body = self.get('/api/v3/person?id=' + r['person_id']).json()
            self.assertEqual((body['age'], body['household_size'], body['registration']),
                             (r['age'], r['hh_size'], FL.REG_GROUPS[r['reg_group']]))
            self.assertEqual((body['block'], body['precinct']), (r['reg_block'] or '', r['reg_precinct'] or ''))
            header = body['header']
            self.assertAlmostEqual(header['pct'], r['ovr_pct'], places=5)
            self.assertAlmostEqual(header['lo'], r['ovr_pct_lo90'], places=5)
            self.assertAlmostEqual(header['hi'], r['ovr_pct_hi90'], places=5)
            self.assertEqual(header['grade'], r['ovr_grade'])
            self.assertAlmostEqual(header['measured_pct'], r['ovr_measured_pct'], places=5)
            self.assertAlmostEqual(header['within_pct'], r['ovr_pct_within_reg'], places=5)
            for card in ('cua', 'part', 'soi'):
                for field, column in (('score', '_score'), ('pct', '_pct'), ('lo', '_pct_lo90'), ('hi', '_pct_hi90')):
                    self.assertAlmostEqual(body['cards'][card][field], r[card + column], places=5)
                self.assertEqual(body['cards'][card]['grade'], r[card + '_grade'])
            lean = body['cards']['lean']
            self.assertAlmostEqual(lean['lean'], r['lean'], places=5)
            self.assertEqual((lean['grade'], lean['undetermined']), (r['lean_grade'], bool(r['lean_undet'])))
            ev = self.decode(pos)
            flags = r['flags']
            code50 = bool(flags & (1 << 3))
            self.assertEqual([g['id'] for g in body['groups']], ['cua', 'part', 'soi', 'lean'])
            self.assertEqual([len(g['fields']) for g in body['groups']], [12, 10, 7, 11])
            fid = 0
            totals = {}
            for group in body['groups']:
                self.assertEqual(group['n_fields'], len(group['fields']))
                self.assertEqual(group['observed'], sum(1 for f in group['fields'] if f['status'] == 'observed'))
                for field in group['fields']:
                    spec = FL.FIELDS[fid]
                    self.assertEqual((field['field'], field['label']), (spec['name'], spec['label']))
                    self.assertEqual(field['status'] == 'observed', int(ev['status'][fid]) == 0)
                    literal = value_dict[fid][int(ev['values'][fid])]
                    expected = FL.CODE50_VALUE if (code50 and spec['name'] == 'sai_digital_reachability_score') \
                        else FL.value_text(spec['name'], literal)
                    self.assertEqual(field['value'], expected)
                    exposure = float(ev['exposure'][fid])
                    self.assertAlmostEqual(field['exposure'], 0.0 if np.isnan(exposure) else exposure, places=5)
                    points = spec['points_code50'] if code50 else spec['points']
                    self.assertAlmostEqual(field['contribution'], (0.0 if np.isnan(exposure) else exposure) * points,
                                           places=4)
                    if field['status'] == 'observed':
                        self.assertIsNone(field['p'])
                    totals[group['id']] = totals.get(group['id'], 0.0) + field['contribution']
                    fid += 1
            # the field contributions add up to the index score (and the lean)
            for group, column in (('cua', 'cua_score'), ('part', 'part_score'), ('soi', 'soi_score'), ('lean', 'lean')):
                self.assertAlmostEqual(totals[group], r[column], delta=1e-3, msg=(pos, group))
            members = self.scores.execute('SELECT person_id, age, ovr_pct, ovr_grade FROM person WHERE hh=? ORDER BY pos',
                                          (r['hh'],)).fetchall()
            got = {m['person_id']: m for m in body['household']['members']}
            self.assertEqual(set(got), {m['person_id'] for m in members})
            self.assertTrue(body['household']['members'][0]['self'])
            for m in members:
                self.assertEqual((got[m['person_id']]['age'], got[m['person_id']]['grade']), (m['age'], m['ovr_grade']))
                self.assertAlmostEqual(got[m['person_id']]['ovr_pct'], m['ovr_pct'], places=5)
            household = self.scores.execute('SELECT hh_ovr_mean_pct FROM household WHERE hh=?', (r['hh'],)).fetchone()
            self.assertAlmostEqual(body['household']['mean_pct'], household[0], places=5)
            self.assertEqual(body['household']['code'], r['hh'])

    def test_area_context_equals_the_area_table(self):
        columns = {}
        for row in self.world.fixture.area_rows:
            columns[(row['basis'], row['level'], row['area_id'], row['group'])] = row
        for pos in (1, 5, 60, 400):
            r = self.scores.execute('SELECT person_id, reg_block, reg_precinct FROM person WHERE pos=?', (pos,)).fetchone()
            body = self.get('/api/v3/person?id=' + r['person_id']).json()
            for level, ident in (('block', r['reg_block']), ('precinct', r['reg_precinct'])):
                ref = body['area_context'][level]
                source = columns.get(('registration', level, ident, 'ovr'))
                self.assertEqual((ref['level'], ref['id']), (level, ident))
                self.assertIsNotNone(source)                         # every area is in the table, however few people it holds
                self.assertNotIn('withheld', ref)
                self.assertAlmostEqual(ref['pct'], source['top10_area_pct'], places=4)
                self.assertAlmostEqual(ref['top10'], source['top10_share_eb'], places=6)
                self.assertEqual((ref['n'], ref['households']), (int(source['n_rows']), int(source['n_households'])))

    def test_unknown_person_and_household_are_404(self):
        self.assertEqual(self.get('/api/v3/person?id=p_' + '0' * 64).status_code, 404)
        self.assertEqual(self.get('/api/v3/household?basis=registration&id=h_' + '0' * 64).status_code, 404)

    # ---- household and street ----------------------------------------------------------------------------------
    def test_household_popover_lists_the_members_without_an_address(self):
        for (h_id,) in self.scores.execute('SELECT h_id FROM street_household LIMIT 5'):
            body = self.get('/api/v3/household?basis=registration&id=' + h_id).json()
            s = self.scores.execute('SELECT hh, reg_block FROM street_household WHERE h_id=?', (h_id,)).fetchone()
            members = self.scores.execute('SELECT person_id FROM person WHERE hh=?', (s['hh'],)).fetchall()
            self.assertEqual({m['person_id'] for m in body['members']}, {m[0] for m in members})
            self.assertEqual(body['code'], s['hh'])
            self.assertEqual(body['block_label'], 'Block ' + s['reg_block'][-4:])
            self.assertEqual(set(body), {'id', 'code', 'n_people', 'mean_pct', 'block_label', 'members'})

    def street_view(self, extra=''):
        street = sqlite3.connect('file:%s?mode=ro' % (self.world.directory / 'street.sqlite3').as_posix(), uri=True)
        try:
            west, south, east, north = street.execute(
                "SELECT MIN(longitude), MIN(latitude), MAX(longitude), MAX(latitude) FROM households "
                "WHERE basis='registration'").fetchone()
            real = {h: (lo, la) for h, lo, la in street.execute(
                "SELECT id, longitude, latitude FROM households WHERE basis='registration'")}
        finally:
            street.close()
        bbox = '%.6f,%.6f,%.6f,%.6f' % (west - 0.0005, south - 0.0005, min(east + 0.0005, west + 0.055),
                                        min(north + 0.0005, south + 0.055))
        return self.get('/api/v3/street?bbox=%s&zoom=16&basis=registration%s' % (bbox, extra)), real

    def test_street_points_carry_a_bin_and_no_address(self):
        response, real = self.street_view()
        body = response.json()
        self.assertEqual(set(body), {'points', 'truncated', 'stats'})
        self.assertGreater(len(body['points']), 20)
        stored = {h: (b, n) for h, b, n in self.scores.execute('SELECT h_id, bin, n_people FROM street_household')}
        for h_id, lon, lat, people, bin_ in body['points']:
            self.assertEqual(len([h_id, lon, lat, people, bin_]), 5)
            self.assertEqual(bin_, stored[h_id][0] - 1)
            self.assertAlmostEqual(lon, real[h_id][0], places=5)       # real mode: the stored position
            self.assertAlmostEqual(lat, real[h_id][1], places=5)
        stats = body['stats']
        self.assertEqual(stats['households'], len(body['points']))
        self.assertEqual(sum(stats['hist']), len(body['points']))
        self.assertEqual(stats['people'], sum(p[3] for p in body['points']))
        self.assertFalse(body['truncated'])
        self.assertIsNone(re.search(r'SENTINEL', response.content.decode('utf-8')))

    @override_settings(DEMO_MODE=True)
    def test_demo_street_positions_are_deterministic_and_inside_the_block(self):
        first, real = self.street_view()
        second, _ = self.street_view()
        self.assertEqual(first.json(), second.json())
        blocks = {h: b for h, b in self.scores.execute('SELECT h_id, reg_block FROM street_household')}
        polygons = {i: shape(p['geometry']) for i, p in self.world.payload['geometry.json']['block'].items()}
        moved = 0
        for h_id, lon, lat, _, _ in first.json()['points']:
            self.assertTrue(polygons[blocks[h_id]].buffer(1e-7).contains(Point(lon, lat)), h_id)
            moved += abs(lon - real[h_id][0]) > 1e-5 or abs(lat - real[h_id][1]) > 1e-5
        self.assertGreater(moved, 0.9 * len(first.json()['points']))

    def test_point_in_polygon_helper(self):
        square = [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]], [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]]
        self.assertTrue(v3_demo_geo.inside_polygon(1, 1, square))
        self.assertFalse(v3_demo_geo.inside_polygon(5, 5, square))        # inside the hole
        self.assertFalse(v3_demo_geo.inside_polygon(11, 5, square))

    # ---- search ---------------------------------------------------------------------------------------------------
    def test_search_is_capped_and_cannot_inject_query_syntax(self):
        body = self.get('/api/v3/search?q=sentinel').json()
        self.assertEqual(len(body['people']), 8)
        for person in body['people']:
            self.assertEqual(set(person), {'person_id', 'name', 'block_label'})
        for q in ('%22%20OR%20%22', '*', 'name%3Aabc', 'NEAR(a%20b)', 'a%20AND%20NOT%20b', "o'brien"):
            self.assertIn(self.get('/api/v3/search?q=' + q).status_code, (200, 400), q)
        with override_settings(DEMO_MODE=True):
            demo = self.get('/api/v3/search?q=ab').json()
            self.assertTrue(len(demo['people']) <= 8)
