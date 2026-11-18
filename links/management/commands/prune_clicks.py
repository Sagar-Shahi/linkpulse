"""Delete old click rows, so the database does not grow forever and old visit data is not kept without a reason.

    python manage.py prune_clicks              # uses CLICK_RETENTION_DAYS (default 365)
    python manage.py prune_clicks --days 90
    python manage.py prune_clicks --dry-run

Schedule it, for example once a week with cron or your host's scheduled jobs.
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from links.models import Click


class Command(BaseCommand):
    help = 'Delete clicks older than the retention period.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=None, help='Keep this many days (default: CLICK_RETENTION_DAYS).')
        parser.add_argument('--dry-run', action='store_true', help='Only show how many rows would be deleted.')

    def handle(self, *args, **options):
        days = settings.CLICK_RETENTION_DAYS if options['days'] is None else options['days']
        if days < 0:
            raise CommandError('--days cannot be negative.')
        if days == 0:
            self.stdout.write('Retention is 0 (keep everything). Nothing deleted.')
            return
        old = Click.objects.filter(clicked_at__lt=timezone.now() - timedelta(days=days))
        count = old.count()
        if options['dry_run']:
            self.stdout.write(f'{count} click(s) older than {days} days would be deleted.')
            return
        old._raw_delete(old.db)
        self.stdout.write(f'Deleted {count} click(s) older than {days} days.')
