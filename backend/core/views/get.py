from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from django.db.models import Sum, Count
from django.utils.dateparse import parse_date
from django.shortcuts import get_object_or_404

from core.models import (
    IrId,
    Ir,
    Team,
    TeamMember,
    InfoDetail,
    PlanDetail,
    TeamWeek,
    TeamRole,
    WeeklyTarget,
)
from core.serializers import (
    IrIdSerializer,
    IrSerializer,
    TeamSerializer,
    TeamMemberSerializer,
    InfoDetailSerializer,
    PlanDetailSerializer,
)

from datetime import datetime
import pytz
import logging

from core.utils.dates import get_current_week_start, get_saturday_friday_week_info

IST = pytz.timezone("Asia/Kolkata")


# ---------------------------------------------------
# GET ALL IR IDs
# ---------------------------------------------------
class GetAllIR(APIView):
    def get(self, request):
        irs = IrId.objects.all()
        return Response(IrIdSerializer(irs, many=True).data)


# ---------------------------------------------------
# GET SINGLE IR BY ID
# ---------------------------------------------------
class GetSingleIR(APIView):
    def get(self, request, ir_id):
        ir = get_object_or_404(Ir, ir_id=ir_id)
        return Response(IrSerializer(ir).data)


# ---------------------------------------------------
# GET ALL REGISTERED IRs
# ---------------------------------------------------
class GetAllRegisteredIR(APIView):
    def get(self, request):
        irs = Ir.objects.all()
        data = IrSerializer(irs, many=True).data
        return Response({"data": data, "count": len(data)})


# ---------------------------------------------------
# GET ALL TEAMS (WITH AGGREGATES)
# ---------------------------------------------------
class GetAllTeams(APIView):
    def get(self, request):
        teams = Team.objects.all()
        result = []

        for team in teams:
            members = TeamMember.objects.filter(team=team).select_related("ir")

            info_total = 0
            plan_total = 0
            uv_total = 0

            for member in members:
                ir = member.ir
                info_total += ir.info_count or 0
                plan_total += ir.plan_count or 0
                if ir.ir_access_level in [2, 3]:
                    uv_total += ir.weekly_uv_target or 0

            result.append({
                **TeamSerializer(team).data,
                "weekly_info_achieved": info_total,
                "weekly_plan_achieved": plan_total,
                "weekly_uv_achieved": uv_total,
            })

        return Response(result)


# ---------------------------------------------------
# GET ALL LDCs
# ---------------------------------------------------
class GetLDCs(APIView):
    def get(self, request):
        ldc_ids = TeamMember.objects.filter(
            role=TeamRole.LDC
        ).values_list("ir_id", flat=True).distinct()

        ldcs = Ir.objects.filter(ir_id__in=ldc_ids)

        data = [{"ir_id": i.ir_id, "ir_name": i.ir_name, "id": i.ir_id} for i in ldcs]
        return Response(data)


# ---------------------------------------------------
# GET TEAMS BY LDC
# ---------------------------------------------------
class GetTeamsByLDC(APIView):
    def get(self, request, ldc_id):
        teams = Team.objects.filter(
            teammember__ir_id=ldc_id,
            teammember__role=TeamRole.LDC
        ).distinct()

        return Response(TeamSerializer(teams, many=True).data)


