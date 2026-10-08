"""Synthetic checks for tools/co_baseline.py: the canonical store hash, normalisation and the difference report.

    <venv python> -m unittest tools.test_co_baseline          (from the V3 folder; no real data, no server, no port)
"""
import collections
import contextlib
import copy
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools import co_baseline as B


def make_store(path, rows, extra_column=False):
    connection = sqlite3.connect(path)
    connection.execute('CREATE TABLE person (pos INTEGER PRIMARY KEY, name TEXT, score REAL%s)'
                       % (', note TEXT' if extra_column else ''))
    connection.execute('CREATE TABLE loose (a TEXT, b INTEGER)')
    connection.execute('CREATE VIRTUAL TABLE box USING rtree(id, west, east)')
    for pos, name, score in rows:
        connection.execute('INSERT INTO person (pos, name, score) VALUES (?, ?, ?)', (pos, name, score))
    for a, b in (('x', 2), ('y', 1), ('x', 1)):
        connection.execute('INSERT INTO loose VALUES (?, ?)', (a, b))
    connection.execute('INSERT INTO box VALUES (1, 0.0, 1.0)')
    connection.commit()
    connection.close()


ROWS = [(1, 'a', 0.5), (2, 'b', 1.0), (3, 'c', None)]


class StoreDigestTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.dir = Path(self.folder.name)

    def digest(self, name, rows=ROWS, **kwargs):
        make_store(self.dir / name, rows, **kwargs)
        return B.store_digest(self.dir / name)

    def test_insertion_order_does_not_matter(self):
        first = self.digest('a.sqlite3')
        second = self.digest('b.sqlite3', rows=list(reversed(ROWS)))
        self.assertEqual(first, second)

    def test_a_changed_value_is_seen_in_its_table_only(self):
        first = self.digest('a.sqlite3')
        second = self.digest('b.sqlite3', rows=[(1, 'a', 0.5), (2, 'b', 1.0), (3, 'c', 2.0)])
        self.assertNotEqual(first['tables']['person']['sha256'], second['tables']['person']['sha256'])
        self.assertEqual(first['tables']['loose'], second['tables']['loose'])
        self.assertEqual(first['schema_sha256'], second['schema_sha256'])

    def test_int_and_float_stay_apart(self):
        digests = []
        for name, value in (('a.sqlite3', 1), ('b.sqlite3', 1.0)):
            connection = sqlite3.connect(self.dir / name)
            connection.execute('CREATE TABLE t (id INTEGER PRIMARY KEY, v)')       # no affinity: 1 and 1.0 are kept as given
            connection.execute('INSERT INTO t VALUES (1, ?)', (value,))
            connection.commit()
            connection.close()
            digests.append(B.store_digest(self.dir / name)['tables']['t']['sha256'])
        self.assertNotEqual(*digests)

    def test_a_schema_change_is_seen(self):
        first = self.digest('a.sqlite3')
        second = self.digest('b.sqlite3', extra_column=True)
        self.assertNotEqual(first['schema_sha256'], second['schema_sha256'])

    def test_counts_orders_and_virtual_flag(self):
        digest = self.digest('a.sqlite3')
        self.assertEqual(digest['tables']['person']['rows'], 3)
        self.assertEqual(digest['tables']['person']['order'], 'primary key')
        self.assertEqual(digest['tables']['loose']['order'], 'all columns')
        self.assertTrue(digest['tables']['box']['virtual'])
        self.assertEqual(digest['tables']['box']['order'], 'rowid')
        self.assertNotIn('virtual', digest['tables']['person'])

    def test_the_store_is_opened_read_only(self):
        make_store(self.dir / 'a.sqlite3', ROWS)
        connection = B.open_readonly(self.dir / 'a.sqlite3')
        with self.assertRaises(sqlite3.OperationalError):
            connection.execute('INSERT INTO loose VALUES (?, ?)', ('z', 9))
        connection.close()


class NormaliseTests(unittest.TestCase):
    def test_only_the_named_field_is_dropped(self):
        one = json.dumps({'generated_on': '2026-10-06', 'totals': {'doors': 3}}).encode()
        two = json.dumps({'totals': {'doors': 3}, 'generated_on': '2026-10-07'}).encode()
        first, second = (B.body_digest('walk_list_a', body, 'application/json') for body in (one, two))
        self.assertEqual(first, second)
        self.assertEqual(first[1], ['generated_on'])
        changed = json.dumps({'generated_on': '2026-10-07', 'totals': {'doors': 4}}).encode()
        self.assertNotEqual(first[0], B.body_digest('walk_list_a', changed, 'application/json')[0])

    def test_other_endpoints_are_hashed_as_they_are(self):
        one = json.dumps({'generated_on': 'x'}).encode()
        two = json.dumps({'generated_on': 'y'}).encode()
        self.assertNotEqual(B.body_digest('people_first_page', one, 'application/json')[0],
                            B.body_digest('people_first_page', two, 'application/json')[0])

    def test_differing_paths_name_no_values(self):
        left = json.dumps({'rows': [{'name': 'SECRET-A', 'rank': '1'}], 'total': 5}).encode()
        right = json.dumps({'rows': [{'name': 'SECRET-B', 'rank': '1'}], 'total': 5}).encode()
        found = B.json_paths_differing(left, right)
        self.assertEqual(found, ['.rows[0].name'])
        self.assertFalse(any('SECRET' in item for item in found))

    def test_first_string_finds_an_id(self):
        ident = 'p_' + 'a' * 64
        self.assertEqual(B.first_string({'rows': [{'x': 1}, {'person_id': ident}]}, r'p_[0-9a-f]{64}'), ident)
        self.assertIsNone(B.first_string({'rows': []}, r'p_[0-9a-f]{64}'))


