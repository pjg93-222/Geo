"""Voter profiles (tools/profiles.py): every registration code, the boundaries of the thirds, a missing score, a D and an R
candidate, and the offered list.

    <venv python> -m unittest tools.test_profiles     (from the V3 folder)
"""
import unittest

from tools import field_labels as FL
from tools import profiles as P

CODES = ('D', 'R', 'U', 'L', 'G', 'W', 'O', '', None)
THIRD, TWO_THIRDS = 1 / 3, 2 / 3


class ProfileTests(unittest.TestCase):
    def test_defaults_are_the_identify_thirds(self):
        self.assertEqual((FL.SUPPORT_MIN, FL.PERSUADE_MIN, FL.PERSUADE_MAX), (2 / 3, 1 / 3, 2 / 3))

    def test_all_counts_everyone_and_is_no_filter(self):
        self.assertIsNone(P.member_fn('all', 'D'))
        everyone = P.PROFILES['all'][1]('D')
        self.assertTrue(all(everyone(code, s) for code in CODES for s in (None, 0.0, 0.5, 1.0)))

    def test_democratic_candidate_every_code(self):
        member = P.member_fn('coalition', 'D')
        for code in ('U', 'L', 'G', 'W', 'O', '', None):         # unaffiliated and minor parties: all of them, scored or not
            for s in (None, 0.0, 0.5, 1.0):
                self.assertTrue(member(code, s), (code, s))
        self.assertTrue(member('D', 0.9) and member('D', 1.0))
        self.assertFalse(member('D', 0.6) or member('D', 0.0))
        self.assertTrue(member('R', 0.5) and member('R', 0.4))
        self.assertFalse(member('R', 0.1) or member('R', 0.9))

    def test_boundaries(self):
        member = P.member_fn('coalition', 'D')
        self.assertTrue(member('D', TWO_THIRDS))                              # supporter edge included
        self.assertTrue(member('D', 66.66666666666667 / 100))                 # floating point of a stored 66.67 score
        self.assertFalse(member('D', TWO_THIRDS - 1e-6))
        self.assertTrue(member('R', THIRD) and member('R', TWO_THIRDS))       # persuadable edges included
        self.assertTrue(member('R', 33.33333333333333 / 100))
        self.assertFalse(member('R', THIRD - 1e-6) or member('R', TWO_THIRDS + 1e-6))

    def test_missing_score(self):
        member = P.member_fn('coalition', 'D')
        self.assertFalse(member('D', None))              # an own-party person with no s is not a supporter
        self.assertTrue(member('R', None))               # an other-party person with no s counts as 0.5: persuadable
        self.assertTrue(member('U', None))

    def test_republican_candidate_is_symmetric(self):
        member = P.member_fn('coalition', 'R')
        self.assertTrue(member('R', 0.8) and not member('R', 0.5) and not member('R', None))
        self.assertTrue(member('D', 0.5) and member('D', THIRD) and not member('D', 0.9) and not member('D', 0.1))
        self.assertTrue(all(member(code, 0.0) for code in ('U', 'L', 'G', 'W', 'O', '', None)))

    def test_a_candidate_of_another_party_is_refused_by_name(self):
        for party in ('L', 'G', ''):
            with self.assertRaisesRegex(ValueError, 'candidate party is %r' % party):
                P.member_fn('coalition', party)
        self.assertIsNone(P.member_fn('all', 'L'))          # 'all' filters nobody, whatever the party

    def test_offered(self):
        self.assertEqual(P.offered(None), ['all'])
        self.assertEqual(P.offered('D'), ['all', 'coalition'])
        self.assertEqual(P.offered('L'), ['all'])
        self.assertEqual(P.listing('R'), [{'key': 'all', 'label': 'Everyone'},
                                          {'key': 'coalition', 'label': 'Supporters and persuadables'}])
        self.assertEqual(P.listing(None), [{'key': 'all', 'label': 'Everyone'}])

    def test_labels_pass_the_advisory_gate(self):
        self.assertFalse([label for label, _ in P.PROFILES.values() if FL.ADVISORY_PATTERN.search(label)])


if __name__ == '__main__':
    unittest.main()
