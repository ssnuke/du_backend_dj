import json
from django.test import TestCase, override_settings
from core.models import (Ir, Team, TeamMember, TeamRole, AccessLevel,
                         ChatRoom, ChatRoomMember, ChatRoomType)

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class LdcChatReachTests(TestCase):
    """
    An LDC leads a team. Some team members sit in their hierarchy subtree,
    others do not — they were sponsored elsewhere but put in the team. The
    people BELOW those team members are the ones going missing.
    """

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, name, lvl, parent=None):
            return Ir.objects.create(ir_id=ir_id, ir_name=name, ir_email=f"{ir_id}@t.t",
                                     ir_password="x", ir_access_level=lvl, status=True,
                                     parent_ir=parent)

        cls.ctc = mk("RCTC", "The CTC", AccessLevel.CTC)
        cls.ldc = mk("RLDC", "The LDC", AccessLevel.LDC, parent=cls.ctc)

        # In the LDC's own hierarchy subtree.
        cls.own_ls = mk("ROWNLS", "Own LS", AccessLevel.LS, parent=cls.ldc)
        cls.own_deep = mk("ROWNDEEP", "Deep under own LS", AccessLevel.IR, parent=cls.own_ls)

        # Sponsored under a DIFFERENT branch, but placed in the LDC's team.
        cls.other_branch = mk("ROTHER", "Other branch head", AccessLevel.LS, parent=cls.ctc)
        cls.their_ir = mk("RTHEIR", "Under the team member", AccessLevel.IR, parent=cls.other_branch)
        cls.their_deep = mk("RDEEP", "Two levels under", AccessLevel.IR, parent=cls.their_ir)

        team = Team.objects.create(name="The Team", created_by=cls.ldc)
        TeamMember.objects.create(team=team, ir=cls.ldc, role=TeamRole.LDC)
        TeamMember.objects.create(team=team, ir=cls.own_ls, role=TeamRole.IR)
        TeamMember.objects.create(team=team, ir=cls.other_branch, role=TeamRole.IR)

        cls.room = ChatRoom.objects.create(room_type=ChatRoomType.GROUP, room_name="G",
                                           created_by=cls.ldc)
        ChatRoomMember.objects.create(room=cls.room, ir=cls.ldc)

    def searchable(self):
        r = self.client.get("/api/chat_candidates/",
                            {"requester_ir_id": self.ldc.ir_id, "limit": 100})
        self.assertEqual(r.status_code, 200, r.content[:200])
        return {c["ir_id"] for c in r.json()["candidates"]}

    def can_add(self, target):
        r = self.client.post(f"/api/chat_rooms/{self.room.id}/members/add/",
                             data=json.dumps({"requester_ir_id": self.ldc.ir_id,
                                              "member_ir_ids": [target.ir_id]}),
                             content_type="application/json")
        return r.status_code in (200, 201)

    # ── what already works ────────────────────────────────────────────────
    def test_own_subtree_is_searchable_and_addable_at_any_depth(self):
        found = self.searchable()
        for p in (self.own_ls, self.own_deep):
            self.assertIn(p.ir_id, found, f"{p.ir_name} should be searchable")
            self.assertTrue(self.can_add(p), f"{p.ir_name} should be addable")

    # ── the reported problem ──────────────────────────────────────────────
    def test_a_team_member_outside_the_subtree_is_addable_but_NOT_searchable(self):
        """
        can_view_ir allows them (same team), but the candidate search only
        looks at the hierarchy subtree — so they cannot be found in the picker.
        """
        self.assertTrue(self.can_add(self.other_branch), "same-team member should be addable")
        self.assertIn(self.other_branch.ir_id, self.searchable(),
                      "a team member the LDC can add must be findable in the picker")

    def test_people_under_a_team_member_are_reachable(self):
        """
        The actual complaint: 'I can see them, but I cannot add the people
        under their tree.'
        """
        for p in (self.their_ir, self.their_deep):
            self.assertIn(p.ir_id, self.searchable(), f"{p.ir_name} should be searchable")
            self.assertTrue(self.can_add(p), f"{p.ir_name} should be addable")


    # ── the reach must not become "everyone" ─────────────────────────────
    def test_an_unrelated_branch_is_still_out_of_reach(self):
        """Widening must not turn into org-wide access."""
        stranger_head = Ir.objects.create(ir_id="RSTRANGE", ir_name="Unrelated LS",
                                          ir_email="s@t.t", ir_password="x",
                                          ir_access_level=AccessLevel.LS, status=True,
                                          parent_ir=self.ctc)
        stranger_deep = Ir.objects.create(ir_id="RSTRANGE2", ir_name="Under unrelated",
                                          ir_email="s2@t.t", ir_password="x",
                                          ir_access_level=AccessLevel.IR, status=True,
                                          parent_ir=stranger_head)
        found = self.searchable()
        for p in (stranger_head, stranger_deep):
            self.assertNotIn(p.ir_id, found, f"{p.ir_name} must stay out of reach")
            self.assertFalse(self.can_add(p), f"{p.ir_name} must not be addable")

    def test_the_requester_is_never_their_own_candidate(self):
        self.assertNotIn(self.ldc.ir_id, self.searchable())

    def test_deactivated_people_are_not_offered(self):
        self.own_deep.status = False
        self.own_deep.save()
        self.assertNotIn(self.own_deep.ir_id, self.searchable())

    def test_a_plain_ir_still_reaches_only_their_own_line(self):
        """Nothing here should hand a plain IR the whole org."""
        r = self.client.get("/api/chat_candidates/",
                            {"requester_ir_id": self.their_deep.ir_id, "limit": 100})
        self.assertEqual(r.status_code, 200)
        found = {c["ir_id"] for c in r.json()["candidates"]}
        self.assertNotIn(self.own_deep.ir_id, found)
        self.assertNotIn(self.ldc.ir_id, found)

    def test_an_ls_reaches_their_team_and_below(self):
        r = self.client.get("/api/chat_candidates/",
                            {"requester_ir_id": self.other_branch.ir_id, "limit": 100})
        found = {c["ir_id"] for c in r.json()["candidates"]}
        self.assertIn(self.their_deep.ir_id, found, "own downline")
        self.assertIn(self.ldc.ir_id, found, "their team's LDC")


