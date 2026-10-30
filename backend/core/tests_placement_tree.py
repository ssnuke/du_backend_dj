import json

from django.test import TestCase, override_settings

from core.models import AccessLevel, Ir, Team, TeamMember, TeamRole

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class PlacementTreeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, level, parent=None):
            return Ir.objects.create(ir_id=ir_id, ir_name=f"N {ir_id}", ir_email=f"{ir_id}@t.t",
                                     ir_password="x", ir_access_level=level, status=True, parent_ir=parent)

        cls.admin = mk("ADM", AccessLevel.ADMIN)
        cls.ctc = mk("CTC", AccessLevel.CTC, cls.admin)
        cls.ldc1 = mk("LDC1", AccessLevel.LDC, cls.ctc)
        cls.ldc2 = mk("LDC2", AccessLevel.LDC, cls.ctc)
        cls.a1, cls.a2, cls.a3 = (mk(i, AccessLevel.IR, cls.ldc1) for i in ("A1", "A2", "A3"))
        cls.b1 = mk("B1", AccessLevel.IR, cls.ldc2)
        cls.player = mk("PL", AccessLevel.PLAYER, cls.ldc1)
        cls.team1 = Team.objects.create(name="T1", created_by=cls.ldc1)
        cls.team2 = Team.objects.create(name="T2", created_by=cls.ldc2)
        for ldc, team in ((cls.ldc1, cls.team1), (cls.ldc2, cls.team2)):
            TeamMember.objects.create(team=team, ir=ldc, role=TeamRole.LDC)
        for ir in (cls.a1, cls.a2, cls.a3, cls.player):
            TeamMember.objects.create(team=cls.team1, ir=ir, role=TeamRole.IR)
        TeamMember.objects.create(team=cls.team2, ir=cls.b1, role=TeamRole.IR)

    def place(self, who, ir, parent, leg):
        return self.client.post("/api/placement_tree/place/", data=json.dumps(
            {"requester_ir_id": who.ir_id, "ir_id": ir.ir_id, "parent_ir_id": parent.ir_id, "leg": leg}),
            content_type="application/json")

    def remove(self, who, ir):
        return self.client.post("/api/placement_tree/remove/", data=json.dumps(
            {"requester_ir_id": who.ir_id, "ir_id": ir.ir_id}), content_type="application/json")

    def tree(self, who):
        res = self.client.get("/api/placement_tree/", {"requester_ir_id": who.ir_id})
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def test_ldc_places_team_members_on_both_legs_and_below(self):
        self.assertEqual(self.place(self.ldc1, self.a1, self.ldc1, "L").status_code, 200)
        self.assertEqual(self.place(self.ldc1, self.a2, self.ldc1, "R").status_code, 200)
        self.assertEqual(self.place(self.ldc1, self.a3, self.a1, "L").status_code, 200)
        nodes = {n["ir_id"]: (n["parent_ir_id"], n["leg"]) for n in self.tree(self.ldc1)["nodes"]}
        self.assertEqual(nodes, {"A1": ("LDC1", "L"), "A2": ("LDC1", "R"), "A3": ("A1", "L")})

    def test_a_leg_holds_one_person(self):
        self.place(self.ldc1, self.a1, self.ldc1, "L")
        res = self.place(self.ldc1, self.a2, self.ldc1, "L")
        self.assertEqual(res.status_code, 409)
        self.assertIn("taken", res.json()["detail"])

    def test_unplaced_list_is_active_non_player_people_not_yet_in_a_tree(self):
        self.place(self.ldc1, self.a1, self.ldc1, "L")
        ids = {u["ir_id"] for u in self.tree(self.ldc1)["unplaced"]}
        self.assertEqual(ids, {"A2", "A3"})  # not A1 (placed), not the Player, not B1 (other team)

    def test_ldc_cannot_place_outside_their_people_or_their_tree(self):
        self.assertEqual(self.place(self.ldc1, self.b1, self.ldc1, "L").status_code, 403)
        self.assertEqual(self.place(self.ldc1, self.player, self.ldc1, "L").status_code, 403)
        self.place(self.ldc2, self.b1, self.ldc2, "L")
        # A spot under someone else's tree is not theirs to use.
        self.assertEqual(self.place(self.ldc1, self.a1, self.b1, "L").status_code, 403)

    def test_cannot_place_under_own_team_or_self(self):
        self.place(self.ldc1, self.a1, self.ldc1, "L")
        self.place(self.ldc1, self.a2, self.a1, "L")
        self.assertEqual(self.place(self.ldc1, self.a1, self.a2, "L").status_code, 400)
        self.assertEqual(self.place(self.ldc1, self.a1, self.a1, "R").status_code, 400)

    def test_moving_a_person_takes_their_team_along(self):
        self.place(self.ldc1, self.a1, self.ldc1, "L")
        self.place(self.ldc1, self.a3, self.a1, "L")
        self.place(self.ldc1, self.a2, self.ldc1, "R")
        self.assertEqual(self.place(self.ldc1, self.a1, self.a2, "L").status_code, 200)
        nodes = {n["ir_id"]: n["parent_ir_id"] for n in self.tree(self.ldc1)["nodes"]}
        self.assertEqual(nodes["A1"], "A2")
        self.assertEqual(nodes["A3"], "A1")

    def test_remove_only_leaves(self):
        self.place(self.ldc1, self.a1, self.ldc1, "L")
        self.place(self.ldc1, self.a3, self.a1, "L")
        self.assertEqual(self.remove(self.ldc1, self.a1).status_code, 400)
        self.assertEqual(self.remove(self.ldc1, self.a3).status_code, 200)
        self.assertEqual(self.remove(self.ldc1, self.a1).status_code, 200)
        self.assertEqual(self.tree(self.ldc1)["nodes"], [])

    def test_ctc_arranges_ldcs_and_their_people_and_ldc_sees_their_part(self):
        self.assertEqual(self.place(self.ctc, self.ldc1, self.ctc, "L").status_code, 200)
        self.assertEqual(self.place(self.ctc, self.ldc2, self.ctc, "R").status_code, 200)
        self.assertEqual(self.place(self.ctc, self.a1, self.ldc1, "L").status_code, 200)
        self.assertEqual({n["ir_id"] for n in self.tree(self.ctc)["nodes"]}, {"LDC1", "LDC2", "A1"})
        # The LDC now sees only what is under them.
        self.assertEqual({n["ir_id"] for n in self.tree(self.ldc1)["nodes"]}, {"A1"})

    def test_a_person_placed_in_the_ctcs_tree_is_not_pulled_out_by_a_sibling_ldc(self):
        self.place(self.ctc, self.ldc1, self.ctc, "L")
        self.place(self.ctc, self.ldc2, self.ctc, "R")
        self.place(self.ctc, self.a1, self.ldc1, "L")
        # A1 is also on LDC2's side if they were in both teams; LDC2 must not take them.
        TeamMember.objects.create(team=self.team2, ir=self.a1, role=TeamRole.IR)
        self.assertEqual(self.place(self.ldc2, self.a1, self.ldc2, "L").status_code, 403)

    def test_admin_arranges_everyone_including_loose_branches(self):
        self.assertEqual(self.place(self.admin, self.ctc, self.admin, "L").status_code, 200)
        self.assertEqual(self.place(self.admin, self.ldc1, self.ctc, "L").status_code, 200)
        # A branch left loose after its parent was removed can still be moved by Admin.
        Ir.objects.filter(pk="A1").update(placement_parent="LDC1", placement_leg="L")
        self.assertEqual(self.place(self.admin, self.a1, self.ctc, "R").status_code, 200)

    def test_other_roles_and_bad_input_are_refused(self):
        self.assertEqual(self.client.get("/api/placement_tree/", {"requester_ir_id": "A1"}).status_code, 403)
        self.assertEqual(self.client.get("/api/placement_tree/", {"requester_ir_id": "NOPE"}).status_code, 400)
        self.assertEqual(self.place(self.ldc1, self.a1, self.ldc1, "X").status_code, 400)

    def test_deleting_someone_frees_their_team_instead_of_leaving_a_stray_leg(self):
        self.place(self.ldc1, self.a1, self.ldc1, "L")
        self.place(self.ldc1, self.a3, self.a1, "L")
        self.a1.refresh_from_db()
        self.a1.delete()
        a3 = Ir.objects.get(ir_id="A3")
        self.assertIsNone(a3.placement_parent_id)
        self.assertIsNone(a3.placement_leg)
