import csv
import io
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Click, Link
from .testutils import PASSWORD, add_clicks, make_link, make_user


class LoginRequiredTests(TestCase):
    def test_every_app_page_needs_a_login(self):
        link = make_link(make_user('owner'))
        urls = [
            reverse('links:dashboard'), reverse('links:list'), reverse('links:create'), reverse('links:export'),
            reverse('links:detail', args=[link.pk]), reverse('links:edit', args=[link.pk]),
            reverse('links:delete', args=[link.pk]), reverse('links:toggle', args=[link.pk]),
        ]
        for url in urls:
            with self.subTest(url=url):
                for method in (self.client.get, self.client.post):
                    response = method(url)
                    self.assertEqual(response.status_code, 302)
                    self.assertTrue(response['Location'].startswith(reverse('accounts:login')))
        self.assertTrue(Link.objects.filter(pk=link.pk).exists())


class LinkTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user('sam')
        self.client.force_login(self.user)


class DashboardTests(LinkTestCase):
    def test_a_new_account_sees_the_empty_state(self):
        self.assertContains(self.client.get(reverse('links:dashboard')), 'Create your first tracked link')

    def test_numbers_and_chart_for_an_account_with_clicks(self):
        link = make_link(self.user, title='Tihar post', utm_campaign='tihar', code='tihar1')
        add_clicks(link, 12, referrer='facebook.com', visitors=5)
        add_clicks(link, 3, device=Click.DEVICE_BOT)
        response = self.client.get(reverse('links:dashboard'))
        self.assertEqual(response.context['stats']['total'], 12)
        self.assertEqual(response.context['stats']['unique_visitors'], 5)
        self.assertEqual(response.context['stats']['bots'], 3)
        self.assertContains(response, 'facebook.com')
        self.assertContains(response, 'Tihar post')
        self.assertContains(response, '<svg class="chart-svg"')

    def test_only_my_clicks_are_counted(self):
        add_clicks(make_link(self.user), 2)
        add_clicks(make_link(make_user('other')), 50)
        self.assertEqual(self.client.get(reverse('links:dashboard')).context['stats']['total'], 2)

    def test_the_period_choice(self):
        add_clicks(make_link(self.user), 4, days_ago=20)
        self.assertEqual(self.client.get(reverse('links:dashboard'), {'days': '7'}).context['stats']['total'], 0)
        self.assertEqual(self.client.get(reverse('links:dashboard'), {'days': '30'}).context['stats']['total'], 4)
        for junk in ('abc', '1', '-5', '9999', ''):
            with self.subTest(days=junk):
                self.assertEqual(self.client.get(reverse('links:dashboard'), {'days': junk}).context['days'], 30)