class CompareTests(unittest.TestCase):
    def test_equal_sections_report_nothing(self):
        out = []
        B.compare({'a': {'b': 1}}, {'a': {'b': 1}}, 'x', out)
        self.assertEqual(out, [])

    def test_missing_new_and_different_are_named(self):
        out = []
        B.compare({'a': 1, 'b': 2, 'c': 3}, {'a': 1, 'b': 9, 'd': 4}, 'x', out)
        self.assertEqual(sorted(line.split()[0] for line in out), ['DIFFERENT', 'MISSING', 'NEW'])
        self.assertTrue(any('x.b' in line for line in out))

    def test_endpoint_difference_is_listed_with_its_fields(self):
        before = {'meta': {'status': 200, 'content_type': 'application/json', 'bytes': 10, 'sha256': 'a' * 64}}
        now = {'meta': {'status': 200, 'content_type': 'application/json', 'bytes': 10, 'sha256': 'b' * 64}}
        out = []
        B.compare_endpoints(before, now, {}, Path('does-not-exist'), out)
        self.assertEqual(len(out), 1)
        self.assertIn('endpoints.meta', out[0])
        self.assertIn('sha256', out[0])


def overview(*properties):
    return {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'geometry': {'type': 'LineString', 'coordinates': [[0, 0], [1, 1 + i]]}, 'properties': p}
        for i, p in enumerate(properties)]}


OLD = overview({'span': 1, 'street': 'A', 'doors': 5, 'd': 3, 'r': 2, 'u': 1, 'o': 4},
               {'span': 2, 'street': 'B', 'doors': 2, 'd': 0, 'r': 0, 'u': 0, 'o': 0})
NEW = overview({'span': 1, 'street': 'A', 'doors': 5, 'parties': {'D': 3, 'R': 2, 'U': 1, 'W': 1, 'G': 1, '': 2}},
               {'span': 2, 'street': 'B', 'doors': 2, 'parties': {}})


def dumps(body):
    return json.dumps(body).encode()


class IntendedTests(unittest.TestCase):
    """The ruled difference of walk_overview_precinct (O-10): the exact split passes, every other change fails."""

    def changed(self, edit):
        body = copy.deepcopy(NEW)
        edit(body)
        return B.overview_party_split(dumps(OLD), dumps(body))

    def test_the_exact_split_passes(self):
        self.assertEqual(B.overview_party_split(dumps(OLD), dumps(NEW)), [])

    def test_every_other_change_fails_and_names_a_path(self):
        feature = lambda body: body['features'][0]
        cases = {
            'a D count changed': lambda b: feature(b)['properties']['parties'].update(D=4),
            'a D count dropped': lambda b: feature(b)['properties']['parties'].pop('D'),
            'doors changed': lambda b: feature(b)['properties'].update(doors=6),
            'a geometry coordinate changed': lambda b: feature(b)['geometry']['coordinates'][0].__setitem__(0, 0.5),
            'an extra property': lambda b: feature(b)['properties'].update(extra=1),
            'an old count kept': lambda b: feature(b)['properties'].update(d=3),
            'the minor sum is off by one': lambda b: feature(b)['properties']['parties'].update(W=2),
            'a zero count': lambda b: feature(b)['properties']['parties'].update(W=0),
            'a code that is not a letter': lambda b: feature(b)['properties']['parties'].update({'1': 1}),
            'parties is missing': lambda b: feature(b)['properties'].pop('parties'),
            'an extra feature key': lambda b: feature(b).update(id=7),
        }
        for name, edit in cases.items():
            self.assertEqual(self.changed(edit), ['$.features[0]'], name)
        self.assertEqual(self.changed(lambda b: b['features'].pop()), ['$.features'], 'a feature dropped')
        self.assertEqual(self.changed(lambda b: b.update(extra=1)), ['$.features'], 'an extra top-level key')

    def compare(self, endpoint_id, now_body, golden=OLD, status=200):
        entry = lambda sha: {'status': 200, 'content_type': 'application/json', 'bytes': 10, 'sha256': sha * 64}
        now = {endpoint_id: {**entry('b'), 'status': status}}
        with tempfile.TemporaryDirectory() as folder:
            if golden is not None:
                (Path(folder) / (endpoint_id + '.body')).write_bytes(dumps(golden))
            out, printed = [], io.StringIO()
            with contextlib.redirect_stdout(printed):
                B.compare_endpoints({endpoint_id: entry('a')}, now, {endpoint_id: dumps(now_body)}, Path(folder), out)
        return out, printed.getvalue()

    def test_the_ruled_endpoint_passes_with_one_verified_line(self):
        out, printed = self.compare('walk_overview_precinct', NEW)
        self.assertEqual(out, [])
        self.assertEqual(printed.strip(), 'INTENDED endpoints.walk_overview_precinct (O-10 party codes as themselves): verified')

    def test_a_ruled_endpoint_still_fails_when_the_verifier_or_the_golden_body_or_the_status_does(self):
        broken = copy.deepcopy(NEW)
        broken['features'][0]['properties']['doors'] = 6
        out, printed = self.compare('walk_overview_precinct', broken)
        self.assertEqual((len(out), printed), (1, ''))
        self.assertIn('not met at $.features[0]', out[0])
        out, _ = self.compare('walk_overview_precinct', NEW, golden=None)
        self.assertIn('cannot verify a ruled difference without the golden body', out[0])
        out, printed = self.compare('walk_overview_precinct', NEW, status=500)
        self.assertEqual((len(out), printed), (1, ''))
        self.assertIn('status 200 -> 500', out[0])

    def test_an_unlisted_endpoint_that_differs_fails(self):
        out, printed = self.compare('walk_overview_other', NEW)
        self.assertEqual((len(out), printed), (1, ''))
        self.assertIn('DIFFERENT endpoints.walk_overview_other', out[0])
        self.assertNotIn('ruled', out[0])
        self.assertEqual(list(B.INTENDED), ['walk_overview_precinct'])


