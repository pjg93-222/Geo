"""Phase 3b Lane S: the bundle validator, the meta answer, the area filter and the level catalog follow the served district
(settings.DISTRICT). The Colorado fixture world is renamed into an Oregon one (precinct, county, legislative and region ids,
labels, hierarchy and road sources, snapshot key). Synthetic data only."""
import copy
import json
import math
import re
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
from django.test import RequestFactory, SimpleTestCase, override_settings

from tools import area_layers as AL
from tools import build_bundle_v4 as B4
from tools import districts as D
from tools import make_scores_fixture as MF
from tools import scores_schema as S
from tools import scores_writer as SW
from tools import support as SUP
from tools.geography_levels import area_label, label, sources

from . import bundle_v4, bundles, scoresdb, v3_area_views, v3_cache, v3_people_views as v3p, views, walk_store
from .test_walk_plan import SUPPORT_DDL
from .v3_http import BadRequest
from .v3_testkit import build_world, retarget

CO, OR, CT = D.get('co-05'), D.get('or-05'), D.get('ct-01')
NOTE = 'Interim overall: candidate support not yet included; final weights 30/25/25/20 arrive with candidate support'
ROADS = [f.rsplit('/', 1)[-1] for f in OR['road_files']]


def with_support(payload, d):
    """Give every published area of a bundle payload the 'sup' group (invented scores, as many people as the area holds) and the
    manifest's records of it: Oregon and Connecticut have a candidate, so their bundles carry it."""
    rng = np.random.default_rng(5)
    file = payload['area_indices.json']
    ok = [r for r in file['rows'] if r['status'] == 'ok']
    cells = B4.sup_cells({(r['basis'], r['level'], r['id']): (rng.uniform(0, 100, r['n_rows']), rng.uniform(0, 100, r['n_rows']))
                          for r in ok})
    for r in ok:
        r['groups']['sup'] = cells[(r['basis'], r['level'], r['id'])]
    file['columns']['sup'] = list(AL.STANDING_COLUMNS)
    manifest = payload['manifest.json']
    manifest['area_index_catalog']['groups'] = AL.index_catalog(d)
    manifest['row_framework'].update(overall_weights=dict(d['score_weights']), support_model={
        **{k: d[k] for k in ('candidate', 'party', 'race_mode')}, 'signals': d['support']['signals']})
    return payload


def oregon(payload):
    """A copy of the Colorado fixture bundle that is a valid Oregon one."""
    payload = copy.deepcopy(payload)
    old = sorted({r['id'] for r in payload['area_indices.json']['rows'] if r['level'] == 'precinct'}
                 | set(payload['geometry.json']['precinct']))
    ids = {'08041': '41005', '08001': '41001', '0805': '4105'}
    ids.update({o: '41005-%03d' % (101 + k) for k, o in enumerate(old)})       # same order, so the rows stay sorted
    text = json.dumps(payload)
    for o, n in ids.items():
        text = text.replace('"%s"' % o, '"%s"' % n)
    payload = json.loads(text)
    for r in payload['rows.json']:
        r['label'] = label(r['level'], r['id'], OR)
    for r in payload['area_indices.json']['rows']:
        r['label'] = area_label(r['level'], r['id'], OR)
    payload['district.geojson']['features'][0]['properties']['label'] = OR['label']
    manifest = payload['manifest.json']
    manifest['hierarchy']['sources'] = sources(OR)
    manifest['geography']['sources']['roads'] = ['https://www2.census.gov/geo/tiger/TIGER2025/ROADS/' + f for f in ROADS]
    manifest['geography']['hashes'] = {name: 'a' * 64 for name in ['blocks.zip', 'districts.zip', *ROADS]}
    rf = manifest['row_framework']
    rf['base_snapshot'] = 'or-cd005-' + rf['base_snapshot'].split('-')[-1]
    manifest['snapshot'] = AL.snapshot_key(rf['base_snapshot'], rf['area_table_sha256'], rf['framework_config_sha256'],
                                           OR['snapshot_prefix'])
    return retarget(with_support(payload, OR))


def validate_as(district, payload, pin='frozen'):
    """bundles.validate in a server process of `district`, whose frozen inputs are pinned to `pin` (None: not pinned)."""
    pin = dict(AL.FROZEN_SHA256) if pin == 'frozen' else pin
    with override_settings(DISTRICT=district), mock.patch.dict(AL.FROZEN_SHA256_BY_DISTRICT, {district['id']: pin}), \
            mock.patch.object(bundle_v4, 'SNAPSHOT_RE', re.compile(D.snapshot_pattern(district))):
        return bundles.validate(payload)


class DistrictBundleTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.co = build_world(Path(cls.temporary.name)).payload
        cls.oregon = oregon(cls.co)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()
        super().tearDownClass()

    def reject(self, mutate, message=None, **kwargs):
        payload = copy.deepcopy(self.oregon)
        mutate(payload)
        retarget(payload)
        with self.assertRaises(ValueError) as caught:
            validate_as(OR, payload, **kwargs)
        if message:
            self.assertIn(message, str(caught.exception))

    def test_oregon_bundle_validates_with_list_roads_and_road_file_hashes(self):
        manifest = validate_as(OR, copy.deepcopy(self.oregon))
        self.assertTrue(manifest['snapshot'].startswith('or-cd005-'))
        self.assertEqual(len(manifest['geography']['sources']['roads']), 6)
        self.assertEqual(set(manifest['geography']['hashes']), {'blocks.zip', 'districts.zip', *ROADS})
        self.assertTrue(validate_as(CO, copy.deepcopy(self.co))['snapshot'].startswith('co-cd005-'))

    def test_a_bundle_belongs_to_one_district(self):
        with self.assertRaises(ValueError):
            validate_as(CO, copy.deepcopy(self.oregon))
        with self.assertRaises(ValueError):
            validate_as(OR, copy.deepcopy(self.co))

    def test_precinct_ids_follow_the_district_pattern(self):
        with override_settings(DISTRICT=OR):
            pattern = bundle_v4.id_pattern('registration', 'precinct')
        self.assertTrue(re.fullmatch(pattern, '41005-123'))
        self.assertFalse(re.fullmatch(pattern, '12345'))
        with override_settings(DISTRICT=CO):
            self.assertEqual(bundle_v4.id_pattern('registration', 'precinct'), r'\d{10}')
            self.assertEqual(bundle_v4.id_pattern('registration', 'block'), r'\d{15}')

        def other_id(payload):
            for row in payload['area_indices.json']['rows']:
                if row['level'] == 'precinct':
                    row['id'] = '12345'
        self.reject(other_id, message='Invalid area id')

    def test_sources_and_hashes_are_checked(self):
        self.reject(lambda p: p['manifest.json']['geography']['sources'].update(roads=['https://example.com/r.zip']),
                    message='geographic source')
        self.reject(lambda p: p['manifest.json']['geography']['hashes'].pop(ROADS[-1]), message='fields')
        self.reject(lambda p: p['manifest.json']['hierarchy']['sources'].update(state_house=sources(CO)['state_house']),
                    message='geographic extension')

    def test_frozen_inputs_are_pinned_per_district_and_none_fails_closed(self):
        for pin, message in ((None, 'not pinned'), ({k: '0' * 64 for k in AL.FROZEN_SHA256}, 'frozen')):
            self.reject(lambda p: None, message=message, pin=pin)
        self.assertIs(AL.FROZEN_SHA256_BY_DISTRICT['co-05'], AL.FROZEN_SHA256)

    def test_a_wrong_freeze_is_refused_against_the_real_district_pins(self):
        # self.oregon claims Colorado's three frozen shas; Oregon's real pin must still refuse it, and so must Connecticut's.
        self.reject(lambda p: None, message='frozen', pin=AL.FROZEN_SHA256_BY_DISTRICT['or-05'])
        with self.assertRaises(ValueError) as caught:
            validate_as(OR, copy.deepcopy(self.oregon), pin=AL.FROZEN_SHA256_BY_DISTRICT['ct-01'])
        self.assertIn('frozen', str(caught.exception))

    # ---- Phase 4B: the 'sup' group of a candidate district's bundle ------------------------------------------------
    def test_a_candidate_bundle_must_carry_the_sup_group_and_the_manifest_records_of_it(self):
        ok_rows = lambda p: [r for r in p['area_indices.json']['rows'] if r['status'] == 'ok']       # noqa: E731

        def drop_sup(payload):
            payload['area_indices.json']['columns'].pop('sup')
            for row in ok_rows(payload):
                row['groups'].pop('sup')
        self.reject(drop_sup, message='Unexpected area index columns')
        self.reject(lambda p: p['manifest.json']['row_framework'].pop('overall_weights'), message='row framework fields')
        self.reject(lambda p: p['manifest.json']['row_framework'].pop('support_model'), message='row framework fields')
        self.reject(lambda p: p['manifest.json']['row_framework']['overall_weights'].update(sup=20, soi=25),
                    message='support settings')
        self.reject(lambda p: p['manifest.json']['row_framework']['support_model'].update(race_mode='primary'),
                    message='support settings')
        self.reject(lambda p: p['manifest.json']['area_index_catalog'].update(groups=AL.INDEX_CATALOG), message='catalog')

    def test_the_sup_cells_are_checked_like_any_other_group(self):
        band = AL.STANDING_COLUMNS.index('band')
        area_pct = AL.STANDING_COLUMNS.index('area_pct')
        first = lambda p: next(r for r in p['area_indices.json']['rows'] if r['level'] == 'precinct' and r['status'] == 'ok')    # noqa: E731
        self.reject(lambda p: first(p)['groups']['sup'].__setitem__(band, first(p)['groups']['sup'][band] % 5 + 1),
                    message='Band does not follow the standing')
        self.reject(lambda p: first(p)['groups']['sup'].__setitem__(area_pct, 101.0), message='Invalid area number')
        self.reject(lambda p: first(p)['groups']['sup'].__setitem__(AL.STANDING_COLUMNS.index('top10_share_eb'), 1.5),
                    message='Invalid area number')

    def test_a_colorado_bundle_cannot_carry_sup(self):
        payload = copy.deepcopy(self.co)
        payload['area_indices.json']['columns']['sup'] = list(AL.STANDING_COLUMNS)
        for row in payload['area_indices.json']['rows']:
            if row['status'] == 'ok':
                row['groups']['sup'] = [50.0] * len(AL.STANDING_COLUMNS)
        with self.assertRaises(ValueError) as caught:
            validate_as(CO, retarget(payload))
        self.assertIn('Unexpected area index columns', str(caught.exception))


