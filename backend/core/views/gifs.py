import logging

import requests
from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)

GIPHY_BASE_URL = "https://api.giphy.com/v1/gifs"
GIPHY_TIMEOUT_SECONDS = 5
RESULT_LIMIT = 25


def _serialize_giphy_gif(gif):
    images = gif.get("images", {})
    original = images.get("original", {})
    fixed_width = images.get("fixed_width", {})

    # Prefer the mp4 rendition for the actual message (smaller than the raw
    # .gif, same bandwidth-conscious approach used for chat attachments
    # elsewhere in this app) — fall back to the .gif url if no mp4 is given.
    full_url = original.get("mp4") or original.get("url")
    preview_url = fixed_width.get("webp") or fixed_width.get("url") or full_url

    return {
        "id": gif.get("id"),
        "title": gif.get("title", ""),
        "preview_url": preview_url,
        "full_url": full_url,
    }


def _fetch_from_giphy(endpoint, params):
    if not settings.GIPHY_API_KEY:
        return None, "GIPHY_API_KEY is not configured"

    params = {**params, "api_key": settings.GIPHY_API_KEY, "limit": RESULT_LIMIT, "rating": "pg-13"}
    try:
        resp = requests.get(f"{GIPHY_BASE_URL}/{endpoint}", params=params, timeout=GIPHY_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json().get("data", []), None
    except requests.RequestException as exc:
        logger.warning("GIPHY %s request failed: %s", endpoint, exc)
        return None, "Unable to reach GIPHY right now"


class GifSearch(APIView):
    def get(self, request):
        query = (request.GET.get("q") or "").strip()
        if not query:
            return Response({"detail": "q is required"}, status=status.HTTP_400_BAD_REQUEST)

        data, error = _fetch_from_giphy("search", {"q": query})
        if error:
            return Response({"detail": error}, status=status.HTTP_502_BAD_GATEWAY)

        return Response({"gifs": [_serialize_giphy_gif(gif) for gif in data]})


class GifTrending(APIView):
    def get(self, request):
        data, error = _fetch_from_giphy("trending", {})
        if error:
            return Response({"detail": error}, status=status.HTTP_502_BAD_GATEWAY)

        return Response({"gifs": [_serialize_giphy_gif(gif) for gif in data]})
