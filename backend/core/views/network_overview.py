"""
Admin network dashboard: how the network moves, month by month.

  * registered   - people created that month (Ir.started_date), every level.
  * deactivated  - switched inactive that month, reactivated - switched back.
                   From IrStatusChange, so history starts when that log did.
  * removed      - deletions approved that month (IrPendingRequest DELETE).
  * quiet        - a SNAPSHOT at each month's end: people with no info, plan or
                   UV in the 30 days up to that date. Derived from the activity
                   tables, so it is available for past months too.

"Quiet" and "deactivated" are kept apart on purpose. Deactivated is somebody's
decision; quiet is a person still marked active who has stopped doing anything,
which nobody has acted on yet. Players are left out of quiet (they have nothing
to log until promoted) and anyone currently deactivated is left out of it too,
since they are already counted as deactivated. The current flag is all we know
about the past, so an earlier month's quiet figure is "who is active now and
was quiet then" - an approximation, and the UI says so.

Admin only.
"""
from datetime import datetime, timedelta

import pytz
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import (AccessLevel, InfoDetail, Ir, IrPendingRequest,
                         IrStatusChange, PlanDetail, UVDetail)
from core.utils.dates import get_calendar_month_bounds

IST = pytz.timezone("Asia/Kolkata")
QUIET_DAYS = 30
MAX_MONTHS = 24
DRILLDOWN_LIMIT = 200
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

KINDS = {"registered", "deactivated", "reactivated", "removed", "quiet"}


def _admin_or_error(request):
    try:
        requester = Ir.objects.get(ir_id=request.GET.get("requester_ir_id"))
    except Ir.DoesNotExist:
        return None, Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)
    if requester.ir_access_level != AccessLevel.ADMIN:
        return None, Response({"detail": "Admin only"}, status=status.HTTP_403_FORBIDDEN)
    return requester, None


def _recent_months(n):
    """The last n (year, month) pairs in IST, oldest first, ending now."""
    now = datetime.now(IST)
    y, m = now.year, now.month
    out = []
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def _window(year, month):
    """(start, end) of the month in IST; the current month ends now, not at month end."""
    start, end = get_calendar_month_bounds(month, year)
    return start, min(end, datetime.now(IST))


def _quiet_ids(end):
    """ir_ids active-flagged and non-Player with no activity in the QUIET_DAYS up to `end`."""
    since = end - timedelta(days=QUIET_DAYS)
    eligible = set(
        Ir.objects.filter(status=True, started_date__lte=since.date())
        .exclude(ir_access_level=AccessLevel.PLAYER)
        .values_list("ir_id", flat=True)
    )
    if not eligible:
        return set()
    active = set()
    for model, field in ((InfoDetail, "info_date"), (PlanDetail, "plan_date"), (UVDetail, "uv_date")):
        active.update(
            model.objects.filter(ir_id__in=eligible, **{f"{field}__gt": since, f"{field}__lte": end})
            .values_list("ir_id", flat=True).distinct()
        )
    return eligible - active


def _month_counts(year, month):
    start, end = _window(year, month)
    first_day, last_day = start.date(), end.date()
    return {
        "year": year,
        "month": month,
        "label": f"{MONTH_NAMES[month - 1]} {year}",
        "registered": Ir.objects.filter(started_date__gte=first_day, started_date__lte=last_day).count(),
        "deactivated": IrStatusChange.objects.filter(changed_at__gte=start, changed_at__lte=end, is_active=False).count(),
        "reactivated": IrStatusChange.objects.filter(changed_at__gte=start, changed_at__lte=end, is_active=True).count(),
        "removed": IrPendingRequest.objects.filter(
            action_type=IrPendingRequest.ActionType.DELETE,
            request_status=IrPendingRequest.Status.APPROVED,
            reviewed_at__gte=start, reviewed_at__lte=end,
        ).count(),
        "quiet_at_end": len(_quiet_ids(end)),
    }


