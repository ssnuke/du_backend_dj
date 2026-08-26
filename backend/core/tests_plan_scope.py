import datetime
from django.test import TestCase, override_settings
from core.models import (Ir, Team, TeamMember, PlanDetail, AccessLevel, TeamRole)
from core.views.get import get_plan_visible_member_ids
from core.utils.dates import get_week_info_monday_to_sunday


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class PlanScopeTests(TestCase):
    """
    A manager's team page asks "the people in the teams this person runs".
    Without an explicit scope a CTC answered with their whole downline, so a
    page about a team of 12 reported plans from 43 people.
    """

    @classmethod
    def setUpTestData(cls):
        cls.seq = 0

        def mk(name, lvl=AccessLevel.IR, parent=None):
            cls.seq += 1
            return Ir.objects.create(ir_id=f"P{cls.seq:06d}", ir_name=name,
                                     ir_email=f"p{cls.seq}@t.t", ir_password="x",
                                     ir_access_level=lvl, status=True, parent_ir=parent)

        cls.ctc = mk("The CTC", AccessLevel.CTC)
        # Their own team — the people a team page is about.
        cls.team = Team.objects.create(name="Their Team", created_by=cls.ctc)
        cls.in_team = [mk(f"In team {i}", parent=cls.ctc) for i in range(3)]
        for p in cls.in_team:
            TeamMember.objects.create(team=cls.team, ir=p, role=TeamRole.IR)

        # Downline people who are NOT in that team — visible to the CTC
        # hierarchically, but nothing to do with this team.
        cls.downline = [mk(f"Downline {i}", parent=cls.ctc) for i in range(4)]

        # A deactivated team member must not count either.
        gone = mk("Left", parent=cls.ctc); gone.status = False; gone.save()
        TeamMember.objects.create(team=cls.team, ir=gone, role=TeamRole.IR)
        cls.gone = gone

    def test_team_scope_covers_only_that_manager_s_team(self):
        ids = get_plan_visible_member_ids(self.ctc, scope="teams")
        self.assertEqual(ids, {p.ir_id for p in self.in_team} | {self.ctc.ir_id})
        for d in self.downline:
            self.assertNotIn(d.ir_id, ids)
        self.assertNotIn(self.gone.ir_id, ids)

    def test_without_scope_a_ctc_still_sees_their_whole_downline(self):
        """The org dashboard depends on this staying wide."""
        ids = get_plan_visible_member_ids(self.ctc)
        for d in self.downline:
            self.assertIn(d.ir_id, ids)
        self.assertGreater(len(ids), len(get_plan_visible_member_ids(self.ctc, scope="teams")))

    def test_admin_is_not_special_cased_on_a_team_page(self):
        admin = Ir.objects.create(ir_id="PADMIN", ir_name="Admin", ir_email="a@t.t",
                                  ir_password="x", ir_access_level=AccessLevel.ADMIN, status=True)
        t = Team.objects.create(name="Admin's team", created_by=admin)
        member = self.in_team[0]
        TeamMember.objects.create(team=t, ir=member, role=TeamRole.IR)
        scoped = get_plan_visible_member_ids(admin, scope="teams")
        self.assertEqual(scoped, {member.ir_id, admin.ir_id})
        # unscoped, an admin still sees everyone
        self.assertIn(self.downline[0].ir_id, get_plan_visible_member_ids(admin))

    def test_endpoint_honours_the_scope_param(self):
        _, _, ws, we = get_week_info_monday_to_sunday()
        for p in self.in_team + self.downline:
            PlanDetail.objects.create(ir=p, plan_date=ws + datetime.timedelta(hours=2))

        wide = self.client.get(f"/api/team_plans/{self.ctc.ir_id}/",
                               {"week": 1, "year": ws.year}).status_code
        self.assertEqual(wide, 200)

        r_all = self.client.get(f"/api/team_plans/{self.ctc.ir_id}/").json()
        r_team = self.client.get(f"/api/team_plans/{self.ctc.ir_id}/", {"scope": "teams"}).json()
        self.assertEqual(r_all["summary"]["total_plans"], 7)
        self.assertEqual(r_team["summary"]["total_plans"], 3)
