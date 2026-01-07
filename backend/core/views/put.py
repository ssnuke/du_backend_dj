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
    AccessLevel,
)
from core.serializers import (
    InfoDetailSerializer,
    PlanDetailSerializer,
    TeamSerializer,
)
from django.contrib.auth.hashers import make_password

import logging


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
