from django.test import TestCase, override_settings
from core.models import Ir, Team, TeamMember, TeamRole, AccessLevel

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class ReferrerCandidatesIncludeSelfTests(TestCase):
    """
    The Referrer (Parent) picker on Register New IR was excluding the person
    doing the registering from their own candidate list: an LDC's own row
    was explicitly excluded from their team roster, and a downline query can
    never include the person at its own root — so an ADMIN or CTC registering
    someone they personally referred could never search up their own name.
    """

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, name, lvl, parent=None):
            return Ir.objects.create(ir_id=ir_id, ir_name=name, ir_email=f"{ir_id}@t.t",
                                     ir_password="x", ir_access_level=lvl, status=True,
                                     parent_ir=parent)

        cls.admin = mk("RADMIN", "The Admin", AccessLevel.ADMIN)
        cls.ctc = mk("RCTC", "Carla The CTC", AccessLevel.CTC, parent=cls.admin)
        cls.ldc = mk("RLDC", "Leo The LDC", AccessLevel.LDC, parent=cls.ctc)
        cls.member = mk("RMEM", "Team Member", AccessLevel.IR, parent=cls.ldc)

        # Unrelated branch — nobody above should ever see this one.
        cls.other_ctc = mk("ROCTC", "Other CTC", AccessLevel.CTC, parent=cls.admin)
        cls.outsider = mk("ROUT", "Outsider", AccessLevel.IR, parent=cls.other_ctc)

        team = Team.objects.create(name="Leo's Team", created_by=cls.ldc)
        TeamMember.objects.create(team=team, ir=cls.ldc, role=TeamRole.LDC)
        TeamMember.objects.create(team=team, ir=cls.member, role=TeamRole.IR)

    def search(self, requester, q=""):
        r = self.client.get(f"/api/referrer_candidates/{requester.ir_id}/", {"search": q, "limit": 50})
        self.assertEqual(r.status_code, 200, r.content[:200])
        return {row["ir_id"] for row in r.json()["results"]}

    def test_ldc_can_find_their_own_name(self):
        ids = self.search(self.ldc, "Leo")
        self.assertIn(self.ldc.ir_id, ids)

    def test_ldc_can_find_their_own_id(self):
        ids = self.search(self.ldc, "RLDC")
        self.assertIn(self.ldc.ir_id, ids)

    def test_ldc_unfiltered_list_still_includes_self_and_team(self):
        ids = self.search(self.ldc)
        self.assertEqual(ids, {self.ldc.ir_id, self.member.ir_id})

    def test_ctc_can_find_their_own_name(self):
        ids = self.search(self.ctc, "Carla")
        self.assertIn(self.ctc.ir_id, ids)

    def test_admin_can_find_their_own_name(self):
        ids = self.search(self.admin, "Admin")
        self.assertIn(self.admin.ir_id, ids)

    def test_ctc_still_cannot_reach_an_unrelated_branch(self):
        ids = self.search(self.ctc)
        self.assertNotIn(self.other_ctc.ir_id, ids)
        self.assertNotIn(self.outsider.ir_id, ids)

    def test_ldc_still_cannot_reach_an_unrelated_branch(self):
        ids = self.search(self.ldc)
        self.assertNotIn(self.outsider.ir_id, ids)

    def test_self_appears_exactly_once_not_duplicated(self):
        r = self.client.get(f"/api/referrer_candidates/{self.ldc.ir_id}/", {"search": "Leo", "limit": 50})
        rows = r.json()["results"]
        self.assertEqual(sum(1 for row in rows if row["ir_id"] == self.ldc.ir_id), 1)