class SafetyTests(unittest.TestCase):
    def test_the_live_port_is_refused(self):
        with self.assertRaises(SystemExit):
            B.choose_port(B.LIVE_PORT)

    def test_a_busy_spare_port_is_skipped(self):
        with patch.object(B, 'listener_pid', side_effect=lambda port: 4242 if port == 8769 else None):
            self.assertEqual(B.choose_port(0), 8771)

    def test_no_endpoint_touches_the_live_port_or_writes(self):
        self.assertTrue(all(template.startswith('/') for _, template in B.ENDPOINTS))
        self.assertEqual({entry[0] for entry in B.ENDPOINTS}.__len__(), len(B.ENDPOINTS))
        self.assertNotIn(str(B.LIVE_PORT), ''.join(template for _, template in B.ENDPOINTS))

    def test_a_start_that_dies_is_retried_three_times_and_noted(self):
        class Flaky:
            starts = 0

            def __init__(self, fail):
                self.fail = fail

            def start(self):
                Flaky.starts += 1
                if self.fail:
                    raise B.StartFailed('exited early with code -1073740022')

            def stop(self):
                pass

        B.STARTUP_RETRIES.clear()
        with patch.object(B.time, 'sleep'):
            server = B.start_with_retry(lambda attempt: Flaky(attempt < 3))
        self.assertEqual((Flaky.starts, len(B.STARTUP_RETRIES)), (3, 2))
        with patch.object(B.time, 'sleep'), self.assertRaises(SystemExit):
            B.start_with_retry(lambda attempt: Flaky(True))
        B.STARTUP_RETRIES.clear()

    def test_a_5xx_is_a_server_fault_not_a_recorded_answer(self):
        with patch.object(B, 'fetch', return_value=(500, 'application/json', b'{"error":"x"}')),                 patch.object(B, 'log'):
            with self.assertRaises(B.ServerFault):
                B.endpoint_pass(1)

    def test_the_server_refuses_the_live_port(self):
        with self.assertRaises(SystemExit):
            B.Server('python', B.LIVE_PORT, Path('.'), 't').start()


# ---- the unmask check (nothing withheld): synthetic before / after payloads -------------------------------------------------

IDS = ['101', '102', '103', '104']       # shown before, shown before, withheld before and now shown, no registered people
OLD_KEY, NEW_KEY = 'co-cd005-' + 'a' * 12, 'co-cd005-' + 'b' * 12
OLD_SHA, NEW_SHA = 'a' * 64, 'b' * 64
BUILT = '2026-10-08T01:00:00+00:00'


def shown_row(n, households, mean, pct, top10, top10_pct):
    return {'n_rows': n, 'n_households': households, 'eb_mean': mean, 'eb_se': 2.0, 'measured_pct_mean': 90.0, 'area_pct': pct,
            'top10_share_eb': top10, 'top10_eb_se': 0.01, 'top10_area_pct': top10_pct, 'share_reg_D': 0.25,
            'share_reg_R': 0.25, 'share_reg_U': 0.5}


def block_payload(key, status, rows, standings, precinct):
    """A registration/block areas payload: rows are shown-table rows (None: no values), standings (pct, top10_pct) or None."""
    pick = lambda f: [None if r is None else f(r) for r in rows]          # noqa: E731
    return {'schema': 'v3-areas-1', 'snapshot': key, 'basis': 'registration', 'level': 'block', 'ids': IDS,
            'labels': ['Block ' + i for i in IDS], 'status': status,
            'n': pick(lambda r: r['n_rows']), 'households': pick(lambda r: r['n_households']), 'parent': ['08'] * 4,
            'precinct': precinct, 'tract': [None] * 4, 'blocks': [None] * 4,
            'groups': {
                'ovr': {'mean': pick(lambda r: r['eb_mean']), 'se': pick(lambda r: r['eb_se']),
                        'measured': pick(lambda r: r['measured_pct_mean']), 'pct': [s and s[0] for s in standings],
                        'top10': pick(lambda r: r['top10_share_eb']), 'top10_se': pick(lambda r: r['top10_eb_se']),
                        'top10_pct': [s and s[1] for s in standings]},
                'lean': {'mean': pick(lambda r: r['eb_mean']), 'se': pick(lambda r: r['eb_se']),
                         'measured': pick(lambda r: r['measured_pct_mean']), 'share_d': pick(lambda r: r['share_reg_D']),
                         'share_r': pick(lambda r: r['share_reg_R']), 'share_u': pick(lambda r: r['share_reg_U'])}}}


def world():
    """(old payload, new payload, ctx): areas 101 and 102 were shown, 103 was withheld, 104 has no registered people."""
    r101, r102, r103 = shown_row(30, 20, 60.0, 83.5, 0.20, 83.5), shown_row(50, 30, 40.0, 16.5, 0.10, 16.5), shown_row(3, 2, 50.0, 50.0, 0.15, 50.0)
    old = block_payload(OLD_KEY, ['ok', 'ok', 'withheld', 'withheld'], [r101, r102, None, None], [(75.0, 75.0), (25.0, 25.0), None, None],
                        ['p1', 'p2', None, None])
    new = block_payload(NEW_KEY, ['ok', 'ok', 'ok', 'empty'], [r101, r102, r103, None], [(83.5, 83.5), (16.5, 16.5), (50.0, 50.0), None],
                        ['p1', 'p2', 'p1', None])
    new['n'][3], new['households'][3] = 0, 0
    shown = {('registration', 'block', i): {'ovr': r, 'lean': r} for i, r in zip(IDS, (r101, r102, r103))}
    ctx = SimpleNamespace(old_key=OLD_KEY, new_key=NEW_KEY, old_sha=OLD_SHA, shown_sha=NEW_SHA, shown=shown, new_built=BUILT,
                          stats=collections.Counter(), problems=[], level_areas={'registration/block': 3})
    return old, new, ctx


