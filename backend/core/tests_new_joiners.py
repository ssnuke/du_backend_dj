from datetime import date

from django.test import TestCase, override_settings

from core.models import AccessLevel, Ir, Team, TeamMember, TeamRole

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
OCT, SEP = date(2026, 10, 12), date(2026, 9, 20)


@override_settings(CACHES=LOCMEM)
class NewJoinersTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, level=AccessLevel.IR, when=OCT):
            ir = Ir.objects.create(ir_id=ir_id, ir_name=f"Name {ir_id}", ir_email=f"{ir_id}@t.t",
                                   ir_password="x", ir_access_level=level, status=True)
            # started_date is auto_now_add, so set it after creation.
            Ir.objects.filter(pk=ir.pk).update(started_date=when)
            return ir

        cls.admin = mk("ADM", AccessLevel.ADMIN, date(2025, 1, 1))
        cls.ctc = mk("CTC", AccessLevel.CTC, date(2025, 1, 1))
        cls.ldc_a = mk("LDCA", AccessLevel.LDC, date(2025, 1, 1))
        cls.ldc_b = mk("LDCB", AccessLevel.LDC, date(2025, 1, 1))
        cls.team_a = Team.objects.create(name="A", created_by=cls.ldc_a)
        cls.team_b = Team.objects.create(name="B", created_by=cls.ldc_b)
        for ldc, team in ((cls.ldc_a, cls.team_a), (cls.ldc_b, cls.team_b)):
            TeamMember.objects.create(team=team, ir=ldc, role=TeamRole.LDC)

        cls.a1 = mk("A1")
        cls.a2 = mk("A2", AccessLevel.PLAYER)
        cls.a_old = mk("AOLD", when=SEP)
        cls.b1 = mk("B1")
        cls.both = mk("BOTH")
        cls.loner = mk("LONER")
        for ir, team in ((cls.a1, cls.team_a), (cls.a2, cls.team_a), (cls.a_old, cls.team_a),
                         (cls.b1, cls.team_b), (cls.both, cls.team_a), (cls.both, cls.team_b)):
            TeamMember.objects.create(team=team, ir=ir, role=TeamRole.IR)

    def get(self, who, **params):
        res = self.client.get("/api/new_joiners/", {"requester_ir_id": who.ir_id, **params})
        return res

    def month(self, res, year, month):
        return next(m for m in res.json()["months"] if (m["year"], m["month"]) == (year, month))

    def test_ldc_sees_only_their_teams_joiners(self):
        res = self.get(self.ldc_a, year=2026, month=10)
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body["scope"], "team")
        ids = {p["ir_id"] for p in body["selected"]["people"]}
        self.assertEqual(ids, {"A1", "A2", "BOTH"})
        self.assertEqual(body["selected"]["total"], 3)
        self.assertEqual(self.month(res, 2026, 10)["total"], 3)
        self.assertEqual(self.month(res, 2026, 9)["total"], 1)

    def test_admin_sees_everyone_grouped_by_ldc(self):
        body = self.get(self.admin, year=2026, month=10).json()
        self.assertEqual(body["scope"], "all")
        groups = {g["ldc_ir_id"]: g for g in body["selected"]["groups"]}
        self.assertEqual({p["ir_id"] for p in groups["LDCA"]["people"]}, {"A1", "A2", "BOTH"})
        self.assertEqual({p["ir_id"] for p in groups["LDCB"]["people"]}, {"B1", "BOTH"})
        self.assertEqual({p["ir_id"] for p in groups[None]["people"]}, {"LONER"})
        self.assertEqual(groups[None]["ldc_name"], "Not in an LDC's team")
        # Five distinct people joined in October (BOTH counted once overall).
        self.assertEqual(body["selected"]["total"], 5)

    def test_each_ldcs_own_figure_matches_their_row_in_the_admin_breakdown(self):
        admin_groups = {g["ldc_ir_id"]: g["count"]
                        for g in self.get(self.admin, year=2026, month=10).json()["selected"]["groups"]}
        for ldc in (self.ldc_a, self.ldc_b):
            own = self.get(ldc, year=2026, month=10).json()["selected"]["total"]
            self.assertEqual(own, admin_groups[ldc.ir_id])

    def test_other_roles_are_refused(self):
        for who in (self.ctc, self.a1, self.a2):
            self.assertEqual(self.get(who).status_code, 403)

    def test_unknown_requester_and_bad_month(self):
        self.assertEqual(self.client.get("/api/new_joiners/", {"requester_ir_id": "NOPE"}).status_code, 400)
        self.assertEqual(self.get(self.admin, year=2026, month=13).status_code, 400)

    def test_a_month_outside_the_window_can_still_be_opened(self):
        body = self.get(self.admin, year=2020, month=3, months=3).json()
        self.assertEqual(body["selected"]["total"], 0)
        self.assertIn((2020, 3), [(m["year"], m["month"]) for m in body["months"]])

    def test_an_ldc_is_not_on_their_own_list_and_admin_ctc_are_not_team_members(self):
        TeamMember.objects.create(team=self.team_a, ir=self.ctc, role=TeamRole.CTC)
        Ir.objects.filter(pk=self.ctc.pk).update(started_date=OCT)
        Ir.objects.filter(pk=self.ldc_a.pk).update(started_date=OCT)
        ids = {p["ir_id"] for p in self.get(self.ldc_a, year=2026, month=10).json()["selected"]["people"]}
        self.assertNotIn("LDCA", ids)
        self.assertNotIn("CTC", ids)
