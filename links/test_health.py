import socket
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from . import health
from .models import Link
from .testutils import PASSWORD, make_link, make_user

PUBLIC = '93.184.216.34'


def fake_resolver(mapping):
    """A DNS stand-in: host name -> list of IPs."""
    def resolver(host, port, type=None):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, port)) for ip in mapping[host]]
    return resolver


class Recorder:
    """A request stand-in that returns scripted answers and remembers what it was asked."""
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, scheme, host, port, ip, target, method):
        self.calls.append((scheme, host, port, ip, target, method))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class ResolveTests(SimpleTestCase):
    def test_private_and_special_addresses_are_refused(self):
        for ip in ('127.0.0.1', '10.0.0.5', '192.168.1.1', '172.16.0.9', '169.254.169.254', '0.0.0.0', '100.64.0.1'):
            with self.subTest(ip=ip), self.assertRaises(health.Unsafe):
                health.resolve_public('x.test', 80, fake_resolver({'x.test': [ip]}))
        with self.assertRaises(health.Unsafe):
            health.resolve_public('x.test', 80, fake_resolver({'x.test': ['::1']}))

    def test_one_private_answer_among_public_ones_is_refused(self):
        with self.assertRaises(health.Unsafe):
            health.resolve_public('x.test', 80, fake_resolver({'x.test': [PUBLIC, '10.0.0.1']}))

    def test_public_address_is_returned(self):
        self.assertEqual(health.resolve_public('x.test', 80, fake_resolver({'x.test': [PUBLIC]})), PUBLIC)

    def test_empty_answer_is_refused(self):
        with self.assertRaises(health.Unsafe):
            health.resolve_public('x.test', 80, fake_resolver({'x.test': []}))


class CheckTests(SimpleTestCase):
    dns = staticmethod(fake_resolver({'example.org': [PUBLIC], 'other.example': [PUBLIC], 'evil.example': ['10.1.2.3']}))

    def run_check(self, url, *answers):
        recorder = Recorder(*answers)
        return health.check(url, resolver=self.dns, request=recorder), recorder

    def test_working_destination(self):
        result, recorder = self.run_check('https://example.org/offer?a=1', (200, ''))
        self.assertEqual(result, {'status': 'ok', 'code': 200, 'detail': ''})
        self.assertEqual(recorder.calls, [('https', 'example.org', 443, PUBLIC, '/offer?a=1', 'HEAD')])

    def test_status_mapping(self):
        for code, status in ((404, 'broken'), (410, 'broken'), (500, 'broken'), (503, 'broken'),
                             (403, 'restricted'), (401, 'restricted'), (429, 'restricted'), (204, 'ok')):
            with self.subTest(code=code):
                result, _ = self.run_check('https://example.org/', (code, ''))
                self.assertEqual((result['status'], result['code']), (status, code))

    def test_head_refused_falls_back_to_get(self):
        result, recorder = self.run_check('http://example.org/', (405, ''), (200, ''))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual([c[5] for c in recorder.calls], ['HEAD', 'GET'])
        self.assertEqual(recorder.calls[0][2], 80)

    def test_follows_redirects_including_relative_ones(self):
        result, recorder = self.run_check(
            'https://example.org/a', (301, 'https://other.example/b'), (302, '/c'), (200, ''))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual([(c[1], c[4]) for c in recorder.calls], [('example.org', '/a'), ('other.example', '/b'), ('other.example', '/c')])

    def test_too_many_redirects(self):
        result, recorder = self.run_check('https://example.org/', *[(302, '/again')] * 10)
        self.assertEqual(result['status'], 'broken')
        self.assertIn('Too many', result['detail'])
        self.assertEqual(len(recorder.calls), health.MAX_REDIRECTS + 1)

    def test_redirect_to_a_private_address_is_never_contacted(self):
        result, recorder = self.run_check('https://example.org/', (302, 'http://evil.example/secret'))
        self.assertEqual(result['status'], 'unreachable')
        self.assertIn('private', result['detail'])
        self.assertEqual(len(recorder.calls), 1)

    def test_redirects_to_local_names_and_ip_literals_are_refused(self):
        for target in ('http://localhost/admin', 'http://127.0.0.1/', 'http://169.254.169.254/latest/meta-data/',
                       'http://[::1]/', 'http://printer.local/', 'http://intranet/', 'ftp://example.org/x', 'file:///etc/passwd'):
            with self.subTest(target=target):
                result, recorder = self.run_check('https://example.org/', (302, target))
                self.assertEqual(result['status'], 'unreachable')
                self.assertEqual(len(recorder.calls), 1)

    def test_a_private_start_address_is_never_contacted(self):
        result, recorder = self.run_check('http://10.0.0.1/', (200, ''))
        self.assertEqual(result['status'], 'unreachable')
        self.assertEqual(recorder.calls, [])

    def test_names_that_resolve_to_private_addresses_are_refused(self):
        result, recorder = self.run_check('https://evil.example/', (200, ''))
        self.assertEqual(result['status'], 'unreachable')
        self.assertEqual(recorder.calls, [])

    def test_network_errors_are_reported_not_raised(self):
        for error in (TimeoutError(), ConnectionRefusedError(), OSError('boom')):
            with self.subTest(error=type(error).__name__):
                result, _ = self.run_check('https://example.org/', error)
                self.assertEqual(result['status'], 'unreachable')
                self.assertIsNone(result['code'])

    def test_unresolvable_name_is_unreachable(self):
        def failing(host, port, type=None):
            raise socket.gaierror('no such host')
        self.assertEqual(health.check('https://example.org/', resolver=failing, request=Recorder())['status'], 'unreachable')


