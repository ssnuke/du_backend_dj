"""
Deleting several IRs at once, with the hierarchy ("the tree") checked.

Shared by the Admin API (views/delete.py: AdminBulkDeleteIrs) and the
`delete_irs` management command, so the two can't disagree.

Why this is more than a loop of ir.delete():
  * Ir.delete() re-attaches a deleted IR's direct downlines to its parent and
    rewrites their hierarchy paths. With many IRs going at once — parents and
    their own children on the same list — any slip leaves downlines with stale
    paths, which silently breaks "who can see whom".
  * So every run is REHEARSED first: the real delete runs inside a database
    transaction, the tree and everything else is verified, and the transaction
    is rolled back. Only if the rehearsal is clean does a real run happen, and
    that is verified again and rolled back automatically if anything is off.

What "verified" means (see execute()):
  1. Every surviving IR's parent is exactly the nearest surviving ancestor it
     should have — computed independently, from the tree as it was before.
  2. Every IR's hierarchy_path / hierarchy_level matches its parent's.
     (Problems that already existed beforehand are reported, not blamed.)
  3. Nobody else lost anything: infos, plans, UVs, messages, memberships and
     groups that don't belong to a deleted IR are all still there.
"""
import re

from django.core.management.base import CommandError
from django.db import transaction
from django.db.models import Sum

from core.models import (
    AccessLevel, ChatMessage, ChatRoom, ChatRoomMember, ChatRoomType, InfoDetail,
    Ir, PlanDetail, Pocket, PocketMember, Team, TeamMember, UVDetail,
)

LINE_RE = re.compile(r"^\s*(?:\d+\s+)?(IM\d+)\s+(.+?)\s*$", re.IGNORECASE)


def norm(name):
    return " ".join((name or "").split()).casefold()


def parse_targets(text):
    """[(ir_id, name)], in order. Lines that aren't "IM<digits> <name>" are ignored."""
    targets, seen = [], set()
    for line in text.splitlines():
        m = LINE_RE.match(line)
        if not m:
            continue
        ir_id = m.group(1).upper()
        if ir_id in seen:
            raise CommandError(f"{ir_id} appears more than once in the list")
        seen.add(ir_id)
        targets.append((ir_id, m.group(2).strip()))
    return targets


def resolve_targets(targets, *, allow_leaders=False, requester=None):
    """-> (resolved Ir list, problems list[str]). Any problem should block the run."""
    problems, resolved = [], []
    for ir_id, expected_name in targets:
        ir = Ir.objects.filter(ir_id=ir_id).first()
        if not ir:
            problems.append(f"{ir_id}: not found (already deleted, or a typo)")
            continue
        if norm(ir.ir_name) != norm(expected_name):
            problems.append(f"{ir_id}: name mismatch — list says '{expected_name}', database says '{ir.ir_name}'")
            continue
        if requester is not None and ir.ir_id == requester.ir_id:
            problems.append(f"{ir_id}: cannot delete your own account")
            continue
        if requester is not None and ir.ir_access_level <= requester.ir_access_level:
            problems.append(f"{ir_id} ({ir.ir_name}): same or higher access level than you")
            continue
        if ir.ir_access_level <= AccessLevel.LDC and not allow_leaders:
            problems.append(
                f"{ir_id} ({ir.ir_name}): is {AccessLevel.get_role_name(ir.ir_access_level)} — "
                "refused without allow_leaders"
            )
            continue
        resolved.append(ir)
    return resolved, problems


def impact(ir, delete_ids):
    uv_sum = UVDetail.objects.filter(ir=ir).aggregate(s=Sum("uv_count"))["s"] or 0
    return {
        "infos": InfoDetail.objects.filter(ir=ir).count(),
        "plans": PlanDetail.objects.filter(ir=ir).count(),
        "uv_records": UVDetail.objects.filter(ir=ir).count(),
        "uv_total": float(uv_sum),
        "messages": ir.chat_messages.count(),
        "groups_owned": ir.chat_rooms_created.filter(room_type=ChatRoomType.GROUP).count(),
        "teams_created": Team.objects.filter(created_by=ir).count(),
        "team_memberships": TeamMember.objects.filter(ir=ir).count(),
        "pocket_memberships": PocketMember.objects.filter(ir=ir).count(),
        "pockets_created": Pocket.objects.filter(created_by=ir).count(),
        "downlines_reattached": [
            {"ir_id": i, "ir_name": n}
            for i, n in ir.direct_downlines.exclude(ir_id__in=delete_ids).values_list("ir_id", "ir_name")
        ],
    }


