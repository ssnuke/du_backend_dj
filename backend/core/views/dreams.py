from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from core.models import Ir, DreamVideo
from core.views.learn import _generate_bunny_token


def _bunny_thumbnail_url(video: DreamVideo) -> str:
    if video.thumbnail_url:
        return video.thumbnail_url
    cdn = settings.BUNNY_STREAM_CDN_HOSTNAME
    return f"https://{cdn}/{video.bunny_video_id}/thumbnail.jpg"


def _dream_video_list_item(video: DreamVideo) -> dict:
    return {
        "id": video.id,
        "title": video.title,
        "description": video.description,
        "thumbnail_url": _bunny_thumbnail_url(video),
        "duration_seconds": video.duration_seconds,
        "order": video.order,
    }


class GetDreamVideos(APIView):
    def get(self, request, ir_id):
        try:
            Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response({"message": "IR not found"}, status=404)

        videos = DreamVideo.objects.filter(is_published=True)
        return Response([_dream_video_list_item(v) for v in videos])


class GetDreamVideoStream(APIView):
    def get(self, request, ir_id, video_id):
        try:
            Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response({"message": "IR not found"}, status=status.HTTP_404_NOT_FOUND)

        try:
            video = DreamVideo.objects.get(id=video_id, is_published=True)
        except DreamVideo.DoesNotExist:
            return Response({"message": "Video not found"}, status=status.HTTP_404_NOT_FOUND)

        cdn_hostname = settings.BUNNY_STREAM_CDN_HOSTNAME
        if not cdn_hostname or not settings.BUNNY_STREAM_TOKEN_KEY:
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
