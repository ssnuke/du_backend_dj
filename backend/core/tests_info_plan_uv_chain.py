import json
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import (AccessLevel, InfoDetail, Ir, PlanDetail, Team,
                         TeamMember, TeamRole, UVDetail)

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class InfoPlanUvChainTests(TestCase):
    """
    One prospect, one trail: a UV hangs off the plan it converted, and a plan
    off the info it grew out of — so the figures can never disagree because of
    a re-typed name.
    """

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, name, lvl=AccessLevel.IR, parent=None):
            return Ir.objects.create(ir_id=ir_id, ir_name=name, ir_email=f"{ir_id}@t.t",
                                     ir_password="x", ir_access_level=lvl, status=True,
                                     parent_ir=parent)

        cls.ldc = mk("CLDC", "The LDC", AccessLevel.LDC)
        cls.owner = mk("COWNER", "Snehith", AccessLevel.IR, parent=cls.ldc)
        cls.other = mk("COTHER", "Someone Else", AccessLevel.IR, parent=cls.ldc)
        cls.team = Team.objects.create(name="T", created_by=cls.ldc)
        for p in (cls.owner, cls.other):
            TeamMember.objects.create(team=cls.team, ir=p, role=TeamRole.IR)

        cls.when = timezone.now().replace(day=15, hour=12, minute=0, second=0, microsecond=0)

    def post(self, url, body):
        return self.client.post(url, data=json.dumps(body), content_type="application/json")

    def plan(self, name="Ravi", status="closed", ir=None, **kw):
        return PlanDetail.objects.create(ir=ir or self.owner, plan_name=name, plan_date=self.when,
                                         status=status, uv_value=5, **kw)

    def add_uv(self, **item):
        item.setdefault("uv_count", 4)
        return self.post(f"/api/add_uv/{self.owner.ir_id}/", item)

    # --- UV -> plan -------------------------------------------------------

    def test_adding_uv_against_plan_converts_it(self):
        plan = self.plan()
        r = self.add_uv(plan_id=plan.id, prospect_name="typo")
        self.assertEqual(r.status_code, 201, r.content)

        plan.refresh_from_db()
        self.assertEqual(plan.status, "uvs_on_counter")
        uv = UVDetail.objects.get()
        self.assertEqual(uv.plan_id, plan.id)
        # Name comes from the plan, not from whatever was typed.
        self.assertEqual(uv.prospect_name, "Ravi")

    def test_plan_of_another_ir_is_refused(self):
        theirs = self.plan(ir=self.other)
        r = self.add_uv(plan_id=theirs.id)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(UVDetail.objects.count(), 0)
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, "closed")

    def test_rejected_plan_is_refused(self):
        plan = self.plan(status="rejected")
        self.assertEqual(self.add_uv(plan_id=plan.id).status_code, 400)

    def test_bad_row_rolls_back_the_whole_batch(self):
        good = self.plan("Good")
        r = self.post(f"/api/add_uv/{self.owner.ir_id}/", [
            {"uv_count": 2, "plan_id": good.id},
            {"uv_count": 2, "plan_id": 999999},
        ])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(UVDetail.objects.count(), 0)
        good.refresh_from_db()
        self.assertEqual(good.status, "closed")

    def test_walk_in_uv_still_works_without_a_plan(self):
        r = self.add_uv(prospect_name="Walk In")
        self.assertEqual(r.status_code, 201, r.content)
        uv = UVDetail.objects.get()
        self.assertIsNone(uv.plan_id)
        self.assertEqual(uv.prospect_name, "Walk In")

    def test_deleting_last_uv_reverts_plan(self):
        plan = self.plan()
        self.add_uv(plan_id=plan.id, uv_count=2)
        self.add_uv(plan_id=plan.id, uv_count=2)
        first, second = UVDetail.objects.order_by("id")

        self.client.delete(f"/api/delete_uv_detail/{first.id}/")
        plan.refresh_from_db()
        self.assertEqual(plan.status, "uvs_on_counter", "one instalment is still on the counter")

        self.client.delete(f"/api/delete_uv_detail/{second.id}/")
        plan.refresh_from_db()
        self.assertEqual(plan.status, "closed")

    # --- plan -> info -----------------------------------------------------

    def test_plan_can_be_created_from_an_info(self):
        info = InfoDetail.objects.create(ir=self.owner, response="A", info_name="Ravi", info_date=self.when)
        r = self.post(f"/api/add_plan_detail/{self.owner.ir_id}/",
                      [{"plan_name": "Ravi", "info_id": info.id, "plan_date": self.when.isoformat()}])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(PlanDetail.objects.get().info_id, info.id)

    def test_info_of_another_ir_is_refused(self):
        info = InfoDetail.objects.create(ir=self.other, response="A", info_name="X", info_date=self.when)
        r = self.post(f"/api/add_plan_detail/{self.owner.ir_id}/",
                      [{"plan_name": "X", "info_id": info.id, "plan_date": self.when.isoformat()}])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(PlanDetail.objects.count(), 0)

    def test_location_travels_from_info_to_plan(self):
        r = self.post(f"/api/add_info_detail/{self.owner.ir_id}/",
                      [{"info_name": "Ravi", "response": "A", "location": "  Hyderabad ",
                        "info_date": self.when.isoformat()}])
        self.assertEqual(r.status_code, 201, r.content)
        info = InfoDetail.objects.get()
        self.assertEqual(info.location, "Hyderabad")

        r = self.post(f"/api/add_plan_detail/{self.owner.ir_id}/",
                      [{"plan_name": "Ravi", "info_id": info.id, "plan_date": self.when.isoformat()}])
        self.assertEqual(r.status_code, 201, r.content)
        plan = PlanDetail.objects.get()
        self.assertEqual(plan.location, "Hyderabad", "inherited from the info")
        picker = self.client.get(f"/api/uv_candidate_plans/{self.owner.ir_id}/"
                                 f"?month={self.when.month}&year={self.when.year}").json()
        self.assertEqual(picker["plans"][0]["location"], "Hyderabad")
        listing = self.client.get(f"/api/plan_details/{self.owner.ir_id}/").json()
        self.assertEqual(listing[0]["location"], "Hyderabad")
        self.assertTrue(plan.pk)

    def test_plan_without_info_can_carry_its_own_location(self):
        r = self.post(f"/api/add_plan_detail/{self.owner.ir_id}/",
                      [{"plan_name": "New", "location": "Pune", "plan_date": self.when.isoformat()}])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(PlanDetail.objects.get().location, "Pune")

    # --- pickers ----------------------------------------------------------

    def test_uv_picker_lists_only_pending_plans_with_months(self):
        pending = self.plan("Pending")
        self.plan("Done", status="uvs_on_counter")
        self.plan("Dead", status="rejected")
        r = self.client.get(f"/api/uv_candidate_plans/{self.owner.ir_id}/"
                            f"?month={self.when.month}&year={self.when.year}")
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual([p["id"] for p in body["plans"]], [pending.id])
        self.assertIn((self.when.month, self.when.year), [(m["month"], m["year"]) for m in body["months"]])

    def test_uv_picker_reaches_back_to_an_earlier_month(self):
        old = self.plan("Old")
        PlanDetail.objects.filter(pk=old.pk).update(plan_date=self.when - timezone.timedelta(days=62))
        old.refresh_from_db()
        r = self.client.get(f"/api/uv_candidate_plans/{self.owner.ir_id}/"
                            f"?month={old.plan_date.month}&year={old.plan_date.year}")
        self.assertEqual([p["id"] for p in r.json()["plans"]], [old.id])

    def test_info_picker_hides_infos_that_already_have_a_plan(self):
        used = InfoDetail.objects.create(ir=self.owner, response="A", info_name="Used", info_date=self.when)
        free = InfoDetail.objects.create(ir=self.owner, response="B", info_name="Free", info_date=self.when)
        self.plan("Used", info=used)
        r = self.client.get(f"/api/plan_candidate_infos/{self.owner.ir_id}/"
                            f"?month={self.when.month}&year={self.when.year}")
        self.assertEqual([i["id"] for i in r.json()["infos"]], [free.id])

    # --- monthly figures --------------------------------------------------

    def test_monthly_uvs_use_actual_not_quoted(self):
        plan = self.plan()                       # quoted 5
        self.add_uv(plan_id=plan.id, uv_count=3)  # only 3 fell
        legacy = self.plan("Legacy", status="uvs_on_counter")  # quoted 5, never linked
        r = self.client.get(f"/api/monthly_plan_summary/{self.ldc.ir_id}/"
                            f"?month={self.when.month}&year={self.when.year}")
        self.assertEqual(r.status_code, 200, r.content)
        # 3 actual + 5 legacy fallback; the quoted 5 on the linked plan is ignored.
        self.assertEqual(r.json()["month_total"]["uv_value_sum"], 8.0)
        self.assertTrue(legacy.pk)

    # --- backfill ---------------------------------------------------------

    def test_backfill_links_unambiguous_matches_only(self):
        info = InfoDetail.objects.create(ir=self.owner, response="A", info_name="ravi ",
                                         info_date=self.when - timezone.timedelta(days=10))
        plan = self.plan("Ravi")
        UVDetail.objects.create(ir=self.owner, prospect_name="RAVI", uv_count=4,
                                uv_date=self.when + timezone.timedelta(days=7))
        # Ambiguous: two plans share the name.
        self.plan("Twin")
        self.plan("Twin")
        UVDetail.objects.create(ir=self.owner, prospect_name="Twin", uv_count=1,
                                uv_date=self.when + timezone.timedelta(days=7))

        call_command("link_info_plan_uv", stdout=StringIO())  # dry run
        self.assertEqual(UVDetail.objects.filter(plan__isnull=False).count(), 0)

        call_command("link_info_plan_uv", "--apply", stdout=StringIO())
        linked = UVDetail.objects.get(prospect_name="RAVI")
        self.assertEqual(linked.plan_id, plan.id)
        self.assertIsNone(UVDetail.objects.get(prospect_name="Twin").plan_id)
        plan.refresh_from_db()
        self.assertEqual(plan.info_id, info.id)
