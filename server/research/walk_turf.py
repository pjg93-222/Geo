"""One-click turf: pick the blocks of a precinct for one walk list (pure; no database, no Django).

Start at the block with the most untapped potential and grow along connected streets (a shared road node). When no block
left touches the turf, take the block with the most potential among those whose nearer end is within `jump_m` metres
(straight line, end node to end node) of the turf's nodes. Stop at the asked-for door count. When that seed is cut off (the turf
stops short with no block left in reach), grow again from the next best blocks not yet in a turf, up to SEEDS seeds in all, and
keep the turf with the most doors (then the most potential, then the earlier seed).

blocks: {span id: {'a', 'b' (end nodes), 'doors', 'people', 'value', 'undecided', 'supporter'}}, after the doors with a
recorded result are left out; xy: {node id: (x, y)} in metres. The block with the larger total goes first (not the larger
total per door), ties to the lower span id, so the same input always gives the same turf.

A block may also carry 'parts', {door: (people, undecided, supporter)} over its doors (a door is any ordered key, in the app
(stop id, unit)); a door is worth undecided + supporter. A turf whose last block would pass the asked-for doors then keeps
only that block's doors worth most (trim_doors), so it holds exactly the asked-for doors. The walk list does the same with
the same function (walk_views), so the list built from a turf has the turf's doors.
"""
from math import hypot

SEEDS = 5       # seeds tried for one turf: the best block, then the next best not in a turf already when the turf falls short


def gap(xy, ends, nodes):
    """The shortest straight line, in metres, from an end of a block to a node of the turf (None when none has coordinates)."""
    lines = [hypot(xy[end][0] - xy[node][0], xy[end][1] - xy[node][1]) for end in ends if end in xy for node in nodes if node in xy]
    return min(lines) if lines else None


def cells(pool, xy, jump_m):
    """The end nodes of the pool's blocks bucketed in a grid of jump_m-metre cells: {(i, j): [(span, end)]} over ends with coordinates."""
    size, grid = jump_m if jump_m > 0 else 1.0, {}
    for k, b in pool.items():
        for end in (b['a'], b['b']):
            if end in xy:
                grid.setdefault((int(xy[end][0] // size), int(xy[end][1] // size)), []).append((k, end))
    return grid


def reach_from(grid, pool, xy, node, jump_m, reach):
    """Lower reach[span] to the straight line from `node` to an end of a block of the grid still in the pool, for the ends within jump_m of it
    (only the 3 x 3 cells around the node can hold one, as a cell is at least jump_m wide)."""
    if node not in xy:
        return
    size = jump_m if jump_m > 0 else 1.0
    cx, cy = int(xy[node][0] // size), int(xy[node][1] // size)
    for i in range(cx - 1, cx + 2):
        for j in range(cy - 1, cy + 2):
            for k, end in grid.get((i, j), ()):
                d = hypot(xy[end][0] - xy[node][0], xy[end][1] - xy[node][1])
                if k in pool and d <= jump_m and d < reach.get(k, d + 1):
                    reach[k] = d


def trim_doors(doors, keep):
    """The keys of the `keep` doors of most value, most valuable first. doors: [(key, value)]; equal values go to the lower key."""
    return [key for key, _ in sorted(doors, key=lambda door: (-door[1], door[0]))[:keep]]


def best_doors(parts, keep):
    """The `keep` doors of most value among parts, {door: (people, undecided, supporter)}: a door is worth undecided + supporter."""
    return trim_doors([(door, undecided + supporter) for door, (_, undecided, supporter) in parts.items()], keep)


def shrink(block, keep):
    """The block with only its `keep` doors of most value (from its 'parts'); the sums are those of the doors kept."""
    parts = block['parts']
    kept = best_doors(parts, keep)
    people, undecided, supporter = (sum(parts[door][i] for door in kept) for i in range(3))
    return {**block, 'doors': len(kept), 'people': people, 'undecided': undecided, 'supporter': supporter,
            'value': undecided + supporter, 'parts': {door: parts[door] for door in kept}}


def turf(blocks, xy, asked, jump_m=400.0, max_people=1500, max_spans=200):
    """{'asked', 'spans' (pick order), 'doors', 'people', 'value', 'undecided', 'supporter', 'jumps' ([{'span', 'metres'}], the
    blocks reached across a gap), 'stopped', 'trim'}; the sums are rounded to 4 digits. 'stopped' says why the turf ends: 'full' it
    holds the asked-for doors, 'exhausted' no block with people and potential is left, 'far' none left is within jump_m, 'cap'
    the next block would take it past max_people or max_spans (a seed that alone passes max_people is passed over for the next
    seed; when none fits the turf is empty and 'cap'). 'trim' is {'span', 'kept', 'of'} when the last block was cut to its `kept`
    doors of most value out of `of` (blocks with 'parts'), else None. The turf has at most 1 + len(jumps) connected pieces; with no
    'parts' it overshoots the asked-for doors by less than its last block's doors."""
    pool = {k: b for k, b in blocks.items() if b['doors'] >= 1 and b['value'] > 0}
    best, seen, tries = None, set(), 0
    for seed in sorted(pool, key=lambda k: (-pool[k]['value'], k)):
        if seed in seen:
            continue
        got = grow(pool, xy, asked, jump_m, max_people, max_spans, seed)
        if not got['spans']:                    # the seed alone is past max_people: the next seed
            best = best or got
            continue
        seen.update(got['spans'])
        tries += 1
        if not best or not best['spans'] or (got['doors'], got['value']) > (best['doors'], best['value']):
            best = got
        if best['doors'] >= asked or best['stopped'] not in ('far', 'exhausted') or tries >= SEEDS:
            break
    return best or grow(pool, xy, asked, jump_m, max_people, max_spans, None)


def grow(pool, xy, asked, jump_m, max_people, max_spans, seed):
    """The turf grown from `seed` (None: an empty turf) over a copy of pool, as turf() describes it."""
    pool = dict(pool)
    order = lambda k: (-pool[k]['value'], k)
    picked, jumps, nodes, trim = [], [], set(), None
    grid, reach = cells(pool, xy, jump_m), {}       # reach: {block: its gap to the turf} for the blocks within jump_m of it
    total = dict.fromkeys(('doors', 'people', 'value', 'undecided', 'supporter'), 0)
    stopped, nxt, jump = 'exhausted', seed, None
    while nxt is not None:
        block, cut = pool.pop(nxt), None
        reach.pop(nxt, None)
        if block.get('parts') and total['doors'] + block['doors'] > asked:      # the last block: only the doors that fit
            keep = asked - total['doors']
            cut, block = {'span': nxt, 'kept': keep, 'of': block['doors']}, shrink(block, keep)
        if total['people'] + block['people'] > max_people or len(picked) >= max_spans:
            stopped = 'cap'
            break
        picked.append(nxt)
        trim = cut
        for node in (block['a'], block['b']):
            if node not in nodes:
                nodes.add(node)
                reach_from(grid, pool, xy, node, jump_m, reach)
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
        if reach:
            nxt = min(reach, key=order)
            jump = {'span': nxt, 'metres': round(reach[nxt], 1)}
        else:
            nxt, stopped = None, 'far' if pool else 'exhausted'
    return {'asked': asked, 'spans': picked, 'doors': total['doors'], 'people': total['people'],
            'value': round(total['value'], 4), 'undecided': round(total['undecided'], 4),
            'supporter': round(total['supporter'], 4), 'jumps': jumps, 'stopped': stopped, 'trim': trim}
