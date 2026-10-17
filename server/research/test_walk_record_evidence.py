"""Private reviewed number recovery stays source/person bound and parcel conditional."""
import json
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from . import walk_address_views as A
from . import walk_record_evidence as R


class RecordEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path=Path(self.temporary.name)/R.FILENAME
        self.person='p_'+'a'*64
        self.source='b'*64
        self.reg={'address':'OAK ROAD','house':'','street':'Oak Rd','unit':'','city':'Town','state':'CT','zip':'01234'}
        home={**self.reg,'address':'15 OAK RD','house':'15'}
        self.entry={'person_id':self.person,'source_row':25,'identity_verified':True,
                    'registration':self.reg,'residential':home,'mail':dict(home)}
        self.bundle={'schema':1,'district':'ct-01','source_sha256':self.source,'records':[self.entry]}
        self.db=sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE person(person_id TEXT,stop_id INTEGER,unit TEXT)')
        self.db.execute('INSERT INTO person VALUES(?,1,?)',(self.person,''))
        self.names={1:SimpleNamespace(key='OAK RD',city='Town')}
        self.store=SimpleNamespace(conn=self.db,info=SimpleNamespace(source_sha256=self.source),directory=lambda:SimpleNamespace(by_id=self.names))
        self.row={'person_id':self.person,'stop_id':1,'street_id':1,'house':'','house_int':None,'parity':None,'unit':'','zip':'01234',
                  'lon':None,'lat':None,'placed_by':'unlocated'}

    def save(self, bundle=None):
        self.path.write_text(json.dumps(self.bundle if bundle is None else bundle))

    def prepare(self, rows=None):
        return R.prepare(self.store, rows if rows is not None else [self.row], 'ct-01','CT',path=self.path)

    def test_missing_overlay_leaves_records_identical(self):
        self.assertEqual(self.prepare(),([self.row],{},[]))

    def test_only_blank_number_is_recovered_and_original_fields_are_preserved(self):
        self.save()
        prepared,proofs,warnings=self.prepare()
        self.assertEqual((prepared[0]['house'],prepared[0]['house_int'],prepared[0]['parity']),('15',15,1))
        self.assertEqual(self.row['house'],'')
        self.assertIsNone(prepared[0]['lon'])
        self.assertEqual(proofs[self.person]['registration'],self.reg)
        self.assertEqual(proofs[self.person]['source_sha256'],self.source)
        self.assertRegex(proofs[self.person]['overlay_sha256'],r'^[a-f0-9]{64}$')
        self.assertEqual(warnings,[])
        changed={**self.row,'house':'17','house_int':17,'parity':1}
        self.assertEqual(self.prepare([changed])[0],[changed])

    def test_source_person_city_state_zip_and_unit_cannot_be_broadened(self):
        self.save()
        for changed in ({**self.row,'person_id':'p_'+'c'*64},{**self.row,'zip':'99999'},{**self.row,'unit':'2'}):
            self.assertFalse(self.prepare([changed])[1])
        self.names[1].city='Elsewhere'
        self.assertFalse(self.prepare()[1])
        self.names[1].city='Town'
        self.store.info.source_sha256='c'*64
        self.assertFalse(self.prepare()[1])
        self.store.info.source_sha256=self.source
        self.assertFalse(R.prepare(self.store,[self.row],'ct-01','MA',path=self.path)[1])
        self.assertFalse(R.prepare(self.store,[self.row],'co-05','CT',path=self.path)[1])

    def test_disagreeing_alternate_addresses_or_full_address_components_fail_closed(self):
        for basis,field,value in [('mail','house','16'),('mail','street','Elm Rd'),('residential','city','Elsewhere'),
                                  ('registration','house','14'),('registration','address','12 OAK RD'),
                                  ('residential','unit','2'),('mail','address','17 OAK RD')]:
            bundle=deepcopy(self.bundle)
            bundle['records'][0][basis][field]=value
            self.save(bundle)
            prepared,proofs,warnings=self.prepare()
            self.assertEqual(prepared,[self.row])
            self.assertFalse(proofs)
            self.assertTrue(warnings)

    def test_unaudited_member_of_shared_stop_prevents_recovery(self):
        self.save()
        other='p_'+'c'*64
        self.db.execute('INSERT INTO person VALUES(?,1,?)',(other,''))
        self.assertFalse(self.prepare()[1])
        self.assertFalse(self.prepare([self.row,{**self.row,'person_id':other}])[1])
        self.db.execute("UPDATE person SET unit='2' WHERE person_id=?",(other,))
        self.assertFalse(self.prepare()[1])

    def test_failed_parcel_validation_rolls_back_number_and_success_records_original_registration(self):
        self.save()
        prepared,proofs,_=self.prepare()
        self.assertEqual(R.finish([self.row],prepared,[],proofs),[self.row])
        repaired=[{**prepared[0],'lon':-72.88,'lat':41.64,'placed_by':'parcel'}]
        correction={'person_ids':[self.person],'house':'','reason':'Parcel representative point.'}
        result=R.finish([self.row],repaired,[correction],proofs)
        self.assertEqual(result,repaired)
        self.assertEqual(correction['house'],'15')
        self.assertEqual(correction['registration_recovery']['registration']['house'],'')
        self.assertIn('Registration house number was blank',correction['reason'])

    def test_door_evidence_reports_recovered_number_only_when_parcel_repair_succeeds(self):
        self.save()
        prepared,proofs,_=self.prepare()
        door={'placed_by':'parcel','voters':[{'person_id':self.person}]}
        correction={'person_ids':[self.person],'house':'','reason':'Parcel representative point.',
                    'codes':['inferred_no_house','parcel_representative_point']}
        R.finish([self.row],[{**prepared[0],'placed_by':'parcel'}],[correction],proofs)
        self.assertEqual(A.door_evidence(door,[],correction,[],{self.person:'interpolated'}),
                         {'version':1,'address_status':'recovered_number','location_precision':'parcel','decision':'review',
                          'reason_codes':['inferred_no_house','parcel_representative_point','number_recovered'],
                          'source_placement':'interpolated','original_house':''})
        # Rolled back: no correction, the blank-number omission stays unresolved and claims no recovery.
        self.assertEqual(R.finish([self.row],prepared,[],proofs),[self.row])
        omission={'person_ids':[self.person],'codes':['inferred_no_house']}
        evidence=A.door_evidence({**door,'placed_by':'unlocated'},[omission],None,['no_house_number'],{self.person:'interpolated'})
        self.assertEqual((evidence['address_status'],evidence['original_house'],evidence['reason_codes']),
                         ('unresolved',None,['inferred_no_house','no_house_number']))

    def test_duplicate_or_malformed_private_evidence_never_applies_partially(self):
        bundle=deepcopy(self.bundle)
        bundle['records'].append(deepcopy(bundle['records'][0]))
        self.save(bundle)
        self.assertFalse(self.prepare()[1])
        bundle=deepcopy(self.bundle)
        bundle['records'][0]['registration']=[]
        self.save(bundle)
        self.assertFalse(self.prepare()[1])
