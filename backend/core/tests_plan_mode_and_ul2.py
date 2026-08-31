import datetime
from django.test import TestCase, override_settings
from django.utils import timezone
from core.models import (Ir, AccessLevel, PlanDetail, UVDetail, Team, TeamMember, TeamRole)


LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class PlanModeAndUl2FilterTests(TestCase):
    """
    Two questions this month's data has to be able to answer: "how much did
    this UL2 convert" and "does virtual or physical close better". Both are
    filters over the same month, so both are pinned here together.
    """

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, name, lvl=AccessLevel.IR, parent=None):
            return Ir.objects.create(ir_id=ir_id, ir_name=name, ir_email=f"{ir_id}@t.t",
                                     ir_password="x", ir_access_level=lvl, status=True,
                                     parent_ir=parent)

        cls.ldc = mk("MLDC", "The LDC", AccessLevel.LDC)
        cls.owner = mk("MOWNER", "Plan Owner", AccessLevel.IR, parent=cls.ldc)
        cls.ul2_a = mk("MUL2A", "Ravi", AccessLevel.LS, parent=cls.ldc)
        cls.ul2_b = mk("MUL2B", "Anita", AccessLevel.LS, parent=cls.ldc)

        # An LDC's plan scope is the teams they CREATED, not their hierarchy
        # subtree — see get_plan_visible_member_ids. Without this team the
        # LDC sees none of the plans below.
        cls.team = Team.objects.create(name="The Team", created_by=cls.ldc)
        for p in (cls.owner, cls.ul2_a, cls.ul2_b):
            TeamMember.objects.create(team=cls.team, ir=p, role=TeamRole.IR)

        # Mid-month so the date lands inside the month's week windows
        # regardless of which month the suite happens to run in.
        cls.when = timezone.now().replace(day=15, hour=12, minute=0, second=0, microsecond=0)

        def plan(presenter, mode, status):
            return PlanDetail.objects.create(
                ir=cls.owner, presented_by=presenter, plan_date=cls.when,
                plan_name="P", status=status, plan_mode=mode,
            )

        # Ravi: 2 virtual, both converted. Anita: 2 physical, 1 converted.
        plan(cls.ul2_a, "virtual", "closed")
        plan(cls.ul2_a, "virtual", "closed")
        plan(cls.ul2_b, "physical", "closed")
        plan(cls.ul2_b, "physical", "rejected")
        # One legacy row with no mode recorded at all.
        plan(cls.ul2_a, None, "closing_pending")

    def summary(self, **params):
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"/api/monthly_plan_summary/{self.ldc.ir_id}/?month={self.when.month}&year={self.when.year}"
        r = self.client.get(f"{url}&{qs}" if qs else url)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_unfiltered_month_counts_every_plan(self):
        self.assertEqual(self.summary()["month_total"]["total"], 5)

    def test_ul2_filter_narrows_to_that_presenter(self):
        data = self.summary(presented_by="MUL2A")
        self.assertEqual(data["month_total"]["total"], 3)
        self.assertEqual(data["month_total"]["closed"], 2)
        self.assertEqual(data["applied_presented_by"], "MUL2A")

    def test_presenter_roster_survives_being_filtered(self):
        """
        Built from the unfiltered month, so picking a UL2 never collapses the
        dropdown to the one option already chosen and strands the user.
        """
        names = [p["ir_name"] for p in self.summary(presented_by="MUL2A")["presenters"]]
        self.assertEqual(names, ["Anita", "Ravi"])  # sorted by name

    def test_mode_totals_separate_virtual_physical_and_unset(self):
        modes = self.summary()["mode_totals"]
        self.assertEqual(modes["virtual"]["total"], 2)
        self.assertEqual(modes["virtual"]["closed"], 2)
        self.assertEqual(modes["physical"]["total"], 2)
        self.assertEqual(modes["physical"]["closed"], 1)
        # The legacy row is its own answer, not folded into either side.
        self.assertEqual(modes["unset"]["total"], 1)

    def test_mode_filter_narrows_the_month(self):
        self.assertEqual(self.summary(plan_mode="physical")["month_total"]["total"], 2)
        self.assertEqual(self.summary(plan_mode="virtual")["month_total"]["total"], 2)

    def test_unset_is_selectable_so_the_legacy_backlog_is_reachable(self):
        data = self.summary(plan_mode="unset")
        self.assertEqual(data["month_total"]["total"], 1)

    def test_a_bogus_mode_is_ignored_rather_than_returning_nothing(self):
        self.assertEqual(self.summary(plan_mode="carrier-pigeon")["month_total"]["total"], 5)

    def test_ul2_and_mode_filters_combine(self):
        self.assertEqual(self.summary(presented_by="MUL2A", plan_mode="virtual")["month_total"]["total"], 2)
        self.assertEqual(self.summary(presented_by="MUL2B", plan_mode="virtual")["month_total"]["total"], 0)

    def test_infos_are_flagged_as_not_answering_a_filtered_question(self):
        """
        Infos carry no UL2 and no mode. Rather than show counts that ignore
        the active filter, the payload tells the client to hide the strip.
        """
        self.assertTrue(self.summary()["infos_apply_to_filter"])
        self.assertFalse(self.summary(presented_by="MUL2A")["infos_apply_to_filter"])
        self.assertFalse(self.summary(plan_mode="virtual")["infos_apply_to_filter"])


    # ── UVs by week ──────────────────────────────────────────────────────
    def test_uv_weekly_comes_from_the_uv_ledger_not_from_plan_values(self):
        """
        The by-week strips count the records themselves — infos from
        InfoDetail, plans from PlanDetail, UVs from UVDetail. A plan carrying
        a uv_value is not a UV record and must not appear here.
        """
        UVDetail.objects.create(ir=self.owner, ir_name="x", uv_date=self.when, uv_count=6)
        UVDetail.objects.create(ir=self.owner, ir_name="x", uv_date=self.when, uv_count=1.5)
        PlanDetail.objects.create(ir=self.owner, plan_date=self.when, plan_name="not a UV",
                                  status="uvs_on_counter", uv_value=99)
        data = self.summary()
        self.assertEqual(data["uv_month_total"], 7.5)
        self.assertEqual(sum(data["uv_weekly"].values()), 7.5)

    def test_uv_weekly_keeps_halves_rather_than_rounding_to_whole(self):
        UVDetail.objects.create(ir=self.owner, ir_name="x", uv_date=self.when, uv_count=2.5)
        self.assertEqual(self.summary()["uv_month_total"], 2.5)

    def test_uv_weekly_is_scoped_to_the_viewers_people(self):
        outsider = Ir.objects.create(ir_id="MOUT", ir_name="Outsider", ir_email="o@t.t",
                                     ir_password="x", ir_access_level=AccessLevel.IR, status=True)
        UVDetail.objects.create(ir=outsider, ir_name="x", uv_date=self.when, uv_count=50)
        self.assertEqual(self.summary()["uv_month_total"], 0)

    def test_uv_weekly_is_withheld_when_a_plan_filter_is_active(self):
        """UVs carry no UL2 or plan mode, same as infos, so a filtered view
        cannot answer with them."""
        UVDetail.objects.create(ir=self.owner, ir_name="x", uv_date=self.when, uv_count=6)
        self.assertEqual(self.summary()["uv_month_total"], 6)
        self.assertEqual(self.summary(presented_by="MUL2A")["uv_month_total"], 0)

    def test_uv_sum_is_not_double_counted_across_week_and_mode_buckets(self):
        PlanDetail.objects.create(ir=self.owner, presented_by=self.ul2_a,
                                  plan_date=self.when, plan_name="UV",
                                  status="uvs_on_counter", plan_mode="virtual", uv_value=10)
        data = self.summary()
        self.assertEqual(data["month_total"]["uv_value_sum"], 10)
        self.assertEqual(data["mode_totals"]["virtual"]["uv_value_sum"], 10)

    def test_uv_total_counts_only_uvs_on_the_counter(self):
        """
        The figure used to sum uv_value off In-process plans, which is a
        projection of what might land — it read as banked business while the
        plan was still being worked.
        """
        PlanDetail.objects.create(ir=self.owner, plan_date=self.when, plan_name="Landed",
                                  status="uvs_on_counter", uv_value=6)
        PlanDetail.objects.create(ir=self.owner, plan_date=self.when, plan_name="Still working",
                                  status="closed", uv_value=100)
        PlanDetail.objects.create(ir=self.owner, plan_date=self.when, plan_name="Pending",
                                  status="closing_pending", uv_value=50)
        PlanDetail.objects.create(ir=self.owner, plan_date=self.when, plan_name="Dead",
                                  status="rejected", uv_value=25)
        self.assertEqual(self.summary()["month_total"]["uv_value_sum"], 6)

    def test_the_in_process_COUNT_is_untouched_by_the_uv_change(self):
        """Only the UV figure changed; the status counts must not move."""
        before = self.summary()["month_total"]["closed"]
        PlanDetail.objects.create(ir=self.owner, plan_date=self.when, plan_name="X",
                                  status="closed", uv_value=100)
        self.assertEqual(self.summary()["month_total"]["closed"], before + 1)


