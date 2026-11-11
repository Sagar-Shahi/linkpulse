"""REST API: /api/v1/links/

Authenticate with an API key (Authorization: Bearer lp_...) created on the API keys page, or with a
normal browser session. A user only ever sees their own links; another user's link answers 404.
"""
from django.conf import settings
from django.db import IntegrityError
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from . import analytics
from .models import UTM_FIELDS, Click, Link
from .validators import validate_code


class LinkSerializer(serializers.ModelSerializer):
    # Declared by hand so the code is compared without regard to case (codes are stored in lowercase).
    code = serializers.CharField(required=False, allow_blank=True, max_length=32, validators=[validate_code])
    short_url = serializers.SerializerMethodField()
    click_count = serializers.SerializerMethodField()

    class Meta:
        model = Link
        # Fields are listed one by one. Anything not listed (such as the owner) can never be set by a client.
        fields = [
            'id', 'code', 'short_url', 'destination_url', 'title', *UTM_FIELDS,
            'is_active', 'expires_at', 'click_count', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'short_url', 'click_count', 'created_at', 'updated_at']

    def get_short_url(self, obj):
        from django.urls import reverse

        return self.context['request'].build_absolute_uri(reverse('links:redirect', args=[obj.code]))

    def get_click_count(self, obj):
        return getattr(obj, 'total_clicks', 0)

    def validate_code(self, value):
        value = (value or '').strip().lower()
        if self.instance is not None:
            if value and value != self.instance.code:
                raise serializers.ValidationError('The short code of an existing link cannot be changed.')
            return self.instance.code
        if value and Link.objects.filter(code=value).exists():
            raise serializers.ValidationError('That short code is already taken.')
        return value

    def validate_expires_at(self, value):
        if value and self.instance is None and value <= timezone.now():
            raise serializers.ValidationError('Choose a time in the future.')
        return value


class LinkViewSet(viewsets.ModelViewSet):
    serializer_class = LinkSerializer
    http_method_names = ['get', 'post', 'put', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        return (
            Link.objects.filter(owner=self.request.user)
            .annotate(total_clicks=Count('clicks', filter=~Q(clicks__device=Click.DEVICE_BOT)))
            .order_by('-created_at', '-id')
        )

    def perform_create(self, serializer):
        if Link.objects.filter(owner=self.request.user).count() >= settings.MAX_LINKS_PER_USER:
            raise ValidationError({'detail': f'Link limit reached ({settings.MAX_LINKS_PER_USER}).'})
        try:
            serializer.save(owner=self.request.user)
        except IntegrityError:  # two requests picked the same code at the same moment
            raise ValidationError({'code': ['That short code is already taken.']}) from None

    @action(detail=True, methods=['get'])
    def stats(self, request, pk=None):
        link = self.get_object()
        try:
            days = int(request.query_params.get('days', 30))
        except (TypeError, ValueError):
            raise ValidationError({'days': ['Use a whole number from 1 to 365.']}) from None
        if not 1 <= days <= 365:
            raise ValidationError({'days': ['Use a whole number from 1 to 365.']})

        clicks = link.clicks.all()
        summary = analytics.summary(clicks, days)
        return Response({
            'days': days,
            'clicks': summary['total'],
            'unique_visitors': summary['unique_visitors'],
            'bot_clicks': summary['bots'],
            'series': [{'date': day.isoformat(), 'clicks': n} for day, n in analytics.daily_series(clicks, days)],
            'top_referrers': analytics.top_referrers(clicks, days),
            'devices': analytics.device_breakdown(clicks, days),
        })
