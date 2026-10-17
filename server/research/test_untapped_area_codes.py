"""Precinct startup cache equivalence, fallback and per-store version isolation."""
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
from django.test import SimpleTestCase

from . import untapped as U


class PrecinctColumnTests(SimpleTestCase):
    def setUp(self):
        U._area_codes.cache_clear()
        self.addCleanup(U._area_codes.cache_clear)
        self.path = Path('fixture-scores.sqlite3').resolve()
        self.stamp = ((str(self.path), 100, 200), None)
        self.values = ['C', None, 'A', 'C', '', 'B']
        self.codes = np.array([2, -1, 0, 2, -1, 1], dtype='<i4')
        self.info = SimpleNamespace(path=self.path, stamp=(100, 200), n=6,
                                    dicts={'a_reg_precinct': ['A', 'B', 'C']},
                                    columns={'a_reg_precinct': self.codes})

    def sql_store(self, path):
        conn = sqlite3.connect(':memory:')
        conn.execute('CREATE TABLE person (pos INTEGER PRIMARY KEY, reg_precinct TEXT)')
        conn.executemany('INSERT INTO person VALUES (?, ?)', enumerate(self.values, 1))
        return conn

    def read(self, *, info=None, stamp=None):
        with mock.patch.object(U, 'terms', return_value=SimpleNamespace(n=6)), \
                mock.patch.object(U.DB, 'store_path', return_value=self.path), \
                mock.patch.object(U.DB, 'info', return_value=info or self.info):
            return U._area_codes('precinct', stamp or self.stamp)

    def assert_expected(self, result):
        ids, codes = result
        self.assertEqual(ids, ['A', 'B', 'C'])
        np.testing.assert_array_equal(codes, self.codes)
        self.assertEqual(codes.dtype, np.dtype('int32'))

    def test_existing_validated_codes_preserve_missing_areas_without_sql_scan(self):
        with mock.patch.object(U.DB, '_open', side_effect=AssertionError('must not rescan person')):
            self.assert_expected(self.read())

    def test_older_store_without_list_column_falls_back_to_rows(self):
        self.info.columns = {}
        with mock.patch.object(U.DB, '_open', side_effect=self.sql_store) as opened:
            self.assert_expected(self.read())
            opened.assert_called_once()

    def test_minimal_store_without_full_metadata_uses_existing_sql_path(self):
        with mock.patch.object(U, 'terms', return_value=SimpleNamespace(n=6)), \
                mock.patch.object(U.DB, 'store_path', return_value=self.path), \
                mock.patch.object(U.DB, 'info', side_effect=U.DB.StoreError('Older store')), \
                mock.patch.object(U.DB, '_open', side_effect=self.sql_store):
            self.assert_expected(U._area_codes('precinct', self.stamp))

    def test_invalid_derived_columns_use_authoritative_rows(self):
        cases = [(['B', 'A', 'C'], self.codes), (['A', 'B', 'C', 'unused'], self.codes),
                 (['A', 'B', 'C'], np.array([2, -2, 0, 2, -1, 1])),
                 (['A', 'B', 'C'], np.array([2, -1, 0, 9, -1, 1])),
                 (['A', 'B', 'C'], self.codes.astype(float)), (['A', 'B', 'C'], self.codes[:3])]
        for ids, codes in cases:
            with self.subTest(ids=ids, dtype=codes.dtype):
                U._area_codes.cache_clear()
                self.info.dicts['a_reg_precinct'], self.info.columns['a_reg_precinct'] = ids, codes
                with mock.patch.object(U.DB, '_open', side_effect=self.sql_store):
                    self.assert_expected(self.read())

    def test_different_store_version_cannot_supply_old_terms_area_codes(self):
        self.info.stamp = (101, 200)
        with mock.patch.object(U.DB, '_open', side_effect=self.sql_store) as opened:
            self.assert_expected(self.read())
            opened.assert_called_once()

    def test_changed_store_stamp_builds_new_precinct_codes(self):
        with mock.patch.object(U.DB, '_open', side_effect=AssertionError('must not rescan person')):
            self.assert_expected(self.read())
            changed = SimpleNamespace(path=self.path, stamp=(101, 220), n=6,
                                      dicts={'a_reg_precinct': ['D']},
                                      columns={'a_reg_precinct': np.array([0, -1, 0, 0, -1, 0], dtype='<i4')})
            ids, codes = self.read(info=changed, stamp=((str(self.path), 101, 220), None))
            self.assertEqual(ids, ['D'])
            np.testing.assert_array_equal(codes, changed.columns['a_reg_precinct'])

    def test_all_people_without_precincts_return_no_area_ids(self):
        self.info.dicts['a_reg_precinct'] = []
        self.info.columns['a_reg_precinct'] = np.full(6, -1, dtype='<i4')
        with mock.patch.object(U.DB, '_open', side_effect=AssertionError('must not rescan person')):
            ids, codes = self.read()
        self.assertEqual(ids, [])
        np.testing.assert_array_equal(codes, np.full(6, -1))
