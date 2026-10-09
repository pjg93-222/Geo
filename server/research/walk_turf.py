"""One-click turf: pick the blocks of a precinct for one walk list (pure; no database, no Django).

Start at the block with the most untapped potential and grow along connected streets (a shared road node). When no block
left touches the turf, take the block with the most potential among those whose nearer end is within `jump_m` metres
(straight line, end node to end node) of the turf's nodes. Stop at the asked-for door count.

blocks: {span id: {'a', 'b' (end nodes), 'doors', 'people', 'value', 'undecided', 'supporter'}}, after the doors with a
recorded result are left out; xy: {node id: (x, y)} in metres. The block with the larger total goes first (not the larger
total per door), ties to the lower span id, so the same input always gives the same turf.
"""
from math import hypot


def gap(xy, ends, nodes):
    """The shortest straight line, in metres, from an end of a block to a node of the turf (None when none has coordinates)."""
    lines = [hypot(xy[end][0] - xy[node][0], xy[end][1] - xy[node][1]) for end in ends if end in xy for node in nodes if node in xy]
    return min(lines) if lines else None


def turf(blocks, xy, asked, jump_m=400.0, max_people=1500, max_spans=200):
    """{'asked', 'spans' (pick order), 'doors', 'people', 'value', 'undecided', 'supporter', 'jumps' ([{'span', 'metres'}], the
    blocks reached across a gap), 'stopped'}; the sums are rounded to 4 digits. 'stopped' says why the turf ends: 'full' it
    holds the asked-for doors, 'exhausted' no block with people and potential is left, 'far' none left is within jump_m, 'cap'
    the next block would take it past max_people or max_spans (the first block is always taken). The turf has at most
    1 + len(jumps) connected pieces, and overshoots the asked-for doors by less than its last block's doors."""
    pool = {k: b for k, b in blocks.items() if b['doors'] >= 1 and b['value'] > 0}
    order = lambda k: (-pool[k]['value'], k)
    picked, jumps, nodes = [], [], set()
    total = dict.fromkeys(('doors', 'people', 'value', 'undecided', 'supporter'), 0)
    stopped, nxt, jump = 'exhausted', (min(pool, key=order) if pool else None), None
    while nxt is not None:
        block = pool.pop(nxt)
        if picked and (total['people'] + block['people'] > max_people or len(picked) >= max_spans):
            stopped = 'cap'
            break
        picked.append(nxt)
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
        # ponytail: a straight scan of the pool for each gap, fine for the few hundred blocks of one precinct; a KD-tree if areas grow
        near = {k: d for k, d in ((k, gap(xy, (b['a'], b['b']), nodes)) for k, b in pool.items()) if d is not None and d <= jump_m}
        if near:
            nxt = min(near, key=order)
            jump = {'span': nxt, 'metres': round(near[nxt], 1)}
        else:
            nxt, stopped = None, 'far' if pool else 'exhausted'
    return {'asked': asked, 'spans': picked, 'doors': total['doors'], 'people': total['people'],
            'value': round(total['value'], 4), 'undecided': round(total['undecided'], 4),
            'supporter': round(total['supporter'], 4), 'jumps': jumps, 'stopped': stopped}
