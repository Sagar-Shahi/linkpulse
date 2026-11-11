"""Recording a click, without keeping personal data."""
import hashlib
import hmac
import logging
from urllib.parse import urlsplit

from django.conf import settings
from django.utils import timezone

from core.throttle import client_ip

from .models import Click

logger = logging.getLogger(__name__)

_BOT_WORDS = (
    'bot', 'spider', 'crawl', 'slurp', 'preview', 'facebookexternalhit', 'whatsapp', 'telegram',
    'headless', 'curl/', 'wget/', 'python-requests', 'python-urllib', 'go-http-client', 'java/',
    'monitor', 'uptime', 'pingdom', 'lighthouse', 'scrapy', 'httpclient', 'libwww',
)


def classify_device(user_agent):
    ua = (user_agent or '').lower()
    if not ua or any(word in ua for word in _BOT_WORDS):
        return Click.DEVICE_BOT
    if 'ipad' in ua or 'tablet' in ua or ('android' in ua and 'mobile' not in ua):
        return Click.DEVICE_TABLET
    if 'mobi' in ua or 'iphone' in ua or 'android' in ua:
        return Click.DEVICE_MOBILE
    if any(word in ua for word in ('windows', 'macintosh', 'x11', 'linux', 'cros')):
        return Click.DEVICE_DESKTOP
    return Click.DEVICE_OTHER


def referrer_host(request):
    """Only the website name from the Referer header, for example 'instagram.com'. Never the full path."""
    raw = request.META.get('HTTP_REFERER', '')
    if not raw:
        return ''
    try:
        host = (urlsplit(raw).hostname or '').lower()
    except ValueError:
        return ''
    if host.startswith('www.'):
        host = host[4:]
    return host[:255]


def visitor_hash(request, link, when=None):
    """A one-way fingerprint that changes every day, so unique visitors can be counted per day
    but nobody can be followed across days, and the IP address itself is never stored."""
    day = (when or timezone.now()).strftime('%Y-%m-%d')
    material = '|'.join([day, str(link.pk), client_ip(request), request.META.get('HTTP_USER_AGENT', '')])
    return hmac.new(settings.SECRET_KEY.encode('utf-8'), material.encode('utf-8'), hashlib.sha256).hexdigest()


def record_click(request, link):
    """Save one click. A problem here must never stop the visitor reaching the destination."""
    try:
        Click.objects.create(
            link=link,
            referrer_host=referrer_host(request),
            device=classify_device(request.META.get('HTTP_USER_AGENT', '')),
            visitor_hash=visitor_hash(request, link),
        )
    except Exception:  # noqa: BLE001
        logger.exception('Could not record a click for link %s', link.pk)
