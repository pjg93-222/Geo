"""Synthetic checks for tools/co_baseline.py: the canonical store hash, normalisation and the difference report.

    <venv python> -m unittest tools.test_co_baseline          (from the V3 folder; no real data, no server, no port)
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import co_baseline as B


def make_store(path, rows, extra_column=False):
    connection = sqlite3.connect(path)
    connection.execute('CREATE TABLE person (pos INTEGER PRIMARY KEY, name TEXT, score REAL%s)'
                       % (', note TEXT' if extra_column else ''))
    connection.execute('CREATE TABLE loose (a TEXT, b INTEGER)')
    connection.execute('CREATE VIRTUAL TABLE box USING rtree(id, west, east)')
    for pos, name, score in rows:
        connection.execute('INSERT INTO person (pos, name, score) VALUES (?, ?, ?)', (pos, name, score))
    for a, b in (('x', 2), ('y', 1), ('x', 1)):
        connection.execute('INSERT INTO loose VALUES (?, ?)', (a, b))
    connection.execute('INSERT INTO box VALUES (1, 0.0, 1.0)')
    connection.commit()
    connection.close()


ROWS = [(1, 'a', 0.5), (2, 'b', 1.0), (3, 'c', None)]


class StoreDigestTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.dir = Path(self.folder.name)

    def digest(self, name, rows=ROWS, **kwargs):
        make_store(self.dir / name, rows, **kwargs)
        return B.store_digest(self.dir / name)

    def test_insertion_order_does_not_matter(self):
        first = self.digest('a.sqlite3')
        second = self.digest('b.sqlite3', rows=list(reversed(ROWS)))
        self.assertEqual(first, second)

    def test_a_changed_value_is_seen_in_its_table_only(self):
        first = self.digest('a.sqlite3')
        second = self.digest('b.sqlite3', rows=[(1, 'a', 0.5), (2, 'b', 1.0), (3, 'c', 2.0)])
        self.assertNotEqual(first['tables']['person']['sha256'], second['tables']['person']['sha256'])
        self.assertEqual(first['tables']['loose'], second['tables']['loose'])
        self.assertEqual(first['schema_sha256'], second['schema_sha256'])

    def test_int_and_float_stay_apart(self):
        digests = []
        for name, value in (('a.sqlite3', 1), ('b.sqlite3', 1.0)):
            connection = sqlite3.connect(self.dir / name)
            connection.execute('CREATE TABLE t (id INTEGER PRIMARY KEY, v)')       # no affinity: 1 and 1.0 are kept as given
            connection.execute('INSERT INTO t VALUES (1, ?)', (value,))
            connection.commit()
            connection.close()
            digests.append(B.store_digest(self.dir / name)['tables']['t']['sha256'])
        self.assertNotEqual(*digests)

    def test_a_schema_change_is_seen(self):
        first = self.digest('a.sqlite3')
        second = self.digest('b.sqlite3', extra_column=True)
        self.assertNotEqual(first['schema_sha256'], second['schema_sha256'])

    def test_counts_orders_and_virtual_flag(self):
        digest = self.digest('a.sqlite3')
        self.assertEqual(digest['tables']['person']['rows'], 3)
        self.assertEqual(digest['tables']['person']['order'], 'primary key')
        self.assertEqual(digest['tables']['loose']['order'], 'all columns')
        self.assertTrue(digest['tables']['box']['virtual'])
        self.assertEqual(digest['tables']['box']['order'], 'rowid')
        self.assertNotIn('virtual', digest['tables']['person'])

    def test_the_store_is_opened_read_only(self):
        make_store(self.dir / 'a.sqlite3', ROWS)
        connection = B.open_readonly(self.dir / 'a.sqlite3')
        with self.assertRaises(sqlite3.OperationalError):
            connection.execute('INSERT INTO loose VALUES (?, ?)', ('z', 9))
        connection.close()


class NormaliseTests(unittest.TestCase):
    def test_only_the_named_field_is_dropped(self):
        one = json.dumps({'generated_on': '2026-10-06', 'totals': {'doors': 3}}).encode()
        two = json.dumps({'totals': {'doors': 3}, 'generated_on': '2026-10-07'}).encode()
        first, second = (B.body_digest('walk_list_a', body, 'application/json') for body in (one, two))
        self.assertEqual(first, second)
        self.assertEqual(first[1], ['generated_on'])
        changed = json.dumps({'generated_on': '2026-10-07', 'totals': {'doors': 4}}).encode()
        self.assertNotEqual(first[0], B.body_digest('walk_list_a', changed, 'application/json')[0])

    def test_other_endpoints_are_hashed_as_they_are(self):
        one = json.dumps({'generated_on': 'x'}).encode()
        two = json.dumps({'generated_on': 'y'}).encode()
        self.assertNotEqual(B.body_digest('people_first_page', one, 'application/json')[0],
                            B.body_digest('people_first_page', two, 'application/json')[0])

    def test_differing_paths_name_no_values(self):
        left = json.dumps({'rows': [{'name': 'SECRET-A', 'rank': '1'}], 'total': 5}).encode()
        right = json.dumps({'rows': [{'name': 'SECRET-B', 'rank': '1'}], 'total': 5}).encode()
        found = B.json_paths_differing(left, right)
        self.assertEqual(found, ['.rows[0].name'])
        self.assertFalse(any('SECRET' in item for item in found))

    def test_first_string_finds_an_id(self):
        ident = 'p_' + 'a' * 64
        self.assertEqual(B.first_string({'rows': [{'x': 1}, {'person_id': ident}]}, r'p_[0-9a-f]{64}'), ident)
        self.assertIsNone(B.first_string({'rows': []}, r'p_[0-9a-f]{64}'))


class CompareTests(unittest.TestCase):
    def test_equal_sections_report_nothing(self):
        out = []
        B.compare({'a': {'b': 1}}, {'a': {'b': 1}}, 'x', out)
        self.assertEqual(out, [])

    def test_missing_new_and_different_are_named(self):
        out = []
        B.compare({'a': 1, 'b': 2, 'c': 3}, {'a': 1, 'b': 9, 'd': 4}, 'x', out)
        self.assertEqual(sorted(line.split()[0] for line in out), ['DIFFERENT', 'MISSING', 'NEW'])
        self.assertTrue(any('x.b' in line for line in out))

    def test_endpoint_difference_is_listed_with_its_fields(self):
        before = {'meta': {'status': 200, 'content_type': 'application/json', 'bytes': 10, 'sha256': 'a' * 64}}
        now = {'meta': {'status': 200, 'content_type': 'application/json', 'bytes': 10, 'sha256': 'b' * 64}}
        out = []
        B.compare_endpoints(before, now, {}, Path('does-not-exist'), out)
        self.assertEqual(len(out), 1)
        self.assertIn('endpoints.meta', out[0])
        self.assertIn('sha256', out[0])


class SafetyTests(unittest.TestCase):
    def test_the_live_port_is_refused(self):
        with self.assertRaises(SystemExit):
            B.choose_port(B.LIVE_PORT)

    def test_a_busy_spare_port_is_skipped(self):
        with patch.object(B, 'listener_pid', side_effect=lambda port: 4242 if port == 8769 else None):
            self.assertEqual(B.choose_port(0), 8771)

    def test_no_endpoint_touches_the_live_port_or_writes(self):
        self.assertTrue(all(template.startswith('/') for _, template in B.ENDPOINTS))
        self.assertEqual({entry[0] for entry in B.ENDPOINTS}.__len__(), len(B.ENDPOINTS))
        self.assertNotIn(str(B.LIVE_PORT), ''.join(template for _, template in B.ENDPOINTS))

    def test_a_start_that_dies_is_retried_three_times_and_noted(self):
        class Flaky:
            starts = 0

            def __init__(self, fail):
                self.fail = fail

            def start(self):
                Flaky.starts += 1
                if self.fail:
                    raise B.StartFailed('exited early with code -1073740022')

            def stop(self):
                pass

        B.STARTUP_RETRIES.clear()
        with patch.object(B.time, 'sleep'):
            server = B.start_with_retry(lambda attempt: Flaky(attempt < 3))
        self.assertEqual((Flaky.starts, len(B.STARTUP_RETRIES)), (3, 2))
        with patch.object(B.time, 'sleep'), self.assertRaises(SystemExit):
            B.start_with_retry(lambda attempt: Flaky(True))
        B.STARTUP_RETRIES.clear()

    def test_a_5xx_is_a_server_fault_not_a_recorded_answer(self):
        with patch.object(B, 'fetch', return_value=(500, 'application/json', b'{"error":"x"}')),                 patch.object(B, 'log'):
            with self.assertRaises(B.ServerFault):
                B.endpoint_pass(1)

    def test_the_server_refuses_the_live_port(self):
        with self.assertRaises(SystemExit):
            B.Server('python', B.LIVE_PORT, Path('.'), 't').start()


if __name__ == '__main__':
    unittest.main()
