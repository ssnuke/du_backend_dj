from django.test import TestCase
from core.models import Ir, Team, TeamMember, AccessLevel, TeamRole


class TeamPermissionTests(TestCase):
    """Test team edit permissions for LDC members"""
    
    def setUp(self):
        """Set up test data"""
        # Create test IRs
        self.ldc1 = Ir.objects.create(
            ir_id="LDC001",
            ir_name="LDC One",
            ir_access_level=AccessLevel.LDC,
            status=True
        )
        
        self.ldc2 = Ir.objects.create(
            ir_id="LDC002",
            ir_name="LDC Two",
            ir_access_level=AccessLevel.LDC,
            status=True
        )
        
        self.ldc3 = Ir.objects.create(
            ir_id="LDC003",
            ir_name="LDC Three",
            ir_access_level=AccessLevel.LDC,
            status=True
        )
        
        # Create a team created by LDC1
        self.team = Team.objects.create(
            name="Test Team",
            created_by=self.ldc1
        )
        
        # Add LDC1 as creator (auto-added as LDC)
        TeamMember.objects.create(
            team=self.team,
            ir=self.ldc1,
            role=TeamRole.LDC
        )
    
    def test_creator_can_edit_team(self):
        """Test that the team creator can edit the team"""
        self.assertTrue(self.ldc1.can_edit_team(self.team))
    
    def test_ldc_member_can_edit_team(self):
        """Test that an LDC added as member can edit the team"""
        # Add LDC2 as an LDC member to the team
        TeamMember.objects.create(
            team=self.team,
            ir=self.ldc2,
            role=TeamRole.LDC
        )
        
        # LDC2 should now be able to edit the team
        self.assertTrue(self.ldc2.can_edit_team(self.team))
    
    def test_non_ldc_member_cannot_edit_team(self):
        """Test that an LS member cannot edit the team"""
        # Add LDC2 as an LS member (not LDC)
        TeamMember.objects.create(
            team=self.team,
            ir=self.ldc2,
            role=TeamRole.LS
        )
        
        # LDC2 should NOT be able to edit the team
        self.assertFalse(self.ldc2.can_edit_team(self.team))
    
    def test_non_member_ldc_cannot_edit_team(self):
        """Test that an LDC who is not a member cannot edit the team"""
        # LDC3 is not a member of the team
        self.assertFalse(self.ldc3.can_edit_team(self.team))


# Create your tests here.

