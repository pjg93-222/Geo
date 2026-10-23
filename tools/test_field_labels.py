"""The advisory-wording gate (field_labels.ADVISORY_PATTERN, self_check) and the audience filter labels (D4 of
docs/AUDIENCE_FILTER_SPEC.md: exactly the label "Persuadable" is exempt).

    <venv python> -m unittest tools.test_field_labels     (from the V3 folder)
"""
import re
import unittest
from unittest import mock

from tools import field_labels as FL

SPECTRUM = re.compile(r'\b(far left|left|lean left|centrist|lean right|right|far right)\b', re.I)


class AdvisoryGateTests(unittest.TestCase):
    def test_self_check_passes_with_the_audience_labels(self):
        self.assertGreater(FL.self_check(), 0)
        self.assertIn('Persuadable', FL.AUDIENCE_PRESET_LABELS)

    def test_only_the_one_label_is_exempt(self):
        self.assertEqual(FL.ADVISORY_EXEMPT, frozenset({'Persuadable'}))
        for text in ('persuasion', 'Persuad', 'persuad voters', 'Persuadable voters to target', 'Best streets'):
            with self.subTest(text=text), mock.patch.object(FL, 'AUDIENCE_PRESET_LABELS', ('Persuadable', text)):
                self.assertTrue(FL.ADVISORY_PATTERN.search(text))
                with self.assertRaises(ValueError):
                    FL.self_check()

    def test_the_exemption_is_the_whole_string(self):
        with mock.patch.object(FL, 'AUDIENCE_PRESET_LABELS', ('Persuadable', 'Persuadable target')):
            with self.assertRaises(ValueError):
                FL.self_check()

    def test_audience_labels_name_the_data_not_a_spectrum(self):
        labels = [*FL.AUDIENCE_PARTY_LABELS.values(), *FL.AUDIENCE_CANVASS_LABELS.values(), *FL.AUDIENCE_PRESET_LABELS]
        self.assertEqual([t for t in labels if SPECTRUM.search(t)], [])
        self.assertEqual(set(FL.AUDIENCE_PARTY_LABELS), {'D', 'R', 'U', 'G', 'L', 'W', 'O', 'T', 'none'})
        self.assertEqual(set(FL.AUDIENCE_CANVASS_LABELS), {'S', 'U', 'O', 'NH', 'none'})
        self.assertEqual((FL.AUDIENCE_PARTY_LABELS['G'], FL.AUDIENCE_PARTY_LABELS['T'], FL.AUDIENCE_PARTY_LABELS['U']),
                         ('Registered Green', 'Party code T', 'Registered Unaffiliated'))


if __name__ == '__main__':
    unittest.main()
