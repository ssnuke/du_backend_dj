from django.db import models
from django.contrib.auth.hashers import make_password, check_password
from django.utils import timezone
import pytz

IST = pytz.timezone("Asia/Kolkata")


class TeamRole(models.TextChoices):
    ADMIN = "ADMIN"  # 1 - Full access
    CTC = "CTC"      # 2 - Full access (like admin)
    LDC = "LDC"      # 3 - Can manage own team, view subtree
    LS = "LS"        # 4 - Can add data for team members
    GC = "GC"        # 5 - View own data only
    IR = "IR"        # 6 - View own data only


class AccessLevel:
    """Access level constants mapped to roles"""
    ADMIN = 1
    CTC = 2
    LDC = 3
    LS = 4
    GC = 5
    IR = 6
    
    @classmethod
    def get_role_name(cls, level):
        mapping = {1: "ADMIN", 2: "CTC", 3: "LDC", 4: "LS", 5: "GC", 6: "IR"}
        return mapping.get(level, "IR")
    
    @classmethod
    def can_promote_demote(cls, actor_level):
        """Check if actor can promote/demote others"""
        return actor_level in [cls.ADMIN, cls.CTC]
    
    @classmethod
    def can_create_team(cls, actor_level):
        """Check if actor can create teams"""
        return actor_level in [cls.ADMIN, cls.CTC, cls.LDC]
    
    @classmethod
    def has_full_access(cls, actor_level):
        """Check if actor has full system access"""
        return actor_level in [cls.ADMIN, cls.CTC]


class InfoResponse(models.TextChoices):
    A = "A"
    B = "B"
    C = "C"


class IrId(models.Model):
    ir_id = models.CharField(primary_key=True, max_length=18)

    def __str__(self):
        return self.ir_id


