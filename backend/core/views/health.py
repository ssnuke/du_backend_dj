from django.conf import settings
from django.db import connection, transaction
from django.http import JsonResponse

import redis

DB_TIMEOUT_MS = 2000
REDIS_TIMEOUT_S = 2


def health_check(request):
    """
    Verifies the process can actually serve traffic, not just that it's
    running — matters once there's more than one instance behind a load
    balancer, and during zero-downtime deploys, where a process that's up
    but can't reach Postgres/Redis should not receive requests.

    Both checks are bounded so this view can never itself become the slow
    straggler that's still in flight when a deploy's shutdown signal arrives
    — see the "took too long to shut down and was killed" failure mode this
    was written to avoid: without a timeout, a contended DB/Redis pool could
    leave this blocking synchronous view queued on Django's shared
    sync-to-async thread pool (same one core/chat/consumers.py's
    _fcm_executor was split out from, for the same reason) long enough that
    Daphne's graceful-shutdown window expires before it ever completes.
    """
    checks = {}

    try:
        # SET LOCAL scopes the timeout to this transaction only — it resets
        # automatically on commit, so it can't leak onto whatever the next
        # request does with this same pooled connection (CONN_MAX_AGE=60
        # reuses connections across requests).
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(f"SET LOCAL statement_timeout = '{DB_TIMEOUT_MS}ms'")
                cursor.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"

    try:
        # A throwaway client with its own tight timeout, deliberately not
        # django-redis's shared cache client/pool — this both fails fast on
        # its own regardless of that pool's contention, and doesn't spend one
        # of the deliberately small REDIS_CACHE_MAX_CONNECTIONS slots on a
        # periodic probe.
        redis_url = settings.CACHES["default"]["LOCATION"]
        client = redis.Redis.from_url(
            redis_url, socket_timeout=REDIS_TIMEOUT_S, socket_connect_timeout=REDIS_TIMEOUT_S
        )
        client.set("health_check_probe", "1", ex=5)
        checks["cache"] = "ok" if client.get("health_check_probe") == b"1" else "error: probe mismatch"
    except Exception as exc:
        checks["cache"] = f"error: {exc}"

    healthy = all(v == "ok" for v in checks.values())
    return JsonResponse({"status": "ok" if healthy else "degraded", "checks": checks}, status=200 if healthy else 503)
