from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.db import transaction
import logging

from core.views.get import invalidate_ldc_pocket_dashboard_cache
from core.models import (
    IrId,
    Ir,
    AccessLevel,
    Team,
    TeamMember,
    InfoDetail,
    PlanDetail,
    UVDetail,
    TeamWeek,
)


class DeleteIr(APIView):
    """
    Delete an IR completely from the database. Restricted to ADMIN. All
    other roles must go through the approval flow (core/views/approvals.py:
    RequestDeleteIr + ApproveIrRequest), which calls this same deletion
    logic once an ADMIN approves.
    Requires requester_ir_id (query param).
    Rules:
      - Cannot delete yourself
      - Cannot delete an IR with a higher access level (lower number) than yourself
      - Must be able to view the target IR in your hierarchy
    Children are automatically reconnected to the grandparent (handled by Ir.delete()).
    """
    def delete(self, request, ir_id):
        requester_ir_id = request.query_params.get("requester_ir_id")

        if not requester_ir_id:
            return Response(
                {"detail": "requester_ir_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            requester = Ir.objects.get(ir_id=requester_ir_id)
        except Ir.DoesNotExist:
            return Response({"detail": "Requester IR not found"}, status=status.HTTP_404_NOT_FOUND)

        # ADMIN only — everyone else must request approval instead
        if requester.ir_access_level != AccessLevel.ADMIN:
            return Response(
                {"detail": "Not authorized. Direct deletion requires ADMIN; other roles must request approval."},
                status=status.HTTP_403_FORBIDDEN,
            )

        target_ir = get_object_or_404(Ir, ir_id=ir_id)

        # Cannot delete yourself
        if requester.ir_id == target_ir.ir_id:
            return Response(
                {"detail": "Cannot delete your own account"},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Cannot delete an IR with equal or higher privilege (lower access_level number)
        if target_ir.ir_access_level <= requester.ir_access_level:
            return Response(
                {"detail": "Cannot delete an IR with the same or higher access level"},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Must be able to view the target IR (hierarchy/team membership check)
        if not requester.can_view_ir(target_ir):
            return Response(
                {"detail": "Not authorized to delete this IR"},
                status=status.HTTP_403_FORBIDDEN,
            )

        ir_name = target_ir.ir_name
        target_ir.delete()  # Ir.delete() reconnects children to grandparent automatically

        return Response(
            {"message": f"IR {ir_id} ({ir_name}) deleted successfully"},
            status=status.HTTP_200_OK,
        )

# ---------------------------------------------------
# BULK DELETE IRs (Admin only — rehearsed, tree-verified)
# ---------------------------------------------------
class AdminBulkDeleteIrs(APIView):
    """
    Delete several IRs in one call, with the hierarchy verified. See
    core/utils/ir_deletion.py for exactly what is checked and why.

    POST /api/admin/delete_irs/
    {
      "requester_ir_id": "<an Admin>",
      "irs": [{"ir_id": "IM123456", "ir_name": "Name As In The Database"}, ...],
        // or "text": the pasted table, one "IM123456 Name" per line
      "apply": false,          // false (default) = rehearsal only, nothing deleted
      "confirm_count": 21,     // required with apply=true; must equal the number of IRs
      "allow_leaders": false,  // required to delete an Admin/CTC/LDC
      "quiet": false,          // true = no "IR Deleted" notification to uplines
      "team_creator_to": "IM..."  // optional: hand any team created by a deleted IR
                                  // to this (surviving) IR instead of leaving it creator-less
    }

    Every call REHEARSES first: the real delete runs inside a transaction, the
    tree and everything else is checked, and it is rolled back. With
    apply=true the real delete only happens if that rehearsal is clean, and is
    checked again and rolled back automatically if anything is off.
    """
    def post(self, request):
        from core.utils import ir_deletion as d

        data = request.data if isinstance(request.data, dict) else {}
        requester = Ir.objects.filter(ir_id=data.get("requester_ir_id")).first() if data.get("requester_ir_id") else None
        if not requester or requester.ir_access_level != AccessLevel.ADMIN:
            return Response({"detail": "Not authorized. Admin only."}, status=status.HTTP_403_FORBIDDEN)

        try:
            if data.get("text"):
                targets = d.parse_targets(str(data["text"]))
            else:
                targets, seen = [], set()
                for row in data.get("irs") or []:
                    ir_id = str((row or {}).get("ir_id", "")).strip().upper()
                    name = str((row or {}).get("ir_name", "")).strip()
                    if not ir_id or not name:
                        return Response({"detail": "Every entry needs ir_id and ir_name"}, status=status.HTTP_400_BAD_REQUEST)
                    if ir_id in seen:
                        return Response({"detail": f"{ir_id} appears more than once"}, status=status.HTTP_400_BAD_REQUEST)
                    seen.add(ir_id)
                    targets.append((ir_id, name))
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if not targets:
            return Response({"detail": "No IRs given"}, status=status.HTTP_400_BAD_REQUEST)

        resolved, problems = d.resolve_targets(
            targets, allow_leaders=bool(data.get("allow_leaders")), requester=requester,
        )
        if problems:
            return Response(
                {"detail": "Nothing deleted — fix these first.", "problems": problems},
                status=status.HTTP_400_BAD_REQUEST,
            )

        team_creator_to = (str(data.get("team_creator_to") or "").strip().upper()) or None
        if team_creator_to:
            if team_creator_to in {ir.ir_id for ir in resolved}:
                return Response({"detail": "team_creator_to is one of the IRs being deleted"}, status=status.HTTP_400_BAD_REQUEST)
            if not Ir.objects.filter(ir_id=team_creator_to).exists():
                return Response({"detail": f"team_creator_to {team_creator_to} not found"}, status=status.HTTP_400_BAD_REQUEST)

        apply = bool(data.get("apply"))
        if apply and data.get("confirm_count") != len(resolved):
            return Response(
                {"detail": f"confirm_count must equal {len(resolved)} to apply. Nothing deleted."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        delete_ids = {ir.ir_id for ir in resolved}
        impact_rows = []
        for ir in sorted(resolved, key=lambda i: i.ir_id):
            row = {"ir_id": ir.ir_id, "ir_name": ir.ir_name,
                   "role": AccessLevel.get_role_name(ir.ir_access_level),
                   "status": "active" if ir.status else "inactive",
                   "referrer_ir_id": ir.parent_ir_id}
            row.update(d.impact(ir, delete_ids))
            impact_rows.append(row)

        result = d.run(resolved, apply=apply, quiet=bool(data.get("quiet")), team_creator_to=team_creator_to)
        body = {"mode": result["mode"], "ok": result["ok"], "committed": result["committed"],
                "total": len(resolved), "impact": impact_rows,
                "reparented": result["reparented"],
                "groups_marked_inactive": result["groups_marked_inactive"],
                "teams_losing_creator": result["teams_losing_creator"],
                "checks": result["checks"], "deleted": result["deleted"] if result["committed"] else []}
        if not result["ok"]:
            body["detail"] = "Verification failed — nothing was deleted."
            return Response(body, status=status.HTTP_409_CONFLICT)
        return Response(body)


# ---------------------------------------------------
# RESET DATABASE
# ---------------------------------------------------
class ResetDatabase(APIView):
    """
    Mirrors POST /reset_database (FastAPI)
    Deletes all records from all tables.
    """
    def post(self, request):
        with transaction.atomic():
            TeamMember.objects.all().delete()
            InfoDetail.objects.all().delete()
            PlanDetail.objects.all().delete()
            TeamWeek.objects.all().delete()
            Team.objects.all().delete()
            Ir.objects.all().delete()
            IrId.objects.all().delete()

        return Response(
            {"status": "success", "message": "Database has been reset successfully"},
            status=status.HTTP_200_OK
        )



# ---------------------------------------------------
# DELETE TEAM (AND MEMBERS) (with role-based check)
# ---------------------------------------------------
class DeleteTeam(APIView):
    """
    Mirrors DELETE /delete_team/{team_id}
    """
    def delete(self, request, team_id):
        team = get_object_or_404(Team, id=team_id)
        
        # Role-based check if requester provided
        requester_ir_id = request.query_params.get("requester_ir_id")
        if requester_ir_id:
            try:
                requester = Ir.objects.get(ir_id=requester_ir_id)
                # Requester must be able to edit team
                if not requester.can_edit_team(team):
                    return Response(
                        {"detail": "Not authorized to delete this team"},
                        status=status.HTTP_403_FORBIDDEN
                    )
            except Ir.DoesNotExist:
                return Response(
                    {"detail": "Requester IR not found"},
                    status=status.HTTP_404_NOT_FOUND
                )

        with transaction.atomic():
            TeamMember.objects.filter(team=team).delete()
            team.delete()

        return Response(
            {"message": f"Team with ID {team_id} and its members have been deleted"},
            status=status.HTTP_200_OK
        )


# ---------------------------------------------------
# REMOVE IR FROM TEAM (with role-based check)
# ---------------------------------------------------
class RemoveIrFromTeam(APIView):
    """
    Mirrors DELETE /remove_ir_from_team/{team_id}/{ir_id}
    """
    def delete(self, request, team_id, ir_id):
        team = get_object_or_404(Team, id=team_id)
        
        # Role-based check if requester provided
        requester_ir_id = request.query_params.get("requester_ir_id")
        if requester_ir_id:
            try:
                requester = Ir.objects.get(ir_id=requester_ir_id)
                # Requester must be able to edit team
                if not requester.can_edit_team(team):
                    return Response(
                        {"detail": "Not authorized to modify this team"},
                        status=status.HTTP_403_FORBIDDEN
                    )
            except Ir.DoesNotExist:
                return Response(
                    {"detail": "Requester IR not found"},
                    status=status.HTTP_404_NOT_FOUND
                )
        
        link = TeamMember.objects.filter(
            team_id=team_id,
            ir_id=ir_id
        ).first()

        if not link:
            return Response(
                {"detail": "IR not found in team"},
                status=status.HTTP_404_NOT_FOUND
            )

        link.delete()

        return Response(
            {"message": f"IR '{ir_id}' removed from team {team_id}"},
            status=status.HTTP_200_OK
        )


# ---------------------------------------------------
# DELETE INFO DETAIL (with role-based check)
# ---------------------------------------------------
class DeleteInfoDetail(APIView):
    """
    Mirrors DELETE /delete_info_detail/{info_id}
    """
    def delete(self, request, info_id):
        info = get_object_or_404(InfoDetail, id=info_id)

        # Role-based check if requester provided
        requester_ir_id = request.query_params.get("requester_ir_id")
        if requester_ir_id:
            try:
                requester = Ir.objects.get(ir_id=requester_ir_id)
                # LDC can only delete their own info details
                if requester.ir_access_level == AccessLevel.LDC and info.ir.ir_id != requester.ir_id:
                    return Response(
                        {"detail": "LDC cannot delete info details belonging to other IRs"},
                        status=status.HTTP_403_FORBIDDEN
                    )
                if not requester.can_add_data_for_ir(info.ir):
                    return Response(
                        {"detail": "Not authorized to delete this info detail"},
                        status=status.HTTP_403_FORBIDDEN
                    )
            except Ir.DoesNotExist:
                return Response(
                    {"detail": "Requester IR not found"},
                    status=status.HTTP_404_NOT_FOUND
                )

        deleted_ir, deleted_date = info.ir, info.info_date
        info.delete()
        # See invalidate_ldc_pocket_dashboard_cache's docstring — a deleted
        # info can just as easily leave a stale, too-HIGH count cached.
        invalidate_ldc_pocket_dashboard_cache(deleted_ir, deleted_date)

        return Response(
            {"message": f"Info detail with ID {info_id} has been deleted"},
            status=status.HTTP_200_OK
        )


# ---------------------------------------------------
# DELETE PLAN DETAIL (with role-based check)
# ---------------------------------------------------
class DeletePlanDetail(APIView):
    """
    Mirrors DELETE /delete_plan_detail/{plan_id}
    """
    def delete(self, request, plan_id):
        try:
            plan = get_object_or_404(PlanDetail, id=plan_id)

            # Role-based check if requester provided
            requester_ir_id = request.query_params.get("requester_ir_id")
            if requester_ir_id:
                try:
                    requester = Ir.objects.get(ir_id=requester_ir_id)
                    # LDC can only delete their own plan details
                    if requester.ir_access_level == AccessLevel.LDC and plan.ir.ir_id != requester.ir_id:
                        return Response(
                            {"detail": "LDC cannot delete plan details belonging to other IRs"},
                            status=status.HTTP_403_FORBIDDEN
                        )
                    if not requester.can_add_data_for_ir(plan.ir):
                        return Response(
                            {"detail": "Not authorized to delete this plan detail"},
                            status=status.HTTP_403_FORBIDDEN
                        )
                except Ir.DoesNotExist:
                    return Response(
                        {"detail": "Requester IR not found"},
                        status=status.HTTP_404_NOT_FOUND
                    )
            
            deleted_ir, deleted_date = plan.ir, plan.plan_date
            plan.delete()
            invalidate_ldc_pocket_dashboard_cache(deleted_ir, deleted_date)

            return Response(
                {"message": f"Plan detail with ID {plan_id} has been deleted"},
                status=status.HTTP_200_OK
            )
        except PlanDetail.DoesNotExist:
            return Response({"detail": "Plan detail not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception:
            logging.exception("Error deleting plan detail with id=%s", plan_id)
            return Response({"detail": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------------------------------------------------
# DELETE UV DETAIL RECORD (with hierarchy check)
# ---------------------------------------------------
class DeleteUVDetail(APIView):
    """
    Delete a specific UV detail record by ID.
    Only allowed for LDC and above (access_level <= 3).
    """
    def delete(self, request, uv_id):
        requester_ir_id = request.data.get("requester_ir_id") if request.data else request.GET.get("requester_ir_id")
        
        try:
            # Get the UV detail record
            uv_detail = get_object_or_404(UVDetail, id=uv_id)
            ir = uv_detail.ir
            
            # Check hierarchy permission if requester provided
            if requester_ir_id:
                try:
                    requester = Ir.objects.get(ir_id=requester_ir_id)
                    
                    # Check if requester can view this IR
                    if not requester.can_view_ir(ir):
                        return Response(
                            {"detail": "Not authorized to delete this UV record"},
                            status=status.HTTP_403_FORBIDDEN
                        )
                    
                    # Check if requester is LDC and above (access_level <= 3)
                    if requester.ir_access_level > 3:
                        return Response(
                            {"detail": "Only LDC and above can delete UV records"},
                            status=status.HTTP_403_FORBIDDEN
                        )
                except Ir.DoesNotExist:
                    return Response(
                        {"detail": "Requester IR not found"},
                        status=status.HTTP_404_NOT_FOUND
                    )
            
            # Store details for response before deletion
            uv_id_val = uv_detail.id
            ir_id = uv_detail.ir_id
            ir_name = uv_detail.ir_name
            prospect_name = uv_detail.prospect_name
            
            # Delete the record
            uv_detail.delete()
            
            return Response(
                {
                    "message": "UV record deleted successfully",
                    "id": uv_id_val,
                    "ir_id": ir_id,
                    "ir_name": ir_name,
                    "prospect_name": prospect_name
                },
                status=status.HTTP_200_OK
            )
        
        except UVDetail.DoesNotExist:
            return Response(
                {"detail": "UV record not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception:
            logging.exception("Error deleting UV record id=%s", uv_id)
            return Response(
                {"detail": "Internal server error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
