"""Candidate support on the display side (Phase 4, Lane SD): the meta weights follow the snapshot manifest (the interim 40/35/25 and its
note until a 4-term snapshot is imported), the area payload carries `groups.sup`, and the people list and the profile show support
only from a score store that holds it. Colorado, with no candidate, stays as it was. Synthetic data only."""
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

import numpy as np
from django.test import Client, TestCase, override_settings

from tools import area_layers as AL
from tools import districts as D
from tools import make_scores_fixture as MF
from tools import scores_writer as SW
from tools import support as SUP

from . import bundles, scoresdb, v3_cache, v3_people_views as v3p
from .test_bundle_districts import NOTE, oregon
from .v3_testkit import build_world

CO, OR, CT = D.get('co-05'), D.get('or-05'), D.get('ct-01')
FOUR = {'cua': 30, 'sup': 25, 'part': 25, 'soi': 20}
LOOPBACK = dict(HTTP_HOST='localhost')


class MetaAndAreasTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.co = build_world(Path(cls.temporary.name)).payload          # a 3-term snapshot: no overall_weights, no sup group
        cls.four = oregon(cls.co)                                         # the same bundle as a 4-term OR one (sup + overall_weights)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()
        super().tearDownClass()

    def patched(self, payload):
        return mock.patch.multiple(v3_cache, read_json=lambda key, name: payload[name]), \
            mock.patch.object(bundles, 'load', lambda key, name: payload[name])

    def meta(self, district, payload):
        a, b = self.patched(payload)
        with override_settings(DISTRICT=district), a, b:
            return v3_cache.build_meta(payload['manifest.json']['snapshot'])

    def areas(self, district, payload, level='precinct', basis='registration'):
        a, b = self.patched(payload)
        with override_settings(DISTRICT=district), a, b:
            return v3_cache.build_areas(payload['manifest.json']['snapshot'], basis, level)

    # ---- meta: weights and the interim note follow the snapshot manifest ---------------------------------------------
    def test_a_four_term_snapshot_gives_final_weights_and_no_note(self):
        for district in (OR, CT):
            self.assertIn('overall_weights', self.four['manifest.json']['row_framework'])
            meta = self.meta(district, self.four)
            self.assertEqual(meta['weights'], FOUR)
            self.assertEqual(list(meta['weights']), ['cua', 'sup', 'part', 'soi'])
            self.assertNotIn('overall_note', meta)
            self.assertEqual([g['id'] for g in meta['index_catalog']], list(AL.groups(district)))

    def test_a_three_term_snapshot_keeps_the_interim_weights_and_note(self):
        for district in (OR, CT):
            meta = self.meta(district, self.co)
            self.assertEqual((meta['weights'], meta['overall_note']), ({'cua': 40, 'part': 35, 'soi': 25}, NOTE))
            self.assertEqual([g['id'] for g in meta['index_catalog']], list(AL.GROUPS))

    def test_the_candidate_is_named_for_a_candidate_district_only(self):
        for district, name in ((OR, 'Janelle Bynum'), (CT, 'Luke Bronin')):
            for payload in (self.co, self.four):
                self.assertEqual(self.meta(district, payload)['candidate'], {
                    'name': name, 'party': 'D', 'party_label': 'Democrat', 'race_mode': 'general'})
        self.assertNotIn('candidate', self.meta(CO, self.co))

    def test_the_race_mode_shown_is_the_configured_one(self):
        self.assertEqual(self.meta({**OR, 'race_mode': 'primary'}, self.four)['candidate']['race_mode'], 'primary')

    def test_colorado_meta_is_unchanged(self):
        meta = self.meta(CO, self.co)
        self.assertEqual(list(meta)[-1], 'registered_people')
        self.assertEqual((json.dumps(meta['weights']), json.dumps(meta['index_catalog'])),
                         ('{"cua": 40, "part": 35, "soi": 25}', json.dumps(AL.INDEX_CATALOG)))

    # ---- areas: groups.sup -----------------------------------------------------------------------------------------
    def test_a_four_term_snapshot_adds_the_sup_group_with_the_seven_cells(self):
        body = self.areas(OR, self.four)
        self.assertEqual(list(body['groups']), ['ovr', 'sup', 'cua', 'part', 'soi', 'lean'])
        cells = body['groups']['sup']
        self.assertEqual(list(cells), list(body['groups']['ovr']))
        self.assertTrue(all(len(v) == len(body['ids']) for v in cells.values()))
        rows = {r['id']: r for r in self.four['area_indices.json']['rows'] if (r['basis'], r['level']) == ('registration', 'precinct')}
        index = {name: k for k, name in enumerate(AL.STANDING_COLUMNS)}
        shown = 0
        for i, ident in enumerate(body['ids']):
            row = rows.get(ident)
            if row is None or row['status'] != 'ok':
                self.assertTrue(all(v[i] is None for v in cells.values()), ident)
                continue
            cell = row['groups']['sup']
            self.assertAlmostEqual(cells['mean'][i], cell[index['eb_mean']], places=6)
            self.assertAlmostEqual(cells['pct'][i], cell[index['area_pct']], places=6)
            self.assertAlmostEqual(cells['top10'][i], cell[index['top10_share_eb']], places=6)
            shown += 1
        self.assertGreater(shown, 0)

    def test_every_level_of_both_bases_builds_and_a_withheld_area_has_no_sup_value(self):
        withheld = 0
        for basis, levels in AL.BASIS_LEVELS.items():
            for level in levels:
                body = self.areas(OR, self.four, level, basis)
                self.assertIn('sup', body['groups'], (basis, level))
                for i, state in enumerate(body['status']):
                    if state == 'withheld':
                        withheld += 1
                        self.assertTrue(all(v[i] is None for v in body['groups']['sup'].values()), (basis, level, i))
        self.assertGreater(withheld, 0)                      # the fixture holds withheld areas, whose rows carry no groups

    def test_a_three_term_snapshot_of_a_candidate_district_has_no_sup_group(self):
        self.assertEqual(list(self.areas(OR, self.co)['groups']), ['ovr', 'cua', 'part', 'soi', 'lean'])

    def test_colorado_areas_are_unchanged(self):
        self.assertEqual(list(self.areas(CO, self.co)['groups']), ['ovr', 'cua', 'part', 'soi', 'lean'])


