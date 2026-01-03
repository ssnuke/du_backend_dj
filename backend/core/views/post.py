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

import logging
from django.db import IntegrityError

from datetime import datetime, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")


# ---------------------------------------------------
# ADD IR ID
# ---------------------------------------------------
class AddIrId(APIView):
    def post(self, request):
        payload = request.data

        if payload is None:
            return Response({"detail": "Empty payload"}, status=status.HTTP_400_BAD_REQUEST)

        items = payload if isinstance(payload, list) else [payload]

        if not items:
            return Response({"detail": "Empty list provided"}, status=status.HTTP_400_BAD_REQUEST)

        errors = []
        seen = set()
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append({"index": idx, "error": "Invalid item format, expected object"})
                continue

            ir_id = item.get("ir_id")
            if not ir_id:
                errors.append({"index": idx, "error": "ir_id missing"})
                continue

            if ir_id in seen:
                errors.append({"index": idx, "ir_id": ir_id, "error": "Duplicate in payload"})
                continue
            seen.add(ir_id)

            if IrId.objects.filter(ir_id=ir_id).exists():
                errors.append({"index": idx, "ir_id": ir_id, "error": "IR ID already exists"})

        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

        serializer = IrIdSerializer(data=items, many=True)
        if not serializer.is_valid():
            return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                created = serializer.save()
        except IntegrityError:
            logging.exception("IntegrityError while bulk adding IrId entries")
            return Response({"detail": "Database integrity error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception:
            logging.exception("Unexpected error while bulk adding IrId entries")
            return Response({"detail": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        created_ids = [obj.ir_id for obj in created]
        return Response({"message": "IrId(s) added", "ir_ids": created_ids}, status=status.HTTP_201_CREATED)


# ---------------------------------------------------
# REGISTER NEW IR
# ---------------------------------------------------
class RegisterIR(APIView):
    def post(self, request):
        payload = request.data

        # Basic payload validation
        if payload is None:
            return Response({"detail": "Empty payload"}, status=status.HTTP_400_BAD_REQUEST)

        items = payload if isinstance(payload, list) else [payload]

        if not items:
            return Response({"detail": "Empty list provided"}, status=status.HTTP_400_BAD_REQUEST)

        for idx, itm in enumerate(items):
            if not isinstance(itm, dict):
                return Response({"detail": "Invalid payload format, expected object or list of objects", "index": idx}, status=status.HTTP_400_BAD_REQUEST)

        # Pre-check whitelist and existing registrations
        errors = []
        for idx, item in enumerate(items):
            ir_id = item.get("ir_id")
            if not ir_id:
                errors.append({"index": idx, "error": "ir_id missing"})
                continue
            if not IrId.objects.filter(ir_id=ir_id).exists():
                errors.append({"index": idx, "ir_id": ir_id, "error": "IR ID Not Found"})
            if Ir.objects.filter(ir_id=ir_id).exists():
                errors.append({"index": idx, "ir_id": ir_id, "error": "Already registered"})

        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

        serializer = IrRegisterSerializer(data=items, many=True)
        # Collect serializer errors without raising to format per-item errors
        if not serializer.is_valid():
            return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                created = serializer.save()
        except IntegrityError as e:
            logging.exception("IntegrityError while bulk registering IRs")
            return Response({"detail": "Database integrity error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            logging.exception("Unexpected error while bulk registering IRs")
            return Response({"detail": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        created_ids = [obj.ir_id for obj in created]

        return Response(
            {"message": "IR(s) registered successfully", "ir_ids": created_ids},
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
    def _process(self, request):
        raw = request.data or {}

        # Support nested payloads: either {"payload": {...}, "acting_ir_id": "..."}
        # or flat payload with keys at top-level.
        payload = raw.get("payload") if isinstance(raw, dict) and isinstance(raw.get("payload"), dict) else raw

        acting_ir_id = raw.get("acting_ir_id") or (payload.get("acting_ir_id") if isinstance(payload, dict) else None)

        if not acting_ir_id:
            return Response({"detail": "acting_ir_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        acting_ir = get_object_or_404(Ir, ir_id=acting_ir_id)

        if acting_ir.ir_access_level not in [1, 2, 3]:
            return Response({"detail": "Not authorized"}, status=status.HTTP_403_FORBIDDEN)

        updated = {}

        # Use atomic transaction for consistency
        try:
            with transaction.atomic():
                # Update IR targets
                if payload.get("ir_id"):
                    ir = get_object_or_404(Ir, ir_id=payload["ir_id"])
                    if payload.get("weekly_info_target") is not None:
                        try:
                            ir.weekly_info_target = int(payload["weekly_info_target"])
                        except Exception:
                            ir.weekly_info_target = payload["weekly_info_target"]
                    if payload.get("weekly_plan_target") is not None:
                        try:
                            ir.weekly_plan_target = int(payload["weekly_plan_target"])
                        except Exception:
                            ir.weekly_plan_target = payload["weekly_plan_target"]
                    if payload.get("weekly_uv_target") is not None and ir.ir_access_level in [2, 3]:
                        try:
                            ir.weekly_uv_target = int(payload["weekly_uv_target"])
                        except Exception:
                            ir.weekly_uv_target = payload["weekly_uv_target"]
                    ir.save()
                    updated["ir_id"] = ir.ir_id

                # Update Team targets
                if payload.get("team_id"):
                    # accept numeric or string team_id
                    team_id_raw = payload.get("team_id")
                    try:
                        team_id_val = int(team_id_raw)
                    except Exception:
                        team_id_val = team_id_raw

                    team = get_object_or_404(Team, id=team_id_val)
                    if payload.get("team_weekly_info_target") is not None:
                        try:
                            team.weekly_info_target = int(payload["team_weekly_info_target"])
                        except Exception:
                            team.weekly_info_target = payload["team_weekly_info_target"]
                    if payload.get("team_weekly_plan_target") is not None:
                        try:
                            team.weekly_plan_target = int(payload["team_weekly_plan_target"])
                        except Exception:
                            team.weekly_plan_target = payload["team_weekly_plan_target"]
                    team.save()
                    updated["team_id"] = team.id

        except Exception as e:
            logging.exception("Error updating targets")
            return Response({"detail": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({"message": "Targets updated", "updated": updated}, status=status.HTTP_200_OK)

    def post(self, request):
        return self._process(request)

    def put(self, request):
        return self._process(request)


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