class CreateLinkTests(LinkTestCase):
    DATA = {
        'destination_url': 'https://example.org/offer', 'title': 'Tihar post', 'code': '',
        'utm_source': 'Facebook', 'utm_medium': ' Social ', 'utm_campaign': 'Tihar Offer 2026!', 'utm_content': '',
        'utm_term': '', 'expires_at': '', 'is_active': 'on',
    }

    def _post(self, **changes):
        return self.client.post(reverse('links:create'), dict(self.DATA, **changes))

    def test_creating_a_link_tidies_the_tags_and_sets_the_owner(self):
        response = self._post()
        link = Link.objects.get()
        self.assertRedirects(response, reverse('links:detail', args=[link.pk]))
        self.assertEqual(link.owner, self.user)
        self.assertEqual((link.utm_source, link.utm_medium, link.utm_campaign), ('facebook', 'social', 'tihar-offer-2026'))
        self.assertEqual(len(link.code), 8)
        self.assertTrue(link.is_active)

    def test_a_custom_code_is_lowercased_and_must_be_free(self):
        self._post(code='Diwali-Sale')
        self.assertEqual(Link.objects.get().code, 'diwali-sale')
        response = self._post(code='DIWALI-sale')
        self.assertContains(response, 'already taken')
        self.assertEqual(Link.objects.count(), 1)

    def test_bad_destinations_are_explained(self):
        cases = {
            'javascript:alert(1)': 'Enter a valid URL', 'https://user:pw@example.org': 'username or password',
            'http://192.168.0.1/': 'private or local', 'https://testserver/r/abc': 'Enter a valid URL',
        }
        for url, message in cases.items():
            with self.subTest(url=url):
                self.assertContains(self._post(destination_url=url), message)
        self.assertEqual(Link.objects.count(), 0)

    @override_settings(ALLOWED_HOSTS=['links.example.com'])
    def test_pointing_back_at_this_site_is_refused(self):
        response = self.client.post(reverse('links:create'), dict(self.DATA, destination_url='https://links.example.com/r/abc'),
                                    headers={'host': 'links.example.com'})
        self.assertContains(response, 'LinkPulse itself')

    def test_the_destination_is_required(self):
        self.assertContains(self._post(destination_url=''), 'This field is required')

    def test_codes_with_odd_characters_are_refused(self):
        self.assertContains(self._post(code='a b'), 'Use 3 to 32 letters')

    def test_the_expiry_must_be_in_the_future(self):
        past = (timezone.now() - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        self.assertContains(self._post(expires_at=past), 'future')
        future = (timezone.now() + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M')
        self._post(expires_at=future)
        self.assertIsNotNone(Link.objects.get().expires_at)

    @override_settings(MAX_LINKS_PER_USER=2)
    def test_the_number_of_links_is_limited(self):
        self._post(); self._post()
        self.assertContains(self._post(), 'limit of 2 links')
        self.assertEqual(Link.objects.count(), 2)

    def test_the_owner_cannot_be_set_from_the_form(self):
        other = make_user('other')
        self._post(owner=other.pk)
        self.assertEqual(Link.objects.get().owner, self.user)

    def test_the_address_can_be_prefilled(self):
        response = self.client.get(reverse('links:create'), {'destination_url': 'https://example.org/x'})
        self.assertContains(response, 'value="https://example.org/x"')

    def test_the_form_shows_quick_fill_buttons(self):
        self.assertContains(self.client.get(reverse('links:create')), 'data-source="google" data-medium="cpc"')


class DetailAndEditTests(LinkTestCase):
    def setUp(self):
        super().setUp()
        self.link = make_link(self.user, title='Offer', code='offer1', utm_source='x')

    def test_detail_shows_the_short_link_and_numbers(self):
        add_clicks(self.link, 3, referrer='t.co')
        response = self.client.get(reverse('links:detail', args=[self.link.pk]))
        self.assertContains(response, 'http://testserver/r/offer1/')
        self.assertContains(response, 'utm_source=x')
        self.assertContains(response, 't.co')
        self.assertEqual(response.context['stats']['total'], 3)

    def test_editing_keeps_the_code_even_if_one_is_posted(self):
        response = self.client.post(reverse('links:edit', args=[self.link.pk]), {
            'destination_url': 'https://example.org/new', 'title': 'Renamed', 'code': 'hijacked',
            'utm_source': 'newsletter', 'is_active': 'on',
        })
        self.assertRedirects(response, reverse('links:detail', args=[self.link.pk]))
        self.link.refresh_from_db()
        self.assertEqual((self.link.code, self.link.title, self.link.utm_source), ('offer1', 'Renamed', 'newsletter'))
        self.assertEqual(self.link.destination_url, 'https://example.org/new')

    def test_editing_checks_the_destination_too(self):
        response = self.client.post(reverse('links:edit', args=[self.link.pk]), {'destination_url': 'http://localhost/', 'is_active': 'on'})
        self.assertEqual(response.status_code, 200)
        self.link.refresh_from_db()
        self.assertEqual(self.link.destination_url, 'https://example.org/offer')

    def test_unticking_active_pauses_the_link(self):
        self.client.post(reverse('links:edit', args=[self.link.pk]), {'destination_url': 'https://example.org/offer'})
        self.link.refresh_from_db()
        self.assertFalse(self.link.is_active)

    def test_toggle_needs_a_post_and_flips_the_state(self):
        url = reverse('links:toggle', args=[self.link.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url)
        self.link.refresh_from_db()
        self.assertFalse(self.link.is_active)
        self.client.post(url)
        self.link.refresh_from_db()
        self.assertTrue(self.link.is_active)

    def test_delete_asks_first_then_removes_the_link_and_its_clicks(self):
        add_clicks(self.link, 3)
        url = reverse('links:delete', args=[self.link.pk])
        self.assertContains(self.client.get(url), 'Delete this link?')
        self.assertTrue(Link.objects.filter(pk=self.link.pk).exists())
        self.assertRedirects(self.client.post(url), reverse('links:list'))
        self.assertFalse(Link.objects.filter(pk=self.link.pk).exists())
        self.assertEqual(Click.objects.count(), 0)


class OwnershipTests(LinkTestCase):
    """One user must never be able to see or change another user's links, whatever the address says."""

    def setUp(self):
        super().setUp()
        self.other = make_user('other')
        self.theirs = make_link(self.other, title='THEIR-SECRET-TITLE', code='theirs1')
        add_clicks(self.theirs, 5)

    def test_every_page_for_another_users_link_is_a_404(self):
        for name in ('detail', 'edit', 'delete', 'toggle'):
            for method in (self.client.get, self.client.post):
                if name == 'toggle' and method == self.client.get:
                    continue
                with self.subTest(page=name, method=method.__name__):
                    response = method(reverse(f'links:{name}', args=[self.theirs.pk]), {'destination_url': 'https://evil.example'})
                    self.assertEqual(response.status_code, 404)
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.destination_url, 'https://example.org/offer')
        self.assertTrue(self.theirs.is_active)
        self.assertEqual(self.theirs.clicks.count(), 5)

    def test_other_users_links_never_appear_in_lists_or_exports(self):
        mine = make_link(self.user, title='MINE')
        for url in (reverse('links:list'), reverse('links:dashboard'), reverse('links:export')):
            body = self.client.get(url).content.decode()
            self.assertNotIn('THEIR-SECRET-TITLE', body)
            self.assertNotIn('theirs1', body)
        self.assertContains(self.client.get(reverse('links:list')), mine.title)


class ListTests(LinkTestCase):
    def test_search(self):
        make_link(self.user, title='Tihar post', utm_campaign='tihar')
        make_link(self.user, title='Newsletter', destination_url='https://example.org/blog')
        self.assertContains(self.client.get(reverse('links:list'), {'q': 'tihar'}), 'Tihar post')
        self.assertNotContains(self.client.get(reverse('links:list'), {'q': 'tihar'}), 'Newsletter')
        self.assertContains(self.client.get(reverse('links:list'), {'q': '/blog'}), 'Newsletter')
        self.assertContains(self.client.get(reverse('links:list'), {'q': 'zzz'}), 'No links match')

    def test_status_filter(self):
        make_link(self.user, title='LIVE-ONE')
        make_link(self.user, title='PAUSED-ONE', is_active=False)
        make_link(self.user, title='EXPIRED-ONE', expires_at=timezone.now() - timedelta(days=1))
        expectations = {'live': 'LIVE-ONE', 'paused': 'PAUSED-ONE', 'expired': 'EXPIRED-ONE'}
        for status, title in expectations.items():
            body = self.client.get(reverse('links:list'), {'status': status}).content.decode()
            with self.subTest(status=status):
                self.assertIn(title, body)
                for other in expectations.values():
                    if other != title:
                        self.assertNotIn(other, body)

    def test_click_counts_leave_out_bots(self):
        link = make_link(self.user)
        add_clicks(link, 4)
        add_clicks(link, 6, device=Click.DEVICE_BOT)
        self.assertEqual(self.client.get(reverse('links:list')).context['page'].object_list[0].total_clicks, 4)

    def test_pagination(self):
        for index in range(20):
            make_link(self.user, title=f'Link {index}')
        first = self.client.get(reverse('links:list'))
        self.assertEqual(len(first.context['page'].object_list), 15)
        second = self.client.get(reverse('links:list'), {'page': 2})
        self.assertEqual(len(second.context['page'].object_list), 5)
        self.assertEqual(self.client.get(reverse('links:list'), {'page': 'junk'}).status_code, 200)

    def test_an_unknown_status_value_is_ignored(self):
        make_link(self.user, title='VISIBLE')
        self.assertContains(self.client.get(reverse('links:list'), {'status': "'; DROP TABLE"}), 'VISIBLE')


class OutputEscapingTests(LinkTestCase):
    """Text typed by users must never run as code in someone's browser."""

    PAYLOAD = '<script>alert("xss")</script>'

    def test_titles_are_escaped_everywhere(self):
        link = make_link(self.user, title=self.PAYLOAD)
        add_clicks(link, 2, referrer='evil.example')
        for url in (reverse('links:list'), reverse('links:detail', args=[link.pk]), reverse('links:edit', args=[link.pk]),
                    reverse('links:delete', args=[link.pk]), reverse('links:dashboard')):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertNotContains(response, self.PAYLOAD)

    def test_the_search_text_is_escaped(self):
        response = self.client.get(reverse('links:list'), {'q': '"><script>alert(1)</script>'})
        self.assertNotContains(response, '<script>alert(1)</script>')

    def test_a_malicious_referrer_is_escaped(self):
        link = make_link(self.user)
        Click.objects.create(link=link, referrer_host='<img src=x onerror=alert(1)>', device='desktop')
        response = self.client.get(reverse('links:detail', args=[link.pk]))
        self.assertNotContains(response, '<img src=x')


class ExportTests(LinkTestCase):
    def _rows(self):
        response = self.client.get(reverse('links:export'))
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertTrue(response['Content-Type'].startswith('text/csv'))
        return list(csv.DictReader(io.StringIO(response.content.decode())))

    def test_export_contains_my_links_with_click_counts(self):
        link = make_link(self.user, title='Tihar', code='tihar1', utm_source='facebook')
        add_clicks(link, 5, visitors=2)
        add_clicks(link, 3, device=Click.DEVICE_BOT)
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['short_url'], 'http://testserver/r/tihar1/')
        self.assertEqual((rows[0]['clicks'], rows[0]['unique_visitors'], rows[0]['status']), ('5', '2', 'live'))
        self.assertIn('utm_source=facebook', rows[0]['full_tracking_url'])

    def test_cells_that_look_like_formulas_are_defused(self):
        make_link(self.user, title='=HYPERLINK("http://evil.example","click")')
        make_link(self.user, title='+1+1')
        make_link(self.user, title='@SUM(A1)')
        make_link(self.user, title='-2+3')
        for row in self._rows():
            self.assertTrue(row['name'].startswith("'"), row['name'])

    def test_login_is_needed(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('links:export')).status_code, 302)
