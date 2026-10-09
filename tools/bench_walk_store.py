"""Time the walk store on real data, and check that a change to it returns the same rows in the same order.

    set CU_DATA_DIR=<a COPY of a district's stores>  (never the live .local: this holds handles for minutes)
    set CU_DISTRICT=or-05
    work\\.venv\\Scripts\\python.exe tools\\bench_walk_store.py [--precincts P1,P2] [--n 3] [--check]

For each precinct (default: the largest, one near 3,200 people and the median) it times span_plan, voters (the area
list), span_stats + span_parties (the overview's queries) and the plan, list and overview views end to end, in one
process: 'first' is the first call for that precinct (a new connection each time, as in the app, so SQLite's cache is
empty; the OS file cache is whatever it is), 'warm' the best of the next n. --check runs the same calls under OLD_AREA
(the area clause before the combo seek) and fails unless every result is equal. Prints counts and timings only: no
names or addresses.
"""
import platform; platform._wmi_query = lambda *a, **k: (_ for _ in ()).throw(OSError('wmi disabled'))  # noqa: E702 (see manage.py)
import argparse
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'server.settings')
import django  # noqa: E402

django.setup()
from django.conf import settings  # noqa: E402
from django.test import RequestFactory  # noqa: E402

from server.research import walk_store as W  # noqa: E402
from server.research import walk_views as V  # noqa: E402

NEW_AREA = W._area


def OLD_AREA(area, alias=''):
    prefix = alias + '.' if alias else ''
    given = [column for column in W.AREA_COLUMNS if area.get(column)]
    return ''.join(' AND %s%s=?' % (prefix, column) for column in given), [area[column] for column in given]


def store_calls(area):
    """{name: (seconds, result)} of the store methods, each on a new connection as a request opens it."""
    out = {}
    for name in ('span_plan', 'voters', 'overview_sql'):
        started = time.perf_counter()
        with W.walk_store() as store:
            if name == 'span_plan':
                result = store.attach_scores() and store.span_plan(area)
            elif name == 'voters':
                result = store.voters([], area, V.MAX_VOTERS + 1)
            else:
                result = (store.span_stats(area), store.span_parties(area))
        out[name] = (time.perf_counter() - started, result)
    return out


def view_calls(area):
    """The one-click path: overview, plan with a 60-door turf, then the list of that turf (fresh=1, doors=60). Without a
    street plan (Colorado) the list is of the first ten blocks of the overview."""
    factory, out = RequestFactory(), {}

    def call(name, view, query):
        request = factory.get('/', query, REMOTE_ADDR='127.0.0.1')
        request.local_admin_access, request.user = True, SimpleNamespace(is_authenticated=True)    # what the middleware sets
        started = time.perf_counter()
        response = view(request)
        body = json.loads(response.content)
        body.pop('generated_on', None)
        out[name] = (time.perf_counter() - started, (response.status_code, body))
        return body

    features = call('overview', V.overview, area).get('features', [])
    if settings.DISTRICT['roi'] is None:
        spans = [feature['properties']['span'] for feature in features[:10]]
        query = {**area, 'spans': ','.join(map(str, spans))}
    else:
        spans = call('plan', V.plan, {**area, 'doors': '60'}).get('turf', {}).get('spans', [])
        query = {**area, 'spans': ','.join(map(str, spans)), 'fresh': '1', 'doors': '60'}
    if spans:
        call('list', V.walk_list, query)
    return out


def plain(value):
    """Rows as tuples so results compare by value."""
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if hasattr(value, 'keys') and not isinstance(value, dict):
        return tuple(value)
    return value


def pick_precincts():
    with W.walk_store() as store:
        sizes = store.conn.execute('SELECT precinct, SUM(people) FROM combo GROUP BY precinct ORDER BY 2 DESC').fetchall()
    near = min(sizes, key=lambda row: abs(row[1] - 3200))
    return list(dict.fromkeys([sizes[0][0], near[0], sizes[len(sizes) // 2][0]]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--precincts')
    parser.add_argument('--n', type=int, default=3)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if 'cu-geography-v3' in str(settings.DATA_DIR) and '.local' in str(settings.DATA_DIR):
        sys.exit('Point CU_DATA_DIR at a copy of the stores, not the live .local.')
    print('district', settings.DISTRICT['id'], 'sqlite', W.sqlite3.sqlite_version)
    precincts = args.precincts.split(',') if args.precincts else pick_precincts()
    failed = False
    for precinct in precincts:
        area = {'precinct': precinct}
        timings, status = {}, {}
        for _ in range(1 + args.n):
            for name, (seconds, result) in {**store_calls(area), **view_calls(area)}.items():
                timings.setdefault(name, []).append(seconds)
                if name in ('plan', 'list', 'overview'):
                    status[name] = result[0]
        line = ' '.join('%s %.2f/%.2f' % (name, t[0], min(t[1:] or t)) for name, t in timings.items())
        line += '  http ' + ' '.join('%s=%d' % item for item in status.items())
        with W.walk_store() as store:
            people = store.count_voters([], area)
        print('precinct #%d people %d  first/warm s: %s' % (precincts.index(precinct) + 1, people, line), flush=True)
        if args.check:
            new = {name: plain(r) for name, (_, r) in {**store_calls(area), **view_calls(area)}.items()}
            W._area = OLD_AREA
            try:
                old = {name: plain(r) for name, (_, r) in {**store_calls(area), **view_calls(area)}.items()}
            finally:
                W._area = NEW_AREA
            differ = sorted(name for name in new if new[name] != old[name])
            failed |= bool(differ)
            print('  check old == new:', 'DIFFERS ' + ','.join(differ) if differ else 'equal (%s)' % ','.join(new))
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
