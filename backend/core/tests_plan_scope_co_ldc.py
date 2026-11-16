from django.test import TestCase, override_settings
from core.models import Ir, Team, TeamMember, AccessLevel, TeamRole
from core.views.get import get_plan_visible_member_ids


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class CoLdcPlanScopeTests(TestCase):
    """Two LDCs on one team see the same month numbers for it."""

    @classmethod
    def setUpTestData(cls):
        cls.n = 0

        def mk(name, lvl=AccessLevel.IR):
            cls.n += 1
            return Ir.objects.create(ir_id=f"C{cls.n:06d}", ir_name=name, ir_email=f"c{cls.n}@t.t",
                                     ir_password="x", ir_access_level=lvl, status=True)

        cls.owner = mk("Owner LDC", AccessLevel.LDC)
        cls.co = mk("Co LDC", AccessLevel.LDC)
        cls.ctc = mk("CTC", AccessLevel.CTC)
        cls.team = Team.objects.create(name="Shared", created_by=cls.owner)
        cls.members = [mk(f"M{i}") for i in range(3)]
        for m in cls.members + [cls.co]:
            TeamMember.objects.create(team=cls.team, ir=m, role=TeamRole.IR)
        # A team a CTC made, with the co-LDC merely added to it.
        cls.ctc_team = Team.objects.create(name="CTC's", created_by=cls.ctc)
        cls.ctc_only = mk("Only in CTC team")
        TeamMember.objects.create(team=cls.ctc_team, ir=cls.ctc_only, role=TeamRole.IR)
        TeamMember.objects.create(team=cls.ctc_team, ir=cls.co, role=TeamRole.IR)

    def test_both_ldcs_see_the_same_team_members(self):
        owner_ids = get_plan_visible_member_ids(self.owner)
        co_ids = get_plan_visible_member_ids(self.co)
        for m in self.members:
            self.assertIn(m.ir_id, owner_ids)
            self.assertIn(m.ir_id, co_ids)
        self.assertIn(self.co.ir_id, owner_ids)

    def test_a_ctc_made_team_does_not_leak_to_an_ldc_added_to_it(self):
        self.assertNotIn(self.ctc_only.ir_id, get_plan_visible_member_ids(self.co))
