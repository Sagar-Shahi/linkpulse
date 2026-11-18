from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from .models import Click
from .testutils import add_clicks, make_link, make_user


class PruneClicksTests(TestCase):
    def setUp(self):
        link = make_link(make_user())
        add_clicks(link, 3, days_ago=400)
        add_clicks(link, 2, days_ago=10)

    def run_command(self, *args):
        out = StringIO()
        call_command('prune_clicks', *args, stdout=out)
        return out.getvalue()

    def test_deletes_only_clicks_older_than_the_period(self):
        self.assertIn('Deleted 3', self.run_command('--days', '365'))
        self.assertEqual(Click.objects.count(), 2)

    def test_dry_run_changes_nothing(self):
        self.assertIn('3 click(s)', self.run_command('--days', '365', '--dry-run'))
        self.assertEqual(Click.objects.count(), 5)

    @override_settings(CLICK_RETENTION_DAYS=0)
    def test_zero_keeps_everything(self):
        self.run_command()
        self.assertEqual(Click.objects.count(), 5)

    @override_settings(CLICK_RETENTION_DAYS=30)
    def test_default_comes_from_the_setting(self):
        self.run_command()
        self.assertEqual(Click.objects.count(), 2)

    def test_negative_days_is_an_error(self):
        with self.assertRaises(CommandError):
            self.run_command('--days', '-1')