class UnmaskAreasTests(unittest.TestCase):
    def setUp(self):
        self.old, self.new, self.ctx = world()

    def verdict(self, edit=lambda payload: None):
        new = copy.deepcopy(self.new)
        edit(new)
        return B.verify_areas(dumps(self.old), dumps(new), self.ctx)

    def test_the_accepted_case_and_its_counts(self):
        self.assertEqual(self.verdict(), [])
        self.assertEqual((self.ctx.stats['newly_shown'], self.ctx.stats['empty'], self.ctx.stats['moved']), (1, 1, 4))

    def test_a_shown_areas_value_that_changed_fails(self):
        self.assertEqual(self.verdict(lambda p: p['groups']['ovr']['mean'].__setitem__(0, 60.001)), ['$.groups.ovr.mean[0]'])
        self.assertEqual(self.verdict(lambda p: p['n'].__setitem__(0, 31)), ['$.n[0]'])
        self.assertEqual(self.verdict(lambda p: p['precinct'].__setitem__(1, 'p9')), ['$.precinct[1]'])
        self.assertEqual(self.verdict(lambda p: p['labels'].__setitem__(0, 'x')), ['$.labels'])
        self.ctx.shown[('registration', 'block', '101')]['ovr']['eb_mean'] = 60.5      # the table moved too: still not allowed
        both = lambda p: [p['groups'][g]['mean'].__setitem__(0, 60.5) for g in ('ovr', 'lean')]      # noqa: E731
        self.assertEqual(self.verdict(both), ['$.groups.ovr.mean[0]', '$.groups.lean.mean[0]'])

    def test_a_standing_that_disagrees_with_the_table_fails(self):
        self.assertEqual(self.verdict(lambda p: p['groups']['ovr']['pct'].__setitem__(0, 90.0)), ['$.groups.ovr.pct[0]'])
        self.assertEqual(self.verdict(lambda p: p['groups']['ovr']['top10_pct'].__setitem__(2, 51.0)), ['$.groups.ovr.top10_pct[2]'])
        self.assertEqual(self.verdict(lambda p: p['groups']['ovr']['pct'].__setitem__(0, 83.5 + 5e-9)), [])    # within the wire rounding

    def test_a_new_value_that_is_not_the_tables_fails(self):
        self.assertEqual(self.verdict(lambda p: p['groups']['lean']['share_d'].__setitem__(2, 0.3)), ['$.groups.lean.share_d[2]'])
        self.assertEqual(self.verdict(lambda p: p['groups']['ovr']['se'].__setitem__(2, None)), ['$.groups.ovr.se[2]'])
        self.assertEqual(self.verdict(lambda p: p['households'].__setitem__(2, 9)), ['$.households[2]'])

    def test_withheld_status_and_wrong_empties_fail(self):
        self.assertEqual(self.verdict(lambda p: p['status'].__setitem__(2, 'withheld')), ['$.status[2]'])
        self.assertEqual(self.verdict(lambda p: p['status'].__setitem__(3, 'withheld')), ['$.status[3]'])
        self.assertEqual(self.verdict(lambda p: p['n'].__setitem__(3, 5)), ['$.status[3]'])
        self.assertEqual(self.verdict(lambda p: p['status'].__setitem__(3, 'ok')), ['$.status[3]'])        # no row in the table
        self.assertEqual(self.verdict(lambda p: p['status'].__setitem__(0, 'empty')), ['$.status[0]'])

    def test_structure_and_missing_areas_fail(self):
        self.assertEqual(self.verdict(lambda p: p.update(extra=1)), ['$'])
        self.assertEqual(self.verdict(lambda p: p['n'].pop()), ['$.n[len]'])
        self.assertEqual(self.verdict(lambda p: p.update(snapshot=OLD_KEY)), ['$.snapshot'])
        self.ctx.shown[('registration', 'block', '105')] = {'ovr': shown_row(9, 5, 1.0, 1.0, 0.1, 1.0)}
        self.assertEqual(self.verdict(), ['$.ids'])
        del self.ctx.shown[('registration', 'block', '105')]
        self.ctx.shown[('registration', 'block', 'nan')] = {'ovr': shown_row(9, 5, 1.0, 1.0, 0.1, 1.0)}   # the pseudo-area is not drawn
        self.assertEqual(self.verdict(), [])


def meta_pair():
    base = {'schema': 'v3-meta-1', 'weights': {'cua': 40}, 'people': 10,
            'row_framework': {'version': 'rowfw-1.0.0', 'framework_config_sha256': 'd' * 64}}
    old = {**copy.deepcopy(base), 'snapshot': OLD_KEY, 'floors': {'min_n': 20, 'min_households': 10},
           'geometry_method': {'concordance': {'zips_without_zcta': 2, 'withheld_without_polygon': 6}}}
    old['row_framework'].update(area_table_sha256=OLD_SHA, suppressed_ids_sha256='c' * 64)
    new = {**copy.deepcopy(base), 'snapshot': NEW_KEY, 'small_area': {'min_n': 20, 'min_households': 10},
           'geometry_method': {'concordance': {'zips_without_zcta': 2, 'without_polygon': 6}}}
    new['row_framework'].update(area_table_sha256=NEW_SHA)
    return old, new


