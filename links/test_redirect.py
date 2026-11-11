from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Click, Link
from .testutils import make_link, make_user

MOBILE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148'


class RedirectTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user()
        self.link = make_link(
            self.owner, destination_url='https://example.org/offer?ref=1', code='tihar-fb',
            utm_source='facebook', utm_medium='social', utm_campaign='tihar-offer-2026',
        )
        self.url = reverse('links:redirect', args=['tihar-fb'])

    def test_a_visit_redirects_to_the_tagged_address_and_is_counted(self):
        response = self.client.get(self.url, HTTP_USER_AGENT=MOBILE, HTTP_REFERER='https://www.facebook.com/post/1')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            'https://example.org/offer?ref=1&utm_source=facebook&utm_medium=social&utm_campaign=tihar-offer-2026',
        )
        click = Click.objects.get()
        self.assertEqual((click.link, click.device, click.referrer_host), (self.link, 'mobile', 'facebook.com'))

    def test_the_code_is_not_case_sensitive(self):
        self.assertEqual(self.client.get(reverse('links:redirect', args=['TIHAR-FB'])).status_code, 302)

    def test_redirects_are_never_cached_so_every_visit_is_counted(self):
        self.assertEqual(self.client.get(self.url)['Cache-Control'], 'no-store')

    def test_visitors_get_no_cookies(self):
        response = self.client.get(self.url)
        self.assertEqual(len(response.cookies), 0)

    def test_head_requests_and_previews_are_not_counted_as_visits(self):
        self.assertEqual(self.client.head(self.url).status_code, 302)
        self.assertEqual(Click.objects.count(), 0)

    def test_only_safe_methods_are_allowed(self):
        for method in ('post', 'put', 'delete', 'patch'):
            with self.subTest(method=method):
                self.assertEqual(getattr(self.client, method)(self.url).status_code, 405)
        self.assertEqual(Click.objects.count(), 0)

    def test_bots_are_recorded_separately(self):
        self.client.get(self.url, HTTP_USER_AGENT='facebookexternalhit/1.1')
        self.assertEqual(Click.objects.get().device, Click.DEVICE_BOT)

    def test_unknown_codes_show_a_404_page(self):
        response = self.client.get(reverse('links:redirect', args=['nope1234']))
        self.assertContains(response, 'does not exist', status_code=404)
        self.assertEqual(Click.objects.count(), 0)

    def test_paused_links_show_a_410_page_and_are_not_counted(self):
        Link.objects.filter(pk=self.link.pk).update(is_active=False)
        response = self.client.get(self.url)
        self.assertContains(response, 'no longer available', status_code=410)
        self.assertEqual(Click.objects.count(), 0)

    def test_expired_links_show_a_410_page(self):
        Link.objects.filter(pk=self.link.pk).update(expires_at=timezone.now() - timedelta(seconds=5))
        self.assertEqual(self.client.get(self.url).status_code, 410)

    def test_a_link_that_has_not_expired_yet_works(self):
        Link.objects.filter(pk=self.link.pk).update(expires_at=timezone.now() + timedelta(hours=1))
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_a_bad_address_that_got_into_the_database_is_never_followed(self):
        Link.objects.filter(pk=self.link.pk).update(destination_url='javascript:alert(document.cookie)')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 410)
        self.assertNotIn('Location', response)

    def test_a_problem_saving_the_click_does_not_stop_the_visitor(self):
        with mock.patch('links.tracking.Click.objects.create', side_effect=RuntimeError('db busy')):
            with self.assertLogs('links.tracking', level='ERROR'):
                response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    @override_settings(REDIRECT_MAX_PER_MINUTE=3)
    def test_one_address_cannot_flood_a_link_with_clicks(self):
        statuses = [self.client.get(self.url, REMOTE_ADDR='203.0.113.50').status_code for _ in range(5)]
        self.assertEqual(statuses, [302, 302, 302, 429, 429])
        self.assertEqual(Click.objects.count(), 3)
        response = self.client.get(self.url, REMOTE_ADDR='203.0.113.50')
        self.assertEqual(response['Retry-After'], '60')

    @override_settings(REDIRECT_MAX_PER_MINUTE=3)
    def test_other_visitors_are_not_affected_by_the_limit(self):
        for _ in range(5):
            self.client.get(self.url, REMOTE_ADDR='203.0.113.50')
        self.assertEqual(self.client.get(self.url, REMOTE_ADDR='203.0.113.51').status_code, 302)

    @override_settings(REDIRECT_MAX_PER_MINUTE=3, TRUSTED_PROXY_COUNT=1)
    def test_behind_a_proxy_visitors_are_told_apart_by_the_forwarded_address(self):
        for _ in range(5):
            self.client.get(self.url, REMOTE_ADDR='10.0.0.1', HTTP_X_FORWARDED_FOR='198.51.100.1')
        other = self.client.get(self.url, REMOTE_ADDR='10.0.0.1', HTTP_X_FORWARDED_FOR='198.51.100.2')
        self.assertEqual(other.status_code, 302)

    def test_the_redirect_does_not_run_extra_queries(self):
        with self.assertNumQueries(2):  # one to find the link, one to save the click
            self.client.get(self.url)
