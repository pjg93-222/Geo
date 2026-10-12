"""Hosted field access: which person-level routes a district's invited staff may use, and nothing else. Synthetic voters only
(walk_testkit): the walk world is built once, lists are saved the local way, then read and answered the hosted way."""
import json
import re
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase, override_settings

from tools import build_walk as B
from tools import container_start

from . import access_testkit as K
from . import field_access as FA
from . import test_walk_api as base
from . import walk_store as W
from .models import AuditEvent, Disposition, Invitation, WalkList

JSON = 'application/json'
STAFF = 'field@example.test'
BOSS = 'manager@example.test'
OTHER = 'volunteer@example.test'
TODAY = '2026-10-01'

# the routes that build and save a list (field managers only): (method, url)
BUILDER = [
    ('get', '/api/v3/walk/options?level=ss'), ('get', '/api/v3/walk/streets?q=ced'), ('get', '/api/v3/walk/list?ss=010'),
    ('get', '/api/v3/walk/overview?ss=010'), ('get', '/api/v3/walk/plan?ss=010'),
    ('post', '/api/v3/walk/match'), ('post', '/api/v3/walk/lists'),
]
# every other person-level route, closed to field staff and to field managers alike: (method, url)
CLOSED_TO_ALL = [
    ('post', '/api/v3/walk/scores'),
    ('get', '/api/v3/people'), ('get', '/api/v3/person'), ('get', '/api/v3/household'), ('get', '/api/v3/street'),
    ('get', '/api/v3/search?q=ab'), ('get', '/api/v3/contacts'), ('get', '/api/v3/contacts/export'),
    ('get', '/api/v3/workspace'),
    ('get', '/api/street'), ('get', '/api/household'), ('get', '/api/street-preload'), ('get', '/api/contacts'),
    ('get', '/api/person'), ('get', '/api/credit-quality'),
]
CLOSED = BUILDER + CLOSED_TO_ALL        # closed to field staff


def staff(name=STAFF, *, group=True):
    account = User.objects.create_user(name, password='correct horse battery staple')
    if group:
        FA.grant(account)
    return account


def manager(name=BOSS, *, also_staff=False):
    account = User.objects.create_user(name, password='correct horse battery staple')
    FA.grant(account, FA.MANAGER)
    if also_staff:
        FA.grant(account)
    return account


