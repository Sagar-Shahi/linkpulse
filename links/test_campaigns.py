from xml.etree import ElementTree

from django.test import TestCase
from django.urls import reverse

from . import analytics
from .models import Click
from .qr import qr_svg
from .testutils import PASSWORD, add_clicks, make_link, make_user


def report_for(user, days=7):
    return {row['name']: row for row in analytics.campaign_report(Click.objects.filter(link__owner=user), days)}


class CampaignReportTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_compares_this_window_with_the_one_before(self):
        link = make_link(self.user, utm_campaign='diwali')
        add_clicks(link, 15, days_ago=1)
        add_clicks(link, 10, days_ago=9)
        row = report_for(self.user)['diwali']
        self.assertEqual((row['clicks'], row['before'], row['difference'], row['percent']), (15, 10, 5, 50))
        self.assertEqual(row['status'], 'up')

    def test_window_edges(self):
        link = make_link(self.user, utm_campaign='edges')
        add_clicks(link, 1, days_ago=6)    # last day inside this window of 7
        add_clicks(link, 1, days_ago=7)    # first day of the previous window
        add_clicks(link, 1, days_ago=13)   # last day of the previous window
        add_clicks(link, 1, days_ago=14)   # outside both
        row = report_for(self.user)['edges']
        self.assertEqual((row['clicks'], row['before']), (1, 2))

    def test_statuses(self):
        for name, now, before in (('up', 20, 10), ('down', 5, 10), ('steady', 10, 10), ('new', 4, 0), ('stopped', 0, 6)):
            link = make_link(self.user, utm_campaign=name)
            if now:
                add_clicks(link, now, days_ago=1)
            if before:
                add_clicks(link, before, days_ago=9)
        report = report_for(self.user)
        for name in ('up', 'down', 'steady', 'new', 'stopped'):
            with self.subTest(name=name):
                self.assertEqual(report[name]['status'], name)
        self.assertIsNone(report['new']['percent'])
        self.assertEqual(report['stopped']['clicks'], 0)

    def test_small_changes_are_steady(self):
        link = make_link(self.user, utm_campaign='calm')
        add_clicks(link, 21, days_ago=1)
        add_clicks(link, 20, days_ago=9)
        self.assertEqual(report_for(self.user)['calm']['status'], 'steady')

    def test_bots_and_unnamed_campaigns_are_left_out(self):
        link = make_link(self.user, utm_campaign='real')
        add_clicks(link, 3, days_ago=1)
        add_clicks(link, 50, device=Click.DEVICE_BOT, days_ago=1)
        add_clicks(make_link(self.user), 9, days_ago=1)
        report = report_for(self.user)
        self.assertEqual(list(report), ['real'])
        self.assertEqual(report['real']['clicks'], 3)

    def test_ranked_by_clicks_and_other_users_are_excluded(self):
        add_clicks(make_link(self.user, utm_campaign='small'), 2, days_ago=1)
        add_clicks(make_link(self.user, utm_campaign='big'), 9, days_ago=1)
        add_clicks(make_link(make_user('other'), utm_campaign='stranger'), 99, days_ago=1)
        ranked = analytics.campaign_report(Click.objects.filter(link__owner=self.user), 7)
        self.assertEqual([r['name'] for r in ranked], ['big', 'small'])

    def test_unique_visitors_and_link_counts(self):
        first = make_link(self.user, utm_campaign='mix')
        second = make_link(self.user, utm_campaign='mix')
        add_clicks(first, 6, days_ago=1, visitors=2)
        add_clicks(second, 3, days_ago=1, visitors=3)
        row = report_for(self.user)['mix']
        self.assertEqual(row['links'], 2)
        self.assertEqual(row['clicks'], 9)
        self.assertEqual(row['people'], 3)  # the same visitor on two links of one campaign counts once


class PercentTextTests(TestCase):
    def test_formatting(self):
        self.assertEqual(analytics.percent_text(None), '')
        self.assertEqual(analytics.percent_text(50), '+50%')
        self.assertEqual(analytics.percent_text(-30), '-30%')
        self.assertEqual(analytics.percent_text(0), '0%')
        self.assertEqual(analytics.percent_text(999), '+999%')
        self.assertEqual(analytics.percent_text(9950), '+999%+')


class TrendAndInsightTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.clicks = Click.objects.filter(link__owner=self.user)

    def test_overall_trend(self):
        link = make_link(self.user)
        add_clicks(link, 6, days_ago=1)
        add_clicks(link, 4, days_ago=8)
        trend = analytics.overall_trend(self.clicks, 7)
        self.assertEqual((trend['now'], trend['before'], trend['percent'], trend['status']), (6, 4, 50, 'up'))

    def test_no_history_means_no_percentage(self):
        add_clicks(make_link(self.user), 3, days_ago=1)
        trend = analytics.overall_trend(self.clicks, 7)
        self.assertIsNone(trend['percent'])
        lines = analytics.insights([], trend, 7)
        self.assertIn('no earlier period', lines[0])

    def test_insights_name_the_best_and_the_worst(self):
        add_clicks(make_link(self.user, utm_campaign='winner'), 30, days_ago=1)
        add_clicks(make_link(self.user, utm_campaign='winner'), 10, days_ago=9)
        add_clicks(make_link(self.user, utm_campaign='fading'), 4, days_ago=1)
        add_clicks(make_link(self.user, utm_campaign='fading'), 20, days_ago=9)
        report = analytics.campaign_report(self.clicks, 7)
        lines = ' '.join(analytics.insights(report, analytics.overall_trend(self.clicks, 7), 7))
        self.assertIn('Best campaign: winner', lines)
        self.assertIn('Fastest growing: winner', lines)
        self.assertIn('Needs attention: fading', lines)

    def test_losing_links_need_a_baseline_and_a_real_drop(self):
        fading = make_link(self.user, title='Fading')
        add_clicks(fading, 3, days_ago=1)
        add_clicks(fading, 10, days_ago=9)
        tiny = make_link(self.user, title='Tiny')          # drop of 60% but only 4 earlier clicks
        add_clicks(tiny, 4, days_ago=9)
        fine = make_link(self.user, title='Fine')
        add_clicks(fine, 9, days_ago=1)
        add_clicks(fine, 10, days_ago=9)
        losing = analytics.losing_links(self.user.links.all(), 7)
        self.assertEqual([link.title for link in losing], ['Fading'])
        self.assertEqual((losing[0].percent, losing[0].lost), (-70, 7))

    def test_losing_links_never_include_other_users(self):
        other = make_link(make_user('other'))
        add_clicks(other, 2, days_ago=1)
        add_clicks(other, 20, days_ago=9)
        self.assertEqual(analytics.losing_links(self.user.links.all(), 7), [])


class CampaignPageTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.login(username='sam', password=PASSWORD)

    def test_empty_states(self):
        response = self.client.get(reverse('links:campaigns'))
        self.assertContains(response, 'Create a link with a campaign name')
        make_link(self.user)
        self.assertContains(self.client.get(reverse('links:campaigns')), 'No campaign clicks yet')

    def test_shows_own_campaigns_only(self):
        add_clicks(make_link(self.user, utm_campaign='mine'), 5, days_ago=1)
        add_clicks(make_link(make_user('other'), utm_campaign='theirs'), 5, days_ago=1)
        response = self.client.get(reverse('links:campaigns'))
        self.assertContains(response, 'mine')
        self.assertNotContains(response, 'theirs')

    def test_periods_and_bad_input(self):
        add_clicks(make_link(self.user, utm_campaign='mine'), 5, days_ago=20)
        self.assertNotContains(self.client.get(reverse('links:campaigns'), {'days': '7'}), '>mine<')
        self.assertContains(self.client.get(reverse('links:campaigns'), {'days': '30'}), '>mine<')
        self.assertEqual(self.client.get(reverse('links:campaigns'), {'days': 'x'}).status_code, 200)

    def test_campaign_names_are_escaped_and_link_to_the_filtered_list(self):
        link = make_link(self.user, utm_campaign='fall-sale')
        add_clicks(link, 3, days_ago=1)
        response = self.client.get(reverse('links:campaigns'))
        self.assertContains(response, reverse('links:list') + '?q=fall-sale')

    def test_navigation_has_campaigns(self):
        self.assertContains(self.client.get(reverse('links:dashboard')), reverse('links:campaigns'))


class QrCodeTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.link = make_link(self.user)
        self.client.login(username='sam', password=PASSWORD)

    def test_returns_a_valid_svg(self):
        response = self.client.get(reverse('links:qr', args=[self.link.pk]), HTTP_HOST='testserver')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/svg+xml')
        self.assertIn('no-store', response['Cache-Control'])
        root = ElementTree.fromstring(response.content)
        self.assertTrue(root.tag.endswith('svg'))
        self.assertNotIn('Content-Disposition', response)

    def test_download_flag_sets_a_filename(self):
        response = self.client.get(reverse('links:qr', args=[self.link.pk]), {'download': '1'})
        self.assertIn(f'linkpulse-{self.link.code}-qr.svg', response['Content-Disposition'])

    def test_encodes_the_short_link(self):
        short = 'http://testserver' + reverse('links:redirect', args=[self.link.code])
        self.assertEqual(qr_svg(short), qr_svg(short))
        self.assertNotEqual(qr_svg(short), qr_svg(short + 'x'))

    def test_other_users_get_404_and_visitors_get_the_login_page(self):
        stranger = make_link(make_user('other'))
        self.assertEqual(self.client.get(reverse('links:qr', args=[stranger.pk])).status_code, 404)
        self.client.logout()
        response = self.client.get(reverse('links:qr', args=[self.link.pk]))
        self.assertEqual(response.status_code, 302)

    def test_detail_page_shows_the_qr_image_and_post_is_refused(self):
        self.assertContains(self.client.get(reverse('links:detail', args=[self.link.pk])), reverse('links:qr', args=[self.link.pk]))
        self.assertEqual(self.client.post(reverse('links:qr', args=[self.link.pk])).status_code, 405)
