"""Numbers and chart geometry for the dashboard and the API.

Every function takes a queryset of Click rows that has already been limited to one user or one link.
Bots and link previews are excluded by `human()`, so the numbers reflect real people.
"""
from datetime import timedelta

from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import Click, Link

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


# --- Campaign intelligence ----------------------------------------------------------------------
# Compare the last `days` days with the `days` days before them, so every number has something to be
# judged against. Only clicks from real people count (see `human`).

STEADY_BAND = 10        # a change smaller than this many percent is "steady"
MIN_BASELINE = 5        # a link or campaign needs this many earlier clicks before a drop is called a drop
LOSING_DROP = 30        # a link that lost at least this many percent is flagged


def windows(days):
    """(this window start, previous window start, previous window end), all inclusive dates."""
    this_start = since(days)
    return this_start, this_start - timedelta(days=days), this_start - timedelta(days=1)


def change_percent(now, before):
    """Percent change, or None when there is nothing to compare with."""
    if not before:
        return None
    return round((now - before) * 100 / before)


STATUS_LABELS = {'up': 'Growing', 'down': 'Falling', 'new': 'New', 'stopped': 'Stopped', 'steady': 'Steady'}


def percent_text(percent):
    """'+12%', '-30%', or '+999%+' for a huge jump, so a small baseline does not print silly numbers."""
    if percent is None:
        return ''
    if percent > 999:
        return '+999%+'
    return f"{'+' if percent > 0 else ''}{percent}%"


def _status(now, before, percent):
    if now and not before:
        return 'new'
    if not now and before:
        return 'stopped'
    if percent is None:
        return 'steady'
    if percent >= STEADY_BAND:
        return 'up'
    if percent <= -STEADY_BAND:
        return 'down'
    return 'steady'


def overall_trend(clicks, days):
    """Total clicks this window against the previous one."""
    this_start, prev_start, prev_end = windows(days)
    now = human(clicks).filter(clicked_at__date__gte=this_start).count()
    before = human(clicks).filter(clicked_at__date__gte=prev_start, clicked_at__date__lte=prev_end).count()
    percent = change_percent(now, before)
    return {'now': now, 'before': before, 'percent': percent, 'percent_text': percent_text(percent), 'status': _status(now, before, percent)}


def campaign_report(clicks, days):
    """One row per campaign name, ranked by clicks in this window."""
    this_start, prev_start, prev_end = windows(days)
    named = human(clicks).exclude(link__utm_campaign='')
    now_rows = (
        named.filter(clicked_at__date__gte=this_start)
        .values('link__utm_campaign')
        .annotate(n=Count('id'), people=Count('visitor_hash', distinct=True), links=Count('link', distinct=True))
    )
    before_rows = (
        named.filter(clicked_at__date__gte=prev_start, clicked_at__date__lte=prev_end)
        .values('link__utm_campaign')
        .annotate(n=Count('id'))
    )
    current = {row['link__utm_campaign']: row for row in now_rows}
    previous = {row['link__utm_campaign']: row['n'] for row in before_rows}
    report = []
    for name in set(current) | set(previous):
        row = current.get(name)
        now = row['n'] if row else 0
        before = previous.get(name, 0)
        percent = change_percent(now, before)
        report.append({
            'name': name, 'clicks': now, 'before': before, 'difference': now - before, 'percent': percent,
            'percent_text': percent_text(percent),
            'people': row['people'] if row else 0, 'links': row['links'] if row else 0,
            'status': _status(now, before, percent),
        })
    report.sort(key=lambda r: (-r['clicks'], -r['before'], r['name']))
    return report


def losing_links(links, days, limit=5):
    """Links that lost a real share of their clicks. `links` must already be one user's links."""
    this_start, prev_start, prev_end = windows(days)
    rows = (
        links.annotate(
            now=Count('clicks', filter=~Q(clicks__device=Click.DEVICE_BOT) & Q(clicks__clicked_at__date__gte=this_start)),
            before=Count('clicks', filter=~Q(clicks__device=Click.DEVICE_BOT)
                         & Q(clicks__clicked_at__date__gte=prev_start) & Q(clicks__clicked_at__date__lte=prev_end)),
        )
        .filter(before__gte=MIN_BASELINE)
    )
    losing = []
    for link in rows:
        percent = change_percent(link.now, link.before)
        if percent is not None and percent <= -LOSING_DROP:
            link.percent = percent
            link.lost = link.before - link.now
            losing.append(link)
    losing.sort(key=lambda link: (-link.lost, link.code))
    return losing[:limit]


def insights(report, trend, days):
    """A few plain sentences that answer 'what should I look at?'. Returned as text, never markup."""
    lines = []
    if trend['before'] or trend['now']:
        if trend['percent'] is None:
            lines.append(f"{trend['now']} clicks in the last {days} days. There is no earlier period to compare with yet.")
        elif trend['percent'] >= 0:
            lines.append(f"Clicks are up {trend['percent_text'].lstrip('+')} on the previous {days} days ({trend['before']} to {trend['now']}).")
        else:
            lines.append(f"Clicks are down {abs(trend['percent'])}% on the previous {days} days ({trend['before']} to {trend['now']}).")
    active = [r for r in report if r['clicks']]
    if active:
        lines.append(f"Best campaign: {active[0]['name']} with {active[0]['clicks']} click{'s' if active[0]['clicks'] != 1 else ''}.")
    growing = [r for r in report if r['status'] == 'up' and r['before'] >= MIN_BASELINE]
    if growing:
        top = max(growing, key=lambda r: (r['difference'], r['name']))
        lines.append(f"Fastest growing: {top['name']}, up {top['percent_text'].lstrip('+')} (+{top['difference']} clicks).")
    falling = [r for r in report if r['status'] in ('down', 'stopped') and r['before'] >= MIN_BASELINE]
    if falling:
        worst = min(falling, key=lambda r: (r['difference'], r['name']))
        lines.append(f"Needs attention: {worst['name']} lost {abs(worst['difference'])} clicks ({worst['before']} to {worst['clicks']}).")
    return lines


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
