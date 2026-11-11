import secrets
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.db import models
from django.utils import timezone

from .validators import validate_code, validate_destination_url, validate_utm

# No look-alike characters (0/o, 1/l/i), so a code can be read out or typed without mistakes.
CODE_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'
CODE_LENGTH = 8  # 31**8 is about 850 billion possibilities, so codes cannot be guessed one by one
UTM_FIELDS = ('utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content')


def generate_code(length=CODE_LENGTH):
    return ''.join(secrets.choice(CODE_ALPHABET) for _ in range(length))


class Link(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='links')
    code = models.CharField(max_length=32, unique=True, blank=True, validators=[validate_code])
    destination_url = models.URLField(max_length=2048, validators=[validate_destination_url])
    title = models.CharField(max_length=120, blank=True)

    utm_source = models.CharField(max_length=100, blank=True, validators=[validate_utm])
    utm_medium = models.CharField(max_length=100, blank=True, validators=[validate_utm])
    utm_campaign = models.CharField(max_length=100, blank=True, validators=[validate_utm])
    utm_term = models.CharField(max_length=100, blank=True, validators=[validate_utm])
    utm_content = models.CharField(max_length=100, blank=True, validators=[validate_utm])

    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['owner', '-created_at'])]

    def __str__(self):
        return self.title or self.code

    def save(self, *args, **kwargs):
        if self.code:
            self.code = self.code.lower()
        else:
            for _ in range(10):
                candidate = generate_code()
                if not Link.objects.filter(code=candidate).exists():
                    self.code = candidate
                    break
            else:  # pragma: no cover - practically impossible
                raise RuntimeError('Could not generate a unique short code.')
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        return self.expires_at is not None and self.expires_at <= timezone.now()

    @property
    def is_live(self):
        return self.is_active and not self.is_expired

    @property
    def status(self):
        if self.is_expired:
            return 'expired'
        return 'live' if self.is_active else 'paused'

    @property
    def target_url(self):
        """The destination with the UTM tags added. Tags set here replace any already in the address."""
        parts = urlsplit(self.destination_url)
        tags = {name: getattr(self, name) for name in UTM_FIELDS if getattr(self, name)}
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in tags]
        query.extend(tags.items())
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class Click(models.Model):
    DEVICE_DESKTOP = 'desktop'
    DEVICE_MOBILE = 'mobile'
    DEVICE_TABLET = 'tablet'
    DEVICE_BOT = 'bot'
    DEVICE_OTHER = 'other'
    DEVICE_CHOICES = [
        (DEVICE_DESKTOP, 'Desktop'),
        (DEVICE_MOBILE, 'Mobile'),
        (DEVICE_TABLET, 'Tablet'),
        (DEVICE_BOT, 'Bot or preview'),
        (DEVICE_OTHER, 'Other'),
    ]

    link = models.ForeignKey(Link, on_delete=models.CASCADE, related_name='clicks')
    clicked_at = models.DateTimeField(default=timezone.now, db_index=True)
    # Privacy: no IP address and no user agent are stored. Only the website a visitor came from,
    # the kind of device, and a one-way daily hash that lets us count unique visitors.
    referrer_host = models.CharField(max_length=255, blank=True)
    device = models.CharField(max_length=10, choices=DEVICE_CHOICES, default=DEVICE_OTHER)
    visitor_hash = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ['-clicked_at']
        indexes = [models.Index(fields=['link', 'clicked_at'])]

    def __str__(self):
        return f'{self.link_id} @ {self.clicked_at:%Y-%m-%d %H:%M}'
