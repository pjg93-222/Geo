from datetime import date, datetime, time, timedelta, timezone as utc

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from server.research.models import AuditEvent

RETENTION = timedelta(days=90)   # ruled 2026-10-26: keep AuditEvent rows for the election cycle plus 90 days, then purge
# The only actions that put voter keys in `detail` (walk_views.py:1093 and :1129, the `voter_keys` list). Every other event (walk.list.*
# keep a list key, walk.build.* a route, account.* and snapshot.* a username or snapshot key) and audit.purged are kept for good.
VOTER_KEY_ACTIONS = ('walk.disposition.write', 'walk.opt_in.write')


class Command(BaseCommand):
    help = ('Purge the AuditEvent rows that hold voter keys (%s) created on or before the end of the --cycle-end day (UTC); '
            'rows after it belong to the next cycle. Account, snapshot and admin events and earlier purge records are kept. '
            'Refuses to run before cycle end + 90 days. Dry run unless --yes. Touches no other model.' % ', '.join(VOTER_KEY_ACTIONS))

    def add_arguments(self, parser):
        parser.add_argument('--cycle-end', required=True, help='last day of the election cycle, YYYY-MM-DD')
        parser.add_argument('--yes', action='store_true', help='delete; without it only the counts are printed')

    def handle(self, *args, **options):
        try:
            cycle_end = date.fromisoformat(options['cycle_end'])
        except ValueError:
            raise CommandError('--cycle-end must be a date as YYYY-MM-DD, got %r' % options['cycle_end'])
        cutoff = datetime.combine(cycle_end + timedelta(days=1), time.min, utc.utc)   # created_at is UTC: rows strictly before this
        earliest = (cutoff + RETENTION).date()
        if timezone.now() < cutoff + RETENTION:
            raise CommandError('Too early: audit rows are kept for the cycle end %s plus %d days; the earliest this can run is %s'
                               % (cycle_end, RETENTION.days, earliest))
        before = AuditEvent.objects.filter(created_at__lt=cutoff)
        rows = before.filter(action__in=VOTER_KEY_ACTIONS)
        with transaction.atomic():
            per_action = dict(rows.values_list('action').annotate(n=Count('id')).order_by('action'))
            count = sum(per_action.values())
            kept = before.count() - count
            self.stdout.write('%d voter-key audit rows on or before %s%s' % (count, cycle_end, '' if options['yes'] else ' (dry run; pass --yes to delete)'))
            for action, n in per_action.items():
                self.stdout.write('  %s: %d' % (action, n))
            self.stdout.write('%d rows in the same period kept as non-voter-key events' % kept)
            if options['yes']:
                deleted, by_model = rows.delete()
                if set(by_model) - {AuditEvent._meta.label}:
                    raise CommandError('Refusing: the delete reached other models %s' % by_model)   # rolls back
                AuditEvent.objects.create(actor='purge_audit', action='audit.purged',
                                          detail={'cycle_end': cycle_end.isoformat(), 'cutoff': cutoff.isoformat(),
                                                  'earliest_run': earliest.isoformat(), 'count': deleted})
                self.stdout.write('Deleted %d audit rows' % deleted)
