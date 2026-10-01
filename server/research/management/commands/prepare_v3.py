import time

from django.core.management.base import BaseCommand, CommandError

from server.research import v3_cache
from server.research.models import Snapshot
from tools.glassbox import GlassBox, new_run_id


class Command(BaseCommand):
    help = ('Build (or refresh) the derived v3 payloads of a cu-summary-v4 snapshot: the per-level area values, the '
            'coarse and fine geometry and the static meta. Idempotent; the importer and the server already do this, '
            'so run it after upgrading the code, before restarting the server.')

    def add_arguments(self, parser):
        parser.add_argument('snapshot', nargs='?', help='snapshot key (default: the active snapshot)')

    def handle(self, *args, **options):
        query = Snapshot.objects.filter(key=options['snapshot']) if options['snapshot'] \
            else Snapshot.objects.filter(active=True)
        snap = query.first()
        if snap is None or snap.manifest.get('schema') != 'cu-summary-v4':
            raise CommandError('Choose a cu-summary-v4 snapshot.')
        box = GlassBox(new_run_id('cu-geo-v3-prepare'), 'cu-geography-v3/prepare_v3')
        box.run('running', 'start', 'derived payloads of %s' % snap.key)
        started = time.time()

        def note(kind, *parts):
            box.stage('running', kind, ' '.join(str(p) for p in parts))
            self.stdout.write('%s %s' % (kind, ' '.join(str(p) for p in parts)))

        sizes = v3_cache.prepare(snap.key, note)
        box.run('completed', 'complete', '%d payloads, %d bytes in %.1fs' % (len(sizes), sum(sizes.values()),
                                                                           time.time() - started))
        self.stdout.write('prepared %d payloads (%d bytes) for %s' % (len(sizes), sum(sizes.values()), snap.key))
