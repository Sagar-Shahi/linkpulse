"""Helpers shared by the test modules."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import Click, Link

PASSWORD = 'S3cure-pass-91-xyz'


def make_user(name='sam'):
    return get_user_model().objects.create_user(name, f'{name}@example.com', PASSWORD)


def make_link(owner, **extra):
    values = {'destination_url': 'https://example.org/offer', 'title': 'Offer'}
    values.update(extra)
    return Link.objects.create(owner=owner, **values)


def add_clicks(link, count, device=Click.DEVICE_DESKTOP, referrer='', days_ago=0, visitors=None):
    """Create `count` clicks `days_ago` days ago. `visitors` is how many different visitors made them."""
    when = timezone.now() - timedelta(days=days_ago, minutes=5)
    visitors = visitors or count
    Click.objects.bulk_create([
        Click(link=link, clicked_at=when, referrer_host=referrer, device=device, visitor_hash=f'v{i % visitors}-{days_ago}')
        for i in range(count)
    ])
