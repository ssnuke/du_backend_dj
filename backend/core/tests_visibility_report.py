from django.test import TestCase, override_settings
from core.models import Ir, AccessLevel, Team, TeamMember, TeamRole

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class VisibilityReportTests(TestCase):
    """
    /api/reports/visibility/?ir_id=X returns exactly what X.get_viewable_irs()
    already returns elsewhere in the app (e.g. /api/irs/) — this endpoint
    adds an Admin-only gate and lets requester != ir_id, it does not define
    a second notion of "visible".
    """

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, name, lvl=AccessLevel.IR, parent=None):
            return Ir.objects.create(ir_id=ir_id, ir_name=name, ir_email=f"{ir_id}@t.t",
                                     ir_password="x", ir_access_level=lvl, status=True,
                                     parent_ir=parent)

        cls.admin = mk("VRADMIN", "The Admin", AccessLevel.ADMIN)
        cls.ctc = mk("VRCTC", "The CTC", AccessLevel.CTC, parent=cls.admin)
        cls.ldc = mk("VRLDC", "The LDC", AccessLevel.LDC, parent=cls.ctc)
        cls.ls = mk("VRLS", "An LS", AccessLevel.LS, parent=cls.ldc)
        cls.member = mk("VRMEM", "Team Member", AccessLevel.IR, parent=cls.ldc)
        cls.non_admin = cls.ldc

        # Unrelated branch — outside everyone above's line of visibility.
        cls.other_ctc = mk("VROCTC", "Other CTC", AccessLevel.CTC, parent=cls.admin)
        cls.outsider = mk("VROUT", "Outsider", AccessLevel.IR, parent=cls.other_ctc, )
        Ir.objects.filter(ir_id=cls.outsider.ir_id).update(status=False)

        team = Team.objects.create(name="The Team", created_by=cls.ldc)
        TeamMember.objects.create(team=team, ir=cls.ldc, role=TeamRole.LDC)
        TeamMember.objects.create(team=team, ir=cls.ls, role=TeamRole.IR)
        TeamMember.objects.create(team=team, ir=cls.member, role=TeamRole.IR)

    def call(self, requester=None, ir_id=None):
        params = {}
        if requester is not None:
            params["requester_ir_id"] = requester.ir_id
        if ir_id is not None:
            params["ir_id"] = ir_id
        return self.client.get("/api/reports/visibility/", params)

    def ids_matching(self, ir):
        """The ground truth this endpoint must reproduce."""
        return set(ir.get_viewable_irs().values_list("ir_id", flat=True))

    def test_non_admin_requester_is_refused(self):
        r = self.call(self.non_admin, ir_id=self.member.ir_id)
        self.assertEqual(r.status_code, 403)

    def test_missing_requester_is_refused(self):
        r = self.client.get("/api/reports/visibility/", {"ir_id": self.member.ir_id})
        self.assertEqual(r.status_code, 403)

    def test_missing_ir_id_is_a_400(self):
        r = self.call(self.admin)
        self.assertEqual(r.status_code, 400)

    def test_unknown_ir_id_is_a_404(self):
        r = self.call(self.admin, ir_id="NOPE")
        self.assertEqual(r.status_code, 404)

    def test_admin_can_inspect_someone_elses_visibility(self):
        """The whole point: requester (Admin) != ir_id (the LDC)."""
        r = self.call(self.admin, ir_id=self.ldc.ir_id)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body["ir_id"], self.ldc.ir_id)
        self.assertEqual(body["role"], "LDC")
        ids = {row["ir_id"] for row in body["rows"]}
        self.assertEqual(ids, self.ids_matching(self.ldc))
        # Sanity: the LDC's own subtree/team, not the unrelated branch.
        self.assertIn(self.member.ir_id, ids)
        self.assertNotIn(self.outsider.ir_id, ids)

    def test_matches_get_viewable_irs_for_every_role(self):
        """
        Not re-deriving the rule — same result as the model method for each
        role, so this can never drift from what the rest of the app enforces.
        """
        for ir in (self.admin, self.ctc, self.ldc, self.ls, self.member):
            r = self.call(self.admin, ir_id=ir.ir_id)
            self.assertEqual(r.status_code, 200, r.content)
            ids = {row["ir_id"] for row in r.json()["rows"]}
            self.assertEqual(ids, self.ids_matching(ir), f"mismatch for {ir.ir_id}")

    def test_inactive_status_is_reported_not_hidden(self):
        # Admin's own get_viewable_irs() filters status=True, so inspect via
        # the CTC branch that owns the inactive outsider instead — LDC/CTC's
        # subtree query does not filter status, matching get_subtree_irs().
        r = self.call(self.admin, ir_id=self.other_ctc.ir_id)
        by_id = {row["ir_id"]: row for row in r.json()["rows"]}
        self.assertEqual(by_id[self.outsider.ir_id]["status"], "inactive")

    def test_role_label_reads_pro_for_gc(self):
        gc = Ir.objects.create(ir_id="VRGC", ir_name="A GC", ir_email="g@t.t",
                               ir_password="x", ir_access_level=AccessLevel.GC, status=True)
        r = self.call(self.admin, ir_id=gc.ir_id)
        self.assertEqual(r.json()["role"], "PRO")
