"""Focused checks for the strictly local source directory (synthetic data only)."""
import csv
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, override_settings
from shapely.geometry import box

from tools.build_street import BASES, SOURCE_FIELDS, build_database
from .street_views import household, street, preload


class StreetDirectoryTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temporary.name)
        cls.csv_path = cls.directory / "synthetic_20260904.csv"
        cls.database_path = cls.directory / "street.sqlite3"
        cls.geography = ({"080410001001001": box(-104.85, 38.78, -104.76, 38.87)},
                         box(-104.85, 38.78, -104.76, 38.87), {"block_vintage": "synthetic"})
        records = []

        def record(number, household_number, lon=-104.805, lat=38.805):
            row = {field: "" for field in SOURCE_FIELDS}
            row.update(sii_persist_id="RAW-PERSON-" + str(number),
                       sii_statara_id="RAW-SECONDARY-" + str(number),
                       sii_household_id="RAW-HOUSEHOLD-" + str(household_number),
                       sii_first_name="Synthetic", sii_last_name="Person " + str(number),
                       svi_registration_congressional_district="005")
            for basis, prefix in BASES.items():
                row.update({prefix + "_state": "CO", prefix + "_censusid": "080410001001001",
                            prefix + "_latitude": str(lat), prefix + "_longitude": str(lon),
                            prefix + "_geolevel": "ROOFTOP",
                            prefix + "_address": str(household_number) + " Synthetic " + basis})
            row["sci_private_attribute"] = "DO-NOT-RETAIN"
            row["sai_private_score"] = "99"
            return row

        # One large household and 305 additional households inside a narrow viewport.
        records.extend(record(i, 1, -104.806, 38.806) for i in range(101))
        records.extend(record(1000 + i, 1000 + i, -104.805 + i * .000001) for i in range(305))
        for i, (lon, lat) in enumerate([(-104.83, 38.825), (-104.82, 38.825),
                                        (-104.825, 38.83), (-104.825, 38.82),
                                        (-104.819999, 38.825)]):
            records.append(record(2000 + i, 2000 + i, lon, lat))
        invalid = record(3000, 3000)
        invalid["svi_registration_geolevel"] = "PLACE"
        invalid["sii_longitude"] = "-104.9"  # Outside the exact official test block.
        records.append(invalid)
        for i, changes in enumerate([
            {"svi_registration_state": "UT", "sii_state": "UT"},
            {"svi_registration_congressional_district": "004"},
            {"svi_registration_latitude": "nan", "sii_latitude": "nan"},
        ]):
            invalid = record(4000 + i, 4000 + i)
            invalid.update(changes)
            records.append(invalid)
        records.append(dict(records[0]))  # Exact duplicate collapses.
        conflict = record(5000, 5000)
        records.append(conflict)
        records.append({**conflict, "sci_private_attribute": "CHANGED"})
        with cls.csv_path.open("w", newline="", encoding="cp1252") as source:
            writer = csv.DictWriter(source, fieldnames=SOURCE_FIELDS + ["sci_private_attribute", "sai_private_score"])
            writer.writeheader()
            writer.writerows(records)
        with patch("tools.build_street.load_geography", return_value=cls.geography):
            cls.receipt = build_database(cls.csv_path, cls.directory, cls.database_path)
        cls.override = override_settings(DATA_DIR=cls.directory, LOCAL_ADMIN_ACCESS=True, HOSTED=False)
        cls.override.enable()

    @classmethod
    def tearDownClass(cls):
        cls.override.disable()
        cls.temporary.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.factory = RequestFactory()

    def request(self, function, parameters, *, authenticated=True, local=True, remote="127.0.0.1", **headers):
        request = self.factory.get("/api/street" if function == street else "/api/household",
                                   parameters, REMOTE_ADDR=remote, **headers)
        request.user = SimpleNamespace(is_authenticated=authenticated, is_staff=True, is_superuser=True)
        request.local_admin_access = local
        return function(request)

    def viewport(self, **changes):
        return {"bbox": "-104.82,38.79,-104.79,38.82", "basis": "registration", "zoom": "16", **changes}

    def decoded(self, result):
        self.assertEqual(result.status_code, 200)
        return json.loads(result.content)

    def test_preload_pages_cover_all_households_once_without_people_or_attributes(self):
        for basis in BASES:
            after='';rows=[];pages=0
            with patch('server.research.street_views.MAX_PRELOAD_BATCH',100):
                while True:
                    result=self.request(preload,{'basis':basis,'after':after,'source':self.receipt['source_sha256']})
                    page=self.decoded(result);pages+=1;rows.extend(page['rows'])
                    self.assertEqual(result['Cache-Control'],'no-store, private')
                    self.assertEqual(page['columns'],['id','longitude','latitude','count','address'])
                    self.assertEqual(page['total_households'],311);self.assertEqual(page['total_people'],411)
                    self.assertLessEqual(len(page['rows']),100)
                    self.assertTrue(all(len(row)==5 for row in page['rows']))
                    if page['next'] is None:break
                    self.assertGreater(page['next'],after);after=page['next']
            self.assertEqual(pages,4);self.assertEqual(len(rows),311)
            self.assertEqual(len({row[0] for row in rows}),311);self.assertEqual(sum(row[3] for row in rows),411)
            self.assertEqual([row[0] for row in rows],sorted(row[0] for row in rows))
            self.assertNotIn('RAW-',json.dumps(rows));self.assertNotIn('Synthetic Person',json.dumps(rows))

    def test_preload_source_cursors_and_local_access_are_enforced(self):
        params={'basis':'registration','after':'','source':self.receipt['source_sha256']}
        self.assertEqual(self.request(preload,{**params,'source':'0'*64}).status_code,409)
        for change in ({'after':'invalid'},{'basis':'unknown'},{'source':'invalid'},{'person':'anything'}):
            self.assertEqual(self.request(preload,{**params,**change}).status_code,400)
        for options in ({'authenticated':False},{'local':False},{'remote':'192.0.2.1'},{'HTTP_X_FORWARDED_FOR':'127.0.0.1'}):
            self.assertEqual(self.request(preload,params,**options).status_code,403)
        with override_settings(HOSTED=True):self.assertEqual(self.request(preload,params).status_code,403)

    def test_requires_authenticated_direct_local_access_even_for_staff(self):
        for options in ({"authenticated": False}, {"local": False}, {"remote": "192.0.2.1"},
                        {"remote": ""}, {"HTTP_X_FORWARDED_FOR": "127.0.0.1"},
                        {"HTTP_FORWARDED": "for=127.0.0.1"}):
            self.assertEqual(self.request(street, self.viewport(), **options).status_code, 403)
            self.assertEqual(self.request(household, {"id": "h_" + "0" * 64, "basis": "registration"},
                                          **options).status_code, 403)
        self.assertEqual(self.request(street, self.viewport(), remote="::1").status_code, 200)
        with override_settings(HOSTED=True):
            self.assertEqual(self.request(street, self.viewport()).status_code, 403)
        with override_settings(LOCAL_ADMIN_ACCESS=False):
            self.assertEqual(self.request(street, self.viewport()).status_code, 403)

    def test_rejects_invalid_broad_and_unexpected_queries(self):
        for change in ({"bbox": "nan,38.8,-104.8,38.81"}, {"bbox": "1,2,3"},
                       {"bbox": "-104.9,38.8,-104.8,38.81"},
                       {"bbox": "-104.81,38.7,-104.8,38.81"},
                       {"bbox": "-104.8,38.8,-104.81,38.81"},
                       {"bbox": "-1,0,-.99,.01"}, {"zoom": "14.99"}, {"zoom": "nan"},
                       {"basis": "other"}, {"name": "Synthetic"}, {"format": "csv"}):
            self.assertEqual(self.request(street, self.viewport(**change)).status_code, 400)
        self.assertEqual(self.request(street, {"basis": "registration", "zoom": 16}).status_code, 400)
        self.assertEqual(self.request(street, self.viewport(basis=["registration", "residential"])).status_code, 400)
        self.assertEqual(self.request(household, {"id": "' OR 1=1 --", "basis": "registration"}).status_code, 400)

    def test_spatial_boundaries_are_inclusive_and_exact(self):
        result = self.decoded(self.request(street, self.viewport(bbox="-104.83,38.82,-104.82,38.83")))
        self.assertEqual(result["total_households"], 4)
        self.assertEqual(len(result["households"]), 4)
        self.assertFalse(result["truncated"])
        self.assertEqual({(row["longitude"], row["latitude"]) for row in result["households"]},
                         {(-104.83, 38.825), (-104.82, 38.825), (-104.825, 38.83), (-104.825, 38.82)})

    def test_household_and_viewport_caps_are_deterministic_without_attributes(self):
        first_response = self.request(street, self.viewport())
        first = self.decoded(first_response)
        self.assertEqual(first, self.decoded(self.request(street, self.viewport())))
        self.assertEqual(len(first["households"]), 300)
        self.assertTrue(first["truncated"])
        self.assertGreater(first["total_households"], 300)
        self.assertEqual(set(first), {"households", "truncated", "total_households", "source_sha256"})
        self.assertEqual(first["source_sha256"], self.receipt["source_sha256"])
        self.assertEqual(first_response["Cache-Control"], "no-store, private")
        for row in first["households"]:
            self.assertEqual(set(row), {"id", "longitude", "latitude", "count", "address"})
            self.assertRegex(row["id"], r"^h_[a-f0-9]{64}$")
        large = next(row for row in first["households"] if row["count"] == 101)
        members = self.decoded(self.request(household, {"id": large["id"], "basis": "registration"}))
        self.assertEqual(set(members), {"id", "address", "people", "count"})
        self.assertEqual(members["count"], 101)
        self.assertEqual(len(members["people"]), 100)
        self.assertTrue(all(set(person) == {"id", "name"} for person in members["people"]))
        self.assertNotIn("RAW-", json.dumps(members))
        self.assertEqual(self.request(household, {"id": large["id"], "basis": "residential"}).status_code, 404)

    def test_bases_are_separate_and_only_validated_source_locations_survive(self):
        for basis in BASES:
            self.assertEqual(self.receipt["bases"][basis]["people"], 411)
            self.assertEqual(self.receipt["bases"][basis]["households"], 311)
        self.assertEqual(self.receipt["input"]["duplicate_rows_collapsed"], 1)
        self.assertEqual(self.receipt["input"]["conflicting_ids_quarantined"], 1)
        registration = self.decoded(self.request(street, self.viewport()))
        residential = self.decoded(self.request(street, self.viewport(basis="residential")))
        self.assertFalse({row["id"] for row in registration["households"]}
                         & {row["id"] for row in residential["households"]})
        self.assertTrue(all(row["address"].endswith("residential") for row in residential["households"]))
        with sqlite3.connect(self.database_path) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(tables, {"metadata", "households", "people"})
            columns = {row[1] for row in connection.execute("PRAGMA table_info(people)")}
            self.assertEqual(columns, {"household_id", "id", "name"})
            text = "\n".join(connection.iterdump())
            self.assertNotIn("RAW-", text)
            self.assertNotIn("DO-NOT-RETAIN", text)
            self.assertNotIn("sai_private_score", text)

    def test_missing_or_invalid_database_is_503_and_never_created(self):
        with tempfile.TemporaryDirectory() as temporary:
            with override_settings(DATA_DIR=Path(temporary)):
                self.assertEqual(self.request(street, self.viewport()).status_code, 503)
                self.assertFalse((Path(temporary) / "street.sqlite3").exists())
                (Path(temporary) / "street.sqlite3").write_text("invalid database")
                self.assertEqual(self.request(street, self.viewport()).status_code, 503)
                self.assertEqual(self.request(household, {"id": "h_" + "0" * 64,
                                                         "basis": "registration"}).status_code, 503)

    def test_readonly_connections_and_atomic_rebuild_preserve_salt_and_backups(self):
        from .street_views import database
        with database() as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM people")
        before = self.decoded(self.request(street, self.viewport()))
        with patch("tools.build_street.load_geography", return_value=self.geography):
            rebuild = build_database(self.csv_path, self.directory, self.database_path)
            first_backups = list((self.directory / "street-backups").glob("*.sqlite3"))
            self.assertTrue(rebuild["backup_created"])
            self.assertEqual(len(first_backups), 1)
            previous = first_backups[0].read_bytes()
            build_database(self.csv_path, self.directory, self.database_path)
        self.assertEqual(len(list((self.directory / "street-backups").glob("*.sqlite3"))), 2)
        self.assertEqual(first_backups[0].read_bytes(), previous)
        self.assertEqual(before, self.decoded(self.request(street, self.viewport())))
        with patch("tools.build_street.load_geography", side_effect=ValueError("test failure")):
            with self.assertRaises(ValueError):
                build_database(self.csv_path, self.directory, self.database_path)
        self.assertEqual(before, self.decoded(self.request(street, self.viewport())))
