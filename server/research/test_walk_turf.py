"""One-click turf (walk_turf.turf): a pure function over an invented street network, checked by hand.

The network (metres; a node is a road end, a block runs between two of them):

    n5 (100, 100)
       |  block 4
    n1 ---- n2 ---- n3 ---- n4          n8 ---- n9  n10 ---- n11
      b1       b2       b3     300 m      b6      b7
    (0,0)  (100,0)  (200,0)  (300,0)  (600,0)  (700,0) (800,0) (900,0)

Block 1 runs n1-n2, 2 runs n2-n3, 3 runs n3-n4, 4 runs n2-n5, 6 runs n8-n9 and 7 runs n10-n11. Block 6 is 300 m from the end of the
chain (n4) and block 7 is 500 m from it, so with a 400 m reach only 6 can be crossed to from the chain. Every block has 10 doors
and 13 people unless said otherwise; its undecided part is three quarters of its value.
"""
from django.test import SimpleTestCase

from .walk_turf import turf

XY = {1: (0.0, 0.0), 2: (100.0, 0.0), 3: (200.0, 0.0), 4: (300.0, 0.0), 5: (100.0, 100.0),
      8: (600.0, 0.0), 9: (700.0, 0.0), 10: (800.0, 0.0), 11: (900.0, 0.0)}


def block(a, b, value, doors=10, people=None):
    people = doors + 3 if people is None else people
    return {'a': a, 'b': b, 'doors': doors, 'people': people, 'value': value, 'undecided': value * 0.75, 'supporter': value * 0.25}


def pieces(blocks, spans):
    """How many connected pieces the picked blocks make, by search over shared end nodes."""
    left, count = set(spans), 0
    while left:
        count += 1
        frontier = [left.pop()]
        while frontier:
            current = frontier.pop()
            ends = {blocks[current]['a'], blocks[current]['b']}
            for other in [o for o in left if ends & {blocks[o]['a'], blocks[o]['b']}]:
                left.discard(other)
                frontier.append(other)
    return count


