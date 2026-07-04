from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse


def health_check(request):
    """
    Verifies the process can actually serve traffic, not just that it's
    running — matters once there's more than one instance behind a load
    balancer, and during zero-downtime deploys, where a process that's up
    but can't reach Postgres/Redis should not receive requests.
    """
    checks = {}

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"

    try:
        cache.set("health_check_probe", "1", 5)
        checks["cache"] = "ok" if cache.get("health_check_probe") == "1" else "error: probe mismatch"
    except Exception as exc:
        checks["cache"] = f"error: {exc}"

    healthy = all(v == "ok" for v in checks.values())
    return JsonResponse({"status": "ok" if healthy else "degraded", "checks": checks}, status=200 if healthy else 503)
