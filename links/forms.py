from django import forms
from django.conf import settings
from django.utils import timezone

from .models import UTM_FIELDS, Link
from .validators import normalize_utm, validate_utm


class LinkForm(forms.ModelForm):
    expires_at = forms.DateTimeField(
        required=False,
        label='Stop working after',
        input_formats=['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M'],
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
        help_text='Optional. After this time the link shows a "no longer available" page.',
    )

    class Meta:
        model = Link
        fields = ['destination_url', 'title', 'code', *UTM_FIELDS, 'expires_at', 'is_active']
        labels = {
            'destination_url': 'Destination address',
            'title': 'Name (only you see this)',
            'code': 'Custom short code',
            'utm_source': 'Source',
            'utm_medium': 'Medium',
            'utm_campaign': 'Campaign',
            'utm_term': 'Keyword',
            'utm_content': 'Content',
            'is_active': 'Link is active',
        }
        help_texts = {
            'destination_url': 'The page visitors should land on, for example https://example.com/offer',
            'code': 'Optional, for example diwali-sale. Leave empty to get a random code.',
            'utm_source': 'Where the link is posted: facebook, newsletter, google.',
            'utm_medium': 'The kind of channel: social, email, cpc.',
            'utm_campaign': 'Your campaign name, for example tihar-offer-2026.',
            'utm_term': 'Paid search keyword. Optional.',
            'utm_content': 'Tells two ads or buttons apart. Optional.',
        }
        widgets = {'destination_url': forms.URLInput(attrs={'placeholder': 'https://', 'autocomplete': 'off'})}

    def __init__(self, *args, owner=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner
        for name in UTM_FIELDS:
            # Tags are tidied up in clean() (lowercase, hyphens) instead of being rejected.
            self.fields[name].validators = [v for v in self.fields[name].validators if v is not validate_utm]
        self.fields['code'].required = False

    def clean_destination_url(self):
        return (self.cleaned_data.get('destination_url') or '').strip()

    def clean_code(self):
        if self.instance.pk:
            return self.instance.code  # short links already shared must keep working, so the code never changes
        code = (self.cleaned_data.get('code') or '').strip().lower()
        if code and Link.objects.filter(code=code).exists():
            raise forms.ValidationError('That short code is already taken. Try another one.')
        return code

    def clean_expires_at(self):
        value = self.cleaned_data.get('expires_at')
        if value and not self.instance.pk and value <= timezone.now():
            raise forms.ValidationError('Choose a time in the future.')
        return value

    def clean(self):
        cleaned = super().clean()
        for name in UTM_FIELDS:
            if name in cleaned:
                cleaned[name] = normalize_utm(cleaned.get(name))
        if self.owner is not None and not self.instance.pk:
            if self.owner.links.count() >= settings.MAX_LINKS_PER_USER:
                raise forms.ValidationError(
                    f'You have reached the limit of {settings.MAX_LINKS_PER_USER} links. Delete some to add more.'
                )
        return cleaned
