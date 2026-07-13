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
    UVDetail,
    TeamWeek,
    TeamRole,
    WeeklyTarget,
    TeamWeeklyTargets,
    DashboardMappingConfig,
    AccessLevel,
)
from core.serializers import (
    IrIdSerializer,
    IrSerializer,
    TeamSerializer,
    TeamMemberSerializer,
    InfoDetailSerializer,
    PlanDetailSerializer,
    UVDetailSerializer,
)

from datetime import datetime, timedelta
from collections import Counter
import pytz
import logging

from core.utils.dates import get_current_week_start, get_week_info_friday_to_friday, get_week_info_monday_to_sunday

IST = pytz.timezone("Asia/Kolkata")
timezone = pytz.timezone


# ===================================================
# HELPER: Get viewable teams for an IR (role-based)
# ===================================================
def get_viewable_teams_for_ir(ir):
    """
    Get all teams visible to an IR based on role.
    Uses the role-based get_teams_can_view() method.
    """
    return ir.get_teams_can_view()


MAX_PAGE_LIMIT = 200


def apply_optional_pagination(queryset, request):
    """
    Opt-in limit/offset slicing: if the client passes `limit`, apply it
    (clamped to MAX_PAGE_LIMIT) with an optional `offset`. If `limit` is
    omitted, the queryset is returned unsliced — existing callers that don't
    yet know about pagination keep getting the full result set, unchanged.
    """
    limit_param = request.GET.get("limit")
    if not limit_param:
        return queryset
    try:
        limit = max(1, min(int(limit_param), MAX_PAGE_LIMIT))
        offset = max(0, int(request.GET.get("offset", 0)))
    except (TypeError, ValueError):
        return queryset
    return queryset[offset:offset + limit]


# ---------------------------------------------------
# GET ALL IR IDs
# ---------------------------------------------------
class GetAllIR(APIView):
    def get(self, request):
        irs = apply_optional_pagination(IrId.objects.all(), request)
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
                irs = requester.get_viewable_irs().select_related('parent_ir')
            except Ir.DoesNotExist:
                return Response(
                    {"detail": "Requester IR not found"},
                    status=status.HTTP_404_NOT_FOUND
                )
        else:
            # No filter - return all (backward compatible)
            irs = Ir.objects.select_related('parent_ir').all()

        irs = list(apply_optional_pagination(irs, request))
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
        
        # Prefetch all memberships + IRs in a single query instead of N queries
        teams = list(teams.prefetch_related('teammember_set__ir').select_related('created_by'))

        _, _, week_start, week_end = get_week_info_friday_to_friday()

        # Collect all member IR IDs across all teams for a single UV batch query
        team_member_ids_map = {}  # team_id -> [ir_id, ...]
        all_member_ir_ids = set()
        for team in teams:
            ids = [m.ir.ir_id for m in team.teammember_set.all()]
            team_member_ids_map[team.id] = ids
            all_member_ir_ids.update(ids)

        # Single UV aggregate query for all teams
        try:
            uv_by_ir = dict(
                UVDetail.objects.filter(
                    ir_id__in=all_member_ir_ids,
                    uv_date__gte=week_start,
                    uv_date__lte=week_end,
                ).values('ir_id').annotate(total=Sum('uv_count')).values_list('ir_id', 'total')
            )
        except Exception:
            uv_by_ir = {}

        result = []
        for team in teams:
            members = list(team.teammember_set.all())  # from prefetch cache, no DB hit
            info_total = sum(m.ir.info_count or 0 for m in members)
            plan_total = sum(m.ir.plan_count or 0 for m in members)
            uv_total = sum(float(uv_by_ir.get(ir_id, 0)) for ir_id in team_member_ids_map[team.id])

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
def get_viewable_ldcs(requester):
    """
    All LDCs visible to `requester`, hierarchy-filtered when a requester is
    given. Shared by GetLDCs (full weekly breakdown) and GetManagerDashboard
    (grouped/aggregated view) so the two stay in sync on "who counts as an
    LDC I can see."
    """
    ldc_ids = TeamMember.objects.filter(
        role=TeamRole.LDC
    ).values_list("ir_id", flat=True).distinct()

    ldcs = Ir.objects.filter(ir_id__in=ldc_ids)

    if requester:
        viewable_irs = requester.get_viewable_irs()
        ldcs = ldcs.filter(ir_id__in=viewable_irs.values_list('ir_id', flat=True))

    return ldcs


