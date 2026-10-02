"""Readiness for the local Terrier app backed by the verified stored dataset."""
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from . import scoresdb
from .v3_area_views import v4_snapshot, store_status


@require_GET
def health(request):
    try:
        found = v4_snapshot()
        if found is None:
            raise scoresdb.StoreError('No recorded snapshot is active.')
        status, info = store_status(found[1])
        if status['status'] != 'ok' or info is None:
            raise scoresdb.StoreError(status['note'])
        if info.meta['w1'].get('status') != 'verified':
            raise scoresdb.StoreError('Terrier requires a verified source dataset.')
        scoresdb.connection(contacts=True)
        payload = {'app': 'Terrier', 'mode': 'recorded', 'version': 2,
                   'people': info.n, 'households': info.meta['n_households'],
                   'snapshot': found[0], 'names_masked': settings.DEMO_MODE}
        response = JsonResponse(payload)
    except (scoresdb.StoreError, OSError):
        response = JsonResponse({'app': 'Terrier', 'mode': 'recorded', 'version': 2,
                                 'error': 'The verified local dataset is unavailable.'}, status=503)
    response['Cache-Control'] = 'no-store'
    return response
