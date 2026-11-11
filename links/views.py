import csv

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import DisallowedRedirect
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_safe

from core import throttle

from . import analytics
from .forms import LinkForm
from .models import Click, Link
from .tracking import record_click

PERIODS = (7, 30, 90)
DEFAULT_PERIOD = 30
PAGE_SIZE = 15

HUMAN = ~Q(clicks__device=Click.DEVICE_BOT)


class SafeRedirect(HttpResponseRedirect):
    """A redirect that can only ever lead to a web page, never to javascript: or data: addresses."""

    allowed_schemes = ['http', 'https']


def _period(request):
    try:
        days = int(request.GET.get('days', DEFAULT_PERIOD))
    except (TypeError, ValueError):
        return DEFAULT_PERIOD
    return days if days in PERIODS else DEFAULT_PERIOD


def _own_links(user):
    """Every query for a link starts here, so one user can never reach another user's links."""
    return Link.objects.filter(owner=user)


def _with_click_counts(queryset):
    return queryset.annotate(total_clicks=Count('clicks', filter=HUMAN))


def _short_url(request, link):
    return request.build_absolute_uri(reverse('links:redirect', args=[link.code]))


# --- The public redirect -------------------------------------------------------------------------

@require_safe
def redirect_view(request, code):
    """Count the click and send the visitor on. This is the only public part of the app."""
    limited = throttle.exceeded(
        throttle.make_key('redirect', throttle.client_ip(request)), settings.REDIRECT_MAX_PER_MINUTE, 60
    )
    if limited:
        response = HttpResponse('Too many requests. Please slow down.', status=429, content_type='text/plain')
        response['Retry-After'] = '60'
        return response

    link = Link.objects.filter(code=code.lower()).first()
    if link is None:
        return render(request, 'links/not_found.html', status=404)
    if not link.is_live:
        return render(request, 'links/unavailable.html', {'link': link}, status=410)

    if request.method == 'GET':  # link previews often send HEAD, and those are not visits
        record_click(request, link)
    try:
        return SafeRedirect(link.target_url)
    except DisallowedRedirect:
        return render(request, 'links/unavailable.html', {'link': link}, status=410)


# --- The signed-in app -----------------------------------------------------------------------

@login_required
def dashboard(request):
    days = _period(request)
    clicks = Click.objects.filter(link__owner=request.user)
    stats = analytics.summary(clicks, days)
    since = analytics.since(days)

    top_links = (
        _own_links(request.user)
        .annotate(period_clicks=Count('clicks', filter=HUMAN & Q(clicks__clicked_at__date__gte=since)))
        .filter(period_clicks__gt=0)
        .order_by('-period_clicks', '-created_at')[:5]
    )
    context = {
        'days': days,
        'periods': PERIODS,
        'stats': stats,
        'chart': analytics.bar_chart(analytics.daily_series(clicks, days)),
        'referrers': analytics.top_referrers(clicks, days),
        'devices': analytics.device_breakdown(clicks, days),
        'campaigns': analytics.top_campaigns(clicks, days),
        'top_links': top_links,
        'link_count': _own_links(request.user).count(),
        'recent_links': _own_links(request.user)[:5],
    }
    return render(request, 'links/dashboard.html', context)


@login_required
def link_list(request):
    links = _with_click_counts(_own_links(request.user))
    query = request.GET.get('q', '').strip()[:100]
    status = request.GET.get('status', '')

    if query:
        links = links.filter(
            Q(title__icontains=query) | Q(code__icontains=query)
            | Q(destination_url__icontains=query) | Q(utm_campaign__icontains=query)
        )
    now = timezone.now()
    if status == 'live':
        links = links.filter(is_active=True).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    elif status == 'paused':
        links = links.filter(is_active=False)
    elif status == 'expired':
        links = links.filter(expires_at__lte=now)
    else:
        status = ''

    page = Paginator(links.order_by('-created_at', '-pk'), PAGE_SIZE).get_page(request.GET.get('page'))
    return render(request, 'links/link_list.html', {
        'page': page, 'q': query, 'status': status,
        'total_links': _own_links(request.user).count(),
    })


@login_required
def link_create(request):
    initial = {}
    if request.method == 'GET':
        initial = {key: request.GET.get(key, '')[:2048] for key in ('destination_url',) if request.GET.get(key)}
    form = LinkForm(request.POST or None, owner=request.user, initial=initial)
    if request.method == 'POST' and form.is_valid():
        link = form.save(commit=False)
        link.owner = request.user
        link.save()
        messages.success(request, 'Your short link is ready.')
        return redirect('links:detail', pk=link.pk)
    return render(request, 'links/link_form.html', {'form': form, 'editing': False})


@login_required
def link_detail(request, pk):
    link = get_object_or_404(_own_links(request.user), pk=pk)
    days = _period(request)
    clicks = link.clicks.all()
    context = {
        'link': link,
        'short_url': _short_url(request, link),
        'days': days,
        'periods': PERIODS,
        'stats': analytics.summary(clicks, days),
        'chart': analytics.bar_chart(analytics.daily_series(clicks, days)),
        'referrers': analytics.top_referrers(clicks, days),
        'devices': analytics.device_breakdown(clicks, days),
        'recent_clicks': analytics.human(clicks)[:10],
    }
    return render(request, 'links/link_detail.html', context)


@login_required
def link_edit(request, pk):
    link = get_object_or_404(_own_links(request.user), pk=pk)
    form = LinkForm(request.POST or None, instance=link, owner=request.user)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Changes saved.')
        return redirect('links:detail', pk=link.pk)
    return render(request, 'links/link_form.html', {'form': form, 'editing': True, 'link': link})


@login_required
def link_delete(request, pk):
    link = get_object_or_404(_own_links(request.user), pk=pk)
    if request.method == 'POST':
        link.delete()
        messages.success(request, 'The link and its click history were deleted.')
        return redirect('links:list')
    return render(request, 'links/link_confirm_delete.html', {'link': link})


@login_required
@require_POST
def link_toggle(request, pk):
    link = get_object_or_404(_own_links(request.user), pk=pk)
    link.is_active = not link.is_active
    link.save(update_fields=['is_active', 'updated_at'])
    messages.success(request, 'The link is active again.' if link.is_active else 'The link is paused. Visitors will see a "no longer available" page.')
    return redirect('links:detail', pk=link.pk)


def _csv_safe(value):
    """Stop spreadsheet programs running a cell as a formula ("CSV injection")."""
    text = '' if value is None else str(value)
    return "'" + text if text[:1] in ('=', '+', '-', '@', '\t', '\r') else text


@login_required
def export_links_csv(request):
    links = _with_click_counts(_own_links(request.user)).annotate(
        unique_visitors=Count('clicks__visitor_hash', filter=HUMAN, distinct=True)
    )
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="linkpulse-links.csv"'
    writer = csv.writer(response)
    writer.writerow([
        'created', 'name', 'short_url', 'destination', 'full_tracking_url', 'source', 'medium', 'campaign',
        'term', 'content', 'status', 'clicks', 'unique_visitors',
    ])
    for link in links:
        writer.writerow([_csv_safe(v) for v in (
            f'{link.created_at:%Y-%m-%d %H:%M}', link.title, _short_url(request, link), link.destination_url,
            link.target_url, link.utm_source, link.utm_medium, link.utm_campaign, link.utm_term, link.utm_content,
            link.status, link.total_clicks, link.unique_visitors,
        )])
    return response
