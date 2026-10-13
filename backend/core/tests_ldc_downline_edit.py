import json
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import AccessLevel, InfoDetail, Ir, PlanDetail

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class LdcCanEditTheirDownlineTests(TestCase):
    """An LDC edits anyone below them in the tree — no team membership needed."""

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, name, lvl, parent=None):
            return Ir.objects.create(ir_id=ir_id, ir_name=name, ir_email=f"{ir_id}@t.t",
                                     ir_password="x", ir_access_level=lvl, status=True, parent_ir=parent)
        cls.ctc = mk("LD0001", "CTC", AccessLevel.CTC)
        cls.ldc = mk("LD0002", "LDC", AccessLevel.LDC, parent=cls.ctc)
        cls.kid = mk("LD0003", "Kid", AccessLevel.IR, parent=cls.ldc)
        cls.grandkid = mk("LD0004", "Grandkid", AccessLevel.IR, parent=cls.kid)
        cls.other_ldc = mk("LD0005", "Other LDC", AccessLevel.LDC, parent=cls.ctc)
        cls.outsider = mk("LD0006", "Outsider", AccessLevel.IR, parent=cls.other_ldc)
        now = timezone.now()
        cls.info = InfoDetail.objects.create(ir=cls.grandkid, info_date=now, response="A", info_name="P")
        cls.plan = PlanDetail.objects.create(ir=cls.grandkid, plan_date=now, plan_name="P")
        cls.out_info = InfoDetail.objects.create(ir=cls.outsider, info_date=now, response="A", info_name="P")

    def put(self, url, body):
        return self.client.put(url, data=json.dumps(body), content_type="application/json")

    def test_permission_covers_every_depth_of_the_downline(self):
        self.assertTrue(self.ldc.can_add_data_for_ir(self.kid))
        self.assertTrue(self.ldc.can_add_data_for_ir(self.grandkid))

    def test_ldc_updates_a_downlines_info_and_plan(self):
        r = self.put(f"/api/update_info_detail/{self.info.id}/", {"requester_ir_id": "LD0002", "info_name": "Edited"})
        self.assertEqual(r.status_code, 200, r.content)
        self.info.refresh_from_db(); self.assertEqual(self.info.info_name, "Edited")
        r = self.put(f"/api/update_plan_detail/{self.plan.id}/", {"requester_ir_id": "LD0002", "plan_name": "Edited"})
        self.assertEqual(r.status_code, 200, r.content)

    def test_ldc_cannot_update_someone_in_another_branch(self):
        r = self.put(f"/api/update_info_detail/{self.out_info.id}/", {"requester_ir_id": "LD0002", "info_name": "Hacked"})
        self.assertEqual(r.status_code, 403)
        self.out_info.refresh_from_db(); self.assertEqual(self.out_info.info_name, "P")

    def test_deleting_is_a_separate_rule_an_ldc_still_deletes_only_their_own(self):
        """Unchanged on purpose: editing widened to the downline, deleting did not."""
        r = self.client.delete(f"/api/delete_info_detail/{self.info.id}/?requester_ir_id=LD0002")
        self.assertEqual(r.status_code, 403)
        self.assertTrue(InfoDetail.objects.filter(id=self.info.id).exists())