class UnmaskSmallBodiesTests(unittest.TestCase):
    def setUp(self):
        _old, _new, self.ctx = world()

    def test_meta(self):
        old, new = meta_pair()
        self.assertEqual(B.verify_meta(dumps(old), dumps(new), self.ctx), [])
        for name, edit in {'floors kept': lambda m: m.update(floors={'min_n': 20, 'min_households': 10}),
                           'floors changed': lambda m: m['small_area'].update(min_n=5),
                           'withheld sha kept': lambda m: m['row_framework'].update(suppressed_ids_sha256='c' * 64),
                           'old area sha': lambda m: m['row_framework'].update(area_table_sha256=OLD_SHA),
                           'concordance renamed back': lambda m: m['geometry_method']['concordance'].update(withheld_without_polygon=6),
                           'something else moved': lambda m: m.update(people=11)}.items():
            _o, bad = meta_pair()
            edit(bad)
            self.assertNotEqual(B.verify_meta(dumps(old), dumps(bad), self.ctx), [], name)

    def profile(self):
        ref = lambda level, ident, pct, top10, n, h: {'level': level, 'id': ident, 'label': level + ' ' + ident, 'pct': pct,    # noqa: E731
                                                       'top10': top10, 'n': n, 'households': h}
        old = {'person_id': 'p_x', 'cards': [1, 2], 'area_context': {
            'block': {'level': 'block', 'id': '101', 'label': 'block 101', 'pct': 75.0, 'top10': 0.2, 'withheld': False},
            'precinct': {'level': 'precinct', 'id': '9', 'label': 'precinct 9', 'pct': None, 'top10': None, 'withheld': True}}}
        new = {'person_id': 'p_x', 'cards': [1, 2], 'area_context': {
            'block': ref('block', '101', 83.5, 0.2, 30, 20), 'precinct': ref('precinct', '9', 40.0, 0.12, 4, 3)}}
        self.ctx.shown[('registration', 'precinct', '9')] = {'ovr': shown_row(4, 3, 45.0, 45.0, 0.12, 40.0)}
        return old, new

    def test_profile(self):
        old, new = self.profile()
        self.assertEqual(B.verify_profile(dumps(old), dumps(new), self.ctx), [])
        for name, edit in {'the old pct kept': lambda p: p['area_context']['block'].update(pct=75.0),
                           'top10 of a shown area moved': lambda p: p['area_context']['block'].update(top10=0.21),
                           'top10 of a new area not the table\'s': lambda p: p['area_context']['precinct'].update(top10=0.5),
                           'n wrong': lambda p: p['area_context']['precinct'].update(n=5),
                           'withheld key kept': lambda p: p['area_context']['block'].update(withheld=False),
                           'another field changed': lambda p: p.update(cards=[1]),
                           'an area still blank': lambda p: p['area_context']['precinct'].update(pct=None)}.items():
            bad = copy.deepcopy(new)
            edit(bad)
            self.assertNotEqual(B.verify_profile(dumps(old), dumps(bad), self.ctx), [], name)

    def test_workspace(self):
        areas = lambda text: {'key': 'areas', 'origin': 'Active stored area table', 'detail': text}      # noqa: E731
        old = {'mode': 'local-source', 'snapshot': OLD_KEY, 'builtAt': 'before', 'availableSnapshots': 1, 'people': 5,
               'layers': [{'key': 'people', 'origin': 'x', 'detail': 'y'}, areas('Withheld areas retain their stored availability status.')]}
        new = {**copy.deepcopy(old), 'snapshot': NEW_KEY, 'builtAt': BUILT,
               'layers': [{'key': 'people', 'origin': 'x', 'detail': 'y'}, areas('Every area is shown, small ones with a note.')]}
        self.assertEqual(B.verify_workspace(dumps(old), dumps(new), self.ctx), [])
        for name, edit in {'old text': lambda w: w['layers'][1].update(detail='Withheld areas stay.'),
                           'built time not the store\'s': lambda w: w.update(builtAt='other'),
                           'old snapshot': lambda w: w.update(snapshot=OLD_KEY),
                           'another field': lambda w: w.update(people=6),
                           'another layer': lambda w: w['layers'][0].update(detail='z')}.items():
            bad = copy.deepcopy(new)
            edit(bad)
            self.assertNotEqual(B.verify_workspace(dumps(old), dumps(bad), self.ctx), [], name)

    def test_method_snapshots(self):
        def manifest(key, sha, **more):
            return {'schema': 'cu-summary-v4', 'snapshot': key, 'created_at': 'Built from the score store written ' + key,
                    'threshold': 20, 'privacy': 'small cells suppressed', 'measure_methodology': 'counts below 20 are suppressed',
                    'bases': {'registration': {'imported': 9}},
                    'row_framework': {'version': 'rowfw-1.0.0', 'base_snapshot': 'co-cd005-' + 'c' * 12, 'area_table_sha256': sha,
                                      'area_indices_sha256': ('e' if key == OLD_KEY else 'f') * 64}, **more}
        older = {'key': 'co-cd005-' + 'c' * 12, 'active': False, 'manifest': {'schema': 'cu-summary-v3', 'snapshot': 'co-cd005-' + 'c' * 12}}
        old_manifest = manifest(OLD_KEY, OLD_SHA, area_floors={'min_n': 20, 'min_households': 10},
                                geometry_method={'concordance': {'withheld_without_polygon': 6}})
        old_manifest['row_framework'].update(suppressed_ids_sha256='c' * 64, withheld_set_sha256='d' * 64, d1_audit={'exactly_disclosed': 0})
        old = {'snapshots': [{'key': OLD_KEY, 'active': True, 'manifest': old_manifest}, older]}
        new_manifest = manifest(NEW_KEY, NEW_SHA, geometry_method={'concordance': {'without_polygon': 6}})
        new_manifest.update(privacy='every area shown', threshold=0)
        new_manifest['row_framework']['base_snapshot'] = 'co-cd005-' + '9' * 12
        new = {'snapshots': [{'key': NEW_KEY, 'active': True, 'manifest': new_manifest}, {**old['snapshots'][0], 'active': False}, older]}
        self.assertEqual(B.verify_snapshots(dumps(old), dumps(new), self.ctx), [])
        for name, edit in {
                'two active': lambda s: s['snapshots'][1].update(active=True),
                'floors kept': lambda s: s['snapshots'][0]['manifest'].update(area_floors={}),
                'an old entry changed': lambda s: s['snapshots'][2]['manifest'].update(schema='x'),
                'withheld sha kept': lambda s: s['snapshots'][0]['manifest']['row_framework'].update(suppressed_ids_sha256='c' * 64),
                'old area sha': lambda s: s['snapshots'][0]['manifest']['row_framework'].update(area_table_sha256=OLD_SHA),
                'a manifest field changed': lambda s: s['snapshots'][0]['manifest']['bases']['registration'].update(imported=8),
                'entry missing': lambda s: s['snapshots'].pop(2)}.items():
            bad = copy.deepcopy(new)
            edit(bad)
            self.assertNotEqual(B.verify_snapshots(dumps(old), dumps(bad), self.ctx), [], name)

    def test_summaries_json_and_csv(self):
        def body(key, rows):
            return {'meta': {'snapshot': key, 'metric': 'count', 'threshold': 20, 'coverage': 0.9, 'denominator': 100, 'measure_methodology': key},
                    'rows': rows}
        row = lambda ident, count, index, status='ok': {'id': ident, 'count': count, 'status': status, 'share': count and count / 100,    # noqa: E731
                                                          'value': count, 'index': index, 'measure_status': status}
        old = body(OLD_KEY, [row('Z1', 100, 75.0), row('Z2', 50, 25.0), row('Z3', None, None, 'suppressed')])
        new = body(NEW_KEY, [row('Z1', 100, 100 * 2.5 / 3), row('Z2', 50, 50.0), row('Z3', 10, 100 * 0.5 / 3)])
        new['meta'].update(threshold=0, coverage=1.0)
        self.assertEqual(B.verify_summaries(dumps(old), dumps(new), self.ctx), [])
        self.assertEqual(self.ctx.stats['summary_rows'], 1)
        for name, edit in {'still suppressed': lambda s: s['rows'].__setitem__(2, row('Z3', None, None, 'suppressed')),
                           'a count changed': lambda s: s['rows'][0].update(count=101, value=101),
                           'a share changed': lambda s: s['rows'][1].update(share=0.4),
                           'index not the midrank': lambda s: s['rows'][0].update(index=99.0),
                           'rows reordered': lambda s: s['rows'].reverse(),
                           'meta changed': lambda s: s['meta'].update(metric='other')}.items():
            bad = copy.deepcopy(new)
            edit(bad)
            self.assertNotEqual(B.verify_summaries(dumps(old), dumps(bad), self.ctx), [], name)
        head = 'snapshot,geographic_id,count,status,measure_status,value,index\n'
        csv_old = (head + '%s,Z1,100,ok,ok,60.5,70.0\n%s,Z3,,suppressed,suppressed,,\n' % (OLD_KEY, OLD_KEY)).encode()
        csv_new = (head + '%s,Z1,100,ok,ok,61.5,72.0\n%s,Z3,10,ok,ok,40.0,10.0\n' % (NEW_KEY, NEW_KEY)).encode()
        self.assertEqual(B.verify_summaries(csv_old, csv_new, self.ctx), [])
        self.assertEqual(B.verify_summaries(csv_old, csv_new.replace(b'Z1,100', b'Z1,101'), self.ctx), ['$.rows[0]'])

    def test_every_other_endpoint_must_be_identical_once_the_keys_are_replaced(self):
        old = dumps({'snapshot': OLD_KEY, 'area': OLD_SHA, 'people': 5})
        self.assertEqual(B.verify_keys_only(old, dumps({'snapshot': NEW_KEY, 'area': NEW_SHA, 'people': 5}), self.ctx), [])
        self.assertEqual(B.verify_keys_only(old, dumps({'snapshot': NEW_KEY, 'area': NEW_SHA, 'people': 6}), self.ctx), ['.people'])
        self.assertEqual(B.verify_keys_only(b'a,b\n1,2\n', b'a,b\n1,3\n', self.ctx), ['(not JSON: 8 bytes vs 8 bytes)'])
        self.assertEqual(B.verify_keys_only(b'a,b\n1,2\n', b'a,b\n1,2\n', self.ctx), [])

    def test_a_verifier_that_meets_an_unexpected_shape_reports_instead_of_crashing(self):
        rules = B.unmask_rules(self.ctx)
        self.assertEqual(rules['meta'][1](dumps({}), dumps({})), ['$ (unexpected shape: KeyError)'])
        self.assertEqual(set(rules), {endpoint_id for endpoint_id, _ in B.ENDPOINTS})


