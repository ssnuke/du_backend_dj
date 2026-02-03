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


class InfoType(models.TextChoices):
    FRESH = "Fresh"
    REINFO = "Re-info"


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

    def delete(self, *args, **kwargs):
        """
        Override delete to reconnect children to grandparent before deletion.
        This ensures hierarchy integrity is maintained.
        """
        grandparent = self.parent_ir  # Could be None if this IR is root
        
        # Get all direct children before deletion
        direct_children = list(self.direct_downlines.all())
        
        # Reconnect each child to grandparent and update their paths
        for child in direct_children:
            child.parent_ir = grandparent
            child.save()  # This triggers save() which recalculates path & level
            
            # Now recursively update all descendants of this child
            # because their paths still contain the deleted IR's id
            self._update_descendant_paths(child)
        
        # Now safe to delete this IR
        super().delete(*args, **kwargs)

    def _update_descendant_paths(self, parent_ir):
        """
        Recursively update hierarchy_path for all descendants of parent_ir.
        Called after parent_ir's path has been updated.
        """
        for child in parent_ir.direct_downlines.all():
            # Recalculate path based on updated parent
            child.hierarchy_path = f"{parent_ir.hierarchy_path}{child.ir_id}/"
            child.hierarchy_level = parent_ir.hierarchy_level + 1
            child.save(update_fields=['hierarchy_path', 'hierarchy_level'])
            # Recurse to update grandchildren
            self._update_descendant_paths(child)

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
        - ADMIN: Can view everyone
        - CTC: Can view subtree
        - LDC: Can view self + subtree
        - LS: Can view self + team members
        - GC/IR: Can view only self
        """
        # Can always view own data
        if self.ir_id == target_ir.ir_id:
            return True
        
        # ADMIN can view everyone
        if self.ir_access_level == AccessLevel.ADMIN:
            return True
        
        # CTC and LDC can view their subtree
        if self.ir_access_level in [AccessLevel.CTC, AccessLevel.LDC]:
            return self.is_in_subtree(target_ir)
        
        # LS can view team members
        if self.ir_access_level == AccessLevel.LS:
            return self._is_in_same_team(target_ir)
        
        # GC/IR can only view themselves
        return False

    def can_edit_ir(self, target_ir):
        """
        Check if this IR can edit target_ir's data
        - ADMIN: Can edit everyone
        - CTC: Can edit subtree
        - LDC: Can edit team members in their own created teams
        - LS: Can add info/plan/UV for team members
        - GC/IR: Can edit only self
        """
        # Can always edit own data
        if self.ir_id == target_ir.ir_id:
            return True
        
        # ADMIN can edit everyone
        if self.ir_access_level == AccessLevel.ADMIN:
            return True
        
        # CTC can edit members in their subtree
        if self.ir_access_level == AccessLevel.CTC:
            return self.is_in_subtree(target_ir)
        
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
        - ADMIN: Can view all teams
        - CTC: Can view teams in subtree only
        - LDC: Can view teams in subtree or teams they're a member of
        - LS: Can view teams they're a member of
        - GC/IR: Cannot view teams
        """
        from core.models import TeamMember
        
        # ADMIN can view all teams
        if self.ir_access_level == AccessLevel.ADMIN:
            return True
        
        # CTC can only view teams created by IRs in their subtree
        if self.ir_access_level == AccessLevel.CTC:
            if team.created_by and self.is_in_subtree(team.created_by):
                return True
            return False
        
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
        - ADMIN: Can edit all teams
        - CTC: Can edit teams in subtree
        - LDC: Can edit teams they created OR teams where they are a member with LDC role
        - LS/GC/IR: Cannot edit teams
        """
        from core.models import TeamMember
        
        # ADMIN can edit all teams
        if self.ir_access_level == AccessLevel.ADMIN:
            return True
        
        # CTC can edit teams created by IRs in their subtree
        if self.ir_access_level == AccessLevel.CTC:
            if team.created_by and self.is_in_subtree(team.created_by):
                return True
            return False
        
        # LDC can edit teams they created OR teams where they are an LDC member
        if self.ir_access_level == AccessLevel.LDC:
            # Check if they created the team
            if team.created_by and team.created_by.ir_id == self.ir_id:
                return True
            # Check if they are an LDC member of the team
            return TeamMember.objects.filter(
                team=team, 
                ir=self, 
                role=TeamRole.LDC
            ).exists()
        
        # Others cannot edit teams
        return False

    def can_add_data_for_ir(self, target_ir):
        """
        Check if this IR can add info/plan/UV for target_ir
        - ADMIN: Can add for everyone
        - CTC: Can add for subtree
        - LDC: Can add for members of teams they created
        - LS: Can add for team members (in teams they belong to)
        - GC/IR: Can add only for self
        """
        # Can always add for self
        if self.ir_id == target_ir.ir_id:
            return True
        
        # ADMIN can add for everyone
        if self.ir_access_level == AccessLevel.ADMIN:
            return True
        
        # CTC can add for members in their subtree
        if self.ir_access_level == AccessLevel.CTC:
            return self.is_in_subtree(target_ir)
        
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
        
        # ADMIN can view all
        if self.ir_access_level == AccessLevel.ADMIN:
            return Ir.objects.filter(status=True)
        
        # CTC and LDC can view subtree
        if self.ir_access_level in [AccessLevel.CTC, AccessLevel.LDC]:
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
        
        # ADMIN has full access to all teams
        if self.ir_access_level == AccessLevel.ADMIN:
            return Team.objects.all()
        
        # CTC can only view teams in their subtree
        if self.ir_access_level == AccessLevel.CTC:
            viewable_irs = self.get_subtree_irs()
            return Team.objects.filter(created_by__in=viewable_irs)
        
        # LDC can view teams in their subtree + teams they're members of
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
        from core.models import Team, TeamMember
        
        # ADMIN can edit all teams
        if self.ir_access_level == AccessLevel.ADMIN:
            return Team.objects.all()
        
        # CTC can edit teams in their subtree
        if self.ir_access_level == AccessLevel.CTC:
            viewable_irs = self.get_subtree_irs()
            return Team.objects.filter(created_by__in=viewable_irs)
        
        # LDC can edit teams they created OR teams where they are an LDC member
        if self.ir_access_level == AccessLevel.LDC:
            created_teams = Team.objects.filter(created_by=self)
            ldc_member_teams = Team.objects.filter(
                teammember__ir=self,
                teammember__role=TeamRole.LDC
            )
            return (created_teams | ldc_member_teams).distinct()
        
        return Team.objects.none()

    def get_irs_can_add_data_for(self):
        """Get all IRs this user can add info/plan/UV for"""
        from core.models import Team, TeamMember
        
        result_ids = {self.ir_id}
        
        # ADMIN can add for all
        if self.ir_access_level == AccessLevel.ADMIN:
            return Ir.objects.filter(status=True)
        
        # CTC can add for subtree
        if self.ir_access_level == AccessLevel.CTC:
            return self.get_subtree_irs()
        
        # LDC can add for members of teams they created
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


class Pocket(models.Model):
    """
    Represents a sub-group within a team.
    Allows better visibility and target management at a granular level.
    """
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='pockets')
    name = models.CharField(max_length=100)
    created_by = models.ForeignKey(
        Ir,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_pockets'
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("team", "name")  # One pocket name per team
        ordering = ['name']

    def __str__(self):
        return f"{self.name} (Team: {self.team.name})"


class PocketMember(models.Model):
    """
    Represents an IR's membership in a pocket within a team.
    Each IR can be in multiple pockets within the same team.
    """
    pocket = models.ForeignKey(Pocket, on_delete=models.CASCADE, related_name='members')
    ir = models.ForeignKey(Ir, on_delete=models.CASCADE, related_name='pocket_memberships')
    role = models.CharField(max_length=5, choices=TeamRole.choices)
    
    # Mark if this member is the pocket head (typically the first/creator member)
    is_head = models.BooleanField(default=False)
    
    # Optional: Track which IR added this member
    added_by = models.ForeignKey(
        Ir,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pocket_members_added'
    )
    
    # Denormalization: Store team ID for easier querying without double FK traversal
    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='pocket_members'
    )
    
    joined_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("pocket", "ir")  # Each IR can be in a pocket only once
        indexes = [
            models.Index(fields=['pocket', 'role']),
            models.Index(fields=['ir', 'team']),
            models.Index(fields=['pocket', 'ir']),
        ]

    def save(self, *args, **kwargs):
        """Auto-populate team from pocket's team"""
        if not self.team:
            self.team = self.pocket.team
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.ir.ir_name} in {self.pocket.name}"