class GetLDCs(APIView):
    import logging
    def get(self, request):
        requester_ir_id = request.GET.get("requester_ir_id")

        requester = None
        if requester_ir_id:
            try:
                requester = Ir.objects.get(ir_id=requester_ir_id)
            except Ir.DoesNotExist:
                return Response(
                    {"detail": "Requester IR not found"},
                    status=status.HTTP_404_NOT_FOUND
                )

        ldcs = get_viewable_ldcs(requester)

        from core.utils.dates import get_week_info_friday_to_friday
        from datetime import datetime
        import pytz
        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.now(ist)
        current_week_number, current_year, _, _ = get_week_info_friday_to_friday(now)

        # ── Build member maps for all LDCs in bulk ───────────────────────────
        ldc_ids_list = [ldc.ir_id for ldc in ldcs]

        # All teams managed/created by these LDCs (2 queries total)
        ldc_team_memberships = list(
            TeamMember.objects.filter(ir_id__in=ldc_ids_list, role=TeamRole.LDC)
                              .values_list('ir_id', 'team_id')
        )
        ldc_created_teams = list(
            Team.objects.filter(created_by_id__in=ldc_ids_list)
                        .values_list('created_by_id', 'id')
        )

        ldc_managed_team_ids = {}
        for ldc_ir_id, team_id in ldc_team_memberships:
            ldc_managed_team_ids.setdefault(ldc_ir_id, set()).add(team_id)

        ldc_created_team_ids = {}
        for ldc_ir_id, team_id in ldc_created_teams:
            ldc_created_team_ids.setdefault(ldc_ir_id, set()).add(team_id)

        # All members for all relevant teams (1 query)
        all_team_ids = set(t for ids in ldc_created_team_ids.values() for t in ids)
        all_team_members = list(
            TeamMember.objects.filter(team_id__in=all_team_ids)
                              .values_list('team_id', 'ir_id')
        )
        team_to_members = {}
        for team_id, ir_id in all_team_members:
            team_to_members.setdefault(team_id, set()).add(ir_id)

        # Build ldc_ir_id -> set of member ir_ids
        ldc_member_map = {}
        for ldc in ldcs:
            created = ldc_created_team_ids.get(ldc.ir_id, set())
            members = set()
            for tid in created:
                members.update(team_to_members.get(tid, set()))
            members.discard(ldc.ir_id)
            ldc_member_map[ldc.ir_id] = members

        # managed-teams member count
        ldc_managed_member_map = {}
        for ldc in ldcs:
            managed = ldc_managed_team_ids.get(ldc.ir_id, set())
            members = set()
            for tid in managed:
                members.update(team_to_members.get(tid, set()))
            members.discard(ldc.ir_id)
            ldc_managed_member_map[ldc.ir_id] = members

        all_member_ids = set(ir_id for ids in ldc_member_map.values() for ir_id in ids)

        # Compute week date ranges once for all weeks
        week_ranges = {}
        for week_num in range(1, current_week_number + 1):
            _, yr, ws, we = get_week_info_friday_to_friday(week_number=week_num, year=current_year)
            _, _, pws, pwe = get_week_info_monday_to_sunday(week_number=week_num, year=current_year)
            week_ranges[week_num] = (ws, we, pws, pwe)

        year_start = week_ranges[1][0]
        year_end   = week_ranges[current_week_number][1]
        plan_year_start = week_ranges[1][2]
        plan_year_end   = week_ranges[current_week_number][3]

        # Bulk fetch raw info/plan/uv rows (3 queries for ALL LDCs and ALL weeks)
        info_rows = list(
            InfoDetail.objects.filter(
                ir_id__in=all_member_ids,
                info_date__gte=year_start,
                info_date__lte=year_end,
            ).values_list('ir_id', 'info_date')
        ) if all_member_ids else []

        plan_rows = list(
            PlanDetail.objects.filter(
                ir_id__in=all_member_ids,
                plan_date__gte=plan_year_start,
                plan_date__lte=plan_year_end,
            ).values_list('ir_id', 'plan_date')
        ) if all_member_ids else []

        try:
            uv_rows = list(
                UVDetail.objects.filter(
                    ir_id__in=all_member_ids,
                    uv_date__gte=year_start,
                    uv_date__lte=year_end,
                ).values_list('ir_id', 'uv_date', 'uv_count')
            ) if all_member_ids else []
        except Exception:
            uv_rows = []

        data = []
        for ldc in ldcs:
            members = ldc_member_map[ldc.ir_id]
            managed_members = ldc_managed_member_map[ldc.ir_id]
            member_count = len(managed_members)

            week_data = {}
            for week_num in range(1, current_week_number + 1):
                ws, we, pws, pwe = week_ranges[week_num]
                total_infos_done = sum(
                    1 for ir_id, dt in info_rows
                    if ir_id in members and ws <= dt <= we
                )
                total_plans_done = sum(
                    1 for ir_id, dt in plan_rows
                    if ir_id in members and pws <= dt <= pwe
                )
                uvs_fallen = sum(
                    float(cnt) for ir_id, dt, cnt in uv_rows
                    if ir_id in members and ws <= dt <= we
                )
                week_data[week_num] = {
                    "total_infos_done": total_infos_done,
                    "total_plans_done": total_plans_done,
                    "uvs_fallen": uvs_fallen,
                }

            data.append({
                "ir_id": ldc.ir_id,
                "ir_name": ldc.ir_name,
                "id": ldc.ir_id,
                "ir_access_level": ldc.ir_access_level,
                "team_member_count": member_count,
                "week": week_data,
            })
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

            # Calculate week info - Infos use Friday 9:30 PM to next Friday 11:45 PM
            # Plans use Monday to Sunday
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
                    # Infos use Friday-Friday range
                    week_number, year, info_week_start, info_week_end = get_week_info_friday_to_friday(
                        week_number=week_number, year=year
                    )
                    # Plans use Monday-Sunday range
                    _, _, plan_week_start, plan_week_end = get_week_info_monday_to_sunday(
                        week_number=week_number, year=year
                    )
                except ValueError:
                    return Response(
                        {"detail": "Invalid week or year parameter"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            else:
                # Get current week
                week_number, year, info_week_start, info_week_end = get_week_info_friday_to_friday()
                _, _, plan_week_start, plan_week_end = get_week_info_monday_to_sunday()

            members = list(TeamMember.objects.filter(team_id=team_id, ir__status=True).select_related("ir"))

            # Map team roles to access level numbers (matching AccessLevel class)
            # Admin=1, CTC=2, LDC=3, LS=4, GC=5, IR=6
            role_map = {"ADMIN": 1, "CTC": 2, "LDC": 3, "LS": 4, "GC": 5, "IR": 6}
            result = []

            # ── Batch all per-member queries into bulk lookups (was 4 queries/member) ──
            member_ir_ids = [member.ir_id for member in members]

            info_by_ir = dict(
                InfoDetail.objects.filter(
                    ir_id__in=member_ir_ids,
                    info_date__gte=info_week_start,
                    info_date__lte=info_week_end,
                ).values('ir_id').annotate(c=Count('id')).values_list('ir_id', 'c')
            )

            plan_by_ir = dict(
                PlanDetail.objects.filter(
                    ir_id__in=member_ir_ids,
                    plan_date__gte=plan_week_start,
                    plan_date__lte=plan_week_end,
                ).values('ir_id').annotate(c=Count('id')).values_list('ir_id', 'c')
            )

            try:
                uv_by_ir = dict(
                    UVDetail.objects.filter(
                        ir_id__in=member_ir_ids,
                        uv_date__gte=info_week_start,
                        uv_date__lte=info_week_end,
                    ).values('ir_id').annotate(total=Sum('uv_count')).values_list('ir_id', 'total')
                )
            except Exception:
                # UVDetail table may not exist yet due to pending migrations
                uv_by_ir = {}

            target_by_ir = {
                t.ir_id: t
                for t in WeeklyTarget.objects.filter(
                    ir_id__in=member_ir_ids, week_number=week_number, year=year
                )
            }

            for member in members:
                ir = member.ir
                ir_target = target_by_ir.get(ir.ir_id)

                result.append({
                    **TeamMemberSerializer(member).data,
                    "ir_name": ir.ir_name,
                    "role_num": role_map.get(member.role, 6),  # Team role (deprecated, use ir_access_level)
                    "ir_access_level": ir.ir_access_level,  # Actual access level from IR model
                    "weekly_info_target": ir_target.ir_weekly_info_target if ir_target else ir.weekly_info_target,
                    "weekly_plan_target": ir_target.ir_weekly_plan_target if ir_target else ir.weekly_plan_target,
                    "info_count": info_by_ir.get(ir.ir_id, 0),
                    "plan_count": plan_by_ir.get(ir.ir_id, 0),
                    "weekly_uv_target": (ir_target.ir_weekly_uv_target if ir_target else ir.weekly_uv_target) if ir.ir_access_level in [2, 3] else None,
                    "uv_count": uv_by_ir.get(ir.ir_id, 0) or 0, #if ir.ir_access_level in [2, 3] else None
                    "cumulative_uv_count": ir.uv_count if ir.ir_access_level in [2, 3] else None,
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
        
        response_filter = request.GET.get("response")

        # Base queryset
        qs = InfoDetail.objects.filter(ir_id=ir_id)

        if week_param and year_param:
            # Use timezone-aware datetime bounds to match Friday-to-Friday definition precisely
            try:
                from core.utils.dates import get_week_info_friday_to_friday
                ist = pytz.timezone('Asia/Kolkata')
                now = datetime.now(ist)
                _, _, week_start, week_end = get_week_info_friday_to_friday(now, int(week_param), int(year_param))
                qs = qs.filter(info_date__gte=week_start, info_date__lte=week_end)
            except Exception as e:
                logging.exception("Error processing week parameters for ir_id=%s", ir_id)
                return Response({"detail": f"Error processing week parameters: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)
        else:
            # Fallback to date-only filters when explicit dates are provided
            from_date = request.GET.get("from_date")
            to_date = request.GET.get("to_date")
            if from_date:
                qs = qs.filter(info_date__date__gte=parse_date(from_date))
            if to_date:
                qs = qs.filter(info_date__date__lte=parse_date(to_date))

        if response_filter:
            qs = qs.filter(response=response_filter)

        # Filter by info_type if provided
        info_type_filter = request.GET.get("infoType")
        if info_type_filter:
            qs = qs.filter(info_type=info_type_filter)

        qs = apply_optional_pagination(qs, request)
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
            status_filter = request.GET.get("status")
            overdue_followups = request.GET.get("overdue_followups")

            # Overdue follow-ups mode: ignore week filter, return plans with past follow_up_date
            if overdue_followups == "true":
                ist = pytz.timezone('Asia/Kolkata')
                today = datetime.now(ist).date()
                qs = PlanDetail.objects.filter(
                    ir_id=ir_id,
                    follow_up_date__lte=today,
                    status__in=['closing_pending', 'kiv'],
                ).order_by('follow_up_date')
                qs = apply_optional_pagination(qs, request)
                return Response(PlanDetailSerializer(qs, many=True).data)

            # Check for week/year parameters first
            week_param = request.GET.get("week")
            year_param = request.GET.get("year")

            if week_param and year_param:
                try:
                    ist = pytz.timezone('Asia/Kolkata')
                    now = datetime.now(ist)
                    _, _, week_start, week_end = get_week_info_monday_to_sunday(now, int(week_param), int(year_param))
                    # Use datetime bounds to match Monday-to-Sunday precisely
                    qs = PlanDetail.objects.filter(
                        ir_id=ir_id,
                        plan_date__gte=week_start,
                        plan_date__lte=week_end,
                    )
                except Exception as e:
                    logging.exception("Error processing week parameters for ir_id=%s", ir_id)
                    return Response({"detail": f"Error processing week parameters: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)
            else:
                from_date = request.GET.get("from_date")
                to_date = request.GET.get("to_date")

                qs = PlanDetail.objects.filter(ir_id=ir_id)
                if from_date:
                    qs = qs.filter(plan_date__date__gte=parse_date(from_date))
                if to_date:
                    qs = qs.filter(plan_date__date__lte=parse_date(to_date))
            if status_filter:
                qs = qs.filter(status=status_filter)

            qs = apply_optional_pagination(qs, request)
            return Response(PlanDetailSerializer(qs, many=True).data)
        except Ir.DoesNotExist:
            return Response({"detail": "IR not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception:
            logging.exception("Error fetching plan details for ir_id=%s", ir_id)
            return Response({"detail": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------------------------------------------------
# TEAM-AGGREGATED PLANS (all plans across visible teams)
# ---------------------------------------------------
class GetTeamAggregatedPlans(APIView):
    def get(self, request, ir_id):
        try:
            ir = Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response({"detail": "IR not found"}, status=status.HTTP_404_NOT_FOUND)

        week_param = request.GET.get("week")
        year_param = request.GET.get("year")

        if week_param and year_param:
            try:
                week_number = int(week_param)
                year = int(year_param)
                _, _, plan_week_start, plan_week_end = get_week_info_monday_to_sunday(
                    week_number=week_number, year=year
                )
            except (ValueError, Exception):
                return Response({"detail": "Invalid week or year"}, status=status.HTTP_400_BAD_REQUEST)
        else:
            _, _, plan_week_start, plan_week_end = get_week_info_monday_to_sunday()

        # Plan visibility follows each person's own downline (hierarchy subtree),
        # never other branches/lines they merely share a team with. Admin alone
        # sees the whole org.
        if ir.ir_access_level == AccessLevel.ADMIN:
            all_member_ids = set(Ir.objects.filter(status=True).values_list('ir_id', flat=True))
        else:
            all_member_ids = set(ir.get_subtree_irs().values_list('ir_id', flat=True))

        if not all_member_ids:
            return Response({"plans": [], "presenters": [], "summary": {"total_plans": 0, "closed_count": 0, "total_positive_uvs": 0}})

        plans_qs = (
            PlanDetail.objects.filter(
                ir_id__in=list(all_member_ids),
                plan_date__gte=plan_week_start,
                plan_date__lte=plan_week_end,
            )
            .select_related('ir', 'presented_by')
            .order_by('-plan_date')
        )

        # Optional filter by presenter (UL2)
        presented_by_filter = request.GET.get("presented_by")
        if presented_by_filter:
            plans_qs = plans_qs.filter(presented_by__ir_id=presented_by_filter)

        # Summary/presenters must reflect ALL matching plans regardless of
        # whether the `plans` list below gets paginated for display — compute
        # them via DB aggregation over the full plans_qs first (also avoids
        # pulling every row into Python just to sum two numbers).
        total_plans_count = plans_qs.count()
        closed_count = plans_qs.filter(status='closed').count()
        total_positive_uvs = float(
            plans_qs.filter(uv_value__gt=0).aggregate(total=Sum('uv_value'))['total'] or 0
        )
        presenters_set = dict(
            plans_qs.exclude(presented_by__isnull=True)
            .values_list('presented_by__ir_id', 'presented_by__ir_name')
            .distinct()
        )

        plans = []
        for p in apply_optional_pagination(plans_qs, request):
            presenter_id = p.presented_by.ir_id if p.presented_by else None
            presenter_name = p.presented_by.ir_name if p.presented_by else None

            plans.append({
                "id": p.id,
                "ir_id": p.ir_id,
                "ir_name": p.ir.ir_name if p.ir else "",
                "plan_name": p.plan_name or "",
                "plan_date": p.plan_date.isoformat() if p.plan_date else None,
                "status": p.status or "closing_pending",
                "follow_up_date": p.follow_up_date.isoformat() if p.follow_up_date else None,
                "uv_value": str(p.uv_value) if p.uv_value is not None else None,
                "comments": p.comments or "",
                "rejection_reason": p.rejection_reason,
                "presented_by_id": presenter_id,
                "presented_by_name": presenter_name,
            })

        presenters = [{"ir_id": k, "ir_name": v} for k, v in presenters_set.items()]

        return Response({
            "plans": plans,
            "presenters": presenters,
            "summary": {
                "total_plans": total_plans_count,
                "closed_count": closed_count,
                "total_positive_uvs": round(total_positive_uvs, 2),
            },
        })


# ---------------------------------------------------
# DASHBOARD TARGETS (with hierarchy-based team filtering)
# ---------------------------------------------------
class GetTargetsDashboard(APIView):
    def get(self, request, ir_id):
        ir = get_object_or_404(Ir, ir_id=ir_id)
        
        # Get week info using Friday 9:30 PM IST → next Friday 11:30 PM IST for infos/UVs
        week_param = request.GET.get("week")
        year_param = request.GET.get("year")
        try:
            if week_param and year_param:
                week_number, year, week_start, week_end = get_week_info_friday_to_friday(
                    week_number=int(week_param), year=int(year_param)
                )
            else:
                week_number, year, week_start, week_end = get_week_info_friday_to_friday()
        except Exception:
            logging.exception("Error computing week bounds for targets_dashboard ir_id=%s", ir_id)
            return Response({"detail": "Invalid week parameters"}, status=status.HTTP_400_BAD_REQUEST)
        
        # Get weekly targets for current week
        ir_weekly_target = WeeklyTarget.objects.filter(
            ir=ir,
            week_number=week_number,
            year=year
        ).first()

        # Calculate current week's info and plan counts
        _, _, plan_week_start, plan_week_end = get_week_info_monday_to_sunday(
            week_number=week_number, year=year
        )
        current_week_info_count = InfoDetail.objects.filter(
            ir_id=ir.ir_id,
            info_date__gte=week_start,
            info_date__lte=week_end
        ).count()
        
        current_week_plan_count = PlanDetail.objects.filter(
            ir_id=ir.ir_id,
            plan_date__gte=plan_week_start,
            plan_date__lte=plan_week_end
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
        viewable_teams = list(get_viewable_teams_for_ir(ir).select_related('created_by'))
        team_ids = [t.id for t in viewable_teams]

        # ── Batch all per-team queries into bulk lookups (was ~5 queries/team) ──
        from core.models import TeamWeeklyTargets

        team_member_map = {}  # team_id -> [ir_id, ...]
        all_member_ids = set()
        for team_id, member_ir_id in TeamMember.objects.filter(
            team_id__in=team_ids, ir__status=True
        ).values_list('team_id', 'ir_id').distinct():
            team_member_map.setdefault(team_id, []).append(member_ir_id)
            all_member_ids.add(member_ir_id)

        info_by_ir = dict(
            InfoDetail.objects.filter(
                ir_id__in=all_member_ids, info_date__gte=week_start, info_date__lte=week_end,
            ).values('ir_id').annotate(c=Count('id')).values_list('ir_id', 'c')
        )
        plan_by_ir = dict(
            PlanDetail.objects.filter(
                ir_id__in=all_member_ids, plan_date__gte=plan_week_start, plan_date__lte=plan_week_end,
            ).values('ir_id').annotate(c=Count('id')).values_list('ir_id', 'c')
        )
        try:
            uv_by_ir = dict(
                UVDetail.objects.filter(
                    ir_id__in=all_member_ids, uv_date__gte=week_start, uv_date__lte=week_end,
                ).values('ir_id').annotate(total=Sum('uv_count')).values_list('ir_id', 'total')
            )
        except Exception:
            # UVDetail table may not exist yet due to pending migrations
            uv_by_ir = {}

        team_targets_map = {
            tt.team_id: tt
            for tt in TeamWeeklyTargets.objects.filter(team_id__in=team_ids)
        }

        teams_progress = []

        for team in viewable_teams:
            member_ids = team_member_map.get(team.id, [])

            team_targets = team_targets_map.get(team.id)
            week_data = team_targets.get_week_targets(year, week_number) if team_targets else None

            team_info_progress = sum(info_by_ir.get(i, 0) for i in member_ids)
            team_plan_progress = sum(plan_by_ir.get(i, 0) for i in member_ids)
            team_uv_progress = sum(uv_by_ir.get(i, 0) or 0 for i in member_ids)

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
                "weekly_info_target": week_data.get("team_weekly_info_target", 0) if week_data else 0,
                "weekly_plan_target": week_data.get("team_weekly_plan_target", 0) if week_data else 0,
                "weekly_uv_target": week_data.get("team_weekly_uv_target", 0) if week_data else 0,
                "info_progress": team_info_progress,
                "plan_progress": team_plan_progress,
                "uv_progress": team_uv_progress,
                "plan_week_start": plan_week_start.isoformat(),
                "plan_week_end": plan_week_end.isoformat(),
                "info_week_start": week_start.isoformat(),
                "info_week_end": week_end.isoformat(),
            })

        return Response({"personal": personal, "teams": teams_progress})


class DashboardMappingConfigView(APIView):
    """
    Per-CTC/Admin custom LDC grouping for GetManagerDashboard — same
    GET/PUT-a-JSON-blob shape as ChatTabsConfigView.
    """

    def get(self, request, ir_id):
        try:
            ir = Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response({"detail": "ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            cfg = ir.dashboard_mapping_config
            return Response({"config": cfg.config})
        except DashboardMappingConfig.DoesNotExist:
            return Response({"config": {}})

    def put(self, request, ir_id):
        config = request.data.get("config")
        if not isinstance(config, dict):
            return Response({"detail": "config must be an object"}, status=status.HTTP_400_BAD_REQUEST)

        groups = config.get("groups")
        if groups is not None:
            if not isinstance(groups, list):
                return Response({"detail": "config.groups must be a list"}, status=status.HTTP_400_BAD_REQUEST)
            for g in groups:
                if not isinstance(g, dict) or not isinstance(g.get("member_ldc_ids"), list):
                    return Response(
                        {"detail": "each group must be an object with a member_ldc_ids list"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

        known_ldc_ids = config.get("known_ldc_ids")
        if known_ldc_ids is not None and not isinstance(known_ldc_ids, list):
            return Response({"detail": "config.known_ldc_ids must be a list"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            ir = Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response({"detail": "ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        DashboardMappingConfig.objects.update_or_create(ir=ir, defaults={"config": config})
        return Response({"config": config})


class GetManagerDashboard(APIView):
    """
    Scalable, grouped replacement for the old per-LDC N+1 dashboard fetch.

    Instead of the frontend calling GetTargetsDashboard once per viewable LDC,
    this reads the requester's saved DashboardMappingConfig (defaulting to one
    group per viewable LDC when unset) and returns one aggregated row per
    group, using bulk queries scoped only to the LDCs actually in some group —
    excluded LDCs are never queried at all, so cost scales with what's
    configured to show rather than with the requester's total downline size.
    """

    def get(self, request):
        requester_ir_id = request.GET.get("requester_ir_id")
        if not requester_ir_id:
            return Response({"detail": "requester_ir_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            requester = Ir.objects.get(ir_id=requester_ir_id)
        except Ir.DoesNotExist:
            return Response({"detail": "Requester IR not found"}, status=status.HTTP_404_NOT_FOUND)

        week_param = request.GET.get("week")
        year_param = request.GET.get("year")
        try:
            if week_param and year_param:
                week_number, year, week_start, week_end = get_week_info_friday_to_friday(
                    week_number=int(week_param), year=int(year_param)
                )
            else:
                week_number, year, week_start, week_end = get_week_info_friday_to_friday()
        except Exception:
            return Response({"detail": "Invalid week parameters"}, status=status.HTTP_400_BAD_REQUEST)
        _, _, plan_week_start, plan_week_end = get_week_info_monday_to_sunday(
            week_number=week_number, year=year
        )

        viewable_ldcs = list(get_viewable_ldcs(requester))
        viewable_ldc_ids = {ldc.ir_id for ldc in viewable_ldcs}
        ldc_by_id = {ldc.ir_id: ldc for ldc in viewable_ldcs}

        try:
            saved_config = requester.dashboard_mapping_config.config or {}
        except DashboardMappingConfig.DoesNotExist:
            saved_config = {}
        saved_groups = saved_config.get("groups") or []
        # Snapshot (taken at last save) of every LDC id the admin could see —
        # lets us tell "intentionally excluded" (in this set, absent from
        # every group) apart from "new LDC since last save" (absent from this
        # set entirely), which must still surface automatically.
        known_ldc_ids = set(saved_config.get("known_ldc_ids") or [])

        # Keep only still-viewable member ids per saved group; drop groups
        # left with none (LDC removed from the org / no longer viewable).
        effective_groups = []
        covered_ldc_ids = set()
        for g in saved_groups:
            member_ids = [mid for mid in (g.get("member_ldc_ids") or []) if mid in viewable_ldc_ids]
            if not member_ids:
                continue
            effective_groups.append({
                "id": g.get("id") or member_ids[0],
                "label": (g.get("label") or "").strip() or None,
                "member_ldc_ids": member_ids,
            })
            covered_ldc_ids.update(member_ids)

        # Any viewable LDC not yet assigned anywhere gets its own default
        # group UNLESS the admin's last save already knew about it and chose
        # to leave it out (a deliberate exclusion, not a new LDC to surface).
        for ldc in viewable_ldcs:
            if ldc.ir_id not in covered_ldc_ids and ldc.ir_id not in known_ldc_ids:
                effective_groups.append({"id": ldc.ir_id, "label": None, "member_ldc_ids": [ldc.ir_id]})

        needed_ldc_ids = [mid for g in effective_groups for mid in g["member_ldc_ids"]]

        # ── Bulk data fetch — scoped only to needed LDCs, current week only ──
        teams = list(Team.objects.filter(created_by_id__in=needed_ldc_ids).only("id", "name", "created_by_id"))
        team_ids = [t.id for t in teams]

        member_rows = list(TeamMember.objects.filter(team_id__in=team_ids).values_list("team_id", "ir_id"))
        team_to_members = {}
        for team_id, ir_id in member_rows:
            team_to_members.setdefault(team_id, set()).add(ir_id)
        all_member_ids = set(ir_id for ids in team_to_members.values() for ir_id in ids)

        info_rows = list(
            InfoDetail.objects.filter(
                ir_id__in=all_member_ids, info_date__gte=week_start, info_date__lte=week_end
            ).values_list("ir_id", flat=True)
        ) if all_member_ids else []
        plan_rows = list(
            PlanDetail.objects.filter(
                ir_id__in=all_member_ids, plan_date__gte=plan_week_start, plan_date__lte=plan_week_end
            ).values_list("ir_id", flat=True)
        ) if all_member_ids else []
        try:
            uv_rows = list(
                UVDetail.objects.filter(
                    ir_id__in=all_member_ids, uv_date__gte=week_start, uv_date__lte=week_end
                ).values_list("ir_id", "uv_count")
            ) if all_member_ids else []
        except Exception:
            uv_rows = []

        info_count_by_ir = Counter(info_rows)
        plan_count_by_ir = Counter(plan_rows)
        uv_sum_by_ir = {}
        for ir_id, cnt in uv_rows:
            uv_sum_by_ir[ir_id] = uv_sum_by_ir.get(ir_id, 0) + float(cnt or 0)

        targets_by_team = {}
        for twt in TeamWeeklyTargets.objects.filter(team_id__in=team_ids):
            targets_by_team[twt.team_id] = twt.get_week_targets(year, week_number) or {}

        teams_by_ldc = {}
        for t in teams:
            teams_by_ldc.setdefault(t.created_by_id, []).append(t)

        def team_stats(team):
            members = team_to_members.get(team.id, set())
            week_data = targets_by_team.get(team.id) or {}
            return {
                "team_id": team.id,
                "team_name": team.name,
                "info_progress": sum(info_count_by_ir.get(m, 0) for m in members),
                "plan_progress": sum(plan_count_by_ir.get(m, 0) for m in members),
                "uv_progress": sum(uv_sum_by_ir.get(m, 0) for m in members),
                "weekly_info_target": week_data.get("team_weekly_info_target", 0),
                "weekly_plan_target": week_data.get("team_weekly_plan_target", 0),
                "weekly_uv_target": week_data.get("team_weekly_uv_target", 0),
            }

        groups_out = []
        for g in effective_groups:
            group_teams = []
            for mid in g["member_ldc_ids"]:
                ldc = ldc_by_id.get(mid)
                ldc_name = ldc.ir_name if ldc else mid
                for t in teams_by_ldc.get(mid, []):
                    stats = team_stats(t)
                    stats["ldc_id"] = mid
                    stats["ldc_name"] = ldc_name
                    group_teams.append(stats)

            label = g["label"] or (ldc_by_id[g["member_ldc_ids"][0]].ir_name if g["member_ldc_ids"][0] in ldc_by_id else g["member_ldc_ids"][0])

            groups_out.append({
                "id": g["id"],
                "label": label,
                "member_ldc_ids": g["member_ldc_ids"],
                "member_ldc_names": [ldc_by_id[m].ir_name if m in ldc_by_id else m for m in g["member_ldc_ids"]],
                "teams": group_teams,
                "totals": {
                    "info_done": sum(t["info_progress"] for t in group_teams),
                    "plan_done": sum(t["plan_progress"] for t in group_teams),
                    "uv_done": round(sum(t["uv_progress"] for t in group_teams), 2),
                    "info_target": sum(t["weekly_info_target"] for t in group_teams),
                    "plan_target": sum(t["weekly_plan_target"] for t in group_teams),
                    "uv_target": round(sum(t["weekly_uv_target"] for t in group_teams), 2),
                },
            })

        overall_totals = {
            "info_done": sum(gr["totals"]["info_done"] for gr in groups_out),
            "plan_done": sum(gr["totals"]["plan_done"] for gr in groups_out),
            "uv_done": round(sum(gr["totals"]["uv_done"] for gr in groups_out), 2),
        }

        return Response({
            "week_number": week_number,
            "year": year,
            "groups": groups_out,
            "overall_totals": overall_totals,
        })


class GetTargets(APIView):
    def get(self, request):
        ir_id = request.GET.get("ir_id")
        team_id = request.GET.get("team_id")
        requester_ir_id = request.GET.get("requester_ir_id")
        week_param = request.GET.get("week")
        year_param = request.GET.get("year")

        if not ir_id and not team_id:
            return Response({"detail": "Provide `ir_id` or `team_id` as query parameter"}, status=status.HTTP_400_BAD_REQUEST)

        # Determine if we should return single week or all weeks
        return_all_weeks = week_param is None or year_param is None
        
        # Get week info (Friday→Friday) - use provided week or current
        if not return_all_weeks:
            try:
                week_number, year, week_start, week_end = get_week_info_friday_to_friday(
                    week_number=int(week_param), year=int(year_param)
                )
            except (ValueError, TypeError):
                return Response({"detail": "Invalid week or year parameter"}, status=status.HTTP_400_BAD_REQUEST)
        else:
            # Get current week info for reference
            week_number, year, week_start, week_end = get_week_info_friday_to_friday()
        
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
                
                if return_all_weeks:
                    # Return all weeks' targets for this IR
                    weekly_targets = WeeklyTarget.objects.filter(ir=ir).order_by('year', 'week_number')
                    data["ir"] = {
                        "ir_id": ir.ir_id,
                        "ir_name": ir.ir_name,
                        "weeks": [
                            {
                                "week_number": wt.week_number,
                                "year": wt.year,
                                "week_start": wt.week_start.isoformat(),
                                "week_end": wt.week_end.isoformat(),
                                "weekly_info_target": wt.ir_weekly_info_target or 0,
                                "weekly_plan_target": wt.ir_weekly_plan_target or 0,
                                "weekly_uv_target": wt.ir_weekly_uv_target if ir.ir_access_level in [2, 3] else None,
                            }
                            for wt in weekly_targets
                        ],
                        "total_weeks_with_targets": weekly_targets.count()
                    }
                else:
                    # Return single week's targets
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
                
                # Get TeamWeeklyTargets record for this team
                from core.models import TeamWeeklyTargets
                try:
                    team_targets = TeamWeeklyTargets.objects.get(team=team)
                except TeamWeeklyTargets.DoesNotExist:
                    team_targets = None
                
                if return_all_weeks:
                    # Return all weeks' targets for this team from JSON
                    if team_targets and team_targets.targets_data:
                        all_weeks = []
                        for year_str, weeks_data in sorted(team_targets.targets_data.items()):
                            for week_str, week_data in sorted(weeks_data.items(), key=lambda x: int(x[0])):
                                all_weeks.append({
                                    "week_number": int(week_str),
                                    "year": int(year_str),
                                    "week_start": week_data.get("week_start"),
                                    "week_end": week_data.get("week_end"),
                                    "team_weekly_info_target": week_data.get("team_weekly_info_target", 0),
                                    "team_weekly_plan_target": week_data.get("team_weekly_plan_target", 0),
                                    "team_weekly_uv_target": week_data.get("team_weekly_uv_target", 0),
                                })
                        
                        data["team"] = {
                            "team_id": team.id,
                            "team_name": team.name,
                            "created_by_id": team.created_by.ir_id if team.created_by else None,
                            "weeks": all_weeks,
                            "total_weeks_with_targets": len(all_weeks)
                        }
                    else:
                        data["team"] = {
                            "team_id": team.id,
                            "team_name": team.name,
                            "created_by_id": team.created_by.ir_id if team.created_by else None,
                            "weeks": [],
                            "total_weeks_with_targets": 0
                        }
                else:
                    # Return single week's targets from JSON
                    week_data = None
                    if team_targets:
                        week_data = team_targets.get_week_targets(year, week_number)
                    
                    data["team"] = {
                        "team_id": team.id,
                        "team_name": team.name,
                        "created_by_id": team.created_by.ir_id if team.created_by else None,
                        "team_weekly_info_target": week_data.get("team_weekly_info_target", 0) if week_data else 0,
                        "team_weekly_plan_target": week_data.get("team_weekly_plan_target", 0) if week_data else 0,
                        "team_weekly_uv_target": week_data.get("team_weekly_uv_target", 0) if week_data else 0,
                        "has_weekly_targets_set": week_data is not None
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
        
        teams = Team.objects.filter(teammember__ir_id=ir_id).distinct().select_related('created_by')
        
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

        # Compute week bounds - Infos use Friday→Friday, Plans use Monday→Sunday
        week_param = request.GET.get("week")
        year_param = request.GET.get("year")
        try:
            if week_param and year_param:
                # Infos use Friday-Friday range
                week_number, year, info_week_start, info_week_end = get_week_info_friday_to_friday(
                    week_number=int(week_param), year=int(year_param)
                )
                # Plans use Monday-Sunday range
                _, _, plan_week_start, plan_week_end = get_week_info_monday_to_sunday(
                    week_number=int(week_param), year=int(year_param)
                )
            else:
                # Infos use Friday-Friday range
                week_number, year, info_week_start, info_week_end = get_week_info_friday_to_friday()
                # Plans use Monday-Sunday range
                _, _, plan_week_start, plan_week_end = get_week_info_monday_to_sunday()
        except Exception:
            logging.exception("Error computing week bounds for team totals team_id=%s", team_id)
            return Response({"detail": "Invalid week parameters"}, status=status.HTTP_400_BAD_REQUEST)

        links = TeamMember.objects.filter(team=team)
        member_ids = links.values_list("ir_id", flat=True)
        from_date = request.GET.get("from_date")
        to_date = request.GET.get("to_date")

        info_qs = InfoDetail.objects.filter(ir_id__in=member_ids)
        plan_qs = PlanDetail.objects.filter(ir_id__in=member_ids)

        if from_date and to_date:
            info_qs = info_qs.filter(info_date__date__gte=parse_date(from_date), info_date__date__lte=parse_date(to_date))
            plan_qs = plan_qs.filter(plan_date__date__gte=parse_date(from_date), plan_date__date__lte=parse_date(to_date))
        else:
            # Use separate date ranges for infos and plans
            info_qs = info_qs.filter(info_date__gte=info_week_start, info_date__lte=info_week_end)
            plan_qs = plan_qs.filter(plan_date__gte=plan_week_start, plan_date__lte=plan_week_end)

        members_info_total = info_qs.count()
        members_plan_total = plan_qs.count()

        # per-member breakdown
        info_counts = {i["ir_id"]: i["total"] for i in info_qs.values("ir_id").annotate(total=Count("id"))}
        plan_counts = {p["ir_id"]: p["total"] for p in plan_qs.values("ir_id").annotate(total=Count("id"))}

        # Get week-specific UV counts from UVDetail records
        uv_counts = {}
        try:
            uv_records = UVDetail.objects.filter(
                ir_id__in=member_ids,
                uv_date__gte=info_week_start,
                uv_date__lte=info_week_end,
                uv_count__gt=0
            ).values('ir_id').annotate(total=Sum('uv_count'))
            for record in uv_records:
                uv_counts[record['ir_id']] = record['total'] or 0
        except Exception:
            # UVDetail table may not exist yet due to pending migrations
            pass

        # fetch ir data for members
        irs = Ir.objects.filter(ir_id__in=member_ids)
        ir_data_map = {ir.ir_id: ir for ir in irs}

        members = []
        total_uv_count = 0
        
        for ir_id in member_ids:
            ir = ir_data_map.get(ir_id)
            uv_count = uv_counts.get(ir_id, 0)
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
        week_param = request.GET.get("week")
        year_param = request.GET.get("year")
        
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
            
            # Calculate week info - UVs use Friday-Friday range
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
            
            # Calculate week-specific UV count from UVDetail records
            try:
                weekly_uv_count = UVDetail.objects.filter(
                    ir_id=ir_id,
                    uv_date__gte=week_start,
                    uv_date__lte=week_end,
                    uv_count__gt=0,
                ).aggregate(total=Sum('uv_count'))['total'] or 0

                uv_details = UVDetail.objects.filter(
                    ir_id=ir_id,
                    uv_date__gte=week_start,
                    uv_date__lte=week_end,
                    uv_count__gt=0,
                ).order_by('-uv_date')

                uv_records = UVDetailSerializer(uv_details, many=True).data
            except Exception:
                # UVDetail table may not exist yet due to pending migrations
                weekly_uv_count = 0
                uv_records = []
            
            # Get weekly UV target for this specific week
            weekly_target = WeeklyTarget.objects.filter(
                ir=ir,
                week_number=week_number,
                year=year
            ).first()
            
            return Response({
                "ir_id": ir.ir_id,
                "ir_name": ir.ir_name,
                "week_number": week_number,
                "year": year,
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "uv_count": weekly_uv_count,
                "cumulative_uv_count": ir.uv_count,
                "weekly_uv_target": (weekly_target.ir_weekly_uv_target if weekly_target else ir.weekly_uv_target) if ir.ir_access_level in [2, 3] else None,
                "uv_records": uv_records,
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

            # Build ir_id -> role once instead of re-querying `links` per member
            role_by_ir_id = dict(links.values_list("ir_id", "role"))

            team_uv_total = 0
            members = []

            for ir in irs:
                uv_count = ir.uv_count or 0
                team_uv_total += uv_count

                # Get member role
                role = role_by_ir_id.get(ir.ir_id)

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
        
        # Calculate week info - Infos use Friday 9:30 PM to next Friday 11:30 PM, Plans use Monday-Sunday
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
                # Infos use Friday-Friday range
                week_number, year, info_week_start, info_week_end = get_week_info_friday_to_friday(
                    week_number=week_number, year=year
                )
                # Plans use Monday-Sunday range
                _, _, plan_week_start, plan_week_end = get_week_info_monday_to_sunday(
                    week_number=week_number, year=year
                )
            except ValueError:
                return Response(
                    {"detail": "Invalid week or year parameter"},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            # Get current week
            week_number, year, info_week_start, info_week_end = get_week_info_friday_to_friday()
            _, _, plan_week_start, plan_week_end = get_week_info_monday_to_sunday()
        
        # ── Batch all per-team queries into bulk lookups ──────────────────────
        viewable_teams = list(
            viewable_teams.select_related('created_by')
                          .prefetch_related('teammember_set__ir')
        )
        team_ids = [t.id for t in viewable_teams]

        # Build team → member IR ids map (single prefetch, no extra queries)
        team_member_map = {}   # team_id -> [ir_id, ...]
        team_raw_members = {}  # team_id -> [TeamMember] (unfiltered, for is_member check)
        all_member_ids = set()
        for team in viewable_teams:
            all_ms = list(team.teammember_set.all())
            team_raw_members[team.id] = all_ms
            filtered = [
                m for m in all_ms
                if m.ir.status and not (m.role == TeamRole.LDC and m.ir == team.created_by)
            ]
            ids = [m.ir_id for m in filtered]
            team_member_map[team.id] = ids
            all_member_ids.update(ids)

        # Bulk InfoDetail counts per IR id (1 query)
        info_by_ir = dict(
            InfoDetail.objects.filter(
                ir_id__in=all_member_ids,
                info_date__gte=info_week_start,
                info_date__lte=info_week_end,
            ).values('ir_id').annotate(c=Count('id')).values_list('ir_id', 'c')
        )

        # Bulk PlanDetail counts per IR id (1 query)
        plan_by_ir = dict(
            PlanDetail.objects.filter(
                ir_id__in=all_member_ids,
                plan_date__gte=plan_week_start,
                plan_date__lte=plan_week_end,
            ).values('ir_id').annotate(c=Count('id')).values_list('ir_id', 'c')
        )

        # Bulk UV sums per IR id (1 query)
        try:
            uv_by_ir = dict(
                UVDetail.objects.filter(
                    ir_id__in=all_member_ids,
                    uv_date__gte=info_week_start,
                    uv_date__lte=info_week_end,
                ).values('ir_id').annotate(total=Sum('uv_count')).values_list('ir_id', 'total')
            )
        except Exception:
            uv_by_ir = {}

        # Bulk WeeklyTarget fetch for all teams (1 query)
        target_map = {
            t.team_id: t
            for t in WeeklyTarget.objects.filter(
                team_id__in=team_ids, week_number=week_number, year=year
            )
        }

        teams_data = []
        for team in viewable_teams:
            member_ids = team_member_map[team.id]
            info_achieved = sum(info_by_ir.get(i, 0) for i in member_ids)
            plan_achieved = sum(plan_by_ir.get(i, 0) for i in member_ids)
            uv_achieved   = float(sum(uv_by_ir.get(i, 0) or 0 for i in member_ids))

            team_target = target_map.get(team.id)
            can_edit = bool(team.created_by and ir.can_view_ir(team.created_by))
            is_member = any(m.ir_id == ir.ir_id for m in team_raw_members[team.id])

            teams_data.append({
                "team_id": team.id,
                "team_name": team.name,
                "created_by_id": team.created_by.ir_id if team.created_by else None,
                "created_by_name": team.created_by.ir_name if team.created_by else None,
                "member_count": len(member_ids),
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
                    "plan_week_start": plan_week_start.isoformat(),
                    "plan_week_end": plan_week_end.isoformat(),
                }
            })
        
        return Response({
            "ir_id": ir.ir_id,
            "ir_name": ir.ir_name,
            "hierarchy_level": ir.hierarchy_level,
            "week_number": week_number,
            "year": year,
            "week_start": info_week_start.isoformat(),
            "week_end": info_week_end.isoformat(),
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

        # Role-specific system count (no duplicates in any case):
        # ADMIN → every active IR in the database
        # CTC   → everyone in their downline (subtree below them)
        # LDC   → everyone in the teams they are a member of (excl. themselves)
        # Others → generic viewable count
        if ir.ir_access_level == AccessLevel.ADMIN:
            system_count = Ir.objects.filter(status=True).count()
        elif ir.ir_access_level == AccessLevel.CTC:
            system_count = ir.get_all_downlines().filter(status=True).count()
        elif ir.ir_access_level == AccessLevel.LDC:
            ldc_team_ids = (
                TeamMember.objects
                .filter(ir=ir)
                .values_list('team_id', flat=True)
            )
            system_count = (
                TeamMember.objects
                .filter(team_id__in=ldc_team_ids, ir__status=True)
                .exclude(ir=ir)
                .values('ir_id')
                .distinct()
                .count()
            )
        else:
            system_count = viewable_irs.filter(status=True).count()

        # Get current week info using Friday→Friday; accept optional week/year
        week_param = request.GET.get("week")
        year_param = request.GET.get("year")
        try:
            if week_param and year_param:
                week_number, year, week_start, week_end = get_week_info_friday_to_friday(
                    int(week_param), int(year_param)
                )
            else:
                week_number, year, week_start, week_end = get_week_info_friday_to_friday()
        except Exception:
            logging.exception("Error computing week bounds for downline ir_id=%s", ir_id)
            return Response({"detail": "Invalid week parameters"}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            "ir_id": ir.ir_id,
            "ir_name": ir.ir_name,
            "hierarchy_level": ir.hierarchy_level,
            "week_number": week_number,
            "year": year,
            "counts": {
                "total_viewable_irs": system_count,
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
# GET IRS FOR STATUS MANAGEMENT (LDC-only: subtree IRs w/ active status)
# ---------------------------------------------------
class GetIrsForStatusManagement(APIView):
    """
    Returns IRs belonging to teams the requester (LDC) is a member of, with
    their current active/inactive status, for the LDC "manage IR status"
    screen. Same scoping as the LDC branch of GetDownlineData's System
    Count, so this list matches what actually feeds that count.

    Supports search (?search=) and pagination (?limit=&offset=) so the
    client never has to load the full team roster up front.
    """
    def get(self, request, ir_id):
        try:
            requester = Ir.objects.get(ir_id=ir_id)
        except Ir.DoesNotExist:
            return Response({"detail": "IR not found"}, status=status.HTTP_404_NOT_FOUND)

        if requester.ir_access_level != AccessLevel.LDC:
            return Response({"detail": "Not authorized"}, status=status.HTTP_403_FORBIDDEN)

        search = (request.GET.get("search") or "").strip()
        try:
            limit = min(max(int(request.GET.get("limit", 15)), 1), 100)
            offset = max(int(request.GET.get("offset", 0)), 0)
        except (TypeError, ValueError):
            return Response({"detail": "Invalid limit/offset"}, status=status.HTTP_400_BAD_REQUEST)

        ldc_team_ids = TeamMember.objects.filter(ir=requester).values_list('team_id', flat=True)
        irs = (
            TeamMember.objects
            .filter(team_id__in=ldc_team_ids)
            .exclude(ir=requester)
            .values_list('ir_id', flat=True)
            .distinct()
        )
        queryset = Ir.objects.filter(ir_id__in=irs)

        if search:
            queryset = queryset.filter(Q(ir_name__icontains=search) | Q(ir_id__icontains=search))

        queryset = queryset.order_by("ir_name")
        total = queryset.count()
        page = queryset[offset:offset + limit].values("ir_id", "ir_name", "ir_access_level", "status")

        return Response({
            "results": list(page),
            "total": total,
            "has_more": offset + limit < total,
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

        # ── Fetch the whole subtree in one query, build the tree in-memory ──
        # (was up to 2 queries per node — get_direct_downlines() + .count() —
        # recursively, for every node in the tree).
        subtree = list(
            Ir.objects.filter(hierarchy_path__startswith=ir.hierarchy_path)
            .exclude(ir_id=ir.ir_id)
        )
        children_by_parent = {}
        for node in subtree:
            children_by_parent.setdefault(node.parent_ir_id, []).append(node)

        def build_tree(node, current_depth=0):
            """Recursively build tree structure from the in-memory subtree — no further queries."""
            children = children_by_parent.get(node.ir_id, [])

            if max_depth is not None and current_depth >= max_depth:
                return {
                    "ir_id": node.ir_id,
                    "ir_name": node.ir_name,
                    "hierarchy_level": node.hierarchy_level,
                    "ir_access_level": node.ir_access_level,
                    "info_count": node.info_count,
                    "plan_count": node.plan_count,
                    "uv_count": node.uv_count,
                    "children_count": len(children),
                    "children": f"... {len(children)} children (max_depth reached)"
                }

            return {
                "ir_id": node.ir_id,
                "ir_name": node.ir_name,
                "hierarchy_level": node.hierarchy_level,
                "ir_access_level": node.ir_access_level,
                "info_count": node.info_count,
                "plan_count": node.plan_count,
                "uv_count": node.uv_count,
                "children_count": len(children),
                "children": [build_tree(child, current_depth + 1) for child in children]
            }

        tree = build_tree(ir)

        return Response({
            "root_ir_id": ir.ir_id,
            "root_ir_name": ir.ir_name,
            "total_downlines": len(subtree),
            "max_depth_in_tree": max((n.hierarchy_level for n in subtree), default=0),
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


# ---------------------------------------------------
# SEARCH PROSPECTS (across accessible InfoDetail + PlanDetail)
# ---------------------------------------------------
class SearchProspects(APIView):
    """
    Full-text name search across InfoDetail and PlanDetail.

    GET /api/search_prospects/?q=<name>&requester_ir_id=<id>[&target_ir_id=<id>]

    When target_ir_id is provided the search is scoped to that single IR
    (provided the requester has permission to view it).  Without it the
    search covers all IRs the requester can view.
    """
    def get(self, request):
        query = request.query_params.get('q', '').strip()
        requester_ir_id = request.query_params.get('requester_ir_id', '').strip()
        target_ir_id = request.query_params.get('target_ir_id', '').strip()

        if not requester_ir_id:
            return Response({'message': 'requester_ir_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        if len(query) < 2:
            return Response({'infos': [], 'plans': [], 'query': query})

        try:
            requester = Ir.objects.get(ir_id=requester_ir_id)
        except Ir.DoesNotExist:
            return Response({'message': 'Requester IR not found'}, status=status.HTTP_404_NOT_FOUND)

        if target_ir_id:
            viewable_ir_ids = list(
                requester.get_viewable_irs()
                .filter(ir_id=target_ir_id)
                .values_list('ir_id', flat=True)
            )
            if not viewable_ir_ids:
                return Response({'infos': [], 'plans': [], 'query': query})
        else:
            viewable_ir_ids = list(requester.get_viewable_irs().values_list('ir_id', flat=True))

        infos = (
            InfoDetail.objects
            .filter(ir_id__in=viewable_ir_ids, info_name__icontains=query)
            .select_related('ir')
            .order_by('-info_date')[:50]
        )

        plans = (
            PlanDetail.objects
            .filter(ir_id__in=viewable_ir_ids, plan_name__icontains=query)
            .select_related('ir')
            .order_by('-plan_date')[:50]
        )

        return Response({
            'query': query,
            'infos': [
                {
                    'id': info.id,
                    'info_name': info.info_name,
                    'response': info.response,
                    'info_type': info.info_type,
                    'info_date': info.info_date.isoformat(),
                    'comments': info.comments,
                    'ir_id': info.ir.ir_id,
                    'ir_name': info.ir.ir_name,
                }
                for info in infos
            ],
            'plans': [
                {
                    'id': plan.id,
                    'plan_name': plan.plan_name,
                    'status': plan.status,
                    'plan_date': plan.plan_date.isoformat(),
                    'comments': plan.comments,
                    'ir_id': plan.ir.ir_id,
                    'ir_name': plan.ir.ir_name,
                }
                for plan in plans
            ],
        })

