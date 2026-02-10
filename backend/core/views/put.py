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
    Pocket,
    PocketMember,
    TeamRole,
    InfoDetail,
    InfoType,
    PlanDetail,
    UVDetail,
    WeeklyTarget,
    Notification,
    AccessLevel,
)
from core.utils.notifications import get_notification_recipients, create_notifications
from core.serializers import (
    InfoDetailSerializer,
    PlanDetailSerializer,
    TeamSerializer,
)
from django.contrib.auth.hashers import make_password

import logging
from datetime import datetime
import pytz


# ---------------------------------------------------
# UPDATE IR DETAILS (with role-based check)
# ---------------------------------------------------
class UpdateIrDetails(APIView):
    """
    Mirrors FastAPI PUT /{update_ir}
    Updates IR details like name, access level, password, and targets.
    """
    def put(self, request, update_ir):
        # Get the IR to update
        ir = get_object_or_404(Ir, ir_id=update_ir)
        
        # Get the acting IR for authorization
        acting_ir_id = request.data.get("acting_ir_id")
        if not acting_ir_id:
            return Response(
                {"detail": "acting_ir_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        acting_ir = get_object_or_404(Ir, ir_id=acting_ir_id)
        
        # Only ADMIN/CTC can update other IRs (self can always update own data)
        if acting_ir.ir_id != ir.ir_id:
            if not acting_ir.has_full_access():
                return Response(
                    {"detail": "Not authorized to update other IR's details. Only ADMIN/CTC can do this."},
                    status=status.HTTP_403_FORBIDDEN
                )
        
        updated_fields = {}
        
        # Update ir_name
        if "ir_name" in request.data:
            ir.ir_name = request.data["ir_name"]
            updated_fields["ir_name"] = ir.ir_name
        
        # Update access level (only ADMIN can change access levels)
        if "ir_access_level" in request.data:
            if acting_ir.ir_access_level != AccessLevel.ADMIN:
                return Response(
                    {"detail": "Only ADMIN can change IR access levels"},
                    status=status.HTTP_403_FORBIDDEN
                )
            new_level = request.data["ir_access_level"]
            if new_level not in [1, 2, 3, 4, 5, 6]:
                return Response(
                    {"detail": "Invalid access level. Must be 1-6"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            ir.ir_access_level = new_level
            updated_fields["ir_access_level"] = ir.ir_access_level
        
        # Update password
        if "password" in request.data:
            ir.ir_password = make_password(request.data["password"])
            updated_fields["password"] = "updated"
        
        # Update targets
        if "weekly_info_target" in request.data:
            ir.weekly_info_target = request.data["weekly_info_target"]
            updated_fields["weekly_info_target"] = ir.weekly_info_target
        
        if "weekly_plan_target" in request.data:
            ir.weekly_plan_target = request.data["weekly_plan_target"]
            updated_fields["weekly_plan_target"] = ir.weekly_plan_target
        
        if "weekly_uv_target" in request.data:
            if ir.ir_access_level in [2, 3]:
                ir.weekly_uv_target = request.data["weekly_uv_target"]
                updated_fields["weekly_uv_target"] = ir.weekly_uv_target
        
        ir.save()
        
        return Response(
            {
                "message": "IR details updated successfully",
                "ir_id": ir.ir_id,
                "updated_fields": updated_fields
            },
            status=status.HTTP_200_OK
        )


# ---------------------------------------------------
# UPDATE PARENT IR (without remapping children paths)
# ---------------------------------------------------
class UpdateParentIR(APIView):
    """
    Updates the parent_ir of an IR without remapping children's paths.
    Only the target IR's hierarchy_path and hierarchy_level are updated.
    Children retain their existing parent_ir (still pointing to this IR).
    
    Request body:
    - acting_ir_id: The IR performing the action (required)
    - new_parent_ir_id: The new parent IR ID (optional, null to make root)
    """
    def put(self, request, ir_id):
        # Get the IR to update
        ir = get_object_or_404(Ir, ir_id=ir_id)
        
        # Get the acting IR for authorization
        acting_ir_id = request.data.get("acting_ir_id")
        if not acting_ir_id:
            return Response(
                {"detail": "acting_ir_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        acting_ir = get_object_or_404(Ir, ir_id=acting_ir_id)
        
        # Only ADMIN/CTC can change parent relationships
        if not acting_ir.has_full_access():
            return Response(
                {"detail": "Not authorized. Only ADMIN/CTC can change parent relationships."},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Get the new parent IR (can be null to make this IR a root)
        new_parent_ir_id = request.data.get("new_parent_ir_id")
        new_parent = None
        
        if new_parent_ir_id:
            new_parent = get_object_or_404(Ir, ir_id=new_parent_ir_id)
            
            # Prevent setting parent to self
            if new_parent.ir_id == ir.ir_id:
                return Response(
                    {"detail": "Cannot set an IR as its own parent"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Prevent circular reference - new parent cannot be a descendant
            if ir.is_in_subtree(new_parent):
                return Response(
                    {"detail": "Cannot set a descendant as parent (would create circular reference)"},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        old_parent_id = ir.parent_ir.ir_id if ir.parent_ir else None
        old_path = ir.hierarchy_path
        old_level = ir.hierarchy_level
        
        # Update the parent (save() will recalculate path and level for this IR only)
        ir.parent_ir = new_parent
        ir.save()
        
        return Response(
            {
                "message": "Parent IR updated successfully",
                "ir_id": ir.ir_id,
                "old_parent_ir_id": old_parent_id,
                "new_parent_ir_id": new_parent.ir_id if new_parent else None,
                "old_hierarchy_path": old_path,
                "new_hierarchy_path": ir.hierarchy_path,
                "old_hierarchy_level": old_level,
                "new_hierarchy_level": ir.hierarchy_level,
                "note": "Children paths were NOT remapped. They still point to this IR."
            },
            status=status.HTTP_200_OK
        )


# ---------------------------------------------------
# UPDATE INFO DETAIL (with role-based check)
# ---------------------------------------------------
class UpdateInfoDetail(APIView):
    """
    Mirrors PUT /update_info_detail/{info_id}
    """
    def put(self, request, info_id):
        info = get_object_or_404(InfoDetail, id=info_id)
        
        # Role-based check if requester provided
        requester_ir_id = request.data.get("requester_ir_id")
        if requester_ir_id:
            try:
                requester = Ir.objects.get(ir_id=requester_ir_id)
                # Requester must be able to add data for this IR
                if not requester.can_add_data_for_ir(info.ir):
                    return Response(
                        {"detail": "Not authorized to update this info detail"},
                        status=status.HTTP_403_FORBIDDEN
                    )
            except Ir.DoesNotExist:
                return Response(
                    {"detail": "Requester IR not found"},
                    status=status.HTTP_404_NOT_FOUND
                )

        serializer = InfoDetailSerializer(
            info,
            data=request.data,
            partial=True
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
# UPDATE PLAN DETAIL (with role-based check)
# ---------------------------------------------------
class UpdatePlanDetail(APIView):
    """
    Mirrors PUT /update_plan_detail/{plan_id}
    """
    def put(self, request, plan_id):
        try:
            plan = get_object_or_404(PlanDetail, id=plan_id)
            
            # Role-based check if requester provided
            requester_ir_id = request.data.get("requester_ir_id")
            if requester_ir_id:
                try:
                    requester = Ir.objects.get(ir_id=requester_ir_id)
                    # Requester must be able to add data for this IR
                    if not requester.can_add_data_for_ir(plan.ir):
                        return Response(
                            {"detail": "Not authorized to update this plan detail"},
                            status=status.HTTP_403_FORBIDDEN
                        )
                except Ir.DoesNotExist:
                    return Response(
                        {"detail": "Requester IR not found"},
                        status=status.HTTP_404_NOT_FOUND
                    )

            serializer = PlanDetailSerializer(
                plan,
                data=request.data,
                partial=True
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()

            recipients = get_notification_recipients(plan.ir)
            create_notifications(
                recipients=recipients,
                title="Plan Updated",
                message=f"{plan.ir.ir_name} ({plan.ir.ir_id}) updated a plan: {plan.plan_name or 'Plan' }.",
                notification_type=Notification.Type.PLAN_UPDATED,
                related_object_id=str(plan.id),
            )

            return Response(
                {
                    "message": "Plan detail updated",
                    "plan_id": plan.id
                },
                status=status.HTTP_200_OK
            )
        except PlanDetail.DoesNotExist:
            return Response({"detail": "Plan detail not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception:
            logging.exception("Error updating plan detail with id=%s", plan_id)
            return Response({"detail": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------------------------------------------------
# SET TARGETS (PUT VERSION – with role-based check)
# ---------------------------------------------------
class SetTargetsPut(APIView):
    """
    Mirrors PUT /set_targets
    """
    def put(self, request):
        acting_ir_id = request.data.get("acting_ir_id")
        payload = request.data

        acting_ir = get_object_or_404(Ir, ir_id=acting_ir_id)

        # Only ADMIN, CTC, LDC can set targets
        if acting_ir.ir_access_level not in [1, 2, 3]:
            return Response(
                {"detail": "Not authorized to set targets. Only ADMIN, CTC, LDC can set targets."},
                status=status.HTTP_403_FORBIDDEN
            )

        updated = {}

        # Update IR targets
        if payload.get("ir_id"):
            ir = get_object_or_404(Ir, ir_id=payload["ir_id"])
            
            # Role-based check: acting IR must be able to add data for target IR
            if not acting_ir.can_add_data_for_ir(ir):
                return Response(
                    {"detail": "Not authorized to set targets for this IR"},
                    status=status.HTTP_403_FORBIDDEN
                )

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
            
            # Role-based check: acting IR must be able to edit team
            if not acting_ir.can_edit_team(team):
                return Response(
                    {"detail": "Not authorized to set targets for this team"},
                    status=status.HTTP_403_FORBIDDEN
                )

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
# UPDATE WEEKLY TARGETS (PATCH) - Update Info/Plan/UV targets
# ---------------------------------------------------
class UpdateWeeklyTargets(APIView):
    """
    Update weekly info, plan, and UV targets for the current week.
    Only updates existing WeeklyTarget records for IR or Team.
    """
    def patch(self, request):
        from core.utils.dates import get_saturday_friday_week_info
        from core.models import WeeklyTarget
        import logging
        
        acting_ir_id = request.data.get("acting_ir_id")
        if not acting_ir_id:
            return Response(
                {"detail": "acting_ir_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        acting_ir = get_object_or_404(Ir, ir_id=acting_ir_id)
        
        # Only ADMIN, CTC, LDC can update targets
        if acting_ir.ir_access_level not in [1, 2, 3]:
            return Response(
                {"detail": "Not authorized. Only ADMIN, CTC, and LDC can update targets"},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Get current week info
        week_number, year, week_start, week_end = get_saturday_friday_week_info()
        
        updated = {
            "week_number": week_number,
            "year": year,
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat()
        }
        
        try:
            # Update IR weekly targets
            if request.data.get("ir_id"):
                ir = get_object_or_404(Ir, ir_id=request.data["ir_id"])
                
                # Permission check
                if not acting_ir.can_add_data_for_ir(ir):
                    return Response(
                        {"detail": "Not authorized to update targets for this IR"},
                        status=status.HTTP_403_FORBIDDEN
                    )
                
                # Get existing weekly target
                try:
                    weekly_target = WeeklyTarget.objects.get(
                        ir=ir,
                        week_number=week_number,
                        year=year
                    )
                except WeeklyTarget.DoesNotExist:
                    return Response(
                        {"detail": "No weekly target found for this IR in current week"},
                        status=status.HTTP_404_NOT_FOUND
                    )
                
                # Update only if provided
                if request.data.get("weekly_info_target") is not None:
                    try:
                        weekly_target.ir_weekly_info_target = int(request.data["weekly_info_target"])
                    except (ValueError, TypeError):
                        weekly_target.ir_weekly_info_target = request.data["weekly_info_target"]
                
                if request.data.get("weekly_plan_target") is not None:
                    try:
                        weekly_target.ir_weekly_plan_target = int(request.data["weekly_plan_target"])
                    except (ValueError, TypeError):
                        weekly_target.ir_weekly_plan_target = request.data["weekly_plan_target"]
                
                # Update UV target (only for CTC and LDC)
                if request.data.get("weekly_uv_target") is not None and ir.ir_access_level in [2, 3]:
                    try:
                        weekly_target.ir_weekly_uv_target = int(request.data["weekly_uv_target"])
                    except (ValueError, TypeError):
                        weekly_target.ir_weekly_uv_target = request.data["weekly_uv_target"]
                
                weekly_target.save()
                updated["ir_id"] = ir.ir_id
                updated["ir_weekly_info_target"] = weekly_target.ir_weekly_info_target
                updated["ir_weekly_plan_target"] = weekly_target.ir_weekly_plan_target
                if ir.ir_access_level in [2, 3]:
                    updated["ir_weekly_uv_target"] = weekly_target.ir_weekly_uv_target
            
            # Update Team weekly targets
            if request.data.get("team_id"):
                team_id_raw = request.data.get("team_id")
                try:
                    team_id_val = int(team_id_raw)
                except (ValueError, TypeError):
                    team_id_val = team_id_raw
                
                team = get_object_or_404(Team, id=team_id_val)
                
                # Permission check
                if not acting_ir.can_edit_team(team):
                    return Response(
                        {"detail": "Not authorized to update targets for this team"},
                        status=status.HTTP_403_FORBIDDEN
                    )
                
                # Get existing weekly target
                try:
                    weekly_target = WeeklyTarget.objects.get(
                        team=team,
                        week_number=week_number,
                        year=year
                    )
                except WeeklyTarget.DoesNotExist:
                    return Response(
                        {"detail": "No weekly target found for this team in current week"},
                        status=status.HTTP_404_NOT_FOUND
                    )
                
                # Update only if provided
                if request.data.get("team_weekly_info_target") is not None:
                    try:
                        weekly_target.team_weekly_info_target = int(request.data["team_weekly_info_target"])
                    except (ValueError, TypeError):
                        weekly_target.team_weekly_info_target = request.data["team_weekly_info_target"]
                
                if request.data.get("team_weekly_plan_target") is not None:
                    try:
                        weekly_target.team_weekly_plan_target = int(request.data["team_weekly_plan_target"])
                    except (ValueError, TypeError):
                        weekly_target.team_weekly_plan_target = request.data["team_weekly_plan_target"]
                
                # Update UV target for team
                if request.data.get("team_weekly_uv_target") is not None:
                    try:
                        weekly_target.team_weekly_uv_target = int(request.data["team_weekly_uv_target"])
                    except (ValueError, TypeError):
                        weekly_target.team_weekly_uv_target = request.data["team_weekly_uv_target"]
                
                weekly_target.save()
                updated["team_id"] = team.id
                updated["team_weekly_info_target"] = weekly_target.team_weekly_info_target
                updated["team_weekly_plan_target"] = weekly_target.team_weekly_plan_target
                updated["team_weekly_uv_target"] = weekly_target.team_weekly_uv_target
            
            return Response(
                {"message": "Weekly targets updated successfully", "updated": updated},
                status=status.HTTP_200_OK
            )
        
        except Exception as e:
            logging.exception("Error updating weekly targets")
            return Response(
                {"detail": "Internal server error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# ---------------------------------------------------
# UPDATE TEAM NAME (PATCH) (with role-based check)
# ---------------------------------------------------
class UpdateTeamName(APIView):
    """
    Mirrors PATCH /update_team_name/{team_id}
    """
    def patch(self, request, team_id):
        team = get_object_or_404(Team, id=team_id)
        
        # Role-based check if requester provided
        requester_ir_id = request.data.get("requester_ir_id")
        if requester_ir_id:
            try:
                requester = Ir.objects.get(ir_id=requester_ir_id)
                # Requester must be able to edit this team
                if not requester.can_edit_team(team):
                    return Response(
                        {"detail": "Not authorized to update this team"},
                        status=status.HTTP_403_FORBIDDEN
                    )
            except Ir.DoesNotExist:
                return Response(
                    {"detail": "Requester IR not found"},
                    status=status.HTTP_404_NOT_FOUND
                )
        
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


# ---------------------------------------------------
# TRANSFER TEAM OWNERSHIP (PUT) (with role-based check)
# ---------------------------------------------------
class TransferTeamOwnership(APIView):
    """
    Transfer team ownership to an LDC member in the team.

    Request body:
    - requester_ir_id: IR performing the action (required)
    - new_owner_ir_id: IR to set as team owner (required)
    """
    def put(self, request, team_id):
        team = get_object_or_404(Team, id=team_id)

        requester_ir_id = request.data.get("requester_ir_id")
        new_owner_ir_id = request.data.get("new_owner_ir_id")

        if not requester_ir_id:
            return Response(
                {"detail": "requester_ir_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not new_owner_ir_id:
            return Response(
                {"detail": "new_owner_ir_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        requester = get_object_or_404(Ir, ir_id=requester_ir_id)
        new_owner = get_object_or_404(Ir, ir_id=new_owner_ir_id)

        if team.created_by and new_owner.ir_id == team.created_by.ir_id:
            return Response(
                {"detail": "IR is already the team owner"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Only current owner or ADMIN/CTC can transfer ownership
        if team.created_by:
            if requester.ir_id != team.created_by.ir_id and not requester.has_full_access():
                return Response(
                    {"detail": "Not authorized to transfer ownership of this team"},
                    status=status.HTTP_403_FORBIDDEN
                )
        else:
            if not requester.has_full_access():
                return Response(
                    {"detail": "Not authorized to transfer ownership of this team"},
                    status=status.HTTP_403_FORBIDDEN
                )

        # Ensure new owner is an LDC member of this team
        is_ldc_member = TeamMember.objects.filter(
            team=team,
            ir=new_owner,
            role=TeamRole.LDC
        ).exists()

        if not is_ldc_member:
            return Response(
                {"detail": "New owner must be an LDC member of this team"},
                status=status.HTTP_400_BAD_REQUEST
            )

        old_owner = team.created_by
        team.created_by = new_owner
        team.save()

        return Response(
            {
                "message": "Team ownership transferred successfully",
                "team_id": team.id,
                "old_owner_id": old_owner.ir_id if old_owner else None,
                "old_owner_name": old_owner.ir_name if old_owner else None,
                "new_owner_id": new_owner.ir_id,
                "new_owner_name": new_owner.ir_name,
            },
            status=status.HTTP_200_OK
        )


# ---------------------------------------------------
# UPDATE IR NAME (PATCH) (with role-based check)
# ---------------------------------------------------
class UpdateIrName(APIView):
    """
    Update IR name. Only ADMIN/CTC can update other IR's names.
    IRs can update their own names.
    """
    def patch(self, request, ir_id):
        ir = get_object_or_404(Ir, ir_id=ir_id)
        
        # Role-based check if requester provided
        requester_ir_id = request.data.get("requester_ir_id")
        if requester_ir_id:
            try:
                requester = Ir.objects.get(ir_id=requester_ir_id)
                # Requester must be ADMIN/CTC or updating their own name
                if requester.ir_id != ir.ir_id and not requester.has_full_access():
                    return Response(
                        {"detail": "Not authorized to update this IR's name"},
                        status=status.HTTP_403_FORBIDDEN
                    )
            except Ir.DoesNotExist:
                return Response(
                    {"detail": "Requester IR not found"},
                    status=status.HTTP_404_NOT_FOUND
                )
        
        old_name = ir.ir_name

        new_name = request.data.get("name")
        if not new_name:
            return Response(
                {"detail": "IR name is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        ir.ir_name = new_name
        ir.save()

        return Response(
            {
                "message": "IR name updated",
                "ir_id": ir.ir_id,
                "old_name": old_name,
                "new_name": ir.ir_name
            },
            status=status.HTTP_200_OK
        )


# ---------------------------------------------------
# UPDATE IR ID (PUT) (with role-based check)
# ---------------------------------------------------
class UpdateIrId(APIView):
    """
    Update an IR's primary ID and rewire all references.

    Request body:
    - requester_ir_id: IR performing the action (required)
    - current_ir_id: existing IR ID to change (required)
    - new_ir_id: new IR ID to set (required)
    """
    def put(self, request):
        requester_ir_id = request.data.get("requester_ir_id")
        current_ir_id = request.data.get("current_ir_id")
        new_ir_id = request.data.get("new_ir_id")

        if not requester_ir_id:
            return Response(
                {"detail": "requester_ir_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not current_ir_id or not new_ir_id:
            return Response(
                {"detail": "current_ir_id and new_ir_id are required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        requester = get_object_or_404(Ir, ir_id=requester_ir_id)

        if not requester.has_full_access():
            return Response(
                {"detail": "Not authorized to update IR IDs"},
                status=status.HTTP_403_FORBIDDEN
            )

        if current_ir_id == new_ir_id:
            return Response(
                {"detail": "current_ir_id and new_ir_id must be different"},
                status=status.HTTP_400_BAD_REQUEST
            )

        ir = get_object_or_404(Ir, ir_id=current_ir_id)

        if Ir.objects.filter(ir_id=new_ir_id).exists():
            return Response(
                {"detail": "new_ir_id already exists"},
                status=status.HTTP_400_BAD_REQUEST
            )

        def refresh_subtree_paths(root_ir):
            queue = list(Ir.objects.filter(parent_ir=root_ir))
            while queue:
                node = queue.pop(0)
                node.save()
                queue.extend(Ir.objects.filter(parent_ir=node))

        try:
            with transaction.atomic():
                # Create a new IR with the new ID
                new_ir = Ir.objects.create(
                    ir_id=new_ir_id,
                    ir_name=ir.ir_name,
                    ir_email=ir.ir_email,
                    ir_access_level=ir.ir_access_level,
                    ir_password=ir.ir_password,
                    status=ir.status,
                    parent_ir=ir.parent_ir,
                    plan_count=ir.plan_count,
                    dr_count=ir.dr_count,
                    info_count=ir.info_count,
                    name_list=ir.name_list,
                    uv_count=ir.uv_count,
                    weekly_info_target=ir.weekly_info_target,
                    weekly_plan_target=ir.weekly_plan_target,
                    weekly_uv_target=ir.weekly_uv_target,
                )

                # Preserve started_date
                Ir.objects.filter(ir_id=new_ir_id).update(started_date=ir.started_date)

                # Update references
                Team.objects.filter(created_by=ir).update(created_by=new_ir)
                TeamMember.objects.filter(ir=ir).update(ir=new_ir)
                Pocket.objects.filter(created_by=ir).update(created_by=new_ir)
                PocketMember.objects.filter(ir=ir).update(ir=new_ir)
                PocketMember.objects.filter(added_by=ir).update(added_by=new_ir)
                InfoDetail.objects.filter(ir=ir).update(ir=new_ir)
                PlanDetail.objects.filter(ir=ir).update(ir=new_ir)
                UVDetail.objects.filter(ir=ir).update(ir=new_ir)
                WeeklyTarget.objects.filter(ir=ir).update(ir=new_ir)
                Notification.objects.filter(recipient=ir).update(recipient=new_ir)

                # Update hierarchy references
                Ir.objects.filter(parent_ir=ir).update(parent_ir=new_ir)

                # Recalculate hierarchy paths for new IR subtree
                new_ir.save()
                refresh_subtree_paths(new_ir)

                # Remove old IR
                ir.delete()

                return Response(
                    {
                        "message": "IR ID updated successfully",
                        "old_ir_id": current_ir_id,
                        "new_ir_id": new_ir_id,
                    },
                    status=status.HTTP_200_OK
                )
        except Exception:
            logging.exception("Error updating IR ID")
            return Response(
                {"detail": "Internal server error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# ---------------------------------------------------
# UPDATE UV DETAIL RECORD (with hierarchy check)
# ---------------------------------------------------
class UpdateUVCount(APIView):
    """
    Update a specific UV detail record by ID.
    Allows updating: uv_count, prospect_name, comments, ir_name (optional)
    """
    def put(self, request, uv_id):
        requester_ir_id = request.data.get("requester_ir_id")
        
        try:
            # Get the UV detail record
            uv_detail = get_object_or_404(UVDetail, id=uv_id)
            ir = uv_detail.ir
            
            # Check hierarchy permission if requester provided
            if requester_ir_id:
                try:
                    requester = Ir.objects.get(ir_id=requester_ir_id)
                    if not requester.can_view_ir(ir):
                        return Response(
                            {"detail": "Not authorized to update this UV record"},
                            status=status.HTTP_403_FORBIDDEN
                        )
                except Ir.DoesNotExist:
                    return Response(
                        {"detail": "Requester IR not found"},
                        status=status.HTTP_404_NOT_FOUND
                    )
            
            # Get fields to update (all optional)
            uv_count = request.data.get("uv_count")
            prospect_name = request.data.get("prospect_name")
            comments = request.data.get("comments")
            ir_name = request.data.get("ir_name")
            uv_date = request.data.get("uv_date")
            
            # Update fields if provided
            if uv_count is not None:
                try:
                    uv_detail.uv_count = float(uv_count)
                except (ValueError, TypeError):
                    return Response(
                        {"detail": "uv_count must be a valid number (integer or decimal)"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            
            if prospect_name is not None:
                uv_detail.prospect_name = str(prospect_name).strip()
            
            if comments is not None:
                uv_detail.comments = str(comments).strip() if comments else ""
            
            if ir_name is not None:
                uv_detail.ir_name = str(ir_name).strip()

            if uv_date is not None:
                try:
                    # Accept YYYY-MM-DD or full ISO; localize to IST if date-only
                    ist = pytz.timezone("Asia/Kolkata")
                    if len(str(uv_date)) == 10:
                        parsed = datetime.strptime(str(uv_date), "%Y-%m-%d")
                        uv_detail.uv_date = ist.localize(parsed)
                    else:
                        parsed_dt = datetime.fromisoformat(str(uv_date))
                        if parsed_dt.tzinfo is None:
                            uv_detail.uv_date = ist.localize(parsed_dt)
                        else:
                            uv_detail.uv_date = parsed_dt.astimezone(ist)
                except Exception:
                    return Response(
                        {"detail": "uv_date must be a valid date (YYYY-MM-DD or ISO datetime)"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            
            # Save the updated record
            uv_detail.save()

            recipients = get_notification_recipients(ir)
            create_notifications(
                recipients=recipients,
                title="UV Record Updated",
                message=f"{ir.ir_name} ({ir.ir_id}) updated a UV record for '{uv_detail.prospect_name or 'Prospect'}'.",
                notification_type=Notification.Type.UV_UPDATED,
                related_object_id=str(uv_detail.id),
            )
            
            return Response(
                {
                    "message": "UV record updated successfully",
                    "id": uv_detail.id,
                    "ir_id": uv_detail.ir_id,
                    "ir_name": uv_detail.ir_name,
                    "prospect_name": uv_detail.prospect_name,
                    "uv_count": uv_detail.uv_count,
                    "uv_date": uv_detail.uv_date.isoformat() if uv_detail.uv_date else None,
                    "comments": uv_detail.comments
                },
                status=status.HTTP_200_OK
            )
        
        except UVDetail.DoesNotExist:
            return Response(
                {"detail": "UV record not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception:
            logging.exception("Error updating UV record id=%s", uv_id)
            return Response(
                {"detail": "Internal server error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
