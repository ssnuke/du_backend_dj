import json

from django.test import TestCase, override_settings

from core.models import AccessLevel, Ir, Team, TeamMember, TeamRole

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class ChatOnlyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, lvl, parent=None):
            ir = Ir(ir_id=ir_id, ir_name=ir_id, ir_email=f"{ir_id}@t.t", ir_access_level=lvl,
                    status=True, parent_ir=parent)
            ir.set_password("pw12345")
            ir.save()
            return ir
        cls.admin = mk("COADMIN", AccessLevel.ADMIN)
        cls.ctc = mk("COCTC", AccessLevel.CTC, cls.admin)
        cls.ldc = mk("COLDC", AccessLevel.LDC, cls.ctc)
        cls.member = mk("COMEM", AccessLevel.IR, cls.ldc)
        cls.outsider = mk("COOUT", AccessLevel.IR, cls.admin)
        team = Team.objects.create(name="T", created_by=cls.ldc)
        TeamMember.objects.create(team=team, ir=cls.ldc, role=TeamRole.LDC)
        TeamMember.objects.create(team=team, ir=cls.member, role=TeamRole.IR)

    def set(self, requester, target, **body):
        if body.get("status") is False:
            body.setdefault("reason", "break")
            body.setdefault("reason_note", "Went quiet after two months and stopped replying")
        return self.client.patch(f"/api/ir/{target}/status/",
                                 data=json.dumps({"requester_ir_id": requester, **body}),
                                 content_type="application/json")

    def reload(self, ir_id):
        return Ir.objects.get(ir_id=ir_id)

    # ── who can set it ──────────────────────────────────────────────────
    def test_ldc_ctc_and_admin_can_limit_someone_to_chat(self):
        for requester in ("COLDC", "COCTC", "COADMIN"):
            Ir.objects.filter(ir_id="COMEM").update(chat_only=False)
            r = self.set(requester, "COMEM", chat_only=True)
            self.assertEqual(r.status_code, 200, (requester, r.content))
            self.assertTrue(r.json()["chat_only"])
            self.assertTrue(self.reload("COMEM").chat_only)

    def test_an_ir_cannot(self):
        r = self.set("COMEM", "COOUT", chat_only=True)
        self.assertEqual(r.status_code, 403)

    def test_an_ldc_cannot_reach_someone_outside_their_team(self):
        r = self.set("COLDC", "COOUT", chat_only=True)
        self.assertEqual(r.status_code, 403)
        self.assertFalse(self.reload("COOUT").chat_only)

    def test_an_admin_cannot_be_limited(self):
        r = self.set("COCTC", "COADMIN", chat_only=True)
        self.assertEqual(r.status_code, 403)

    def test_bad_input(self):
        self.assertEqual(self.set("COLDC", "COMEM", chat_only="yes").status_code, 400)
        self.assertEqual(self.set("COLDC", "COMEM").status_code, 400)

    # ── state transitions ───────────────────────────────────────────────
    def test_active_clears_it_and_inactive_replaces_it(self):
        self.set("COLDC", "COMEM", chat_only=True)
        self.assertTrue(self.set("COLDC", "COMEM", status=True).json()["status"])
        self.assertFalse(self.reload("COMEM").chat_only)

        self.set("COLDC", "COMEM", chat_only=True)
        r = self.set("COLDC", "COMEM", status=False)
        self.assertFalse(r.json()["status"])
        self.assertFalse(r.json()["chat_only"])

    def test_chat_only_on_an_inactive_account_makes_it_active_but_limited(self):
        self.set("COLDC", "COMEM", status=False)
        r = self.set("COLDC", "COMEM", chat_only=True)
        self.assertTrue(r.json()["status"])
        self.assertTrue(r.json()["chat_only"])

    def test_it_can_be_lifted_on_its_own(self):
        self.set("COLDC", "COMEM", chat_only=True)
        r = self.set("COLDC", "COMEM", chat_only=False)
        self.assertEqual((r.json()["status"], r.json()["chat_only"]), (True, False))

    # ── what a limited account can do ───────────────────────────────────
    def call(self, path, method="get", **params):
        return getattr(self.client, method)(path, {"requester_ir_id": "COMEM", **params})

    def test_chat_still_works_and_everything_else_is_refused_with_the_code(self):
        self.set("COLDC", "COMEM", chat_only=True)
        self.assertEqual(self.call("/api/chat_rooms/").status_code, 200)
        for path in ("/api/new_joiners/", "/api/ir_status_management/COMEM/", "/api/notifications/",
                     "/api/ir/COOUT/", "/api/placement_tree/"):
            r = self.call(path)
            self.assertEqual(r.status_code, 403, path)
            self.assertEqual(r.json()["code"], "chat_only", path)

    def test_they_may_read_their_own_record_but_not_anothers(self):
        self.set("COLDC", "COMEM", chat_only=True)
        own = self.call("/api/ir/COMEM/")
        self.assertEqual(own.status_code, 200)
        self.assertTrue(own.json()["chat_only"])
        self.assertEqual(self.call("/api/ir/COOUT/").status_code, 403)

    def test_a_post_naming_them_is_refused_too(self):
        self.set("COLDC", "COMEM", chat_only=True)
        r = self.client.post("/api/create_team/", data=json.dumps({"requester_ir_id": "COMEM"}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "chat_only")

    def test_lifting_it_restores_everything_at_once(self):
        self.set("COLDC", "COMEM", chat_only=True)
        self.assertEqual(self.call("/api/notifications/").status_code, 403)
        self.set("COLDC", "COMEM", chat_only=False)
        self.assertNotEqual(self.call("/api/notifications/").status_code, 403)

    def test_login_reports_it(self):
        self.set("COLDC", "COMEM", chat_only=True)
        r = self.client.post("/api/login/", data=json.dumps({"ir_id": "COMEM", "ir_password": "pw12345"}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ir"]["chat_only"])

    def test_the_status_list_shows_it(self):
        self.set("COLDC", "COMEM", chat_only=True)
        rows = self.client.get("/api/ir_status_management/COLDC/").json()["results"]
        self.assertTrue(next(r for r in rows if r["ir_id"] == "COMEM")["chat_only"])

    def test_other_people_are_untouched(self):
        self.set("COLDC", "COMEM", chat_only=True)
        self.assertEqual(self.client.get("/api/new_joiners/", {"requester_ir_id": "COLDC"}).status_code, 200)