class PinnedConnectionTests(SimpleTestCase):
    def test_connects_to_the_checked_ip_not_to_a_new_dns_answer(self):
        with mock.patch('links.health.socket.create_connection') as connect:
            connection = health._PinnedHTTP('example.org', 80, PUBLIC, 5)
            connection.connect()
        connect.assert_called_once_with((PUBLIC, 80), 5)

    def test_https_uses_the_host_name_for_certificate_checks(self):
        with mock.patch('links.health.socket.create_connection') as connect, \
                mock.patch.object(health.ssl.SSLContext, 'wrap_socket') as wrap:
            connection = health._PinnedHTTPS('example.org', 443, PUBLIC, 5)
            connection.connect()
        connect.assert_called_once_with((PUBLIC, 443), 5)
        self.assertEqual(wrap.call_args.kwargs['server_hostname'], 'example.org')


class CheckViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.link = make_link(self.user)
        self.client.login(username='sam', password=PASSWORD)
        self.url = reverse('links:check', args=[self.link.pk])

    def post(self, status='ok', code=200):
        with mock.patch('links.views.health.check', return_value={'status': status, 'code': code, 'detail': ''}) as check:
            response = self.client.post(self.url)
        return response, check

    def test_saves_and_shows_the_result(self):
        response, check = self.post('broken', 404)
        self.assertRedirects(response, reverse('links:detail', args=[self.link.pk]))
        check.assert_called_once_with(self.link.destination_url)
        self.link.refresh_from_db()
        self.assertEqual((self.link.health_status, self.link.health_code), ('broken', 404))
        self.assertIsNotNone(self.link.health_checked_at)
        page = self.client.get(reverse('links:detail', args=[self.link.pk]))
        self.assertContains(page, 'Broken')
        self.assertContains(page, 'HTTP 404')

    def test_detail_says_not_checked_yet(self):
        self.assertContains(self.client.get(reverse('links:detail', args=[self.link.pk])), 'Not checked yet')

    def test_only_post_and_only_the_owner(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        stranger = make_link(make_user('other'))
        with mock.patch('links.views.health.check') as check:
            self.assertEqual(self.client.post(reverse('links:check', args=[stranger.pk])).status_code, 404)
        check.assert_not_called()
        self.assertEqual(Link.objects.get(pk=stranger.pk).health_status, '')

    def test_limited_to_ten_checks_an_hour(self):
        for _ in range(10):
            self.post()
        response, check = self.post()
        check.assert_not_called()
        self.assertRedirects(response, reverse('links:detail', args=[self.link.pk]), fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse('links:detail', args=[self.link.pk])), '10 destinations an hour')

    def test_visitors_are_sent_to_log_in(self):
        self.client.logout()
        with mock.patch('links.views.health.check') as check:
            self.assertEqual(self.client.post(self.url).status_code, 302)
        check.assert_not_called()