# ---------------------------------------------------
# GET TEAM MEMBERS WITH TARGETS
# ---------------------------------------------------
class GetTeamMembers(APIView):
    def get(self, request, team_id):
        try:
            team = get_object_or_404(Team, id=team_id)

            members = TeamMember.objects.filter(team_id=team_id).select_related("ir")

            role_map = {"LDC": 2, "LS": 3, "GC": 4, "IR": 5}
            result = []

            for member in members:
                ir = member.ir
                result.append({
                    **TeamMemberSerializer(member).data,
                    "ir_name": ir.ir_name,
                    "role_num": role_map.get(member.role),
                    "weekly_info_target": ir.weekly_info_target,
                    "weekly_plan_target": ir.weekly_plan_target,
                    "info_count": ir.info_count,
                    "plan_count": ir.plan_count,
                    "weekly_uv_target": ir.weekly_uv_target if ir.ir_access_level in [2, 3] else None,
                    "uv_count": ir.weekly_uv_target if ir.ir_access_level in [2, 3] else None,
                })

            return Response(result)
        except Team.DoesNotExist:
            return Response({"detail": "Team not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception:
            logging.exception("Error fetching team members for team_id=%s", team_id)
            return Response({"detail": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------------------------------------------------
# GET INFO DETAILS (OPTIONAL DATE FILTER)
# ---------------------------------------------------
class GetInfoDetails(APIView):
    def get(self, request, ir_id):
        from_date = request.GET.get("from_date")
        to_date = request.GET.get("to_date")

        qs = InfoDetail.objects.filter(ir_id=ir_id)

        if from_date:
            qs = qs.filter(info_date__date__gte=parse_date(from_date))
        if to_date:
            qs = qs.filter(info_date__date__lte=parse_date(to_date))

        return Response(InfoDetailSerializer(qs, many=True).data)


# ---------------------------------------------------
# GET PLAN DETAILS (OPTIONAL DATE FILTER)
# ---------------------------------------------------
class GetPlanDetails(APIView):
    def get(self, request, ir_id):
        try:
            from_date = request.GET.get("from_date")
            to_date = request.GET.get("to_date")

            qs = PlanDetail.objects.filter(ir_id=ir_id)

            if from_date:
                qs = qs.filter(plan_date__date__gte=parse_date(from_date))
            if to_date:
                qs = qs.filter(plan_date__date__lte=parse_date(to_date))

            return Response(PlanDetailSerializer(qs, many=True).data)
        except Ir.DoesNotExist:
            return Response({"detail": "IR not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception:
            logging.exception("Error fetching plan details for ir_id=%s", ir_id)
            return Response({"detail": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------------------------------------------------
# DASHBOARD TARGETS
# ---------------------------------------------------
class GetTargetsDashboard(APIView):
    def get(self, request, ir_id):
        ir = get_object_or_404(Ir, ir_id=ir_id)

        # Get current week info (Saturday-Friday cycle)
        week_number, year, week_start, week_end = get_saturday_friday_week_info()
        
        # Get weekly targets for current week
        ir_weekly_target = WeeklyTarget.objects.filter(
            ir=ir,
            week_number=week_number,
            year=year
        ).first()

        personal = {
            "weekly_info_target": ir_weekly_target.ir_weekly_info_target if ir_weekly_target else 0,
            "weekly_plan_target": ir_weekly_target.ir_weekly_plan_target if ir_weekly_target else 0,
            "weekly_uv_target": ir_weekly_target.ir_weekly_uv_target if (ir_weekly_target and ir.ir_access_level in [2, 3]) else None,
            "info_count": ir.info_count,
            "plan_count": ir.plan_count,
            "week_number": week_number,
            "year": year,
            "uv_count": ir.uv_count if ir.ir_access_level in [2, 3] else None,
        }

        if ir.ir_access_level not in [2, 3]:
            return Response({"personal": personal, "teams": "NA"})

        teams_progress = []

        for link in TeamMember.objects.filter(ir=ir):
            team = link.team
            members = Ir.objects.filter(
                teammember__team=team
            ).distinct()

            # Get weekly targets for this team
            team_weekly_target = WeeklyTarget.objects.filter(
                team=team,
                week_number=week_number,
                year=year
            ).first()

            teams_progress.append({
                "team_id": team.id,
                "week_number": week_number,
                "year": year,
                "team_name": team.name,
                "weekly_info_target": team_weekly_target.team_weekly_info_target if team_weekly_target else 0,
                "weekly_plan_target": team_weekly_target.team_weekly_plan_target if team_weekly_target else 0,
                "info_progress": sum(m.info_count or 0 for m in members),
                "plan_progress": sum(m.plan_count or 0 for m in members),
                "uv_progress": sum(
                    m.uv_count or 0 for m in members if m.ir_access_level in [2, 3]
                ),
            })

        return Response({"personal": personal, "teams": teams_progress})


class GetTargets(APIView):
    def get(self, request):
        ir_id = request.GET.get("ir_id")
        team_id = request.GET.get("team_id")

        if not ir_id and not team_id:
            return Response({"detail": "Provide `ir_id` or `team_id` as query parameter"}, status=status.HTTP_400_BAD_REQUEST)

        # Get current week info (Saturday-Friday cycle)
        week_number, year, week_start, week_end = get_saturday_friday_week_info()
        
        data = {
            "week_info": {
                "week_number": week_number,
                "year": year,
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat()
            }
        }
        
        try:
            if ir_id:
                ir = get_object_or_404(Ir, ir_id=ir_id)
                
                # Get weekly targets for current week
                weekly_target = WeeklyTarget.objects.filter(
                    ir=ir,
                    week_number=week_number,
                    year=year
                ).first()
                
                data["ir"] = {
                    "ir_id": ir.ir_id,
                    "ir_name": ir.ir_name,
                    "weekly_info_target": weekly_target.ir_weekly_info_target if weekly_target else 0,
                    "weekly_plan_target": weekly_target.ir_weekly_plan_target if weekly_target else 0,
                    "weekly_uv_target": weekly_target.ir_weekly_uv_target if (weekly_target and ir.ir_access_level in [2, 3]) else None,
                    "has_weekly_targets_set": weekly_target is not None
                }

            if team_id:
                team = get_object_or_404(Team, id=team_id)
                
                # Get weekly targets for current week
                weekly_target = WeeklyTarget.objects.filter(
                    team=team,
                    week_number=week_number,
                    year=year
                ).first()
                
                data["team"] = {
                    "team_id": team.id,
                    "team_name": team.name,
                    "weekly_info_target": weekly_target.team_weekly_info_target if weekly_target else 0,
                    "weekly_plan_target": weekly_target.team_weekly_plan_target if weekly_target else 0,
                    "has_weekly_targets_set": weekly_target is not None
                }

            return Response(data)
        except Exception:
            logging.exception("Error fetching weekly targets for ir_id=%s team_id=%s", ir_id, team_id)
            return Response({"detail": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------------------------------------------------
# GET TEAMS BY IR
# ---------------------------------------------------
class GetTeamsByIR(APIView):
    def get(self, request, ir_id):
        teams = Team.objects.filter(teammember__ir_id=ir_id).distinct()
        return Response(TeamSerializer(teams, many=True).data)


# ---------------------------------------------------
# TEAM INFO TOTAL CHECK
# ---------------------------------------------------
class GetTeamInfoTotal(APIView):
    def get(self, request, team_id):
        team = get_object_or_404(Team, id=team_id)

        links = TeamMember.objects.filter(team=team)
        member_ids = links.values_list("ir_id", flat=True)
        from_date = request.GET.get("from_date")
        to_date = request.GET.get("to_date")

        info_qs = InfoDetail.objects.filter(ir_id__in=member_ids)
        plan_qs = PlanDetail.objects.filter(ir_id__in=member_ids)

        if from_date:
            info_qs = info_qs.filter(info_date__date__gte=parse_date(from_date))
            plan_qs = plan_qs.filter(plan_date__date__gte=parse_date(from_date))
        if to_date:
            info_qs = info_qs.filter(info_date__date__lte=parse_date(to_date))
            plan_qs = plan_qs.filter(plan_date__date__lte=parse_date(to_date))

        members_info_total = info_qs.count()
        members_plan_total = plan_qs.count()

        # per-member breakdown
        info_counts = {i["ir_id"]: i["total"] for i in info_qs.values("ir_id").annotate(total=Count("id"))}
        plan_counts = {p["ir_id"]: p["total"] for p in plan_qs.values("ir_id").annotate(total=Count("id"))}

        # fetch ir names for members
        irs = Ir.objects.filter(ir_id__in=member_ids)
        ir_name_map = {ir.ir_id: ir.ir_name for ir in irs}

        members = []
        for ir_id in member_ids:
            members.append({
                "ir_id": ir_id,
                "ir_name": ir_name_map.get(ir_id),
                "info_total": info_counts.get(ir_id, 0),
                "plan_total": plan_counts.get(ir_id, 0),
            })

        return Response({
            "team_id": team.id,
            "team_name": team.name,
            "running_weekly_info_done": team.weekly_info_done,
            "running_weekly_plan_done": team.weekly_plan_done,
            "members_info_total": members_info_total,
            "members_plan_total": members_plan_total,
            "members": members,
        })


# ---------------------------------------------------
# GET UV COUNT FOR IR
# ---------------------------------------------------
class GetUVCount(APIView):
    def get(self, request, ir_id):
        try:
            ir = get_object_or_404(Ir, ir_id=ir_id)
            
            return Response({
                "ir_id": ir.ir_id,
                "ir_name": ir.ir_name,
                "uv_count": ir.uv_count,
                "weekly_uv_target": ir.weekly_uv_target if ir.ir_access_level in [2, 3] else None,
            })
        except Ir.DoesNotExist:
            return Response({"detail": "IR not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception:
            logging.exception("Error fetching UV count for ir_id=%s", ir_id)
            return Response({"detail": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------------------------------------------------
# GET TEAM UV TOTAL
# ---------------------------------------------------
class GetTeamUVTotal(APIView):
    def get(self, request, team_id):
        try:
            team = get_object_or_404(Team, id=team_id)
            
            links = TeamMember.objects.filter(team=team)
            member_ids = links.values_list("ir_id", flat=True)
            
            # Note: UV counts are stored as counters in Ir model, not as detailed records with dates
            # So date filtering is not applicable for UV counts unlike Info/Plan details
            from_date = request.GET.get("from_date")
            to_date = request.GET.get("to_date")
            
            # Get UV counts for all team members
            irs = Ir.objects.filter(ir_id__in=member_ids)
            
            team_uv_total = 0
            members = []
            
            for ir in irs:
                uv_count = ir.uv_count or 0
                team_uv_total += uv_count
                
                # Get member role
                member_link = links.filter(ir_id=ir.ir_id).first()
                role = member_link.role if member_link else None
                
                members.append({
                    "ir_id": ir.ir_id,
                    "ir_name": ir.ir_name,
                    "uv_count": uv_count,
                    "weekly_uv_target": ir.weekly_uv_target if ir.ir_access_level in [2, 3] else None,
                    "role": role
                })
            
            response_data = {
                "team_id": team.id,
                "team_name": team.name,
                "team_uv_total": team_uv_total,
                "member_count": len(members),
                "members": members,
            }
            
            # Add note about date filtering if dates were provided
            if from_date or to_date:
                response_data["note"] = "Date filtering is not applicable for UV counts as they are stored as counters, not detailed records"
            
            return Response(response_data)
        except Team.DoesNotExist:
            return Response({"detail": "Team not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception:
            logging.exception("Error fetching team UV total for team_id=%s", team_id)
            return Response({"detail": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