@override_settings(CACHES=LOCMEM)
class ReachByRoleTests(TestCase):
    """
    Who each role may put in a chat room. Stated by the business as:
    LDC sees everyone under them, LS sees their visible tree, GC/PRO sees only
    the few people directly beneath them, and an IR assembles nothing.

    Pinned because widening the LDC's reach accidentally widened everyone's:
    a plain IR briefly reached their whole team roster and every downline
    hanging off it.
    """

    @classmethod
    def setUpTestData(cls):
        def mk(i, n, lvl, parent=None):
            return Ir.objects.create(ir_id=i, ir_name=n, ir_email=f"{i}@t.t", ir_password="x",
                                     ir_access_level=lvl, status=True, parent_ir=parent)
        cls.ctc = mk("BCTC", "CTC", AccessLevel.CTC)
        cls.ldc = mk("BLDC", "LDC", AccessLevel.LDC, cls.ctc)
        cls.ls = mk("BLS", "LS", AccessLevel.LS, cls.ldc)
        cls.gc = mk("BGC", "GC", AccessLevel.GC, cls.ls)
        cls.ir = mk("BIR", "IR", AccessLevel.IR, cls.gc)
        cls.ir_deep = mk("BIR2", "IR under IR", AccessLevel.IR, cls.ir)
        # a second branch, everyone sharing one big team as in the real org
        cls.other_ls = mk("BOLS", "Other LS", AccessLevel.LS, cls.ldc)
        cls.other_gc = mk("BOGC", "Other GC", AccessLevel.GC, cls.other_ls)
        cls.other_ir = mk("BOIR", "Other IR", AccessLevel.IR, cls.other_gc)
        team = Team.objects.create(name="Big Team", created_by=cls.ldc)
        for p in (cls.ldc, cls.ls, cls.gc, cls.ir, cls.other_ls, cls.other_gc, cls.other_ir):
            TeamMember.objects.create(team=team, ir=p, role=TeamRole.IR)

    def reach(self, who):
        from core.views.chat import get_chat_reachable_ids
        return get_chat_reachable_ids(who)

    def test_an_ldc_reaches_everyone_under_them_at_any_depth(self):
        got = self.reach(self.ldc)
        for p in (self.ls, self.gc, self.ir, self.ir_deep, self.other_ls, self.other_ir):
            self.assertIn(p.ir_id, got, f"LDC should reach {p.ir_name}")

    def test_an_ls_reaches_their_visible_tree(self):
        got = self.reach(self.ls)
        for p in (self.gc, self.ir, self.ir_deep):
            self.assertIn(p.ir_id, got, f"LS should reach {p.ir_name}")
        self.assertIn(self.ldc.ir_id, got, "and their upline LDC")

    def test_a_gc_reaches_only_their_own_line_and_their_ldc(self):
        got = self.reach(self.gc)
        self.assertEqual(got, {self.ir.ir_id, self.ir_deep.ir_id, self.ldc.ir_id})
        # NOT the rest of the team they happen to sit in
        for p in (self.other_ls, self.other_gc, self.other_ir, self.ls):
            self.assertNotIn(p.ir_id, got, f"GC must not reach {p.ir_name}")

    def test_an_ir_reaches_nobody(self):
        self.assertEqual(self.reach(self.ir), set())
        self.assertEqual(self.reach(self.ir_deep), set())

    def test_team_membership_does_not_widen_a_junior_role(self):
        """
        Every one of these people is in the same team. That must not hand a
        GC or an IR the whole roster.
        """
        self.assertNotIn(self.other_ir.ir_id, self.reach(self.gc))
        self.assertEqual(self.reach(self.ir), set())

    def test_nobody_reaches_themselves(self):
        for p in (self.ctc, self.ldc, self.ls, self.gc):
            self.assertNotIn(p.ir_id, self.reach(p))
