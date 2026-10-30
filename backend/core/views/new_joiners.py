"""
New joiners: how many people registered in each month, for the people who tally them.

  * Admin : everyone who registered, with the month's joiners grouped under the
            LDC whose team they are in, so each LDC's figure can be checked.
  * LDC   : only the people in the teams they belong to.

"Their team" is the one definition used everywhere an LDC's people are listed
(get_status_manageable_irs: members of the teams the LDC belongs to), so the
number an LDC sees here is the same people the IR Status and Documents tabs show
them, and equals their row in the Admin's breakdown.

A joiner who is in several LDCs' teams is counted under each of them, so the LDC
rows can add up to more than the month's total. A joiner in no LDC's team is
listed under "Not in an LDC's team" so the total still tallies. A person's month
is Ir.started_date, and their team is the one they are in today.
"""
from collections import defaultdict
from datetime import datetime

import pytz
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import AccessLevel, Ir, TeamMember
from core.utils.dates import get_calendar_month_bounds
from core.views.get import get_status_manageable_irs

IST = pytz.timezone("Asia/Kolkata")
MAX_MONTHS = 24
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
UNASSIGNED_KEY = "none"


def _recent_months(n):
    """The last n (year, month) pairs in IST, newest first."""
    now = datetime.now(IST)
    y, m = now.year, now.month
    out = []
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def _scope(requester):
    """Everyone whose registration this requester may see counted."""
    if requester.ir_access_level == AccessLevel.ADMIN:
        return Ir.objects.all()
    return get_status_manageable_irs(requester).exclude(
        ir_access_level__in=(AccessLevel.ADMIN, AccessLevel.CTC)
    )


def _person(ir):
    return {
        "ir_id": ir.ir_id,
        "ir_name": ir.ir_name,
        "role": AccessLevel.get_role_name(ir.ir_access_level),
        "registered_on": ir.started_date.isoformat(),
    }


def _by_ldc(joiners):
    """Group a month's joiners under the LDC(s) whose teams they are in."""
    joiner_ids = [ir.ir_id for ir in joiners]
    team_ids_of = defaultdict(set)
    for ir_id, team_id in TeamMember.objects.filter(ir_id__in=joiner_ids).values_list("ir_id", "team_id"):
        team_ids_of[ir_id].add(team_id)

    ldcs_of_team = defaultdict(set)
    ldc_names = {}
    for team_id, ldc_id, ldc_name in TeamMember.objects.filter(
        team_id__in={t for ts in team_ids_of.values() for t in ts},
        ir__ir_access_level=AccessLevel.LDC,
    ).values_list("team_id", "ir_id", "ir__ir_name"):
        ldcs_of_team[team_id].add(ldc_id)
        ldc_names[ldc_id] = ldc_name

    groups = defaultdict(list)
    for ir in joiners:
        counted_under = set()
        # Admin and CTC are nobody's team member as far as an LDC is concerned.
        if ir.ir_access_level not in (AccessLevel.ADMIN, AccessLevel.CTC):
            for team_id in team_ids_of.get(ir.ir_id, ()):
                counted_under |= ldcs_of_team[team_id]
            counted_under.discard(ir.ir_id)  # an LDC is not on their own list
        for key in counted_under or {UNASSIGNED_KEY}:
            groups[key].append(ir)

    rows = [
        {"ldc_ir_id": key, "ldc_name": ldc_names[key], "count": len(irs),
         "people": [_person(i) for i in irs]}
        for key, irs in groups.items() if key != UNASSIGNED_KEY
    ]
    rows.sort(key=lambda r: (-r["count"], r["ldc_name"].lower()))
    if UNASSIGNED_KEY in groups:
        irs = groups[UNASSIGNED_KEY]
        rows.append({"ldc_ir_id": None, "ldc_name": "Not in an LDC's team", "count": len(irs),
                     "people": [_person(i) for i in irs]})
    return rows


class NewJoiners(APIView):
    """GET /api/new_joiners/?requester_ir_id=&months=6&year=&month="""

    def get(self, request):
        requester = Ir.objects.filter(ir_id=request.GET.get("requester_ir_id")).first()
        if requester is None:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)
        if requester.ir_access_level not in (AccessLevel.ADMIN, AccessLevel.LDC):
            return Response({"detail": "Only Admin and LDC can see new joiners"}, status=status.HTTP_403_FORBIDDEN)
        is_admin = requester.ir_access_level == AccessLevel.ADMIN

        try:
            n = min(max(1, int(request.GET.get("months", 6))), MAX_MONTHS)
        except (TypeError, ValueError):
            n = 6
        months = _recent_months(n)

        selected = months[0]
        if request.GET.get("year") or request.GET.get("month"):
            try:
                selected = (int(request.GET["year"]), int(request.GET["month"]))
                if not 1 <= selected[1] <= 12:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                return Response({"detail": "year and month must be given together"}, status=status.HTTP_400_BAD_REQUEST)
            if selected not in months:
                months = sorted(set(months) | {selected}, reverse=True)

        scope = _scope(requester)
        oldest_start = get_calendar_month_bounds(months[-1][1], months[-1][0])[0].date()
        counts = defaultdict(int)
        for started in scope.filter(started_date__gte=oldest_start).values_list("started_date", flat=True):
            counts[(started.year, started.month)] += 1

        sel_start, sel_end = get_calendar_month_bounds(selected[1], selected[0])
        joiners = list(
            scope.filter(started_date__gte=sel_start.date(), started_date__lte=sel_end.date())
            .order_by("-started_date", "ir_name")
        )

        detail = {
            "year": selected[0],
            "month": selected[1],
            "label": f"{MONTH_NAMES[selected[1] - 1]} {selected[0]}",
            "total": len(joiners),
        }
        if is_admin:
            detail["groups"] = _by_ldc(joiners)
        else:
            detail["people"] = [_person(i) for i in joiners]

        return Response({
            "scope": "all" if is_admin else "team",
            "months": [
                {"year": y, "month": m, "label": f"{MONTH_NAMES[m - 1]} {y}", "total": counts[(y, m)]}
                for y, m in months
            ],
            "selected": detail,
        })
