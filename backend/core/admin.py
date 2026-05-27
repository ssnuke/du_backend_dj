from django.contrib import admin
from core.models import LearnVideo


@admin.register(LearnVideo)
class LearnVideoAdmin(admin.ModelAdmin):
    list_display = ('title', 'order', 'duration_seconds', 'is_published', 'created_at')
    list_editable = ('order', 'is_published')
    list_filter = ('is_published',)
    search_fields = ('title', 'description', 'bunny_video_id')
    ordering = ('order', 'created_at')
    fieldsets = (
        (None, {
            'fields': ('title', 'description', 'order', 'is_published'),
        }),
        ('Bunny.net Stream', {
            'fields': ('bunny_video_id', 'bunny_library_id', 'thumbnail_url', 'duration_seconds'),
            'description': (
                'bunny_video_id: the GUID from Bunny Stream dashboard (e.g. abc123de-…). '
                'Leave thumbnail_url blank to use the auto-generated Bunny thumbnail.'
            ),
        }),
    )
