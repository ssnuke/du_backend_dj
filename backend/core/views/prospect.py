"""
Prospect trail — one prospect's whole history in one place.

Infos and plans are recorded as free text on two unrelated tables
(InfoDetail.info_name, PlanDetail.plan_name); nothing joins them. So the
trail is reconstructed by matching on a normalised name rather than by a
foreign key. That works on every record already in the database, which a new
link table would not without the same name matching to backfill it.

The known weakness is spelling: "Ramesh K" and "Ramesh Kumar" are two
prospects here. Normalisation covers case and stray whitespace only — it
does not guess.
"""
import re

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Ir, InfoDetail, PlanDetail, InfoType

# Enough to answer "who is this", not so many that one loose query pulls the
# whole book of business into memory.
MAX_PROSPECTS = 40
MAX_EVENTS_PER_PROSPECT = 60


def normalise_name(name: str) -> str:
    """Case and whitespace only. Anything cleverer would merge real people."""
    return re.sub(r"\s+", " ", (name or "").strip()).lower()


class GetProspectTrail(APIView):
    """
    GET /api/prospect_trail/<ir_id>/?q=<name>

    Every info and plan recorded against a prospect, merged into one
    chronological trail, within the viewer's own visible scope.
    """

    def get(self, request, ir_id):
        try:
            viewer = Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response({"detail": "IR not found"}, status=status.HTTP_404_NOT_FOUND)

        query = (request.GET.get("q") or "").strip()
        if len(query) < 2:
            # Refusing the empty query is deliberate: without it this returns
            # every prospect the viewer can see, which for an admin is the
            # entire org.
            return Response({"query": query, "prospects": [],
                             "detail": "Type at least 2 characters to search"})

        # Deliberately the same scope as SearchProspects (get_viewable_irs),
        # not the plan-dashboard scope: the two answer the same question from
        # the same box, and a trail that omitted a record the search had just
        # found would read as data going missing.
        #
        # target_ir_id narrows to one person, which is what the search box
        # does when it sits on somebody else's dashboard — without it, opening
        # a colleague's dashboard and searching would return prospects that
        # have nothing to do with them.
        viewable = viewer.get_viewable_irs()
        target_ir_id = (request.GET.get("target_ir_id") or "").strip()
        if target_ir_id:
            viewable = viewable.filter(ir_id=target_ir_id)
        member_ids = list(viewable.values_list("ir_id", flat=True))
        if not member_ids:
            return Response({"query": query, "prospects": []})

        infos = list(
            InfoDetail.objects.filter(ir_id__in=member_ids, info_name__icontains=query)
            .select_related("ir")
            .only("info_name", "info_date", "info_type", "response", "comments",
                  "monitored_by", "ir__ir_name", "ir__ir_id")
            .order_by("info_date")[:1000]
        )
        plans = list(
            PlanDetail.objects.filter(ir_id__in=member_ids, plan_name__icontains=query)
            .select_related("ir", "presented_by")
            .only("plan_name", "plan_date", "status", "plan_mode", "uv_value",
                  "comments", "follow_up_date", "rejection_reason",
                  "ir__ir_name", "ir__ir_id", "presented_by__ir_name")
            .order_by("plan_date")[:1000]
        )

        buckets = {}

        def bucket(raw_name):
            key = normalise_name(raw_name)
            if key not in buckets:
                buckets[key] = {"display_name": (raw_name or "").strip(), "events": []}
            return buckets[key]

        for i in infos:
            is_reinfo = i.info_type == InfoType.REINFO
            bucket(i.info_name)["events"].append({
                "kind": "reinfo" if is_reinfo else "info",
                "date": i.info_date.isoformat() if i.info_date else None,
                "response": i.response,
                "comments": i.comments or "",
                "by_ir_id": i.ir.ir_id if i.ir else None,
                "by_name": i.ir.ir_name if i.ir else "",
                "monitored_by": i.monitored_by or "",
            })

        for p in plans:
            bucket(p.plan_name)["events"].append({
                "kind": "plan",
                "id": p.id,
                "date": p.plan_date.isoformat() if p.plan_date else None,
                # 'fg' is retired; fold it so the trail agrees with every
                # other screen rather than showing a status nothing else uses.
                "status": "closed" if p.status == "fg" else (p.status or "closing_pending"),
                "plan_mode": p.plan_mode,
                "uv_value": str(p.uv_value) if p.uv_value is not None else None,
                "follow_up_date": p.follow_up_date.isoformat() if p.follow_up_date else None,
                "rejection_reason": p.rejection_reason,
                "comments": p.comments or "",
                "by_ir_id": p.ir.ir_id if p.ir else None,
                "by_name": p.ir.ir_name if p.ir else "",
                "presented_by_name": p.presented_by.ir_name if p.presented_by else None,
            })

        out = []
        for key, b in buckets.items():
            events = sorted(b["events"], key=lambda e: (e["date"] or ""))
            counts = {
                "info": sum(1 for e in events if e["kind"] == "info"),
                "reinfo": sum(1 for e in events if e["kind"] == "reinfo"),
                "plan": sum(1 for e in events if e["kind"] == "plan"),
            }
            plan_events = [e for e in events if e["kind"] == "plan"]
            out.append({
                "key": key,
                "name": b["display_name"],
                "counts": counts,
                "first_seen": events[0]["date"] if events else None,
                "last_activity": events[-1]["date"] if events else None,
                # The trail's punchline: where this prospect actually ended up.
                "latest_plan_status": plan_events[-1]["status"] if plan_events else None,
                "events": events[:MAX_EVENTS_PER_PROSPECT],
                "events_truncated": len(events) > MAX_EVENTS_PER_PROSPECT,
            })

        # Most recently active first — that is who you are most likely looking
        # for after typing a partial name.
        out.sort(key=lambda p: p["last_activity"] or "", reverse=True)
        return Response({
            "query": query,
            "prospects": out[:MAX_PROSPECTS],
            "truncated": len(out) > MAX_PROSPECTS,
        })
