import datetime
from django.test import TestCase, override_settings
from core.models import Ir, AccessLevel

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class NewRegistrationsReportTests(TestCase):
    """
    Internal report: every registered IR with who referred them
    (parent_ir). Admin only, table-shaped (fixed columns + rows), optional
    started_date range.
    """

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, name, lvl=AccessLevel.IR, parent=None):
            return Ir.objects.create(ir_id=ir_id, ir_name=name, ir_email=f"{ir_id}@t.t",
                                     ir_password="x", ir_access_level=lvl, status=True,
                                     parent_ir=parent)

        cls.admin = mk("RRADMIN", "The Admin", AccessLevel.ADMIN)
        cls.ctc = mk("RRCTC", "The CTC", AccessLevel.CTC, parent=cls.admin)
        cls.sponsor = mk("RRSPONSOR", "Sponsoring LDC", AccessLevel.LDC, parent=cls.ctc)
        cls.recruit = mk("RRRECRUIT", "New Recruit", AccessLevel.IR, parent=cls.sponsor)
        cls.orphan = mk("RRORPHAN", "No Referrer", AccessLevel.IR)  # parent_ir left null

        # started_date is auto_now_add — backdate two rows directly so the
        # date-range filter has something to actually discriminate on.
        Ir.objects.filter(ir_id=cls.sponsor.ir_id).update(started_date=datetime.date(2026, 1, 1))
        Ir.objects.filter(ir_id=cls.recruit.ir_id).update(started_date=datetime.date(2026, 6, 15))

    def call(self, requester=None, **params):
        query = dict(params)
        if requester is not None:
            query["requester_ir_id"] = requester.ir_id
        return self.client.get("/api/reports/new_registrations/", query)

    def test_non_admin_is_refused(self):
        r = self.call(self.ctc)
        self.assertEqual(r.status_code, 403)

    def test_missing_requester_is_refused(self):
        r = self.client.get("/api/reports/new_registrations/")
        self.assertEqual(r.status_code, 403)

    def test_admin_gets_every_ir_with_referrer_name(self):
        r = self.call(self.admin)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(set(body["columns"]),
                         {"ir_id", "ir_name", "role", "email", "registered_on", "referrer_ir_id", "referrer_name"})
        by_id = {row["ir_id"]: row for row in body["rows"]}
        self.assertEqual(len(by_id), 5)

        recruit_row = by_id[self.recruit.ir_id]
        self.assertEqual(recruit_row["referrer_ir_id"], self.sponsor.ir_id)
        self.assertEqual(recruit_row["referrer_name"], "Sponsoring LDC")
        self.assertEqual(recruit_row["role"], "IR")
        self.assertEqual(recruit_row["registered_on"], "2026-06-15")

    def test_no_referrer_comes_back_null_not_missing(self):
        r = self.call(self.admin)
        by_id = {row["ir_id"]: row for row in r.json()["rows"]}
        orphan_row = by_id[self.orphan.ir_id]
        self.assertIsNone(orphan_row["referrer_ir_id"])
        self.assertIsNone(orphan_row["referrer_name"])

    def test_pro_role_label_for_gc(self):
        gc = Ir.objects.create(ir_id="RRGC", ir_name="A GC", ir_email="g@t.t",
                               ir_password="x", ir_access_level=AccessLevel.GC, status=True)
        r = self.call(self.admin)
        by_id = {row["ir_id"]: row for row in r.json()["rows"]}
        self.assertEqual(by_id[gc.ir_id]["role"], "PRO")

    def test_date_range_filters_inclusively(self):
        r = self.call(self.admin, start_date="2026-01-01", end_date="2026-01-01")
        ids = {row["ir_id"] for row in r.json()["rows"]}
        self.assertEqual(ids, {self.sponsor.ir_id})

    def test_date_range_excludes_outside_rows(self):
        r = self.call(self.admin, start_date="2026-02-01")
        ids = {row["ir_id"] for row in r.json()["rows"]}
        self.assertNotIn(self.sponsor.ir_id, ids)
        self.assertIn(self.recruit.ir_id, ids)

    def test_bad_date_is_rejected(self):
        r = self.call(self.admin, start_date="not-a-date")
        self.assertEqual(r.status_code, 400)

    def test_newest_first(self):
        r = self.call(self.admin)
        rows = r.json()["rows"]
        dates = [row["registered_on"] for row in rows if row["registered_on"]]
        self.assertEqual(dates, sorted(dates, reverse=True))