# ── verification ─────────────────────────────────────────────────────────────

def hierarchy_violations():
    """{(ir_id, kind): detail} for every IR whose stored path/level doesn't match its parent's."""
    rows = {
        i.ir_id: (i.parent_ir_id, i.hierarchy_path, i.hierarchy_level)
        for i in Ir.objects.only("ir_id", "parent_ir", "hierarchy_path", "hierarchy_level")
    }
    found = {}
    for ir_id, (parent_id, path, level) in rows.items():
        if parent_id is None:
            exp_path, exp_level = f"/{ir_id}/", 0
        elif parent_id not in rows:
            found[(ir_id, "parent_missing")] = f"parent {parent_id} does not exist"
            continue
        else:
            exp_path, exp_level = f"{rows[parent_id][1]}{ir_id}/", rows[parent_id][2] + 1
        if path != exp_path:
            found[(ir_id, "path")] = f"path {path} should be {exp_path}"
        if level != exp_level:
            found[(ir_id, "level")] = f"level {level} should be {exp_level}"
    return found


def expected_parents(delete_ids):
    """{surviving ir_id: parent after the delete} for every IR whose parent changes —
    the nearest surviving ancestor, worked out from the tree as it is now."""
    parents = dict(Ir.objects.values_list("ir_id", "parent_ir_id"))
    changed = {}
    for ir_id, pid in parents.items():
        if ir_id in delete_ids:
            continue
        p, seen = pid, set()
        while p in delete_ids and p not in seen:
            seen.add(p)
            p = parents.get(p)
        if p != pid:
            changed[ir_id] = p
    return changed, parents


def collateral_snapshot(delete_ids):
    """Counts of everything that does NOT belong to a deleted IR."""
    return {
        "irs_surviving": Ir.objects.exclude(ir_id__in=delete_ids).count(),
        "infos": InfoDetail.objects.exclude(ir_id__in=delete_ids).count(),
        "plans": PlanDetail.objects.exclude(ir_id__in=delete_ids).count(),
        "uv_records": UVDetail.objects.exclude(ir_id__in=delete_ids).count(),
        "chat_messages": ChatMessage.objects.exclude(sender_id__in=delete_ids).count(),
        "team_memberships": TeamMember.objects.exclude(ir_id__in=delete_ids).count(),
        "pocket_memberships": PocketMember.objects.exclude(ir_id__in=delete_ids).count(),
        "teams": Team.objects.count(),
        "pockets": Pocket.objects.count(),
        "chat_rooms": ChatRoom.objects.count(),
    }


# ── execution ────────────────────────────────────────────────────────────────

def _delete_all(resolved, delete_ids):
    """The actual deletes. Caller owns the transaction and notification muting."""
    # A group whose owner is deleted would be left ownerless (created_by is
    # SET_NULL). Archive it instead — readable, not postable. Must happen
    # BEFORE the deletes; afterwards created_by is already NULL.
    orphaned = ChatRoom.objects.filter(
        room_type=ChatRoomType.GROUP, created_by__in=resolved, is_active=True,
    )
    orphaned_rooms = list(orphaned.values("id", "room_name"))
    member_ids = set(
        ChatRoomMember.objects.filter(room_id__in=[r["id"] for r in orphaned_rooms]).values_list("ir_id", flat=True)
    )
    orphaned.update(is_active=False)

    # Deepest first; and each IR is re-fetched just before its delete.
    # Ir.delete() re-attaches children to self.parent_ir — a copy loaded before
    # an earlier delete re-attached this IR would still carry the OLD parent.
    deleted = []
    for stale in sorted(resolved, key=lambda i: i.hierarchy_level, reverse=True):
        Ir.objects.get(ir_id=stale.ir_id).delete()
        deleted.append({"ir_id": stale.ir_id, "ir_name": stale.ir_name})
    return deleted, orphaned_rooms, member_ids


