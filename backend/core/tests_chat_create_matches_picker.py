import json
from django.test import TestCase, override_settings

from core.models import AccessLevel, Ir, Team, TeamMember, TeamRole

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class CreatingARoomAcceptsWhomeverThePickerOffers(TestCase):
    """
    The picker (ChatCandidates) and group creation used to disagree: the picker
    listed an LS's whole downline, but creating the room checked the narrower
    direct-message rule (team members / upline only) and refused them with
    "Not authorized to add ...".
    """

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, lvl, parent=None):
            return Ir.objects.create(ir_id=ir_id, ir_name=ir_id, ir_email=f"{ir_id}@t.t", ir_password="x",
                                     ir_access_level=lvl, status=True, parent_ir=parent)
        cls.ctc = mk("CRCTC", AccessLevel.CTC)
        cls.ldc = mk("CRLDC", AccessLevel.LDC, cls.ctc)
        cls.ls = mk("CRLS", AccessLevel.LS, cls.ldc)
        cls.kid = mk("CRKID", AccessLevel.IR, cls.ls)           # LS's downline, in no team
        cls.grandkid = mk("CRGKID", AccessLevel.IR, cls.kid)
        cls.stranger = mk("CRSTRANGER", AccessLevel.IR, cls.ctc)

    def create(self, requester, room_type, members, name="Room"):
        return self.client.post("/api/chat_rooms/", data=json.dumps({
            "requester_ir_id": requester.ir_id, "room_type": room_type, "room_name": name,
            "initial_member_ir_ids": [m.ir_id for m in members]}), content_type="application/json")

    def offered(self, requester):
        r = self.client.get(f"/api/chat_candidates/?requester_ir_id={requester.ir_id}&limit=100")
        return {c["ir_id"] for c in r.json()["candidates"]}

    def test_sanity_the_picker_does_offer_the_downline(self):
        self.assertTrue({"CRKID", "CRGKID"} <= self.offered(self.ls))

    def test_ls_can_create_a_group_with_downlines(self):
        r = self.create(self.ls, "group", [self.kid, self.grandkid])
        self.assertEqual(r.status_code, 201, r.content)

    def test_ls_can_message_a_downline_directly(self):
        r = self.create(self.ls, "direct", [self.kid], name="dm")
        self.assertIn(r.status_code, (200, 201), r.content)

    def test_ls_still_cannot_add_a_stranger(self):
        self.assertEqual(self.create(self.ls, "group", [self.stranger]).status_code, 403)

    def test_ls_can_still_include_their_upline_ldc(self):
        self.assertEqual(self.create(self.ls, "group", [self.ldc, self.kid]).status_code, 201)

    def test_everyone_the_picker_offers_is_accepted_for_every_creator_role(self):
        for who in (self.ctc, self.ldc, self.ls):
            for target in self.offered(who):
                r = self.create(who, "group", [Ir.objects.get(ir_id=target)], name=f"{who.ir_id}->{target}")
                self.assertEqual(r.status_code, 201, f"{who.ir_id} was offered {target} but creation said {r.content}")
