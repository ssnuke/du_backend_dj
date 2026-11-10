"""
Network dashboard: how the network moves, month by month.

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

Who sees it, and whose people the figures cover:

  * Admin, CTC : everyone.
  * LDC        : their own people - the members of their teams (the list the IR
                 Status tab uses), everyone they registered, and the Players under
                 them (the ones they can promote). Never Admin/CTC or themselves.

An LDC's "removed" is the deletions THEY requested: a removed person is gone from
the Ir table, so there is no team left to find them in.
"""
from datetime import datetime, timedelta

import pytz
from django.db.models import Q
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import (inactive_reason_text, AccessLevel, InfoDetail, Ir, IrPendingRequest,
                         IrStatusChange, PlanDetail, UVDetail)
from core.utils.dates import get_calendar_month_bounds
from core.views.get import get_status_manageable_irs

IST = pytz.timezone("Asia/Kolkata")
QUIET_DAYS = 30
MAX_MONTHS = 24
DRILLDOWN_LIMIT = 200
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

KINDS = {"registered", "deactivated", "reactivated", "removed", "quiet"}


def _requester_or_error(request):
    try:
        requester = Ir.objects.get(ir_id=request.GET.get("requester_ir_id"))
    except Ir.DoesNotExist:
        return None, Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)
    if requester.ir_access_level not in (AccessLevel.ADMIN, AccessLevel.CTC, AccessLevel.LDC):
        return None, Response({"detail": "Only Admin, CTC and LDC can see the network overview"},
                              status=status.HTTP_403_FORBIDDEN)
    return requester, None


def _scope(requester):
    """The people whose figures this requester may see."""
    if requester.has_full_access():
        return Ir.objects.all()
    return Ir.objects.filter(
        Q(ir_id__in=get_status_manageable_irs(requester).values("ir_id"))
        | Q(parent_ir=requester)
        | Q(ir_access_level=AccessLevel.PLAYER, ir_id__in=requester.get_all_downlines().values("ir_id"))
    ).exclude(
        ir_access_level__in=(AccessLevel.ADMIN, AccessLevel.CTC)
    ).exclude(ir_id=requester.ir_id)


def _status_changes(requester, scope):
    qs = IrStatusChange.objects.all()
    return qs if requester.has_full_access() else qs.filter(ir_id_text__in=scope.values("ir_id"))


def _removals(requester):
    qs = IrPendingRequest.objects.filter(
        action_type=IrPendingRequest.ActionType.DELETE,
        request_status=IrPendingRequest.Status.APPROVED,
    )
    return qs if requester.has_full_access() else qs.filter(requested_by=requester)


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


def _quiet_ids(end, scope):
    """ir_ids in `scope`, active-flagged and non-Player, with no activity in the QUIET_DAYS up to `end`."""
    since = end - timedelta(days=QUIET_DAYS)
    eligible = set(
        scope.filter(status=True, started_date__lte=since.date())
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


def _month_counts(year, month, requester, scope):
    start, end = _window(year, month)
    first_day, last_day = start.date(), end.date()
    return {
        "year": year,
        "month": month,
        "label": f"{MONTH_NAMES[month - 1]} {year}",
        "registered": scope.filter(started_date__gte=first_day, started_date__lte=last_day).count(),
        "deactivated": _status_changes(requester, scope).filter(changed_at__gte=start, changed_at__lte=end, is_active=False).count(),
        "reactivated": _status_changes(requester, scope).filter(changed_at__gte=start, changed_at__lte=end, is_active=True).count(),
        "removed": _removals(requester).filter(reviewed_at__gte=start, reviewed_at__lte=end).count(),
        "quiet_at_end": len(_quiet_ids(end, scope)),
    }


class NetworkOverview(APIView):
    """GET /api/admin/network_overview/?requester_ir_id=&months=6"""

    def get(self, request):
        requester, err = _requester_or_error(request)
        if err:
            return err
        scope = _scope(requester)
        try:
            n = min(max(1, int(request.GET.get("months", 6))), MAX_MONTHS)
        except (TypeError, ValueError):
            n = 6

        today = datetime.now(IST).date()
        waiting = list(
            scope.filter(ir_access_level=AccessLevel.PLAYER, status=True)
            .values_list("started_date", flat=True)
        )
        first_log = IrStatusChange.objects.order_by("changed_at").values_list("changed_at", flat=True).first()

        return Response({
            "quiet_days": QUIET_DAYS,
            "scope": "all" if requester.has_full_access() else "team",
            "months": [_month_counts(y, m, requester, scope) for y, m in _recent_months(n)],
            "now": {
                "quiet": len(_quiet_ids(datetime.now(IST), scope)),
                "deactivated": scope.filter(status=False).exclude(ir_access_level=AccessLevel.PLAYER).count(),
                "players_waiting": len(waiting),
                "oldest_player_wait_days": max(((today - d).days for d in waiting), default=0),
            },
            # Deactivations before this date were never recorded.
            "status_log_started": first_log.astimezone(IST).date().isoformat() if first_log else None,
        })


class NetworkOverviewNames(APIView):
    """GET /api/admin/network_overview/names/?requester_ir_id=&kind=&year=&month="""

    def get(self, request):
        requester, err = _requester_or_error(request)
        if err:
            return err
        scope = _scope(requester)
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
            qs = scope.filter(started_date__gte=start.date(), started_date__lte=end.date()).order_by("-started_date")
            rows = [{"ir_id": i.ir_id, "ir_name": i.ir_name, "detail": f"{i.started_date:%d %b}"} for i in qs[:DRILLDOWN_LIMIT]]
            total = qs.count()
        elif kind in ("deactivated", "reactivated"):
            qs = _status_changes(requester, scope).filter(
                changed_at__gte=start, changed_at__lte=end, is_active=(kind == "reactivated")
            ).select_related("changed_by").order_by("-changed_at")
            rows = [
                {"ir_id": c.ir_id_text, "ir_name": c.ir_name,
                 "detail": f"{c.changed_at.astimezone(IST):%d %b}"
                           + (f" · by {c.changed_by.ir_name}" if c.changed_by else "")
                           + (f" · {inactive_reason_text(c.reason, c.reason_note)}" if c.reason else "")}
                for c in qs[:DRILLDOWN_LIMIT]
            ]
            total = qs.count()
        elif kind == "removed":
            qs = _removals(requester).filter(reviewed_at__gte=start, reviewed_at__lte=end).order_by("-reviewed_at")
            rows = [
                {"ir_id": r.target_ir_code, "ir_name": r.target_ir_name,
                 "detail": f"{r.reviewed_at.astimezone(IST):%d %b}"}
                for r in qs[:DRILLDOWN_LIMIT]
            ]
            total = qs.count()
        else:  # quiet
            ids = _quiet_ids(end, scope)
            irs = Ir.objects.filter(ir_id__in=ids).order_by("ir_name")
            rows = [{"ir_id": i.ir_id, "ir_name": i.ir_name, "detail": f"joined {i.started_date:%d %b %Y}"}
                    for i in irs[:DRILLDOWN_LIMIT]]
            total = len(ids)

        return Response({"kind": kind, "year": year, "month": month, "total": total, "results": rows})
