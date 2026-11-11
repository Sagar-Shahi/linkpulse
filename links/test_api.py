from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import ApiKey
from links.models import Click, Link
from links.testutils import PASSWORD, add_clicks, make_link, make_user

LIST = '/api/v1/links/'


class ApiTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user('sam')
        self.key, self.raw = ApiKey.issue(self.user, 'test')
        self.api = APIClient()
        self.api.credentials(HTTP_AUTHORIZATION=f'Bearer {self.raw}')

    def other_client(self, name='other'):
        user = make_user(name)
        _, raw = ApiKey.issue(user, 'other')
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {raw}')
        return user, client

    def create(self, **extra):
        data = {'destination_url': 'https://example.org/offer', 'title': 'Offer'}
        data.update(extra)
        return self.api.post(LIST, data, format='json')


class AuthenticationTests(ApiTestCase):
    def test_no_credentials_means_401_with_a_bearer_hint(self):
        response = APIClient().get(LIST)
        self.assertEqual(response.status_code, 401)
        self.assertIn('Bearer', response['WWW-Authenticate'])

    def test_a_wrong_key_is_rejected_with_a_generic_message(self):
        client = APIClient()
        for bad in ('lp_' + 'a' * 43, 'nonsense', 'lp_', 'x' * 500):
            client.credentials(HTTP_AUTHORIZATION=f'Bearer {bad}')
            response = client.get(LIST)
            with self.subTest(key=bad[:10]):
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {'detail': 'Invalid API key.'})

    def test_a_revoked_key_stops_working_immediately(self):
        self.assertEqual(self.api.get(LIST).status_code, 200)
        self.key.revoke()
        self.assertEqual(self.api.get(LIST).status_code, 401)

    def test_keys_of_deactivated_accounts_stop_working(self):
        get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertEqual(self.api.get(LIST).status_code, 401)

    def test_only_the_bearer_scheme_is_accepted(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {self.raw}')
        self.assertEqual(client.get(LIST).status_code, 401)
        client.credentials(HTTP_AUTHORIZATION=f'Basic {self.raw}')
        self.assertEqual(client.get(LIST).status_code, 401)

    def test_the_key_is_not_accepted_in_the_address(self):
        self.assertEqual(APIClient().get(LIST, {'api_key': self.raw}).status_code, 401)

    def test_last_used_is_recorded_but_not_on_every_request(self):
        self.assertIsNone(self.key.last_used_at)
        self.api.get(LIST)
        self.key.refresh_from_db()
        first = self.key.last_used_at
        self.assertIsNotNone(first)
        self.api.get(LIST)
        self.key.refresh_from_db()
        self.assertEqual(self.key.last_used_at, first)

    @override_settings(API_KEY_FAILURES_PER_5_MIN=3)
    def test_guessing_keys_gets_an_address_blocked(self):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer lp_guess')
        statuses = [client.get(LIST).status_code for _ in range(5)]
        self.assertEqual(statuses, [401, 401, 401, 429, 429])
        self.assertEqual(self.api.get(LIST).status_code, 429)  # the same address, even with a real key
        self.assertEqual(self.api.get(LIST, REMOTE_ADDR='203.0.113.99').status_code, 200)

    def test_a_signed_in_browser_session_also_works(self):
        client = APIClient()
        client.login(username='sam', password=PASSWORD)
        self.assertEqual(client.get(LIST).status_code, 200)

    def test_browser_sessions_have_to_send_the_csrf_token(self):
        client = APIClient(enforce_csrf_checks=True)
        client.login(username='sam', password=PASSWORD)
        response = client.post(LIST, {'destination_url': 'https://example.org/'}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Link.objects.count(), 0)


class ReadTests(ApiTestCase):
    def test_list_has_pagination_and_only_my_links(self):
        make_link(self.user, title='mine-1'); make_link(self.user, title='mine-2')
        make_link(make_user('other'), title='THEIRS')
        body = self.api.get(LIST).json()
        self.assertEqual(body['count'], 2)
        self.assertEqual({r['title'] for r in body['results']}, {'mine-1', 'mine-2'})
        self.assertEqual(set(body), {'count', 'next', 'previous', 'results'})

    def test_page_size_is_capped(self):
        for index in range(3):
            make_link(self.user, title=f'l{index}')
        self.assertEqual(len(self.api.get(LIST, {'page_size': 2}).json()['results']), 2)
        self.assertEqual(self.api.get(LIST, {'page_size': 100000}).status_code, 200)

    def test_responses_contain_the_documented_fields_and_nothing_private(self):
        link = make_link(self.user, code='tihar1', utm_source='facebook')
        add_clicks(link, 4); add_clicks(link, 2, device=Click.DEVICE_BOT)
        item = self.api.get(f'{LIST}{link.pk}/').json()
        self.assertEqual(set(item), {
            'id', 'code', 'short_url', 'destination_url', 'title', 'utm_source', 'utm_medium', 'utm_campaign',
            'utm_term', 'utm_content', 'is_active', 'expires_at', 'click_count', 'created_at', 'updated_at',
        })
        self.assertEqual(item['short_url'], 'http://testserver/r/tihar1/')
        self.assertEqual(item['click_count'], 4)
        self.assertNotIn(self.raw, str(item))

    def test_responses_are_json_and_never_the_browsable_html_api(self):
        response = self.api.get(LIST, HTTP_ACCEPT='*/*')
        self.assertTrue(response['Content-Type'].startswith('application/json'))
        self.assertEqual(self.api.get(LIST, HTTP_ACCEPT='text/html').status_code, 406)

    def test_other_users_links_are_404(self):
        theirs = make_link(make_user('other'))
        self.assertEqual(self.api.get(f'{LIST}{theirs.pk}/').status_code, 404)
        self.assertEqual(self.api.get(f'{LIST}{theirs.pk}/stats/').status_code, 404)


class WriteTests(ApiTestCase):
    def test_create(self):
        response = self.create(code='Diwali-Sale', utm_source='newsletter', utm_medium='email')
        self.assertEqual(response.status_code, 201)
        link = Link.objects.get()
        self.assertEqual((link.owner, link.code, link.utm_source), (self.user, 'diwali-sale', 'newsletter'))
        self.assertEqual(response.json()['code'], 'diwali-sale')

    def test_a_random_code_is_made_when_none_is_given(self):
        self.assertEqual(len(self.create().json()['code']), 8)

    def test_fields_that_belong_to_the_server_cannot_be_set(self):
        other = make_user('other')
        response = self.create(owner=other.pk, id=9999, created_at='2001-01-01T00:00:00Z', click_count=500,
                               short_url='https://evil.example/', key_hash='x')
        self.assertEqual(response.status_code, 201)
        link = Link.objects.get()
        self.assertEqual(link.owner, self.user)
        self.assertNotEqual(link.pk, 9999)
        self.assertGreater(link.created_at.year, 2020)
        self.assertEqual(response.json()['click_count'], 0)

    def test_bad_input_is_refused_with_clear_errors(self):
        cases = [
            ({'destination_url': 'javascript:alert(1)'}, 'destination_url'),
            ({'destination_url': 'http://localhost:8000/'}, 'destination_url'),
            ({'destination_url': 'http://169.254.169.254/'}, 'destination_url'),
            ({'destination_url': 'https://testserver/r/x'}, 'destination_url'),
            ({'destination_url': ''}, 'destination_url'),
            ({'utm_source': 'Not Valid!'}, 'utm_source'),
            ({'code': 'a b'}, 'code'),
            ({'title': 'x' * 500}, 'title'),
            ({'expires_at': '2001-01-01T00:00:00Z'}, 'expires_at'),
            ({'expires_at': 'tomorrow'}, 'expires_at'),
        ]
        for extra, field in cases:
            with self.subTest(extra=extra):
                response = self.create(**extra)
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.json())
        self.assertEqual(Link.objects.count(), 0)

    def test_codes_must_be_unique_ignoring_case(self):
        self.create(code='taken-code')
        for code in ('taken-code', 'TAKEN-CODE'):
            self.assertEqual(self.create(code=code).status_code, 400)
        self.assertEqual(Link.objects.count(), 1)

    def test_an_unreadable_body_is_a_clean_400(self):
        response = self.api.post(LIST, 'not json', content_type='application/json')
        self.assertEqual(response.status_code, 400)
        response = self.api.post(LIST, '["a list"]', content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_form_encoded_bodies_are_not_accepted(self):
        response = self.api.post(LIST, {'destination_url': 'https://example.org/'})
        self.assertEqual(response.status_code, 415)

    @override_settings(MAX_LINKS_PER_USER=2)
    def test_the_link_limit_applies_to_the_api_too(self):
        self.create(); self.create()
        self.assertEqual(self.create().status_code, 400)
        self.assertEqual(Link.objects.count(), 2)

    def test_patch_changes_only_what_is_sent(self):
        link = Link.objects.get(pk=self.create(code='keep-me', utm_source='facebook').json()['id'])
        response = self.api.patch(f'{LIST}{link.pk}/', {'title': 'Renamed', 'is_active': False}, format='json')
        self.assertEqual(response.status_code, 200)
        link.refresh_from_db()
        self.assertEqual((link.title, link.is_active, link.utm_source, link.code), ('Renamed', False, 'facebook', 'keep-me'))

    def test_the_code_of_an_existing_link_cannot_change(self):
        link_id = self.create(code='keep-me').json()['id']
        response = self.api.patch(f'{LIST}{link_id}/', {'code': 'new-code'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.api.patch(f'{LIST}{link_id}/', {'code': 'KEEP-me'}, format='json').status_code, 200)

    def test_put_needs_every_required_field(self):
        link_id = self.create().json()['id']
        self.assertEqual(self.api.put(f'{LIST}{link_id}/', {'title': 'x'}, format='json').status_code, 400)
        ok = self.api.put(f'{LIST}{link_id}/', {'destination_url': 'https://example.org/new'}, format='json')
        self.assertEqual(ok.status_code, 200)

    def test_delete(self):
        link_id = self.create().json()['id']
        self.assertEqual(self.api.delete(f'{LIST}{link_id}/').status_code, 204)
        self.assertEqual(Link.objects.count(), 0)

    def test_nobody_can_change_or_delete_another_users_link(self):
        _, other_client = self.other_client()
        link_id = self.create().json()['id']
        self.assertEqual(other_client.patch(f'{LIST}{link_id}/', {'title': 'hacked'}, format='json').status_code, 404)
        self.assertEqual(other_client.put(f'{LIST}{link_id}/', {'destination_url': 'https://evil.example'}, format='json').status_code, 404)
        self.assertEqual(other_client.delete(f'{LIST}{link_id}/').status_code, 404)
        link = Link.objects.get()
        self.assertEqual((link.title, link.destination_url), ('Offer', 'https://example.org/offer'))

    def test_unsupported_methods(self):
        self.assertEqual(self.api.generic('TRACE', LIST).status_code, 405)


class StatsTests(ApiTestCase):
    def test_stats(self):
        link = make_link(self.user)
        add_clicks(link, 6, referrer='facebook.com', visitors=3)
        add_clicks(link, 2, days_ago=3, referrer='', device=Click.DEVICE_MOBILE)
        add_clicks(link, 4, device=Click.DEVICE_BOT)
        body = self.api.get(f'{LIST}{link.pk}/stats/', {'days': 7}).json()
        self.assertEqual((body['days'], body['clicks'], body['unique_visitors'], body['bot_clicks']), (7, 8, 5, 4))
        self.assertEqual(len(body['series']), 7)
        self.assertEqual(body['series'][-1]['clicks'], 6)
        self.assertEqual(body['top_referrers'][0], {'name': 'facebook.com', 'n': 6})
        self.assertEqual({d['name'] for d in body['devices']}, {'Desktop', 'Mobile'})

    def test_the_default_window_is_30_days(self):
        link = make_link(self.user)
        self.assertEqual(len(self.api.get(f'{LIST}{link.pk}/stats/').json()['series']), 30)

    def test_the_window_is_validated(self):
        link = make_link(self.user)
        for days in ('0', '-1', '366', 'abc', '1.5', ''):
            with self.subTest(days=days):
                self.assertEqual(self.api.get(f'{LIST}{link.pk}/stats/', {'days': days}).status_code, 400)
        for days in ('1', '365'):
            self.assertEqual(self.api.get(f'{LIST}{link.pk}/stats/', {'days': days}).status_code, 200)


class ThrottlingTests(ApiTestCase):
    @override_settings(API_RATE_USER='3/min')
    def test_a_key_that_sends_too_many_requests_gets_a_429(self):
        statuses = [self.api.get(LIST).status_code for _ in range(5)]
        self.assertEqual(statuses, [200, 200, 200, 429, 429])
        self.assertIn('Retry-After', self.api.get(LIST))

    @override_settings(API_RATE_USER='3/min')
    def test_the_limit_is_per_user(self):
        for _ in range(4):
            self.api.get(LIST)
        _, other_client = self.other_client()
        self.assertEqual(other_client.get(LIST).status_code, 200)
