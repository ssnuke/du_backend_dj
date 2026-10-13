import json
from django.test import TestCase, override_settings

from core.models import AccessLevel, ChatRoom, ChatRoomMember, ChatRoomType, ChatMessage, Ir

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class PlayerInGroupChatTests(TestCase):
    """A leader can add a Player to a group; inside it they chat like anyone."""

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, lvl, parent=None):
            return Ir.objects.create(ir_id=ir_id, ir_name=ir_id, ir_email=f"{ir_id}@t.t", ir_password="x",
                                     ir_access_level=lvl, status=True, parent_ir=parent)
        cls.admin = mk("GADMIN", AccessLevel.ADMIN)
        cls.ctc = mk("GCTC", AccessLevel.CTC, cls.admin)
        cls.ldc = mk("GLDC", AccessLevel.LDC, cls.ctc)
        cls.pro = mk("GPRO", AccessLevel.GC, cls.ldc)
        cls.ir = mk("GIR", AccessLevel.IR, cls.ldc)
        cls.player = mk("GPLAYER", AccessLevel.PLAYER, cls.pro)
        cls.other_player = mk("GOTHERPL", AccessLevel.PLAYER, cls.admin)   # not under the LDC
        cls.room = ChatRoom.objects.create(room_type=ChatRoomType.GROUP, room_name="G", created_by=cls.ldc)
        ChatRoomMember.objects.create(room=cls.room, ir=cls.ldc)

    def add(self, requester, who):
        return self.client.post(
            f"/api/chat_rooms/{self.room.id}/members/add/",
            data=json.dumps({"requester_ir_id": requester.ir_id, "member_ir_ids": [who.ir_id]}),
            content_type="application/json")

    def test_ldc_can_add_a_player_in_their_downline(self):
        r = self.add(self.ldc, self.player)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(ChatRoomMember.objects.filter(room=self.room, ir=self.player).exists())

    def test_ldc_cannot_add_a_player_outside_their_reach(self):
        self.assertEqual(self.add(self.ldc, self.other_player).status_code, 403)

    def test_admin_can_add_any_player(self):
        ChatRoomMember.objects.create(room=self.room, ir=self.admin)
        self.assertEqual(self.add(self.admin, self.other_player).status_code, 200)

    def test_an_added_player_sees_the_group_and_can_post(self):
        self.add(self.ldc, self.player)
        rooms = self.client.get("/api/chat_rooms/", {"requester_ir_id": self.player.ir_id}).json()["rooms"]
        self.assertIn(self.room.id, [x["id"] for x in rooms])
        r = self.client.post(
            f"/api/chat_rooms/{self.room.id}/messages/",
            data=json.dumps({"requester_ir_id": self.player.ir_id, "content": "hi all"}),
            content_type="application/json")
        self.assertIn(r.status_code, (200, 201), r.content)
        self.assertTrue(ChatMessage.objects.filter(room=self.room, sender=self.player).exists())

    def test_a_player_cannot_add_people_themselves(self):
        self.add(self.ldc, self.player)
        self.assertEqual(self.add(self.player, self.ir).status_code, 403)

    def test_a_plain_ir_cannot_add_anyone(self):
        ChatRoomMember.objects.create(room=self.room, ir=self.ir)
        self.assertEqual(self.add(self.ir, self.player).status_code, 403)
