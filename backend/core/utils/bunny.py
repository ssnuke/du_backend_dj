"""
Bunny.net cache purging.

Confirmed by inspecting real response headers (Cdn-Cache: HIT, Cdn-Cachedat
unchanged across requests with different ?v= query strings) that Bunny's
Stream CDN caches purely by path and ignores the query string entirely — so
appending a cache-busting query param to a thumbnail URL (see
core/views/learn.py _with_cache_buster) does not, on its own, force a fresh
pull from origin. The only way to actually invalidate an already-cached file
is to call Bunny's Purge Cache API directly.

This requires the ACCOUNT-level API key (My Account → API on Bunny's
dashboard, settings.BUNNY_ACCOUNT_API_KEY) — the per-library Stream API key
(settings.BUNNY_STREAM_API_KEY) is scoped to that library's Stream API only
and cannot call this endpoint.
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

PURGE_URL = "https://api.bunny.net/purge"


def get_bunny_thumbnail_filename(bunny_library_id: str, bunny_video_id: str):
    """
    Ask Bunny's Stream API what the video's thumbnail file is actually named
    (its `thumbnailFileName` field), instead of assuming "thumbnail.jpg".
    That assumption is wrong whenever a custom thumbnail was set rather than
    auto-generated — confirmed by a real 404 (surfaced in-app as
    net::ERR_BLOCKED_BY_ORB, Chrome's opaque-response-blocking reaction to a
    non-image response landing in an <img> tag) for a video whose actual
    thumbnail file has a different name.

    Returns the filename string, or None if it can't be determined (missing
    API key, request error, or the field is genuinely null on Bunny's side).
    Uses the per-library Stream API key (BUNNY_STREAM_API_KEY) — this is a
    Stream API operation, not account-level, unlike purge_bunny_url above.
    """
    api_key = settings.BUNNY_STREAM_API_KEY
    if not api_key:
        logger.warning("BUNNY_STREAM_API_KEY is not set — cannot look up thumbnail filename")
        return None

    url = f"https://video.bunnycdn.com/library/{bunny_library_id}/videos/{bunny_video_id}"
    try:
        response = requests.get(url, headers={"AccessKey": api_key}, timeout=10)
        if response.status_code != 200:
            logger.error(
                "Bunny get-video failed for library %s video %s: HTTP %s — %s",
                bunny_library_id, bunny_video_id, response.status_code, response.text[:200],
            )
            return None
        return response.json().get("thumbnailFileName") or None
    except requests.RequestException:
        logger.exception("Error calling Bunny get-video API for %s/%s", bunny_library_id, bunny_video_id)
        return None


def purge_bunny_url(url: str) -> bool:
    """
    Purge a single URL from Bunny's CDN cache. Returns True on success, False
    otherwise (never raises — a failed purge shouldn't break the admin action
    that triggered it; it just means the thumbnail may still be stale).
    """
    api_key = settings.BUNNY_ACCOUNT_API_KEY
    if not api_key:
        logger.warning("BUNNY_ACCOUNT_API_KEY is not set — cannot purge Bunny cache for %s", url)
        return False

    try:
        response = requests.post(
            PURGE_URL,
            params={"url": url},
            headers={"AccessKey": api_key},
            timeout=10,
        )
        if response.status_code in (200, 204):
            logger.info("Purged Bunny CDN cache for %s", url)
            return True
        logger.error(
            "Bunny purge failed for %s: HTTP %s — %s", url, response.status_code, response.text[:200]
        )
        return False
    except requests.RequestException:
        logger.exception("Error calling Bunny purge API for %s", url)
        return False
