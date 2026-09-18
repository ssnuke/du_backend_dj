import datetime
import json

from django.test import TestCase, override_settings

from core.models import (Ir, Team, TeamMember, Pocket, PocketMember,
                         AccessLevel, TeamRole)
from django.core.cache import cache
from core.utils.dates import get_week_info_friday_to_friday, get_week_info_monday_to_sunday

WEEK, YEAR = 32, 2026


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class LdcPocketDashboardCacheInvalidationTests(TestCase):
    """
    GetLdcPocketDashboard's cache had a 30-second TTL and nothing that ever
    actively invalidated it. Reported as: a rep's own dashboard (computed
    live) showed 4 infos for the week, while their LDC's pocket screen —
    served from a cache populated before those infos were logged — showed 0
    for the same person, same week, at the same moment. Not a counting bug:
    the pocket screen was correctly answering a question from before the
    write happened, with nothing telling it the answer had changed.

    Every test here forces the cache to populate FIRST (an initial GET),
    exactly as a real client would, then performs the write through its real
    HTTP endpoint (not a direct model .create(), which would trivially not
    exercise this) and re-reads — that ordering is what actually reproduces
    the bug this pins against.
    """

    @classmethod
    def setUpTestData(cls):
        _, _, cls.ws, cls.we = get_week_info_friday_to_friday(week_number=WEEK, year=YEAR)
        cls.seq = 0

        def mk(name, lvl=AccessLevel.IR):
            cls.seq += 1
            return Ir.objects.create(ir_id=f"C{cls.seq:06d}", ir_name=name,
                                     ir_email=f"c{cls.seq}@t.t", ir_password="x",
                                     ir_access_level=lvl, status=True)

        cls.ldc = mk("The LDC", AccessLevel.LDC)
        cls.pocketed_team = Team.objects.create(name="Has Pockets", created_by=cls.ldc)
        cls.pocket = Pocket.objects.create(team=cls.pocketed_team, name="Dream Team", is_active=True)
        cls.member = mk("Hrithika")
        TeamMember.objects.create(team=cls.pocketed_team, ir=cls.member, role=TeamRole.IR)
        PocketMember.objects.create(pocket=cls.pocket, ir=cls.member, role=TeamRole.IR, is_head=False)

        cls.teamless_team = Team.objects.create(name="No Pockets Yet", created_by=cls.ldc)
        cls.teamless_member = mk("Teamless Member")
        TeamMember.objects.create(team=cls.teamless_team, ir=cls.teamless_member, role=TeamRole.IR)

    def setUp(self):
        # locmem is a process-level dict, not part of the DB transaction each
        # test rolls back — without this, one test's cached response leaks
        # into the next and produces exactly the kind of false result this
        # suite exists to catch.
        cache.clear()

    def dashboard(self):
        r = self.client.get("/api/ldc_pocket_dashboard/",
                            {"requester_ir_id": self.ldc.ir_id, "week": WEEK, "year": YEAR})
        self.assertEqual(r.status_code, 200, r.content[:200])
        return r.json()["pockets"]

    def member_row(self, pockets, pocket_label, ir_id):
        pocket = next(p for p in pockets if p["label"] == pocket_label)
        return next(m for m in pocket["members"] if m["ir_id"] == ir_id)

    def midweek_iso(self, offset_days=1):
        """
        A moment safely inside BOTH the info window and the plan window for
        WEEK/YEAR. The plan (Monday-Sunday) window is fully contained inside
        the wider info (Friday-Friday) window for the same week_number, so
        anchoring on the plan week's Monday — not the info week's Friday
        start — is what guarantees that.
        """
        plan_start, _, _, _ = (None, None, None, None)
        _, _, plan_start, _ = get_week_info_monday_to_sunday(week_number=WEEK, year=YEAR)
        return (plan_start + datetime.timedelta(days=offset_days, hours=2)).isoformat()

    # ── the reported bug, on the pocketed path ──────────────────────────────
    def test_a_fresh_info_appears_immediately_not_after_the_cache_expires(self):
        before = self.dashboard()
        self.assertEqual(self.member_row(before, "Dream Team", self.member.ir_id)["info_done"], 0)

        r = self.client.post(f"/api/add_info_detail/{self.member.ir_id}/",
                             data=json.dumps({"requester_ir_id": self.member.ir_id,
                                              "response": "A", "info_name": "Prospect",
                                              "info_date": self.midweek_iso()}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content[:300])

        after = self.dashboard()
        self.assertEqual(self.member_row(after, "Dream Team", self.member.ir_id)["info_done"], 1,
                         "the pocket screen must reflect the info immediately, not after 30s")

    def test_a_fresh_plan_appears_immediately_too(self):
        before = self.dashboard()
        self.assertEqual(self.member_row(before, "Dream Team", self.member.ir_id)["plan_done"], 0)

        r = self.client.post(f"/api/add_plan_detail/{self.member.ir_id}/",
                             data=json.dumps({"requester_ir_id": self.member.ir_id,
                                              "plan_name": "Prospect", "status": "closing_pending",
                                              "plan_date": self.midweek_iso()}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content[:300])

        after = self.dashboard()
        self.assertEqual(self.member_row(after, "Dream Team", self.member.ir_id)["plan_done"], 1)

    # ── the teamless (whole-team-card) path must invalidate too ─────────────
    def test_a_fresh_info_for_a_teamless_member_appears_immediately(self):
        before = self.dashboard()
        team_card = next(p for p in before if p["team_id"] == self.teamless_team.id)
        self.assertEqual(
            next(m for m in team_card["members"] if m["ir_id"] == self.teamless_member.ir_id)["info_done"], 0
        )

        r = self.client.post(f"/api/add_info_detail/{self.teamless_member.ir_id}/",
                             data=json.dumps({"requester_ir_id": self.teamless_member.ir_id,
                                              "response": "A", "info_name": "Prospect",
                                              "info_date": self.midweek_iso()}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content[:300])

        after = self.dashboard()
        team_card = next(p for p in after if p["team_id"] == self.teamless_team.id)
        self.assertEqual(
            next(m for m in team_card["members"] if m["ir_id"] == self.teamless_member.ir_id)["info_done"], 1
        )

    # ── delete and edit, the other direction of the same bug ────────────────
    def test_deleting_an_info_drops_the_count_immediately(self):
        r = self.client.post(f"/api/add_info_detail/{self.member.ir_id}/",
                             data=json.dumps({"requester_ir_id": self.member.ir_id,
                                              "response": "A", "info_name": "Prospect",
                                              "info_date": self.midweek_iso()}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content[:300])
        info_id = r.json()["info_ids"][0]

        populated = self.dashboard()
        self.assertEqual(self.member_row(populated, "Dream Team", self.member.ir_id)["info_done"], 1)

        d = self.client.delete(f"/api/delete_info_detail/{info_id}/",
                               {"requester_ir_id": self.member.ir_id})
        self.assertEqual(d.status_code, 200, d.content[:300])

        after = self.dashboard()
        self.assertEqual(self.member_row(after, "Dream Team", self.member.ir_id)["info_done"], 0,
                         "a deleted info must not leave a stale, too-high count cached")

    def test_editing_an_infos_date_invalidates_both_the_old_and_new_week(self):
        from core.models import InfoDetail
        info = InfoDetail.objects.create(ir=self.member, response="A", info_name="Prospect",
                                         info_date=self.ws + datetime.timedelta(days=1, hours=2))

        # Populate the cache for week WEEK (the info's original week).
        self.dashboard()

        _, _, next_ws, _ = get_week_info_friday_to_friday(week_number=WEEK + 1, year=YEAR)
        r = self.client.put(f"/api/update_info_detail/{info.id}/",
                            data=json.dumps({"requester_ir_id": self.member.ir_id,
                                             "info_date": (next_ws + datetime.timedelta(hours=2)).isoformat()}),
                            content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content[:300])

        # The old week must no longer count it...
        old_week = self.dashboard()
        self.assertEqual(self.member_row(old_week, "Dream Team", self.member.ir_id)["info_done"], 0)

        # ...and the new week must show it immediately.
        r2 = self.client.get("/api/ldc_pocket_dashboard/",
                             {"requester_ir_id": self.ldc.ir_id, "week": WEEK + 1, "year": YEAR})
        self.assertEqual(r2.status_code, 200, r2.content[:200])
        new_week = r2.json()["pockets"]
        self.assertEqual(self.member_row(new_week, "Dream Team", self.member.ir_id)["info_done"], 1)