class Ir(models.Model):
    ir_id = models.CharField(primary_key=True, max_length=18)
    ir_name = models.CharField(max_length=45)
    ir_email = models.EmailField()
    ir_access_level = models.PositiveSmallIntegerField(default=6)  # Default to IR (6)
    ir_password = models.CharField(max_length=256)
    status = models.BooleanField(default=True)

    # ========== HIERARCHY FIELDS ==========
    parent_ir = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='direct_downlines'
    )
    hierarchy_path = models.CharField(max_length=500, default="/", db_index=True)
    hierarchy_level = models.PositiveIntegerField(default=0)
    # ======================================

    plan_count = models.IntegerField(default=0)
    dr_count = models.IntegerField(default=0)
    info_count = models.IntegerField(default=0)
    name_list = models.IntegerField(default=0)
    uv_count = models.IntegerField(default=0)
    weekly_info_target = models.IntegerField(default=0)
    weekly_plan_target = models.IntegerField(default=0)
    weekly_uv_target = models.IntegerField(null=True, blank=True)
    started_date = models.DateField(auto_now_add=True)

    def set_password(self, raw_password):
        self.ir_password = make_password(raw_password)

    def check_password(self, raw_password):
        return check_password(raw_password, self.ir_password)

    def save(self, *args, **kwargs):
        """Auto-update hierarchy path and level when saved"""
        if self.parent_ir:
            self.hierarchy_path = f"{self.parent_ir.hierarchy_path}{self.ir_id}/"
            self.hierarchy_level = self.parent_ir.hierarchy_level + 1
        else:
            self.hierarchy_path = f"/{self.ir_id}/"
            self.hierarchy_level = 0
        super().save(*args, **kwargs)

    # ========== HIERARCHY HELPER METHODS ==========

    def get_all_downlines(self):
        """Get ALL IRs below this IR in the hierarchy (entire subtree)"""
        return Ir.objects.filter(
            hierarchy_path__startswith=self.hierarchy_path
        ).exclude(ir_id=self.ir_id)

    def get_direct_downlines(self):
        """Get only direct children (one level down)"""
        return Ir.objects.filter(parent_ir=self)

    def get_all_uplines(self):
        """Get ALL IRs above this IR (ancestors to root)"""
        if not self.parent_ir:
            return Ir.objects.none()
        path_ids = [id for id in self.hierarchy_path.split('/') if id and id != self.ir_id]
        return Ir.objects.filter(ir_id__in=path_ids)

    def get_direct_upline(self):
        """Get immediate parent"""
        return self.parent_ir

    def is_in_subtree(self, target_ir):
        """Check if target_ir is in this IR's subtree (below this IR)"""
        if self.ir_id == target_ir.ir_id:
            return True
        return target_ir.hierarchy_path.startswith(self.hierarchy_path)

    def get_subtree_irs(self):
        """Get all IRs in this user's subtree (self + all downlines)"""
        return Ir.objects.filter(
            hierarchy_path__startswith=self.hierarchy_path
        )

    # ========== ROLE-BASED PERMISSION METHODS ==========

    def has_full_access(self):
        """Check if this IR has full system access (ADMIN or CTC)"""
        return self.ir_access_level in [AccessLevel.ADMIN, AccessLevel.CTC]

    def can_promote_demote(self):
        """Check if this IR can promote/demote others"""
        return self.ir_access_level in [AccessLevel.ADMIN, AccessLevel.CTC]

    def can_create_team(self):
        """Check if this IR can create teams"""
        return self.ir_access_level in [AccessLevel.ADMIN, AccessLevel.CTC, AccessLevel.LDC]

    def can_view_ir(self, target_ir):
        """
        Check if this IR can view target_ir's data based on role
        - ADMIN/CTC: Can view everyone
        - LDC: Can view self + subtree
        - LS: Can view self + team members
        - GC/IR: Can view only self
        """
        # Can always view own data
        if self.ir_id == target_ir.ir_id:
            return True
        
        # ADMIN/CTC can view everyone
        if self.has_full_access():
            return True
        
        # LDC can view their subtree
        if self.ir_access_level == AccessLevel.LDC:
            return self.is_in_subtree(target_ir)
        
        # LS can view team members
        if self.ir_access_level == AccessLevel.LS:
            return self._is_in_same_team(target_ir)
        
        # GC/IR can only view themselves
        return False

    def can_edit_ir(self, target_ir):
        """
        Check if this IR can edit target_ir's data
        - ADMIN/CTC: Can edit everyone
        - LDC: Can edit team members in their own created teams
        - LS: Can add info/plan/UV for team members
        - GC/IR: Can edit only self
        """
        # Can always edit own data
        if self.ir_id == target_ir.ir_id:
            return True
        
        # ADMIN/CTC can edit everyone
        if self.has_full_access():
            return True
        
        # LDC can edit members of teams they created
        if self.ir_access_level == AccessLevel.LDC:
            return self._is_member_of_own_team(target_ir)
        
        # LS can add data for team members
        if self.ir_access_level == AccessLevel.LS:
            return self._is_in_same_team(target_ir)
        
        # GC/IR can only edit themselves
        return False

    def can_view_team(self, team):
        """
        Check if this IR can view a team's data
        - ADMIN/CTC: Can view all teams
        - LDC: Can view teams in subtree or teams they're a member of
        - LS: Can view teams they're a member of
        - GC/IR: Cannot view teams
        """
        from core.models import TeamMember
        
        # ADMIN/CTC can view all teams
        if self.has_full_access():
            return True
        
        # LDC can view teams created by IRs in their subtree OR teams they're a member of
        if self.ir_access_level == AccessLevel.LDC:
            if team.created_by and self.is_in_subtree(team.created_by):
                return True
            return TeamMember.objects.filter(team=team, ir=self).exists()
        
        # LS can view teams they're a member of
        if self.ir_access_level == AccessLevel.LS:
            return TeamMember.objects.filter(team=team, ir=self).exists()
        
        # GC/IR cannot view teams
        return False

    def can_edit_team(self, team):
        """
        Check if this IR can edit a team (add members, update, delete)
        - ADMIN/CTC: Can edit all teams
        - LDC: Can edit only teams they created
        - LS/GC/IR: Cannot edit teams
        """
        # ADMIN/CTC can edit all teams
        if self.has_full_access():
            return True
        
        # LDC can edit only teams they created
        if self.ir_access_level == AccessLevel.LDC:
            return team.created_by and team.created_by.ir_id == self.ir_id
        
        # Others cannot edit teams
        return False

    def can_add_data_for_ir(self, target_ir):
        """
        Check if this IR can add info/plan/UV for target_ir
        - ADMIN/CTC: Can add for everyone
        - LDC: Can add for members of teams they created
        - LS: Can add for team members (in teams they belong to)
        - GC/IR: Can add only for self
        """
        # Can always add for self
        if self.ir_id == target_ir.ir_id:
            return True
        
        # ADMIN/CTC can add for everyone
        if self.has_full_access():
            return True
        
        # LDC can add for members of teams they created
        if self.ir_access_level == AccessLevel.LDC:
            return self._is_member_of_own_team(target_ir)
        
        # LS can add for team members
        if self.ir_access_level == AccessLevel.LS:
            return self._is_in_same_team(target_ir)
        
        # GC/IR can only add for themselves
        return False

    def _is_in_same_team(self, target_ir):
        """Check if target_ir is in the same team as this IR"""
        from core.models import TeamMember
        my_teams = TeamMember.objects.filter(ir=self).values_list('team_id', flat=True)
        return TeamMember.objects.filter(ir=target_ir, team_id__in=my_teams).exists()

    def _is_member_of_own_team(self, target_ir):
        """Check if target_ir is a member of a team created by this IR"""
        from core.models import Team, TeamMember
        my_created_teams = Team.objects.filter(created_by=self).values_list('id', flat=True)
        return TeamMember.objects.filter(ir=target_ir, team_id__in=my_created_teams).exists()

    def get_viewable_irs(self):
        """Get all IRs this user can view based on role"""
        from core.models import TeamMember
        
        # ADMIN/CTC can view all
        if self.has_full_access():
            return Ir.objects.filter(status=True)
        
        # LDC can view subtree
        if self.ir_access_level == AccessLevel.LDC:
            return self.get_subtree_irs()
        
        # LS can view self + team members
        if self.ir_access_level == AccessLevel.LS:
            my_teams = TeamMember.objects.filter(ir=self).values_list('team_id', flat=True)
            team_member_ids = TeamMember.objects.filter(
                team_id__in=my_teams
            ).values_list('ir_id', flat=True)
            return Ir.objects.filter(ir_id__in=team_member_ids)
        
        # GC/IR can only view self
        return Ir.objects.filter(ir_id=self.ir_id)

    def get_teams_can_view(self):
        """Get all teams this IR can view"""
        from core.models import Team, TeamMember
        
        if self.has_full_access():
            return Team.objects.all()
        
        if self.ir_access_level == AccessLevel.LDC:
            viewable_irs = self.get_subtree_irs()
            created_by_subtree = Team.objects.filter(created_by__in=viewable_irs)
            member_of = Team.objects.filter(teammember__ir=self)
            return (created_by_subtree | member_of).distinct()
        
        # LS, GC, IR can view teams they are members of
        if self.ir_access_level in [AccessLevel.LS, AccessLevel.GC, AccessLevel.IR]:
            return Team.objects.filter(teammember__ir=self)
        
        return Team.objects.none()

    def get_teams_can_edit(self):
        """Get all teams this IR can edit"""
        from core.models import Team
        
        if self.has_full_access():
            return Team.objects.all()
        
        if self.ir_access_level == AccessLevel.LDC:
            return Team.objects.filter(created_by=self)
        
        return Team.objects.none()

    def get_irs_can_add_data_for(self):
        """Get all IRs this user can add info/plan/UV for"""
        from core.models import Team, TeamMember
        
        result_ids = {self.ir_id}
        
        if self.has_full_access():
            return Ir.objects.filter(status=True)
        
        if self.ir_access_level == AccessLevel.LDC:
            my_created_teams = Team.objects.filter(created_by=self)
            team_member_ids = TeamMember.objects.filter(
                team__in=my_created_teams
            ).values_list('ir_id', flat=True)
            result_ids.update(team_member_ids)
        
        elif self.ir_access_level == AccessLevel.LS:
            my_teams = TeamMember.objects.filter(ir=self).values_list('team_id', flat=True)
            team_member_ids = TeamMember.objects.filter(
                team_id__in=my_teams
            ).values_list('ir_id', flat=True)
            result_ids.update(team_member_ids)
        
        return Ir.objects.filter(ir_id__in=result_ids)


