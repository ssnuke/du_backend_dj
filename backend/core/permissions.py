"""
Stop an inactive account from using the app.

The app has no session tokens: it keeps the IR ID in the browser and sends it
as `requester_ir_id` with each request. So "logged out when inactive" has to be
enforced here, on the requests themselves — otherwise an inactive person who
is already signed in would keep working until they closed the app.

An inactive requester gets a 403 carrying code "account_inactive"; the app
recognises that and signs the person out. Requests that name no requester
(login, registration, public pages) are untouched.
"""
import logging

from django.core.cache import cache
from rest_framework.exceptions import ParseError, PermissionDenied, UnsupportedMediaType
from rest_framework.permissions import BasePermission

from core.models import Ir

logger = logging.getLogger(__name__)

INACTIVE_MESSAGE = "Your account is inactive. Please contact your LDC to reactivate your account."
INACTIVE_CODE = "account_inactive"

_KEY = "ir_inactive:{}"
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


def forget_inactive(ir_id):
    """Drop the cached flag so a status change takes effect on the very next request."""
    try:
        cache.delete(_KEY.format(ir_id))
    except Exception:
        logger.warning("could not clear inactive flag for %s", ir_id)


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
        return True