def execute(resolved, *, commit, quiet=False, team_creator_to=None):
    """
    Run the delete inside a transaction and verify it. commit=False is a
    rehearsal: the same delete, the same checks, then rolled back. A commit run
    is also rolled back automatically if any check fails.

    result["ok"] is True only when every check passed; result["committed"] says
    whether the delete was actually kept.
    """
    from core.signals import suppress_notifications
    from core.views.chat import invalidate_chat_rooms_cache

    delete_ids = {ir.ir_id for ir in resolved}
    names = dict(Ir.objects.values_list("ir_id", "ir_name"))
    changed_parents, parents_before = expected_parents(delete_ids)
    expected_after = {i: changed_parents.get(i, p) for i, p in parents_before.items() if i not in delete_ids}
    violations_before = hierarchy_violations()
    snapshot_before = collateral_snapshot(delete_ids)

    # Teams created by a deleted IR survive the delete but lose their creator
    # (SET_NULL), and a CTC sees a team through its creator — so a team can
    # silently drop out of the CTC's view. Report each one and who is left in
    # it; optionally hand them to a named IR instead (team_creator_to).
    teams_affected = []
    for team in Team.objects.filter(created_by__in=resolved).order_by("id"):
        remaining = (
            TeamMember.objects.filter(team=team).exclude(ir_id__in=delete_ids)
            .select_related("ir").order_by("ir__ir_name")
        )
        teams_affected.append({
            "team_id": team.id,
            "team_name": team.name,
            "created_by": team.created_by_id,
            "remaining_members": [{"ir_id": m.ir_id, "ir_name": m.ir.ir_name} for m in remaining],
            "creator_after": team_creator_to,  # None = left with no creator
        })

    # A rehearsal must never push to a real phone.
    muted = ["uv", "plan", "member"] + (["ir"] if (quiet or not commit) else [])
    with suppress_notifications(*muted), transaction.atomic():
        if team_creator_to:
            Team.objects.filter(created_by__in=resolved).update(created_by_id=team_creator_to)
        deleted, orphaned_rooms, member_ids = _delete_all(resolved, delete_ids)

        parents_after = dict(Ir.objects.values_list("ir_id", "parent_ir_id"))
        parent_mismatches = [
            {"ir_id": i, "expected_parent": expected_after.get(i), "actual_parent": parents_after.get(i)}
            for i in sorted(set(expected_after) | set(parents_after))
            if expected_after.get(i, "<gone>") != parents_after.get(i, "<gone>")
        ]
        violations_after = hierarchy_violations()
        new_violations = [
            {"ir_id": k[0], "problem": k[1], "detail": d}
            for k, d in sorted(violations_after.items()) if k not in violations_before
        ]
        snapshot_after = collateral_snapshot(delete_ids)
        collateral_changes = {
            k: {"before": snapshot_before[k], "after": snapshot_after[k]}
            for k in snapshot_before if snapshot_before[k] != snapshot_after[k]
        }
        still_there = sorted(i for i in delete_ids if i in parents_after)

        ok = not (parent_mismatches or new_violations or collateral_changes or still_there)
        if not ok or not commit:
            transaction.set_rollback(True)

    committed = bool(ok and commit)
    if committed:
        invalidate_chat_rooms_cache(member_ids - delete_ids)

    return {
        "ok": ok,
        "committed": committed,
        "deleted": deleted,
        "reparented": [
            {
                "ir_id": i, "ir_name": names.get(i),
                "new_parent_id": p, "new_parent_name": names.get(p) if p else None,
            }
            for i, p in sorted(changed_parents.items())
        ],
        "groups_marked_inactive": orphaned_rooms,
        "teams_losing_creator": teams_affected,
        "checks": {
            "parent_mismatches": parent_mismatches,
            "new_hierarchy_violations": new_violations,
            "preexisting_hierarchy_violations": len(violations_before),
            "collateral_changes": collateral_changes,
            "deleted_ids_still_present": still_there,
        },
    }


def run(resolved, *, apply, quiet=False, team_creator_to=None):
    """
    Rehearse; if (and only if) the rehearsal is clean and apply is set, do it for
    real — verified again, and rolled back automatically if anything is off.
    """
    rehearsal = execute(resolved, commit=False, quiet=True, team_creator_to=team_creator_to)
    if not apply or not rehearsal["ok"]:
        rehearsal["mode"] = "dry_run" if not apply else "refused_rehearsal_failed"
        return rehearsal
    real = execute(resolved, commit=True, quiet=quiet, team_creator_to=team_creator_to)
    real["mode"] = "applied" if real["committed"] else "refused_verification_failed"
    return real
