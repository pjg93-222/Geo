"""Audience filter: voter-file door counts per street inside a drawn polygon (docs/AUDIENCE_FILTER_SPEC.md).

  GET  /api/v3/walk/audience/options  {district, candidate_party ('D', 'R' or null: the slider's end labels),
                                      support_coverage, party: [{code, label, count}], canvass: [{code, label, count}]}: the
                                      choices with the people of the district on each (movers left out). Party codes D R U G
                                      L W O T and 'none' (blank); canvass codes S U O NH and 'none' (no result yet). MV is
                                      never offered.
  POST /api/v3/walk/audience/streets  {polygon: [[lon, lat], ...], include: {party, canvass}, exclude: {party, canvass},
                                      support: {low, high, include_no_score}} -> {basis: 'voter-file', streets: [{id, label,
                                      center [lon, lat], matching_doors, voter_doors, dropped_by_exclude}], totals:
                                      {matching_doors, voter_doors, dropped_by_exclude, unplaced}, support_coverage}. A street
                                      with voter doors in the area is listed even when none match. unplaced: matching doors
                                      on the listed streets whose stop has no map position (in no street's counts).
  Errors are {error} with 400, 403 or 503.

A door is a distinct (stop, unit), as WalkStore.street_counts has it, and it is in the area when the polygon covers its
stop. A person is included when every dimension takes them: registration and canvass by their include choices (OR-ed
within a dimension; none chosen takes everyone) and the support range low <= sup_score <= high (both ends included; a
person with no support row only with include_no_score). A person is excluded when they match any exclude choice. A door
matches when at least one resident is included and no resident is excluded (exclude wins, Q1); dropped_by_exclude counts
the doors that exclude wins removed although a resident there is included and not excluded (a mixed household). Canvass is each voter's newest result that stands
across all lists (walk_views.latest_codes), with no age cutoff; a voter whose result is Moved is left out of every count.

Local administrator only (Q6) with walk_views' gate: loopback owner, 403 in demo mode. No audit (Q9) and no small-count
floor (Q7). Nothing per person or per door leaves the server.
"""
import math
from collections import Counter

import numpy as np
import shapely
from django.conf import settings

from tools import field_labels as FL

from . import area_street_views as A
from . import walk_store as W
from . import walk_views as V
from .street_views import response

PARTIES = tuple(FL.AUDIENCE_PARTY_LABELS)       # 'none' is "No party code"
CANVASS = tuple(FL.AUDIENCE_CANVASS_LABELS)     # 'none' is "No result yet"
MOVED = 'MV'
SLACK = 1e-9            # as tools/profiles.TOLERANCE: a preset edge at an exact third keeps a score stored at that third
MAX_STREETS = A.MAX_STREETS
TOO_MANY = 'This area holds more than %s streets with voters. Draw a smaller area.' % '{:,}'.format(MAX_STREETS)
# CROSS JOIN keeps stop as the outer loop: the WHERE is on stop, and SQLite would otherwise scan every person by person_stop
# (seconds on a 560k-voter district, against tenths of a second this way)
ROWS = ("SELECT p.street_id, p.stop_id, IFNULL(p.unit, ''), IFNULL(NULLIF(p.party, ''), 'none'), p.voter_key, s.lon, s.lat, %s "
        "FROM stop s CROSS JOIN person p ON p.stop_id = s.id%s WHERE %s")
SCORE_JOIN = (' LEFT JOIN scores.person sc ON sc.person_id = p.person_id'
              ' LEFT JOIN scores.support sp ON sp.pos = sc.pos')


def _choices(value, allowed):
    if (not isinstance(value, list) or len(value) > len(allowed) or not all(isinstance(v, str) and v in allowed for v in value)
            or len(set(value)) != len(value)):
        raise V.Refused('Choose filter values from the offered list.')
    return frozenset(value)


def _side(value):
    if not isinstance(value, dict) or set(value) != {'party', 'canvass'}:
        raise V.Refused('Give include and exclude as party and canvass lists.')
    return _choices(value['party'], PARTIES), _choices(value['canvass'], CANVASS)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def parse(body):
    """(polygon, include, exclude, support) of a request body; include and exclude are (party set, canvass set), support is
    (low, high, include no score). Refused on anything outside the whitelist."""
    if not isinstance(body, dict) or set(body) != {'polygon', 'include', 'exclude', 'support'}:
        raise V.Refused('Provide only polygon, include, exclude and support.')
    support = body['support']
    if not isinstance(support, dict) or set(support) != {'low', 'high', 'include_no_score'}:
        raise V.Refused('Give support as low, high and include_no_score.')
    low, high, no_score = support['low'], support['high'], support['include_no_score']
    if not (_number(low) and _number(high) and 0 <= low <= high <= 100) or type(no_score) is not bool:
        raise V.Refused('The support range runs from 0 to 100, low no higher than high.')
    try:
        polygon = A.polygon_input(body['polygon'])
    except A.RequestProblem as problem:
        raise V.Refused(str(problem)) from None
    return polygon, _side(body['include']), _side(body['exclude']), (low, high, no_score)


