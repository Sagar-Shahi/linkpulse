"""Numbers and chart geometry for the dashboard and the API.

Every function takes a queryset of Click rows that has already been limited to one user or one link.
Bots and link previews are excluded by `human()`, so the numbers reflect real people.
"""
from datetime import timedelta

from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import Click

DIRECT_LABEL = 'Direct or unknown'


def human(clicks):
    return clicks.exclude(device=Click.DEVICE_BOT)


def since(days):
    """The first day of a window that ends today and is `days` days long."""
    return timezone.localdate() - timedelta(days=days - 1)


def in_period(clicks, days):
    return clicks.filter(clicked_at__date__gte=since(days))


def summary(clicks, days):
    """Totals for a window. `clicks` is every click, bots included."""
    windowed = in_period(clicks, days)
    people = human(windowed)
    return {
        'total': people.count(),
        'unique_visitors': people.values('visitor_hash').distinct().count(),
        'today': human(clicks).filter(clicked_at__date=timezone.localdate()).count(),
        'bots': windowed.filter(device=Click.DEVICE_BOT).count(),
    }


def daily_series(clicks, days):
    """One (date, count) pair for every day in the window, with zeros for quiet days."""
    start = since(days)
    rows = (
        human(clicks)
        .filter(clicked_at__date__gte=start)
        .annotate(day=TruncDate('clicked_at'))
        .values('day')
        .annotate(n=Count('id'))
    )
    counts = {row['day']: row['n'] for row in rows}
    return [(start + timedelta(days=i), counts.get(start + timedelta(days=i), 0)) for i in range(days)]


def top_referrers(clicks, days, limit=8):
    rows = (
        human(in_period(clicks, days))
        .values('referrer_host')
        .annotate(n=Count('id'))
        .order_by('-n', 'referrer_host')[:limit]
    )
    return [{'name': row['referrer_host'] or DIRECT_LABEL, 'n': row['n']} for row in rows]


def device_breakdown(clicks, days):
    labels = dict(Click.DEVICE_CHOICES)
    rows = (
        human(in_period(clicks, days))
        .values('device')
        .annotate(n=Count('id'))
        .order_by('-n', 'device')
    )
    return [{'name': labels.get(row['device'], row['device']), 'n': row['n']} for row in rows]


def top_campaigns(clicks, days, limit=5):
    rows = (
        human(in_period(clicks, days))
        .exclude(link__utm_campaign='')
        .values('link__utm_campaign')
        .annotate(n=Count('id'))
        .order_by('-n', 'link__utm_campaign')[:limit]
    )
    return [{'name': row['link__utm_campaign'], 'n': row['n']} for row in rows]


def nice_ceiling(value):
    """Round up to a tidy axis maximum: 4, 5, 10, 20, 50, 100, 200 ..."""
    if value <= 4:
        return 4
    magnitude = 1
    while magnitude * 10 < value:
        magnitude *= 10
    for step in (1, 2, 5, 10):
        if step * magnitude >= value:
            return step * magnitude
    return 10 * magnitude  # pragma: no cover


def _fmt(number):
    return f'{number:.1f}'.rstrip('0').rstrip('.')


def bar_chart(series, width=720, height=220):
    """Geometry for a bar chart drawn as SVG on the server, so no script or chart library is needed.

    All numbers are returned as ready-to-print strings, so the page never depends on locale
    formatting. `series` is a list of (date, count).
    """
    left, right, top, bottom = 38, 8, 10, 26
    plot_w = width - left - right
    plot_h = height - top - bottom
    days = len(series)
    peak = max((count for _, count in series), default=0)
    axis_max = nice_ceiling(peak)
    slot = plot_w / days if days else plot_w
    bar_w = max(slot * 0.68, 1.5)

    bars = []
    for index, (day, count) in enumerate(series):
        h = plot_h * count / axis_max
        bars.append({
            'x': _fmt(left + index * slot + (slot - bar_w) / 2),
            'y': _fmt(top + plot_h - h),
            'w': _fmt(bar_w),
            'h': _fmt(h),
            'count': count,
            'label': f'{day:%b} {day.day}',
        })

    ticks = [
        {'value': _fmt(axis_max * fraction), 'y': _fmt(top + plot_h - plot_h * fraction)}
        for fraction in (0, 0.5, 1)
    ]

    x_labels = []
    if days:
        for index in sorted({0, days // 2, days - 1}):
            day = series[index][0]
            x_labels.append({
                'text': f'{day:%b} {day.day}',
                'x': _fmt(left + index * slot + slot / 2),
                'y': _fmt(height - 8),
            })

    total = sum(count for _, count in series)
    return {
        'width': width,
        'height': height,
        'left': left,
        'right_edge': width - right,
        'bars': bars,
        'ticks': ticks,
        'x_labels': x_labels,
        'axis_max': axis_max,
        'total': total,
        'description': f'{total} click{"s" if total != 1 else ""} over the last {days} days, highest day {peak}.',
    }
