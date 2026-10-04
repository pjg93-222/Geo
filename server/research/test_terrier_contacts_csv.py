"""Contact CSV cells must not become spreadsheet formulas, including formulas behind leading characters."""
import csv
from contextlib import closing
import io
import sqlite3

from django.test import SimpleTestCase, override_settings

from . import test_terrier_contacts as base
from .terrier_contacts import csv_value, export_contacts


class ContactCsvValueTests(SimpleTestCase):
    def test_formulas_behind_leading_spaces_invisible_or_control_characters_are_guarded(self):
        prefixes = (' ', '   ', '\u00a0', '\u200b', '\u200c', '\u200d', '\u200e', '\u200f', '\u2060', '\ufeff',
                    '\x0b', '\x0c', '\x00', '\x1f', '\x7f', '\x85', '\u1680', '\u2003', '\u2028', '\u202f', '\u3000',
                    ' \t', '\u00a0\r\n', '\ufeff\u200b \u00a0')
        values = [prefix + trigger + '1+1' for prefix in prefixes for trigger in '=+-@']
        values.append('\u00a0=HYPERLINK("https://example.invalid","x")')
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(csv_value(value), "'" + value)

    def test_first_character_guards_are_unchanged(self):
        for value in ('=1+1', '+1', '-1', '@SUM(A1)', '\t=1', '\tplain', '\rplain', '\nplain', -5):
            with self.subTest(value=value):
                self.assertEqual(csv_value(value), "'" + str(value))

    def test_ordinary_values_including_leading_spaces_are_unchanged(self):
        cases = [(None, ''), (0, '0'), (42, '42'), (3.5, '3.5')]
        cases += [(value, value) for value in (
            '', 'Jane Doe', 'jane@example.com', '612-555-0100', 'Ward 3, Precinct 2-A', '  Jane Doe', '\u00a0Main St',
            '\u200bname', '\ufeffName', ' 1-2', 'a=b', 'x =1', '1+1', '   ')]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(csv_value(value), expected)


@override_settings(LOCAL_ADMIN_ACCESS=True, HOSTED=False, DEMO_MODE=False)
class ContactCsvExportTests(SimpleTestCase):
    # Borrow the synthetic source stores; subclassing TerrierSourceContactsTests here would run its tests twice.
    setUp = base.TerrierSourceContactsTests.setUp
    tearDown = base.TerrierSourceContactsTests.tearDown
    put_meta = staticmethod(base.TerrierSourceContactsTests.put_meta)
    request = base.TerrierSourceContactsTests.request
    decoded = base.TerrierSourceContactsTests.decoded

    def test_export_guards_a_formula_behind_a_leading_character_without_changing_the_store(self):
        value = '\u00a0=1+1'
        with closing(sqlite3.connect(self.directory / 'contacts.sqlite3')) as connection, connection:
            connection.execute('UPDATE contacts SET name=? WHERE id=?', (value, self.people[0]))
        result = self.decoded(export_contacts, {'q': self.people[0]})
        self.assertEqual(result['count'], 1)
        rows = list(csv.reader(io.StringIO(result['csv'].removeprefix('\ufeff'))))
        self.assertEqual(rows[1][:2], [self.people[0], "'" + value])
        self.assertEqual(self.decoded(values={'q': self.people[0]})['rows'][0]['name'], value)
