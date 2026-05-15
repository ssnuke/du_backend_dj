from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.db import transaction
import logging

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
    Delete an IR completely from the database.
    Requires requester_ir_id (query param). Only LDC (3) and above can delete.
    Rules:
      - Cannot delete yourself
      - Cannot delete an IR with a higher access level (lower number) than yourself
      - Must be able to view the target IR in your hierarchy
      - LDC can only delete IRs in their own downline
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

        # Only LDC and above (access_level <= 3)
        if requester.ir_access_level > 3:
            return Response(
                {"detail": "Not authorized. Only LDC and above can delete IRs"},
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

        info.delete()

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
            
            plan.delete()

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
