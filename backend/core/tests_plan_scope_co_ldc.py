from django.test import TestCase, override_settings
from core.models import Ir, Team, TeamMember, AccessLevel, TeamRole
from core.views.get import get_plan_visible_member_ids


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class CoLdcPlanScopeTests(TestCase):
    """Two LDCs on one team: the creator sees all of it, the other only their own tree."""

    @classmethod
    def setUpTestData(cls):
        cls.n = 0

        def mk(name, lvl=AccessLevel.IR, parent=None):
            cls.n += 1
            return Ir.objects.create(ir_id=f"C{cls.n:06d}", ir_name=name, ir_email=f"c{cls.n}@t.t",
                                     ir_password="x", ir_access_level=lvl, status=True, parent_ir=parent)

        cls.owner = mk("Owner LDC", AccessLevel.LDC)
        cls.co = mk("Co LDC", AccessLevel.LDC)
        cls.ctc = mk("CTC", AccessLevel.CTC)
        cls.team = Team.objects.create(name="Shared", created_by=cls.owner)
        cls.owner_people = [mk(f"Owner's {i}", parent=cls.owner) for i in range(2)]
        cls.co_people = [mk(f"Co's {i}", parent=cls.co) for i in range(2)]
        for m in cls.owner_people + cls.co_people + [cls.co]:
            TeamMember.objects.create(team=cls.team, ir=m, role=TeamRole.IR)
        # A team a CTC made, with the co-LDC merely added to it.
        cls.ctc_team = Team.objects.create(name="CTC's", created_by=cls.ctc)
        cls.ctc_only = mk("Only in CTC team")
        TeamMember.objects.create(team=cls.ctc_team, ir=cls.ctc_only, role=TeamRole.IR)
        TeamMember.objects.create(team=cls.ctc_team, ir=cls.co, role=TeamRole.IR)

    def test_the_creator_sees_every_member_of_their_team(self):
        ids = get_plan_visible_member_ids(self.owner)
        for m in self.owner_people + self.co_people:
            self.assertIn(m.ir_id, ids)

    def test_a_co_ldc_sees_only_the_members_in_their_own_tree(self):
        ids = get_plan_visible_member_ids(self.co)
        for m in self.co_people:
            self.assertIn(m.ir_id, ids)
        for m in self.owner_people:
            self.assertNotIn(m.ir_id, ids)

    def test_a_ctc_made_team_does_not_leak_to_an_ldc_added_to_it(self):
        self.assertNotIn(self.ctc_only.ir_id, get_plan_visible_member_ids(self.co))
