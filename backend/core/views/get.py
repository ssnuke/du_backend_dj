from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from django.db.models import Sum, Count, Q
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
    AccessLevel,
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

from core.utils.dates import get_current_week_start, get_saturday_friday_week_info, get_week_info_friday_to_friday

IST = pytz.timezone("Asia/Kolkata")


# ===================================================
# HELPER: Get viewable teams for an IR (role-based)
# ===================================================
def get_viewable_teams_for_ir(ir):
    """
    Get all teams visible to an IR based on role.
    Uses the role-based get_teams_can_view() method.
    """
    return ir.get_teams_can_view()


# ---------------------------------------------------
# GET ALL IR IDs
# ---------------------------------------------------
class GetAllIR(APIView):
    def get(self, request):
        irs = IrId.objects.all()
        return Response(IrIdSerializer(irs, many=True).data)


# ---------------------------------------------------
# GET SINGLE IR BY ID (with role-based check)
# ---------------------------------------------------
class GetSingleIR(APIView):
    def get(self, request, fetch_ir_id):
        requester_ir_id = request.GET.get("requester_ir_id")
        
        ir = get_object_or_404(Ir, ir_id=fetch_ir_id)
        
        # If requester_ir_id provided, check role-based permission
        if requester_ir_id:
            try:
                requester = Ir.objects.get(ir_id=requester_ir_id)
                if not requester.can_view_ir(ir):
                    return Response(
                        {"detail": "Not authorized to view this IR"},
                        status=status.HTTP_403_FORBIDDEN
                    )
            except Ir.DoesNotExist:
                return Response(
                    {"detail": "Requester IR not found"},
                    status=status.HTTP_404_NOT_FOUND
                )
        
        data = IrSerializer(ir).data
        # Add hierarchy info
        data["hierarchy_level"] = ir.hierarchy_level
        data["parent_ir_id"] = ir.parent_ir.ir_id if ir.parent_ir else None
        
        return Response(data)


# ---------------------------------------------------
# GET ALL REGISTERED IRs (with role-based filter)
# ---------------------------------------------------
class GetAllRegisteredIR(APIView):
    def get(self, request):
        requester_ir_id = request.GET.get("requester_ir_id")
        
        if requester_ir_id:
            try:
                requester = Ir.objects.get(ir_id=requester_ir_id)
                # Use role-based viewable IRs
                irs = requester.get_viewable_irs()
            except Ir.DoesNotExist:
                return Response(
                    {"detail": "Requester IR not found"},
                    status=status.HTTP_404_NOT_FOUND
                )
        else:
            # No filter - return all (backward compatible)
            irs = Ir.objects.all()
        
        data = IrSerializer(irs, many=True).data
        
        # Add hierarchy info to each IR
        ir_map = {ir.ir_id: ir for ir in irs}
        for item in data:
            ir = ir_map.get(item['ir_id'])
            if ir:
                item["hierarchy_level"] = ir.hierarchy_level
                item["parent_ir_id"] = ir.parent_ir.ir_id if ir.parent_ir else None
        
        return Response({"data": data, "count": len(data)})


# ---------------------------------------------------
# GET ALL TEAMS (WITH AGGREGATES & ROLE-BASED FILTER)
# ---------------------------------------------------
class GetAllTeams(APIView):
    def get(self, request):
        requester_ir_id = request.GET.get("requester_ir_id")
        
        if requester_ir_id:
            try:
                requester = Ir.objects.get(ir_id=requester_ir_id)
                teams = requester.get_teams_can_view()
            except Ir.DoesNotExist:
                return Response(
                    {"detail": "Requester IR not found"},
                    status=status.HTTP_404_NOT_FOUND
                )
        else:
            # No filter - return all (backward compatible)
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
                "created_by_id": team.created_by.ir_id if team.created_by else None,
                "created_by_name": team.created_by.ir_name if team.created_by else None,
                "weekly_info_achieved": info_total,
                "weekly_plan_achieved": plan_total,
                "weekly_uv_achieved": uv_total,
            })

        return Response(result)


