"""Validation for everything a user types that ends up in a redirect.

A link shortener is an open door for abuse unless the destination is checked, so the rules are strict:
only http and https, no embedded passwords, no private or local addresses, and never LinkPulse itself.
"""
import ipaddress
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError

MAX_URL_LENGTH = 2048

_CONTROL_OR_SPACE = re.compile(r'[\x00-\x20\x7f]')
_CODE_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{2,31}$')
_UTM_RE = re.compile(r'^[a-z0-9._-]{1,100}$')
_HAS_LETTER = re.compile(r'[a-z]')
_BLOCKED_SUFFIXES = ('.local', '.localhost', '.internal', '.lan', '.home', '.corp', '.intranet', '.test', '.invalid')


def _is_own_host(host):
    """True when `host` is one of this site's own hostnames (so a link could redirect to itself)."""
    for allowed in settings.ALLOWED_HOSTS:
        allowed = (allowed or '').lower().rstrip('.')
        if not allowed or allowed == '*':
            continue
        if allowed.startswith('.'):
            if host == allowed[1:] or host.endswith(allowed):
                return True
        elif host == allowed:
            return True
    return False


def validate_destination_url(value):
    value = value or ''
    if len(value) > MAX_URL_LENGTH:
        raise ValidationError(f'The web address is too long (the limit is {MAX_URL_LENGTH} characters).', code='too_long')
    if _CONTROL_OR_SPACE.search(value):
        raise ValidationError('The web address cannot contain spaces or control characters.', code='invalid')
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - reading it raises ValueError for a bad port
    except ValueError:
        raise ValidationError('That web address is not valid.', code='invalid') from None

    if parts.scheme.lower() not in ('http', 'https'):
        raise ValidationError('Only web addresses starting with http:// or https:// are allowed.', code='scheme')
    if parts.username is not None or parts.password is not None:
        raise ValidationError('Web addresses with a username or password are not allowed.', code='credentials')

    host = (parts.hostname or '').lower().rstrip('.')
    if not host:
        raise ValidationError('That web address has no website name.', code='invalid')

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None

    if ip is not None:
        if not ip.is_global:
            raise ValidationError('Addresses on a private or local network are not allowed.', code='private')
    else:
        last_label = host.rsplit('.', 1)[-1]
        if (
            '.' not in host
            or host == 'localhost'
            or host.endswith(_BLOCKED_SUFFIXES)
            or not _HAS_LETTER.search(last_label)
        ):
            raise ValidationError('Use a public website address, for example https://example.com/page.', code='private')

    if _is_own_host(host):
        raise ValidationError('A short link cannot point back at LinkPulse itself.', code='self')


def validate_code(value):
    if not _CODE_RE.match((value or '').lower()):
        raise ValidationError(
            'Use 3 to 32 letters, numbers, hyphens or underscores, starting with a letter or number.',
            code='invalid',
        )


def validate_utm(value):
    if value and not _UTM_RE.match(value):
        raise ValidationError(
            'Use lowercase letters, numbers, dots, hyphens or underscores (up to 100 characters).',
            code='invalid',
        )


def normalize_utm(value):
    """Turn 'Diwali Sale 2026!' into 'diwali-sale-2026' so reports stay consistent."""
    value = re.sub(r'\s+', '-', (value or '').strip().lower())
    value = re.sub(r'[^a-z0-9._-]', '', value)
    return re.sub(r'-{2,}', '-', value).strip('-')[:100]
