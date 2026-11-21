import json

from channels.testing import WebsocketCommunicator
from django.test import TestCase, TransactionTestCase, override_settings

from core.chat.consumers import ChatConsumer
from core.models import AccessLevel, ChatMessage, ChatRoom, ChatRoomMember, ChatRoomType, Ir

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
IN_MEMORY_LAYER = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


def _make_people_and_room():
    people = {}
    for key, level in (("admin", AccessLevel.ADMIN), ("ctc", AccessLevel.CTC), ("ldc", AccessLevel.LDC),
                       ("ldc2", AccessLevel.LDC), ("ls", AccessLevel.LS), ("gc", AccessLevel.GC),
                       ("ir", AccessLevel.IR), ("player", AccessLevel.PLAYER)):
        ir_id = f"LD{key.upper()}"
        people[key] = Ir.objects.create(ir_id=ir_id, ir_name=key, ir_email=f"{ir_id}@t.t",
                                        ir_password="x", ir_access_level=level, status=True)
    room = ChatRoom.objects.create(room_type=ChatRoomType.GROUP, room_name="Team", created_by=people["ldc"])
    for ir in people.values():
        ChatRoomMember.objects.create(room=room, ir=ir)
    return people, room


@override_settings(CACHES=LOCMEM, CHANNEL_LAYERS=IN_MEMORY_LAYER)
class LeaderDeleteRestTests(TestCase):
    """An LDC (and above) may delete messages from anyone below LDC; never an LDC's or higher."""

    @classmethod
    def setUpTestData(cls):
        cls.p, cls.room = _make_people_and_room()

    def say(self, who):
        return ChatMessage.objects.create(room=self.room, sender=self.p[who], content=f"from {who}")

    def delete(self, requester, msg):
        return self.client.delete(f"/api/chat_rooms/{self.room.id}/messages/{msg.id}/delete/",
                                  data=json.dumps({"requester_ir_id": self.p[requester].ir_id}),
                                  content_type="application/json")

    def test_an_ldc_can_delete_messages_from_anyone_below_ldc(self):
        for who in ("ls", "gc", "ir", "player"):
            msg = self.say(who)
            self.assertEqual(self.delete("ldc", msg).status_code, 200, who)
            msg.refresh_from_db()
            self.assertTrue(msg.is_deleted, who)
            self.assertEqual(msg.content, "")

    def test_an_ldc_cannot_delete_another_ldc_a_ctc_or_an_admin(self):
        for who in ("ldc2", "ctc", "admin"):
            msg = self.say(who)
            self.assertEqual(self.delete("ldc", msg).status_code, 403, who)
            msg.refresh_from_db()
            self.assertFalse(msg.is_deleted, who)

    def test_an_ldc_can_still_delete_their_own(self):
        msg = self.say("ldc")
        self.assertEqual(self.delete("ldc", msg).status_code, 200)

    def test_people_below_ldc_still_cannot_delete_others(self):
        for requester, target in (("ls", "ir"), ("ir", "player"), ("gc", "ls"), ("player", "ir")):
            msg = self.say(target)
            self.assertEqual(self.delete(requester, msg).status_code, 403, (requester, target))
            msg.refresh_from_db()
            self.assertFalse(msg.is_deleted)

    def test_people_below_ldc_can_still_delete_their_own(self):
        msg = self.say("ir")
        self.assertEqual(self.delete("ir", msg).status_code, 200)

    def test_an_ldc_outside_the_room_cannot_delete_anything_in_it(self):
        outsider = Ir.objects.create(ir_id="LDOUT", ir_name="o", ir_email="o@t.t", ir_password="x",
                                     ir_access_level=AccessLevel.LDC, status=True)
        msg = self.say("ir")
        r = self.client.delete(f"/api/chat_rooms/{self.room.id}/messages/{msg.id}/delete/",
                               data=json.dumps({"requester_ir_id": outsider.ir_id}),
                               content_type="application/json")
        self.assertEqual(r.status_code, 403)

    def test_messages_carry_the_senders_level_so_the_app_can_offer_delete(self):
        msg = self.say("ir")
        listed = self.client.get(f"/api/chat_rooms/{self.room.id}/messages/",
                                 {"requester_ir_id": self.p["ldc"].ir_id}).json()["messages"]
        row = next(m for m in listed if m["id"] == msg.id)
        self.assertEqual(row["sender_access_level"], AccessLevel.IR)


@override_settings(CACHES=LOCMEM, CHANNEL_LAYERS=IN_MEMORY_LAYER)
class LeaderDeleteWebSocketTests(TransactionTestCase):
    def setUp(self):
        self.p, self.room = _make_people_and_room()

    async def _connect(self, who):
        ws = WebsocketCommunicator(ChatConsumer.as_asgi(), f"/ws/chat/{self.room.id}/?ir_id={self.p[who].ir_id}")
        ws.scope["url_route"] = {"kwargs": {"room_id": self.room.id}}
        ok, _ = await ws.connect()
        self.assertTrue(ok)
        return ws

    def _make(self, who):
        return ChatMessage.objects.create(room=self.room, sender=self.p[who], content="x").id

    async def test_an_ldc_deleting_a_lower_ranks_message_broadcasts_it(self):
        from asgiref.sync import sync_to_async
        mid = await sync_to_async(self._make)("ir")
        ws = await self._connect("ldc")
        await ws.send_json_to({"type": "delete_message", "message_id": mid})
        event = await ws.receive_json_from(timeout=5)
        self.assertEqual(event, {"type": "message_deleted", "message_id": mid})
        self.assertTrue(await sync_to_async(lambda: ChatMessage.objects.get(id=mid).is_deleted)())
        await ws.disconnect()

    async def test_an_ldc_cannot_delete_another_ldcs_message_over_the_socket(self):
        from asgiref.sync import sync_to_async
        mid = await sync_to_async(self._make)("ldc2")
        ws = await self._connect("ldc")
        await ws.send_json_to({"type": "delete_message", "message_id": mid})
        event = await ws.receive_json_from(timeout=5)
        self.assertEqual(event["type"], "error")
        self.assertFalse(await sync_to_async(lambda: ChatMessage.objects.get(id=mid).is_deleted)())
        await ws.disconnect()
