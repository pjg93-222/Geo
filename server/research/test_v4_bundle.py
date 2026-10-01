"""cu-summary-v4: the validator accepts the synthetic row-framework bundle, rejects every planted violation, and the
importer fails closed on a checksum mismatch. Synthetic data only."""
import copy
import io
import json
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from tools import area_layers as AL

from tools import glassbox

from . import bundles, v3_cache
from .models import Snapshot
from .v3_testkit import build_world, retarget, write_zip
from .test_area_indices import hierarchy_bundle


def rows_of(payload, basis='registration', level='block', status='ok'):
    return [r for r in payload['area_indices.json']['rows']
            if r['basis'] == basis and r['level'] == level and r['status'] == status]


class V4ValidatorTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.world = build_world(Path(cls.temporary.name))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()
        super().tearDownClass()

    def fresh(self):
        return copy.deepcopy(self.world.payload)

    def reject(self, mutate, *, rehash=True, message=None):
        payload = self.fresh()
        mutate(payload)
        if rehash:
            retarget(payload)
        with self.assertRaises(ValueError) as caught:
            bundles.validate(payload)
        if message:
            self.assertIn(message, str(caught.exception))

    # -- acceptance ---------------------------------------------------------------------------------------------
    def test_valid_bundle_is_accepted_and_v3_still_validates(self):
        manifest = bundles.validate(self.fresh())
        self.assertEqual(manifest['schema'], 'cu-summary-v4')
        self.assertEqual(bundles.validate(hierarchy_bundle())['schema'], 'cu-summary-v3')

    def test_snapshot_key_follows_from_its_inputs(self):
        manifest = self.world.payload['manifest.json']
        rf = manifest['row_framework']
        self.assertEqual(manifest['snapshot'], AL.snapshot_key(
            rf['base_snapshot'], rf['area_table_sha256'], rf['suppressed_ids_sha256'], rf['framework_config_sha256']))
        self.reject(lambda p: p['manifest.json'].update(snapshot='co-cd005-000000000000'), message='Snapshot key')

    # -- the four planted violations of W3 ----------------------------------------------------------------------
    def test_w3_cell_of_nineteen_is_rejected(self):
        def mutate(p):
            rows_of(p)[0]['n_rows'] = 19
        self.reject(mutate)

    def test_w3_person_level_key_is_rejected(self):
        self.reject(lambda p: rows_of(p)[0].update(person_id='p_' + 'a' * 64))
        self.reject(lambda p: p['area_indices.json']['rows'][0].update(model_source_row=7))

    def test_w3_leaked_withheld_value_is_rejected(self):
        def mutate(p):
            withheld = rows_of(p, status='withheld')[0]
            full = rows_of(p)[0]
            withheld['n_rows'] = full['n_rows']
        self.reject(mutate, message='withheld')

        def mutate_groups(p):
            withheld = rows_of(p, status='withheld')[0]
            withheld['groups'] = copy.deepcopy(rows_of(p)[0]['groups'])
        self.reject(mutate_groups)

    def test_w3_missing_complementary_suppression_is_rejected(self):
        def mutate(p):
            # withhold exactly one precinct of a partition that has none: the file total minus the published rest
            # would give that precinct back, so the second area must be withheld too
            row = rows_of(p, level='precinct')[0]
            row.update(status='withheld', n_rows=None, n_households=None, groups=None,
                       tract=None, n_tracts=None, n_blocks=None)
        self.reject(mutate, message='subtraction')

    # -- the rest of the contract --------------------------------------------------------------------------------
    def test_residence_blocks_and_the_hatch_column_are_rejected(self):
        def residence_block(p):
            row = copy.deepcopy(rows_of(p)[0])
            row['basis'] = 'residence'
            p['area_indices.json']['rows'].append(row)
        self.reject(residence_block)

        def hatch(p):
            p['area_indices.json']['columns']['cua'].append('cua_hatch_cpi_blank')
        self.reject(hatch, message='columns')

    def test_band_and_percentile_must_follow_the_values(self):
        columns = {name: k for k, name in enumerate(AL.COLUMNS['ovr'])}

        def band(p):
            row = rows_of(p, level='tract')[0]
            row['groups']['ovr'][columns['band']] = 1 + (row['groups']['ovr'][columns['band']] % 5)
        self.reject(band, message='Band')

        def percentile(p):
            row = rows_of(p, level='precinct')[0]
            row['groups']['soi'][columns['area_pct']] += 7.0
            row['groups']['soi'][columns['band']] = AL.band(row['groups']['soi'][columns['area_pct']])
        self.reject(percentile, message='percentile')

    def test_lean_shares_must_sum_to_one(self):
        columns = {name: k for k, name in enumerate(AL.COLUMNS['lean'])}

        def shares(p):
            rows_of(p)[0]['groups']['lean'][columns['share_band_p1']] += 0.2
        self.reject(shares, message='Lean band shares')

        def registration(p):
            row = rows_of(p)[0]
            row['groups']['lean'][columns['share_reg_D']] = 0.9
            row['groups']['lean'][columns['share_reg_R']] = 0.9
        self.reject(registration)

    def test_tract_remainder_of_one_to_nineteen_is_rejected(self):
        def mutate(p):
            tract = rows_of(p, level='tract')[0]
            blocks = [r for r in rows_of(p) if r['parent_id'] == tract['id']]
            blocks[0]['n_rows'] = max(20, blocks[0]['n_rows'])
            # leave a remainder of 5 people between the tract and its published blocks
            total = sum(b['n_rows'] for b in blocks)
            tract['n_rows'] = total + 5
        self.reject(mutate, message='remainder')

    def test_published_area_without_polygon_is_rejected(self):
        def mutate(p):
            ident = rows_of(p)[0]['id']
            del p['geometry.json']['block'][ident]
        self.reject(mutate, message='polygon')

    def test_hash_and_frozen_input_mismatches_are_rejected(self):
        self.reject(lambda p: p['area_indices.json']['rows'][0].update(label='Block 0000'), rehash=True)
        # editing a value without refreshing the manifest hash
        def edit(p):
            rows_of(p)[0]['groups']['ovr'][0] += 0.001
        self.reject(edit, rehash=False, message='checksum')
        self.reject(lambda p: p['manifest.json']['row_framework'].update(framework_config_sha256='0' * 64))
        self.reject(lambda p: p['manifest.json']['row_framework'].update(
            d1_audit={'exactly_disclosed': 1, 'symdiff_violations': 0, 'remainder_violations': 0}))
        self.reject(lambda p: p['adjacency.json']['tract'].update({'x': []}), rehash=False)

    def test_asymmetric_adjacency_is_rejected(self):
        def mutate(p):
            graph = p['adjacency.json']['precinct']
            ident = sorted(graph)[0]
            other = next(i for i in sorted(graph) if i != ident and i not in graph[ident])
            graph[ident] = sorted(graph[ident] + [other])
        self.reject(mutate, message='symmetric')


