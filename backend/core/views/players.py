"""
Players awaiting promotion to IR.

A Player is a newly registered member (access level 7) with only Chat, Dreams
and Learn. Someone has to find them to promote them; they belong to no team, so
no team screen will ever list them. This is that list.

Scope matches who may actually promote them (see Ir.can_promote_player):
Admin and CTC see every Player; an LDC sees the Players under them in the
hierarchy plus any they registered themselves.
"""
from django.db.models import Q
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import AccessLevel, Ir, IrPendingRequest, PromotionDocument
from core.promotion_docs import DOCUMENTS

MAX_LIMIT = 50


class PromotablePlayers(APIView):
    """GET /api/promotable_players/?requester_ir_id=&search=&limit=&offset="""

    def get(self, request):
        try:
            requester = Ir.objects.get(ir_id=request.GET.get("requester_ir_id"))
        except Ir.DoesNotExist:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        level = requester.ir_access_level
        if level > AccessLevel.LDC:
            return Response({"detail": "Only LDC and above can promote Players"}, status=status.HTTP_403_FORBIDDEN)

        players = Ir.objects.filter(ir_access_level=AccessLevel.PLAYER, status=True)
        if level == AccessLevel.LDC:
            registered = IrPendingRequest.objects.filter(
                requested_by=requester,
                action_type=IrPendingRequest.ActionType.REGISTER,
                request_status=IrPendingRequest.Status.APPROVED,
            ).values_list("target_ir_code", flat=True)
            players = players.filter(
                Q(ir_id__in=requester.get_subtree_irs().values_list("ir_id", flat=True))
                | Q(ir_id__in=list(registered))
            )

        search = (request.GET.get("search") or "").strip()
        if search:
            players = players.filter(Q(ir_name__icontains=search) | Q(ir_id__icontains=search))

        try:
            limit = min(max(1, int(request.GET.get("limit", 15))), MAX_LIMIT)
            offset = max(0, int(request.GET.get("offset", 0)))
        except (TypeError, ValueError):
            limit, offset = 15, 0

        players = players.select_related("parent_ir").order_by("ir_name")
        total = players.count()
        page = list(players[offset:offset + limit])

        # Which of the three promotion documents each Player has sent, in one query.
        sent = {}
        for ir_id, kind in PromotionDocument.objects.filter(ir__in=page).values_list("ir_id", "kind"):
            sent.setdefault(ir_id, set()).add(kind)
        kinds = [(d["kind"], d["label"]) for d in DOCUMENTS]

        return Response({
            "results": [
                {
                    "ir_id": p.ir_id,
                    "ir_name": p.ir_name,
                    "ir_access_level": p.ir_access_level,
                    "referrer_name": p.parent_ir.ir_name if p.parent_ir_id else None,
                    "documents": [
                        {"kind": k, "label": label, "uploaded": k in sent.get(p.ir_id, set())}
                        for k, label in kinds
                    ],
                    "documents_complete": all(k in sent.get(p.ir_id, set()) for k, _ in kinds),
                }
                for p in page
            ],
            "total": total,
            "has_more": offset + limit < total,
        })
