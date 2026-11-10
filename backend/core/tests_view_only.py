import json

from channels.testing import WebsocketCommunicator
from django.test import TestCase, TransactionTestCase, override_settings

from core.chat.consumers import ChatConsumer
from core.models import AccessLevel, ChatMessage, ChatRoom, ChatRoomMember, ChatRoomType, Ir

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class ViewOnlyAccountTests(TestCase):
    """A view-only account reads everything its role allows and changes nothing."""

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, lvl, **kw):
            return Ir.objects.create(ir_id=ir_id, ir_name=ir_id, ir_email=f"{ir_id}@t.t", ir_password="x",
                                     ir_access_level=lvl, status=True, **kw)

        cls.admin = mk("VADMIN", AccessLevel.ADMIN)
        cls.viewer = mk("VVIEW", AccessLevel.ADMIN, parent_ir=cls.admin, view_only=True)
        cls.member = mk("VMEM", AccessLevel.IR, parent_ir=cls.admin)

    def setUp(self):
        # The flag is cached for a few seconds; tests flip it, so start clean.
        from django.core.cache import cache
        cache.clear()

    def post(self, path, body, method="post"):
        return getattr(self.client, method)(path, data=json.dumps(body), content_type="application/json")

    def assertRefused(self, r):
        self.assertEqual(r.status_code, 403, r.content[:200])
        self.assertEqual(r.json().get("code"), "view_only")

    def test_reads_still_work(self):
        for path in (f"/api/ir/{self.member.ir_id}/", f"/api/teams/?requester_ir_id={self.viewer.ir_id}",
                     f"/api/admin/network_overview/?requester_ir_id={self.viewer.ir_id}",
                     f"/api/ir_status_management/{self.viewer.ir_id}/"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_writes_naming_them_as_requester_are_refused(self):
        self.assertRefused(self.post(f"/api/ir/{self.member.ir_id}/status/",
                                     {"requester_ir_id": self.viewer.ir_id, "status": False, "reason": "break"}, "patch"))
        self.assertRefused(self.post("/api/add_ir_id/", {"requester_ir_id": self.viewer.ir_id, "ir_id": "X1"}))
        self.assertFalse(Ir.objects.get(ir_id=self.member.ir_id).status is False)

    def test_writes_naming_them_as_acting_ir_are_refused(self):
        self.assertRefused(self.post("/api/change_access_level/", {
            "acting_ir_id": self.viewer.ir_id, "target_ir_id": self.member.ir_id, "new_access_level": 3}))
        self.assertEqual(Ir.objects.get(ir_id=self.member.ir_id).ir_access_level, AccessLevel.IR)

    def test_a_write_to_their_own_record_by_path_is_refused_when_no_actor_is_named(self):
        self.assertRefused(self.post(f"/api/add_info_detail/{self.viewer.ir_id}/", {"info_name": "x"}))

    def test_another_admin_can_still_manage_the_view_only_account(self):
        """The URL's IR is the target, not the actor, when somebody else is named."""
        r = self.post(f"/api/ir/{self.viewer.ir_id}/status/",
                      {"requester_ir_id": self.admin.ir_id, "status": False, "reason": "break"}, "patch")
        self.assertEqual(r.status_code, 200, r.content[:200])

    def test_other_accounts_are_untouched(self):
        r = self.post(f"/api/ir/{self.member.ir_id}/status/",
                      {"requester_ir_id": self.admin.ir_id, "status": False, "reason": "break"}, "patch")
        self.assertEqual(r.status_code, 200, r.content[:200])

    def test_chat_and_own_chat_profile_are_not_blocked_by_this_rule(self):
        v = self.viewer.ir_id
        for path, body, method in (
            ("/api/chat_presence/", {"requester_ir_id": v}, "post"),
            ("/api/save_fcm_token/", {"ir_id": v, "fcm_token": "t"}, "post"),
            ("/api/notifications/mark_all_read/", {"requester_ir_id": v}, "post"),
            ("/api/chat_rooms/", {"requester_ir_id": v, "room_type": "group", "room_name": "R", "member_ir_ids": []}, "post"),
            (f"/api/ir/{v}/display_name/", {"requester_ir_id": v, "display_name": "Viewer"}, "put"),
        ):
            r = self.post(path, body, method)
            refused = r.status_code == 403 and r.json().get("code") == "view_only"
            self.assertFalse(refused, f"{path} must stay available")

    def test_someone_elses_chat_profile_is_not_theirs_to_change(self):
        self.assertRefused(self.post(f"/api/ir/{self.member.ir_id}/display_name/",
                                     {"requester_ir_id": self.viewer.ir_id, "display_name": "x"}, "put"))

    def test_turning_it_off_restores_write_access(self):
        Ir.objects.filter(pk=self.viewer.pk).update(view_only=False)
        from core.permissions import forget_inactive
        forget_inactive(self.viewer.ir_id)
        r = self.post(f"/api/ir/{self.member.ir_id}/status/",
                      {"requester_ir_id": self.viewer.ir_id, "status": False, "reason": "break"}, "patch")
        self.assertEqual(r.status_code, 200, r.content[:200])


@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}, CACHES=LOCMEM)
class ViewOnlyChatSocketTests(TransactionTestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.viewer = Ir.objects.create(ir_id="WVIEW", ir_name="Viewer", ir_access_level=AccessLevel.ADMIN,
                                        status=True, view_only=True)
        self.other = Ir.objects.create(ir_id="WOTHER", ir_name="Other", ir_access_level=AccessLevel.LS, status=True)
        self.room = ChatRoom.objects.create(room_type=ChatRoomType.GROUP, room_name="R", created_by=self.other)
        ChatRoomMember.objects.create(room=self.room, ir=self.viewer)
        ChatRoomMember.objects.create(room=self.room, ir=self.other)

    async def test_a_view_only_account_can_use_chat_normally(self):
        ws = WebsocketCommunicator(ChatConsumer.as_asgi(), f"/ws/chat/{self.room.id}/?ir_id=WVIEW")
        ws.scope["url_route"] = {"kwargs": {"room_id": self.room.id}}
        connected, _ = await ws.connect()
        self.assertTrue(connected)
        await ws.send_json_to({"type": "send_message", "content": "hello"})
        reply = await ws.receive_json_from(timeout=5)
        self.assertEqual(reply["type"], "message_created")
        self.assertEqual(await ChatMessage.objects.acount(), 1)
        await ws.disconnect()
