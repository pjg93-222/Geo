from datetime import date, datetime, time, timedelta, timezone as utc

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from server.research.models import AuditEvent

RETENTION = timedelta(days=90)   # ruled 2026-10-26: keep AuditEvent rows for the election cycle plus 90 days


class Command(BaseCommand):
    help = ('Purge AuditEvent rows (they hold voter keys) older than the election cycle plus 90 days. Dry run unless --yes. '
            'Refuses to run before cycle end + 90 days. Touches no other model.')

    def add_arguments(self, parser):
        parser.add_argument('--cycle-end', required=True, help='last day of the election cycle, YYYY-MM-DD')
        parser.add_argument('--yes', action='store_true', help='delete; without it only the counts are printed')

    def handle(self, *args, **options):
        try:
            cycle_end = date.fromisoformat(options['cycle_end'])
        except ValueError:
            raise CommandError('--cycle-end must be a date as YYYY-MM-DD, got %r' % options['cycle_end'])
        cutoff = cycle_end + RETENTION
        if timezone.now().date() < cutoff:
            raise CommandError('Too early: audit rows are kept until the cycle end %s plus %d days; the earliest this can run is %s'
                               % (cycle_end, RETENTION.days, cutoff))
        # created_at is UTC: every row on or before the cutoff day, i.e. before the next midnight
        rows = AuditEvent.objects.filter(created_at__lt=datetime.combine(cutoff + timedelta(days=1), time.min, utc.utc))
        with transaction.atomic():
            per_action = dict(rows.values_list('action').annotate(n=Count('id')).order_by('action'))
            count = sum(per_action.values())
            self.stdout.write('%d audit rows on or before %s%s' % (count, cutoff, '' if options['yes'] else ' (dry run; pass --yes to delete)'))
            for action, n in per_action.items():
                self.stdout.write('  %s: %d' % (action, n))
            if options['yes']:
                rows.delete()
                AuditEvent.objects.create(actor='purge_audit', action='audit.purged',
                                          detail={'cycle_end': cycle_end.isoformat(), 'cutoff': cutoff.isoformat(), 'count': count})
                self.stdout.write('Deleted %d audit rows' % count)
