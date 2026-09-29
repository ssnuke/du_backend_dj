import json
from django.test import TestCase, override_settings
from core.models import Ir, AccessLevel, ChatRoom, ChatRoomMember, ChatRoomType

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class ChatLeaveGroupTests(TestCase):
    """
    Members could not exit a group at all — ChatRoomMembersRemove only lets
    the owner/an Admin remove *someone else*. ChatRoomLeave lets any member
    remove themselves; the one rule is that the owner can't just vanish and
    leave the group ownerless — they must hand it to another member first.
    """

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, name, lvl=AccessLevel.IR):
            return Ir.objects.create(ir_id=ir_id, ir_name=name, ir_email=f"{ir_id}@t.t",
                                     ir_password="x", ir_access_level=lvl, status=True)

        cls.owner = mk("LOWNER", "The Owner", AccessLevel.LDC)
        cls.member = mk("LMEMBER", "A Member")
        cls.other = mk("LOTHER", "Another Member")
        cls.outsider = mk("LOUT", "Not In The Group")

        cls.group = ChatRoom.objects.create(room_type=ChatRoomType.GROUP, room_name="G", created_by=cls.owner)
        for ir in (cls.owner, cls.member, cls.other):
            ChatRoomMember.objects.create(room=cls.group, ir=ir)

        cls.direct = ChatRoom.objects.create(room_type=ChatRoomType.DIRECT, room_name="D", created_by=cls.owner)
        for ir in (cls.owner, cls.member):
            ChatRoomMember.objects.create(room=cls.direct, ir=ir)

    def leave(self, room, requester, new_owner=None):
        payload = {"requester_ir_id": requester.ir_id}
        if new_owner:
            payload["new_owner_ir_id"] = new_owner.ir_id
        return self.client.post(
            f"/api/chat_rooms/{room.id}/leave/",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def is_member(self, room, ir):
        return ChatRoomMember.objects.filter(room=room, ir=ir).exists()

    def test_a_plain_member_can_leave(self):
        r = self.leave(self.group, self.member)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(self.is_member(self.group, self.member))
        # Owner and the other member are untouched.
        self.assertTrue(self.is_member(self.group, self.owner))
        self.assertTrue(self.is_member(self.group, self.other))

    def test_owner_cannot_leave_without_naming_a_successor(self):
        r = self.leave(self.group, self.owner)
        self.assertEqual(r.status_code, 400)
        self.assertTrue(self.is_member(self.group, self.owner))
        self.group.refresh_from_db()
        self.assertEqual(self.group.created_by_id, self.owner.ir_id)

    def test_owner_leaving_with_a_successor_transfers_and_removes_them(self):
        r = self.leave(self.group, self.owner, new_owner=self.member)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["new_owner"]["ir_id"], self.member.ir_id)
        self.assertFalse(self.is_member(self.group, self.owner))
        self.assertTrue(self.is_member(self.group, self.member))
        self.group.refresh_from_db()
        self.assertEqual(self.group.created_by_id, self.member.ir_id)

    def test_successor_must_already_be_a_member(self):
        r = self.leave(self.group, self.owner, new_owner=self.outsider)
        self.assertEqual(r.status_code, 400)
        self.assertTrue(self.is_member(self.group, self.owner))

    def test_successor_cannot_be_self(self):
        r = self.client.post(
            f"/api/chat_rooms/{self.group.id}/leave/",
            data=json.dumps({"requester_ir_id": self.owner.ir_id, "new_owner_ir_id": self.owner.ir_id}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 400)

    def test_sole_member_owner_cannot_leave(self):
        solo = ChatRoom.objects.create(room_type=ChatRoomType.GROUP, room_name="Solo", created_by=self.owner)
        ChatRoomMember.objects.create(room=solo, ir=self.owner)
        r = self.leave(solo, self.owner)
        self.assertEqual(r.status_code, 400)
        self.assertTrue(self.is_member(solo, self.owner))

    def test_non_member_cannot_leave(self):
        r = self.leave(self.group, self.outsider)
        self.assertEqual(r.status_code, 403)

    def test_direct_chats_have_no_leave(self):
        r = self.leave(self.direct, self.member)
        self.assertEqual(r.status_code, 400)
        self.assertTrue(self.is_member(self.direct, self.member))


@override_settings(CACHES=LOCMEM)
class ChatTransferOwnershipTests(TestCase):
    """Handing the group to someone else without leaving it."""

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, name, lvl=AccessLevel.IR):
            return Ir.objects.create(ir_id=ir_id, ir_name=name, ir_email=f"{ir_id}@t.t",
                                     ir_password="x", ir_access_level=lvl, status=True)

        cls.admin = mk("TADMIN", "The Admin", AccessLevel.ADMIN)
        cls.owner = mk("TOWNER", "The Owner", AccessLevel.LDC)
        cls.member = mk("TMEMBER", "A Member")
        cls.outsider = mk("TOUT", "Not In The Group")

        cls.group = ChatRoom.objects.create(room_type=ChatRoomType.GROUP, room_name="G", created_by=cls.owner)
        for ir in (cls.owner, cls.admin, cls.member):
            ChatRoomMember.objects.create(room=cls.group, ir=ir)

    def transfer(self, requester, new_owner):
        return self.client.post(
            f"/api/chat_rooms/{self.group.id}/transfer_ownership/",
            data=json.dumps({"requester_ir_id": requester.ir_id, "new_owner_ir_id": new_owner.ir_id}),
            content_type="application/json",
        )

    def test_owner_can_transfer(self):
        r = self.transfer(self.owner, self.member)
        self.assertEqual(r.status_code, 200, r.content)
        self.group.refresh_from_db()
        self.assertEqual(self.group.created_by_id, self.member.ir_id)
        # The old owner is still a member — transferring is not leaving.
        self.assertTrue(ChatRoomMember.objects.filter(room=self.group, ir=self.owner).exists())

    def test_admin_in_room_can_transfer(self):
        r = self.transfer(self.admin, self.member)
        self.assertEqual(r.status_code, 200, r.content)

    def test_plain_member_cannot_transfer(self):
        r = self.transfer(self.member, self.owner)
        self.assertEqual(r.status_code, 400)
        self.group.refresh_from_db()
        self.assertEqual(self.group.created_by_id, self.owner.ir_id)

    def test_target_must_be_a_member(self):
        r = self.transfer(self.owner, self.outsider)
        self.assertEqual(r.status_code, 400)
