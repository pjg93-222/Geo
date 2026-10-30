"""Categorical input filtering for the owner's synthetic route simulations.

This changes which records enter the existing route planner, never its distances,
street geometry, household units or ordering algorithm. Blank is a separate code.
"""
import re


def parse(text):
    if text is None or text == '':
        return None  # no filter: preserve the existing request exactly
    if text == 'empty':
        return ()
    if not isinstance(text, str) or len(text) > 512:
        raise ValueError('Invalid affiliation selection.')
    codes = text.split(',')
    if len(codes) > 32 or len(set(codes)) != len(codes) or any(
            c != 'none' and not re.fullmatch(r'[A-Z0-9][A-Z0-9_-]{0,15}', c) for c in codes):
        raise ValueError('Choose distinct affiliation codes from the list.')
    return tuple(sorted(codes))


def wire(codes):
    return '' if codes is None else ','.join(codes) if codes else 'empty'


def sql(codes):
    if codes is None:
        return '', []
    if not codes:
        return ' AND 0', []
    return " AND COALESCE(NULLIF(p.party, ''), 'none') IN (%s)" % ','.join('?' for _ in codes), list(codes)


def keep(previous, codes):
    if codes is None:
        return previous
    allowed = frozenset(codes)
    return lambda row: (row['party'] or 'none') in allowed and (previous is None or previous(row))