@override_settings(LOCAL_ADMIN_ACCESS=True, HOSTED=False, DEMO_MODE=False)
class PeopleAndProfileTests(TestCase):
    """One synthetic store with a support table (a candidate district's), one without (Colorado's)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.root = Path(cls.temporary.name)
        cls.fx = MF.make_store(cls.root / 'plain')
        cls.score = np.round(np.random.default_rng(11).uniform(0, 100, cls.fx.n), 1)       # one decimal: ties
        cls.pct = SUP.standing_pct(cls.score)
        info = {'candidate': OR['candidate'], 'party': OR['party'], 'race_mode': OR['race_mode'],
                'signals': OR['support']['signals'], 'coverage': {}, 'orientation': {}, 'sha256': 'a' * 64}
        cls.candidate = cls.root / 'candidate'
        cls.candidate.mkdir()
        SW.write_scores_db(cls.candidate / 'scores.sqlite3', {**cls.fx.data, 'support': {
            'sup_score': cls.score, 'sup_pct': cls.pct, 'in_universe': np.ones(cls.fx.n, dtype=bool), 'info': info}},
            cls.fx.meta, cls.fx.demo_key)

    @classmethod
    def tearDownClass(cls):
        scoresdb.reset()
        cls.temporary.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.client = Client(**LOOPBACK)
        self.addCleanup(scoresdb.reset)

    def get(self, url, folder):
        scoresdb.reset()
        with override_settings(DATA_DIR=folder), mock.patch.object(v3p, 'area_lookup', lambda info: {}):
            return self.client.get(url + ('&' if '?' in url else '?') + 'demo=1')

    def stored(self, folder, sql, *args):
        with sqlite3.connect('file:%s?mode=ro' % (folder / 'scores.sqlite3').as_posix(), uri=True) as db:
            return db.execute(sql, args).fetchall()

    def test_the_store_knows_whether_it_holds_support(self):
        for folder, expected in ((self.candidate, True), (self.fx.directory, False)):
            scoresdb.reset()
            with override_settings(DATA_DIR=folder):
                self.assertIs(scoresdb.info().support, expected)

    def test_sort_sup_lists_people_by_support_rank_with_equals_for_ties(self):
        want = self.stored(self.candidate, 'SELECT p.person_id, s.sup_pct, s.sup_rank, s.sup_tie_n FROM support s JOIN person p '
                           'ON p.pos = s.pos ORDER BY s.sup_rank, s.pos')
        page = self.get('/api/v3/people?basis=registration&sort=sup', self.candidate).json()
        self.assertEqual((page['total'], len(page['rows'])), (self.fx.n, 50))
        self.assertEqual([r['person_id'] for r in page['rows']], [w[0] for w in want[:50]])
        self.assertEqual([r['rank'] for r in page['rows']], [v3p.rank_text(w[2], w[3]) for w in want[:50]])
        self.assertEqual([r['sup_pct'] for r in page['rows']], [scoresdb.number(w[1]) for w in want[:50]])
        self.assertTrue(any(r['rank'].startswith('=') for r in page['rows']))           # the 0.1 steps tie
        self.assertEqual([r['sup_pct'] for r in page['rows']], sorted((r['sup_pct'] for r in page['rows']), reverse=True))
        nxt = self.get('/api/v3/people?basis=registration&sort=sup&after=' + page['next'], self.candidate).json()
        self.assertEqual([r['person_id'] for r in nxt['rows']], [w[0] for w in want[50:100]])

    def test_every_list_row_of_a_candidate_store_carries_sup_pct_and_the_default_order_is_unchanged(self):
        page = self.get('/api/v3/people?basis=registration', self.candidate).json()
        plain = self.get('/api/v3/people?basis=registration', self.fx.directory).json()
        self.assertTrue(all(isinstance(r['sup_pct'], float) for r in page['rows']))
        self.assertEqual([r['person_id'] for r in page['rows']], [r['person_id'] for r in plain['rows']])
        self.assertEqual([r['rank'] for r in page['rows']], [r['rank'] for r in plain['rows']])
        by_id = dict(self.stored(self.candidate, 'SELECT p.person_id, s.sup_pct FROM support s JOIN person p ON p.pos = s.pos'))
        self.assertEqual({r['person_id']: r['sup_pct'] for r in page['rows']}, {r['person_id']: scoresdb.number(by_id[r['person_id']]) for r in page['rows']})

    def test_support_sort_composes_with_the_filters(self):
        page = self.get('/api/v3/people?basis=registration&sort=sup&reg=D&grade=AB', self.candidate).json()
        both = self.get('/api/v3/people?basis=registration&reg=D&grade=AB', self.candidate).json()
        self.assertEqual(page['total'], both['total'])
        scores = [r['sup_pct'] for r in page['rows']]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_without_a_support_table_sort_sup_is_a_400_and_rows_have_no_sup_pct(self):
        self.assertEqual(self.get('/api/v3/people?basis=registration&sort=sup', self.fx.directory).status_code, 400)
        page = self.get('/api/v3/people?basis=registration&sort=cua', self.fx.directory).json()
        self.assertTrue(all('sup_pct' not in r for r in page['rows']))
        for url in ('/api/v3/people?basis=registration&sort=name', '/api/v3/people?basis=registration&sort=sup&sort=cua'):
            self.assertEqual(self.get(url, self.candidate).status_code, 400, url)

    def test_the_profile_card_has_a_score_and_a_standing_and_only_for_a_candidate_store(self):
        stored = self.stored(self.candidate, 'SELECT p.person_id, s.sup_score, s.sup_pct FROM support s JOIN person p ON p.pos = s.pos '
                             'ORDER BY s.pos LIMIT 3')
        for person, score, pct in stored:
            cards = self.get('/api/v3/person?id=' + person, self.candidate).json()['cards']
            self.assertEqual(cards['sup'], {'score': scoresdb.number(score), 'pct': scoresdb.number(pct)})
            self.assertEqual(list(cards), ['cua', 'part', 'soi', 'lean', 'sup'])
            plain = self.get('/api/v3/person?id=' + person, self.fx.directory).json()['cards']
            self.assertEqual(list(plain), ['cua', 'part', 'soi', 'lean'])
            self.assertEqual({k: cards[k] for k in plain}, plain)
