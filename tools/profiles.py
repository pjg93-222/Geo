"""Voter profiles: which people a walk list and the street plan count, apart from how the blocks are ranked.

A profile is (label, builder); the builder takes the candidate's party ('D' or 'R') and gives member(code, s) -> bool, with
`code` the person's registration code as the walk store holds it ('' or None for none) and `s` the support score / 100
(None when the person has no score). Pure: no store, no Django.

  all        everyone (the default; the output is the same as without a profile)
  coalition  supporters and persuadables, for a candidate of party P and the other major party Q:
               registered P with s >= SUPPORT_MIN, or registered with neither P nor Q (unaffiliated and minor parties, all of
               them), or registered Q with PERSUADE_MIN <= s <= PERSUADE_MAX. A person with no s counts as 0.5 for the
               persuadable test (as untapped.py does), and an own-party person with no s is not a supporter.
The thresholds are tools/field_labels.py's (defaults pending Paul's ruling).
"""
from . import field_labels as FL

TOLERANCE = 1e-9    # s is score / 100 in floating point; the same slack as the middle third of the street plan (walk_views.MIDDLE)
OTHER = {'D': 'R', 'R': 'D'}


def _everyone(party):
    return lambda code, s: True


def _coalition(party):
    own, other = party, OTHER[party]

    def member(code, s):
        if code == own:
            return s is not None and s >= FL.SUPPORT_MIN - TOLERANCE
        if code == other:
            return FL.PERSUADE_MIN - TOLERANCE <= (0.5 if s is None else s) <= FL.PERSUADE_MAX + TOLERANCE
        return True
    return member


PROFILES = {'all': ('Everyone', _everyone), 'coalition': ('Supporters and persuadables', _coalition)}


def offered(party):
    """The profile keys a district whose candidate is of `party` (None: no candidate) offers: only 'all' without one."""
    return list(PROFILES) if party else ['all']


def listing(party):
    """[{'key', 'label'}] of the profiles offered."""
    return [{'key': key, 'label': PROFILES[key][0]} for key in offered(party)]


def member_fn(key, party):
    """member(code, s) for profile `key` and candidate party `party`; None for 'all' (no filter at all)."""
    return None if key == 'all' else PROFILES[key][1](party)
