"""Reviewed parcel repairs must never turn nearby streets into guessed houses."""
import json
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from . import walk_location_evidence as L


class LocationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE road_span(id INTEGER,name_key TEXT,len_m REAL,coords TEXT,virtual INTEGER)')
        self.db.execute('INSERT INTO road_span VALUES(1,?,?,?,0)', ('OAK RD', 200, json.dumps([[-72.881,41.64],[-72.879,41.64]])))
        self.names = {1: SimpleNamespace(key='OAK RD', display='Oak Rd', city='Town'),
                      2: SimpleNamespace(key='PNE ST', display='Pine St', city='Town')}
        self.store = SimpleNamespace(conn=self.db, directory=lambda: SimpleNamespace(by_id=self.names))
        self.target = {'person_id':'a','stop_id':1,'street_id':1,'house':'10','unit':'','precinct':'P1','zip':'01234',
                       'lon':None,'lat':None,'span_id':None,'pos_m':None,'placed_by':'unlocated','side_lr':'R'}
        self.anchor = {**self.target,'person_id':'b','stop_id':2,'street_id':2,'house':'20','lon':-72.88,'lat':41.641,'placed_by':'geocode'}
        self.omission = {'street_id':1,'street':'Oak Rd','house':'10','unit':'','precinct':'P1','person_ids':['a'],
                         'original':{'lon':-72.5,'lat':41.8,'span_id':8,'road_name':'ELM RD','placed_by':'nearest'},
                         'reason':'Wrong source road; map placement omitted.'}
        self.entry = {'address':{'house':'10','street':'Oak Rd','unit':'','city':'Town','state':'CT','zip':None},
                      'aliases':[], 'location':{'lon':-72.88,'lat':41.6401,'kind':'municipal_parcel'},
                      'polygon':{'rings':[[[-72.8802,41.6400],[-72.8798,41.6400],[-72.8798,41.6402],[-72.8802,41.6402],[-72.8802,41.6400]]]},
                      'source':{'url':'https://example.gov/parcel/1','object_id':1,'retrieved_at':'2026-10-17T00:00:00Z','sha256':'a'*64,'parcel_unit':'0610'},
                      'context':{'street_names':['Pine St'],'reason':'Reviewed named road neighborhood'},'bundle_sha256':'b'*64}

    def repair(self, entries=None, rows=None, missing=()):
        return L.repair_locations(self.store, rows if rows is not None else [self.target,self.anchor], [self.omission],
                                  'ct-01','CT', evidence=(entries if entries is not None else [self.entry],list(missing),[]))

    def test_exact_parcel_preserves_people_source_coordinates_and_separate_route_projection(self):
        rows = [self.target, self.anchor]
        clean, omitted, corrections, warnings = self.repair(rows=rows)
        self.assertEqual([r['person_id'] for r in clean], ['a','b'])
        self.assertEqual(clean[1], self.anchor)
        self.assertIsNone(rows[0]['lon'])
        self.assertEqual((clean[0]['placed_by'],clean[0]['lat'],clean[0]['span_id'],clean[0]['side_lr']), ('parcel',41.6401,1,None))
        self.assertEqual(clean[0]['zip'], '01234')
        self.assertEqual(omitted, [])
        self.assertEqual(warnings, [])
        proof = corrections[0]
        self.assertEqual(proof['original'], self.omission['original'])
        self.assertEqual(proof['route_projection']['lat'], 41.64)
        self.assertAlmostEqual(proof['route_projection']['pos_m'], 100, places=5)
        self.assertGreater(proof['route_projection']['distance_m'], 10)
        self.assertEqual(proof['location']['kind'], 'municipal_parcel')
        self.assertIn('entrance position is not verified', proof['reason'])
        self.assertEqual(proof['source']['parcel_unit'], '0610')
        self.assertEqual(proof['address_match']['unit'], '')
        json.dumps(corrections, allow_nan=False)

    def test_exact_house_locality_unit_and_known_zip_cannot_be_broadened(self):
        for field, value in [('house','11'),('street','Oaks Rd'),('unit','0610'),('city','Elsewhere'),('state','MA'),('zip','99999')]:
            with self.subTest(field=field):
                entry = deepcopy(self.entry)
                entry['address'][field] = value
                clean, omitted, corrections, _ = self.repair(entries=[entry])
                self.assertIsNone(clean[0]['lon'])
                self.assertEqual(len(omitted), 1)
                self.assertFalse(corrections)
        # ZIP null says nothing about a postal code; all original record fields survive.
        self.assertEqual(self.repair()[0][0]['zip'], '01234')

    def test_only_reviewed_alias_is_allowed(self):
        entry = deepcopy(self.entry)
        entry['address']['street'] = 'Oakroad Rd'
        self.assertFalse(self.repair(entries=[entry])[2])
        entry['aliases'] = ['Oak Rd']
        self.assertEqual(len(self.repair(entries=[entry])[2]), 1)
        entry['aliases'] = ['Oak Rd', 'Oak Road']
        self.assertEqual(len(self.repair(entries=[entry])[2]), 1)

    def test_no_anchor_or_wrong_neighborhood_or_inferred_anchor_stays_unlocated(self):
        for anchor in [None, {**self.anchor,'lon':-72.5}, {**self.anchor,'placed_by':'interpolated'}, {**self.anchor,'placed_by':'parcel'},
                       {**self.anchor,'lon':float('nan')}, {**self.anchor,'street_id':1}]:
            with self.subTest(anchor=anchor):
                rows = [self.target] + ([anchor] if anchor else [])
                self.assertFalse(self.repair(rows=rows)[2])
        self.names[2].city = 'Elsewhere'
        self.assertFalse(self.repair()[2])

    def test_ambiguous_parcels_do_not_choose_first_and_far_duplicate_cannot_match(self):
        duplicate = deepcopy(self.entry)
        duplicate['source']['object_id'] = 2
        self.assertFalse(self.repair(entries=[self.entry,duplicate])[2])
        duplicate['location']['lat'] = 41.2
        self.assertEqual(len(self.repair(entries=[self.entry,duplicate])[2]), 1)

    def test_missing_house_wrong_road_or_virtual_road_never_gets_parcel_placement(self):
        self.target['house'] = ''
        self.assertFalse(self.repair()[2])
        self.target['house'] = '10'
        for sql in ["UPDATE road_span SET name_key='ELM RD'", "UPDATE road_span SET name_key='OAK RD',virtual=1", "UPDATE road_span SET virtual=0,coords='[[-72.5,41.6],[-72.4,41.6]]'"]:
            self.db.execute(sql)
            self.assertFalse(self.repair()[2])

    def test_unconfirmed_address_keeps_explicit_review_provenance_and_no_point(self):
        missing = {k:deepcopy(self.entry[k]) for k in ('address','source')}
        missing['reason'] = 'No confirming municipal parcel; nearby street evidence does not locate the house.'
        clean, omissions, corrections, _ = self.repair(entries=[], missing=[missing])
        self.assertIsNone(clean[0]['lon'])
        self.assertFalse(corrections)
        self.assertIn(missing['reason'], omissions[0]['reason'])
        self.assertEqual(omissions[0]['reviewed_evidence'][0]['source'], missing['source'])

    def test_budget_refusal_is_atomic_and_preserves_all_original_rows(self):
        with mock.patch.object(L,'MAX_CHECKS',0):
            clean, omissions, corrections, warnings = self.repair()
        self.assertEqual(clean, [self.target,self.anchor])
        self.assertEqual(omissions, [self.omission])
        self.assertFalse(corrections)
        self.assertIn('no corrections', warnings[0])

    def test_reviewed_bundle_has_all_seven_valid_parcels_and_five_unconfirmed_addresses(self):
        entries, missing, warnings = L.load_evidence('ct-01')
        self.assertEqual((len(entries),len(missing),warnings),(7,5,[]))
        self.assertTrue(all(e['address']['zip'] is None and e['address']['unit']=='' for e in entries))
        self.assertEqual(L.load_evidence('co-05'), ([],[],[]))

    def test_loader_fails_closed_on_malformed_assets_or_point_outside_parcel(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'evidence.json'
            for field in ('address','location','context','polygon'):
                entry = deepcopy(self.entry)
                entry[field] = []
                path.write_text(json.dumps({'schema':1,'district':'ct-01','entries':[entry]}))
                entries, missing, warnings = L.load_evidence('ct-01',temporary)
                self.assertEqual((entries,missing),([],[]))
                self.assertTrue(warnings)
            entry = deepcopy(self.entry)
            entry['location']['lon'] = -72.5
            path.write_text(json.dumps({'schema':1,'district':'ct-01','entries':[entry]}))
            self.assertTrue(L.load_evidence('ct-01',temporary)[2])
            path.write_text(json.dumps({'schema':1,'district':'ct-01','entries':[self.entry]}))
            self.assertEqual(len(L.load_evidence('ct-01',temporary)[0]),1)
            with mock.patch.object(L,'MAX_BYTES',1):
                self.assertTrue(L.load_evidence('ct-01',temporary)[2])
