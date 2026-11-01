"""
Stop an inactive account from using the app.

The app has no session tokens: it keeps the IR ID in the browser and sends it
as `requester_ir_id` with each request. So "logged out when inactive" has to be
enforced here, on the requests themselves — otherwise an inactive person who
is already signed in would keep working until they closed the app.

An inactive requester gets a 403 carrying code "account_inactive"; the app
recognises that and signs the person out. Requests that name no requester
(login, registration, public pages) are untouched.

A chat-only account (Ir.chat_only) is active but partly restricted: it may use
Chat and nothing else. Any other request naming it as requester gets a 403
with code "chat_only"; the app answers by re-reading the person's access and
dropping to the chat-only layout.
"""
import logging

from django.core.cache import cache
from rest_framework.exceptions import ParseError, PermissionDenied, UnsupportedMediaType
from rest_framework.permissions import BasePermission

from core.models import Ir

logger = logging.getLogger(__name__)

INACTIVE_MESSAGE = "Your account is inactive. Please contact your LDC to reactivate your account."
INACTIVE_CODE = "account_inactive"
CHAT_ONLY_MESSAGE = "Your access is limited to Chat. Please contact your LDC."
CHAT_ONLY_CODE = "chat_only"

# What a chat-only account may still call, relative to /api/. Chat itself, its
# helpers (candidates, stickers, GIFs, presence, search), push registration so
# chat notifications keep arriving, and reading their own record so the app can
# see when the restriction is lifted.
_CHAT_ONLY_PREFIXES = (
    "chat_rooms/", "chat_candidates/", "chat_tabs_config/", "chat_messages/",
    "chat_presence/", "sticker_packs/", "gifs/", "save_fcm_token/",
    "notifications/subscribe/", "notifications/unsubscribe/", "notifications/vapid_public_key/",
)

_KEY = "ir_inactive:{}"
_CHAT_ONLY_KEY = "ir_chat_only:{}"
# Short on purpose: it caps how long a change can go unnoticed if something
# flips status without going through ToggleIrStatus (which clears it at once).
_TTL_SECONDS = 30


def is_inactive(ir_id):
    """True if this IR exists and is inactive. Cached; falls back to the DB if the cache is down."""
    key = _KEY.format(ir_id)
    try:
        cached = cache.get(key)
    except Exception:
        cached = None
    if cached is not None:
        return cached
    inactive = Ir.objects.filter(ir_id=ir_id, status=False).exists()
    try:
        cache.set(key, inactive, _TTL_SECONDS)
    except Exception:
        logger.warning("could not cache inactive flag for %s", ir_id)
    return inactive


def is_chat_only(ir_id):
    """True if this IR is active but limited to Chat. Cached like is_inactive."""
    key = _CHAT_ONLY_KEY.format(ir_id)
    try:
        cached = cache.get(key)
    except Exception:
        cached = None
    if cached is not None:
        return cached
    restricted = Ir.objects.filter(ir_id=ir_id, status=True, chat_only=True).exists()
    try:
        cache.set(key, restricted, _TTL_SECONDS)
    except Exception:
        logger.warning("could not cache chat-only flag for %s", ir_id)
    return restricted


def forget_inactive(ir_id):
    """Drop the cached flags so a status change takes effect on the very next request."""
    try:
        cache.delete_many([_KEY.format(ir_id), _CHAT_ONLY_KEY.format(ir_id)])
    except Exception:
        logger.warning("could not clear inactive flag for %s", ir_id)


def _chat_only_may_call(request, requester):
    path = request.path
    rel = path[len("/api/"):] if path.startswith("/api/") else path.lstrip("/")
    if rel.startswith(_CHAT_ONLY_PREFIXES):
        return True
    # Their own record (GET /api/ir/<their id>/), nobody else's.
    return request.method == "GET" and rel == f"ir/{requester}/"


class ActiveRequesterPermission(BasePermission):
    """Refuse any request made on behalf of an inactive IR."""

    def has_permission(self, request, view):
        requester = request.query_params.get("requester_ir_id")
        if not requester and request.method in ("POST", "PUT", "PATCH", "DELETE"):
            # A body this API can't parse is the view's business, as it always
            # was — reading it here must not turn that into a new error.
            try:
                data = request.data
            except (ParseError, UnsupportedMediaType):
                data = None
            requester = data.get("requester_ir_id") if hasattr(data, "get") else None
        if isinstance(requester, str) and requester and is_inactive(requester):
            raise PermissionDenied(detail={"detail": INACTIVE_MESSAGE, "code": INACTIVE_CODE})
        if (isinstance(requester, str) and requester and is_chat_only(requester)
                and not _chat_only_may_call(request, requester)):
            raise PermissionDenied(detail={"detail": CHAT_ONLY_MESSAGE, "code": CHAT_ONLY_CODE})
        return True
