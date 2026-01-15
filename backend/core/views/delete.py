from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from core.models import Ir
from django.shortcuts import get_object_or_404
from django.db import transaction
import logging

from core.models import (
    IrId,
    Ir,
    Team,
    TeamMember,
    InfoDetail,
    PlanDetail,
    TeamWeek,
)


class DeleteIr(APIView):
    """
    Delete an IR completely from the database by IR ID.
    """
    def delete(self, request, ir_id):
        ir = get_object_or_404(Ir, ir_id=ir_id)
        ir.delete()
        return Response({"message": f"IR {ir_id} deleted successfully."}, status=status.HTTP_200_OK)

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
                # Requester must be able to add data for this IR (same permissions for delete)
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
                    # Requester must be able to add data for this IR (same permissions for delete)
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
