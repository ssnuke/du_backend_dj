import hashlib
import hmac
import time
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

from django.conf import settings
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from core.learn_curriculum import LESSONS
from core.models import AccessLevel, Ir, LearnVideo, LearnVideoProgress, RESTRICTED_LEARN_CATEGORIES

COURSE_CATEGORY = "basic_training"


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
        "category": video.category,
        "order": video.order,
    }


def _blocked_categories_for(ir) -> list:
    """
    Learn categories this IR may not see. Access level is a number where
    lower is more senior, so a category needing LS (4) is blocked for anyone
    whose level is greater than 4.
    """
    level = ir.ir_access_level
    return [cat for cat, min_level in RESTRICTED_LEARN_CATEGORIES.items()
            if level is None or level > min_level]


def _course_items(ir, course_videos):
    """
    The Basic Training tab as a course: every lesson in the outline, in order,
    each marked with whether it is completed and whether it is locked.

    Only a Player is ever locked. Their rule is Udemy's: a lesson opens once
    every earlier lesson that HAS a video has been finished. A slot with no
    published video is "coming soon" and does not hold anyone up. For everyone
    else every lesson is open from the start, which is also what a Player gets
    the moment they are promoted — there is no unlock state to carry over.

    Videos not assigned to a slot are listed after the course. For a Player
    they open once the whole course is done, so they cannot be used to skip it.
    """
    by_key = {v.lesson_key: v for v in course_videos if v.lesson_key}
    extras = [v for v in course_videos if not v.lesson_key]
    done = set(
        LearnVideoProgress.objects.filter(ir=ir, video__in=course_videos, completed_at__isnull=False)
        .values_list("video_id", flat=True)
    )
    gated = ir.ir_access_level == AccessLevel.PLAYER

    items = []
    earlier_unfinished = False
    for number, lesson in enumerate(LESSONS, start=1):
        video = by_key.get(lesson["key"])
        base = {"lesson_key": lesson["key"], "lesson_number": number, "scripts": lesson["scripts"]}
        if video is None:
            items.append({**base, "id": None, "title": lesson["title"], "category": COURSE_CATEGORY,
                          "coming_soon": True, "locked": False, "completed": False})
            continue
        completed = video.id in done
        items.append({
            **_video_list_item(video), **base,
            "coming_soon": False,
            "completed": completed,
            "locked": gated and earlier_unfinished,
        })
        if not completed:
            earlier_unfinished = True

    for video in extras:
        completed = video.id in done
        items.append({
            **_video_list_item(video), "lesson_key": None, "lesson_number": None, "scripts": [],
            "coming_soon": False, "completed": completed,
            "locked": gated and earlier_unfinished,
        })
    return items


def _is_locked_for(ir, video):
    """True when `ir` may not yet play `video` (Players only, Basic Training only)."""
    if ir.ir_access_level != AccessLevel.PLAYER or video.category != COURSE_CATEGORY:
        return False
    course = LearnVideo.objects.filter(is_published=True, category=COURSE_CATEGORY)
    for item in _course_items(ir, course):
        if item["id"] == video.id:
            return item["locked"]
    return False


class GetLearnVideos(APIView):
    """
    Published videos for an IR, minus any restricted category they are not
    senior enough for. Filtered here rather than only in the client: hiding a
    tab still ships the videos in this payload.
    """
    def get(self, request, ir_id):
        try:
            ir = Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response({"message": "IR not found"}, status=404)

        videos = LearnVideo.objects.filter(is_published=True)
        blocked = _blocked_categories_for(ir)
        if blocked:
            videos = videos.exclude(category__in=blocked)

        items = [_video_list_item(v) for v in videos.exclude(category=COURSE_CATEGORY)]
        if COURSE_CATEGORY not in blocked:
            items.extend(_course_items(ir, videos.filter(category=COURSE_CATEGORY)))

        # Where they left off, for any video that is not finished.
        resume = dict(
            LearnVideoProgress.objects.filter(ir=ir, completed_at__isnull=True, position_seconds__gt=0)
            .values_list("video_id", "position_seconds")
        )
        for item in items:
            item["resume_seconds"] = int(resume.get(item.get("id"), 0))
        return Response(items)