def metadata_store(path, pairs):
    connection = sqlite3.connect(path)
    connection.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value)')
    connection.executemany('INSERT INTO metadata VALUES (?, ?)', pairs)
    connection.commit()
    connection.close()
    return Path(path)


class UnmaskStoresTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.dir = Path(self.folder.name)
        _old, _new, self.ctx = world()

    OLD = {'n_people': 10, 'built_utc': '"before"', 'area_table_sha256': '"%s"' % OLD_SHA, 'suppressed_ids_sha256': '"%s"' % ('c' * 64),
           'area_table': json.dumps({'area_table_sha256': OLD_SHA, 'levels': {'registration/block': {'published': 2, 'withheld': 1}}}),
           'build_code_sha256': '"x"', 'w1': json.dumps({'matches': 5, 'seconds': 17.2}), 'inputs': '{"a": 1}'}

    def new(self, **edit):
        new = {k: v for k, v in self.OLD.items() if k != 'suppressed_ids_sha256'}
        new.update(built_utc='"%s"' % BUILT, area_table_sha256='"%s"' % NEW_SHA, build_code_sha256='"y"',
                   area_table=json.dumps({'area_table_sha256': NEW_SHA, 'levels': {'registration/block': {'areas': 3}}, 'manifest_sha256': 'e' * 64}),
                   w1=json.dumps({'matches': 5, 'seconds': 16.9}))
        new.update(edit)
        return {k: v for k, v in new.items() if v is not None}

    def test_only_the_area_table_keys_the_build_time_and_the_timing_may_change(self):
        with patch.object(B, 'SHOWN', self.dir / 'none.csv'):
            self.assertEqual(B.metadata_problems(self.OLD, self.new(), self.ctx), [])
            self.assertEqual(B.metadata_problems(self.OLD, self.new(n_people=11), self.ctx), ['scores.metadata.n_people changed'])
            self.assertEqual(B.metadata_problems(self.OLD, self.new(inputs='{"a": 2}'), self.ctx), ['scores.metadata.inputs changed'])
            self.assertEqual(B.metadata_problems(self.OLD, self.new(w1=json.dumps({'matches': 6, 'seconds': 1.0})), self.ctx),
                             ['scores.metadata.w1 changed'])
            self.assertIn('scores.metadata.suppressed_ids_sha256 is still there',
                          B.metadata_problems(self.OLD, self.new(suppressed_ids_sha256='"x"'), self.ctx))
            self.assertIn('scores.metadata.area_table_sha256 is not the shown table',
                          B.metadata_problems(self.OLD, self.new(area_table_sha256='"%s"' % OLD_SHA), self.ctx))
            levels = json.dumps({'area_table_sha256': NEW_SHA, 'levels': {'registration/block': {'areas': 4}}})
            self.assertIn('scores.metadata.area_table.levels differ from the shown table',
                          B.metadata_problems(self.OLD, self.new(area_table=levels), self.ctx))

    def test_the_manifest_sha_is_checked_when_the_shown_table_has_one(self):
        shown = self.dir / 'shown.csv'
        shown.write_text('x', encoding='utf-8')
        shown.with_suffix('.manifest.json').write_text('{}', encoding='utf-8')
        with patch.object(B, 'SHOWN', shown):
            self.assertEqual(len(B.metadata_problems(self.OLD, self.new(), self.ctx)), 1)
            good = json.dumps({'area_table_sha256': NEW_SHA, 'levels': {'registration/block': {'areas': 3}},
                               'manifest_sha256': B.sha256_file(shown.with_suffix('.manifest.json'))})
            self.assertEqual(B.metadata_problems(self.OLD, self.new(area_table=good), self.ctx), [])

    def test_rows_digest_is_the_table_digest_and_the_dump_is_checked(self):
        pairs = sorted(self.OLD.items())
        path = metadata_store(self.dir / 'old.sqlite3', pairs)
        connection = B.open_readonly(path)
        digest = B.table_digest(connection, 'metadata')
        connection.close()
        self.assertEqual(B.rows_digest(pairs), digest['sha256'])
        fixture = {'stores': {'scores': {'tables': {'metadata': digest}}}}
        dump = self.dir / 'dump.json'
        dump.write_text(json.dumps({'digest': digest, 'rows': [list(p) for p in pairs]}), encoding='utf-8')
        with patch.object(B, 'PRE_FILE', dump):
            self.assertEqual(B.old_metadata(fixture), dict(pairs))
            dump.write_text(json.dumps({'digest': digest, 'rows': [list(p) for p in pairs[1:]]}), encoding='utf-8')
            with self.assertRaises(SystemExit):
                B.old_metadata(fixture)
        with patch.object(B, 'PRE_FILE', self.dir / 'none.json'), patch.object(B, 'LOCAL', self.dir):
            (self.dir / 'scores.sqlite3').write_bytes(path.read_bytes())          # no dump: the live store, while it is the recorded one
            self.assertEqual(B.old_metadata(fixture), dict(pairs))
            (self.dir / 'scores.sqlite3').unlink()
            metadata_store(self.dir / 'scores.sqlite3', pairs[1:])
            with self.assertRaises(SystemExit):
                B.old_metadata(fixture)

    def test_shown_table_reader_keeps_ids_as_text_and_empty_cells_as_none(self):
        path = self.dir / 'shown.csv'
        path.write_text('basis,level,area_id,group,parent_id,n_rows,eb_mean,area_pct\n'
                        'registration,block,00101,ovr,001,3.0,50.5,\n'
                        'registration,block,nan,lean,,1.0,,12.5\n', encoding='utf-8')
        shown = B.read_shown(path)
        self.assertEqual(shown[('registration', 'block', '00101')]['ovr'], {'n_rows': 3.0, 'eb_mean': 50.5, 'area_pct': None})
        self.assertEqual(shown[('registration', 'block', 'nan')]['lean'], {'n_rows': 1.0, 'eb_mean': None, 'area_pct': 12.5})


