from django.conf import settings
from django.urls import path, re_path
from tools import districts as D
from server.research import views as v
from server.research import street_views as street
from server.research.quality_views import credit_quality
from server.research.contact_views import contacts, person
from server.research.road_views import roads, area_focus
from server.research import v3_area_views as v3a, v3_people_views as v3p
from server.research.v3_http import error as v3_error
from server.research.terrier_health import health as terrier_health
from server.research import terrier_contacts, terrier_workspace
from server.research import walk_views, walk_address_views, walk_image_views
from server.research import area_street_views
SNAPSHOT = D.snapshot_pattern(settings.DISTRICT)   # one district per process: its snapshot keys only
v3_routes = [
    path('api/v3/contacts', terrier_contacts.contacts),
    path('api/v3/contacts/export', terrier_contacts.export_contacts),
    path('api/v3/workspace', terrier_workspace.workspace),
    path('api/v3/meta', v3a.meta),
    path('api/v3/districts', v3a.districts),
    path('api/v3/features', v3a.features), path('api/v3/density', v3a.density), path('api/v3/untapped', v3a.untapped),
    re_path(r'^api/v3/areas/(?P<snap>' + SNAPSHOT + r')/(?P<basis>registration|residence)/(?P<level>block|precinct|tract|zip)\.json$', v3a.areas),
    re_path(r'^api/v3/parties/(?P<snap>' + SNAPSHOT + r')/(?P<basis>registration|residence)/(?P<level>block|precinct|tract|zip)\.json$', v3a.parties),
    re_path(r'^api/v3/geometry/(?P<snap>' + SNAPSHOT + r')/(?P<level>block|precinct|tract|zip|state_house|state_senate)/(?P<variant>coarse|fine)\.json$', v3a.geometry),
    path('api/v3/people', v3p.people), path('api/v3/person', v3p.person), path('api/v3/household', v3p.household),
    path('api/v3/street', v3p.street), path('api/v3/search', v3p.search),
    path('api/v3/walk/options', walk_views.options), path('api/v3/walk/streets', walk_views.streets),
    path('api/v3/walk/public-streets/catalogs', area_street_views.catalogs),
    path('api/v3/walk/public-streets/query', area_street_views.query),
    path('api/v3/walk/match', walk_views.match), path('api/v3/walk/list', walk_views.walk_list),
    path('api/v3/walk/address-match', walk_address_views.address_match),
    path('api/v3/walk/address-doors', walk_address_views.address_doors),
    path('api/v3/walk/address-list', walk_address_views.address_list),
    path('api/v3/walk/image-context', walk_image_views.image_context),
    path('api/v3/walk/image-resolve', walk_image_views.image_resolve),
    path('api/v3/walk/overview', walk_views.overview), path('api/v3/walk/plan', walk_views.plan),
    path('api/v3/walk/scores', walk_views.voter_scores), path('api/v3/walk/lists', walk_views.lists),
    path('api/v3/walk/lists/<slug:key>', walk_views.saved_list), path('api/v3/walk/dispositions', walk_views.dispositions),
    re_path(r'^api/v3/.*$', lambda request: v3_error('Unknown v3 endpoint.', 404)),   # JSON, never the page shell
]
quality_routes = [path('api/street-preload',street.preload),path('api/index-detail',v.index_detail),path('api/credit-quality', credit_quality), path('api/contacts',contacts), path('api/person',person), path('api/roads',roads), path('api/area-focus',area_focus)]
urlpatterns=[path('_terrier/health',terrier_health)]+v3_routes+quality_routes+[path('api/street',street.street),path('api/household',street.household),path('api/auth/session',v.who),path('api/auth/login',v.signin),path('api/auth/logout',v.signout),path('api/auth/accept',v.accept_invitation),path('api/accounts',v.accounts),path('api/snapshots',v.snapshots),path('api/snapshots/import',v.upload),path('api/snapshots/activate',v.activate),path('api/summaries',v.summaries),path('api/geometry',v.geometry),path('api/criteria',v.criteria),path('api/views',v.saved_views),path('api/health',v.health),path('version',v.version),re_path(r'^(?P<path>.*)$',v.frontend)]
