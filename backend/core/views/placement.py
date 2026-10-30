"""
The binary placement tree: who sits on whose Left or Right leg.

Separate from the referral tree (Ir.parent_ir), which says who introduced whom
and drives visibility. Nothing here changes who can see whom.

Each manager arranges their own part of the tree, with themselves at the top.
The screen edits a draft and sends the whole arrangement in one save, which the
server checks as a whole and applies atomically:

  * Admin : the whole tree, from Admin down.
  * CTC   : the tree under the CTC - their LDCs and those LDCs' people.
  * LDC   : the tree under the LDC - the people in the LDC's teams.

A manager can put a person on a free leg of themselves or of anyone placed
below them. The person must be one of the people that manager looks after, and
must be unplaced or already inside that manager's own tree, so nobody can pull a
person out of a tree somebody else is arranging. Players are not placed until
promoted.

A save carries the version the draft was started from. If the tree has changed
since (another manager edited it), the save is refused rather than silently
overwriting their work.
"""
import hashlib

from django.db import transaction
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


def _version(nodes):
    """A fingerprint of the tree below a manager, to detect edits made meanwhile."""
    state = sorted(f"{n.ir_id}:{n.placement_parent_id}:{n.placement_leg}" for n in nodes)
    return hashlib.sha1("|".join(state).encode()).hexdigest()


def _tree_payload(requester):
    below = _descendant_ids(requester.ir_id)
    nodes = list(Ir.objects.filter(ir_id__in=below).order_by("ir_name"))
    # Not in anyone's tree yet, so free to be placed. A person already placed
    # outside this manager's tree is somebody else's to move.
    unplaced = _arrangeable(requester).filter(status=True, placement_parent__isnull=True).order_by("ir_name")
    return {
        "root": {**_node(requester), "parent_ir_id": None, "leg": None},
        "nodes": [_node(n) for n in nodes],
        "unplaced": [_node(u) for u in unplaced],
        "count": len(nodes),
        "version": _version(nodes),
    }


class PlacementTree(APIView):
    """GET /api/placement_tree/?requester_ir_id="""

    def get(self, request):
        requester, err = _requester_or_error(request, request.GET)
        if err:
            return err
        return Response(_tree_payload(requester))


class SaveTree(APIView):
    """
    POST /api/placement_tree/save/
    {requester_ir_id, base_version, placements: [{ir_id, parent_ir_id, leg}, ...]}

    `placements` is the complete tree below the requester as they want it. Anyone
    currently below them who is missing from it is taken out of the tree.
    """

    MAX_PLACEMENTS = 5000

    def post(self, request):
        requester, err = _requester_or_error(request, request.data)
        if err:
            return err
        placements = request.data.get("placements")
        if not isinstance(placements, list) or len(placements) > self.MAX_PLACEMENTS:
            return Response({"detail": "placements must be a list"}, status=status.HTTP_400_BAD_REQUEST)

        final = {}
        for item in placements:
            if not isinstance(item, dict):
                return Response({"detail": "Each placement needs ir_id, parent_ir_id and leg"},
                                status=status.HTTP_400_BAD_REQUEST)
            ir_id, parent_id, leg = item.get("ir_id"), item.get("parent_ir_id"), item.get("leg")
            if not ir_id or not parent_id or leg not in LEGS:
                return Response({"detail": "Each placement needs ir_id, parent_ir_id and leg (L or R)"},
                                status=status.HTTP_400_BAD_REQUEST)
            if ir_id in final:
                return Response({"detail": f"{ir_id} appears twice"}, status=status.HTTP_400_BAD_REQUEST)
            if ir_id == requester.ir_id:
                return Response({"detail": "You are the top of your own tree"}, status=status.HTTP_400_BAD_REQUEST)
            final[ir_id] = (parent_id, leg)

        # Shape of the tree as a whole: parents exist in it, one person per
        # leg, and everything leads back up to the requester.
        spots = set()
        for ir_id, (parent_id, leg) in final.items():
            if parent_id != requester.ir_id and parent_id not in final:
                return Response({"detail": "Everyone must sit under you or under someone in the tree"},
                                status=status.HTTP_400_BAD_REQUEST)
            if (parent_id, leg) in spots:
                return Response({"detail": f"Two people are on the same spot under {parent_id}"},
                                status=status.HTTP_400_BAD_REQUEST)
            spots.add((parent_id, leg))
        for ir_id in final:
            seen, cur = set(), ir_id
            while cur != requester.ir_id:
                if cur in seen:
                    return Response({"detail": "Someone cannot be placed under their own team"},
                                    status=status.HTTP_400_BAD_REQUEST)
                seen.add(cur)
                cur = final[cur][0]

        is_admin = requester.ir_access_level == AccessLevel.ADMIN
        with transaction.atomic():
            below = _descendant_ids(requester.ir_id)
            lock_ids = sorted(below | set(final) | {requester.ir_id})
            people = {p.ir_id: p for p in Ir.objects.select_for_update().filter(ir_id__in=lock_ids).order_by("ir_id")}

            # Re-read under the lock, so a concurrent save cannot slip between
            # the version check and the write.
            current = [people[i] for i in below if i in people]
            if request.data.get("base_version") != _version(current):
                return Response(
                    {"detail": "The tree was changed by someone else since you opened it. Discard your draft to load the latest."},
                    status=status.HTTP_409_CONFLICT,
                )

            allowed = set(_arrangeable(requester).filter(ir_id__in=final).values_list("ir_id", flat=True))
            for ir_id in final:
                person = people.get(ir_id)
                if person is None:
                    return Response({"detail": f"{ir_id} was not found"}, status=status.HTTP_400_BAD_REQUEST)
                if ir_id in below:
                    continue  # already in this manager's own tree
                if ir_id not in allowed:
                    return Response({"detail": f"{person.ir_name} is not one of the people you look after"},
                                    status=status.HTTP_403_FORBIDDEN)
                if person.placement_parent_id and not is_admin:
                    return Response({"detail": f"{person.ir_name} is already placed in another tree"},
                                    status=status.HTTP_403_FORBIDDEN)

            changed = [
                i for i in (set(below) | set(final))
                if (people[i].placement_parent_id, people[i].placement_leg) != final.get(i, (None, None))
            ]
            # Clear first, then set: moving A onto the spot B is leaving would
            # otherwise trip the one-person-per-spot rule mid-way.
            Ir.objects.filter(ir_id__in=changed).update(placement_parent=None, placement_leg=None)
            for ir_id in changed:
                if ir_id in final:
                    parent_id, leg = final[ir_id]
                    Ir.objects.filter(ir_id=ir_id).update(placement_parent_id=parent_id, placement_leg=leg)

        return Response(_tree_payload(requester))
