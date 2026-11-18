"""Made-up numbers for the public /demo/ page, so visitors can see a report without signing up.

Nothing here touches the database, so no real account, link or click is ever shown.
"""
from . import analytics

SAMPLE = (
    # name, clicks now, clicks before, unique visitors, links
    ('diwali-sale', 418, 260, 361, 3),
    ('instagram-reels', 236, 118, 204, 2),
    ('weekly-newsletter', 150, 149, 131, 1),
    ('google-search-ads', 96, 142, 88, 2),
    ('old-flyer-qr', 12, 58, 11, 1),
)

LOSING = (
    {'title': 'Flyer QR, Thamel', 'code': 'flyer-tm', 'before': 41, 'now': 7, 'percent': -83},
    {'title': 'Search ads, brand', 'code': 'ads-brand', 'before': 90, 'now': 54, 'percent': -40},
)


def sample_report():
    rows = []
    for name, now, before, people, links in SAMPLE:
        percent = analytics.change_percent(now, before)
        rows.append({
            'name': name, 'clicks': now, 'before': before, 'difference': now - before, 'percent': percent,
            'percent_text': analytics.percent_text(percent), 'people': people, 'links': links,
            'status': analytics._status(now, before, percent),
        })
    now = sum(r['clicks'] for r in rows)
    before = sum(r['before'] for r in rows)
    percent = analytics.change_percent(now, before)
    trend = {
        'now': now, 'before': before, 'percent': percent, 'percent_text': analytics.percent_text(percent),
        'status': analytics._status(now, before, percent),
    }
    return rows, trend, analytics.insights(rows, trend, 30), [dict(row) for row in LOSING]
