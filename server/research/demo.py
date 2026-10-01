"""Demo mode for the v3 preview (display-only pseudonymisation).

CU_DEMO=1 (settings.DEMO_MODE) forces demo mode for the whole instance, and `?demo=1` turns it on for one request. Demo
mode changes what is shown, never what is computed: scores, standings, grades, ages and area ids are real; names are
synthetic (tools/demo_names.py, served by server/research/v3_people_views.py); addresses, emails and phones are never
sent by any v3 endpoint; street markers are drawn at a synthetic point inside their block (v3_demo_geo.py).

This middleware is the safety floor under all of it:
  * the legacy personal endpoints, which return real names and addresses from the local contacts layer, answer 403 for
    every demo request;
  * every /api/v3/ response of a demo request carries `X-CU-Demo: 1`, so a screenshot guard can check the mode of what
    it is looking at.
Nothing here reads or returns a name.
"""
from django.conf import settings
from django.http import JsonResponse

LEGACY_PERSONAL_PATHS = frozenset({
    '/api/contacts', '/api/person', '/api/household', '/api/street', '/api/street-preload',
})


class DemoGuardMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (settings.DEMO_MODE or request.GET.get('demo') == '1') and request.path.rstrip('/') in LEGACY_PERSONAL_PATHS:
            response = JsonResponse({'error': 'Unavailable in demo mode'}, status=403)
            response['Cache-Control'] = 'no-store, private'
            response['Pragma'] = 'no-cache'
            response['X-CU-Demo'] = '1'
            return response
        response = self.get_response(request)
        if request.path.startswith('/api/v3/') and (settings.DEMO_MODE or request.GET.get('demo') == '1'):
            response['X-CU-Demo'] = '1'
        return response
