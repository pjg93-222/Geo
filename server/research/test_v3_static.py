"""S1: static files (immutable assets, ETag/304, br/gzip siblings) and the precomputed aggregate payloads
(areas, geometry, meta). Synthetic data only."""
import gzip
import json
import os
import tempfile
import time
from pathlib import Path

from django.test import Client, SimpleTestCase, TestCase, override_settings

from tools import area_layers as AL

from . import bundles, scoresdb, v3_cache
from .v3_testkit import build_world

LOCAL = dict(HTTP_HOST='localhost')


class StaticFileTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(cls.temporary.name)
        dist = base / 'frontend' / 'dist'
        (dist / 'assets').mkdir(parents=True)
        cls.index = b'<!doctype html><title>x</title>' + b'<p>shell</p>' * 200
        cls.script = b'console.log("app");' * 400
        (dist / 'index.html').write_bytes(cls.index)
        (dist / 'assets' / 'app-abc123.js').write_bytes(cls.script)
        (dist / 'assets' / 'app-abc123.js.br').write_bytes(b'BROTLI-BYTES')
        (dist / 'assets' / 'app-abc123.js.gz').write_bytes(gzip.compress(cls.script))
        (dist / 'assets' / 'font-1.woff2').write_bytes(b'wOF2' + os.urandom(4000))
        (dist / 'assets' / 'plain-9.css').write_bytes(b'body{margin:0}' * 300)
        (dist / 'brand').mkdir()
        (dist / 'brand' / 'loading.mp4').write_bytes(bytes(range(256)))
        # a sibling older than its source must be ignored
        (dist / 'assets' / 'stale-1.js').write_bytes(b'var a=1;' * 300)
        old = dist / 'assets' / 'stale-1.js.gz'
        old.write_bytes(b'NOT-GZIP-OLD')
        past = time.time() - 3600
        os.utime(old, (past, past))
        cls.override = override_settings(BASE_DIR=base)
        cls.override.enable()

    @classmethod
    def tearDownClass(cls):
        cls.override.disable()
        cls.temporary.cleanup()
        super().tearDownClass()

    def fetch(self, path, **extra):
        response = self.client.get(path, **extra)
        self.addCleanup(response.close)          # release the file handle of streamed files (Windows cannot delete it)
        return response

    def test_hashed_assets_are_immutable_and_the_shell_revalidates(self):
        asset = self.fetch('/assets/app-abc123.js')
        self.assertEqual(asset['Cache-Control'], 'public, max-age=31536000, immutable')
        self.assertTrue(asset['ETag'] and asset['Last-Modified'])
        shell = self.fetch('/')
        self.assertEqual(shell['Cache-Control'], 'no-cache')
        self.assertTrue(shell['ETag'])
        self.assertEqual(self.fetch('/some/spa/route')['Cache-Control'], 'no-cache')    # falls back to the shell
        self.assertIn("default-src 'self'", shell['Content-Security-Policy'])
        self.assertIn("script-src 'self' 'wasm-unsafe-eval'", shell['Content-Security-Policy'])
        self.assertNotIn("'unsafe-eval'", shell['Content-Security-Policy'])
        self.assertIn("connect-src 'self'", shell['Content-Security-Policy'])
        self.assertEqual(shell['X-Content-Type-Options'], 'nosniff')

    def test_loading_video_byte_ranges(self):
        clip = self.fetch('/brand/loading.mp4', HTTP_RANGE='bytes=10-31')
        self.assertEqual(clip.status_code, 206)
        self.assertEqual(clip.content, bytes(range(10, 32)))
        self.assertEqual(clip['Content-Range'], 'bytes 10-31/256')
        self.assertEqual(clip['Content-Encoding'], 'identity')
        suffix = self.fetch('/brand/loading.mp4', HTTP_RANGE='bytes=-8')
        self.assertEqual(suffix.content, bytes(range(248, 256)))
        self.assertEqual(self.fetch('/brand/loading.mp4', HTTP_RANGE='bytes=999-').status_code, 416)
        self.assertEqual(self.fetch('/brand/loading.mp4', HTTP_RANGE='bytes=-0').status_code, 416)

    def test_brotli_then_gzip_siblings_are_chosen_by_accept_encoding(self):
        br = self.fetch('/assets/app-abc123.js', HTTP_ACCEPT_ENCODING='br, gzip')
        self.assertEqual(br['Content-Encoding'], 'br')
        self.assertEqual(b''.join(br.streaming_content), b'BROTLI-BYTES')
        self.assertEqual(br['Vary'], 'Accept-Encoding')
        gz = self.fetch('/assets/app-abc123.js', HTTP_ACCEPT_ENCODING='gzip')
        self.assertEqual(gz['Content-Encoding'], 'gzip')
        self.assertEqual(gzip.decompress(b''.join(gz.streaming_content)), self.script)
        plain = self.fetch('/assets/app-abc123.js')
        self.assertFalse(plain.has_header('Content-Encoding'))
        self.assertEqual(b''.join(plain.streaming_content), self.script)
        self.assertNotEqual(br['ETag'], gz['ETag'])
        self.assertNotEqual(gz['ETag'], plain['ETag'])
        refused = self.fetch('/assets/app-abc123.js', HTTP_ACCEPT_ENCODING='br;q=0')
        self.assertFalse(refused.has_header('Content-Encoding'))

    def test_stale_sibling_is_ignored_and_plain_files_are_gzipped_in_memory(self):
        stale = self.fetch('/assets/stale-1.js', HTTP_ACCEPT_ENCODING='gzip')
        self.assertEqual(stale['Content-Encoding'], 'gzip')
        self.assertEqual(gzip.decompress(stale.content), b'var a=1;' * 300)       # built from the source, not the stale .gz
        css = self.fetch('/assets/plain-9.css', HTTP_ACCEPT_ENCODING='gzip')
        self.assertEqual(gzip.decompress(css.content), b'body{margin:0}' * 300)
        shell = self.fetch('/', HTTP_ACCEPT_ENCODING='gzip')
        self.assertEqual(gzip.decompress(shell.content), self.index)

    def test_already_compact_files_are_not_compressed_again(self):
        font = self.fetch('/assets/font-1.woff2', HTTP_ACCEPT_ENCODING='gzip, br')
        self.assertEqual(font['Content-Type'], 'font/woff2')
        self.assertEqual(font['Content-Encoding'], 'identity')

    def test_conditional_requests_answer_304_without_a_body(self):
        for path, encoding in (('/assets/app-abc123.js', 'br'), ('/', 'gzip'), ('/assets/font-1.woff2', '')):
            first = self.fetch(path, HTTP_ACCEPT_ENCODING=encoding)
            again = self.fetch(path, HTTP_ACCEPT_ENCODING=encoding, HTTP_IF_NONE_MATCH=first['ETag'])
            self.assertEqual(again.status_code, 304, path)
            self.assertEqual(again.content, b'')
            self.assertEqual(again['ETag'], first['ETag'])
            other = self.fetch(path, HTTP_ACCEPT_ENCODING=encoding, HTTP_IF_NONE_MATCH='"nope"')
            self.assertEqual(other.status_code, 200)
        modified = self.fetch('/', HTTP_IF_MODIFIED_SINCE='Fri, 01 Jan 2100 00:00:00 GMT')
        self.assertEqual(modified.status_code, 304)
        # a validator weakened on the way out (GZipMiddleware) still matches on the way back
        tag = self.fetch('/assets/app-abc123.js')['ETag']
        weak = self.fetch('/assets/app-abc123.js', HTTP_IF_NONE_MATCH='W/' + tag)
        self.assertEqual(weak.status_code, 304)

    def test_path_traversal_never_leaves_the_dist_folder(self):
        for path in ('/../../settings.py', '/assets/../../../manage.py', '/%2e%2e/%2e%2e/manage.py'):
            response = self.fetch(path)
            self.assertIn(response.status_code, (200, 404))
            if response.status_code == 200:
                self.assertIn(b'shell', b''.join(getattr(response, 'streaming_content', [response.content])))


