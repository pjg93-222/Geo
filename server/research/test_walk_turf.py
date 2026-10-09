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

Trimming (blocks that carry 'parts', the people, undecided and supporter part of each of their doors): a door is worth its two
parts added; the last block keeps the doors worth most. Door values here are quarters (exact in floating point), the undecided
part three quarters of the value.
"""
import random
from math import hypot
from unittest import mock

from django.test import SimpleTestCase

from . import walk_turf
from .walk_turf import best_doors, shrink, trim_doors, turf

XY = {1: (0.0, 0.0), 2: (100.0, 0.0), 3: (200.0, 0.0), 4: (300.0, 0.0), 5: (100.0, 100.0),
      8: (600.0, 0.0), 9: (700.0, 0.0), 10: (800.0, 0.0), 11: (900.0, 0.0)}


def block(a, b, value, doors=10, people=None):
    people = doors + 3 if people is None else people
    return {'a': a, 'b': b, 'doors': doors, 'people': people, 'value': value, 'undecided': value * 0.75, 'supporter': value * 0.25}


def parted(a, b, values, people=1):
    """A block with a door (k, '') worth values[k] each, `people` people at each, and the sums of its doors."""
    parts = {(k, ''): (people, v * 0.75, v * 0.25) for k, v in enumerate(values)}
    return {'a': a, 'b': b, 'doors': len(values), 'people': people * len(values), 'value': sum(values),
            'undecided': sum(values) * 0.75, 'supporter': sum(values) * 0.25, 'parts': parts}


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
        result = turf(self.CHAIN, XY, 99, max_people=13, max_spans=1)              # the first block fits; the second passes max_spans
        self.assertEqual((result['spans'], result['stopped']), ([2], 'cap'))
        result = turf(self.CHAIN, XY, 99, max_people=1)                             # no block alone fits: an empty turf
        self.assertEqual((result['spans'], result['doors'], result['stopped']), ([], 0, 'cap'))
        self.assertEqual(turf(self.CHAIN, XY, 99)['stopped'], 'exhausted')
        self.assertEqual(turf(self.CHAIN, XY, 25, max_spans=3)['stopped'], 'full')  # the doors are reached before the cap is

    def test_a_seed_that_alone_passes_max_people_is_passed_over_for_the_next(self):
        blocks = {**self.CHAIN, 6: block(8, 9, 50.0, people=99)}                   # the best block, 300 m off, too big on its own
        result = turf(blocks, XY, 99, max_people=60)
        self.assertEqual((result['spans'], result['people'], result['stopped']), ([2, 4, 3, 1], 52, 'cap'))     # 6 would make 151

    def test_a_cut_off_seed_gives_way_to_the_next_best_seed(self):
        blocks = {**self.CHAIN, 7: block(10, 11, 50.0)}                             # the best block, alone 500 m off: out of reach
        result = turf(blocks, XY, 25)
        self.assertEqual((result['spans'], result['doors'], result['stopped'], result['jumps']), ([2, 4, 3], 30, 'full', []))
        self.assertEqual(turf(dict(reversed(list(blocks.items()))), XY, 25), result)
        result = turf(blocks, XY, 45)                                               # neither seed reaches 45: the one with more doors
        self.assertEqual((result['spans'], result['doors'], result['stopped']), ([2, 4, 3, 1], 40, 'far'))
        result = turf(blocks, XY, 5)                                                # the best seed alone is enough: it is kept
        self.assertEqual((result['spans'], result['stopped']), ([7], 'full'))

    def test_the_tie_between_seeds_goes_to_more_potential_then_the_earlier_seed(self):
        far = {1: block(1, 2, 9.0), 2: block(90, 91, 5.0), 3: block(92, 93, 7.0)}   # 10 doors each; 2 and 3 have no coordinates to reach
        self.assertEqual(turf(far, XY, 25)['spans'], [1])                          # equal doors: the most potential
        far[2] = block(90, 91, 5.0, doors=12)
        self.assertEqual(turf(far, XY, 25)['spans'], [2])                          # more doors first
        self.assertEqual(turf({3: block(1, 2, 9.0), 2: block(90, 91, 9.0), 1: block(92, 93, 9.0)}, XY, 25)['spans'], [1])   # all equal: the first seed

    def test_at_most_five_seeds_are_tried(self):
        xy = {n: (n * 1000.0, 0.0) for n in range(20)}
        lonely = {k: block(2 * k, 2 * k + 1, 10.0 - k, doors=1 if k < 5 else 9) for k in range(7)}   # 1 km apart, none in reach
        self.assertEqual(turf(lonely, xy, 9)['spans'], [0])                        # seeds 0 to 4, one door each: the sixth is not tried

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
                 'stopped': 'exhausted', 'trim': None}
        self.assertEqual(turf({k: blocks[k] for k in (6, 7, 8)}, XY, 25), empty)
        self.assertEqual(turf({}, XY, 25), empty)

    def test_the_sums_are_rounded_and_the_two_parts_add_up(self):
        result = turf({1: block(1, 2, 1 / 3), 2: block(2, 3, 2 / 3)}, XY, 99)
        self.assertEqual((result['value'], result['undecided'], result['supporter']), (1.0, 0.75, 0.25))
        self.assertEqual(result['spans'], [2, 1])
        result = turf({1: block(1, 2, 0.123456789)}, XY, 99)
        self.assertEqual(result['value'], 0.1235)


class TrimTests(SimpleTestCase):
    def test_the_doors_of_most_value_are_kept_most_valuable_first_and_ties_go_to_the_lower_key(self):
        doors = [((5, ''), 1.0), ((2, 'B'), 3.0), ((2, ''), 3.0), ((9, ''), 0.5), ((1, ''), 2.0)]
        self.assertEqual(trim_doors(doors, 3), [(2, ''), (2, 'B'), (1, '')])        # 3.0 twice: the key (2, '') is lower than (2, 'B')
        self.assertEqual(trim_doors(doors, 1), [(2, '')])
        self.assertEqual(trim_doors(doors, 0), [])
        self.assertEqual(trim_doors(doors, 5), [(2, ''), (2, 'B'), (1, ''), (5, ''), (9, '')])
        self.assertEqual(trim_doors(doors, 9), trim_doors(doors, 5))                # more than there are: all of them
        self.assertEqual(trim_doors([((3, ''), 1.0), ((1, ''), 1.0), ((2, ''), 1.0)], 2), [(1, ''), (2, '')])
        self.assertEqual(trim_doors(list(reversed(doors)), 3), trim_doors(doors, 3))      # whatever order they come in
        self.assertEqual(trim_doors([], 4), [])

    def test_a_door_is_worth_its_undecided_and_supporter_parts(self):
        parts = {(1, ''): (2, 0.5, 0.25), (2, ''): (1, 0.0, 1.0), (3, ''): (1, 0.75, 0.0)}
        self.assertEqual(best_doors(parts, 2), [(2, ''), (1, '')])                  # worth 1.0, 0.75 and 0.75: the tie goes to door 1
        self.assertEqual(best_doors(parts, 3), [(2, ''), (1, ''), (3, '')])

    def test_a_single_block_of_144_doors_is_trimmed_to_60(self):
        values = [(k % 12 + 1) * 0.25 for k in range(144)]                         # twelve doors at each of 0.25, 0.5 ... 3.0
        blocks = {7: parted(1, 2, values, people=2)}
        result = turf(blocks, XY, 60)
        self.assertEqual((result['spans'], result['doors'], result['people'], result['stopped']), ([7], 60, 120, 'full'))
        self.assertEqual(result['trim'], {'span': 7, 'kept': 60, 'of': 144})
        best = sorted(values, reverse=True)[:60]                                    # twelve doors each of 3.0, 2.75, 2.5, 2.25 and 2.0
        self.assertEqual(result['value'], sum(best))
        self.assertEqual((result['undecided'], result['supporter']), (sum(best) * 0.75, sum(best) * 0.25))
        self.assertEqual((min(best), sum(best)), (2.0, 150.0))
        self.assertEqual(turf(blocks, XY, 144)['trim'], None)                       # all 144 asked: nothing cut
        self.assertEqual(turf(blocks, XY, 144)['doors'], 144)

    def test_the_last_block_keeps_only_what_is_left_of_the_asked_doors(self):
        chain = {1: parted(1, 2, [1.0] * 10), 2: parted(2, 3, [4.0, 3.0, 2.0, 1.0, 0.5, 0.25, 0.25, 0.25, 0.25, 0.25]),
                 3: parted(3, 4, [2.0] * 10), 4: parted(2, 5, [3.5] * 10)}
        result = turf(chain, XY, 25)
        # worth 10, 11.75, 20 and 35: 4 starts, 2 and 1 touch it and 2 is worth more, then 3 touches 2 and is worth more than 1
        self.assertEqual(result['spans'], [4, 2, 3])
        self.assertEqual(result['trim'], {'span': 3, 'kept': 5, 'of': 10})               # 30 doors would pass 25: block 3 keeps its first five at 2.0
        self.assertEqual((result['doors'], result['people'], result['stopped']), (25, 25, 'full'))
        self.assertEqual((result['value'], result['undecided'], result['supporter']), (56.75, 42.5625, 14.1875))     # 35 + 11.75 + 5 x 2.0

    def test_the_trimmed_block_of_a_chain_keeps_its_best_doors(self):
        chain = {2: parted(2, 3, [4.0, 3.0, 2.0, 1.0, 0.5, 0.25, 0.25, 0.25, 0.25, 0.25]), 4: parted(2, 5, [1.0] * 10)}
        result = turf(chain, XY, 14)
        self.assertEqual((result['spans'], result['doors'], result['trim']), ([2, 4], 14, {'span': 4, 'kept': 4, 'of': 10}))
        result = turf(chain, XY, 5)                                                 # block 2 alone, cut to its five best: 4 + 3 + 2 + 1 + 0.5
        self.assertEqual((result['spans'], result['doors'], result['people'], result['value']), ([2], 5, 5, 10.5))
        self.assertEqual(result['trim'], {'span': 2, 'kept': 5, 'of': 10})

    def test_no_trim_when_the_count_is_exact_or_not_reached(self):
        chain = {1: parted(1, 2, [1.0] * 10), 2: parted(2, 3, [2.0] * 10), 3: parted(3, 4, [1.5] * 10)}
        for asked, doors in ((10, 10), (20, 20), (30, 30)):
            result = turf(chain, XY, asked)
            self.assertEqual((result['doors'], result['trim'], result['stopped']), (doors, None, 'full'), asked)
        result = turf(chain, XY, 31)                                                # the whole chain is 30 doors: nothing more to take
        self.assertEqual((result['doors'], result['trim'], result['stopped']), (30, None, 'exhausted'))
        result = turf(chain, XY, 21)
        self.assertEqual((result['doors'], result['trim']['kept'], result['trim']['of']), (21, 1, 10))

    def test_equal_doors_keep_the_lower_key(self):
        blocks = {1: parted(1, 2, [1.0] * 10)}
        result = turf(blocks, XY, 3)
        self.assertEqual((result['doors'], result['value']), (3, 3.0))
        self.assertEqual(result['trim'], {'span': 1, 'kept': 3, 'of': 10})
        self.assertEqual(trim_doors([(d, u + s) for d, (_, u, s) in blocks[1]['parts'].items()], 3), [(0, ''), (1, ''), (2, '')])

    def test_the_trimmed_block_is_what_the_caps_see(self):
        pair = {2: parted(1, 2, [5.0] * 10, people=3), 4: parted(2, 3, [1.0] * 10, people=3)}      # 30 people each
        self.assertEqual(turf(pair, XY, 14, max_people=44)['stopped'], 'full')      # 30 + 4 x 3 = 42 once the second block is cut
        self.assertEqual(turf(pair, XY, 14, max_people=44)['people'], 42)
        capped = turf(pair, XY, 20, max_people=44)                                  # whole, 60 people: the first block only
        self.assertEqual((capped['spans'], capped['stopped'], capped['trim']), ([2], 'cap', None))
        self.assertEqual(turf(pair, XY, 14, max_spans=1)['stopped'], 'cap')
        self.assertEqual(turf(pair, XY, 4, max_people=12)['trim'], {'span': 2, 'kept': 4, 'of': 10})  # the seed is cut before the cap sees it
        self.assertEqual(turf(pair, XY, 4, max_people=11)['spans'], [])                                 # 12 people even when cut: no seed fits

    def test_a_block_without_parts_is_taken_whole_as_before(self):
        result = turf({1: block(1, 2, 4.0), 2: block(2, 3, 9.0), 3: block(3, 4, 6.0)}, XY, 25)
        self.assertEqual((result['doors'], result['trim']), (30, None))

    def test_the_trimmed_turf_is_deterministic(self):
        blocks = {1: parted(1, 2, [1.0, 2.0, 2.0, 0.5] * 5), 2: parted(2, 3, [1.5] * 12)}
        first = turf(blocks, XY, 27)
        self.assertEqual(turf(dict(reversed(list(blocks.items()))), XY, 27), first)
        self.assertEqual(first['doors'], 27)


def _reference_grow(pool, xy, asked, jump_m, max_people, max_spans, seed):
    """The turf grown as before the grid index: a scan of the whole pool for each gap. The grid version must match it exactly."""
    def gap(ends, nodes):
        lines = [hypot(xy[end][0] - xy[node][0], xy[end][1] - xy[node][1]) for end in ends if end in xy for node in nodes if node in xy]
        return min(lines) if lines else None
    pool = dict(pool)
    order = lambda k: (-pool[k]['value'], k)
    picked, jumps, nodes, trim = [], [], set(), None
    total = dict.fromkeys(('doors', 'people', 'value', 'undecided', 'supporter'), 0)
    stopped, nxt, jump = 'exhausted', seed, None
    while nxt is not None:
        block, cut = pool.pop(nxt), None
        if block.get('parts') and total['doors'] + block['doors'] > asked:
            keep = asked - total['doors']
            cut, block = {'span': nxt, 'kept': keep, 'of': block['doors']}, shrink(block, keep)
        if total['people'] + block['people'] > max_people or len(picked) >= max_spans:
            stopped = 'cap'
            break
        picked.append(nxt)
        trim = cut
        nodes.update((block['a'], block['b']))
        for name in total:
            total[name] += block[name]
        if jump:
            jumps.append(jump)
        if total['doors'] >= asked:
            stopped = 'full'
            break
        touching = [k for k, b in pool.items() if b['a'] in nodes or b['b'] in nodes]
        if touching:
            nxt, jump = min(touching, key=order), None
            continue
        near = {k: d for k, d in ((k, gap((b['a'], b['b']), nodes)) for k, b in pool.items()) if d is not None and d <= jump_m}
        if near:
            nxt = min(near, key=order)
            jump = {'span': nxt, 'metres': round(near[nxt], 1)}
        else:
            nxt, stopped = None, 'far' if pool else 'exhausted'
    return {'asked': asked, 'spans': picked, 'doors': total['doors'], 'people': total['people'],
            'value': round(total['value'], 4), 'undecided': round(total['undecided'], 4),
            'supporter': round(total['supporter'], 4), 'jumps': jumps, 'stopped': stopped, 'trim': trim}


def synthetic(rng, n, layout):
    """n blocks laid out 'connected' (chained along rows), 'sparse' (each its own two nodes, 100 to 700 m apart) or 'islands'
    (sparse plus small clusters far from everything); values are rounded so ties occur, a few nodes have no coordinates."""
    blocks, xy, side = {}, {}, int(n ** 0.5) + 1
    spacing = rng.choice((120, 350, 700))
    for i in range(n):
        r, c = divmod(i, side)
        if layout == 'connected':
            a, b = ('n', r, c), ('n', r, c + 1)
            xy[a], xy[b] = (c * 100.0, r * 100.0), ((c + 1) * 100.0, r * 100.0)
        else:
            a, b = ('a', i), ('b', i)
            xy[a], xy[b] = (c * spacing + rng.random() * 40, r * spacing), (c * spacing + 50.0, r * spacing + rng.random() * 40)
        doors = rng.randint(3, 15)
        value = round(rng.random() * 4, 1) + 0.1
        blocks[i] = {'a': a, 'b': b, 'doors': doors, 'people': doors * 2, 'value': value, 'undecided': value * 0.5, 'supporter': value * 0.5}
        if rng.random() < 0.3:
            blocks[i]['parts'] = {(i, str(d)): (2, 0.25 * (d % 4 + 1), 0.25) for d in range(doors)}
            blocks[i].update(value=sum(u + s for _, u, s in blocks[i]['parts'].values()))
    if layout == 'islands':
        for k in range(6):
            for j in range(rng.randint(1, 4)):
                a, b = ('i', k, j), ('i', k, j + 1)
                xy[a], xy[b] = (1e5 * (k + 1) + j * 60.0, -5e4), (1e5 * (k + 1) + j * 60.0 + 50.0, -5e4)
                blocks[10 ** 6 + k * 10 + j] = {'a': a, 'b': b, 'doors': 2, 'people': 4, 'value': 50.0 - k, 'undecided': 1, 'supporter': 1}
    for node in rng.sample(sorted(xy, key=repr), max(1, len(xy) // 50)):
        del xy[node]
    return blocks, xy


class TurfGridIndexTests(SimpleTestCase):
    def test_the_grid_index_picks_what_the_full_scan_picked(self):
        for seed, (n, layout) in enumerate([(40, 'connected'), (60, 'sparse'), (120, 'sparse'), (80, 'islands'), (150, 'islands')] * 4):
            rng = random.Random(seed)
            blocks, xy = synthetic(rng, n, layout)
            for asked in (1, 25, 120, 400, 5000):
                for jump_m, max_people, max_spans in ((400.0, 1500, 200), (150.0, 10000, 30), (0.0, 10000, 200), (800.0, 300, 200)):
                    got = turf(blocks, xy, asked, jump_m, max_people, max_spans)
                    with mock.patch.object(walk_turf, 'grow', _reference_grow):
                        want = turf(blocks, xy, asked, jump_m, max_people, max_spans)
                    self.assertEqual(got, want, (seed, n, layout, asked, jump_m, max_people, max_spans))
