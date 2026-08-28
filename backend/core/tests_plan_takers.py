from django.test import TestCase, override_settings
from core.models import Ir, Team, TeamMember, TeamRole, AccessLevel

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class PlanTakerCandidateTests(TestCase):
    """
    The UL2 picker used to search ChatCandidates, which answers "who may I
    message". For a plain IR that returned nothing at all, and nobody of any
    role could pick themselves.
    """

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, name, lvl, parent=None):
            return Ir.objects.create(ir_id=ir_id, ir_name=name, ir_email=f"{ir_id}@t.t",
                                     ir_password="x", ir_access_level=lvl, status=True,
                                     parent_ir=parent)

        cls.ctc = mk("PCTC", "The CTC", AccessLevel.CTC)
        cls.ldc = mk("PLDC", "The LDC", AccessLevel.LDC, parent=cls.ctc)
        cls.ls = mk("PLS", "The LS", AccessLevel.LS, parent=cls.ldc)
        cls.gc = mk("PGC", "The GC", AccessLevel.GC, parent=cls.ls)
        cls.ir = mk("PIR", "Plain IR", AccessLevel.IR, parent=cls.gc)
        cls.gone = mk("PGONE", "Left The Org", AccessLevel.IR, parent=cls.gc)
        cls.gone.status = False
        cls.gone.save()

        team = Team.objects.create(name="T", created_by=cls.ldc)
        for p in (cls.ldc, cls.ls, cls.gc, cls.ir):
            TeamMember.objects.create(team=team, ir=p, role=TeamRole.IR)

    def candidates(self, requester, q=""):
        r = self.client.get("/api/plan_taker_candidates/",
                            {"requester_ir_id": requester.ir_id, "q": q, "limit": 50})
        self.assertEqual(r.status_code, 200, r.content[:200])
        return {c["ir_id"] for c in r.json()["candidates"]}

    def test_a_plain_ir_gets_candidates_at_all(self):
        """This returned an empty list before — the reported bug."""
        got = self.candidates(self.ir)
        self.assertTrue(got, "a plain IR must not get an empty plan-taker list")

    def test_everyone_can_pick_themselves(self):
        for who in (self.ctc, self.ldc, self.ls, self.gc, self.ir):
            self.assertIn(who.ir_id, self.candidates(who),
                          f"{who.ir_id} must be able to record their own plan")

    def test_an_ir_can_reach_their_whole_upline(self):
        got = self.candidates(self.ir)
        for up in (self.gc, self.ls, self.ldc, self.ctc):
            self.assertIn(up.ir_id, got, f"{up.ir_id} shows plans for this IR")

    def test_a_gc_can_reach_their_upline_too(self):
        got = self.candidates(self.gc)
        self.assertIn(self.ls.ir_id, got)
        self.assertIn(self.ldc.ir_id, got)

    def test_deactivated_people_are_not_offered(self):
        self.assertNotIn(self.gone.ir_id, self.candidates(self.ir))

    def test_search_narrows_by_name_and_id(self):
        self.assertEqual(self.candidates(self.ir, q="The LDC"), {self.ldc.ir_id})
        self.assertEqual(self.candidates(self.ir, q="PLS"), {self.ls.ir_id})

    def test_the_requester_sorts_first_when_they_match(self):
        r = self.client.get("/api/plan_taker_candidates/",
                            {"requester_ir_id": self.ir.ir_id, "q": "P", "limit": 50})
        self.assertEqual(r.json()["candidates"][0]["ir_id"], self.ir.ir_id)

    def test_an_unknown_requester_is_rejected(self):
        r = self.client.get("/api/plan_taker_candidates/", {"requester_ir_id": "NOPE"})
        self.assertEqual(r.status_code, 400)

    def test_a_leader_still_reaches_their_team(self):
        got = self.candidates(self.ldc)
        for p in (self.ls, self.gc, self.ir):
            self.assertIn(p.ir_id, got)
