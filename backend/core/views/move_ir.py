from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from django.shortcuts import get_object_or_404
from django.db import transaction

from core.models import Ir, Team, TeamMember


class MoveIrToTeam(APIView):
    """
    Move an IR from their current team to a new team.
    
    PUT /api/move_ir_to_team/
    Body: {
        "ir_id": "IR123",
        "current_team_id": 1,
        "new_team_id": 2,
        "new_role": "LS",  # optional, defaults to current role
        "requester_ir_id": "IR001"  # optional, for permission check
    }
    """
    def put(self, request):
        ir_id = request.data.get("ir_id")
        current_team_id = request.data.get("current_team_id")
        new_team_id = request.data.get("new_team_id")
        new_role = request.data.get("new_role")  # Optional
        requester_ir_id = request.data.get("requester_ir_id")
        
        # Validate required fields
        if not ir_id:
            return Response(
                {"detail": "ir_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not current_team_id:
            return Response(
                {"detail": "current_team_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not new_team_id:
            return Response(
                {"detail": "new_team_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate that current and new teams are different
        if current_team_id == new_team_id:
            return Response(
                {"detail": "Current team and new team cannot be the same"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get IR and teams
        ir = get_object_or_404(Ir, ir_id=ir_id)
        current_team = get_object_or_404(Team, id=current_team_id)
        new_team = get_object_or_404(Team, id=new_team_id)
        
        # Role-based permission check if requester provided
        if requester_ir_id:
            try:
                requester = Ir.objects.get(ir_id=requester_ir_id)
                
                # Requester must be able to edit both teams
                if not requester.can_edit_team(current_team):
                    return Response(
                        {"detail": "Not authorized to remove member from current team"},
                        status=status.HTTP_403_FORBIDDEN
                    )
                
                if not requester.can_edit_team(new_team):
                    return Response(
                        {"detail": "Not authorized to add member to new team"},
                        status=status.HTTP_403_FORBIDDEN
                    )
                    
            except Ir.DoesNotExist:
                return Response(
                    {"detail": "Requester IR not found"},
                    status=status.HTTP_404_NOT_FOUND
                )
        
        # Check if IR is in the current team
        current_membership = TeamMember.objects.filter(
            team_id=current_team_id,
            ir_id=ir_id
        ).first()
        
        if not current_membership:
            return Response(
                {"detail": f"IR '{ir_id}' is not a member of team {current_team_id}"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Check if IR is already in the new team
        if TeamMember.objects.filter(team_id=new_team_id, ir_id=ir_id).exists():
            return Response(
                {"detail": f"IR '{ir_id}' is already a member of team {new_team_id}"},
                status=status.HTTP_409_CONFLICT
            )
        
        # Determine the role for the new team
        role_for_new_team = new_role if new_role else current_membership.role
        
        # Perform the move in a transaction
        with transaction.atomic():
            # Remove from current team
            current_membership.delete()
            
            # Add to new team
            TeamMember.objects.create(
                ir=ir,
                team=new_team,
                role=role_for_new_team
            )
        
        return Response(
            {
                "message": f"IR '{ir_id}' successfully moved from team {current_team_id} to team {new_team_id}",
                "ir_id": ir_id,
                "ir_name": ir.ir_name,
                "previous_team_id": current_team_id,
                "previous_team_name": current_team.name,
                "new_team_id": new_team_id,
                "new_team_name": new_team.name,
                "role": role_for_new_team
            },
            status=status.HTTP_200_OK
        )
