from datetime import timedelta
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils import timezone

from .models import CODE_ALPHABET, CODE_LENGTH, Click, Link, generate_code
from .testutils import make_link, make_user
from .tracking import classify_device, record_click, referrer_host, visitor_hash


class CodeTests(TestCase):
    def test_generated_codes_are_long_and_use_only_clear_characters(self):
        codes = {generate_code() for _ in range(300)}
        self.assertEqual(len(codes), 300)
        for code in codes:
            self.assertEqual(len(code), CODE_LENGTH)
            self.assertTrue(set(code) <= set(CODE_ALPHABET))
        self.assertFalse(set('0o1li') & set(CODE_ALPHABET))

    def test_a_code_is_created_on_save_and_custom_codes_are_lowercased(self):
        user = make_user()
        self.assertEqual(len(make_link(user).code), CODE_LENGTH)
        self.assertEqual(make_link(user, code='Diwali-Sale').code, 'diwali-sale')

    def test_codes_are_unique(self):
        user = make_user()
        make_link(user, code='same-code')
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_link(user, code='same-code')

    def test_the_model_refuses_bad_destinations(self):
        link = Link(owner=make_user(), destination_url='http://127.0.0.1/admin', code='abc')
        with self.assertRaises(ValidationError):
            link.full_clean()


class TargetUrlTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_without_tags_the_address_is_unchanged(self):
        link = make_link(self.user, destination_url='https://example.org/offer?ref=1#top')
        self.assertEqual(link.target_url, 'https://example.org/offer?ref=1#top')

    def test_tags_are_added_and_other_parameters_kept(self):
        link = make_link(self.user, destination_url='https://example.org/offer?ref=1#top',
                         utm_source='facebook', utm_medium='social', utm_campaign='tihar-offer-2026')
        self.assertEqual(
            link.target_url,
            'https://example.org/offer?ref=1&utm_source=facebook&utm_medium=social&utm_campaign=tihar-offer-2026#top',
        )

    def test_tags_replace_ones_already_in_the_address(self):
        link = make_link(self.user, destination_url='https://example.org/?utm_source=old&x=1', utm_source='new')
        self.assertEqual(link.target_url, 'https://example.org/?x=1&utm_source=new')

    def test_tags_that_are_not_set_leave_existing_ones_alone(self):
        link = make_link(self.user, destination_url='https://example.org/?utm_term=kept', utm_source='new')
        self.assertIn('utm_term=kept', link.target_url)

    def test_values_are_encoded(self):
        link = make_link(self.user, destination_url='https://example.org/?q=a%26b%20c')
        self.assertEqual(link.target_url, 'https://example.org/?q=a%26b+c')


class StatusTests(TestCase):
    def test_status_follows_active_and_expiry(self):
        user = make_user()
        live = make_link(user)
        paused = make_link(user, is_active=False)
        expired = make_link(user, expires_at=timezone.now() - timedelta(minutes=1))
        later = make_link(user, expires_at=timezone.now() + timedelta(days=1))
        self.assertEqual([x.status for x in (live, paused, expired, later)], ['live', 'paused', 'expired', 'live'])
        self.assertEqual([x.is_live for x in (live, paused, expired, later)], [True, False, False, True])


class DeviceTests(SimpleTestCase):
    def test_classify_device(self):
        cases = {
            'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148': 'mobile',
            'Mozilla/5.0 (Linux; Android 14; Pixel 8) Mobile Safari/537.36': 'mobile',
            'Mozilla/5.0 (Linux; Android 13; SM-X700) Safari/537.36': 'tablet',
            'Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) Mobile/15E148': 'tablet',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36': 'desktop',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) Safari/605.1.15': 'desktop',
            'Mozilla/5.0 (X11; Linux x86_64) Firefox/125.0': 'desktop',
            'Googlebot/2.1 (+http://www.google.com/bot.html)': 'bot',
            'facebookexternalhit/1.1': 'bot',
            'WhatsApp/2.23': 'bot',
            'curl/8.4.0': 'bot',
            'python-requests/2.31': 'bot',
            'Slackbot-LinkExpanding 1.0': 'bot',
            '': 'bot',
            'SomethingElse/1.0': 'other',
        }
        for agent, expected in cases.items():
            with self.subTest(agent=agent):
                self.assertEqual(classify_device(agent), expected)

    def test_a_missing_user_agent_counts_as_a_bot(self):
        self.assertEqual(classify_device(None), Click.DEVICE_BOT)


class ReferrerTests(SimpleTestCase):
    def _host(self, referer=None):
        extra = {'HTTP_REFERER': referer} if referer is not None else {}
        return referrer_host(RequestFactory().get('/', **extra))

    def test_only_the_site_name_is_kept(self):
        self.assertEqual(self._host('https://www.Instagram.com/p/secret-post?token=abc'), 'instagram.com')
        self.assertEqual(self._host('https://l.facebook.com/l.php?u=x'), 'l.facebook.com')

    def test_missing_or_broken_referrers_become_empty(self):
        self.assertEqual(self._host(), '')
        self.assertEqual(self._host(''), '')
        self.assertEqual(self._host('not a url'), '')
        self.assertEqual(self._host('http://[::1'), '')

    def test_very_long_hosts_are_cut(self):
        self.assertLessEqual(len(self._host('https://' + 'a' * 400 + '.com/')), 255)


class VisitorHashTests(TestCase):
    def setUp(self):
        self.link = make_link(make_user())
        self.now = timezone.now()

    def _hash(self, ip='203.0.113.9', agent='Mozilla/5.0', link=None, when=None):
        request = RequestFactory().get('/', REMOTE_ADDR=ip, HTTP_USER_AGENT=agent)
        return visitor_hash(request, link or self.link, when or self.now)

    def test_same_visitor_same_day_gives_the_same_hash(self):
        self.assertEqual(self._hash(), self._hash())

    def test_a_different_day_visitor_browser_or_link_gives_a_different_hash(self):
        base = self._hash()
        self.assertNotEqual(base, self._hash(when=self.now + timedelta(days=1)))
        self.assertNotEqual(base, self._hash(ip='203.0.113.10'))
        self.assertNotEqual(base, self._hash(agent='Other/1.0'))
        self.assertNotEqual(base, self._hash(link=make_link(self.link.owner)))

    def test_the_hash_does_not_contain_the_address(self):
        value = self._hash()
        self.assertRegex(value, r'^[0-9a-f]{64}$')
        self.assertNotIn('203.0.113.9', value)


class RecordClickTests(TestCase):
    def setUp(self):
        self.link = make_link(make_user())

    def test_a_click_stores_no_address_and_no_user_agent(self):
        request = RequestFactory().get('/', REMOTE_ADDR='203.0.113.9', HTTP_USER_AGENT='Mozilla/5.0 (iPhone) Mobile',
                                       HTTP_REFERER='https://www.facebook.com/some/post')
        record_click(request, self.link)
        click = Click.objects.get()
        self.assertEqual((click.referrer_host, click.device), ('facebook.com', 'mobile'))
        stored = ' '.join(str(v) for v in Click.objects.values().get().values())
        self.assertNotIn('203.0.113.9', stored)
        self.assertNotIn('Mozilla', stored)
        self.assertNotIn('some/post', stored)

    def test_a_failure_is_logged_and_never_raised(self):
        request = RequestFactory().get('/')
        with mock.patch('links.tracking.Click.objects.create', side_effect=RuntimeError('db down')):
            with self.assertLogs('links.tracking', level='ERROR'):
                record_click(request, self.link)
        self.assertEqual(Click.objects.count(), 0)
