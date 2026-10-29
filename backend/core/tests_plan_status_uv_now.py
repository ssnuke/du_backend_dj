import json
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import AccessLevel, Ir, PlanDetail, UVDetail

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class PlanStatusUvLandsNowTests(TestCase):
    """
    A plan reaching "UV's on Counter" is the UVs landing today. A plan shown a
    month ago must not drag its UVs back into the month it was shown.
    """

    @classmethod
    def setUpTestData(cls):
        cls.ldc = Ir.objects.create(
            ir_id="PLDC", ir_name="The LDC", ir_email="pldc@t.t", ir_password="x",
            ir_access_level=AccessLevel.LDC, status=True,
        )
        cls.ir = Ir.objects.create(
            ir_id="PIR", ir_name="Snehith", ir_email="pir@t.t", ir_password="x",
            ir_access_level=AccessLevel.IR, status=True, parent_ir=cls.ldc,
        )

    def make_plan(self, **kw):
        defaults = dict(ir=self.ir, plan_name="Ravi", status="closed",
                        uv_value=Decimal("6"), plan_date=timezone.now() - timedelta(days=45))
        defaults.update(kw)
        return PlanDetail.objects.create(**defaults)

    def put(self, plan, body):
        body = {"ir": self.ir.ir_id, "requester_ir_id": self.ir.ir_id, **body}
        return self.client.put(f"/api/update_plan_detail/{plan.id}/", data=json.dumps(body),
                               content_type="application/json")

    def test_old_plan_moved_to_counter_records_uvs_dated_now(self):
        plan = self.make_plan()
        before = timezone.now()
        res = self.put(plan, {"status": "uvs_on_counter"})
        self.assertEqual(res.status_code, 200, res.content)

        uv = UVDetail.objects.get(plan=plan)
        self.assertEqual(uv.uv_count, Decimal("6"))
        self.assertEqual(uv.ir_id, self.ir.ir_id)
        self.assertEqual(uv.prospect_name, "Ravi")
        self.assertGreaterEqual(uv.uv_date, before)
        # The plan keeps the date it was shown on.
        plan.refresh_from_db()
        self.assertLess(plan.plan_date, before - timedelta(days=30))
        self.ir.refresh_from_db()
        self.assertEqual(self.ir.uv_count, Decimal("6"))

    def test_uv_value_sent_with_the_status_change_is_what_is_recorded(self):
        plan = self.make_plan(uv_value=Decimal("6"))
        self.put(plan, {"status": "uvs_on_counter", "uv_value": 4})
        self.assertEqual(UVDetail.objects.get(plan=plan).uv_count, Decimal("4"))

    def test_saving_again_does_not_double_count(self):
        plan = self.make_plan()
        self.put(plan, {"status": "uvs_on_counter"})
        self.put(plan, {"status": "uvs_on_counter", "comments": "again"})
        self.assertEqual(UVDetail.objects.filter(plan=plan).count(), 1)
        self.ir.refresh_from_db()
        self.assertEqual(self.ir.uv_count, Decimal("6"))

    def test_plan_that_already_has_uvs_is_not_given_a_second_record(self):
        plan = self.make_plan()
        UVDetail.objects.create(ir=self.ir, ir_name="Snehith", prospect_name="Ravi",
                                uv_count=Decimal("3"), plan=plan)
        self.put(plan, {"status": "uvs_on_counter"})
        self.assertEqual(UVDetail.objects.filter(plan=plan).count(), 1)

    def test_other_status_changes_record_nothing(self):
        plan = self.make_plan(status="closing_pending")
        self.put(plan, {"status": "kiv"})
        self.put(plan, {"status": "closed"})
        self.assertFalse(UVDetail.objects.filter(plan=plan).exists())

    def test_no_uv_value_records_nothing(self):
        plan = self.make_plan(uv_value=None)
        res = self.put(plan, {"status": "uvs_on_counter"})
        self.assertEqual(res.status_code, 200)
        self.assertFalse(UVDetail.objects.filter(plan=plan).exists())

    def test_deleting_the_recorded_uvs_returns_plan_to_in_process_and_can_land_again(self):
        plan = self.make_plan()
        self.put(plan, {"status": "uvs_on_counter"})
        uv = UVDetail.objects.get(plan=plan)
        self.client.delete(f"/api/delete_uv_detail/{uv.id}/?requester_ir_id={self.ldc.ir_id}")
        plan.refresh_from_db()
        self.assertEqual(plan.status, "closed")
        self.put(plan, {"status": "uvs_on_counter"})
        self.assertEqual(UVDetail.objects.filter(plan=plan).count(), 1)

    def test_uvs_show_in_the_current_month_not_the_plans_month(self):
        now = timezone.now()
        plan = self.make_plan(plan_date=now - timedelta(days=45))
        self.put(plan, {"status": "uvs_on_counter"})

        def uv_total(moment):
            res = self.client.get(f"/api/monthly_plan_summary/{self.ir.ir_id}/",
                                  {"month": moment.month, "year": moment.year})
            self.assertEqual(res.status_code, 200, res.content)
            return res.json()["uv_month_total"]

        self.assertEqual(uv_total(now), 6.0)
        self.assertEqual(uv_total(plan.plan_date), 0.0)
