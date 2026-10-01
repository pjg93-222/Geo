import json
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from .views import protected, snapshot_for


@protected()
@require_GET
def credit_quality(request):
    # Boundary-only snapshot extensions may reuse an audit of the identical file.
    if settings.HOSTED or not getattr(request, 'local_admin_access', False):
        return JsonResponse({'error': 'Credit diagnostics are available in the local app.'}, status=403)
    path = settings.DATA_DIR/'credit-quality.json'
    if not path.exists():
        return JsonResponse({'error': 'The credit-field audit has not been prepared.'}, status=404)
    data = json.loads(path.read_text(encoding='utf-8'))
    snapshot = snapshot_for(request)
    if not snapshot or snapshot.manifest.get('source_sha256') != data['source_sha256']:
        return JsonResponse({'error': 'This audit belongs to a different source snapshot.'}, status=409)
    response = JsonResponse({**data,'audited_snapshot':data['snapshot'],'snapshot':snapshot.key})
    response['Cache-Control'] = 'private, no-store'
    return response