class UnmaskGateTests(unittest.TestCase):
    """The pieces in one pass: generic lines, endpoint comparison, context and the argument rules."""

    def setUp(self):
        _old, _new, self.ctx = world()

    def test_only_the_lines_the_unmask_explains_are_dropped(self):
        explained = ['DIFFERENT .snapshot: recorded x, now y', 'NEW       files.shown', 'NEW       files.snapshots.' + NEW_KEY,
                     'DIFFERENT stores.scores.tables.metadata.sha256: recorded a, now b',
                     'DIFFERENT stores.scores.tables.metadata.rows: recorded 22, now 21',
                     'DIFFERENT stores.accounts.tables.research_snapshot.rows: recorded 2, now 3',
                     'DIFFERENT stores.accounts.tables.research_auditevent.sha256: recorded a, now b',
                     'DIFFERENT stores.accounts.tables.sqlite_sequence.sha256: recorded a, now b']
        unexplained = ['DIFFERENT stores.scores.tables.person.sha256: recorded a, now b',
                       'DIFFERENT stores.scores.schema_sha256: recorded a, now b',
                       'DIFFERENT stores.accounts.tables.research_walklist.rows: recorded 10, now 9',
                       'NEW       files.snapshots.co-cd005-' + 'f' * 12,
                       'MISSING   files.snapshots.' + OLD_KEY, 'DIFFERENT files.rowfw.framework_config.json: recorded a, now b',
                       'DIFFERENT files.snapshots.%s.core.rows.json: recorded a, now b' % OLD_KEY,
                       'DIFFERENT endpoints.people_first_page: sha256 aaa -> bbb']
        self.assertEqual(B.unmask_unexplained(explained + unexplained, self.ctx), unexplained)

    def endpoint_entry(self, sha):
        return {'status': 200, 'content_type': 'application/json', 'bytes': 10, 'sha256': sha * 64}

    def compare(self, golden, now, endpoint_id):
        rules = {**B.unmask_rules(self.ctx), **B.INTENDED}
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / (endpoint_id + '.body')).write_bytes(golden)
            out, printed = [], io.StringIO()
            with contextlib.redirect_stdout(printed):
                B.compare_endpoints({endpoint_id: self.endpoint_entry('a')}, {endpoint_id: self.endpoint_entry('b')},
                                    {endpoint_id: now}, Path(folder), out, rules)
        return out, printed.getvalue()

    def test_a_new_endpoint_difference_fails_and_a_key_only_one_passes(self):
        out, printed = self.compare(dumps({'snapshot': OLD_KEY, 'rows': [1]}), dumps({'snapshot': NEW_KEY, 'rows': [1]}), 'health_terrier')
        self.assertEqual(out, [])
        self.assertIn('INTENDED endpoints.health_terrier (nothing withheld, Paul 2026-10-08): verified', printed)
        out, printed = self.compare(dumps({'rows': [1]}), dumps({'rows': [2]}), 'people_first_page')
        self.assertEqual(printed, '')
        self.assertIn('DIFFERENT endpoints.people_first_page', out[0])
        self.assertIn('not met at .rows[0]', out[0])
        out, _ = self.compare(dumps({'rows': [1]}), dumps({'rows': [2]}), 'walk_overview_precinct')       # O-10 keeps its own rule
        self.assertIn('"O-10 party codes as themselves" not met', out[0])

    def test_the_areas_rule_runs_through_compare_endpoints(self):
        old, new, _ctx = world()
        out, printed = self.compare(dumps(old), dumps(new), 'areas_registration_block')
        self.assertEqual(out, [])
        new['groups']['ovr']['mean'][0] = 61.0
        out, _ = self.compare(dumps(old), dumps(new), 'areas_registration_block')
        self.assertIn('not met at $.groups.ovr.mean[0]', out[0])

    def test_context_collects_what_already_fails(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        shown = root / 'shown.csv'
        shown.write_text('basis,level,area_id,group,n_rows\nregistration,block,101,ovr,3.0\n', encoding='utf-8')
        sha = B.sha256_file(shown)
        golden = root / 'golden'
        golden.mkdir()
        (golden / 'meta.body').write_bytes(b'{"old": 1}')
        entry = {'status': 200, 'content_type': 'application/json', 'bytes': 10,
                 'sha256': B.body_digest('meta', b'{"old": 1}', 'application/json')[0]}
        old_pairs = sorted(UnmaskStoresTests.OLD.items())
        old_store = metadata_store(root / 'old.sqlite3', old_pairs)
        connection = B.open_readonly(old_store)
        digest = B.table_digest(connection, 'metadata')
        connection.close()
        fixture = {'snapshot': OLD_KEY, 'files': {'rowfw': {'area_scores_rowfw-1.0.0.csv': OLD_SHA}},
                   'endpoints': {'meta': entry}, 'stores': {'scores': {'tables': {'metadata': digest}}}}
        new_pairs = sorted({**UnmaskStoresTests.OLD, 'built_utc': '"%s"' % BUILT, 'area_table_sha256': '"%s"' % sha,
                            'area_table': json.dumps({'area_table_sha256': sha, 'levels': {'registration/block': {'areas': 1}}})}.items())
        new_pairs = [p for p in new_pairs if p[0] != 'suppressed_ids_sha256']
        stage = root / 'stage'
        stage.mkdir()
        metadata_store(stage / 'scores.sqlite3', new_pairs)
        bodies = {'meta': dumps({'row_framework': {'area_table_sha256': sha}})}
        dump = root / 'dump.json'
        dump.write_text(json.dumps({'digest': digest, 'rows': [list(p) for p in old_pairs]}), encoding='utf-8')
        with patch.object(B, 'PRE_FILE', dump), patch.object(B, 'SHOWN', root / 'x.csv'):
            ctx = B.unmask_context(fixture, {'snapshot': NEW_KEY}, bodies, stage, golden, shown)
            self.assertEqual((ctx.problems, ctx.new_built, ctx.shown_sha), ([], BUILT, sha))
            self.assertEqual(ctx.level_areas, {'registration/block': 1})
        # the context also names a snapshot that did not change, a meta that names another table, a stale golden body
        (golden / 'meta.body').write_bytes(b'{"old": 2}')
        with patch.object(B, 'PRE_FILE', dump), patch.object(B, 'SHOWN', root / 'x.csv'):
            ctx = B.unmask_context(fixture, {'snapshot': OLD_KEY}, {'meta': dumps({'row_framework': {'area_table_sha256': OLD_SHA}})}, stage, golden, shown)
        self.assertEqual(sorted(ctx.problems), sorted(['the snapshot key did not change', 'the new meta does not name the shown table',
                                                       'golden body meta is not the recorded state']))
        with self.assertRaises(SystemExit):
            B.unmask_context(fixture, {'snapshot': NEW_KEY}, bodies, stage, golden, root / 'missing.csv')

    def test_nothing_newly_shown_is_a_failure_and_the_arguments_are_checked(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(B.unmask_final(self.ctx), ['no previously withheld area carries a value'])
            self.ctx.stats['newly_shown'] = 1
            self.ctx.problems.append('x')
            self.assertEqual(B.unmask_final(self.ctx), ['x'])
        base = dict(parts='stores,files,api', runs=2, intended='unmask', stage_dir=None)
        for edit in ({'parts': 'stores'}, {'stage_dir': 'x', 'intended': None}):
            with self.assertRaises(SystemExit):
                B.run('check', SimpleNamespace(**{**base, **edit}))
        with self.assertRaises(SystemExit):
            B.run('record', SimpleNamespace(**base))


if __name__ == '__main__':
    unittest.main()
