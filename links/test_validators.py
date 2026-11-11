from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, override_settings

from .validators import normalize_utm, validate_code, validate_destination_url, validate_utm


class DestinationUrlTests(SimpleTestCase):
    def test_ordinary_public_addresses_are_accepted(self):
        for url in (
            'https://example.org/path?a=1&b=2#top', 'http://sub.example.co.uk', 'https://example.org:8443/x',
            'HTTPS://Example.ORG/Path', 'https://8.8.8.8/', 'https://xn--p1ai.xn--p1ai/', 'https://example.org/a%20b',
        ):
            with self.subTest(url=url):
                validate_destination_url(url)

    def test_dangerous_or_unsupported_schemes_are_refused(self):
        for url in ('javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'data:text/html,<script>1</script>', 'ftp://example.org/f',
                    'file:///etc/passwd', 'vbscript:x', '//example.org/x', 'example.org/no-scheme'):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                validate_destination_url(url)

    def test_embedded_passwords_are_refused(self):
        for url in ('https://user:pass@example.org/', 'https://user@example.org/', 'https://example.org@evil.example/'):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                validate_destination_url(url)

    def test_private_and_local_addresses_are_refused(self):
        for url in (
            'https://localhost/', 'http://LOCALHOST:8000/', 'http://127.0.0.1/', 'http://127.1.2.3/', 'http://10.0.0.1/',
            'http://192.168.1.5/', 'http://172.16.0.9/', 'http://169.254.169.254/latest/meta-data/', 'http://0.0.0.0/',
            'http://[::1]/', 'http://[fe80::1]/', 'https://intranet/', 'https://printer.local/', 'https://app.internal/',
            'http://0x7f.1/', 'http://2130706433/',
        ):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                validate_destination_url(url)

    def test_malformed_addresses_are_refused(self):
        for url in ('https://', 'https:///path', 'https://exa mple.org', 'https://example.org/\n', 'https://example.org/\t',
                    'https://example.org:99999/', 'https://example.org:abc/', 'http://[::1', '', 'https://example.org/\x00'):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                validate_destination_url(url)

    def test_very_long_addresses_are_refused(self):
        validate_destination_url('https://example.org/' + 'a' * 2000)
        with self.assertRaises(ValidationError):
            validate_destination_url('https://example.org/' + 'a' * 2100)

    @override_settings(ALLOWED_HOSTS=['links.example.com', '.myapp.io', '*'])
    def test_a_link_cannot_point_back_at_this_site(self):
        for url in ('https://links.example.com/r/abc', 'https://LINKS.example.com./x', 'https://myapp.io/', 'https://a.b.myapp.io/'):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                validate_destination_url(url)
        validate_destination_url('https://example.com/')  # a different site is fine
        validate_destination_url('https://notmyapp.io/')


class CodeAndTagTests(SimpleTestCase):
    def test_codes(self):
        for good in ('abc', 'Sale-2026', 'a_b-c', 'x' * 32, '2026-sale'):
            validate_code(good)
        for bad in ('ab', '-abc', '_abc', 'a b c', 'x' * 33, 'café', 'a/b', '', 'a.b.c'):
            with self.subTest(code=bad), self.assertRaises(ValidationError):
                validate_code(bad)

    def test_utm_values(self):
        for good in ('facebook', 'tihar-offer-2026', 'a.b_c', ''):
            validate_utm(good)
        for bad in ('Diwali Sale', 'UPPER', 'a&b=c', 'x' * 101, '<script>'):
            with self.subTest(value=bad), self.assertRaises(ValidationError):
                validate_utm(bad)

    def test_normalize_utm(self):
        cases = {
            'Diwali Sale 2026!': 'diwali-sale-2026', '  FB  ': 'fb', '--a--b--': 'a-b', '': '', None: '',
            'a&b=c': 'abc', 'New   Year': 'new-year', '<script>x</script>': 'scriptxscript', 'x' * 150: 'x' * 100,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_utm(raw), expected)
                validate_utm(normalize_utm(raw))
