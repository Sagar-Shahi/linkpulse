"""Fill a development database with a demo account, links and a month of believable clicks.

    python manage.py seed_demo

Only works when DJANGO_DEBUG=True, so a demo account with a known password can never exist on a live site.
"""
import random
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from links.models import Click, Link
from links.tracking import classify_device

DEMO_PASSWORD = 'demo-password-2026'  # nosec B105 - DEBUG-only demo account

LINKS = [
    ('Tihar offer, Facebook post', 'https://example.com/offers/tihar', 'tihar-fb', 'facebook', 'social', 'tihar-offer-2026', 'video-ad-a', 9),
    ('Tihar offer, Instagram story', 'https://example.com/offers/tihar', 'tihar-ig', 'instagram', 'social', 'tihar-offer-2026', 'story', 7),
    ('November newsletter', 'https://example.com/blog/november', 'nov-news', 'newsletter', 'email', 'november-newsletter', 'header-button', 5),
    ('Google Ads, brand search', 'https://example.com/', 'brand-ads', 'google', 'cpc', 'brand-search', 'ad-1', 6),
    ('WhatsApp broadcast', 'https://example.com/join', 'wa-join', 'whatsapp', 'messaging', 'community-launch', '', 3),
    ('LinkedIn launch post', 'https://example.com/launch', 'in-launch', 'linkedin', 'social', 'community-launch', 'post', 2),
]
REFERRERS = [('facebook.com', 5), ('instagram.com', 4), ('', 6), ('google.com', 4), ('linkedin.com', 2), ('t.co', 1)]
AGENTS = [
    ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148', 10),
    ('Mozilla/5.0 (Linux; Android 14; Pixel 8) Mobile Safari/537.36', 8),
    ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36', 7),
    ('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) Safari/605.1.15', 3),
    ('Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) Mobile/15E148', 1),
    ('facebookexternalhit/1.1', 1),
]


class Command(BaseCommand):
    help = 'Create a demo user ("demo") with sample links and click history. Development only.'

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('seed_demo only runs when DJANGO_DEBUG=True.')

        rng = random.Random(2026)  # nosec B311 - fake demo data
        user_model = get_user_model()
        user, _ = user_model.objects.get_or_create(username='demo', defaults={'email': 'demo@example.com'})
        user.set_password(DEMO_PASSWORD)
        user.save()
        user.links.all().delete()

        now = timezone.now()
        clicks = []
        for title, url, code, source, medium, campaign, content, weight in LINKS:
            link = Link.objects.create(
                owner=user, title=title, destination_url=url, code=code, utm_source=source,
                utm_medium=medium, utm_campaign=campaign, utm_content=content,
            )
            for days_ago in range(30):
                base = weight * (1 + 0.5 * (days_ago < 10)) * rng.uniform(0.2, 1.6)
                if days_ago % 7 in (5, 6):
                    base *= 0.6
                for _ in range(int(base)):
                    agent = rng.choices([a for a, _ in AGENTS], [w for _, w in AGENTS])[0]
                    clicks.append(Click(
                        link=link,
                        clicked_at=now - timedelta(days=days_ago, minutes=rng.randint(0, 1380)),
                        referrer_host=rng.choices([r for r, _ in REFERRERS], [w for _, w in REFERRERS])[0],
                        device=classify_device(agent),
                        visitor_hash=f'demo-{link.pk}-{rng.randint(0, 40)}-{days_ago}',
                    ))
        Click.objects.bulk_create(clicks)
        self.stdout.write(self.style.SUCCESS(
            f'Created {len(LINKS)} links and {len(clicks)} clicks. Log in as "demo" with password "{DEMO_PASSWORD}".'
        ))
