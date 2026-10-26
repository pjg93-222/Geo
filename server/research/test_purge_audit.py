from datetime import datetime, timezone as utc
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .models import AuditEvent


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=utc.utc)


class PurgeAuditTests(TestCase):
    """cycle end 2026-11-03 + 90 days = cutoff 2027-02-01 (the whole of that UTC day is purged)."""

    def setUp(self):
        self.ids = {}
        for name, stamp, action in (('old', '2026-09-01T10:00:00', 'walk.read'), ('edge', '2027-02-01T23:59:59', 'walk.result'),
                                    ('new', '2027-02-02T00:00:00', 'walk.read')):
            row = AuditEvent.objects.create(actor='a@example.com', action=action, detail={'voter_keys': ['k-' + name]})
            AuditEvent.objects.filter(pk=row.pk).update(created_at=at(stamp))   # auto_now_add ignores a value on create
            self.ids[name] = row.pk

    def run_command(self, today, *args):
        out = StringIO()
        with mock.patch('django.utils.timezone.now', return_value=at(today)):
            call_command('purge_audit', *args, stdout=out)
        return out.getvalue()

    def test_refuses_before_cycle_end_plus_90_days(self):
        with self.assertRaisesRegex(CommandError, r'earliest .* 2027-02-01'):
            self.run_command('2027-01-31T12:00:00', '--cycle-end', '2026-11-03', '--yes')
        self.assertEqual(AuditEvent.objects.count(), 3)

    def test_dry_run_deletes_nothing_and_reports_counts(self):
        out = self.run_command('2027-02-01T12:00:00', '--cycle-end', '2026-11-03')
        self.assertIn('2 audit rows on or before 2027-02-01', out)
        self.assertIn('walk.read: 1', out)
        self.assertIn('walk.result: 1', out)
        self.assertEqual(AuditEvent.objects.count(), 3)

    def test_yes_deletes_through_the_cutoff_day_only_and_audits_itself(self):
        self.run_command('2027-03-01T00:00:00', '--cycle-end', '2026-11-03', '--yes')
        self.assertEqual(set(AuditEvent.objects.exclude(action='audit.purged').values_list('pk', flat=True)), {self.ids['new']})
        purged = AuditEvent.objects.get(action='audit.purged')
        self.assertEqual(purged.detail, {'cycle_end': '2026-11-03', 'cutoff': '2027-02-01', 'count': 2})
        self.assertNotIn('k-', str(purged.detail))

    def test_bad_date_is_an_error(self):
        for bad in ('11/03/2026', '2026-13-01', ''):
            with self.assertRaisesRegex(CommandError, 'YYYY-MM-DD'):
                self.run_command('2027-03-01T00:00:00', '--cycle-end', bad)
        self.assertEqual(AuditEvent.objects.count(), 3)