@override_settings(LOCAL_ADMIN_ACCESS=True, HOSTED=False)
class V4ImportTests(TestCase):
    """The importer is the trust boundary: a v4 ZIP is imported only with the sha256 of its bytes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.world = build_world(Path(cls.temporary.name) / 'world')
        cls.data = Path(cls.temporary.name) / 'data'
        cls.data.mkdir()
        cls.override = override_settings(DATA_DIR=cls.data)
        cls.override.enable()

    @classmethod
    def tearDownClass(cls):
        cls.override.disable()
        cls.temporary.cleanup()
        super().tearDownClass()

    def setUp(self):
        v3_cache.forget()
        bundles.load.cache_clear()

    def test_v4_import_needs_a_matching_sha256(self):
        with self.assertRaises(ValueError):
            bundles.import_bundle(self.world.zip_path)                              # no checksum at all
        with self.assertRaises(ValueError):
            bundles.import_bundle(self.world.zip_path, expected_sha256='0' * 64)    # wrong checksum
        with self.assertRaises(ValueError):
            bundles.import_bundle(self.world.zip_path, expected_sha256='nothex')
        self.assertFalse(Snapshot.objects.exists())

    def test_import_activates_and_prepares_every_derived_payload(self):
        snap = bundles.import_bundle(self.world.zip_path, expected_sha256=self.world.zip_sha256)
        self.assertEqual(snap.key, self.world.snapshot)
        self.assertTrue(Snapshot.objects.get(key=snap.key).active)
        derived = self.data / 'snapshots' / snap.key / 'derived'
        names = {p.name for p in derived.iterdir()}
        for basis, levels in AL.BASIS_LEVELS.items():
            for level in levels:
                self.assertIn('%s_areas_%s_%s.json.gz' % (v3_cache.DERIVED_VERSION, basis, level), names)
        for level in AL.LEVEL_ORDER:
            for variant in ('coarse', 'fine'):
                self.assertIn('%s_geometry_%s_%s.json' % (v3_cache.DERIVED_VERSION, level, variant), names)
        self.assertIn('%s_meta.json' % v3_cache.DERIVED_VERSION, names)
        # re-importing the same bytes is idempotent; a different ZIP under the same key is refused
        again = bundles.import_bundle(self.world.zip_path, expected_sha256=self.world.zip_sha256)
        self.assertEqual(again.key, snap.key)

    def test_tampered_zip_bytes_fail_before_anything_is_read(self):
        copy_path = Path(self.temporary.name) / 'tampered.zip'
        data = bytearray(self.world.zip_path.read_bytes())
        data[len(data) // 2] ^= 0xFF
        copy_path.write_bytes(bytes(data))
        with self.assertRaises(ValueError) as caught:
            bundles.import_bundle(copy_path, expected_sha256=self.world.zip_sha256)
        self.assertIn('checksum', str(caught.exception))

    def test_command_uses_the_sidecar_and_refuses_without_one(self):
        directory = Path(self.temporary.name) / 'cmd'
        directory.mkdir()
        path = directory / self.world.zip_path.name
        path.write_bytes(self.world.zip_path.read_bytes())
        sidecar = directory / (path.name + '.sha256')
        events_dir, mirror = directory / 'events', directory / 'events.jsonl'
        with patch.object(glassbox, 'EVENT_DIR', events_dir), patch.object(glassbox, 'MIRROR', mirror):
            with self.assertRaises(CommandError):
                call_command('import_summary', str(path), stdout=io.StringIO())
            sidecar.write_text('%s *%s%s' % ('f' * 64, path.name, chr(10)), encoding='ascii')
            with self.assertRaises(CommandError):
                call_command('import_summary', str(path), stdout=io.StringIO())
            sidecar.write_text('%s *%s%s' % (self.world.zip_sha256, path.name, chr(10)), encoding='ascii')
            out = io.StringIO()
            call_command('import_summary', str(path), stdout=out)
        self.assertEqual(out.getvalue().strip(), self.world.snapshot)
        events = [json.loads(e) for e in (directory / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(events[-1]['status'], 'completed')
        self.assertIn('failed', [e['status'] for e in events])         # the refused import was reported too

    def test_v3_bundles_still_import_without_a_checksum(self):
        payload = hierarchy_bundle()
        path = Path(self.temporary.name) / 'v3.zip'
        write_zip(payload, path)
        self.assertEqual(bundles.import_bundle(path).key, payload['manifest.json']['snapshot'])