def rule(include, exclude, support, codes):
    """A function of (party, voter key, support score or None) giving (included, excluded), or None for a voter whose
    canvass result is Moved. codes: {voter key: newest standing canvass code}."""
    (in_party, in_canvass), (out_party, out_canvass) = include, exclude
    low, high, no_score = support

    def person(party, key, score):
        canvass = codes.get(key, 'none')
        if canvass == MOVED:
            return None
        in_range = no_score if score is None else low - SLACK <= score <= high + SLACK
        included = in_range and (not in_party or party in in_party) and (not in_canvass or canvass in in_canvass)
        return included, party in out_party or canvass in out_canvass
    return person


def _rows(store, where, args):
    scored = store.attach_scores() and store.support
    return store.conn.execute(ROWS % ('sp.sup_score' if scored else 'NULL', SCORE_JOIN if scored else '', where), args)


def _doors(rows, person, inside=None):
    """{(stop, unit): [street, lon, lat, any included, any excluded, any included and not excluded]} of the doors with at
    least one counted voter."""
    doors = {}
    for street, stop, unit, party, key, lon, lat, score in rows:
        if inside is not None and stop not in inside:
            continue
        state = person(party, key, score)
        if state is None:
            continue
        door = doors.setdefault((stop, unit), [street, lon, lat, False, False, False])
        door[3] |= state[0]
        door[4] |= state[1]
        door[5] |= state[0] and not state[1]
    return doors


def count(store, polygon, include, exclude, support, codes):
    """The response body of POST /audience/streets (see the module docstring)."""
    person = rule(include, exclude, support, codes)
    west, south, east, north = polygon.bounds       # the bbox prefilter; the polygon decides per stop
    rows = _rows(store, 's.lon BETWEEN ? AND ? AND s.lat BETWEEN ? AND ?', [west, east, south, north]).fetchall()
    stops = {row[1]: (row[5], row[6]) for row in rows}
    inside = set()
    if stops:
        ids = np.fromiter(stops, dtype=np.int64, count=len(stops))
        shapely.prepare(polygon)
        covered = shapely.covers(polygon, shapely.points(np.array(list(stops.values()), dtype=np.float64)))
        inside = set(ids[covered].tolist())
    doors = _doors(rows, person, inside)
    sums = {}
    for street, lon, lat, included, excluded, kept in doors.values():
        row = sums.setdefault(street, [0, 0, 0, 0.0, 0.0])
        row[0] += 1
        row[1] += included and not excluded
        row[2] += kept and excluded             # a resident would still be taken but another one is excluded
        row[3] += lon
        row[4] += lat
    if len(sums) > MAX_STREETS:
        raise V.Refused(TOO_MANY)
    unplaced = 0                                    # doors on these streets that cannot be placed in or out of the area
    for chunk in W._chunks(sorted(sums)):
        rows = _rows(store, '(s.lon IS NULL OR s.lat IS NULL) AND s.street_id IN (%s)' % ','.join('?' * len(chunk)), chunk)
        unplaced += sum(1 for door in _doors(rows, person).values() if door[3] and not door[4])
    names = store.directory().by_id
    streets = []
    for street, (doors_in, matching, dropped, lon, lat) in sums.items():
        name = names.get(street)
        streets.append(((name.display, name.city) if name else ('', ''), street, {
            'id': str(street), 'label': name.display if name else '', 'center': [round(lon / doors_in, 6), round(lat / doors_in, 6)],
            'matching_doors': matching, 'voter_doors': doors_in, 'dropped_by_exclude': dropped}))
    streets = [row for *_, row in sorted(streets, key=lambda item: item[:2])]
    totals = {key: sum(s[key] for s in streets) for key in ('matching_doors', 'voter_doors', 'dropped_by_exclude')}
    return {'basis': 'voter-file', 'streets': streets, 'totals': {**totals, 'unplaced': unplaced},
            'support_coverage': bool(store.support)}


def _candidate_party():
    district = settings.DISTRICT
    return district['party'] if district.get('candidate') and district.get('party') in ('D', 'R') else None


def _codes():
    # ponytail: one pass over every recorded result per request; bounded by canvass volume, not by the voter file
    return {key: last['code'] for key, last in V.latest_codes(None).items()}


@V.read
def options(request):
    if request.GET:
        raise V.Refused('Unexpected query parameters.')
    with W.walk_store() as store:
        support = bool(store.attach_scores() and store.support)
        rows = store.conn.execute("SELECT IFNULL(NULLIF(p.party, ''), 'none'), p.voter_key FROM person p JOIN stop s ON s.id = p.stop_id").fetchall()
    codes = _codes()
    party, canvass = Counter(), Counter()
    for code, key in rows:
        result = codes.get(key, 'none')
        if result != MOVED:
            party[code] += 1
            canvass[result] += 1
    return response({
        'district': settings.DISTRICT['id'], 'candidate_party': _candidate_party(), 'support_coverage': support,
        'party': [{'code': c, 'label': FL.AUDIENCE_PARTY_LABELS[c], 'count': party[c]} for c in PARTIES],
        'canvass': [{'code': c, 'label': FL.AUDIENCE_CANVASS_LABELS[c], 'count': canvass[c]} for c in CANVASS]})


@V.write
def streets(request):
    try:
        body = A.json_request(request)
    except A.RequestProblem as problem:
        raise V.Refused(str(problem), problem.status) from None
    polygon, include, exclude, support = parse(body)
    codes = _codes()
    with W.walk_store() as store:
        return response(count(store, polygon, include, exclude, support, codes))
