import datetime
from django.test import TestCase, override_settings
from core.models import (Ir, Team, TeamMember, Pocket, PocketMember,
                         InfoDetail, PlanDetail, AccessLevel, TeamRole)
from core.utils.dates import get_week_info_friday_to_friday

WEEK, YEAR = 32, 2026


# The view caches through the project's real Redis, which isn't reachable
# from a test run. A local in-memory cache keeps the view's own logic under
# test without depending on external infrastructure.
@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class LdcPocketDashboardTeamlessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _, _, cls.ws, cls.we = get_week_info_friday_to_friday(week_number=WEEK, year=YEAR)
        cls.seq = 0

        def mk(name, lvl=AccessLevel.IR):
            cls.seq += 1
            return Ir.objects.create(ir_id=f"T{cls.seq:06d}", ir_name=name,
                                     ir_email=f"t{cls.seq}@t.t", ir_password="x",
                                     ir_access_level=lvl, status=True)

        cls.ldc = mk("The LDC", AccessLevel.LDC)

        # Team A has a pocket; Team B has none and must still appear.
        cls.team_a = Team.objects.create(name="Has Pockets", created_by=cls.ldc)
        cls.team_b = Team.objects.create(name="No Pockets Yet", created_by=cls.ldc)

        pk = Pocket.objects.create(team=cls.team_a, name="Pocket One", is_active=True)
        for i, infos in enumerate([4, 2]):
            p = mk(f"A member {i}")
            TeamMember.objects.create(team=cls.team_a, ir=p, role=TeamRole.IR)
            PocketMember.objects.create(pocket=pk, ir=p, role=TeamRole.IR, is_head=(i == 0))
            for k in range(infos):
                InfoDetail.objects.create(ir=p, response="A", info_name=f"x{k}",
                                          info_date=cls.ws + datetime.timedelta(days=k % 5, hours=2))

        for i, infos in enumerate([7, 0, 3]):
            p = mk(f"B member {i}")
            TeamMember.objects.create(team=cls.team_b, ir=p, role=TeamRole.IR)
            for k in range(infos):
                InfoDetail.objects.create(ir=p, response="A", info_name=f"y{k}",
                                          info_date=cls.ws + datetime.timedelta(days=k % 5, hours=2))

        # A deactivated member must not inflate the whole-team card.
        gone = mk("Left the org")
        gone.status = False
        gone.save()
        TeamMember.objects.create(team=cls.team_b, ir=gone, role=TeamRole.IR)

    def rows(self):
        r = self.client.get("/api/ldc_pocket_dashboard/",
                            {"requester_ir_id": self.ldc.ir_id, "week": WEEK, "year": YEAR})
        self.assertEqual(r.status_code, 200, r.content[:200])
        return r.json()["pockets"]

    def test_team_without_pockets_gets_a_whole_team_card(self):
        rows = self.rows()
        by_label = {p["label"]: p for p in rows}
        self.assertIn("Pocket One", by_label)
        self.assertIn("No Pockets Yet", by_label)

        whole = by_label["No Pockets Yet"]
        self.assertEqual(whole["kind"], "team")
        self.assertEqual(whole["totals"]["info_done"], 10)   # 7 + 0 + 3
        self.assertEqual(whole["member_count"], 3)           # deactivated excluded
        self.assertEqual(whole["active_count"], 2)
        self.assertEqual(whole["inactive_count"], 1)
        self.assertEqual(len(whole["members"]), 3)

    def test_ids_cannot_collide_with_pocket_ids(self):
        rows = self.rows()
        ids = [p["id"] for p in rows]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(any(str(i).startswith("team-") for i in ids))

    def test_a_pocketed_team_is_not_also_shown_whole(self):
        labels = [p["label"] for p in self.rows()]
        self.assertNotIn("Has Pockets", labels)
