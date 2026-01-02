from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from django.shortcuts import get_object_or_404
from django.db import transaction

from core.models import (
    IrId,
    Ir,
    Team,
    TeamMember,
    InfoDetail,
    PlanDetail,
    TeamWeek,
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
# DELETE TEAM (AND MEMBERS)
# ---------------------------------------------------
class DeleteTeam(APIView):
    """
    Mirrors DELETE /delete_team/{team_id}
    """
    def delete(self, request, team_id):
        team = get_object_or_404(Team, id=team_id)

        with transaction.atomic():
            TeamMember.objects.filter(team=team).delete()
            team.delete()

        return Response(
            {"message": f"Team with ID {team_id} and its members have been deleted"},
            status=status.HTTP_200_OK
        )


# ---------------------------------------------------
# REMOVE IR FROM TEAM
# ---------------------------------------------------
class RemoveIrFromTeam(APIView):
    """
    Mirrors DELETE /remove_ir_from_team/{team_id}/{ir_id}
    """
    def delete(self, request, team_id, ir_id):
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
# DELETE INFO DETAIL
# ---------------------------------------------------
class DeleteInfoDetail(APIView):
    """
    Mirrors DELETE /delete_info_detail/{info_id}
    """
    def delete(self, request, info_id):
        info = get_object_or_404(InfoDetail, id=info_id)

        info.delete()

        return Response(
            {"message": f"Info detail with ID {info_id} has been deleted"},
            status=status.HTTP_200_OK
        )
