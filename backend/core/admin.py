from django.contrib import admin
from core.models import LearnVideo, DreamVideo, StickerPack, Sticker


@admin.register(LearnVideo)
class LearnVideoAdmin(admin.ModelAdmin):
    list_display = ('title', 'order', 'duration_seconds', 'is_published', 'created_at')
    list_editable = ('order', 'duration_seconds', 'is_published')
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