class InfoDetail(models.Model):
    ir = models.ForeignKey(Ir, on_delete=models.CASCADE)
    info_date = models.DateTimeField(default=timezone.now)
    response = models.CharField(max_length=1, choices=InfoResponse.choices)
    info_type = models.CharField(max_length=10, choices=InfoType.choices, default=InfoType.FRESH, null=True, blank=True)
    comments = models.TextField(null=True, blank=True)
    info_name = models.CharField(max_length=100)


class PlanDetail(models.Model):
    STATUS_CHOICES = [
        ('closing_pending', 'Closing Pending'),
        ('closed', 'Closed'),
        ('rejected', 'Rejected'),
        ('uvs_on_counter', "UV's on Counter"),
    ]
    
    ir = models.ForeignKey(Ir, on_delete=models.CASCADE)
    plan_date = models.DateTimeField(default=timezone.now)
    plan_name = models.CharField(max_length=255, null=True, blank=True)
    comments = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='closing_pending', null=True, blank=True)


class UVDetail(models.Model):
    ir = models.ForeignKey(Ir, on_delete=models.CASCADE)
    ir_name = models.CharField(max_length=45, blank=True, default="")  # Track IR name for display
    prospect_name = models.CharField(max_length=255, blank=True, default="")  # Name of prospect whose UV fell
    uv_date = models.DateTimeField(default=timezone.now)
    uv_count = models.IntegerField(default=1)
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

    # Pocket targets (optional - can split team targets to pockets)
    pocket = models.ForeignKey(Pocket, on_delete=models.CASCADE, null=True, blank=True, related_name='weekly_targets')
    pocket_weekly_info_target = models.IntegerField(null=True, blank=True)
    pocket_weekly_plan_target = models.IntegerField(null=True, blank=True)
    pocket_weekly_uv_target = models.IntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [
            ('week_number', 'year', 'ir'),
            ('week_number', 'year', 'team'),
            ('week_number', 'year', 'pocket'),
        ]
        indexes = [
            models.Index(fields=['week_number', 'year']),
            models.Index(fields=['ir', 'week_number', 'year']),
            models.Index(fields=['team', 'week_number', 'year']),
            models.Index(fields=['pocket', 'week_number', 'year']),
        ]


