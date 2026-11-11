from datetime import timedelta

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from . import analytics
from .models import Click
from .testutils import add_clicks, make_link, make_user


class NiceCeilingTests(SimpleTestCase):
    def test_axis_maximums_are_tidy_numbers(self):
        cases = {0: 4, 1: 4, 4: 4, 5: 5, 6: 10, 10: 10, 11: 20, 20: 20, 21: 50, 50: 50, 51: 100, 101: 200, 999: 1000, 1001: 2000}
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(analytics.nice_ceiling(value), expected)


class SeriesTests(TestCase):
    def setUp(self):
        self.link = make_link(make_user())

    def test_the_series_has_one_entry_per_day_and_fills_quiet_days_with_zero(self):
        add_clicks(self.link, 3, days_ago=0)
        add_clicks(self.link, 2, days_ago=2)
        series = analytics.daily_series(self.link.clicks.all(), 7)
        self.assertEqual(len(series), 7)
        self.assertEqual([n for _, n in series], [0, 0, 0, 0, 2, 0, 3])
        self.assertEqual(series[-1][0], timezone.localdate())
        self.assertEqual([d for d, _ in series], sorted(d for d, _ in series))

    def test_clicks_before_the_window_are_left_out(self):
        add_clicks(self.link, 4, days_ago=10)
        self.assertEqual(sum(n for _, n in analytics.daily_series(self.link.clicks.all(), 7)), 0)

    def test_bots_are_never_counted(self):
        add_clicks(self.link, 5, device=Click.DEVICE_BOT)
        add_clicks(self.link, 2)
        self.assertEqual(sum(n for _, n in analytics.daily_series(self.link.clicks.all(), 7)), 2)


class SummaryTests(TestCase):
    def test_totals(self):
        link = make_link(make_user())
        add_clicks(link, 6, visitors=3)
        add_clicks(link, 4, days_ago=3, visitors=4)
        add_clicks(link, 5, device=Click.DEVICE_BOT)
        add_clicks(link, 9, days_ago=40)
        result = analytics.summary(link.clicks.all(), 7)
        self.assertEqual(result, {'total': 10, 'unique_visitors': 7, 'today': 6, 'bots': 5})
        self.assertEqual(analytics.summary(link.clicks.all(), 90)['total'], 19)

    def test_an_empty_history(self):
        link = make_link(make_user())
        self.assertEqual(analytics.summary(link.clicks.all(), 30), {'total': 0, 'unique_visitors': 0, 'today': 0, 'bots': 0})


class BreakdownTests(TestCase):
    def setUp(self):
        self.link = make_link(make_user(), utm_campaign='tihar')
        add_clicks(self.link, 5, referrer='facebook.com')
        add_clicks(self.link, 3, referrer='')
        add_clicks(self.link, 2, referrer='t.co', device=Click.DEVICE_MOBILE)
        add_clicks(self.link, 9, referrer='bot.example', device=Click.DEVICE_BOT)
        add_clicks(make_link(self.link.owner), 4, referrer='other.com')  # another link

    def test_referrers_are_sorted_and_direct_visits_are_named(self):
        rows = analytics.top_referrers(self.link.clicks.all(), 30)
        self.assertEqual(rows, [{'name': 'facebook.com', 'n': 5}, {'name': analytics.DIRECT_LABEL, 'n': 3}, {'name': 't.co', 'n': 2}])

    def test_the_limit_is_respected(self):
        self.assertEqual(len(analytics.top_referrers(self.link.clicks.all(), 30, limit=1)), 1)

    def test_devices_use_readable_names_and_skip_bots(self):
        rows = analytics.device_breakdown(self.link.clicks.all(), 30)
        self.assertEqual(rows, [{'name': 'Desktop', 'n': 8}, {'name': 'Mobile', 'n': 2}])

    def test_campaigns_ignore_links_without_one(self):
        everything = Click.objects.filter(link__owner=self.link.owner)
        self.assertEqual(analytics.top_campaigns(everything, 30), [{'name': 'tihar', 'n': 10}])


class BarChartTests(SimpleTestCase):
    def _series(self, counts):
        today = timezone.localdate()
        return [(today - timedelta(days=len(counts) - 1 - i), n) for i, n in enumerate(counts)]

    def test_geometry(self):
        chart = analytics.bar_chart(self._series([0, 5, 10]), width=300, height=100)
        self.assertEqual(len(chart['bars']), 3)
        self.assertEqual(chart['axis_max'], 10)
        heights = [float(b['h']) for b in chart['bars']]
        self.assertEqual(heights[0], 0)
        self.assertAlmostEqual(heights[1] * 2, heights[2], delta=0.2)
        self.assertEqual([t['value'] for t in chart['ticks']], ['0', '5', '10'])
        for bar in chart['bars']:
            self.assertGreaterEqual(float(bar['x']), chart['left'])
            self.assertLessEqual(float(bar['x']) + float(bar['w']), chart['right_edge'])
            self.assertLessEqual(float(bar['y']) + float(bar['h']), 100)

    def test_every_number_is_a_ready_to_print_string(self):
        chart = analytics.bar_chart(self._series([1, 2, 3]))
        for bar in chart['bars']:
            for key in ('x', 'y', 'w', 'h'):
                self.assertIsInstance(bar[key], str)
                self.assertNotIn(',', bar[key])

    def test_an_empty_history_still_draws_an_axis(self):
        chart = analytics.bar_chart(self._series([0] * 30))
        self.assertEqual(chart['axis_max'], 4)
        self.assertEqual(chart['total'], 0)
        self.assertEqual(len(chart['x_labels']), 3)
        self.assertIn('0 clicks', chart['description'])

    def test_the_description_is_readable(self):
        self.assertIn('1 click over', analytics.bar_chart(self._series([0, 1]))['description'])
        self.assertIn('6 clicks over', analytics.bar_chart(self._series([1, 5]))['description'])