class GetLearnVideoStream(APIView):
    """Returns a signed Bunny stream URL for a specific video."""
    def get(self, request, ir_id, video_id):
        try:
            ir = Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response({"message": "IR not found"}, status=status.HTTP_404_NOT_FOUND)

        try:
            video = LearnVideo.objects.get(id=video_id, is_published=True)
        except LearnVideo.DoesNotExist:
            return Response({"message": "Video not found"}, status=status.HTTP_404_NOT_FOUND)

        # Filtering the list is not enough on its own — this endpoint takes an
        # id, so without the same check a restricted video stays playable by
        # anyone who guesses or keeps one. Same 404 as a missing video, so the
        # response does not confirm that a hidden video exists.
        if video.category in _blocked_categories_for(ir):
            return Response({"message": "Video not found"}, status=status.HTTP_404_NOT_FOUND)

        # A Player works through Basic Training in order. The list marks later
        # lessons locked, but that is only a display: this is what stops a
        # locked video being played by asking for it directly.
        if _is_locked_for(ir, video):
            return Response(
                {"message": "Finish the previous lesson to unlock this one."},
                status=status.HTTP_403_FORBIDDEN,
            )

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


class CompleteLearnVideo(APIView):
    """
    POST /api/learn/<ir_id>/videos/<video_id>/complete/

    Records that a video was watched to the end. The player only calls this
    once playback reaches the end, and for a Player seeking ahead of what they
    have actually watched is blocked there — so this trusts the client no more
    than the rest of the app does. It does refuse a lesson that is still locked.
    """
    def post(self, request, ir_id, video_id):
        try:
            ir = Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response({"message": "IR not found"}, status=status.HTTP_404_NOT_FOUND)
        try:
            video = LearnVideo.objects.get(id=video_id, is_published=True)
        except LearnVideo.DoesNotExist:
            return Response({"message": "Video not found"}, status=status.HTTP_404_NOT_FOUND)

        if video.category in _blocked_categories_for(ir):
            return Response({"message": "Video not found"}, status=status.HTTP_404_NOT_FOUND)
        if _is_locked_for(ir, video):
            return Response({"message": "This lesson is still locked."}, status=status.HTTP_403_FORBIDDEN)

        progress, _ = LearnVideoProgress.objects.get_or_create(ir=ir, video=video)
        if progress.completed_at is None:
            progress.completed_at = timezone.now()
            # A finished video replays from the start, not from the end.
            progress.position_seconds = 0
            progress.save(update_fields=["completed_at", "position_seconds", "updated_at"])
        return Response({"completed": True, "video_id": video.id})


class SaveLearnVideoProgress(APIView):
    """
    POST /api/learn/<ir_id>/videos/<video_id>/progress/  {"position_seconds": 123.4}

    Remembers where someone stopped so they can carry on from there. Called
    every few seconds while watching and when they pause or leave, so it is
    deliberately cheap: one row per person and video. Once a video is finished
    the position is no longer kept, since it would only send them to the last
    seconds next time. Saving 0 clears it (used for videos outside the course
    when they reach the end).
    """
    def post(self, request, ir_id, video_id):
        try:
            ir = Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response({"message": "IR not found"}, status=status.HTTP_404_NOT_FOUND)
        try:
            video = LearnVideo.objects.get(id=video_id, is_published=True)
        except LearnVideo.DoesNotExist:
            return Response({"message": "Video not found"}, status=status.HTTP_404_NOT_FOUND)

        if video.category in _blocked_categories_for(ir):
            return Response({"message": "Video not found"}, status=status.HTTP_404_NOT_FOUND)
        if _is_locked_for(ir, video):
            return Response({"message": "This lesson is still locked."}, status=status.HTTP_403_FORBIDDEN)

        try:
            position = float(request.data.get("position_seconds"))
        except (TypeError, ValueError):
            return Response({"message": "position_seconds must be a number"}, status=status.HTTP_400_BAD_REQUEST)
        if position != position or position < 0:  # NaN or negative
            return Response({"message": "position_seconds must be zero or more"}, status=status.HTTP_400_BAD_REQUEST)
        if video.duration_seconds:
            position = min(position, float(video.duration_seconds))

        progress, _ = LearnVideoProgress.objects.get_or_create(ir=ir, video=video)
        if progress.completed_at is None:
            progress.position_seconds = position
            progress.save(update_fields=["position_seconds", "updated_at"])
        return Response({"position_seconds": progress.position_seconds})
