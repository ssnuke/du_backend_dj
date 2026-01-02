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
)
from core.serializers import (
    InfoDetailSerializer,
    TeamSerializer,
)

# ---------------------------------------------------
# UPDATE IR DETAILS (PLACEHOLDER – SAME AS FASTAPI)
# ---------------------------------------------------
class UpdateIrDetails(APIView):
    """
    Mirrors FastAPI PUT /{update_ir}
    Currently validates IR ID existence only.
    """
    def put(self, request, update_ir):
        ir_id = get_object_or_404(IrId, ir_id=update_ir)
        return Response(
            {"message": "IR ID exists. Update logic can be added."},
            status=status.HTTP_200_OK
        )


# ---------------------------------------------------
# UPDATE INFO DETAIL
# ---------------------------------------------------
class UpdateInfoDetail(APIView):
    """
    Mirrors PUT /update_info_detail/{info_id}
    """
    def put(self, request, info_id):
        info = get_object_or_404(InfoDetail, id=info_id)

        serializer = InfoDetailSerializer(
            info,
            data=request.data,
            partial=False
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(
            {
                "message": "Info detail updated",
                "info_id": info.id
            },
            status=status.HTTP_200_OK
        )


# ---------------------------------------------------
# SET TARGETS (PUT VERSION – SAME LOGIC AS POST)
# ---------------------------------------------------
class SetTargetsPut(APIView):
    """
    Mirrors PUT /set_targets
    """
    def put(self, request):
        acting_ir_id = request.data.get("acting_ir_id")
        payload = request.data

        acting_ir = get_object_or_404(Ir, ir_id=acting_ir_id)

        if acting_ir.ir_access_level not in [1, 2, 3]:
            return Response(
                {"detail": "Not authorized to set targets"},
                status=status.HTTP_403_FORBIDDEN
            )

        updated = {}

        # Update IR targets
        if payload.get("ir_id"):
            ir = get_object_or_404(Ir, ir_id=payload["ir_id"])

            if payload.get("weekly_info_target") is not None:
                ir.weekly_info_target = payload["weekly_info_target"]

            if payload.get("weekly_plan_target") is not None:
                ir.weekly_plan_target = payload["weekly_plan_target"]

            if payload.get("weekly_uv_target") is not None and ir.ir_access_level in [2, 3]:
                ir.weekly_uv_target = payload["weekly_uv_target"]

            ir.save()
            updated["ir_id"] = ir.ir_id

        # Update Team targets
        if payload.get("team_id"):
            team = get_object_or_404(Team, id=payload["team_id"])

            if payload.get("team_weekly_info_target") is not None:
                team.weekly_info_target = payload["team_weekly_info_target"]

            if payload.get("team_weekly_plan_target") is not None:
                team.weekly_plan_target = payload["team_weekly_plan_target"]

            team.save()
            updated["team_id"] = team.id

        return Response(
            {
                "message": "Targets updated",
                "updated": updated
            },
            status=status.HTTP_200_OK
        )


# ---------------------------------------------------
# UPDATE TEAM NAME (PATCH)
# ---------------------------------------------------
class UpdateTeamName(APIView):
    """
    Mirrors PATCH /update_team_name/{team_id}
    """
    def patch(self, request, team_id):
        team = get_object_or_404(Team, id=team_id)
        old_name = team.name

        new_name = request.data.get("name")
        if not new_name:
            return Response(
                {"detail": "Team name is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        team.name = new_name
        team.save()

        return Response(
            {
                "message": "Team name updated",
                "team_id": team.id,
                "old_name": old_name,
                "new_name": team.name
            },
            status=status.HTTP_200_OK
        )