class FieldAccessTests(base.WalkCase):
    """Local owner prepares the lists; field staff use them hosted."""

    def setUp(self):
        super().setUp()
        FA.reset_rate_limits()
        key = self.make_list({'streets': '%d:both' % self.street_id('Ridgeview Ct')})['list']['key']
        self.key = key
        self.voters = base.voters_of(self.json('/api/v3/walk/lists/' + key))
        self.person = self.voters[0]['person_id']

    def call(self, made, method, url, body=None):
        if method == 'get':
            return made.get(url)
        return made.post(url, data=json.dumps(body or {}), content_type=JSON)

    def entry(self, code='S', person=None):
        return {'list': self.key, 'on': TODAY, 'entries': [{'person_id': person or self.person, 'code': code}]}

    # ---- what field staff can do ------------------------------------------------------------------------------------
    def test_staff_view_a_list_and_record_a_door_knock(self):
        account = staff()
        with K.hosted():
            made = K.client(STAFF, login=account)
            lists = made.get('/api/v3/walk/lists')
            self.assertEqual(lists.status_code, 200)
            self.assertIn(self.key, [row['key'] for row in lists.json()['lists']])
            shown = made.get('/api/v3/walk/lists/' + self.key)
            self.assertEqual(shown.status_code, 200)
            self.assertEqual(len(base.voters_of(shown.json())), len(self.voters))
            self.assertEqual(shown['Cache-Control'], base.NO_STORE)
            saved = self.call(made, 'post', '/api/v3/walk/dispositions', self.entry('NH'))
            self.assertEqual((saved.status_code, saved.json()), (200, {'saved': 1}))
            after = made.get('/api/v3/walk/lists/' + self.key).json()
            self.assertEqual([v['last']['code'] for v in base.voters_of(after) if v['person_id'] == self.person], ['NH'])
        self.assertEqual(Disposition.objects.filter(walk_list__key=self.key, code='NH').count(), 1)

    def test_the_session_endpoint_names_the_visitor_for_the_watermark(self):
        account = staff()
        with K.hosted():
            session = K.client(STAFF, login=account).get('/api/auth/session').json()
        self.assertEqual((session['hosted'], session['email'], session['authenticated']), (True, STAFF, True))

    # ---- who cannot ------------------------------------------------------------------------------------------------
    def test_an_account_that_is_not_field_staff_gets_403_and_changes_nothing(self):
        account = staff(group=False)
        before = (Disposition.objects.count(), AuditEvent.objects.filter(action__startswith='walk.').count())
        with K.hosted():
            made = K.client(STAFF, login=account)
            self.assertEqual(made.get('/api/v3/walk/lists').status_code, 403)
            self.assertEqual(made.get('/api/v3/walk/lists/' + self.key).status_code, 403)
            self.assertEqual(self.call(made, 'post', '/api/v3/walk/dispositions', self.entry()).status_code, 403)
        self.assertEqual((Disposition.objects.count(), AuditEvent.objects.filter(action__startswith='walk.').count()), before)

    def test_an_access_identity_with_no_session_is_asked_to_sign_in(self):
        with K.hosted():
            made = K.client(STAFF)
            self.assertEqual(made.get('/api/v3/walk/lists').status_code, 401)
            self.assertEqual(self.call(made, 'post', '/api/v3/walk/dispositions', self.entry()).status_code, 401)

    def test_no_token_is_refused_before_the_app_looks_at_anything(self):
        account = staff()
        with K.hosted():
            made = K.client(None, login=account)
            self.assertEqual(made.get('/api/v3/walk/lists/' + self.key).status_code, 403)

    def test_a_session_on_someone_elses_access_identity_is_refused_and_ended(self):
        account = staff()
        with K.hosted():
            made = K.client(OTHER, login=account)
            sid = made.cookies[settings.SESSION_COOKIE_NAME].value
            self.assertEqual(made.get('/api/v3/walk/lists/' + self.key).status_code, 403)
            self.assertFalse(Session.objects.filter(session_key=sid).exists())

    def test_the_gate_inside_the_view_does_not_trust_a_missing_middleware(self):
        account = staff()
        with override_settings(HOSTED=True, LOCAL_ADMIN_ACCESS=False):        # settings say hosted, Access never ran
            made = Client(HTTP_HOST='localhost')
            made.force_login(account)
            self.assertEqual(made.get('/api/v3/walk/lists/' + self.key).status_code, 403)

    def test_an_inactive_account_is_not_field_staff(self):
        account = staff()
        with K.hosted():
            made = K.client(STAFF, login=account)
            account.is_active = False
            account.save()
            self.assertEqual(made.get('/api/v3/walk/lists').status_code, 401)

    # ---- what stays closed ------------------------------------------------------------------------------------------
    def test_every_other_person_level_route_stays_closed_to_staff(self):
        account = staff()
        users, lists = User.objects.count(), WalkList.objects.count()
        with K.hosted():
            made = K.client(STAFF, login=account)
            for method, url in CLOSED:
                response = self.call(made, method, url)
                self.assertEqual(response.status_code, 403, url)
            # a list cannot be built, saved or re-knocked, and the summary table cannot be downloaded
            self.assertEqual(self.call(made, 'post', '/api/v3/walk/lists', {'title': 'x', 'from': self.key, 'reknock': True}).status_code, 403)
            self.assertEqual(made.put('/api/v3/walk/lists').status_code, 403)
            self.assertEqual(made.get('/api/summaries?format=csv&level=precinct&metric=count').status_code, 403)
        self.assertEqual((User.objects.count(), WalkList.objects.count()), (users, lists))

    # ---- field managers: the routes that build and save a list, and nothing else -------------------------------------------
    def builder_requests(self):
        """(method, url, body) of each builder route, valid for the synthetic district."""
        street = '%d:both' % self.street_id('Ridgeview Ct')
        return [('get', '/api/v3/walk/options?level=ss', None), ('get', '/api/v3/walk/streets?q=ced', None),
                ('get', '/api/v3/walk/list?streets=' + street, None), ('get', '/api/v3/walk/overview?ss=010', None),
                ('get', '/api/v3/walk/plan?ss=010', None), ('post', '/api/v3/walk/match', {'rows': [{'street': 'Pine St'}]}),
                ('post', '/api/v3/walk/lists', {'title': 'Made on the server', 'query': {'streets': street},
                                                'fingerprint': base.ANY_FINGERPRINT})]

    def test_a_manager_builds_and_saves_a_list_on_the_server(self):
        account = manager()
        self.posted('/api/v3/walk/dispositions', self.entry('NH'))              # the local owner's result: a re-knock list needs one
        before = WalkList.objects.count()
        with K.hosted():
            made = K.client(BOSS, login=account)
            for method, url, body in self.builder_requests():
                answer = self.call(made, method, url, body)
                if url.startswith('/api/v3/walk/plan'):         # this synthetic district has no candidate: 404 after the gate
                    self.assertNotIn(answer.status_code, (401, 403, 429), url)
                else:
                    self.assertIn(answer.status_code, (200, 201), (url, answer.content[:200]))
                self.assertEqual(answer['Cache-Control'], base.NO_STORE, url)
            saved = WalkList.objects.order_by('-id').first()
            self.assertEqual((WalkList.objects.count(), saved.title), (before + 1, 'Made on the server'))
            # a re-knock list is made the same way
            again = self.call(made, 'post', '/api/v3/walk/lists', {'title': 'Again', 'from': self.key, 'reknock': True})
            self.assertEqual(again.status_code, 201)

    def test_being_a_manager_is_not_being_field_staff_and_opens_nothing_else(self):
        account = manager()
        users, lists, results = User.objects.count(), WalkList.objects.count(), Disposition.objects.count()
        with K.hosted():
            made = K.client(BOSS, login=account)
            self.assertEqual(made.get('/api/v3/walk/lists').status_code, 403)                 # the field-staff routes
            self.assertEqual(made.get('/api/v3/walk/lists/' + self.key).status_code, 403)
            self.assertEqual(self.call(made, 'post', '/api/v3/walk/dispositions', self.entry()).status_code, 403)
            for method, url in CLOSED_TO_ALL:
                self.assertEqual(self.call(made, method, url).status_code, 403, url)
            self.assertEqual(made.put('/api/v3/walk/lists').status_code, 403)
            self.assertEqual(made.get('/api/summaries?format=csv&level=precinct&metric=count').status_code, 403)
        self.assertEqual((User.objects.count(), WalkList.objects.count(), Disposition.objects.count()), (users, lists, results))

    def test_a_manager_who_is_also_field_staff_has_both(self):
        account = manager(also_staff=True)
        with K.hosted():
            made = K.client(BOSS, login=account)
            self.assertEqual(made.get('/api/v3/walk/options?level=ss').status_code, 200)
            self.assertEqual(made.get('/api/v3/walk/lists').status_code, 200)

    def test_field_staff_who_are_not_managers_cannot_build_or_save(self):
        account = staff()
        before = (WalkList.objects.count(), AuditEvent.objects.filter(action__startswith='walk.').count())
        with K.hosted():
            made = K.client(STAFF, login=account)
            for method, url, body in self.builder_requests():
                self.assertEqual(self.call(made, method, url, body).status_code, 403, url)
        self.assertEqual((WalkList.objects.count(), AuditEvent.objects.filter(action__startswith='walk.').count()), before)

    def test_nobody_else_gets_the_builder_routes(self):
        account = manager()
        plain = manager(OTHER)
        plain.groups.clear()                                                    # signed in, in no group
        before = WalkList.objects.count()
        with K.hosted():
            for who, made in (('no token', K.client(None, login=account)),                    # a session but no Access token
                              ('no session', K.client(BOSS)),                                 # an Access identity but no account
                              ('no group', K.client(OTHER, login=plain)),
                              ('carried session', K.client(OTHER, login=account))):           # a session on someone else's token
                for method, url, body in self.builder_requests():
                    self.assertIn(self.call(made, method, url, body).status_code, (401, 403), (who, url))
            self.assertEqual(K.client(BOSS).get('/api/v3/walk/options?level=ss').status_code, 401)
            self.assertEqual(K.client(None).get('/api/v3/walk/options?level=ss').status_code, 403)
            inactive = K.client(BOSS, login=account)
            account.is_active = False
            account.save()
            self.assertEqual(inactive.get('/api/v3/walk/options?level=ss').status_code, 401)
        self.assertEqual(WalkList.objects.count(), before)

    def test_the_builder_routes_stay_closed_without_the_access_middleware(self):
        account = manager()
        with override_settings(HOSTED=True, LOCAL_ADMIN_ACCESS=False):          # settings say hosted, Access never ran
            made = Client(HTTP_HOST='localhost')
            made.force_login(account)
            self.assertEqual(made.get('/api/v3/walk/options?level=ss').status_code, 403)

    def test_a_manager_shares_the_rate_limit(self):
        first, second = manager(), manager(OTHER)
        with K.hosted(FIELD_RATE_PER_MINUTE=2):
            made, other = K.client(BOSS, login=first), K.client(OTHER, login=second)
            self.assertEqual([made.get('/api/v3/walk/options?level=ss').status_code for _ in range(2)], [200, 200])
            blocked = made.get('/api/v3/walk/streets?q=ced')
            self.assertEqual((blocked.status_code, blocked['Retry-After']), (429, '60'))
            self.assertEqual(other.get('/api/v3/walk/options?level=ss').status_code, 200)

    def test_each_builder_call_is_audited_by_route_without_a_name_or_a_parameter(self):
        account = manager()
        with K.hosted():
            made = K.client(BOSS, login=account)
            for method, url, body in self.builder_requests():
                self.call(made, method, url, body)
        events = list(AuditEvent.objects.filter(action__startswith='walk.').order_by('id'))
        self.assertEqual([e.action for e in events], [
            'walk.build.options', 'walk.build.streets', 'walk.build.walk_list', 'walk.build.overview', 'walk.build.plan',
            'walk.build.match', 'walk.build.create_list', 'walk.list.create'])
        self.assertTrue(all(e.actor == BOSS for e in events))
        # one row per builder route, each carrying its own route (the saved list's row comes last)
        self.assertEqual([e.detail['route'] for e in events[:-1]], [url.split('?')[0] for _, url, _ in self.builder_requests()])
        made_list = WalkList.objects.get(title='Made on the server')
        self.assertEqual(events[-1].detail, {'route': '/api/v3/walk/lists', 'list': made_list.key, 'kind': 'standard'})
        everything = json.dumps([e.detail for e in events]) + ''.join(e.actor + e.action for e in events)
        for secret in ('Made on the server', 'ced', 'Pine St', 'Ridgeview') + tuple(v['name'] for v in self.voters if v['name']):
            self.assertNotIn(secret, everything)

    def test_one_allowance_counts_gets_and_posts_of_both_groups_together(self):
        account = manager(also_staff=True)
        with K.hosted(FIELD_RATE_PER_MINUTE=3):
            made = K.client(BOSS, login=account)
            self.assertEqual(made.get('/api/v3/walk/options?level=ss').status_code, 200)                     # manager GET
            self.assertEqual(self.call(made, 'post', '/api/v3/walk/match', {'rows': [{'street': 'Pine St'}]}).status_code, 200)   # manager POST
            self.assertEqual(made.get('/api/v3/walk/lists').status_code, 200)                                # staff GET
            blocked = self.call(made, 'post', '/api/v3/walk/dispositions', self.entry())                      # staff POST
            self.assertEqual((blocked.status_code, blocked['Retry-After']), (429, '60'))
            self.assertEqual(made.get('/api/v3/walk/options?level=ss').status_code, 429)
        self.assertFalse(Disposition.objects.exists())

    # ---- audit ----------------------------------------------------------------------------------------------------
    def test_each_opened_read_and_write_leaves_an_audit_event_without_a_name(self):
        account = staff()
        with K.hosted():
            made = K.client(STAFF, login=account)
            made.get('/api/v3/walk/lists')
            made.get('/api/v3/walk/lists/' + self.key)
            self.call(made, 'post', '/api/v3/walk/dispositions', self.entry('U'))
        events = list(AuditEvent.objects.filter(action__startswith='walk.').order_by('id'))
        self.assertEqual([e.action for e in events], ['walk.lists.read', 'walk.list.read', 'walk.disposition.write'])
        self.assertTrue(all(e.actor == STAFF for e in events))
        read, write = events[1].detail, events[2].detail
        self.assertEqual((read['list'], read['voters'], read['route']), (self.key, len(self.voters), '/api/v3/walk/lists/' + self.key))
        self.assertEqual((write['list'], write['n'], len(write['voter_keys'])), (self.key, 1, 1))
        everything = json.dumps([e.detail for e in events]) + ''.join(e.actor + e.action for e in events)
        for voter in self.voters:
            for secret in (voter['name'], voter['phone']):
                if secret:
                    self.assertNotIn(secret, everything)

    def test_a_refused_request_is_not_audited_as_a_read(self):
        account = staff(group=False)
        with K.hosted():
            K.client(STAFF, login=account).get('/api/v3/walk/lists/' + self.key)
        self.assertFalse(AuditEvent.objects.filter(action__startswith='walk.').exists())

    def test_local_use_of_the_same_routes_writes_no_audit_events(self):
        self.assertEqual(self.get('/api/v3/walk/lists').status_code, 200)
        self.assertEqual(self.get('/api/v3/walk/lists/' + self.key).status_code, 200)
        self.assertEqual(self.post('/api/v3/walk/dispositions', self.entry()).status_code, 200)
        self.assertFalse(AuditEvent.objects.filter(action__startswith='walk.').exists())

    # ---- rate limit -----------------------------------------------------------------------------------------------
    def test_a_user_who_asks_too_often_waits(self):
        first, second = staff(), staff(OTHER)
        with K.hosted(FIELD_RATE_PER_MINUTE=3):
            made, other = K.client(STAFF, login=first), K.client(OTHER, login=second)
            for _ in range(5):                                                   # refusals do not use up the allowance
                self.assertEqual(made.get('/api/v3/walk/streets?q=a').status_code, 403)
            self.assertEqual([made.get('/api/v3/walk/lists').status_code for _ in range(3)], [200, 200, 200])
            blocked = made.get('/api/v3/walk/lists/' + self.key)
            self.assertEqual((blocked.status_code, blocked['Retry-After']), (429, '60'))
            self.assertEqual(self.call(made, 'post', '/api/v3/walk/dispositions', self.entry()).status_code, 429)
            self.assertEqual(other.get('/api/v3/walk/lists').status_code, 200)    # someone else is not held back
        self.assertFalse(Disposition.objects.exists())

    def test_the_allowance_comes_back_with_the_clock(self):
        with override_settings(FIELD_RATE_PER_MINUTE=2):
            self.assertEqual([FA.limited('A@x', now=t) for t in (0, 10, 20)], [False, False, True])
            self.assertFalse(FA.limited('a@X', now=61))                           # the first has aged out; case is ignored
            self.assertTrue(FA.limited('a@x', now=62))


