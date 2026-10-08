"""Candidate support (Terrier Phase 4B, O-11): how far each person leans toward the candidate's party, 0-100.

General mode: direction signals only, each turned toward the candidate's party; no turnout count is an input. Pure numpy
over the columns of the rowfw cache (name -> sequence), so the rowfw-support step and the tests run the same code.
Primary mode waits for Paul's approval of PRIMARY_MODE_SPEC.md: compute() refuses it.
"""
import numpy as np

SIGNALS = ('partisan', 'presidential', 'registration', 'primary_ballot')
INPUT_COLUMNS = ('sai_partisan_score', 'sai_harris_support_score', 'sai_trump_support_score', 'svi_party_registration')
PRIMARY_NOT_APPROVED = "Primary mode is not built yet: PRIMARY_MODE_SPEC.md waits for Paul's approval."


def ballot_columns(names, years):
    """svi_vh_<year>pp_party / svi_vh_<year>p_party present in `names`, newest first."""
    lo, hi = years
    return [c for y in range(hi, lo - 1, -1) for c in ('svi_vh_%dpp_party' % y, 'svi_vh_%dp_party' % y) if c in names]


def _num(column):
    return np.array([float(v) if v not in (None, '') else np.nan for v in column], dtype=np.float64)


def standing_pct(score):
    """Verbatim body of rowfw engine.standing_pct_only (RFB/rowfw/engine.py:77-81); test_support compares the source text."""
    key = np.round(score, 9)
    u, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
    L = np.cumsum(cnt) - cnt
    return (100.0 * (L + 0.5 * cnt) / len(key))[inv]


def signals(cols, party, cfg):
    """{signal: float array in 0..1, nan where absent} for a candidate of `party` ('D' or 'R')."""
    other = {'D': 'R', 'R': 'D'}[party]
    partisan = _num(cols['sai_partisan_score']) / 100.0
    harris, trump = _num(cols['sai_harris_support_score']), _num(cols['sai_trump_support_score'])
    values = {party: 1.0, other: 0.0, **cfg['party_values']}
    reg = np.array([values.get(c, 0.5) if isinstance(c, str) and c else np.nan for c in cols['svi_party_registration']])
    ballot = np.full(len(reg), np.nan)
    for c in ballot_columns(cols, cfg['ballot_years']):                       # newest first; the first D/R ballot wins
        a = np.asarray(cols[c], dtype=object)
        fill = np.isnan(ballot) & np.isin(a, (party, other))
        ballot[fill] = (a[fill] == party).astype(float)
    return {'partisan': partisan if party == 'D' else 1.0 - partisan,
            'presidential': ((harris + 100 - trump) if party == 'D' else (trump + 100 - harris)) / 200.0,
            'registration': reg, 'primary_ballot': ballot}


def orientation(cols):
    """{score column: {'D': mean, 'R': mean}} among D and R registrants (aggregates only). Raises ValueError when a score
    points the wrong way: partisan and Harris must average higher among D registrants, Trump among R."""
    reg = np.asarray(cols['svi_party_registration'], dtype=object)
    means = {}
    for name, higher in (('sai_partisan_score', 'D'), ('sai_harris_support_score', 'D'), ('sai_trump_support_score', 'R')):
        x = _num(cols[name])
        means[name] = {p: float(np.nanmean(x[reg == p])) for p in 'DR'}
        if not means[name][higher] > means[name]['R' if higher == 'D' else 'D']:         # also true for a nan mean
            raise ValueError('%s does not average higher among %s registrants (%s)' % (name, higher, means[name]))
    return means


def compute(d, cols):
    """{'sup_score', 'sup_pct', 'in_universe', 'coverage'} for district entry `d` (tools/districts.py) over `cols`."""
    if d['race_mode'] == 'primary':
        raise SystemExit(PRIMARY_NOT_APPROVED)
    if d['race_mode'] != 'general' or d['party'] not in ('D', 'R'):
        raise ValueError('%s: race_mode %r / party %r cannot be scored' % (d['id'], d['race_mode'], d['party']))
    s = signals(cols, d['party'], d['support'])
    w = d['support']['signals']
    num = sum(w[k] * np.nan_to_num(s[k]) for k in SIGNALS)
    den = sum(w[k] * np.isfinite(s[k]) for k in SIGNALS)
    if not (den > 0).all():
        raise ValueError('%s: %d people have no support signal at all' % (d['id'], int((~(den > 0)).sum())))
    score = 100.0 * num / den
    return {'sup_score': score, 'sup_pct': standing_pct(score), 'in_universe': np.ones(len(score), dtype=bool),
            'coverage': {k: int(np.isfinite(s[k]).sum()) for k in SIGNALS}}
