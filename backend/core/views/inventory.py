from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from core.models import Ir, InventoryFile, InventoryVideo, AccessLevel
from core.views.learn import _generate_bunny_token, raw_thumbnail_url, _with_cache_buster


def _visible_filter(ir):
    """Visibility values the given IR may see."""
    if AccessLevel.is_ldc_and_above(ir.ir_access_level):
        return ["everyone", "gc_and_above", "leadership"]
    if AccessLevel.is_gc_and_above(ir.ir_access_level):
        return ["everyone", "gc_and_above"]
    return ["everyone"]


def _file_list_item(file) -> dict:
    return {
        "id": file.id,
        "title": file.title,
        "description": file.description,
        "file_url": file.file_url,
        "file_size": file.file_size,
        "visibility": file.visibility,
        "order": file.order,
    }


def _video_list_item(video) -> dict:
    """Serialise an inventory video for the list view (no stream URL)."""
    thumbnail_url = _with_cache_buster(raw_thumbnail_url(video), video.updated_at.timestamp() if video.updated_at else 0)
    return {
        "id": video.id,
        "title": video.title,
        "description": video.description,
        "thumbnail_url": thumbnail_url,
        "duration_seconds": video.duration_seconds,
        "visibility": video.visibility,
        "order": video.order,
    }


class GetInventoryFiles(APIView):
    """Returns published files list for any authenticated IR, filtered by visibility."""
    def get(self, request, ir_id):
        try:
            ir = Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response({"message": "IR not found"}, status=status.HTTP_404_NOT_FOUND)

        files = InventoryFile.objects.filter(is_published=True, visibility__in=_visible_filter(ir))
        return Response([_file_list_item(f) for f in files])


class GetInventoryVideos(APIView):
    """Returns published videos list for any authenticated IR, filtered by visibility."""
    def get(self, request, ir_id):
        try:
            ir = Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response({"message": "IR not found"}, status=status.HTTP_404_NOT_FOUND)

        videos = InventoryVideo.objects.filter(is_published=True, visibility__in=_visible_filter(ir))
        return Response([_video_list_item(v) for v in videos])


class GetInventoryVideoStream(APIView):
    """Returns a signed Bunny stream URL for a specific inventory video."""
    def get(self, request, ir_id, video_id):
        try:
            ir = Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response({"message": "IR not found"}, status=status.HTTP_404_NOT_FOUND)

        try:
            video = InventoryVideo.objects.get(id=video_id, is_published=True, visibility__in=_visible_filter(ir))
        except InventoryVideo.DoesNotExist:
            return Response({"message": "Video not found"}, status=status.HTTP_404_NOT_FOUND)

        cdn_hostname = settings.BUNNY_STREAM_CDN_HOSTNAME
        if not cdn_hostname or not settings.BUNNY_STREAM_TOKEN_KEY:
            # Token auth not configured — return plain URL (dev fallback)
            stream_url = f"https://{cdn_hostname}/{video.bunny_video_id}/playlist.m3u8"
            return Response({
                "stream_url": stream_url,
                "expires_at": None,
            })

        token, expiry = _generate_bunny_token(video.bunny_video_id)
        stream_url = (
            f"https://{cdn_hostname}/{video.bunny_video_id}/playlist.m3u8"
            f"?token={token}&expires={expiry}"
        )

        return Response({
            "stream_url": stream_url,
            "expires_at": expiry,
        })