class InviteTests(TestCase):
    def test_an_invitation_can_make_the_account_field_staff(self):
        from .views import create_invitation
        link = create_invitation(STAFF, field_staff=True)
        token = link.split('=', 1)[1]
        answer = Client().post('/api/auth/accept', json.dumps({'token': token, 'password': 'a long passphrase for tests'}),
                                    content_type=JSON)
        self.assertEqual(answer.status_code, 200)
        self.assertTrue(FA.is_field_staff(User.objects.get(username=STAFF)))

    def test_an_invitation_can_make_the_account_a_field_manager_and_only_that(self):
        from .views import create_invitation
        token = create_invitation(BOSS, field_manager=True).split('=', 1)[1]
        answer = Client().post('/api/auth/accept', json.dumps({'token': token, 'password': 'a long passphrase for tests'}),
                                    content_type=JSON)
        self.assertEqual(answer.status_code, 200)
        account = User.objects.get(username=BOSS)
        self.assertTrue(FA.in_group(account, FA.MANAGER))
        self.assertFalse(FA.is_field_staff(account))

    def test_an_ordinary_invitation_is_not_field_staff(self):
        from .views import create_invitation
        token = create_invitation(OTHER).split('=', 1)[1]
        Client().post('/api/auth/accept', json.dumps({'token': token, 'password': 'a long passphrase for tests'}), content_type=JSON)
        self.assertFalse(FA.is_field_staff(User.objects.get(username=OTHER)))
        self.assertFalse(FA.in_group(User.objects.get(username=OTHER), FA.MANAGER))

    def test_hosted_an_invitation_can_only_be_accepted_by_the_identity_it_names(self):
        from .views import create_invitation
        with K.hosted():
            token = create_invitation(STAFF, field_staff=True).split('=', 1)[1]
            body = json.dumps({'token': token, 'password': 'a long passphrase for tests'})
            stranger = K.client(OTHER).post('/api/auth/accept', body, content_type=JSON)
            self.assertEqual(stranger.status_code, 403)
            self.assertFalse(User.objects.filter(username=STAFF).exists())
            self.assertIsNone(Invitation.objects.get(username=STAFF).used_at)               # the link is still good for its owner
            mine = K.client(STAFF.upper()).post('/api/auth/accept', body, content_type=JSON)
            self.assertEqual(mine.status_code, 200)
            self.assertTrue(FA.is_field_staff(User.objects.get(username=STAFF)))
            self.assertEqual(K.client(None).post('/api/auth/accept', body, content_type=JSON).status_code, 403)

    def test_the_command_grants_it_and_hosted_names_accounts_by_email(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from io import StringIO
        out = StringIO()
        with override_settings(HOSTED=True):
            call_command('invite', STAFF, '--field-staff', '--origin', 'https://co05.example.test', stdout=out)
            self.assertTrue(out.getvalue().startswith('https://co05.example.test/?invite='))
            self.assertTrue(Invitation.objects.get(username=STAFF).field_staff)
            with self.assertRaises(CommandError):
                call_command('invite', 'plainname', stdout=out)
        call_command('invite', 'plainname', stdout=out)                            # locally any name still works
        self.assertFalse(Invitation.objects.get(username='plainname').field_staff)
        call_command('invite', BOSS, '--field-manager', stdout=out)
        made = Invitation.objects.get(username=BOSS)
        self.assertEqual((made.field_manager, made.field_staff), (True, False))

    def test_the_admin_api_can_invite_field_staff(self):
        admin = User.objects.create_user('boss@example.test', password='correct horse battery staple', is_staff=True)
        made = Client()
        made.force_login(admin)
        answer = made.post('/api/accounts', json.dumps({'username': STAFF, 'field_staff': True}), content_type=JSON)
        self.assertEqual(answer.status_code, 200)
        self.assertTrue(Invitation.objects.get(username=STAFF).field_staff)


def dockerignored(rules, path):
    """Docker's rule: the last pattern matching the path or one of its parent directories decides; a leading ! re-includes."""
    out = False
    parents = [path] + [path.rsplit('/', n)[0] for n in range(1, path.count('/') + 1)]
    for rule in rules:
        rx = re.escape(rule.lstrip('!')).replace(r'\*\*/', '(.*/)?').replace(r'\*\*', '.*').replace(r'\*', '[^/]*')
        if any(re.fullmatch(rx, each) for each in parents):
            out = not rule.startswith('!')
    return out


class ContainerStartTests(SimpleTestCase):
    # (the line the image needs, a path it keeps out). .local holds the stores, .secret and salts; the first line (default deny)
    # also covers them, so their paths are in OUT
    REQUIRED = [
        ('**/__pycache__/**', 'server/__pycache__/anything.txt'), ('**/*.pyc', 'tools/stray.pyc'),
        ('**/test_*.py', 'server/research/test_hosted.py'), ('**/tests.py', 'server/research/tests.py'),
        ('**/*_testkit.py', 'server/research/walk_testkit.py'), ('**/*.test.ts', 'tools/household-index.test.ts'),
        ('**/*.ps1', 'tools/Test-TerrierDesktop.ps1'), ('frontend/public/fixtures/**', 'frontend/public/fixtures/v3/meta.json'),
        ('tools/luna_district/**', 'tools/luna_district/g0.py'), ('**/*.sqlite3', 'server/research/walk.sqlite3'),
        ('**/*.zip', 'tools/bundle.zip'), ('.local', '.local/co-05/walk.sqlite3'),
    ]
    OUT = ['.local/co-05/.secret', '.local/co-05/.walk-salt', '.local/co-05/.street-salt', '.secret', '.walk-salt', 'data/voters.csv',
           'frontend/node_modules/react/index.js', 'HANDOFF_2026-10-01.md']
    IN = ['manage.py', 'requirements.linux.lock', 'server/settings.py', 'server/research/walk_store.py', 'tools/container_start.py',
          'data/criteria.json', 'data/field_dictionary.csv', 'frontend/package.json', 'frontend/package-lock.json', 'frontend/tsconfig.json',
          'frontend/vite.config.ts', 'frontend/index.html', 'frontend/src/main.tsx', 'frontend/public/brand/preview.js']

    def test_the_image_build_context_leaves_out_tests_fixtures_state_and_secrets(self):
        text = (Path(settings.BASE_DIR) / '.dockerignore').read_text(encoding='utf-8')
        rules = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith('#')]
        last_reinclude = max(i for i, rule in enumerate(rules) if rule.startswith('!'))
        for rule, path in self.REQUIRED:
            self.assertIn(rule, rules)
            self.assertGreater(rules.index(rule), last_reinclude, rule + ' must come after every re-inclusion')    # last rule wins
            self.assertTrue(dockerignored(rules, path), rule + ' must keep ' + path + ' out of the image')
        for path in self.OUT:
            self.assertTrue(dockerignored(rules, path), path + ' must be out of the image')
        for path in self.IN:
            self.assertFalse(dockerignored(rules, path), path + ' must be in the image')

    def test_a_store_in_wal_mode_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, mode in (('scores.sqlite3', 'delete'), ('walk.sqlite3', 'wal'), ('accounts.sqlite3', 'wal')):
                with closing(sqlite3.connect(Path(tmp) / name)) as db:
                    db.execute('PRAGMA journal_mode=' + mode)
                    db.execute('CREATE TABLE t(x)')
                    db.commit()
            (Path(tmp) / 'notes.txt').write_text('x')
            self.assertEqual(container_start.wal_stores(tmp), ['walk.sqlite3'])      # accounts.sqlite3 is the state volume's

    def test_a_delete_mode_store_opens_from_a_read_only_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'scores.sqlite3'
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE t(x)')
                db.execute('INSERT INTO t VALUES (1)')
                db.commit()
            path.chmod(0o444)
            try:
                with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
                    self.assertEqual(db.execute('SELECT x FROM t').fetchone()[0], 1)
                    self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
            finally:
                path.chmod(0o644)