class TeamWeeklyTargets(models.Model):
    """
    Stores team weekly targets in nested JSON format organized by year and week.
    Structure: {
        "2026": {
            "1": {
                "week_start": "2026-01-03T00:00:00+05:30",
                "week_end": "2026-01-09T23:59:59+05:30",
                "team_weekly_info_target": 150,
                "team_weekly_plan_target": 30,
                "team_weekly_uv_target": 15
            },
            "2": { ... }
        },
        "2027": { ... }
    }
    """
    team = models.OneToOneField(Team, on_delete=models.CASCADE, related_name='weekly_targets_json')
    targets_data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Team Weekly Targets (JSON)"
        verbose_name_plural = "Team Weekly Targets (JSON)"

    def get_week_targets(self, year, week_number):
        """Get targets for a specific week"""
        year_str = str(year)
        week_str = str(week_number)
        return self.targets_data.get(year_str, {}).get(week_str)

    def set_week_targets(self, year, week_number, week_start, week_end, 
                         info_target, plan_target, uv_target, allow_overwrite=False):
        """Set targets for a specific week"""
        year_str = str(year)
        week_str = str(week_number)

        # Initialize year if doesn't exist
        if year_str not in self.targets_data:
            self.targets_data[year_str] = {}

        # Check if week already exists
        if week_str in self.targets_data[year_str] and not allow_overwrite:
            return False, "Week targets already exist"

        # Set the week data
        self.targets_data[year_str][week_str] = {
            "week_start": week_start.isoformat() if hasattr(week_start, 'isoformat') else week_start,
            "week_end": week_end.isoformat() if hasattr(week_end, 'isoformat') else week_end,
            "team_weekly_info_target": int(info_target),
            "team_weekly_plan_target": int(plan_target),
            "team_weekly_uv_target": int(uv_target)
        }

        return True, "Targets set successfully"

    def get_all_weeks_for_year(self, year):
        """Get all week targets for a specific year"""
        year_str = str(year)
        return self.targets_data.get(year_str, {})


class Notification(models.Model):
    class Type(models.TextChoices):
        UV_ADDED = 'UV_ADDED', 'UV Added'
        NEW_IR = 'NEW_IR', 'New IR Registered'

    recipient = models.ForeignKey(Ir, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=255)
    message = models.TextField()
    notification_type = models.CharField(max_length=20, choices=Type.choices)
    is_read = models.BooleanField(default=False)
    
    # Optional: Link to the related object (e.g., the UV ID or New IR ID) for navigation
    related_object_id = models.CharField(max_length=50, null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} -> {self.recipient.ir_name}"
