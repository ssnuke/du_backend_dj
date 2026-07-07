import hashlib
import hmac
import time
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from core.models import Ir, LearnVideo


def _with_cache_buster(url: str, version) -> str:
    """
    Append ?v=<version> to a thumbnail URL. Bunny's CDN and this app's own
    service worker (cache-first for images) both cache purely by URL with no
    expiry — re-uploading a thumbnail on Bunny's dashboard changes the image
    bytes but not the URL, so every cache layer keeps serving the old image
    indefinitely. Changing the URL whenever `version` changes is what
    actually busts both caches.
    """
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query))
    query["v"] = str(version)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _generate_bunny_token(video_id: str, expires_in_seconds: int = 7200) -> tuple[str, int]:
    """
    Generate a Bunny.net Stream token-auth URL.
    Returns (token, expiry_timestamp).

    Bunny token formula:
        token = sha256(TOKEN_KEY + video_path + expiry_timestamp)
        URL   = https://{CDN_HOSTNAME}/{video_id}/playlist.m3u8?token={token}&expires={expiry}
    """
    token_key = settings.BUNNY_STREAM_TOKEN_KEY
    expiry = int(time.time()) + expires_in_seconds
    video_path = f"/{video_id}/playlist.m3u8"

    hash_string = token_key + video_path + str(expiry)
    token = hashlib.sha256(hash_string.encode("utf-8")).digest()
    import base64
    token_b64 = base64.b64encode(token).decode("utf-8")
    # Bunny expects URL-safe base64 without padding
    token_b64 = token_b64.replace("+", "-").replace("/", "_").rstrip("=")

    return token_b64, expiry


def raw_thumbnail_url(video) -> str:
    """
    The actual Bunny thumbnail URL with no cache-busting query param — this is
    the exact path Bunny's CDN keys its cache by (it ignores query strings
    entirely, confirmed via response headers), so it's what must be passed to
    the Purge Cache API (see core/utils/bunny.py). Shared by LearnVideo and
    DreamVideo, which have identical thumbnail_url/bunny_video_id fields.
    """
    if video.thumbnail_url:
        return video.thumbnail_url
    cdn = settings.BUNNY_STREAM_CDN_HOSTNAME
    return f"https://{cdn}/{video.bunny_video_id}/thumbnail.jpg"


def _bunny_thumbnail_url(video: LearnVideo) -> str:
    """Return the Bunny Stream auto-generated thumbnail URL, falling back to any manually set URL."""
    url = raw_thumbnail_url(video)
    return _with_cache_buster(url, video.updated_at.timestamp() if video.updated_at else 0)


def _video_list_item(video: LearnVideo) -> dict:
    """Serialise a video for the list view (no stream URL)."""
    return {
        "id": video.id,
        "title": video.title,
        "description": video.description,
        "thumbnail_url": _bunny_thumbnail_url(video),
        "duration_seconds": video.duration_seconds,
        "order": video.order,
    }


class GetLearnVideos(APIView):
    """Returns published videos list for any authenticated IR."""
    def get(self, request, ir_id):
        try:
            Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response({"message": "IR not found"}, status=404)

        videos = LearnVideo.objects.filter(is_published=True)
        return Response([_video_list_item(v) for v in videos])


class GetLearnVideoStream(APIView):
    """Returns a signed Bunny stream URL for a specific video."""
    def get(self, request, ir_id, video_id):
        try:
            Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response({"message": "IR not found"}, status=status.HTTP_404_NOT_FOUND)

        try:
            video = LearnVideo.objects.get(id=video_id, is_published=True)
        except LearnVideo.DoesNotExist:
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