class FieldResultsSurviveRebuildTests(base.WalkCase):
    """Voter keys are HMAC(.walk-salt, voter id): the same across rebuilds that keep the salt, so results never orphan."""

    def keys(self):
        with closing(sqlite3.connect(self.stores.directory / 'walk.sqlite3')) as db:
            return dict(db.execute('SELECT person_id, voter_key FROM person'))

    def rebuild(self):
        B.build(self.stores.source_csv, self.stores.directory, force=True)
        W.reset()

    def test_keys_and_recorded_results_survive_a_rebuild_with_the_same_salt_and_not_without_it(self):
        key = self.make_list({'streets': '%d:both' % self.street_id('Ridgeview Ct')})['list']['key']
        voters = base.voters_of(self.json('/api/v3/walk/lists/' + key))
        entries = [{'person_id': v['person_id'], 'code': 'NH'} for v in voters]
        self.posted('/api/v3/walk/dispositions', {'list': key, 'on': TODAY, 'entries': entries})
        before = self.keys()
        self.assertEqual(len(set(before.values())), len(before))                    # one key per person here

        self.rebuild()                                                              # same .walk-salt
        self.assertEqual(self.keys(), before)
        again = self.posted('/api/v3/walk/lists', {'title': 'Again', 'from': key, 'reknock': True}, 201)
        self.assertEqual(again['missing'], 0)                                       # every Not home voter found by key
        self.assertEqual(len(base.voters_of(again)), len(voters))
        shown = self.json('/api/v3/walk/lists/' + key)
        self.assertEqual({v['last']['code'] for v in base.voters_of(shown)}, {'NH'})

        (self.stores.directory / '.walk-salt').unlink()                              # a lost salt is a new salt: everyone is re-keyed
        self.rebuild()
        self.assertTrue(set(self.keys().values()).isdisjoint(before.values()))
        gone = self.post('/api/v3/walk/lists', {'title': 'Lost', 'from': key, 'reknock': True})
        self.assertEqual(gone.status_code, 409)                                      # the results are orphaned, loudly
