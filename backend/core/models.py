from django.db import models
from django.contrib.auth.hashers import make_password, check_password
from django.utils import timezone
import pytz

IST = pytz.timezone("Asia/Kolkata")


class TeamRole(models.TextChoices):
    LDC = "LDC"
    LS = "LS"
    GC = "GC"
    IR = "IR"


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
    ir_access_level = models.PositiveSmallIntegerField(default=5)
    ir_password = models.CharField(max_length=256)
    status = models.BooleanField(default=True)

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


class Team(models.Model):
    name = models.CharField(max_length=100)

    weekly_info_done = models.IntegerField(default=0)
    weekly_plan_done = models.IntegerField(default=0)

    weekly_info_target = models.IntegerField(default=0)
    weekly_plan_target = models.IntegerField(default=0)


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
