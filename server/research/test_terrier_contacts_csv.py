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


# FULL_WIDTH, BIDI_FORMAT, SKIPPED and TRIGGERS are copied verbatim from frontend/tests/v3/csv.test.ts,
# so both guards are pinned to the same code points.
FULL_WIDTH = ['＝', '＋', '－', '＠']
BIDI_FORMAT = ['‪', '‫', '‬', '‭', '‮', '⁦', '⁧', '⁨', '⁩', '­', '؜', '᠎']
SKIPPED = [[0x0000, 0x0020], [0x007f, 0x00a0], [0x00ad, 0x00ad], [0x061c, 0x061c], [0x1680, 0x1680], [0x180e, 0x180e],
           [0x2000, 0x200f], [0x2028, 0x202f], [0x205f, 0x2060], [0x2066, 0x2069], [0x3000, 0x3000], [0xfeff, 0xfeff]]
TRIGGERS = [0x002b, 0x002d, 0x003d, 0x0040, 0xff0b, 0xff0d, 0xff1d, 0xff20]


class ContactCsvFullWidthAndBidiTests(SimpleTestCase):
    def test_full_width_triggers_are_guarded_first_or_behind_any_skipped_prefix(self):
        for trigger in FULL_WIDTH:
            for prefix in ('', ' ', ' ', '​', '﻿', '\t', '　', '‮', '⁦­'):
                for value in (prefix + trigger + '1+1', prefix + trigger + 'HYPERLINK("https://example.invalid","x")'):
                    with self.subTest(value=value):
                        self.assertEqual(csv_value(value), "'" + value)

    def test_formulas_behind_bidi_and_format_characters_are_guarded(self):
        for prefix in BIDI_FORMAT + ['‮⁦', '‫ ‏', '­­ ']:
            for trigger in ['=', '+', '-', '@'] + FULL_WIDTH:
                value = prefix + trigger + '1+1'
                with self.subTest(value=value):
                    self.assertEqual(csv_value(value), "'" + value)

    def test_bidi_or_format_before_text_and_later_full_width_triggers_are_unchanged(self):
        values = [c + 'Jane Doe' for c in BIDI_FORMAT] + [
            '‮enaJ', '⁦Main St⁩', 'a＝b', 'x ＋1', '＃ 5', '１＋１',
            '­', '‮', 'Jane＠example.com']
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(csv_value(value), value)

    def test_guard_skips_exactly_skipped_and_fires_on_exactly_triggers_like_the_frontend(self):
        def in_ranges(cp):
            return any(a <= cp <= b for a, b in SKIPPED)
        first_only = (0x09, 0x0a, 0x0d)
        wrong = []
        for cp in range(0x110000):
            if 0xd800 <= cp <= 0xdfff:
                continue
            c = chr(cp)
            triggers = cp in TRIGGERS or cp in first_only
            if (csv_value(c + '1') != c + '1') != triggers:
                wrong.append('U+%04X as the first character' % cp)
            if (csv_value(c + '=1') != c + '=1') != (triggers or in_ranges(cp)):
                wrong.append('U+%04X before =' % cp)
        self.assertEqual(wrong, [])


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
