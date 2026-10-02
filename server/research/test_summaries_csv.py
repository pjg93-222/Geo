"""Summary CSV text cells must not become spreadsheet formulas; numbers keep their exact values."""
import csv
import io
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from .models import Snapshot
from .test_measures import MEAN, measure_bundle

FORMULAS = ('=HYPERLINK("https://example.invalid","x")', '@SUM(1+1)', '+1+1', '-1+1', ' =1+1',
            '\u00a0=1+1', '\u200b=1+1', '\ufeff=1+1', '\t=1+1', '\n=1+1')
NUMBERS = ('value', 'index', 'valid_count', 'field_coverage', 'count', 'share', 'coverage', 'source_count',
           'component_coverage', 'components_used', 'components_total')


class SummaryCsvTests(TestCase):
    def setUp(self):
        self.payload = measure_bundle()
        self.client.force_login(User.objects.create_user('summary-csv-analyst'))
        self.loader = patch('server.research.views.load', side_effect=lambda key, filename: self.payload[filename])
        self.loader.start()
        self.addCleanup(self.loader.stop)

    def export(self, source_date, **query):
        # Each source date gets its own snapshot, as a separate import would, so no cached summary is reused.
        key = 'co-cd005-%012d' % Snapshot.objects.count()
        Snapshot.objects.create(key=key, manifest={**self.payload['manifest.json'], 'source_date': source_date})
        query = {'snapshot': key, 'level': 'postal_zip', 'metric': MEAN['id'], **query}
        data = self.client.get('/api/summaries', query)
        exported = self.client.get('/api/summaries', {**query, 'format': 'csv'})
        self.assertEqual((data.status_code, exported.status_code), (200, 200))
        return data.json(), list(csv.DictReader(io.StringIO(exported.content.decode(), newline='')))

    def test_formula_source_dates_are_quoted_in_every_csv_row_and_raw_in_json(self):
        for value in FORMULAS:
            with self.subTest(value=value):
                data, exported = self.export(value)
                self.assertEqual(data['meta']['source_date'], value)
                self.assertEqual(len(exported), len(data['rows']))
                self.assertEqual([row['source_date'] for row in exported], ["'" + value] * len(data['rows']))

    def test_ordinary_source_dates_are_unchanged(self):
        for value in (self.payload['manifest.json']['source_date'], 'Collected 2026-09-01 - updated', ' 2026-09-04'):
            with self.subTest(value=value):
                data, exported = self.export(value)
                self.assertEqual(data['meta']['source_date'], value)
                self.assertEqual([row['source_date'] for row in exported], [value] * len(data['rows']))

    def test_numbers_including_negatives_are_exported_unquoted(self):
        self.payload['rows.json'][0]['measures'][MEAN['id']][0] = -12.5
        data, exported = self.export(self.payload['manifest.json']['source_date'], scale='raw')
        self.assertEqual(data['rows'][0]['value'], -12.5)
        self.assertEqual(exported[0]['value'], '-12.5')
        self.assertEqual(len(exported), len(data['rows']))
        for actual, expected in zip(exported, data['rows']):
            self.assertEqual(actual['geographic_id'], expected['id'])
            for key in NUMBERS:
                with self.subTest(id=expected['id'], key=key):
                    self.assertEqual(actual[key], '' if expected.get(key) is None else str(expected[key]))
            self.assertEqual(actual['denominator'], str(data['meta']['denominator']))
            self.assertEqual(actual['partial'], 'False')
