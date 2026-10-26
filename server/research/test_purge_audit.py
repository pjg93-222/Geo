from datetime import datetime, timezone as utc
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import QuerySet
from django.test import TestCase

from .models import AuditEvent


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=utc.utc)


class PurgeAuditTests(TestCase):
    """cycle end 2026-11-03: voter-key rows before 2026-11-04T00:00Z go, from 2027-02-02T00:00Z (cutoff + 90 days) on."""

    def make(self, name, stamp, action):
        row = AuditEvent.objects.create(actor='a@example.com', action=action, detail={'voter_keys': ['k-' + name]})
        AuditEvent.objects.filter(pk=row.pk).update(created_at=at(stamp))   # auto_now_add ignores a value on create
        self.ids[name] = row.pk

    def setUp(self):
        self.ids = {}
        for name, stamp, action in (('old', '2026-09-01T10:00:00', 'walk.disposition.write'),
                                    ('edge', '2026-11-03T23:59:59', 'walk.opt_in.write'),
                                    ('next', '2026-11-04T00:00:00', 'walk.disposition.write'),
                                    ('late', '2027-02-02T09:00:00', 'walk.opt_in.write'),
                                    ('account', '2026-09-02T10:00:00', 'account.activate'),
                                    ('listread', '2026-09-03T10:00:00', 'walk.list.read')):
            self.make(name, stamp, action)

    def run_command(self, today, *args):
        out = StringIO()
        with mock.patch('django.utils.timezone.now', return_value=at(today)):
            call_command('purge_audit', *args, stdout=out)
        return out.getvalue()

    def test_refuses_until_cutoff_plus_90_days(self):
        with self.assertRaisesRegex(CommandError, r'earliest .* 2027-02-02'):
            self.run_command('2027-02-01T23:59:59', '--cycle-end', '2026-11-03', '--yes')
        with self.assertRaisesRegex(CommandError, r'earliest .* 2027-02-02'):
            self.run_command('2027-02-01T12:00:00', '--cycle-end', '2026-11-03', '--yes')
        self.assertEqual(AuditEvent.objects.count(), 6)
        self.run_command('2027-02-02T00:00:00', '--cycle-end', '2026-11-03', '--yes')      # allowed
        self.assertFalse(AuditEvent.objects.filter(pk=self.ids['old']).exists())

    def test_dry_run_deletes_nothing_and_reports_counts(self):
        out = self.run_command('2027-02-02T12:00:00', '--cycle-end', '2026-11-03')
        self.assertIn('2 voter-key audit rows on or before 2026-11-03', out)
        self.assertIn('walk.disposition.write: 1', out)
        self.assertIn('walk.opt_in.write: 1', out)
        self.assertIn('2 rows in the same period kept as non-voter-key events', out)
        self.assertEqual(AuditEvent.objects.count(), 6)

    def test_yes_deletes_voter_key_rows_of_the_cycle_only_and_audits_itself(self):
        self.run_command('2027-03-01T00:00:00', '--cycle-end', '2026-11-03', '--yes')
        self.assertFalse(AuditEvent.objects.filter(pk__in=[self.ids['old'], self.ids['edge']]).exists())   # voter-key rows of the cycle
        for name in ('next', 'late', 'account', 'listread'):     # the next cycle's rows, account and non-key walk events
            self.assertTrue(AuditEvent.objects.filter(pk=self.ids[name]).exists(), name)
        purged = AuditEvent.objects.get(action='audit.purged')
        self.assertEqual(purged.detail, {'cycle_end': '2026-11-03', 'cutoff': '2026-11-04T00:00:00+00:00',
                                         'earliest_run': '2027-02-02', 'count': 2})
        self.assertNotIn('k-', str(purged.detail))

    def test_a_second_cycle_keeps_the_first_purge_record(self):
        self.run_command('2027-03-01T00:00:00', '--cycle-end', '2026-11-03', '--yes')
        first = AuditEvent.objects.get(action='audit.purged').pk
        self.make('cycle2', '2027-06-01T10:00:00', 'walk.disposition.write')
        self.run_command('2027-11-01T00:00:00', '--cycle-end', '2027-06-30', '--yes')
        self.assertEqual(AuditEvent.objects.filter(action='audit.purged').count(), 2)
        self.assertTrue(AuditEvent.objects.filter(pk=first).exists())
        self.assertFalse(AuditEvent.objects.filter(pk=self.ids['cycle2']).exists())

    def test_bad_date_is_an_error(self):
        for bad in ('11/03/2026', '2026-13-01', ''):
            with self.assertRaisesRegex(CommandError, 'YYYY-MM-DD'):
                self.run_command('2027-03-01T00:00:00', '--cycle-end', bad)
        self.assertEqual(AuditEvent.objects.count(), 6)

    def test_a_delete_that_reaches_another_model_rolls_back(self):
        real = QuerySet.delete

        def reaches_another_model(queryset):
            deleted, by_model = real(queryset)     # really delete, so only the rollback can bring the rows back
            return deleted + 1, {**by_model, 'research.Disposition': 1}

        with mock.patch.object(QuerySet, 'delete', autospec=True, side_effect=reaches_another_model):
            with self.assertRaisesRegex(CommandError, 'other models'):
                self.run_command('2027-03-01T00:00:00', '--cycle-end', '2026-11-03', '--yes')
        self.assertTrue(AuditEvent.objects.filter(pk__in=[self.ids['old'], self.ids['edge']]).count() == 2)
        self.assertFalse(AuditEvent.objects.filter(action='audit.purged').exists())
