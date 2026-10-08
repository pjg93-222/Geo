"""Source and criteria metadata for the local Terrier workspace."""
from .models import Snapshot
from .street_views import local_only, response
from .terrier_contacts import SourceStoreError, coverage, source_errors, verified_stores
from .v3_http import params


def field_catalog(catalog):
    """Display definitions come from the active score store, not the UI's dummy catalog."""
    special = {'sci_owner_renter': 'owner', 'sii_homeowner': 'homeowner',
               'sii_dwelling_type': 'dwelling', 'sci_number_of_children_in_household': 'children',
               'sci_number_of_adults_in_household': 'int', 'household_size': 'int',
               'svi_party_registration': 'party'}
    fields = []
    for field in catalog:
        group = field['group']
        name = field['name']
        kind = special.get(name)
        if kind is None:
            kind = 'lean-yn' if group == 'lean' else 'yn' if field['kind'] == 'binary' else 'score'
        fields.append({'key': name, 'group': group, 'label': field['label'], 'kind': kind,
                       'w': 0 if group == 'lean' else field['points'] / 100})
    return fields


@local_only
@source_errors
def workspace(request):
    params(request, set())
    with verified_stores() as (connection, meta):
        contacts, scores, street = (meta[key] for key in ('contacts', 'scores', 'street'))
        source = contacts['source_sha256']
        snapshot = Snapshot.objects.filter(active=True).first()
        if snapshot is None:
            raise SourceStoreError('There is no active source snapshot.')
        manifest = snapshot.manifest
        if (manifest.get('source_sha256') != source
                or manifest.get('row_framework', {}).get('area_table_sha256') != scores.get('area_table_sha256')):
            raise SourceStoreError('The active map snapshot and source stores do not match.', 409)
        counts = coverage()
        people = connection.execute('SELECT COUNT(*) FROM scores.person').fetchone()[0]
        households = connection.execute('SELECT COUNT(*) FROM scores.household').fetchone()[0]
        if people != scores.get('n_people') or households != scores.get('n_households'):
            raise SourceStoreError('The score store counts do not match its metadata.')
        geography = manifest.get('geography', {})
        geometry = manifest.get('geometry_method', {})
        layers = [
            {'key': 'people', 'origin': manifest.get('source_name') or 'Supplied source file',
             'detail': 'Recorded contacts and existing stored profile values, joined by the verified person crosswalk.'},
            {'key': 'areas', 'origin': 'Active stored area table',
             'detail': 'Area summaries from the matching source snapshot; every area is shown, small areas with a note.'},
        ]
        for key, vintage in (('block', 'block_vintage'), ('tract', 'block_vintage'),
                             ('precinct', 'block_vintage'), ('zip', 'zcta_vintage')):
            layers.append({'key': key, 'origin': geography.get(vintage) or 'Active snapshot geography',
                           'detail': geometry.get(key) or 'Geometry supplied by the active snapshot.'})
        layers.extend([
            {'key': 'district', 'origin': geography.get('district_vintage') or 'Active snapshot district',
             'detail': 'District boundary supplied by the active snapshot.'},
            {'key': 'roads', 'origin': geography.get('street_vintage') or 'Active snapshot roads',
             'detail': geography.get('street_method') or 'Road geometry supplied by the active snapshot.'},
        ])
        # Count only snapshots belonging to these source/profile stores.
        available = sum(1 for item in Snapshot.objects.all() if
                        item.manifest.get('source_sha256') == source and
                        item.manifest.get('row_framework', {}).get('area_table_sha256') == scores.get('area_table_sha256'))
        payload = {'mode': 'local-source', 'source_sha256': source, 'people': people, 'households': households,
                   'contacts': counts, 'snapshot': snapshot.key, 'sourceName': manifest.get('source_name'),
                   'sourceDate': manifest.get('source_date'), 'builtAt': scores.get('built_utc'),
                   'fields': field_catalog(scores['field_catalog']), 'layers': layers,
                   'basis': 'registration', 'availableSnapshots': available,
                   'locationCoverage': street.get('bases', {}),
                   'stores': [
                       {'name': 'contacts.sqlite3', 'records': counts['records'], 'source_sha256': source},
                       {'name': 'scores.sqlite3', 'records': people, 'source_sha256': source},
                       {'name': 'street.sqlite3', 'records': sum(value.get('households', 0) for value in street.get('bases', {}).values()),
                        'source_sha256': source},
                   ]}
    return response(payload)
