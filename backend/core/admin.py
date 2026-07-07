from django.contrib import admin
from core.models import LearnVideo, DreamVideo, StickerPack, Sticker


def refresh_thumbnail_cache(modeladmin, request, queryset):
    """
    Bunny's CDN and this app's own service worker both cache the thumbnail
    purely by URL, with no expiry — re-uploading/replacing a thumbnail
    directly on Bunny's dashboard changes the image bytes but never touches
    this row, so the URL (and every cache of it) never changes and the app
    keeps showing the old image indefinitely. The thumbnail URL served to
    the app includes a `?v=<updated_at>` cache-buster (see
    core/views/learn.py _bunny_thumbnail_url), so re-saving here — which
    bumps `updated_at` — is what actually busts it everywhere.
    """
    count = 0
    for obj in queryset:
        obj.save(update_fields=['updated_at'])
        count += 1
    modeladmin.message_user(request, f"Refreshed thumbnail cache for {count} video(s).")
refresh_thumbnail_cache.short_description = "Refresh thumbnail cache (after updating on Bunny.net)"


@admin.register(LearnVideo)
class LearnVideoAdmin(admin.ModelAdmin):
    list_display = ('title', 'order', 'duration_seconds', 'is_published', 'created_at')
    list_editable = ('order', 'duration_seconds', 'is_published')
    list_filter = ('is_published',)
    search_fields = ('title', 'description', 'bunny_video_id')
    ordering = ('order', 'created_at')
    actions = [refresh_thumbnail_cache]
    fieldsets = (
        (None, {
            'fields': ('title', 'description', 'order', 'is_published'),
        }),
        ('Bunny.net Stream', {
            'fields': ('bunny_video_id', 'bunny_library_id', 'thumbnail_url', 'duration_seconds'),
            'description': (
                'bunny_video_id: the GUID from Bunny Stream dashboard (e.g. abc123de-…). '
                'Leave thumbnail_url blank to use the auto-generated Bunny thumbnail. '
                'After replacing a thumbnail on Bunny\'s dashboard, select this video and run '
                '"Refresh thumbnail cache" below — otherwise the app keeps showing the old image.'
            ),
        }),
    )


class StickerInline(admin.TabularInline):
    model = Sticker
    extra = 1
    fields = ('admin_upload', 'image_url', 'emoji', 'is_animated', 'order')
    readonly_fields = ()


@admin.register(StickerPack)
class StickerPackAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_public', 'sticker_count', 'owner', 'created_at')
    list_editable = ('is_public',)
    list_filter = ('is_public',)
    search_fields = ('name',)
    ordering = ('name',)
    inlines = [StickerInline]
    fields = ('name', 'is_public', 'owner', 'cover_sticker')

    def sticker_count(self, obj):
        return obj.stickers.count()
    sticker_count.short_description = 'Stickers'


@admin.register(DreamVideo)
class DreamVideoAdmin(admin.ModelAdmin):
    list_display = ('title', 'order', 'duration_seconds', 'is_published', 'created_at')
    list_editable = ('order', 'duration_seconds', 'is_published')
    list_filter = ('is_published',)
    search_fields = ('title', 'description', 'bunny_video_id')
    ordering = ('order', 'created_at')
    actions = [refresh_thumbnail_cache]
    fieldsets = (
        (None, {
            'fields': ('title', 'description', 'order', 'is_published'),
        }),
        ('Bunny.net Stream', {
            'fields': ('bunny_video_id', 'bunny_library_id', 'thumbnail_url', 'duration_seconds'),
            'description': (
                'bunny_video_id: the GUID from Bunny Stream dashboard (e.g. abc123de-…). '
                'Leave thumbnail_url blank to use the auto-generated Bunny thumbnail. '
                'After replacing a thumbnail on Bunny\'s dashboard, select this video and run '
                '"Refresh thumbnail cache" below — otherwise the app keeps showing the old image.'
            ),
        }),
    )
