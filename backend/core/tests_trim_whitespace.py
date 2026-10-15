import json
from django.contrib.auth.hashers import check_password
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import (AccessLevel, ChatRoom, ChatRoomMember, ChatRoomType, ChatMessage,
                         InfoDetail, Ir, IrPendingRequest)

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class WhitespaceIsTrimmedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, lvl, parent=None):
            return Ir.objects.create(ir_id=ir_id, ir_name=ir_id, ir_email=f"{ir_id}@t.t", ir_password="x",
                                     ir_access_level=lvl, status=True, parent_ir=parent)
        cls.admin = mk("TWADMIN", AccessLevel.ADMIN)
        cls.ldc = mk("TWLDC", AccessLevel.LDC, cls.admin)
        cls.existing = mk("IM123456", AccessLevel.IR, cls.ldc)

    def post_json(self, url, body):
        return self.client.post(url, data=json.dumps(body), content_type="application/json")

    # ── registration ────────────────────────────────────────────────────
    def register(self, **over):
        body = {"requester_ir_id": "TWLDC", "ir_id": "  IM777001 ", "ir_name": "  Anna  Rao ",
                "ir_email": " anna@example.com ", "ir_password": "  s3cret  ", "parent_ir_id": " TWLDC "}
        body.update(over)
        return self.post_json("/api/ir_requests/register/", body)

    def test_registration_saves_trimmed_details(self):
        r = self.register()
        self.assertEqual(r.status_code, 201, r.content)
        p = IrPendingRequest.objects.get()
        self.assertEqual(p.target_ir_code, "IM777001")
        self.assertEqual(p.target_ir_name, "Anna  Rao")       # inner spacing is left alone
        self.assertEqual(p.payload["ir_id"], "IM777001")
        self.assertEqual(p.payload["ir_name"], "Anna  Rao")
        self.assertEqual(p.payload["ir_email"], "anna@example.com")
        self.assertEqual(p.referred_ir_id, "TWLDC")

    def test_a_padded_id_is_recognised_as_already_registered(self):
        r = self.register(ir_id="  IM123456 ")
        self.assertEqual(r.status_code, 400)
        self.assertIn("already registered", r.json()["detail"])

    def test_a_name_of_only_spaces_counts_as_missing(self):
        r = self.register(ir_name="    ")
        self.assertEqual(r.status_code, 400)

    def test_zero_width_characters_pasted_around_a_value_are_removed(self):
        r = self.register(ir_name="​Anna Rao﻿")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(IrPendingRequest.objects.get().payload["ir_name"], "Anna Rao")

    def test_the_password_is_kept_exactly_as_typed(self):
        self.register()
        p = IrPendingRequest.objects.get()
        self.assertTrue(check_password("  s3cret  ", p.payload["ir_password_hash"]))
        self.assertFalse(check_password("s3cret", p.payload["ir_password_hash"]))

    # ── any other form ──────────────────────────────────────────────────
    def test_other_updates_are_trimmed_too(self):
        info = InfoDetail.objects.create(ir=self.existing, info_date=timezone.now(), response="A", info_name="P")
        r = self.client.put(f"/api/update_info_detail/{info.id}/",
                            data=json.dumps({"requester_ir_id": "TWADMIN", "info_name": "  Ravi Kumar  "}),
                            content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        info.refresh_from_db()
        self.assertEqual(info.info_name, "Ravi Kumar")

    def test_form_encoded_and_multipart_bodies_are_trimmed_as_well(self):
        # Django's test client sends multipart by default; the second call is urlencoded.
        r = self.client.post("/api/ir_requests/register/", {
            "requester_ir_id": "TWLDC", "ir_id": " IM777002 ", "ir_name": " Multi Part ", "ir_email": " m@e.com ",
            "ir_password": " pw ", "parent_ir_id": "TWLDC"})
        self.assertEqual(r.status_code, 201, r.content)
        r = self.client.post("/api/ir_requests/register/", "requester_ir_id=TWLDC&ir_id=%20IM777003%20&ir_name=%20Url%20Enc%20"
                             "&ir_email=u%40e.com&ir_password=pw&parent_ir_id=TWLDC",
                             content_type="application/x-www-form-urlencoded")
        self.assertEqual(r.status_code, 201, r.content)
        codes = set(IrPendingRequest.objects.values_list("target_ir_code", flat=True))
        self.assertEqual(codes, {"IM777002", "IM777003"})
        self.assertEqual(IrPendingRequest.objects.get(target_ir_code="IM777003").target_ir_name, "Url Enc")

    # ── what must not change ────────────────────────────────────────────
    def test_chat_message_text_keeps_its_inner_formatting(self):
        """`content` is exempt from the parser; line breaks and indents inside survive."""
        room = ChatRoom.objects.create(room_type=ChatRoomType.GROUP, room_name="G", created_by=self.ldc)
        ChatRoomMember.objects.create(room=room, ir=self.ldc)
        text = "line one\n    indented line two\n\nline four"
        r = self.post_json(f"/api/chat_rooms/{room.id}/messages/", {"requester_ir_id": "TWLDC", "content": text})
        self.assertIn(r.status_code, (200, 201), r.content)
        self.assertEqual(ChatMessage.objects.get(room=room).content, text)

    def test_parser_does_not_touch_content_or_password_keys(self):
        from core.utils.text_clean import trim_strings
        self.assertEqual(trim_strings({"content": "  x  "}), {"content": "  x  "})

    def test_non_text_values_and_nesting_are_untouched(self):
        from core.utils.text_clean import trim_strings
        out = trim_strings({"n": 3, "b": True, "none": None, "list": [" a ", 2, {"k": " b "}],
                            "new_password": " keep ", "current_password": " keep "})
        self.assertEqual(out, {"n": 3, "b": True, "none": None, "list": ["a", 2, {"k": "b"}],
                               "new_password": " keep ", "current_password": " keep "})
