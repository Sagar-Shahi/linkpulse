"""On-demand check that a link's destination still works.

This is the only place LinkPulse contacts another website, and it only runs when the owner presses
"Check destination". Because the server makes the request, it is locked down against server-side
request forgery (SSRF):

* every address in the chain (the link itself and each redirect) must pass the same rules as when
  the link was created: http or https, a public website name, no credentials, not LinkPulse itself;
* the host name is resolved here and every resulting IP must be a public address; the connection is
  then made to that exact IP, so a second DNS answer cannot swap in a private one (DNS rebinding);
* at most 3 redirects, a 5 second timeout per step, HEAD requests, and the page body is never read.
"""
import http.client
import socket
import ssl
from urllib.parse import urljoin, urlsplit

import ipaddress
from django.core.exceptions import ValidationError

from .validators import validate_destination_url

TIMEOUT = 5
MAX_REDIRECTS = 3
USER_AGENT = 'LinkPulse-DestinationCheck/1.0 (+owner-initiated, no page content is stored)'

OK, RESTRICTED, BROKEN, UNREACHABLE = 'ok', 'restricted', 'broken', 'unreachable'
LABELS = {
    OK: 'Working',
    RESTRICTED: 'Responds, but blocks automated checks',
    BROKEN: 'Broken',
    UNREACHABLE: 'Could not be reached',
}


class Unsafe(Exception):
    """The address, or a redirect target, is not allowed to be contacted."""


def resolve_public(host, port, resolver=socket.getaddrinfo):
    """Return one public IP for `host`, or raise Unsafe if any answer is not a public address."""
    addresses = []
    for info in resolver(host, port, type=socket.SOCK_STREAM):
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise Unsafe('The website name points to a private or local address.')
        addresses.append(str(ip))
    if not addresses:
        raise Unsafe('The website name did not resolve.')
    return addresses[0]


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, ip, timeout):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, ip, timeout):
        self._tls = ssl.create_default_context()
        super().__init__(host, port, timeout=timeout, context=self._tls)
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._tls.wrap_socket(sock, server_hostname=self.host)


def fetch(scheme, host, port, ip, target, method):
    """One request to a pinned IP. Returns (status code, Location header or '')."""
    connection_class = _PinnedHTTPS if scheme == 'https' else _PinnedHTTP
    connection = connection_class(host, port, ip, TIMEOUT)
    try:
        connection.request(method, target, headers={'User-Agent': USER_AGENT, 'Accept': '*/*', 'Connection': 'close'})
        response = connection.getresponse()
        return response.status, response.getheader('Location') or ''
    finally:
        connection.close()


def check(url, resolver=socket.getaddrinfo, request=fetch):
    """Check `url` and return {'status': ..., 'code': int or None, 'detail': str}."""
    code = None
    try:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                validate_destination_url(url)
            except ValidationError as error:
                raise Unsafe(error.messages[0]) from None
            parts = urlsplit(url)
            port = parts.port or (443 if parts.scheme == 'https' else 80)
            ip = resolve_public(parts.hostname, port, resolver)
            target = (parts.path or '/') + (f'?{parts.query}' if parts.query else '')
            code, location = request(parts.scheme, parts.hostname, port, ip, target, 'HEAD')
            if code in (405, 501):  # some servers refuse HEAD; ask again with GET (the body is still never read)
                code, location = request(parts.scheme, parts.hostname, port, ip, target, 'GET')
            if 300 <= code < 400 and location:
                url = urljoin(url, location)
                continue
            break
        else:
            return {'status': BROKEN, 'code': code, 'detail': 'Too many redirects.'}
    except Unsafe as error:
        return {'status': UNREACHABLE, 'code': None, 'detail': str(error)}
    except (OSError, ssl.SSLError, http.client.HTTPException, ValueError, UnicodeError):
        return {'status': UNREACHABLE, 'code': None, 'detail': 'The website did not answer in time or refused the connection.'}

    if code < 400:
        return {'status': OK, 'code': code, 'detail': ''}
    if code in (401, 403, 429):
        return {'status': RESTRICTED, 'code': code, 'detail': 'The website answered but may block automated checks.'}
    return {'status': BROKEN, 'code': code, 'detail': ''}
