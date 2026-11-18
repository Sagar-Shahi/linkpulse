import csv
import io

from django.test import TestCase
from django.urls import reverse

from .models import Click
from .testutils import PASSWORD, add_clicks, make_link, make_user


class CampaignExportTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.login(username='sam', password=PASSWORD)

    def rows(self, **params):
        response = self.client.get(reverse('links:campaigns-export'), params)
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        return response, list(csv.reader(io.StringIO(response.content.decode())))

    def test_contains_my_campaigns_with_changes(self):
        link = make_link(self.user, utm_campaign='diwali')
        add_clicks(link, 15, days_ago=1)
        add_clicks(link, 10, days_ago=9)
        response, rows = self.rows(days='7')
        self.assertIn('linkpulse-campaigns-7d.csv', response['Content-Disposition'])
        self.assertEqual(rows[0][0], 'campaign')
        self.assertEqual(rows[1], ['diwali', '15', '10', '5', '+50%', '15', '1', 'Growing'])

    def test_other_users_and_bots_are_not_included(self):
        add_clicks(make_link(make_user('other'), utm_campaign='theirs'), 9, days_ago=1)
        mine = make_link(self.user, utm_campaign='mine')
        add_clicks(mine, 2, days_ago=1)
        add_clicks(mine, 40, device=Click.DEVICE_BOT, days_ago=1)
        _, rows = self.rows(days='7')
        self.assertEqual([r[0] for r in rows[1:]], ['mine'])
        self.assertEqual(rows[1][1], '2')

    def test_formula_injection_is_neutralised(self):
        add_clicks(make_link(self.user, utm_campaign='=SUM(A1)'), 3, days_ago=1)
        add_clicks(make_link(self.user, utm_campaign='-2+3'), 3, days_ago=1)
        _, rows = self.rows(days='7')
        self.assertEqual({r[0] for r in rows[1:]}, {"'=SUM(A1)", "'-2+3"})

    def test_bad_period_falls_back_and_visitors_must_log_in(self):
        response, rows = self.rows(days='abc')
        self.assertIn('30d', response['Content-Disposition'])
        self.client.logout()
        self.assertEqual(self.client.get(reverse('links:campaigns-export')).status_code, 302)
        self.assertEqual(self.client.post(reverse('links:campaigns-export')).status_code, 302)

    def test_campaign_page_offers_export_and_print(self):
        add_clicks(make_link(self.user, utm_campaign='mine'), 3, days_ago=1)
        page = self.client.get(reverse('links:campaigns'))
        self.assertContains(page, reverse('links:campaigns-export'))
        self.assertContains(page, 'data-print')


class DashboardCardTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.login(username='sam', password=PASSWORD)

    def test_shows_top_campaigns_with_trend(self):
        for name, now in (('a-first', 9), ('b-second', 7), ('c-third', 5), ('d-fourth', 3)):
            add_clicks(make_link(self.user, utm_campaign=name), now, days_ago=1)
        page = self.client.get(reverse('links:dashboard'))
        for name in ('a-first', 'b-second', 'c-third'):
            self.assertContains(page, name)
        self.assertNotContains(page, '/app/links/?q=d-fourth')
        self.assertContains(page, reverse('links:campaigns'))

    def test_hidden_without_campaign_clicks_and_never_shows_others(self):
        add_clicks(make_link(make_user('other'), utm_campaign='theirs'), 9, days_ago=1)
        add_clicks(make_link(self.user), 4, days_ago=1)
        page = self.client.get(reverse('links:dashboard'))
        self.assertNotContains(page, 'Full comparison')
        self.assertNotContains(page, 'theirs')


class DemoPageTests(TestCase):
    def test_public_and_uses_only_made_up_data(self):
        add_clicks(make_link(make_user('other'), utm_campaign='secret-campaign'), 9, days_ago=1)
        response = self.client.get(reverse('links:demo'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'made up')
        self.assertContains(response, 'diwali-sale')
        self.assertContains(response, 'Create a free account')
        self.assertNotContains(response, 'secret-campaign')
        self.assertNotContains(response, 'Export CSV')

    def test_demo_has_no_links_into_the_app_and_only_allows_reading(self):
        response = self.client.get(reverse('links:demo'))
        self.assertNotContains(response, '/app/links/')
        self.assertEqual(self.client.post(reverse('links:demo')).status_code, 405)

    def test_home_links_to_the_demo(self):
        self.assertContains(self.client.get(reverse('home')), reverse('links:demo'))

    def test_sample_numbers_are_consistent(self):
        from . import demo
        rows, trend, insights, losing = demo.sample_report()
        self.assertEqual(trend['now'], sum(r['clicks'] for r in rows))
        self.assertEqual(trend['before'], sum(r['before'] for r in rows))
        self.assertTrue(insights)
        self.assertTrue(all(row['status'] in ('up', 'down', 'steady', 'new', 'stopped') for row in rows))
        self.assertEqual(len(losing), 2)