class NetworkOverview(APIView):
    """GET /api/admin/network_overview/?requester_ir_id=&months=6"""

    def get(self, request):
        _, err = _admin_or_error(request)
        if err:
            return err
        try:
            n = min(max(1, int(request.GET.get("months", 6))), MAX_MONTHS)
        except (TypeError, ValueError):
            n = 6

        today = datetime.now(IST).date()
        waiting = list(
            Ir.objects.filter(ir_access_level=AccessLevel.PLAYER, status=True)
            .values_list("started_date", flat=True)
        )
        first_log = IrStatusChange.objects.order_by("changed_at").values_list("changed_at", flat=True).first()

        return Response({
            "quiet_days": QUIET_DAYS,
            "months": [_month_counts(y, m) for y, m in _recent_months(n)],
            "now": {
                "quiet": len(_quiet_ids(datetime.now(IST))),
                "deactivated": Ir.objects.filter(status=False).exclude(ir_access_level=AccessLevel.PLAYER).count(),
                "players_waiting": len(waiting),
                "oldest_player_wait_days": max(((today - d).days for d in waiting), default=0),
            },
            # Deactivations before this date were never recorded.
            "status_log_started": first_log.astimezone(IST).date().isoformat() if first_log else None,
        })


class NetworkOverviewNames(APIView):
    """GET /api/admin/network_overview/names/?requester_ir_id=&kind=&year=&month="""

    def get(self, request):
        _, err = _admin_or_error(request)
        if err:
            return err
        kind = request.GET.get("kind")
        if kind not in KINDS:
            return Response({"detail": f"kind must be one of {sorted(KINDS)}"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            year, month = int(request.GET.get("year")), int(request.GET.get("month"))
            if not 1 <= month <= 12:
                raise ValueError
        except (TypeError, ValueError):
            return Response({"detail": "year and month are required"}, status=status.HTTP_400_BAD_REQUEST)

        start, end = _window(year, month)

        if kind == "registered":
            qs = Ir.objects.filter(started_date__gte=start.date(), started_date__lte=end.date()).order_by("-started_date")
            rows = [{"ir_id": i.ir_id, "ir_name": i.ir_name, "detail": f"{i.started_date:%d %b}"} for i in qs[:DRILLDOWN_LIMIT]]
            total = qs.count()
        elif kind in ("deactivated", "reactivated"):
            qs = IrStatusChange.objects.filter(
                changed_at__gte=start, changed_at__lte=end, is_active=(kind == "reactivated")
            ).select_related("changed_by").order_by("-changed_at")
            rows = [
                {"ir_id": c.ir_id_text, "ir_name": c.ir_name,
                 "detail": f"{c.changed_at.astimezone(IST):%d %b}" + (f" · by {c.changed_by.ir_name}" if c.changed_by else "")}
                for c in qs[:DRILLDOWN_LIMIT]
            ]
            total = qs.count()
        elif kind == "removed":
            qs = IrPendingRequest.objects.filter(
                action_type=IrPendingRequest.ActionType.DELETE,
                request_status=IrPendingRequest.Status.APPROVED,
                reviewed_at__gte=start, reviewed_at__lte=end,
            ).order_by("-reviewed_at")
            rows = [
                {"ir_id": r.target_ir_code, "ir_name": r.target_ir_name,
                 "detail": f"{r.reviewed_at.astimezone(IST):%d %b}"}
                for r in qs[:DRILLDOWN_LIMIT]
            ]
            total = qs.count()
        else:  # quiet
            ids = _quiet_ids(end)
            irs = Ir.objects.filter(ir_id__in=ids).order_by("ir_name")
            rows = [{"ir_id": i.ir_id, "ir_name": i.ir_name, "detail": f"joined {i.started_date:%d %b %Y}"}
                    for i in irs[:DRILLDOWN_LIMIT]]
            total = len(ids)

        return Response({"kind": kind, "year": year, "month": month, "total": total, "results": rows})