class SupportLayerTests(SimpleTestCase):
    """The helpers behind the 'sup' group: which groups a district has, the plain area means on hand numbers, and the real score store
    (scores_writer, with a support table) read by the bundle builder and by the walk list's street plan and printed scores."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.root = Path(cls.temporary.name)
        cls.fx = MF.make_store(cls.root / 'fx')
        n = cls.fx.n
        cls.score = np.round(np.random.default_rng(3).uniform(0, 100, n), 2)         # two decimals: some ties
        cls.pct = SUP.standing_pct(cls.score)
        cls.info = {'candidate': OR['candidate'], 'party': OR['party'], 'race_mode': OR['race_mode'], 'signals': OR['support']['signals'],
                    'coverage': {}, 'orientation': {}, 'sha256': 'a' * 64}
        support = {'sup_score': cls.score, 'sup_pct': cls.pct, 'in_universe': np.ones(n, dtype=bool), 'info': cls.info}
        SW.write_scores_db(cls.root / 'scores.sqlite3', {**cls.fx.data, 'support': support}, cls.fx.meta, cls.fx.demo_key)

    @classmethod
    def tearDownClass(cls):
        scoresdb.reset()
        cls.temporary.cleanup()
        super().tearDownClass()

    # ---- the district's groups ----------------------------------------------------------------------------------
    def test_colorado_and_a_district_without_a_candidate_get_the_existing_objects(self):
        for d in (CO, None, {**OR, 'candidate': None}):
            self.assertIs(AL.groups(d), AL.GROUPS)
            self.assertIs(AL.columns(d), AL.COLUMNS)
            self.assertIs(AL.index_catalog(d), AL.INDEX_CATALOG)

    def test_a_candidate_district_adds_sup_after_ovr(self):
        self.assertEqual(AL.groups(OR), ('ovr', 'sup', 'cua', 'part', 'soi', 'lean'))
        self.assertEqual(AL.groups(D.get('ct-01')), AL.groups(OR))
        self.assertIs(AL.columns(OR)['sup'], AL.STANDING_COLUMNS)
        self.assertEqual([g['id'] for g in AL.index_catalog(OR)], list(AL.groups(OR)))
        self.assertEqual(AL.index_catalog(OR)[1], {'id': 'sup', 'label': 'Candidate support', 'control': 'Candidate support',
                                                   'top10': 'Candidate support'})
        self.assertEqual([g['id'] for g in AL.INDEX_CATALOG], list(AL.GROUPS))            # Colorado's catalog is untouched

    # ---- hand numbers: six people in two areas --------------------------------------------------------------------
    def test_area_means_on_six_people_in_two_areas(self):
        a, b = ('registration', 'precinct', 'P1'), ('registration', 'precinct', 'P2')
        codes = {'a_reg_precinct': np.array([0, 0, 0, 1, 1, 1], dtype=np.int32)}
        dicts = {'a_reg_precinct': ['P1', 'P2']}
        score = np.array([10.0, 20.0, 30.0, 40.0, 50.0, 90.0])
        pct = np.array([50.0, 95.0, 92.0, 10.0, 20.0, 99.0])
        stats = B4.sup_stats({a: 3, b: 3}, score, pct, codes, dicts)
        self.assertEqual(stats[a][0].tolist(), [10.0, 20.0, 30.0])
        cells = B4.sup_cells(stats)
        names = AL.STANDING_COLUMNS
        one, two = (dict(zip(names, cells[k])) for k in (a, b))
        self.assertEqual(len(cells[a]), len(names))
        # P1: mean 20, sd 10, se 10 / sqrt(3); two of three above the 90th standing. P2: mean 60, sd sqrt(700), one of three.
        self.assertAlmostEqual(one['raw_mean'], 20.0)
        self.assertEqual((one['eb_mean'], one['shrink_B'], one['measured_pct_mean'], one['top10_shrink_B']), (one['raw_mean'], 0.0, 100.0, 0.0))
        self.assertAlmostEqual(one['eb_se'], 10 / 3 ** .5)
        self.assertAlmostEqual(two['raw_mean'], 60.0)
        self.assertAlmostEqual(two['eb_se'], 700 ** .5 / 3 ** .5)
        self.assertAlmostEqual(one['top10_share_raw'], 2 / 3)
        self.assertAlmostEqual(two['top10_share_eb'], 1 / 3)
        self.assertAlmostEqual(one['top10_eb_se'], (2 / 3 * (1 / 3) / 3) ** .5)
        # standings among the two published areas: midrank 25 and 75 (top-10 share: P1 is higher, so 75 and 25); bands follow
        self.assertEqual((one['area_pct'], two['area_pct'], one['band'], two['band']), (25.0, 75.0, 2, 4))
        self.assertEqual((one['top10_area_pct'], two['top10_area_pct'], one['top10_band'], two['top10_band']), (75.0, 25.0, 4, 2))
        # 90% bounds: the midrank of mean -/+ 1.6449 se among the two means. P1 10.5 / 29.5 -> 0 / 50; P2 34.9 / 85.1 -> 50 / 100
        self.assertEqual((one['area_pct_lo90'], one['area_pct_hi90'], two['area_pct_lo90'], two['area_pct_hi90']), (0.0, 50.0, 50.0, 100.0))
        self.assertEqual((one['top10_area_pct_lo90'], one['top10_area_pct_hi90']), (0.0, 100.0))
        self.assertTrue(all(isinstance(v, int) for v in (one['band'], one['top10_band'])))

    def test_a_one_person_area_gets_its_levels_pooled_sd_not_nan(self):
        """ddof=1 on one score is NaN (the standard error crashed the validator once small areas were shown): the area takes the
        level's pooled within-area SD, sqrt(sum((n - 1) var) / sum(n - 1)); the areas of more than one person are unchanged."""
        a, b, c = (('registration', 'precinct', p) for p in ('P1', 'P2', 'P3'))
        codes = {'a_reg_precinct': np.array([0, 0, 0, 1, 1, 1, 2], dtype=np.int32)}
        dicts = {'a_reg_precinct': ['P1', 'P2', 'P3']}
        score = np.array([10.0, 20.0, 30.0, 40.0, 50.0, 90.0, 70.0])
        pct = np.array([50.0, 95.0, 92.0, 10.0, 20.0, 99.0, 91.0])
        cells = B4.sup_cells(B4.sup_stats({a: 3, b: 3, c: 1}, score, pct, codes, dicts))
        names = AL.STANDING_COLUMNS
        one, two, lone = (dict(zip(names, cells[k])) for k in (a, b, c))
        pooled = ((2 * 100.0 + 2 * 700.0) / 4) ** .5                                          # sqrt(400) = 20
        self.assertAlmostEqual(lone['eb_se'], pooled)
        self.assertAlmostEqual(one['eb_se'], 10 / 3 ** .5)                                    # as without the lone area
        self.assertAlmostEqual(two['eb_se'], 700 ** .5 / 3 ** .5)
        self.assertEqual((lone['raw_mean'], lone['top10_share_raw'], lone['top10_eb_se']), (70.0, 1.0, 0.0))
        self.assertTrue(all(math.isfinite(v) for v in cells[c]))
        only = B4.sup_cells(B4.sup_stats({c: 1}, score[6:], pct[6:], {'a_reg_precinct': np.array([0], dtype=np.int32)},
                                         {'a_reg_precinct': ['P3']}))
        self.assertEqual(dict(zip(names, only[c]))['eb_se'], 0.0)                              # a level of lone areas has no spread

    def test_an_area_must_hold_exactly_the_people_the_area_table_counts(self):
        codes, dicts = {'a_reg_precinct': np.array([0, 0, 0, 1, 1, 1], dtype=np.int32)}, {'a_reg_precinct': ['P1', 'P2']}
        score = pct = np.arange(6.0)
        for counts in ({('registration', 'precinct', 'P1'): 4}, {('registration', 'precinct', 'P9'): 3}):
            with self.assertRaises(B4.BuildError):
                B4.sup_stats(counts, score, pct, codes, dicts)

    # ---- the real writer, the bundle builder's reader, the area file --------------------------------------------------
    def test_the_builder_reads_the_support_table_in_pos_order_and_builds_every_published_area(self):
        recorded, score, pct, codes, dicts = B4.read_support(self.root / 'scores.sqlite3')
        self.assertEqual(recorded, self.info)
        order = {p: k for k, p in enumerate(np.asarray(self.fx.data['person_id']).tolist())}
        db = sqlite3.connect('file:%s?mode=ro' % (self.root / 'scores.sqlite3').as_posix(), uri=True)
        try:
            ids = [r[0] for r in db.execute('SELECT person_id FROM person ORDER BY pos')]
        finally:
            db.close()
        self.assertEqual(score.tolist(), self.score[[order[i] for i in ids]].tolist())
        config = self.root / 'rf'
        config.mkdir(exist_ok=True)
        key = 'W' + '_'.join(str(v) for v in OR['score_weights'].values())
        block = {'weights': {g: {'value': v / 100.0} for g, v in OR['score_weights'].items()}, 'support': dict(self.info)}
        (config / 'framework_config.json').write_text(json.dumps({'overall': {'default': key, key: block}}), encoding='utf-8')
        cells, weights, model = B4.support_layer(OR, config, self.root / 'scores.sqlite3', self.fx.area_rows)
        self.assertEqual((weights, model['signals']), (OR['score_weights'], OR['support']['signals']))
        published = {(r['basis'], r['level'], r['area_id']) for r in self.fx.area_rows if r['group'] == 'ovr'}
        self.assertEqual(set(cells), published)
        precinct = next(k for k in sorted(published) if k[:2] == ('registration', 'precinct'))
        members = np.flatnonzero(np.asarray(self.fx.data['reg_precinct']).astype(str) == precinct[2])
        self.assertAlmostEqual(cells[precinct][0], float(self.score[members].mean()))
        self.assertAlmostEqual(cells[precinct][AL.STANDING_COLUMNS.index('top10_share_raw')], float((self.pct[members] >= 90).mean()))
        file = B4.make_area_file(self.fx.area_rows, None, OR, cells)
        self.assertEqual(file['columns']['sup'], list(AL.STANDING_COLUMNS))
        self.assertEqual(list(file['columns']), list(AL.groups(OR)))
        self.assertEqual(len(file['rows']), len(published))                    # every area of the table, none withheld
        for row in file['rows']:
            self.assertEqual((row['status'], list(row['groups'])), ('ok', list(AL.groups(OR))))
        self.assertTrue(any(r['n_rows'] < AL.SMALL_N for r in file['rows']))      # small areas carry their values too

    def test_the_builder_refuses_a_store_or_config_for_other_support_settings(self):
        key = 'W' + '_'.join(str(v) for v in OR['score_weights'].values())
        folder = self.root / 'rf_bad'
        folder.mkdir(exist_ok=True)

        def build(**changes):
            support = {**self.info, **changes.pop('support', {})}
            weights = changes.pop('weights', OR['score_weights'])
            block = {'weights': {g: {'value': v / 100.0} for g, v in weights.items()}, 'support': support}
            (folder / 'framework_config.json').write_text(json.dumps({'overall': {'default': key, key: block}}), encoding='utf-8')
            return B4.support_layer(OR, folder, self.root / 'scores.sqlite3', self.fx.area_rows)
        for changes, message in (({'support': {'sha256': 'b' * 64}}, 'another support file'),
                                 ({'support': {'candidate': 'Someone Else'}}, 'other support settings'),
                                 ({'weights': {'cua': 40, 'part': 35, 'soi': 25}}, 'weights differ')):
            with self.assertRaises(B4.BuildError) as caught:
                build(**changes)
            self.assertIn(message, str(caught.exception))

    # ---- the walk list reads the same table -------------------------------------------------------------------------------
    def test_the_walk_list_reader_accepts_the_support_table_the_writer_makes(self):
        def store(directory):
            info = SimpleNamespace(path=Path(directory) / 'walk.sqlite3', source_sha256=self.fx.source_sha)
            return walk_store.Store(sqlite3.connect(':memory:', uri=True), info)
        self.assertEqual(S.SUPPORT_TABLE, SUPPORT_DDL)                       # the table walk_store's street plan tests build by hand
        with_support = store(self.root)
        self.assertTrue(with_support.attach_scores())
        self.assertTrue(with_support.support)
        row = {p: k for k, p in enumerate(np.asarray(self.fx.data['person_id']).tolist())}
        ids = list(row)[:40]
        got = with_support.scores(ids)
        self.assertEqual(len(got), len(ids))
        for person in ids:
            self.assertEqual(got[person][2], self.pct[row[person]])
            self.assertAlmostEqual(got[person][0], float(self.fx.data['ovr_pct'][row[person]]), places=4)
        plain = store(self.fx.directory)                                       # the fixture's own store has no support table
        self.assertTrue(plain.attach_scores())
        self.assertFalse(plain.support)
        self.assertEqual({v[2] for v in plain.scores(ids).values()}, {None})
        # span_plan's SQL joins scores.support on pos: the same join, run against the walk list's own connection
        count = with_support.conn.execute('SELECT COUNT(*) FROM scores.person sc JOIN scores.support sp ON sp.pos = sc.pos').fetchone()[0]
        self.assertEqual(count, self.fx.n)

    def test_the_features_page_sees_the_support_metadata_key(self):
        for folder, expected in ((self.root, True), (self.fx.directory, False)):
            with override_settings(DATA_DIR=folder):
                scoresdb.reset()
                self.assertIs(v3_area_views._store_has_support(), expected)
        scoresdb.reset()


class DistrictMetaTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.payload = build_world(Path(cls.temporary.name)).payload

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()
        super().tearDownClass()

    def meta(self, district):
        with override_settings(DISTRICT=district), \
                mock.patch.object(v3_cache, 'read_json', lambda key, name: self.payload[name]):
            return v3_cache.build_meta(self.payload['manifest.json']['snapshot'])

    def test_colorado_meta_has_no_note_and_keeps_its_keys_and_weights(self):
        meta = self.meta(CO)
        self.assertNotIn('overall_note', meta)
        self.assertEqual(list(meta)[-1], 'registered_people')
        self.assertEqual((meta['district_label'], json.dumps(meta['weights'])), ('District 5', '{"cua": 40, "part": 35, "soi": 25}'))

    def test_oregon_and_connecticut_meta_carry_the_interim_note_and_their_label(self):
        for district, short in ((OR, 'District 5'), (CT, 'District 1')):
            meta = self.meta(district)
            self.assertEqual(meta['overall_note'], NOTE)
            self.assertEqual(meta['weights'], {'cua': 40, 'part': 35, 'soi': 25})
            self.assertEqual(meta['district_label'], short)

    def test_people_area_filter_and_level_catalog_follow_the_district(self):
        def query(area):
            return v3p.list_query(RequestFactory().get('/api/v3/people', {'level': 'precinct', 'area': area}))
        with override_settings(DISTRICT=OR):
            self.assertEqual(query('41005-123')['area'], '41005-123')
            with self.assertRaises(BadRequest):
                query('0804100621')
            self.assertEqual(views.available_levels('cu-summary-v4')[0]['vintage'], 'Study region: supplied Oregon CD-05 extract')
        with override_settings(DISTRICT=CO):
            self.assertEqual(query('0804100621')['area'], '0804100621')
            with self.assertRaises(BadRequest):
                query('41005-123')
            self.assertEqual(views.available_levels('cu-summary-v4')[0]['vintage'], 'Study region: supplied Colorado CD-05 extract')
