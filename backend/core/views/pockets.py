"""
Pocket-related views for team sub-grouping and target allocation.
Handles CRUD operations for pockets and pocket members.
"""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.db import transaction
from django.shortcuts import get_object_or_404

from core.models import (
    Ir, Team, Pocket, PocketMember, WeeklyTarget, 
    AccessLevel, TeamRole
)
from core.serializers import (
    PocketSerializer, PocketDetailedSerializer, PocketMemberSerializer
)


class CreatePocket(APIView):
    """
    Create a new pocket within a team.
    Allowed: LDC (who created team) or ADMIN/CTC
    """
    def post(self, request):
        try:
            requester_id = request.data.get("requester_ir_id")
            requester = get_object_or_404(Ir, ir_id=requester_id)
            
            team_id = request.data.get("team_id")
            pocket_name = request.data.get("pocket_name")
            
            if not team_id or not pocket_name:
                return Response(
                    {"error": "team_id and pocket_name are required"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            team = get_object_or_404(Team, id=team_id)
            
            # Permission check: Can user edit this team?
            if not requester.can_edit_team(team):
                return Response(
                    {"error": "Not authorized to create pockets in this team"},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            # Check if pocket name already exists in this team
            if Pocket.objects.filter(team=team, name=pocket_name).exists():
                return Response(
                    {"error": "Pocket with this name already exists in the team"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Create pocket
            pocket = Pocket.objects.create(
                team=team,
                name=pocket_name,
                created_by=requester
            )
            
            serializer = PocketSerializer(pocket)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class GetPockets(APIView):
    """
    Get all pockets in a team.
    Allowed: LS and above who can view the team
    """
    def get(self, request, team_id):
        try:
            requester_id = request.query_params.get("requester_ir_id")
            requester = get_object_or_404(Ir, ir_id=requester_id)
            
            team = get_object_or_404(Team, id=team_id)
            
            # Permission check: Can user view this team?
            if not requester.can_view_team(team):
                # Allow GC/IR to view only if they're a pocket head in this team
                is_pocket_head = PocketMember.objects.filter(
                    pocket__team=team,  # Check through the pocket relationship
                    ir=requester, 
                    is_head=True
                ).exists()
                if not is_pocket_head:
                    return Response(
                        {"error": "Not authorized to view this team"},
                        status=status.HTTP_403_FORBIDDEN
                    )
            
            # For GC/IR (non-LS+): restrict to pockets where requester is a pocket head
            if requester.ir_access_level > AccessLevel.LS:
                pockets = Pocket.objects.filter(
                    team=team,
                    is_active=True,
                    members__ir=requester,
                    members__is_head=True
                ).distinct().order_by('name')
            else:
                pockets = Pocket.objects.filter(team=team, is_active=True).order_by('name')
            serializer = PocketDetailedSerializer(pockets, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
        
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class GetPocketDetail(APIView):
    """
    Get detailed info about a specific pocket including all members.
    Allowed: LS and above who can view the team
    """
    def get(self, request, pocket_id):
        try:
            requester_id = request.query_params.get("requester_ir_id")
            requester = get_object_or_404(Ir, ir_id=requester_id)
            
            pocket = get_object_or_404(Pocket, id=pocket_id)
            
            # Permission check: Can user view the team?
            if not requester.can_view_team(pocket.team):
                # Allow GC/IR if they're the pocket head
                is_pocket_head = PocketMember.objects.filter(
                    pocket=pocket,
                    ir=requester,
                    is_head=True
                ).exists()
                if not is_pocket_head:
                    return Response(
                        {"error": "Not authorized to view this pocket"},
                        status=status.HTTP_403_FORBIDDEN
                    )
            
            # For GC/IR: only allow if they're the pocket head
            if requester.ir_access_level > AccessLevel.LS:
                if not PocketMember.objects.filter(pocket=pocket, ir=requester, is_head=True).exists():
                    return Response(
                        {"error": "Only pocket heads can view their pocket details"},
                        status=status.HTTP_403_FORBIDDEN
                    )
            
            serializer = PocketDetailedSerializer(pocket)
            return Response(serializer.data, status=status.HTTP_200_OK)
        
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class UpdatePocket(APIView):
    """
    Update pocket details (name, active status).
    Allowed: LDC (who created team) or ADMIN/CTC
    """
    def put(self, request, pocket_id):
        try:
            requester_id = request.data.get("requester_ir_id")
            requester = get_object_or_404(Ir, ir_id=requester_id)
            
            pocket = get_object_or_404(Pocket, id=pocket_id)
            
            # Permission check: Can user edit the team?
            if not requester.can_edit_team(pocket.team):
                return Response(
                    {"error": "Not authorized to update this pocket"},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            # Update fields if provided
            if "pocket_name" in request.data:
                # Check for duplicate names
                new_name = request.data.get("pocket_name")
                if Pocket.objects.filter(
                    team=pocket.team, 
                    name=new_name
                ).exclude(id=pocket.id).exists():
                    return Response(
                        {"error": "Pocket with this name already exists in the team"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                pocket.name = new_name
            
            if "is_active" in request.data:
                pocket.is_active = request.data.get("is_active")
            
            pocket.save()
            serializer = PocketSerializer(pocket)
            return Response(serializer.data, status=status.HTTP_200_OK)
        
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class DeletePocket(APIView):
    """
    Delete a pocket (soft delete via is_active flag).
    Allowed: LDC (who created team) or ADMIN/CTC
    """
    def delete(self, request, pocket_id):
        try:
            requester_id = request.data.get("requester_ir_id")
            requester = get_object_or_404(Ir, ir_id=requester_id)
            
            pocket = get_object_or_404(Pocket, id=pocket_id)
            
            # Permission check: Can user edit the team?
            if not requester.can_edit_team(pocket.team):
                return Response(
                    {"error": "Not authorized to delete this pocket"},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            # Hard delete - also deletes related PocketMembers due to CASCADE
            pocket.delete()
            
            return Response(
                {"message": "Pocket deleted successfully"},
                status=status.HTTP_200_OK
            )
        
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class AddMemberToPocket(APIView):
    """
    Add an IR to a pocket.
    Allowed: LDC (who created team) or ADMIN/CTC
    """
    def post(self, request):
        try:
            requester_id = request.data.get("requester_ir_id")
            requester = get_object_or_404(Ir, ir_id=requester_id)
            
            pocket_id = request.data.get("pocket_id")
            ir_id = request.data.get("ir_id")
            role = request.data.get("role", TeamRole.IR)
            
            if not pocket_id or not ir_id:
                return Response(
                    {"error": "pocket_id and ir_id are required"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            pocket = get_object_or_404(Pocket, id=pocket_id)
            ir = get_object_or_404(Ir, ir_id=ir_id)
            
            # Permission check: Can user edit the team?
            if not requester.can_edit_team(pocket.team):
                return Response(
                    {"error": "Not authorized to add members to this pocket"},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            # Check if member already exists
            if PocketMember.objects.filter(pocket=pocket, ir=ir).exists():
                return Response(
                    {"error": "IR is already a member of this pocket"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Validate role
            valid_roles = [choice[0] for choice in TeamRole.choices]
            if role not in valid_roles:
                return Response(
                    {"error": f"Invalid role. Valid roles: {valid_roles}"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Create membership
            # First member becomes the pocket head
            is_first_member = not PocketMember.objects.filter(pocket=pocket).exists()
            
            member = PocketMember.objects.create(
                pocket=pocket,
                ir=ir,
                team=pocket.team,
                role=role,
                added_by=requester,
                is_head=is_first_member
            )
            
            serializer = PocketMemberSerializer(member)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class RemoveMemberFromPocket(APIView):
    """
    Remove an IR from a pocket.
    Allowed: LDC (who created team) or ADMIN/CTC
    """
    def delete(self, request):
        try:
            requester_id = request.data.get("requester_ir_id")
            requester = get_object_or_404(Ir, ir_id=requester_id)
            
            pocket_member_id = request.data.get("pocket_member_id")
            
            member = get_object_or_404(PocketMember, id=pocket_member_id)
            pocket = member.pocket
            
            # Permission check: Can user edit the team?
            if not requester.can_edit_team(pocket.team):
                return Response(
                    {"error": "Not authorized to remove members from this pocket"},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            member.delete()
            
            return Response(
                {"message": "Member removed from pocket successfully"},
                status=status.HTTP_200_OK
            )
        
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class MoveMemberBetweenPockets(APIView):
    """
    Move an IR from one pocket to another.
    Allowed: LDC (who created team) or ADMIN/CTC
    """
    def put(self, request):
        try:
            requester_id = request.data.get("requester_ir_id")
            requester = get_object_or_404(Ir, ir_id=requester_id)
            
            ir_id = request.data.get("ir_id")
            from_pocket_id = request.data.get("from_pocket_id")
            to_pocket_id = request.data.get("to_pocket_id")
            new_role = request.data.get("new_role", TeamRole.IR)
            
            if not ir_id or not from_pocket_id or not to_pocket_id:
                return Response(
                    {"error": "ir_id, from_pocket_id, and to_pocket_id are required"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            from_pocket = get_object_or_404(Pocket, id=from_pocket_id)
            to_pocket = get_object_or_404(Pocket, id=to_pocket_id)
            ir = get_object_or_404(Ir, ir_id=ir_id)
            
            # Ensure both pockets are in the same team
            if from_pocket.team_id != to_pocket.team_id:
                return Response(
                    {"error": "Cannot move members between pockets in different teams"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            team = from_pocket.team
            
            # Permission check: Can user edit the team?
            if not requester.can_edit_team(team):
                return Response(
                    {"error": "Not authorized to move members in this team"},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            # Check if member exists in from_pocket
            member = get_object_or_404(PocketMember, pocket=from_pocket, ir=ir)
            
            # Check if already in to_pocket
            if PocketMember.objects.filter(pocket=to_pocket, ir=ir).exists():
                return Response(
                    {"error": "IR is already a member of the target pocket"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            with transaction.atomic():
                # Remove from old pocket
                member.delete()
                
                # Add to new pocket
                new_member = PocketMember.objects.create(
                    pocket=to_pocket,
                    ir=ir,
                    team=team,
                    role=new_role,
                    added_by=requester
                )
            
            serializer = PocketMemberSerializer(new_member)
            return Response(
                {
                    "message": f"Member moved from {from_pocket.name} to {to_pocket.name}",
                    "member": serializer.data
                },
                status=status.HTTP_200_OK
            )
        
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class SplitTargetToPockets(APIView):
    """
    Split a team's weekly target across pockets.
    Allowed: LDC (who created team) or ADMIN/CTC
    """
    def post(self, request):
        try:
            requester_id = request.data.get("requester_ir_id")
            requester = get_object_or_404(Ir, ir_id=requester_id)
            
            team_id = request.data.get("team_id")
            week_number = request.data.get("week_number")
            year = request.data.get("year")
            pocket_targets = request.data.get("pocket_targets", [])
            
            if not team_id or not week_number or not year or not pocket_targets:
                return Response(
                    {"error": "team_id, week_number, year, and pocket_targets are required"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            team = get_object_or_404(Team, id=team_id)
            
            # Permission check: Can user edit the team?
            if not requester.can_edit_team(team):
                return Response(
                    {"error": "Not authorized to split targets in this team"},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            # Get or create team weekly target
            team_target = WeeklyTarget.objects.filter(
                team=team,
                week_number=week_number,
                year=year
            ).first()
            
            if not team_target:
                return Response(
                    {"error": "Team target not found for this week"},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Validate pocket_targets format and totals
            total_info = 0
            total_plan = 0
            total_uv = 0
            
            created_targets = []
            
            with transaction.atomic():
                for pocket_target in pocket_targets:
                    pocket_id = pocket_target.get("pocket_id")
                    info_target = pocket_target.get("info_target", 0)
                    plan_target = pocket_target.get("plan_target", 0)
                    uv_target = pocket_target.get("uv_target", 0)
                    
                    pocket = get_object_or_404(Pocket, id=pocket_id)
                    
                    total_info += info_target
                    total_plan += plan_target
                    total_uv += uv_target
                    
                    # Create or update pocket weekly target
                    pocket_wt, _ = WeeklyTarget.objects.get_or_create(
                        pocket=pocket,
                        week_number=week_number,
                        year=year,
                        defaults={
                            'week_start': team_target.week_start,
                            'week_end': team_target.week_end,
                        }
                    )
                    
                    pocket_wt.pocket_weekly_info_target = info_target
                    pocket_wt.pocket_weekly_plan_target = plan_target
                    pocket_wt.pocket_weekly_uv_target = uv_target
                    pocket_wt.save()
                    
                    created_targets.append(pocket_wt)
            
            return Response(
                {
                    "message": "Targets split successfully across pockets",
                    "team_target_info": team_target.team_weekly_info_target,
                    "team_target_plan": team_target.team_weekly_plan_target,
                    "team_target_uv": team_target.team_weekly_uv_target,
                    "allocated_info": total_info,
                    "allocated_plan": total_plan,
                    "allocated_uv": total_uv,
                    "pockets_updated": len(created_targets)
                },
                status=status.HTTP_200_OK
            )
        
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class GetPocketTargets(APIView):
    """
    Get pocket-level targets for a week.
    Allowed: LS and above who can view the team
    """
    def get(self, request):
        try:
            requester_id = request.query_params.get("requester_ir_id")
            requester = get_object_or_404(Ir, ir_id=requester_id)
            
            pocket_id = request.query_params.get("pocket_id")
            week_number = request.query_params.get("week_number")
            year = request.query_params.get("year")
            
            if not pocket_id or not week_number or not year:
                return Response(
                    {"error": "pocket_id, week_number, and year are required"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            pocket = get_object_or_404(Pocket, id=pocket_id)
            
            # Permission check: Can user view the team?
            if not requester.can_view_team(pocket.team):
                return Response(
                    {"error": "Not authorized to view this pocket"},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            targets = WeeklyTarget.objects.filter(
                pocket=pocket,
                week_number=week_number,
                year=year
            )
            
            if not targets.exists():
                return Response(
                    {"error": "No targets found for this pocket and week"},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            target = targets.first()
            return Response({
                "pocket_id": pocket.id,
                "pocket_name": pocket.name,
                "week_number": week_number,
                "year": year,
                "info_target": target.pocket_weekly_info_target,
                "plan_target": target.pocket_weekly_plan_target,
                "uv_target": target.pocket_weekly_uv_target,
                "week_start": target.week_start,
                "week_end": target.week_end,
            }, status=status.HTTP_200_OK)
        
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
