from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from core.utils.dates import get_current_week_start
from django.db.models import F

from core.models import (
    IrId,
    Ir,
    Team,
    TeamMember,
    InfoDetail,
    PlanDetail,
    TeamWeek,
    TeamRole,
)
from core.serializers import (
    IrIdSerializer,
    IrRegisterSerializer,
    TeamSerializer,
    InfoDetailSerializer,
    PlanDetailSerializer,
)

from datetime import datetime, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")


# ---------------------------------------------------
# ADD IR ID
# ---------------------------------------------------
class AddIrId(APIView):
    def post(self, request):
        serializer = IrIdSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------
# REGISTER NEW IR
# ---------------------------------------------------
class RegisterIR(APIView):
    def post(self, request):
        ir_id = request.data.get("ir_id")

        if not IrId.objects.filter(ir_id=ir_id).exists():
            return Response(
                {"detail": "IR ID Not Found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = IrRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(
            {"message": "IR registered successfully", "ir_id": ir_id},
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------
# IR LOGIN
# ---------------------------------------------------
class IRLogin(APIView):
    def post(self, request):
        ir_id = request.data.get("ir_id")
        password = request.data.get("ir_password")

        try:
            ir = Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response({"detail": "IR ID Not Found"}, status=404)

        if not ir.check_password(password):
            return Response({"detail": "Invalid credentials"}, status=401)

        return Response({
            "message": "Login Successful",
            "ir": {
                "ir_id": ir.ir_id,
                "ir_name": ir.ir_name,
                "ir_email": ir.ir_email,
                "ir_access_level": ir.ir_access_level,
            }
        })


# ---------------------------------------------------
# CREATE TEAM
# ---------------------------------------------------
class CreateTeam(APIView):
    def post(self, request):
        serializer = TeamSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        team = serializer.save()

        return Response(
            {"message": "Team created", "team_id": team.id, "team_name": team.name},
            status=201,
        )


# ---------------------------------------------------
# ADD IR TO TEAM
# ---------------------------------------------------
class AddIrToTeam(APIView):
    def post(self, request):
        ir_id = request.data.get("ir_id")
        team_id = request.data.get("team_id")
        role = request.data.get("role")

        ir = get_object_or_404(Ir, ir_id=ir_id)
        team = get_object_or_404(Team, id=team_id)

        if TeamMember.objects.filter(ir=ir, team=team).exists():
            return Response(
                {"detail": "IR already assigned to team"},
                status=status.HTTP_409_CONFLICT,
            )

        TeamMember.objects.create(
            ir=ir,
            team=team,
            role=role,
        )

        return Response(
            {"message": f"{role} assigned to team {team.id}"},
            status=201,
        )


# ---------------------------------------------------
# ADD INFO DETAIL (WITH WEEKLY ROLLOVER)
# ---------------------------------------------------
class AddInfoDetail(APIView):
    def post(self, request, ir_id):
        with transaction.atomic():
            ir = Ir.objects.select_for_update().get(ir_id=ir_id)
            payload = request.data
            created_ids = []

            for item in payload:
                info = InfoDetail.objects.create(
                    ir=ir,
                    info_date=item.get("info_date", timezone.now()),
                    response=item["response"],
                    comments=item.get("comments"),
                    info_name=item["info_name"],
                )
                created_ids.append(info.id)

                # ✅ Atomic IR counter update
                Ir.objects.filter(ir_id=ir_id).update(
                    info_count=F("info_count") + 1
                )

                # Update teams
                links = (
                    TeamMember.objects
                    .select_related("team")
                    .select_for_update()
                    .filter(ir=ir)
                )

                week_start = get_current_week_start()

                for link in links:
                    team = link.team

                    # Archive week if needed
                    if not TeamWeek.objects.filter(
                        team=team,
                        week_start=week_start
                    ).exists():
                        TeamWeek.objects.create(
                            team=team,
                            week_start=week_start,
                            weekly_info_done=team.weekly_info_done,
                            weekly_plan_done=team.weekly_plan_done,
                        )
                        Team.objects.filter(id=team.id).update(
                            weekly_info_done=0,
                            weekly_plan_done=0,
                        )

                    # ✅ Atomic team counter update
                    Team.objects.filter(id=team.id).update(
                        weekly_info_done=F("weekly_info_done") + 1
                    )

        return Response(
            {"message": "Info details added", "info_ids": created_ids},
            status=201,
        )

# ---------------------------------------------------
# ADD PLAN DETAIL
# ---------------------------------------------------
class AddPlanDetail(APIView):
    def post(self, request, ir_id):
        with transaction.atomic():
            ir = Ir.objects.select_for_update().get(ir_id=ir_id)
            payload = request.data
            created_ids = []

            for item in payload:
                plan = PlanDetail.objects.create(
                    ir=ir,
                    plan_date=item.get("plan_date", timezone.now()),
                    plan_name=item.get("plan_name"),
                    comments=item.get("comments"),
                )
                created_ids.append(plan.id)

                # ✅ Atomic IR counter update
                Ir.objects.filter(ir_id=ir_id).update(
                    plan_count=F("plan_count") + 1
                )

                links = (
                    TeamMember.objects
                    .select_related("team")
                    .select_for_update()
                    .filter(ir=ir)
                )

                week_start = get_current_week_start()

                for link in links:
                    team = link.team

                    if not TeamWeek.objects.filter(
                        team=team,
                        week_start=week_start
                    ).exists():
                        TeamWeek.objects.create(
                            team=team,
                            week_start=week_start,
                            weekly_info_done=team.weekly_info_done,
                            weekly_plan_done=team.weekly_plan_done,
                        )
                        Team.objects.filter(id=team.id).update(
                            weekly_info_done=0,
                            weekly_plan_done=0,
                        )

                    # ✅ Atomic team counter update
                    Team.objects.filter(id=team.id).update(
                        weekly_plan_done=F("weekly_plan_done") + 1
                    )

        return Response(
            {"message": "Plan details added", "plan_ids": created_ids},
            status=201,
        )


# ---------------------------------------------------
# SET TARGETS (IR + TEAM)
# ---------------------------------------------------
class SetTargets(APIView):
    def post(self, request):
        acting_ir_id = request.data.get("acting_ir_id")
        payload = request.data

        acting_ir = get_object_or_404(Ir, ir_id=acting_ir_id)

        if acting_ir.ir_access_level not in [1, 2, 3]:
            return Response({"detail": "Not authorized"}, status=403)

        updated = {}

        if payload.get("ir_id"):
            ir = get_object_or_404(Ir, ir_id=payload["ir_id"])
            ir.weekly_info_target = payload.get("weekly_info_target", ir.weekly_info_target)
            ir.weekly_plan_target = payload.get("weekly_plan_target", ir.weekly_plan_target)
            if ir.ir_access_level in [2, 3]:
                ir.weekly_uv_target = payload.get("weekly_uv_target", ir.weekly_uv_target)
            ir.save()
            updated["ir_id"] = ir.ir_id

        if payload.get("team_id"):
            team = get_object_or_404(Team, id=payload["team_id"])
            team.weekly_info_target = payload.get("team_weekly_info_target", team.weekly_info_target)
            team.weekly_plan_target = payload.get("team_weekly_plan_target", team.weekly_plan_target)
            team.save()
            updated["team_id"] = team.id

        return Response({"message": "Targets updated", "updated": updated})


# ---------------------------------------------------
# RESET DATABASE
# ---------------------------------------------------
class ResetDatabase(APIView):
    def post(self, request):
        TeamMember.objects.all().delete()
        InfoDetail.objects.all().delete()
        PlanDetail.objects.all().delete()
        TeamWeek.objects.all().delete()
        Team.objects.all().delete()
        Ir.objects.all().delete()
        IrId.objects.all().delete()

        return Response({"status": "success", "message": "Database reset"})
