from django.test import TestCase, override_settings
from core.models import (Ir, AccessLevel, ChatRoom, ChatRoomMember, ChatRoomType)
from core.views.chat import _can_moderate_room


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class ChatAdminModerationTests(TestCase):
    """
    Deleting a group and removing people from it used to be owner-only, so an
    Admin sitting in a group someone else created could not clean it up. An
    Admin now qualifies — but membership is still the boundary: being Admin is
    not a key to every conversation in the org.
    """

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, name, lvl):
            return Ir.objects.create(ir_id=ir_id, ir_name=name, ir_email=f"{ir_id}@t.t",
                                     ir_password="x", ir_access_level=lvl, status=True)

        cls.admin = mk("CADMIN", "The Admin", AccessLevel.ADMIN)
        cls.owner = mk("COWNER", "The Owner", AccessLevel.LDC)
        cls.ctc = mk("CCTC", "A CTC", AccessLevel.CTC)
        cls.member = mk("CMEMBER", "A Member", AccessLevel.IR)

        cls.group = ChatRoom.objects.create(room_type=ChatRoomType.GROUP,
                                            room_name="Someone else's group",
                                            created_by=cls.owner)
        for ir in (cls.owner, cls.admin, cls.ctc, cls.member):
            ChatRoomMember.objects.create(room=cls.group, ir=ir)

    def test_owner_can_moderate_their_own_group(self):
        self.assertTrue(_can_moderate_room(self.group, self.owner))

    def test_admin_can_moderate_a_group_they_did_not_create(self):
        self.assertTrue(_can_moderate_room(self.group, self.admin))

    def test_ctc_and_ir_still_cannot(self):
        """Only Admin was asked for — the wider elevated set must not leak in."""
        self.assertFalse(_can_moderate_room(self.group, self.ctc))
        self.assertFalse(_can_moderate_room(self.group, self.member))

    def test_direct_chats_have_no_owner(self):
        direct = ChatRoom.objects.create(room_type=ChatRoomType.DIRECT,
                                         room_name="dm", created_by=self.owner)
        self.assertTrue(_can_moderate_room(direct, self.member))

    def test_membership_is_still_required_by_the_view(self):
        """
        _can_moderate_room deliberately does not check membership — the views
        check it first and would 403 before reaching here. This pins that
        contract so a future caller does not assume otherwise.
        """
        outsider = Ir.objects.create(ir_id="COUT", ir_name="Outsider", ir_email="o@t.t",
                                     ir_password="x", ir_access_level=AccessLevel.ADMIN, status=True)
        self.assertFalse(ChatRoomMember.objects.filter(room=self.group, ir=outsider).exists())
        self.assertTrue(_can_moderate_room(self.group, outsider))

    def test_view_403s_an_admin_who_is_not_in_the_group(self):
        """The end-to-end guard: membership is checked before ownership."""
        outsider = Ir.objects.create(ir_id="COUT2", ir_name="Outsider", ir_email="o2@t.t",
                                     ir_password="x", ir_access_level=AccessLevel.ADMIN, status=True)
        r = self.client.delete(f"/api/chat_rooms/{self.group.id}/delete/",
                               data={"requester_ir_id": outsider.ir_id},
                               content_type="application/json")
        self.assertEqual(r.status_code, 403)
        self.assertTrue(ChatRoom.objects.filter(id=self.group.id).exists())

    def test_view_lets_a_member_admin_delete_someone_else_s_group(self):
        r = self.client.delete(f"/api/chat_rooms/{self.group.id}/delete/",
                               data={"requester_ir_id": self.admin.ir_id},
                               content_type="application/json")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(ChatRoom.objects.filter(id=self.group.id).exists())

    def test_view_lets_a_member_admin_remove_someone(self):
        r = self.client.post(f"/api/chat_rooms/{self.group.id}/members/remove/",
                             data={"requester_ir_id": self.admin.ir_id,
                                   "member_ir_ids": [self.member.ir_id]},
                             content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(ChatRoomMember.objects.filter(room=self.group, ir=self.member).exists())

    def test_view_still_403s_a_ctc_in_the_group(self):
        r = self.client.post(f"/api/chat_rooms/{self.group.id}/members/remove/",
                             data={"requester_ir_id": self.ctc.ir_id,
                                   "member_ir_ids": [self.member.ir_id]},
                             content_type="application/json")
        self.assertEqual(r.status_code, 403)
        self.assertTrue(ChatRoomMember.objects.filter(room=self.group, ir=self.member).exists())
