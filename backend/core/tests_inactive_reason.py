import json

from django.core.cache import cache
from django.test import TestCase, override_settings

from core.models import AccessLevel, Ir, IrStatusChange

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class InactiveReasonTests(TestCase):
    """Making someone Inactive needs a reason, which then shows on their record."""

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, lvl, parent=None, **kw):
            return Ir.objects.create(ir_id=ir_id, ir_name=f"Name {ir_id}", ir_email=f"{ir_id}@t.t", ir_password="x",
                                     ir_access_level=lvl, status=True, parent_ir=parent, **kw)

        cls.admin = mk("RAADMIN", AccessLevel.ADMIN)
        cls.ldc = mk("RALDC", AccessLevel.LDC, cls.admin)
        cls.member = mk("RAMEM", AccessLevel.IR, cls.ldc)
        cls.ls = mk("RALS", AccessLevel.LS, cls.ldc)

    def setUp(self):
        cache.clear()

    def patch(self, target, **body):
        return self.client.patch(f"/api/ir/{target}/status/", data=json.dumps({"requester_ir_id": "RAADMIN", **body}),
                                 content_type="application/json")

    NOTE = "Joined for the income, expected quick results, got none and stopped replying."

    def test_a_reason_and_a_few_words_are_required_to_make_someone_inactive(self):
        r = self.patch("RAMEM", status=False)
        self.assertEqual(r.status_code, 400)
        self.assertIn("reason", r.json()["detail"].lower())
        self.assertEqual(self.patch("RAMEM", status=False, reason="made-up", reason_note=self.NOTE).status_code, 400)
        # a reason alone is not enough: the note is how the leader explains it
        for note in (None, "", "   ", "too short"):
            body = {"status": False, "reason": "refund"}
            if note is not None:
                body["reason_note"] = note
            r = self.patch("RAMEM", **body)
            self.assertEqual(r.status_code, 400, note)
            self.assertIn("few words", r.json()["detail"])
        self.assertTrue(Ir.objects.get(ir_id="RAMEM").status, "nothing must change without both")

    def test_each_listed_reason_is_accepted_and_saved_with_its_note(self):
        for key in ("refund", "negative", "break", "not_responding", "other"):
            Ir.objects.filter(ir_id="RAMEM").update(status=True, inactive_reason=None)
            r = self.patch("RAMEM", status=False, reason=key, reason_note=f"  {self.NOTE}  ")
            self.assertEqual(r.status_code, 200, (key, r.content))
            ir = Ir.objects.get(ir_id="RAMEM")
            self.assertEqual((ir.status, ir.inactive_reason, ir.inactive_reason_note), (False, key, self.NOTE))

    def test_the_note_has_a_length_limit(self):
        ok = self.patch("RAMEM", status=False, reason="other", reason_note="x" * 1000)
        self.assertEqual(ok.status_code, 200)
        Ir.objects.filter(ir_id="RAMEM").update(status=True)
        self.assertEqual(self.patch("RAMEM", status=False, reason="other", reason_note="x" * 1001).status_code, 400)

    def test_the_reason_is_logged_with_the_change(self):
        self.patch("RAMEM", status=False, reason="refund", reason_note=self.NOTE)
        log = IrStatusChange.objects.get(ir_id_text="RAMEM", is_active=False)
        self.assertEqual(log.reason, "refund")

    def test_reactivating_needs_no_reason_and_clears_it(self):
        self.patch("RAMEM", status=False, reason="break", reason_note=self.NOTE)
        r = self.patch("RAMEM", status=True)
        self.assertEqual(r.status_code, 200)
        ir = Ir.objects.get(ir_id="RAMEM")
        self.assertEqual((ir.status, ir.inactive_reason, ir.inactive_reason_note), (True, None, ""))
        self.assertIsNone(r.json()["inactive_reason"])

    def test_chat_only_needs_no_reason(self):
        self.assertEqual(self.patch("RAMEM", chat_only=True).status_code, 200)

    def test_correcting_the_reason_of_someone_already_inactive(self):
        self.patch("RAMEM", status=False, reason="break", reason_note=self.NOTE)
        r = self.patch("RAMEM", status=False, reason="refund", reason_note=self.NOTE)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Ir.objects.get(ir_id="RAMEM").inactive_reason, "refund")
        self.assertEqual(IrStatusChange.objects.filter(ir_id_text="RAMEM", is_active=False).count(), 1,
                         "a correction is not a second deactivation")
        # and sending status=False again with nothing usable leaves the reason alone
        self.assertEqual(self.patch("RAMEM", status=False).status_code, 200)
        self.assertEqual(Ir.objects.get(ir_id="RAMEM").inactive_reason, "refund")

    def test_the_status_list_shows_the_reason(self):
        self.patch("RAMEM", status=False, reason="other", reason_note="Moved abroad for work and could not continue")
        rows = {r["ir_id"]: r for r in self.client.get("/api/ir_status_management/RAADMIN/", {"limit": 50}).json()["results"]}
        self.assertEqual(rows["RAMEM"]["inactive_reason"], "other")
        self.assertEqual(rows["RAMEM"]["inactive_reason_label"], "Other")
        self.assertEqual(rows["RAMEM"]["inactive_reason_note"], "Moved abroad for work and could not continue")
        self.assertIsNone(rows["RALS"]["inactive_reason_label"])

    def test_the_ir_record_shows_the_reason_only_to_leaders(self):
        self.patch("RAMEM", status=False, reason="negative", reason_note=self.NOTE)
        as_admin = self.client.get("/api/ir/RAMEM/", {"requester_ir_id": "RAADMIN"}).json()
        self.assertEqual(as_admin["inactive_reason_label"], "Negative")
        as_peer = self.client.get("/api/ir/RAMEM/", {"requester_ir_id": "RALS"})
        self.assertNotIn("inactive_reason", as_peer.json())
        self.assertNotIn("inactive_reason", self.client.get("/api/ir/RAMEM/").json())

    def test_the_overview_drilldown_names_the_reason(self):
        from django.utils import timezone
        self.patch("RAMEM", status=False, reason="refund", reason_note=self.NOTE)
        now = timezone.now()
        rows = self.client.get("/api/admin/network_overview/names/", {
            "requester_ir_id": "RAADMIN", "kind": "deactivated", "year": now.year, "month": now.month}).json()["results"]
        self.assertTrue(any("Refund" in r["detail"] for r in rows), rows)
