from django.urls import path, re_path
from server.research import views as v
from server.research import street_views as street
from server.research.quality_views import credit_quality
from server.research.contact_views import contacts, person
from server.research.road_views import roads, area_focus
from server.research import v3_area_views as v3a, v3_people_views as v3p
from server.research.v3_http import error as v3_error
v3_routes = [
    path('api/v3/meta', v3a.meta),
    re_path(r'^api/v3/areas/(?P<snap>co-cd005-[a-f0-9]{12})/(?P<basis>registration|residence)/(?P<level>block|precinct|tract|zip)\.json$', v3a.areas),
    re_path(r'^api/v3/geometry/(?P<snap>co-cd005-[a-f0-9]{12})/(?P<level>block|precinct|tract|zip)/(?P<variant>coarse|fine)\.json$', v3a.geometry),
    path('api/v3/people', v3p.people), path('api/v3/person', v3p.person), path('api/v3/household', v3p.household),
    path('api/v3/street', v3p.street), path('api/v3/search', v3p.search),
    re_path(r'^api/v3/.*$', lambda request: v3_error('Unknown v3 endpoint.', 404)),   # JSON, never the page shell
]
quality_routes = [path('api/street-preload',street.preload),path('api/index-detail',v.index_detail),path('api/credit-quality', credit_quality), path('api/contacts',contacts), path('api/person',person), path('api/roads',roads), path('api/area-focus',area_focus)]
urlpatterns=v3_routes+quality_routes+[path('api/street',street.street),path('api/household',street.household),path('api/auth/session',v.who),path('api/auth/login',v.signin),path('api/auth/logout',v.signout),path('api/auth/accept',v.accept_invitation),path('api/accounts',v.accounts),path('api/snapshots',v.snapshots),path('api/snapshots/import',v.upload),path('api/snapshots/activate',v.activate),path('api/summaries',v.summaries),path('api/geometry',v.geometry),path('api/criteria',v.criteria),path('api/views',v.saved_views),path('api/health',v.health),re_path(r'^(?P<path>.*)$',v.frontend)]
