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
