from django.contrib import admin

from .models import Click, Link


@admin.register(Link)
class LinkAdmin(admin.ModelAdmin):
    list_display = ('code', 'title', 'owner', 'is_active', 'expires_at', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('code', 'title', 'destination_url', 'owner__username')
    raw_id_fields = ('owner',)
    list_select_related = ('owner',)


@admin.register(Click)
class ClickAdmin(admin.ModelAdmin):
    """Click history is a record, so it can be looked at but not edited or added by hand."""

    list_display = ('link', 'clicked_at', 'referrer_host', 'device')
    list_filter = ('device',)
    list_select_related = ('link',)
    readonly_fields = ('link', 'clicked_at', 'referrer_host', 'device', 'visitor_hash')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