# ---------------------------------------------------
# GET ALL LDCs (with hierarchy filter)
# ---------------------------------------------------
class GetLDCs(APIView):
    def get(self, request):
        requester_ir_id = request.GET.get("requester_ir_id")
        
        ldc_ids = TeamMember.objects.filter(
            role=TeamRole.LDC
        ).values_list("ir_id", flat=True).distinct()

        ldcs = Ir.objects.filter(ir_id__in=ldc_ids)
        
        # Filter by hierarchy if requester provided
        if requester_ir_id:
            try:
                requester = Ir.objects.get(ir_id=requester_ir_id)
                viewable_irs = requester.get_viewable_irs()
                ldcs = ldcs.filter(ir_id__in=viewable_irs.values_list('ir_id', flat=True))
            except Ir.DoesNotExist:
                return Response(
                    {"detail": "Requester IR not found"},
                    status=status.HTTP_404_NOT_FOUND
                )

        data = [{"ir_id": i.ir_id, "ir_name": i.ir_name, "id": i.ir_id, "ir_access_level": i.ir_access_level} for i in ldcs]
        return Response(data)


# ---------------------------------------------------
# GET TEAMS BY LDC (with hierarchy check)
# ---------------------------------------------------
class GetTeamsByLDC(APIView):
    def get(self, request, ldc_id):
        requester_ir_id = request.GET.get("requester_ir_id")
        
        # Get the LDC
        try:
            ldc = Ir.objects.get(ir_id=ldc_id)
        except Ir.DoesNotExist:
            return Response(
                {"detail": "LDC not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # If requester provided, verify they can view this LDC
        if requester_ir_id:
            try:
                requester = Ir.objects.get(ir_id=requester_ir_id)
                if not requester.can_view_ir(ldc):
                    return Response(
                        {"detail": "Not authorized to view this LDC's teams"},
                        status=status.HTTP_403_FORBIDDEN
                    )
            except Ir.DoesNotExist:
                return Response(
                    {"detail": "Requester IR not found"},
                    status=status.HTTP_404_NOT_FOUND
                )
        
        # Return teams the LDC can view (created by them, in their subtree, OR where they are a member)
        teams = ldc.get_teams_can_view()

        return Response(TeamSerializer(teams, many=True).data)


# ---------------------------------------------------
# GET TEAM MEMBERS WITH TARGETS (with role-based check)
# ---------------------------------------------------
class GetTeamMembers(APIView):
    def get(self, request, team_id):
        requester_ir_id = request.GET.get("requester_ir_id")
        week_param = request.GET.get("week")
        year_param = request.GET.get("year")
        
        try:
            team = get_object_or_404(Team, id=team_id)
            
            # If requester provided, verify they can view this team
            if requester_ir_id:
                try:
                    requester = Ir.objects.get(ir_id=requester_ir_id)
                    if not requester.can_view_team(team):
                        return Response(
                            {"detail": "Not authorized to view this team"},
                            status=status.HTTP_403_FORBIDDEN
                        )
                except Ir.DoesNotExist:
                    return Response(
                        {"detail": "Requester IR not found"},
                        status=status.HTTP_404_NOT_FOUND
                    )

            # Calculate week info using Friday 9:31 PM IST to Friday 9:30 PM IST
            if week_param and year_param:
                try:
                    week_number = int(week_param)
                    year = int(year_param)
                    # Validate week number
                    if week_number < 1 or week_number > 52:
                        return Response(
                            {"detail": "Week number must be between 1 and 52"},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    week_number, year, week_start, week_end = get_week_info_friday_to_friday(
                        week_number=week_number, year=year
                    )
                except ValueError:
                    return Response(
                        {"detail": "Invalid week or year parameter"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            else:
                # Get current week
                week_number, year, week_start, week_end = get_week_info_friday_to_friday()

            members = TeamMember.objects.filter(team_id=team_id).select_related("ir")

            # Map team roles to access level numbers (matching AccessLevel class)
            # Admin=1, CTC=2, LDC=3, LS=4, GC=5, IR=6
            role_map = {"ADMIN": 1, "CTC": 2, "LDC": 3, "LS": 4, "GC": 5, "IR": 6}
            result = []

            for member in members:
                ir = member.ir
                
                # Calculate info and plan counts for the selected week
                info_count_week = InfoDetail.objects.filter(
                    ir_id=ir.ir_id,
                    info_date__gte=week_start,
                    info_date__lte=week_end
                ).count()
                
                plan_count_week = PlanDetail.objects.filter(
                    ir_id=ir.ir_id,
                    plan_date__gte=week_start,
                    plan_date__lte=week_end
                ).count()
                
                # Get weekly targets for the selected week
                ir_target = WeeklyTarget.objects.filter(
                    ir=ir, week_number=week_number, year=year
                ).first()
                
                result.append({
                    **TeamMemberSerializer(member).data,
                    "ir_name": ir.ir_name,
                    "role_num": role_map.get(member.role, 6),  # Team role (deprecated, use ir_access_level)
                    "ir_access_level": ir.ir_access_level,  # Actual access level from IR model
                    "weekly_info_target": ir_target.ir_weekly_info_target if ir_target else ir.weekly_info_target,
                    "weekly_plan_target": ir_target.ir_weekly_plan_target if ir_target else ir.weekly_plan_target,
                    "info_count": info_count_week,
                    "plan_count": plan_count_week,
                    "weekly_uv_target": (ir_target.ir_weekly_uv_target if ir_target else ir.weekly_uv_target) if ir.ir_access_level in [2, 3] else None,
                    "uv_count": ir.uv_count if ir.ir_access_level in [2, 3] else None,
                    "week_number": week_number,
                    "year": year,
                })

            return Response(result)
        except Team.DoesNotExist:
            return Response({"detail": "Team not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception:
            logging.exception("Error fetching team members for team_id=%s", team_id)
            return Response({"detail": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------------------------------------------------
# GET INFO DETAILS (OPTIONAL DATE FILTER + hierarchy check)
# ---------------------------------------------------
class GetInfoDetails(APIView):
    def get(self, request, ir_id):
        requester_ir_id = request.GET.get("requester_ir_id")
        
        # If requester provided, verify they can view this IR
        if requester_ir_id:
            try:
                requester = Ir.objects.get(ir_id=requester_ir_id)
                target_ir = Ir.objects.get(ir_id=ir_id)
                if not requester.can_view_ir(target_ir):
                    return Response(
                        {"detail": "Not authorized to view this IR's info details"},
                        status=status.HTTP_403_FORBIDDEN
                    )
            except Ir.DoesNotExist:
                return Response(
                    {"detail": "IR not found"},
                    status=status.HTTP_404_NOT_FOUND
                )
        
        # Check for week/year parameters first
        week_param = request.GET.get("week")
        year_param = request.GET.get("year")
        
        if week_param and year_param:
            try:
                from core.utils.dates import get_week_info_friday_to_friday
                ist = pytz.timezone('Asia/Kolkata')
                now = datetime.now(ist)
                week_info = get_week_info_friday_to_friday(now, int(week_param), int(year_param))
                from_date = week_info['week_start'].strftime('%Y-%m-%d')
                to_date = week_info['week_end'].strftime('%Y-%m-%d')
            except Exception as e:
                logging.exception("Error processing week parameters for ir_id=%s", ir_id)
                return Response({"detail": f"Error processing week parameters: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)
        else:
            from_date = request.GET.get("from_date")
            to_date = request.GET.get("to_date")
        
        response_filter = request.GET.get("response")

        qs = InfoDetail.objects.filter(ir_id=ir_id)

        if from_date:
            qs = qs.filter(info_date__date__gte=parse_date(from_date))
        if to_date:
            qs = qs.filter(info_date__date__lte=parse_date(to_date))
        if response_filter:
            qs = qs.filter(response=response_filter)

        return Response(InfoDetailSerializer(qs, many=True).data)


# ---------------------------------------------------
# GET PLAN DETAILS (OPTIONAL DATE FILTER + hierarchy check)
# ---------------------------------------------------
class GetPlanDetails(APIView):
    def get(self, request, ir_id):
        requester_ir_id = request.GET.get("requester_ir_id")
        
        # If requester provided, verify they can view this IR
        if requester_ir_id:
            try:
                requester = Ir.objects.get(ir_id=requester_ir_id)
                target_ir = Ir.objects.get(ir_id=ir_id)
                if not requester.can_view_ir(target_ir):
                    return Response(
                        {"detail": "Not authorized to view this IR's plan details"},
                        status=status.HTTP_403_FORBIDDEN
                    )
            except Ir.DoesNotExist:
                return Response(
                    {"detail": "IR not found"},
                    status=status.HTTP_404_NOT_FOUND
                )
        
        try:
            # Check for week/year parameters first
            week_param = request.GET.get("week")
            year_param = request.GET.get("year")
            
            if week_param and year_param:
                try:
                    from core.utils.dates import get_week_info_friday_to_friday
                    ist = pytz.timezone('Asia/Kolkata')
                    now = datetime.now(ist)
                    week_info = get_week_info_friday_to_friday(now, int(week_param), int(year_param))
                    from_date = week_info['week_start'].strftime('%Y-%m-%d')
                    to_date = week_info['week_end'].strftime('%Y-%m-%d')
                except Exception as e:
                    logging.exception("Error processing week parameters for ir_id=%s", ir_id)
                    return Response({"detail": f"Error processing week parameters: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)
            else:
                from_date = request.GET.get("from_date")
                to_date = request.GET.get("to_date")
            
            status_filter = request.GET.get("status")

            qs = PlanDetail.objects.filter(ir_id=ir_id)

            if from_date:
                qs = qs.filter(plan_date__date__gte=parse_date(from_date))
            if to_date:
                qs = qs.filter(plan_date__date__lte=parse_date(to_date))
            if status_filter:
                qs = qs.filter(status=status_filter)

            return Response(PlanDetailSerializer(qs, many=True).data)
        except Ir.DoesNotExist:
            return Response({"detail": "IR not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception:
            logging.exception("Error fetching plan details for ir_id=%s", ir_id)
            return Response({"detail": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------------------------------------------------
# DASHBOARD TARGETS (with hierarchy-based team filtering)
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

        # Calculate current week's info and plan counts
        current_week_info_count = InfoDetail.objects.filter(
            ir_id=ir.ir_id,
            info_date__date__gte=week_start,
            info_date__date__lte=week_end
        ).count()
        
        current_week_plan_count = PlanDetail.objects.filter(
            ir_id=ir.ir_id,
            plan_date__date__gte=week_start,
            plan_date__date__lte=week_end
        ).count()

        personal = {
            "weekly_info_target": ir_weekly_target.ir_weekly_info_target if ir_weekly_target else 0,
            "weekly_plan_target": ir_weekly_target.ir_weekly_plan_target if ir_weekly_target else 0,
            "info_count": current_week_info_count,
            "plan_count": current_week_plan_count,
            "week_number": week_number,
            "year": year,
            "uv_count": ir.uv_count if ir.ir_access_level in [2, 3] else None,
            "hierarchy_level": ir.hierarchy_level,
            "parent_ir_id": ir.parent_ir.ir_id if ir.parent_ir else None,
        }

        if ir.ir_access_level not in [2, 3]:
            return Response({"personal": personal, "teams": "NA"})

        # Get teams visible to this IR (hierarchy-based)
        viewable_teams = get_viewable_teams_for_ir(ir)
        
        teams_progress = []

        for team in viewable_teams:
            members = Ir.objects.filter(
                teammember__team=team
            ).distinct()
            
            member_ids = members.values_list('ir_id', flat=True)

            # Get weekly targets for this team
            team_weekly_target = WeeklyTarget.objects.filter(
                team=team,
                week_number=week_number,
                year=year
            ).first()

            # Calculate current week's progress for all team members
            team_info_progress = InfoDetail.objects.filter(
                ir_id__in=member_ids,
                info_date__date__gte=week_start,
                info_date__date__lte=week_end
            ).count()
            
            team_plan_progress = PlanDetail.objects.filter(
                ir_id__in=member_ids,
                plan_date__date__gte=week_start,
                plan_date__date__lte=week_end
            ).count()
            
            team_uv_progress = sum(m.uv_count or 0 for m in members)

            # Check if requester can edit this team (created by someone in their subtree)
            can_edit = False
            if team.created_by:
                can_edit = ir.can_view_ir(team.created_by)

            teams_progress.append({
                "team_id": team.id,
                "week_number": week_number,
                "year": year,
                "team_name": team.name,
                "created_by_id": team.created_by.ir_id if team.created_by else None,
                "created_by_name": team.created_by.ir_name if team.created_by else None,
                "can_edit": can_edit,
                "weekly_info_target": team_weekly_target.team_weekly_info_target if team_weekly_target else 0,
                "weekly_plan_target": team_weekly_target.team_weekly_plan_target if team_weekly_target else 0,
                "weekly_uv_target": team_weekly_target.team_weekly_uv_target if team_weekly_target else 0,
                "info_progress": team_info_progress,
                "plan_progress": team_plan_progress,
                "uv_progress": team_uv_progress,
            })

        return Response({"personal": personal, "teams": teams_progress})


class GetTargets(APIView):
    def get(self, request):
        ir_id = request.GET.get("ir_id")
        team_id = request.GET.get("team_id")
        requester_ir_id = request.GET.get("requester_ir_id")

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
            # Get requester for permission checks
            requester = None
            if requester_ir_id:
                try:
                    requester = Ir.objects.get(ir_id=requester_ir_id)
                except Ir.DoesNotExist:
                    return Response(
                        {"detail": "Requester IR not found"},
                        status=status.HTTP_404_NOT_FOUND
                    )
            
            if ir_id:
                ir = get_object_or_404(Ir, ir_id=ir_id)
                
                # Check hierarchy permission if requester provided
                if requester and not requester.can_view_ir(ir):
                    return Response(
                        {"detail": "Not authorized to view this IR's targets"},
                        status=status.HTTP_403_FORBIDDEN
                    )
                
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
                
                # Check hierarchy permission if requester provided
                if requester:
                    viewable_teams = get_viewable_teams_for_ir(requester)
                    if team not in viewable_teams:
                        return Response(
                            {"detail": "Not authorized to view this team's targets"},
                            status=status.HTTP_403_FORBIDDEN
                        )
                
                # Get weekly targets for current week
                weekly_target = WeeklyTarget.objects.filter(
                    team=team,
                    week_number=week_number,
                    year=year
                ).first()
                
                data["team"] = {
                    "team_id": team.id,
                    "team_name": team.name,
                    "created_by_id": team.created_by.ir_id if team.created_by else None,
                    "weekly_info_target": weekly_target.team_weekly_info_target if weekly_target else 0,
                    "weekly_plan_target": weekly_target.team_weekly_plan_target if weekly_target else 0,
                    "has_weekly_targets_set": weekly_target is not None
                }

            return Response(data)
        except Exception:
            logging.exception("Error fetching weekly targets for ir_id=%s team_id=%s", ir_id, team_id)
            return Response({"detail": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------------------------------------------------
# GET TEAMS BY IR (with hierarchy check)
# ---------------------------------------------------
class GetTeamsByIR(APIView):
    def get(self, request, ir_id):
        requester_ir_id = request.GET.get("requester_ir_id")
        
        # If requester provided, verify they can view this IR
        if requester_ir_id:
            try:
                requester = Ir.objects.get(ir_id=requester_ir_id)
                target_ir = Ir.objects.get(ir_id=ir_id)
                if not requester.can_view_ir(target_ir):
                    return Response(
                        {"detail": "Not authorized to view this IR's teams"},
                        status=status.HTTP_403_FORBIDDEN
                    )
            except Ir.DoesNotExist:
                return Response(
                    {"detail": "IR not found"},
                    status=status.HTTP_404_NOT_FOUND
                )
        
        teams = Team.objects.filter(teammember__ir_id=ir_id).distinct()
        
        result = []
        for team in teams:
            result.append({
                **TeamSerializer(team).data,
                "created_by_id": team.created_by.ir_id if team.created_by else None,
                "created_by_name": team.created_by.ir_name if team.created_by else None,
            })
        
        return Response(result)


# ---------------------------------------------------
# TEAM INFO TOTAL CHECK (with hierarchy check)
# ---------------------------------------------------
class GetTeamInfoTotal(APIView):
    def get(self, request, team_id):
        requester_ir_id = request.GET.get("requester_ir_id")
        
        team = get_object_or_404(Team, id=team_id)
        
        # Check hierarchy permission if requester provided
        if requester_ir_id:
            try:
                requester = Ir.objects.get(ir_id=requester_ir_id)
                viewable_teams = get_viewable_teams_for_ir(requester)
                if team not in viewable_teams:
                    return Response(
                        {"detail": "Not authorized to view this team"},
                        status=status.HTTP_403_FORBIDDEN
                    )
            except Ir.DoesNotExist:
                return Response(
                    {"detail": "Requester IR not found"},
                    status=status.HTTP_404_NOT_FOUND
                )

        # Get current week info (Saturday-Friday cycle)
        week_number, year, week_start, week_end = get_saturday_friday_week_info()

        links = TeamMember.objects.filter(team=team)
        member_ids = links.values_list("ir_id", flat=True)
        from_date = request.GET.get("from_date")
        to_date = request.GET.get("to_date")

        # Default to current week if no date filters provided
        if not from_date:
            from_date = week_start.strftime("%Y-%m-%d")
        if not to_date:
            to_date = week_end.strftime("%Y-%m-%d")

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

        # fetch ir data for members (including UV counts)
        irs = Ir.objects.filter(ir_id__in=member_ids)
        ir_data_map = {ir.ir_id: ir for ir in irs}

        members = []
        total_uv_count = 0
        
        for ir_id in member_ids:
            ir = ir_data_map.get(ir_id)
            uv_count = ir.uv_count or 0 if ir else 0
            total_uv_count += uv_count
            
            members.append({
                "ir_id": ir_id,
                "ir_name": ir.ir_name if ir else None,
                "info_total": info_counts.get(ir_id, 0),
                "plan_total": plan_counts.get(ir_id, 0),
                "uv_count": uv_count,
                "week_number": week_number,
                "year": year,
            })

        return Response({
            "team_id": team.id,
            "team_name": team.name,
            "created_by_id": team.created_by.ir_id if team.created_by else None,
            "created_by_name": team.created_by.ir_name if team.created_by else None,
            "week_number": week_number,
            "year": year,
            "running_weekly_info_done": team.weekly_info_done,
            "running_weekly_plan_done": team.weekly_plan_done,
            "members_info_total": members_info_total,
            "members_plan_total": members_plan_total,
            "members_uv_total": total_uv_count,
            "members": members,
        })


# ---------------------------------------------------
# GET UV COUNT FOR IR (with hierarchy check)
# ---------------------------------------------------
class GetUVCount(APIView):
    def get(self, request, ir_id):
        requester_ir_id = request.GET.get("requester_ir_id")
        
        try:
            ir = get_object_or_404(Ir, ir_id=ir_id)
            
            # Check hierarchy permission if requester provided
            if requester_ir_id:
                try:
                    requester = Ir.objects.get(ir_id=requester_ir_id)
                    if not requester.can_view_ir(ir):
                        return Response(
                            {"detail": "Not authorized to view this IR's UV count"},
                            status=status.HTTP_403_FORBIDDEN
                        )
                except Ir.DoesNotExist:
                    return Response(
                        {"detail": "Requester IR not found"},
                        status=status.HTTP_404_NOT_FOUND
                    )
            
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
# GET TEAM UV TOTAL (with hierarchy check)
# ---------------------------------------------------
class GetTeamUVTotal(APIView):
    def get(self, request, team_id):
        requester_ir_id = request.GET.get("requester_ir_id")
        
        try:
            team = get_object_or_404(Team, id=team_id)
            
            # Check hierarchy permission if requester provided
            if requester_ir_id:
                try:
                    requester = Ir.objects.get(ir_id=requester_ir_id)
                    viewable_teams = get_viewable_teams_for_ir(requester)
                    if team not in viewable_teams:
                        return Response(
                            {"detail": "Not authorized to view this team's UV total"},
                            status=status.HTTP_403_FORBIDDEN
                        )
                except Ir.DoesNotExist:
                    return Response(
                        {"detail": "Requester IR not found"},
                        status=status.HTTP_404_NOT_FOUND
                    )
            
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
                "created_by_id": team.created_by.ir_id if team.created_by else None,
                "created_by_name": team.created_by.ir_name if team.created_by else None,
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


# ===================================================
# NEW HIERARCHY-BASED ENDPOINTS
# ===================================================

# ---------------------------------------------------
# GET VISIBLE TEAMS (All teams visible to an IR)
# ---------------------------------------------------
class GetVisibleTeams(APIView):
    def get(self, request, ir_id):
        try:
            ir = Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response({"detail": "IR not found"}, status=status.HTTP_404_NOT_FOUND)
        
        viewable_teams = get_viewable_teams_for_ir(ir)
        
        # Get week filter parameters
        week_param = request.GET.get("week")
        year_param = request.GET.get("year")
        
        # Calculate week info using Friday 9:31 PM IST to Friday 9:30 PM IST
        if week_param and year_param:
            try:
                week_number = int(week_param)
                year = int(year_param)
                # Validate week number
                if week_number < 1 or week_number > 52:
                    return Response(
                        {"detail": "Week number must be between 1 and 52"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                week_number, year, week_start, week_end = get_week_info_friday_to_friday(
                    week_number=week_number, year=year
                )
            except ValueError:
                return Response(
                    {"detail": "Invalid week or year parameter"},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            # Get current week
            week_number, year, week_start, week_end = get_week_info_friday_to_friday()
        
        teams_data = []
        for team in viewable_teams:
            # Get team members
            members = TeamMember.objects.filter(team=team).select_related('ir')
            member_irs = [m.ir for m in members]
            member_ir_ids = [m.ir_id for m in member_irs]
            
            # Calculate achieved for selected week
            info_achieved = InfoDetail.objects.filter(
                ir_id__in=member_ir_ids,
                info_date__gte=week_start,
                info_date__lte=week_end
            ).count()
            
            plan_achieved = PlanDetail.objects.filter(
                ir_id__in=member_ir_ids,
                plan_date__gte=week_start,
                plan_date__lte=week_end
            ).count()
            
            uv_achieved = sum(m.uv_count or 0 for m in member_irs)
            
            # Get weekly targets for selected week
            team_target = WeeklyTarget.objects.filter(
                team=team, week_number=week_number, year=year
            ).first()
            
            # Check if requester can edit this team
            can_edit = False
            if team.created_by:
                can_edit = ir.can_view_ir(team.created_by)
            
            # Check if IR is a member
            is_member = any(m.ir_id == ir.ir_id for m in members)
            
            teams_data.append({
                "team_id": team.id,
                "team_name": team.name,
                "created_by_id": team.created_by.ir_id if team.created_by else None,
                "created_by_name": team.created_by.ir_name if team.created_by else None,
                "member_count": len(member_irs),
                "is_member": is_member,
                "can_edit": can_edit,
                "targets": {
                    "info_target": team_target.team_weekly_info_target if team_target else 0,
                    "plan_target": team_target.team_weekly_plan_target if team_target else 0,
                    "uv_target": team_target.team_weekly_uv_target if team_target else 0,
                },
                "achieved": {
                    "info_achieved": info_achieved,
                    "plan_achieved": plan_achieved,
                    "uv_achieved": uv_achieved,
                }
            })
        
        return Response({
            "ir_id": ir.ir_id,
            "ir_name": ir.ir_name,
            "hierarchy_level": ir.hierarchy_level,
            "week_number": week_number,
            "year": year,
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "total_visible_teams": len(teams_data),
            "teams": teams_data
        })


# ---------------------------------------------------
# GET DOWNLINE DATA (Aggregated stats for all downlines)
# ---------------------------------------------------
class GetDownlineData(APIView):
    def get(self, request, ir_id):
        try:
            ir = Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response({"detail": "IR not found"}, status=status.HTTP_404_NOT_FOUND)
        
        # Get all IRs this user can view
        viewable_irs = ir.get_viewable_irs()
        downlines = ir.get_all_downlines()
        direct_downlines = ir.get_direct_downlines()
        
        # Aggregate stats
        total_info = sum(i.info_count or 0 for i in viewable_irs)
        total_plan = sum(i.plan_count or 0 for i in viewable_irs)
        total_uv = sum(i.uv_count or 0 for i in viewable_irs)
        
        # Get teams created by viewable IRs
        viewable_teams = Team.objects.filter(created_by__in=viewable_irs)
        
        # Get current week info
        week_number, year, week_start, week_end = get_saturday_friday_week_info()
        
        return Response({
            "ir_id": ir.ir_id,
            "ir_name": ir.ir_name,
            "hierarchy_level": ir.hierarchy_level,
            "week_number": week_number,
            "year": year,
            "counts": {
                "total_viewable_irs": viewable_irs.count(),
                "total_downlines": downlines.count(),
                "direct_downlines": direct_downlines.count(),
                "teams_created_by_downlines": viewable_teams.count(),
            },
            "aggregates": {
                "total_info_count": total_info,
                "total_plan_count": total_plan,
                "total_uv_count": total_uv,
            },
            "personal": {
                "info_count": ir.info_count,
                "plan_count": ir.plan_count,
                "uv_count": ir.uv_count,
            }
        })


# ---------------------------------------------------
# GET DIRECT DOWNLINES (List of direct children)
# ---------------------------------------------------
class GetDirectDownlines(APIView):
    def get(self, request, ir_id):
        try:
            ir = Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response({"detail": "IR not found"}, status=status.HTTP_404_NOT_FOUND)
        
        direct_downlines = ir.get_direct_downlines()
        
        data = []
        for downline in direct_downlines:
            # Count how many downlines each direct downline has
            sub_downlines_count = downline.get_all_downlines().count()
            
            data.append({
                "ir_id": downline.ir_id,
                "ir_name": downline.ir_name,
                "ir_email": downline.ir_email,
                "ir_access_level": downline.ir_access_level,
                "hierarchy_level": downline.hierarchy_level,
                "info_count": downline.info_count,
                "plan_count": downline.plan_count,
                "uv_count": downline.uv_count,
                "sub_downlines_count": sub_downlines_count,
                "status": downline.status,
            })
        
        return Response({
            "ir_id": ir.ir_id,
            "ir_name": ir.ir_name,
            "hierarchy_level": ir.hierarchy_level,
            "direct_downlines_count": len(data),
            "direct_downlines": data
        })


# ---------------------------------------------------
# GET HIERARCHY TREE (Full tree structure below an IR)
# ---------------------------------------------------
class GetHierarchyTree(APIView):
    def get(self, request, ir_id):
        max_depth = request.GET.get("max_depth")
        try:
            max_depth = int(max_depth) if max_depth else None
        except ValueError:
            max_depth = None
        
        try:
            ir = Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response({"detail": "IR not found"}, status=status.HTTP_404_NOT_FOUND)
        
        def build_tree(node, current_depth=0):
            """Recursively build tree structure"""
            if max_depth is not None and current_depth >= max_depth:
                children_count = node.get_direct_downlines().count()
                return {
                    "ir_id": node.ir_id,
                    "ir_name": node.ir_name,
                    "hierarchy_level": node.hierarchy_level,
                    "ir_access_level": node.ir_access_level,
                    "info_count": node.info_count,
                    "plan_count": node.plan_count,
                    "uv_count": node.uv_count,
                    "children_count": children_count,
                    "children": f"... {children_count} children (max_depth reached)"
                }
            
            children = node.get_direct_downlines()
            return {
                "ir_id": node.ir_id,
                "ir_name": node.ir_name,
                "hierarchy_level": node.hierarchy_level,
                "ir_access_level": node.ir_access_level,
                "info_count": node.info_count,
                "plan_count": node.plan_count,
                "uv_count": node.uv_count,
                "children_count": children.count(),
                "children": [build_tree(child, current_depth + 1) for child in children]
            }
        
        tree = build_tree(ir)
        
        # Get total counts
        all_downlines = ir.get_all_downlines()
        
        return Response({
            "root_ir_id": ir.ir_id,
            "root_ir_name": ir.ir_name,
            "total_downlines": all_downlines.count(),
            "max_depth_in_tree": all_downlines.aggregate(max_level=Count('hierarchy_level'))['max_level'] or 0,
            "tree": tree
        })


# ---------------------------------------------------
# GET AVAILABLE WEEKS (for dropdown)
# ---------------------------------------------------
class GetAvailableWeeks(APIView):
    """
    Returns list of available weeks for filtering.
    Calculates weeks based on Friday 9:31 PM IST to Friday 9:30 PM IST.
    """
    def get(self, request):
        year_param = request.GET.get("year")
        
        # Get current week info
        current_week_number, current_year, _, _ = get_week_info_friday_to_friday()
        
        # Use provided year or current year
        if year_param:
            try:
                year = int(year_param)
            except ValueError:
                return Response(
                    {"detail": "Invalid year parameter"},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            year = current_year
        
        # Generate all 52 weeks
        weeks = []
        for week_num in range(1, 53):
            week_number, year_calc, week_start, week_end = get_week_info_friday_to_friday(
                week_number=week_num, year=year
            )
            weeks.append({
                "week_number": week_number,
                "year": year_calc,
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "is_current": (week_number == current_week_number and year_calc == current_year),
                "display_name": f"Week {week_number}"
            })
        
        return Response({
            "year": year,
            "current_week": current_week_number,
            "current_year": current_year,
            "weeks": weeks
        })

