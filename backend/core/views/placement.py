"""
The binary placement tree: who sits on whose Left or Right leg.

Separate from the referral tree (Ir.parent_ir), which says who introduced whom
and drives visibility. Nothing here changes who can see whom.

Each manager arranges their own part of the tree, with themselves at the top:

  * Admin : the whole tree, from Admin down.
  * CTC   : the tree under the CTC - their LDCs and those LDCs' people.
  * LDC   : the tree under the LDC - the people in the LDC's teams.

A manager can put a person on a free leg of themselves or of anyone already
below them. The person must be one of the people that manager looks after, and
must be unplaced or already somewhere inside that manager's own tree, so nobody
can pull a person out of a tree somebody else is arranging. Moving a person
takes everyone placed under them along. Players are not placed until promoted.
"""
from django.db import IntegrityError, transaction
from django.db.models import Q
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import AccessLevel, Ir, TeamMember
from core.views.get import get_status_manageable_irs

ARRANGERS = (AccessLevel.ADMIN, AccessLevel.CTC, AccessLevel.LDC)
LEGS = (Ir.LEG_LEFT, Ir.LEG_RIGHT)


def _requester_or_error(request, source):
    requester = Ir.objects.filter(ir_id=source.get("requester_ir_id")).first()
    if requester is None:
        return None, Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)
    if requester.ir_access_level not in ARRANGERS:
        return None, Response({"detail": "Only Admin, CTC and LDC can arrange the tree"},
                              status=status.HTTP_403_FORBIDDEN)
    return requester, None


def _descendant_ids(ir_id):
    """Everyone placed anywhere below ir_id."""
    seen, frontier = set(), {ir_id}
    while frontier:
        kids = set(
            Ir.objects.filter(placement_parent_id__in=frontier).values_list("ir_id", flat=True)
        ) - seen - {ir_id}
        seen |= kids
        frontier = kids
    return seen


def _arrangeable(requester):
    """The people this manager looks after and may therefore place."""
    if requester.ir_access_level == AccessLevel.ADMIN:
        qs = Ir.objects.all()
    else:
        qs = get_status_manageable_irs(requester)
        if requester.ir_access_level == AccessLevel.CTC:
            # LDCs own no downline of their own, their people are their teams.
            ldc_ids = qs.filter(ir_access_level=AccessLevel.LDC).values_list("ir_id", flat=True)
            team_ids = TeamMember.objects.filter(ir_id__in=ldc_ids).values_list("team_id", flat=True)
            member_ids = TeamMember.objects.filter(team_id__in=team_ids).values_list("ir_id", flat=True)
            qs = Ir.objects.filter(Q(ir_id__in=qs.values("ir_id")) | Q(ir_id__in=member_ids))
    excluded = [AccessLevel.ADMIN, AccessLevel.PLAYER]
    if requester.ir_access_level == AccessLevel.LDC:
        excluded.append(AccessLevel.CTC)
    return qs.exclude(ir_access_level__in=excluded).exclude(ir_id=requester.ir_id)


def _node(ir):
    return {
        "ir_id": ir.ir_id,
        "ir_name": ir.ir_name,
        "role": AccessLevel.get_role_name(ir.ir_access_level),
        "active": ir.status,
        "parent_ir_id": ir.placement_parent_id,
        "leg": ir.placement_leg if ir.placement_parent_id else None,
    }


class PlacementTree(APIView):
    """GET /api/placement_tree/?requester_ir_id="""

    def get(self, request):
        requester, err = _requester_or_error(request, request.GET)
        if err:
            return err

        below = _descendant_ids(requester.ir_id)
        nodes = list(Ir.objects.filter(ir_id__in=below).order_by("ir_name"))
        # Not in anyone's tree yet, so free to be placed. A person already
        # placed outside this manager's tree is somebody else's to move.
        unplaced = list(
            _arrangeable(requester).filter(status=True, placement_parent__isnull=True).order_by("ir_name")
        )
        return Response({
            "root": {**_node(requester), "parent_ir_id": None, "leg": None},
            "nodes": [_node(n) for n in nodes],
            "unplaced": [_node(u) for u in unplaced],
            "count": len(nodes),
        })


