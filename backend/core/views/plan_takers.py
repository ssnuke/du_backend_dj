"""
Who can be recorded as the UL2 / Plan Taker on a plan.

Split out from ChatCandidates, which the picker used to call. That endpoint
answers "who may I message", which is a different question and got this
badly wrong:

  * it excludes the requester, so nobody could record themselves as having
    shown their own plan — the single most common case;
  * get_viewable_irs() returns ONLY SELF for a GC or an IR, so after that
    exclusion a plain IR's picker returned an empty list every time;
  * its role-specific unions (admins, upline contacts) exist for messaging
    reach and have nothing to do with who shows plans.

The right population for a plan taker is: yourself, everyone above you (the
uplines who show plans for you), and everyone you can already see.
"""
from django.db.models import Q
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Ir


class PlanTakerCandidates(APIView):
    """GET /api/plan_taker_candidates/?requester_ir_id=&q=&limit="""

    def get(self, request):
        requester_ir_id = (request.GET.get("requester_ir_id") or "").strip()
        query = (request.GET.get("q") or "").strip()
        try:
            limit = min(max(1, int(request.GET.get("limit", 10))), 50)
        except (TypeError, ValueError):
            limit = 10

        try:
            requester = Ir.objects.get(ir_id=requester_ir_id)
        except Ir.DoesNotExist:
            return Response({"detail": "requester_ir_id is invalid"},
                            status=status.HTTP_400_BAD_REQUEST)

        # Self is included on purpose — showing your own plan is the norm, and
        # excluding it was half of why names went missing.
        viewable_ids = set(requester.get_viewable_irs().values_list("ir_id", flat=True))
        viewable_ids |= set(requester.get_all_uplines().values_list("ir_id", flat=True))
        viewable_ids.add(requester.ir_id)

        qs = Ir.objects.filter(ir_id__in=viewable_ids, status=True)
        if query:
            qs = qs.filter(Q(ir_name__icontains=query) | Q(ir_id__icontains=query))

        # The requester first when they match, since it is the common pick.
        rows = sorted(
            qs.values("ir_id", "ir_name", "ir_access_level")[: limit + 25],
            key=lambda r: (r["ir_id"] != requester.ir_id, (r["ir_name"] or "").lower()),
        )[:limit]

        return Response({"candidates": rows, "count": len(rows)})