class Team(models.Model):
    name = models.CharField(max_length=100)
    
    # Track who created the team for hierarchy-based visibility
    created_by = models.ForeignKey(
        Ir,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_teams'
    )

    weekly_info_done = models.IntegerField(default=0)
    weekly_plan_done = models.IntegerField(default=0)

    weekly_info_target = models.IntegerField(default=0)
    weekly_plan_target = models.IntegerField(default=0)
    
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)


class TeamMember(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE)
    ir = models.ForeignKey(Ir, on_delete=models.CASCADE)
    role = models.CharField(max_length=5, choices=TeamRole.choices)

    class Meta:
        unique_together = ("team", "ir")


class InfoDetail(models.Model):
    ir = models.ForeignKey(Ir, on_delete=models.CASCADE)
    info_date = models.DateTimeField(default=timezone.now)
    response = models.CharField(max_length=1, choices=InfoResponse.choices)
    comments = models.TextField(null=True, blank=True)
    info_name = models.CharField(max_length=100)


class PlanDetail(models.Model):
    ir = models.ForeignKey(Ir, on_delete=models.CASCADE)
    plan_date = models.DateTimeField(default=timezone.now)
    plan_name = models.CharField(max_length=255, null=True, blank=True)
    comments = models.TextField(null=True, blank=True)


class TeamWeek(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE)
    week_start = models.DateTimeField()
    weekly_info_done = models.IntegerField(default=0)
    weekly_plan_done = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)


class WeeklyTarget(models.Model):
    week_number = models.PositiveSmallIntegerField()  # 1-52
    year = models.PositiveIntegerField()
    week_start = models.DateTimeField()
    week_end = models.DateTimeField()
    
    # IR targets (optional - only set if IR target is being set)
    ir = models.ForeignKey(Ir, on_delete=models.CASCADE, null=True, blank=True)
    ir_weekly_info_target = models.IntegerField(null=True, blank=True)
    ir_weekly_plan_target = models.IntegerField(null=True, blank=True)
    ir_weekly_uv_target = models.IntegerField(null=True, blank=True)
    
    # Team targets (optional - only set if team target is being set)
    team = models.ForeignKey(Team, on_delete=models.CASCADE, null=True, blank=True)
    team_weekly_info_target = models.IntegerField(null=True, blank=True)
    team_weekly_plan_target = models.IntegerField(null=True, blank=True)
    team_weekly_uv_target = models.IntegerField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = [
            ('week_number', 'year', 'ir'),
            ('week_number', 'year', 'team')
        ]
        indexes = [
            models.Index(fields=['week_number', 'year']),
            models.Index(fields=['ir', 'week_number', 'year']),
            models.Index(fields=['team', 'week_number', 'year']),
        ]
