"""Route input regression tests using the invented walk_testkit world only."""
import sqlite3
from contextlib import closing
from urllib.parse import urlencode
from unittest import mock
from django.test import SimpleTestCase
from . import route_affiliation as A
from . import walk_store as W
from .test_walk_api import WalkCase, voters_of


class AffiliationRules(SimpleTestCase):
    def test_categories_are_not_collapsed_and_empty_is_not_everyone(self):
        self.assertIsNone(A.parse(None))
        self.assertEqual(A.parse('empty'), ())
        self.assertEqual(A.parse('U,I,G,none,T'), ('G', 'I', 'T', 'U', 'none'))
        keep=A.keep(None,A.parse('I,G'))
        self.assertTrue(keep({'party':'I'}))
        self.assertTrue(keep({'party':'G'}))
        self.assertFalse(keep({'party':'U'}))
        self.assertFalse(A.keep(None,())({'party':'I'}))
        self.assertTrue(A.keep(None,('none',))({'party':''}))

    def test_malformed_filters_are_rejected_and_sql_is_parameterized(self):
        for value in ['I,I','I,','i',"D'); DROP TABLE person;--",'I, R',False]:
            with self.subTest(value=value),self.assertRaises(ValueError):A.parse(value)
        clause,args=A.sql(('I','U','none'))
        self.assertEqual(args,['I','U','none'])
        self.assertEqual(clause.count('?'),3)
        self.assertEqual(A.sql(None),('',[]))
        previous=lambda row:row['eligible']
        keep=A.keep(previous,('I',))
        self.assertFalse(keep({'party':'I','eligible':False}))
        self.assertTrue(keep({'party':'I','eligible':True}))


class AffiliationRoutes(WalkCase):
    def query(self,code=None):
        q={'streets':self.cedar_juniper()}
        if code is not None:q['affiliation']=code
        return q

    def route(self,code=None):
        return self.json('/api/v3/walk/list?'+urlencode(self.query(code)))

    def test_all_existing_categories_preserve_every_door_path_and_order(self):
        base=self.route()
        codes=sorted({p['party'] or 'none' for p in voters_of(base)})
        filtered=self.route(','.join(codes))
        self.assertEqual(base['runs'],filtered['runs'])
        self.assertEqual(base['totals'],filtered['totals'])
        self.assertEqual(base['bbox'],filtered['bbox'])
        self.assertNotIn('affiliation',base['query'])

    def test_filter_is_applied_before_routing_and_persists_in_saved_list(self):
        base=self.route();code=voters_of(base)[0]['party'] or 'none'
        expected={p['person_id'] for p in voters_of(base) if (p['party'] or 'none')==code}
        result=self.route(code)
        self.assertEqual({p['person_id'] for p in voters_of(result)},expected)
        self.assertTrue(all(door['voters'] for run in result['runs'] for door in run['doors']))
        saved=self.make_list(self.query(code))
        loaded=self.json('/api/v3/walk/lists/'+saved['list']['key'])
        self.assertEqual(loaded['query']['affiliation'],code)
        self.assertEqual({p['person_id'] for p in voters_of(loaded)},expected)

    def test_independent_and_third_party_records_keep_house_coordinates(self):
        base=self.route();people=voters_of(base);changes={people[0]['person_id']:'I',people[-1]['person_id']:'G'}
        original={p['person_id']:p['party'] for p in people if p['person_id'] in changes}
        with closing(sqlite3.connect(self.stores.directory/'walk.sqlite3')) as db, db:
            for pid,code in changes.items():db.execute('UPDATE person SET party=? WHERE person_id=?',(code,pid))
        try:
            # Affiliation alone must work without a support model or a scores join.
            with mock.patch.object(W.Store,'attach_scores',side_effect=AssertionError('No model needed')):
                result=self.route('I,G')
            self.assertEqual({p['party'] for p in voters_of(result)},{'I','G'})
            before={p['person_id']:(d['house'],d['unit'],d['lon'],d['lat']) for r in base['runs'] for d in r['doors'] for p in d['voters']}
            after={p['person_id']:(d['house'],d['unit'],d['lon'],d['lat']) for r in result['runs'] for d in r['doors'] for p in d['voters']}
            self.assertEqual(after,{pid:before[pid] for pid in changes})
        finally:
            with closing(sqlite3.connect(self.stores.directory/'walk.sqlite3')) as db, db:
                for pid,code in original.items():db.execute('UPDATE person SET party=? WHERE person_id=?',(code,pid))

    def test_empty_or_invalid_selection_never_broadens_to_everyone(self):
        for code in ['empty','I,I','I,','invalid','ZZ-NOT-PRESENT']:
            self.json('/api/v3/walk/list?'+urlencode(self.query(code)),400)
