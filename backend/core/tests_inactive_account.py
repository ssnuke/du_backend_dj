import json
from django.test import TestCase, override_settings

from core.models import AccessLevel, Ir

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class InactiveAccountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, lvl, parent=None, status=True):
            ir = Ir(ir_id=ir_id, ir_name=ir_id, ir_email=f"{ir_id}@t.t", ir_access_level=lvl,
                    status=status, parent_ir=parent)
            ir.set_password("pw12345")
            ir.save()
            return ir
        cls.admin = mk("IAADMIN", AccessLevel.ADMIN)
        cls.ldc = mk("IALDC", AccessLevel.LDC, cls.admin)
        cls.member = mk("IAMEM", AccessLevel.IR, cls.ldc)
        cls.sleeper = mk("IAOFF", AccessLevel.IR, cls.ldc, status=False)

    def login(self, ir_id, pw="pw12345"):
        return self.client.post("/api/login/", data=json.dumps({"ir_id": ir_id, "ir_password": pw}),
                                content_type="application/json")

    def rooms(self, ir_id):
        return self.client.get("/api/chat_rooms/", {"requester_ir_id": ir_id})

    # ── signing in ──────────────────────────────────────────────────────
    def test_an_inactive_account_cannot_sign_in(self):
        r = self.login("IAOFF")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "account_inactive")
        self.assertIn("inactive", r.json()["detail"])

    def test_an_active_account_signs_in(self):
        self.assertEqual(self.login("IAMEM").status_code, 200)

    def test_a_wrong_password_is_still_just_invalid_credentials(self):
        """Inactivity is not revealed to someone who does not know the password."""
        self.assertEqual(self.login("IAOFF", "wrong").status_code, 401)

    # ── already signed in ───────────────────────────────────────────────
    def test_every_request_for_an_inactive_account_is_refused_with_the_code(self):
        r = self.rooms("IAOFF")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "account_inactive")

    def test_a_post_naming_an_inactive_requester_is_refused_too(self):
        r = self.client.post("/api/chat_rooms/", data=json.dumps(
            {"requester_ir_id": "IAOFF", "room_type": "group", "room_name": "x", "initial_member_ir_ids": []}),
            content_type="application/json")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "account_inactive")

    def test_active_accounts_are_untouched(self):
        self.assertEqual(self.rooms("IAMEM").status_code, 200)

    def test_requests_that_name_no_requester_are_untouched(self):
        # Viewing an inactive person's own record (an LDC looking at their downline) must still work.
        self.assertEqual(self.client.get("/api/ir/IAOFF/").status_code, 200)

    # ── the moment it changes ───────────────────────────────────────────
    def toggle(self, target, active):
        return self.client.patch(f"/api/ir/{target}/status/",
                                 data=json.dumps({"requester_ir_id": "IAADMIN", "status": active}),
                                 content_type="application/json")

    def test_deactivating_takes_effect_on_the_very_next_request(self):
        self.assertEqual(self.rooms("IAMEM").status_code, 200)       # warms the cache
        self.assertEqual(self.toggle("IAMEM", False).status_code, 200)
        r = self.rooms("IAMEM")
        self.assertEqual(r.status_code, 403, "must not wait for a cache to expire")
        self.assertEqual(r.json()["code"], "account_inactive")

    def test_reactivating_lets_them_back_in_immediately(self):
        self.assertEqual(self.rooms("IAOFF").status_code, 403)       # warms the cache
        self.assertEqual(self.toggle("IAOFF", True).status_code, 200)
        self.assertEqual(self.rooms("IAOFF").status_code, 200)
        self.assertEqual(self.login("IAOFF").status_code, 200)

    def test_a_failed_push_does_not_fail_the_deactivation(self):
        from unittest import mock
        with mock.patch("channels.layers.get_channel_layer", side_effect=RuntimeError("redis down")):
            self.assertEqual(self.toggle("IAMEM", False).status_code, 200)
        self.member.refresh_from_db()
        self.assertFalse(self.member.status)

    def test_the_login_no_longer_prints_the_password(self):
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.login("IAMEM", "pw12345")
        self.assertNotIn("pw12345", buf.getvalue())

    def test_a_body_the_api_cannot_parse_is_left_to_the_view(self):
        """Reading the body for the requester must not turn an odd request into a 415."""
        info_url = "/api/delete_info_detail/999999/?requester_ir_id=IAMEM"
        r = self.client.delete(info_url)           # no content type -> octet-stream
        self.assertEqual(r.status_code, 404)       # the view's own answer (no such info), not 415
        r = self.client.delete("/api/delete_info_detail/999999/")
        self.assertEqual(r.status_code, 404)