class TurfTests(SimpleTestCase):
    CHAIN = {1: block(1, 2, 4.0), 2: block(2, 3, 9.0), 3: block(3, 4, 6.0), 4: block(2, 5, 7.0)}

    def test_it_starts_at_the_block_with_the_most_potential_and_grows_to_the_best_block_that_touches(self):
        result = turf(self.CHAIN, XY, 25)
        # block 2 (9.0) first; 1, 3 and 4 all touch it and 4 (7.0) is the best; then 3 (6.0), which touches 2
        self.assertEqual(result['spans'], [2, 4, 3])
        self.assertEqual((result['doors'], result['people'], result['stopped']), (30, 39, 'full'))
        self.assertEqual((result['value'], result['undecided'], result['supporter']), (22.0, 16.5, 5.5))
        self.assertEqual((result['asked'], result['jumps']), (25, []))

    def test_a_better_block_that_does_not_touch_waits_while_a_touching_one_is_left(self):
        blocks = {**self.CHAIN, 6: block(8, 9, 50.0)}              # the best block of all, 300 m from the chain: the turf starts there
        self.assertEqual(turf(blocks, XY, 25)['spans'][0], 6)
        blocks[6] = block(8, 9, 5.0)                               # one that is not the best comes after every block that touches
        result = turf(blocks, XY, 25)
        self.assertEqual((result['spans'], result['jumps']), ([2, 4, 3], []))
        self.assertEqual(turf(blocks, XY, 45)['spans'], [2, 4, 3, 1, 6])

    def test_the_door_count_is_reached_without_going_past_the_last_block(self):
        for asked in (1, 10, 11, 20, 25, 30, 31, 40, 41, 45):
            result = turf(self.CHAIN, XY, asked)
            last = self.CHAIN[result['spans'][-1]]['doors']
            if result['stopped'] == 'full':
                self.assertGreaterEqual(result['doors'], asked)
                self.assertLess(result['doors'] - last, asked)
            else:
                self.assertEqual(result['stopped'], 'exhausted')
                self.assertLess(result['doors'], asked)
        self.assertEqual(turf(self.CHAIN, XY, 1)['spans'], [2])                      # one block is always taken
        self.assertEqual(turf(self.CHAIN, XY, 40)['stopped'], 'full')                # 40 doors is every block of the chain
        self.assertEqual((turf(self.CHAIN, XY, 41)['doors'], turf(self.CHAIN, XY, 41)['stopped']), (40, 'exhausted'))

    def test_a_gap_of_300_metres_is_crossed_and_one_of_500_is_not(self):
        result = turf({**self.CHAIN, 6: block(8, 9, 1.0)}, XY, 45)
        self.assertEqual(result['spans'], [2, 4, 3, 1, 6])
        self.assertEqual(result['jumps'], [{'span': 6, 'metres': 300.0}])
        self.assertEqual((result['doors'], result['stopped']), (50, 'full'))
        result = turf({**self.CHAIN, 7: block(10, 11, 1.0)}, XY, 45)
        self.assertEqual((result['spans'], result['jumps'], result['doors'], result['stopped']), ([2, 4, 3, 1], [], 40, 'far'))

    def test_the_reach_is_a_parameter_and_the_nearer_end_is_what_counts(self):
        blocks = {**self.CHAIN, 6: block(8, 9, 1.0)}
        self.assertEqual(turf(blocks, XY, 45, jump_m=299.9)['stopped'], 'far')
        self.assertEqual(turf(blocks, XY, 45, jump_m=300.0)['jumps'], [{'span': 6, 'metres': 300.0}])
        flipped = {**self.CHAIN, 6: block(9, 8, 1.0)}                               # the same block written the other way round
        self.assertEqual(turf(flipped, XY, 45)['jumps'], [{'span': 6, 'metres': 300.0}])
        self.assertEqual(turf({**self.CHAIN, 7: block(10, 11, 1.0)}, XY, 45, jump_m=500.0)['jumps'], [{'span': 7, 'metres': 500.0}])

    def test_the_best_block_within_reach_is_the_one_crossed_to(self):
        blocks = {**self.CHAIN, 6: block(8, 9, 1.0), 12: block(8, 9, 3.0)}
        result = turf(blocks, XY, 55)
        self.assertEqual([j['span'] for j in result['jumps']], [12])
        self.assertEqual(result['spans'][-2:], [12, 6])            # once across, 6 touches 12 and needs no second jump

    def test_a_block_with_no_coordinates_cannot_be_crossed_to(self):
        self.assertEqual(turf({**self.CHAIN, 6: block(98, 99, 1.0)}, XY, 45)['stopped'], 'far')

    def test_the_pieces_are_connected_apart_from_the_gaps(self):
        result = turf(self.CHAIN, XY, 45)
        self.assertEqual(pieces(self.CHAIN, result['spans']), 1)
        blocks = {**self.CHAIN, 6: block(8, 9, 1.0)}
        result = turf(blocks, XY, 50)
        self.assertEqual(len(result['spans']), 5)
        self.assertEqual(pieces(blocks, result['spans']), 2)
        self.assertLessEqual(pieces(blocks, result['spans']), 1 + len(result['jumps']))

    def test_the_caps_stop_the_turf_before_the_block_that_would_pass_them(self):
        result = turf(self.CHAIN, XY, 99, max_people=30)
        self.assertEqual((result['spans'], result['people'], result['stopped']), ([2, 4], 26, 'cap'))      # block 3 would make 39
        result = turf(self.CHAIN, XY, 99, max_spans=3)
        self.assertEqual((result['spans'], result['stopped']), ([2, 4, 3], 'cap'))
        result = turf(self.CHAIN, XY, 99, max_people=1, max_spans=1)               # the first block is always taken
        self.assertEqual((result['spans'], result['stopped']), ([2], 'cap'))
        self.assertEqual(turf(self.CHAIN, XY, 99)['stopped'], 'exhausted')
        self.assertEqual(turf(self.CHAIN, XY, 25, max_spans=3)['stopped'], 'full')  # the doors are reached before the cap is

    def test_ties_go_to_the_lower_span_id(self):
        blocks = {7: block(2, 3, 5.0), 3: block(1, 2, 5.0), 5: block(3, 4, 5.0)}
        self.assertEqual(turf(blocks, XY, 1)['spans'], [3])                         # three equal blocks: the lowest id starts
        self.assertEqual(turf(blocks, XY, 99)['spans'], [3, 7, 5])                  # 7 touches 3 at n2, then 5 touches 7 at n3
        tied = {2: block(2, 3, 9.0), 9: block(3, 4, 4.0), 4: block(2, 5, 4.0)}
        self.assertEqual(turf(tied, XY, 25)['spans'], [2, 4, 9])                    # 4 and 9 both touch block 2 and tie

    def test_the_same_input_gives_the_same_turf_whatever_order_it_comes_in(self):
        blocks = {**self.CHAIN, 6: block(8, 9, 1.0)}
        first = turf(blocks, XY, 45)
        self.assertEqual(turf(dict(reversed(list(blocks.items()))), XY, 45), first)
        self.assertEqual(turf(blocks, XY, 45), first)

    def test_blocks_without_doors_or_potential_are_not_in_the_pool(self):
        blocks = {**self.CHAIN, 6: block(1, 2, 99.0, doors=0, people=0), 7: block(1, 2, 0.0), 8: block(1, 2, -1.0)}
        self.assertEqual(turf(blocks, XY, 25)['spans'], [2, 4, 3])
        empty = {'asked': 25, 'spans': [], 'doors': 0, 'people': 0, 'value': 0, 'undecided': 0, 'supporter': 0, 'jumps': [],
                 'stopped': 'exhausted'}
        self.assertEqual(turf({k: blocks[k] for k in (6, 7, 8)}, XY, 25), empty)
        self.assertEqual(turf({}, XY, 25), empty)

    def test_the_sums_are_rounded_and_the_two_parts_add_up(self):
        result = turf({1: block(1, 2, 1 / 3), 2: block(2, 3, 2 / 3)}, XY, 99)
        self.assertEqual((result['value'], result['undecided'], result['supporter']), (1.0, 0.75, 0.25))
        self.assertEqual(result['spans'], [2, 1])
        result = turf({1: block(1, 2, 0.123456789)}, XY, 99)
        self.assertEqual(result['value'], 0.1235)