@override_settings(LOCAL_ADMIN_ACCESS=True, HOSTED=False, DEMO_MODE=False)
class AggregatePayloadTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.world = build_world(Path(cls.temporary.name))
        cls.override = override_settings(DATA_DIR=cls.world.directory)
        cls.override.enable()
        scoresdb.reset()
        v3_cache.forget()
        bundles.load.cache_clear()
        bundles.import_bundle(cls.world.zip_path, expected_sha256=cls.world.zip_sha256)
        cls.key = cls.world.snapshot

    @classmethod
    def tearDownClass(cls):
        scoresdb.reset()
        v3_cache.forget()
        cls.override.disable()
        cls.temporary.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.client = Client(**LOCAL)

    def url(self, kind, *parts):
        return '/api/v3/%s/%s/%s' % (kind, self.key, '/'.join(parts))

    def test_meta_uses_store_totals_and_registration_counts(self):
        response = self.client.get('/api/v3/meta')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        info = scoresdb.info()
        self.assertEqual(body['source'], 'live')
        self.assertEqual(body['data_origin'], 'local-source-and-model-output')
        self.assertFalse(body['demo'])
        self.assertEqual(body['people'], info.n)
        self.assertEqual(body['households'], info.meta['n_households'])
        # O-10: every party code is its own count; D, R and U are the old group counts and the rest add up to the old Other
        parties = body['district_parties']
        self.assertNotIn('district_registration', body)
        self.assertEqual(sum(parties.values()), info.n)
        for code in ('D', 'R', 'U'):
            self.assertEqual(parties[code], info.meta['reg_group_counts'][code])
        self.assertEqual(sum(n for code, n in parties.items() if code not in ('D', 'R', 'U')), info.meta['reg_group_counts']['O'])
        self.assertTrue({'G', 'L', 'O', 'T'} <= set(parties))

    def test_levels_are_immutable_gzipped_and_etagged(self):
        for basis, levels in AL.BASIS_LEVELS.items():
            for level in levels:
                response = self.client.get(self.url('areas', basis, level + '.json'))
                self.assertEqual(response.status_code, 200, (basis, level))
                self.assertEqual(response['Cache-Control'], 'private, max-age=31536000, immutable')
                self.assertTrue(response['ETag'].startswith('"'))
                body = response.json()
                self.assertEqual((body['schema'], body['snapshot'], body['basis'], body['level']),
                                 ('v3-areas-1', self.key, basis, level))
                n = len(body['ids'])
                for column in ('labels', 'status', 'n', 'households', 'parent', 'precinct', 'tract', 'blocks'):
                    self.assertEqual(len(body[column]), n, column)
                for group in ('ovr', 'cua', 'part', 'soi'):
                    for column, values in body['groups'][group].items():
                        self.assertEqual(len(values), n, (group, column))
                for column, values in body['groups']['lean'].items():
                    self.assertEqual(len(values), n, column)
                for i, status in enumerate(body['status']):
                    for group in ('ovr', 'cua', 'part', 'soi', 'lean'):
                        cell = body['groups'][group]['mean'][i]
                        self.assertEqual(cell is None, status != 'ok', (level, body['ids'][i], group))
                    self.assertEqual(body['n'][i] is None, status != 'ok')
        gz = self.client.get(self.url('areas', 'registration', 'block.json'), HTTP_ACCEPT_ENCODING='gzip')
        plain = self.client.get(self.url('areas', 'registration', 'block.json'))
        self.assertEqual(gz['Content-Encoding'], 'gzip')
        self.assertEqual(json.loads(gzip.decompress(gz.content)), plain.json())
        self.assertEqual(gz['Vary'], 'Accept-Encoding')

    def test_values_equal_the_area_table(self):
        body = self.client.get(self.url('areas', 'registration', 'tract.json')).json()
        table = {(r['area_id'], r['group']): r for r in self.world.fixture.area_rows
                 if (r['basis'], r['level']) == ('registration', 'tract')}
        for i, ident in enumerate(body['ids']):
            if body['status'][i] != 'ok':
                self.assertNotIn((ident, 'ovr'), table)
                continue
            for group in ('ovr', 'cua', 'part', 'soi'):
                row = table[(ident, group)]
                cells = body['groups'][group]
                self.assertAlmostEqual(cells['mean'][i], row['eb_mean'], places=5)
                self.assertAlmostEqual(cells['se'][i], row['eb_se'], places=5)
                self.assertAlmostEqual(cells['pct'][i], row['area_pct'], places=5)
                self.assertAlmostEqual(cells['top10'][i], row['top10_share_eb'], places=7)
                self.assertAlmostEqual(cells['top10_pct'][i], row['top10_area_pct'], places=5)
            lean = table[(ident, 'lean')]
            self.assertAlmostEqual(body['groups']['lean']['mean'][i], lean['eb_mean'], places=5)
            self.assertAlmostEqual(body['groups']['lean']['share_d'][i], lean['share_reg_D'], places=7)
            self.assertEqual(body['n'][i], int(table[(ident, 'ovr')]['n_rows']))

    def test_parties_counts_every_party_code_in_every_area(self):
        for basis, levels in AL.BASIS_LEVELS.items():
            for level in levels:
                response = self.client.get(self.url('parties', basis, level + '.json'))
                self.assertEqual(response.status_code, 200, (basis, level))
                self.assertEqual(response['Cache-Control'], 'private, no-cache')
                body = response.json()
                self.assertEqual((body['schema'], body['snapshot'], body['basis'], body['level']),
                                 ('v3-parties-1', self.key, basis, level))
                self.assertEqual(len(body['ids']), len(body['parties']))
                self.assertEqual(body['ids'], sorted(body['ids']))
                counts = dict(zip(body['ids'], body['parties']))
                areas = self.client.get(self.url('areas', basis, level + '.json')).json()
                lean = areas['groups']['lean']
                for i, ident in enumerate(areas['ids']):
                    if areas['status'][i] != 'ok':
                        self.assertNotIn(ident, counts)              # no registered people: no counts
                        continue
                    here = counts[ident]
                    self.assertEqual(sum(here.values()), areas['n'][i], (level, ident))
                    # D, R and U are the old shares of the area; the other codes make up the old Other
                    for code, cell in (('D', 'share_d'), ('R', 'share_r'), ('U', 'share_u')):
                        self.assertAlmostEqual(here.get(code, 0), areas['n'][i] * lean[cell][i], places=3)
                    other = sum(n for code, n in here.items() if code not in ('D', 'R', 'U'))
                    self.assertAlmostEqual(other, areas['n'][i] * (1 - lean['share_d'][i] - lean['share_r'][i] - lean['share_u'][i]), places=3)
                self.assertEqual(sum(sum(here.values()) for here in counts.values()), sum(n or 0 for n in areas['n']))
        # the whole district is the sum of the blocks
        blocks = self.client.get(self.url('parties', 'registration', 'block.json')).json()['parties']
        total = {}
        for here in blocks:
            for code, n in here.items():
                total[code] = total.get(code, 0) + n
        self.assertEqual(total, self.client.get('/api/v3/meta').json()['district_parties'])

    def test_parties_are_revalidated_and_refuse_what_it_cannot_count(self):
        url = self.url('parties', 'registration', 'precinct.json')
        first = self.client.get(url)
        self.assertEqual(self.client.get(url, HTTP_IF_NONE_MATCH=first['ETag']).status_code, 304)
        gz = self.client.get(url, HTTP_ACCEPT_ENCODING='gzip')
        self.assertEqual(json.loads(gzip.decompress(gz.content)), first.json())
        self.assertEqual(self.client.get('/api/v3/parties/co-cd005-000000000000/registration/block.json').status_code, 404)
        self.assertEqual(self.client.get(self.url('parties', 'residence', 'block.json')).status_code, 404)
        self.assertEqual(self.client.get(url + '?x=1').status_code, 400)
        self.assertEqual(self.client.post(url).status_code, 405)

    def test_blank_and_missing_codes_are_counted_as_no_code(self):
        import sqlite3
        connection = sqlite3.connect(self.world.directory / 'scores.sqlite3')
        saved = connection.execute("SELECT pos, party FROM person WHERE party IN ('T', 'G') ORDER BY pos").fetchall()
        self.assertGreaterEqual(len(saved), 4)
        try:
            # both stay in the old Other group, so nothing else about the store changes
            connection.executemany('UPDATE person SET party=? WHERE pos=?', [('' if i % 2 else None, pos) for i, (pos, _) in enumerate(saved)])
            connection.commit()
            scoresdb.reset()
            meta = self.client.get('/api/v3/meta').json()
            parties = meta['district_parties']
            self.assertNotIn('T', parties)
            self.assertNotIn('G', parties)
            self.assertEqual(parties[''], len(saved))
            self.assertEqual(sum(parties.values()), meta['people'])
            blocks = self.client.get(self.url('parties', 'registration', 'block.json')).json()['parties']
            self.assertEqual(sum(here.get('', 0) for here in blocks), len(saved))
            self.assertTrue(all(code == '' or len(code) == 1 for here in blocks for code in here))
        finally:
            connection.executemany('UPDATE person SET party=? WHERE pos=?', [(party, pos) for pos, party in saved])
            connection.commit()
            connection.close()
            scoresdb.reset()

    def test_conditional_get_and_etags_survive_a_restart(self):
        url = self.url('areas', 'registration', 'precinct.json')
        first = self.client.get(url)
        again = self.client.get(url, HTTP_IF_NONE_MATCH=first['ETag'])
        self.assertEqual(again.status_code, 304)
        self.assertEqual(again.content, b'')
        v3_cache.forget()                       # a new process has an empty memory cache: payloads come from disk
        after = self.client.get(url)
        self.assertEqual(after['ETag'], first['ETag'])
        self.assertEqual(after.content, first.content)
        gz = self.client.get(url, HTTP_ACCEPT_ENCODING='gzip')
        again = self.client.get(url, HTTP_ACCEPT_ENCODING='gzip', HTTP_IF_NONE_MATCH=gz['ETag'])
        self.assertEqual(again.status_code, 304)

    def test_geometry_is_a_feature_collection_with_label_points(self):
        for level in AL.LEVEL_ORDER:
            for variant in ('coarse', 'fine'):
                body = self.client.get(self.url('geometry', level, variant + '.json')).json()
                self.assertEqual(body['type'], 'FeatureCollection')
                self.assertGreater(len(body['features']), 0)
                for feature in body['features']:
                    self.assertEqual(set(feature['properties']), {'id', 'lx', 'ly'})
                    self.assertIn(feature['geometry']['type'], ('Polygon', 'MultiPolygon'))
        coarse = self.client.get(self.url('geometry', 'block', 'coarse.json'))
        self.assertEqual(coarse['Cache-Control'], 'private, max-age=31536000, immutable')

    def test_meta_reports_the_store_and_the_demo_flag(self):
        body = self.client.get('/api/v3/meta').json()
        self.assertEqual((body['schema'], body['snapshot'], body['store']['status']), ('v3-meta-1', self.key, 'ok'))
        self.assertFalse(body['demo'])
        self.assertEqual(body['people'], self.world.fixture.n)
        self.assertEqual(body['levels'], list(AL.LEVEL_ORDER))
        self.assertEqual(body['weights'], {'cua': 40, 'part': 35, 'soi': 25})
        self.assertEqual(body['small_area'], {'min_n': 20, 'min_households': 10})       # a note only: every area is shown
        self.assertNotIn('floors', body)
        self.assertNotIn('suppressed_ids_sha256', body['row_framework'])
        self.assertEqual(body['share_breaks'], [0.05, 0.08, 0.12, 0.18])
        self.assertTrue(self.client.get('/api/v3/meta?demo=1').json()['demo'])
        with override_settings(DEMO_MODE=True):
            response = self.client.get('/api/v3/meta')
            self.assertTrue(response.json()['demo'])
            self.assertEqual(response['X-CU-Demo'], '1')
        response = self.client.get('/api/v3/meta')
        self.assertEqual(response['Cache-Control'], 'private, no-cache')
        self.assertEqual(self.client.get('/api/v3/meta', HTTP_IF_NONE_MATCH=response['ETag']).status_code, 304)

    def test_unknown_snapshot_level_and_residence_blocks_are_404(self):
        self.assertEqual(self.client.get('/api/v3/areas/co-cd005-000000000000/registration/block.json').status_code, 404)
        self.assertEqual(self.client.get(self.url('areas', 'residence', 'block.json')).status_code, 404)
        self.assertEqual(self.client.get(self.url('geometry', 'county', 'fine.json')).status_code, 404)
        self.assertEqual(self.client.get('/api/v3/areas/%s/registration/block.json?x=1' % self.key).status_code, 400)

    def test_a_score_store_from_another_area_table_is_reported_and_refused(self):
        path = self.world.directory / 'scores.sqlite3'
        import sqlite3
        connection = sqlite3.connect(path)
        original = connection.execute("SELECT value FROM metadata WHERE key='area_table_sha256'").fetchone()[0]
        try:
            connection.execute("UPDATE metadata SET value=? WHERE key='area_table_sha256'", (json.dumps('0' * 64),))
            connection.commit()
            scoresdb.reset()
            self.assertEqual(self.client.get('/api/v3/meta').json()['store']['status'], 'mismatch')
            self.assertEqual(self.client.get(self.url('parties', 'registration', 'block.json')).status_code, 404)
            pid = connection.execute('SELECT person_id FROM person LIMIT 1').fetchone()[0]
            self.assertEqual(self.client.get('/api/v3/person?id=' + pid).status_code, 409)
        finally:
            connection.execute("UPDATE metadata SET value=? WHERE key='area_table_sha256'", (original,))
            connection.commit()
            connection.close()
            scoresdb.reset()