class PlacePerson(APIView):
    """
    POST /api/placement_tree/place/
    {requester_ir_id, ir_id, parent_ir_id, leg}  - place someone, or move them.
    """

    def post(self, request):
        requester, err = _requester_or_error(request, request.data)
        if err:
            return err
        ir_id, parent_id, leg = (request.data.get(k) for k in ("ir_id", "parent_ir_id", "leg"))
        if leg not in LEGS:
            return Response({"detail": "leg must be L or R"}, status=status.HTTP_400_BAD_REQUEST)
        if not ir_id or not parent_id:
            return Response({"detail": "ir_id and parent_ir_id are required"}, status=status.HTTP_400_BAD_REQUEST)
        if ir_id == parent_id:
            return Response({"detail": "Someone cannot be placed under themselves"}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            if not _arrangeable(requester).filter(ir_id=ir_id).exists():
                return Response({"detail": "You can only place the people you look after"},
                                status=status.HTTP_403_FORBIDDEN)
            person = Ir.objects.select_for_update().get(ir_id=ir_id)

            below = _descendant_ids(requester.ir_id)
            if parent_id != requester.ir_id and parent_id not in below:
                return Response({"detail": "That spot is not in your tree"}, status=status.HTTP_403_FORBIDDEN)
            is_admin = requester.ir_access_level == AccessLevel.ADMIN
            if person.placement_parent_id and person.ir_id not in below and not is_admin:
                return Response({"detail": "This person is already placed in another tree"},
                                status=status.HTTP_403_FORBIDDEN)
            if parent_id in _descendant_ids(person.ir_id):
                return Response({"detail": "Cannot place someone under their own team"},
                                status=status.HTTP_400_BAD_REQUEST)

            parent = Ir.objects.select_for_update().get(ir_id=parent_id)
            taken = Ir.objects.filter(placement_parent=parent, placement_leg=leg).exclude(ir_id=person.ir_id).first()
            if taken:
                return Response(
                    {"detail": f"The {'left' if leg == 'L' else 'right'} spot under {parent.ir_name} is taken by {taken.ir_name}"},
                    status=status.HTTP_409_CONFLICT,
                )

            person.placement_parent, person.placement_leg = parent, leg
            try:
                person.save(update_fields=["placement_parent", "placement_leg"])
            except IntegrityError:
                transaction.set_rollback(True)
                return Response({"detail": "That spot was just taken"}, status=status.HTTP_409_CONFLICT)

        return Response({"message": "Placed", "ir_id": person.ir_id, "parent_ir_id": parent_id, "leg": leg})


class RemoveFromTree(APIView):
    """
    POST /api/placement_tree/remove/  {requester_ir_id, ir_id}
    Takes someone out of the tree. Only when nobody is placed under them, so a
    whole team is never cut loose by accident - move that team first.
    """

    def post(self, request):
        requester, err = _requester_or_error(request, request.data)
        if err:
            return err
        ir_id = request.data.get("ir_id")
        with transaction.atomic():
            person = Ir.objects.select_for_update().filter(ir_id=ir_id).first()
            if person is None or person.ir_id not in _descendant_ids(requester.ir_id):
                return Response({"detail": "That person is not in your tree"}, status=status.HTTP_403_FORBIDDEN)
            if Ir.objects.filter(placement_parent=person).exists():
                return Response({"detail": "Move the people under them first"}, status=status.HTTP_400_BAD_REQUEST)
            person.placement_parent, person.placement_leg = None, None
            person.save(update_fields=["placement_parent", "placement_leg"])
        return Response({"message": "Removed from the tree", "ir_id": ir_id})