@override_settings(CACHES=LOCMEM)
class PlanPartialUpdateTests(TestCase):
    """
    The UL2 bug: the client sent presented_by on EVERY plan update, so saving
    a status change from a screen that did not know about UL2 sent null and
    silently wiped it. The server updates partially, so the contract the fix
    relies on is: an omitted key is left alone, an explicit null clears.
    """

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, name, lvl=AccessLevel.IR):
            return Ir.objects.create(ir_id=ir_id, ir_name=name, ir_email=f"{ir_id}@t.t",
                                     ir_password="x", ir_access_level=lvl, status=True)
        cls.owner = mk("UOWNER", "Owner")
        cls.ul2 = mk("UUL2", "Ravi", AccessLevel.LS)

    def setUp(self):
        self.plan = PlanDetail.objects.create(
            ir=self.owner, presented_by=self.ul2, plan_name="P",
            status="closing_pending", plan_mode="virtual",
            rejection_reason="timing", plan_date=timezone.now(),
        )

    def put(self, payload):
        r = self.client.put(f"/api/update_plan_detail/{self.plan.id}/",
                            data=payload, content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        self.plan.refresh_from_db()

    def test_omitting_presented_by_preserves_it(self):
        """This is the exact shape a status-only edit now sends."""
        self.put({"ir": self.owner.ir_id, "plan_name": "P", "comments": "c", "status": "closed"})
        self.assertEqual(self.plan.presented_by_id, self.ul2.ir_id)
        self.assertEqual(self.plan.status, "closed")

    def test_omitting_plan_mode_preserves_it(self):
        self.put({"ir": self.owner.ir_id, "plan_name": "P", "comments": "c", "status": "closed"})
        self.assertEqual(self.plan.plan_mode, "virtual")

    def test_omitting_rejection_reason_preserves_it(self):
        """Same class of bug, same wipe — it rode along on the same payload."""
        self.put({"ir": self.owner.ir_id, "plan_name": "P", "comments": "c", "status": "kiv"})
        self.assertEqual(self.plan.rejection_reason, "timing")

    def test_explicit_null_still_clears(self):
        """Clearing the chip in the editor must keep working."""
        self.put({"ir": self.owner.ir_id, "plan_name": "P", "comments": "c", "presented_by": None})
        self.assertIsNone(self.plan.presented_by_id)

    def test_plan_mode_can_be_changed_and_cleared(self):
        self.put({"ir": self.owner.ir_id, "plan_name": "P", "comments": "c", "plan_mode": "physical"})
        self.assertEqual(self.plan.plan_mode, "physical")
        self.put({"ir": self.owner.ir_id, "plan_name": "P", "comments": "c", "plan_mode": None})
        self.assertIsNone(self.plan.plan_mode)

    def test_an_invalid_plan_mode_is_rejected_not_stored(self):
        r = self.client.put(f"/api/update_plan_detail/{self.plan.id}/",
                            data={"ir": self.owner.ir_id, "plan_name": "P",
                                  "comments": "c", "plan_mode": "telepathy"},
                            content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.plan_mode, "virtual")

