"""
Pickers for the Info -> Plan -> UV chain.

A prospect is one trail rather than three unrelated rows:

  * "Add Plan" can attach the info the plan grew out of;
  * "Add UV's Fallen" can pick the plan that has converted, instead of typing
    the prospect's name again.

Both pickers are scoped to one IR and to one calendar month, with the list of
months that actually have something to pick returned alongside so the client
can render a month dropdown. A plan shown in week 38 often converts in week 41
(a different month), so the UV picker has to be able to reach back.
"""
from datetime import datetime

import pytz
from django.db.models.functions import TruncMonth
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import InfoDetail, Ir, PlanDetail
from core.utils.dates import get_calendar_month_bounds

IST = pytz.timezone("Asia/Kolkata")

# Plans still worth converting. Rejected is dead, and uvs_on_counter has
# already converted.
PENDING_PLAN_STATUSES = ["closing_pending", "closed", "kiv", "fg"]

MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]


def _authorized_ir(request, ir_id):
    """Returns (ir, error_response). Same gate AddPlanDetail/AddUV apply."""
    try:
        ir = Ir.objects.get(ir_id=ir_id)
    except Ir.DoesNotExist:
        return None, Response({"detail": "IR not found"}, status=status.HTTP_404_NOT_FOUND)

    requester_ir_id = request.GET.get("requester_ir_id")
    if requester_ir_id:
        try:
            requester = Ir.objects.get(ir_id=requester_ir_id)
        except Ir.DoesNotExist:
            return None, Response({"detail": "Requester IR not found"}, status=status.HTTP_404_NOT_FOUND)
        if not requester.can_add_data_for_ir(ir):
            return None, Response({"detail": "Not authorized for this IR"}, status=status.HTTP_403_FORBIDDEN)
    return ir, None


def _month_params(request):
    now = datetime.now(IST)
    try:
        month = int(request.GET.get("month") or now.month)
        year = int(request.GET.get("year") or now.year)
        if not (1 <= month <= 12):
            raise ValueError("month out of range")
    except (TypeError, ValueError):
        return None
    return month, year


def _months_with_rows(qs, date_field):
    """
    Distinct IST calendar months present in qs, newest first, always including
    the current month so the dropdown is never empty on a quiet month.
    """
    now = datetime.now(IST)
    counts = {}
    for row in (
        qs.annotate(m=TruncMonth(date_field, tzinfo=IST))
        .order_by()
        .values_list("m", flat=True)
    ):
        key = (row.year, row.month)
        counts[key] = counts.get(key, 0) + 1
    counts.setdefault((now.year, now.month), 0)
    return [
        {"month": m, "year": y, "label": f"{MONTH_NAMES[m - 1]} {y}", "count": c}
        for (y, m), c in sorted(counts.items(), reverse=True)
    ]


class UVCandidatePlans(APIView):
    """
    GET /api/uv_candidate_plans/<ir_id>/?requester_ir_id=&month=&year=

    The plans an IR has shown that have not converted yet, for one month.
    """

    def get(self, request, ir_id):
        ir, err = _authorized_ir(request, ir_id)
        if err:
            return err
        params = _month_params(request)
        if params is None:
            return Response({"detail": "Invalid month or year"}, status=status.HTTP_400_BAD_REQUEST)
        month, year = params

        pending = PlanDetail.objects.filter(ir=ir, status__in=PENDING_PLAN_STATUSES)
        start, end = get_calendar_month_bounds(month, year)
        rows = (
            pending.filter(plan_date__gte=start, plan_date__lte=end)
            .select_related("info", "presented_by")
            .order_by("-plan_date")
        )

        return Response({
            "month": month,
            "year": year,
            "months": _months_with_rows(pending, "plan_date"),
            "plans": [
                {
                    "id": p.id,
                    "plan_name": p.plan_name or "",
                    "plan_date": p.plan_date.isoformat(),
                    "status": p.status or "closing_pending",
                    "uv_value": str(p.uv_value) if p.uv_value is not None else None,
                    "presented_by_name": p.presented_by.ir_name if p.presented_by else None,
                    "info_name": p.info.info_name if p.info_id else None,
                    "info_date": p.info.info_date.isoformat() if p.info_id else None,
                    "location": p.location or "",
                }
                for p in rows
            ],
        })


class PlanCandidateInfos(APIView):
    """
    GET /api/plan_candidate_infos/<ir_id>/?requester_ir_id=&month=&year=

    The infos an IR has logged that no plan has been attached to yet, for one
    month. An info that already has a plan is left out so one lead does not
    quietly fork into several trails.
    """

    def get(self, request, ir_id):
        ir, err = _authorized_ir(request, ir_id)
        if err:
            return err
        params = _month_params(request)
        if params is None:
            return Response({"detail": "Invalid month or year"}, status=status.HTTP_400_BAD_REQUEST)
        month, year = params

        open_infos = InfoDetail.objects.filter(ir=ir, plans__isnull=True)
        start, end = get_calendar_month_bounds(month, year)
        rows = open_infos.filter(info_date__gte=start, info_date__lte=end).order_by("-info_date")

        return Response({
            "month": month,
            "year": year,
            "months": _months_with_rows(open_infos, "info_date"),
            "infos": [
                {
                    "id": i.id,
                    "info_name": i.info_name,
                    "info_date": i.info_date.isoformat(),
                    "response": i.response,
                    "info_type": i.info_type,
                    "location": i.location,
                }
                for i in rows
            ],
        })
