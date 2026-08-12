from django.conf import settings
from django.contrib import admin
from core.models import LearnVideo, DreamVideo, StickerPack, Sticker, InventoryFile, InventoryVideo
from core.views.learn import raw_thumbnail_url
from core.utils.bunny import purge_bunny_url, get_bunny_thumbnail_filename


def refresh_thumbnail_cache(modeladmin, request, queryset):
    """
    Bunny's CDN caches the thumbnail purely by path (confirmed via response
    headers — Cdn-Cache stayed a HIT across requests with different ?v=
    cache-busting query strings, so it ignores the query entirely), and this
    app's own service worker cache-first-caches images with no expiry. Both
    only get invalidated by: (1) actually purging Bunny's edge cache for the
    real thumbnail URL, and (2) bumping `updated_at`, which changes the
    `?v=<updated_at>` the app serves to clients (see
    core/views/learn.py _bunny_thumbnail_url) so their own caches see a new
    URL too. Re-uploading a thumbnail directly on Bunny's dashboard does
    neither on its own — this action does both in one click.

    Also re-resolves the actual thumbnail filename from Bunny's Stream API
    (thumbnailFileName) and persists it to thumbnail_url — the hardcoded
    "thumbnail.jpg" guess used when that field is blank is wrong for any
    video with a custom (non-auto-generated) thumbnail, which 404s and shows
    up in the app as a broken image (net::ERR_BLOCKED_BY_ORB).
    """
    purged, failed, resolved = 0, 0, 0
    for obj in queryset:
        filename = get_bunny_thumbnail_filename(obj.bunny_library_id, obj.bunny_video_id)
        if filename:
            new_url = f"https://{settings.BUNNY_STREAM_CDN_HOSTNAME}/{obj.bunny_video_id}/{filename}"
            if new_url != obj.thumbnail_url:
                obj.thumbnail_url = new_url
                resolved += 1

        if purge_bunny_url(raw_thumbnail_url(obj)):
            purged += 1
        else:
            failed += 1
        obj.save(update_fields=['thumbnail_url', 'updated_at'])

    if failed:
        modeladmin.message_user(
            request,
            f"Refreshed {purged} video(s) ({resolved} thumbnail URL(s) corrected), but Bunny purge "
            f"failed for {failed} — check BUNNY_ACCOUNT_API_KEY is set correctly. "
            f"updated_at was still bumped for all.",
            level='WARNING',
        )
    else:
        modeladmin.message_user(request, f"Refreshed thumbnail cache for {purged} video(s).")
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


@admin.register(InventoryVideo)
class InventoryVideoAdmin(admin.ModelAdmin):
    list_display = ('title', 'visibility', 'order', 'duration_seconds', 'is_published', 'created_at')
    list_editable = ('visibility', 'order', 'duration_seconds', 'is_published')
    list_filter = ('visibility', 'is_published')
    search_fields = ('title', 'description', 'bunny_video_id')
    ordering = ('order', 'created_at')
    actions = [refresh_thumbnail_cache]
    fieldsets = (
        (None, {
            'fields': ('title', 'description', 'visibility', 'order', 'is_published'),
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


@admin.register(InventoryFile)
class InventoryFileAdmin(admin.ModelAdmin):
    list_display = ('title', 'visibility', 'order', 'file_size', 'is_published', 'created_at')
    list_editable = ('visibility', 'order', 'is_published')
    list_filter = ('visibility', 'is_published')
    search_fields = ('title', 'description')
    ordering = ('order', 'created_at')
    readonly_fields = ('file_url', 'file_size')
    fields = ('title', 'description', 'admin_upload', 'file_url', 'file_size', 'visibility', 'order', 'is_published')


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
