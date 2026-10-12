import json

from django.test import TestCase, override_settings

from core.models import AccessLevel, Ir, IrId, IrPendingRequest, PromotionDocument
from core.promotion_docs import DOCUMENTS

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class PlayerLevelTests(TestCase):
    """
    A newly registered member is a Player (level 7) until an Admin, CTC or LDC
    promotes them to IR. Players have no data entry and can chat only with
    their referrer and upline.
    """

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, name, lvl, parent=None):
            ir = Ir(ir_id=ir_id, ir_name=name, ir_email=f"{ir_id}@t.t", ir_password="x",
                    ir_access_level=lvl, status=True, parent_ir=parent)
            ir.save()
            return ir

        cls.admin = mk("PADMIN", "Admin", AccessLevel.ADMIN)
        cls.ctc = mk("PCTC", "CTC", AccessLevel.CTC, cls.admin)
        cls.ldc = mk("PLDC", "LDC", AccessLevel.LDC, cls.ctc)
        cls.other_ldc = mk("POLDC", "Other LDC", AccessLevel.LDC, cls.ctc)
        cls.ls = mk("PLS", "LS", AccessLevel.LS, cls.ldc)
        cls.player = mk("PPLAYER", "Player", AccessLevel.PLAYER, cls.ls)
        cls.stranger_player = mk("PSTRANGE", "Stranger", AccessLevel.PLAYER, cls.other_ldc)
        cls.peer = mk("PPEER", "Peer", AccessLevel.IR, cls.ls)

    def post(self, url, body):
        return self.client.post(url, data=json.dumps(body), content_type="application/json")

    def give_documents(self, ir):
        """The three promotion documents, so the document check is not what is under test."""
        for spec in DOCUMENTS:
            PromotionDocument.objects.get_or_create(
                ir=ir, kind=spec["kind"],
                defaults={"file_key": f"k/{ir.ir_id}/{spec['kind']}", "original_name": "f", "content_type": "application/pdf", "size_bytes": 1},
            )

    def promote(self, actor, target, level=AccessLevel.IR):
        return self.post("/api/change_access_level/",
                         {"acting_ir_id": actor.ir_id, "target_ir_id": target.ir_id, "new_access_level": level})

    # --- registration -----------------------------------------------------

    @override_settings(PLAYER_REGISTRATION_ENFORCED=True)
    def test_registration_always_starts_as_player(self):
        IrId.objects.get_or_create(ir_id="NEWBIE")
        r = self.post("/api/ir_requests/register/", {
            "requester_ir_id": self.ldc.ir_id, "ir_id": "NEWBIE", "ir_name": "Newbie",
            "ir_email": "n@t.t", "parent_ir_id": self.ls.ir_id,
            "ir_access_level": 3,  # a client asking for LDC is ignored
        })
        self.assertEqual(r.status_code, 201, r.content)
        pending = IrPendingRequest.objects.get(target_ir_code="NEWBIE")
        self.assertEqual(pending.payload["ir_access_level"], AccessLevel.PLAYER)

        r = self.post(f"/api/ir_requests/{pending.id}/approve/", {"requester_ir_id": self.admin.ir_id})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Ir.objects.get(ir_id="NEWBIE").ir_access_level, AccessLevel.PLAYER)

    def register(self, ir_id, **extra):
        IrId.objects.get_or_create(ir_id=ir_id)
        r = self.post("/api/ir_requests/register/", {
            "requester_ir_id": self.ldc.ir_id, "ir_id": ir_id, "ir_name": ir_id, "ir_email": "n@t.t",
            "parent_ir_id": self.ls.ir_id, **extra,
        })
        self.assertEqual(r.status_code, 201, r.content)
        return IrPendingRequest.objects.get(target_ir_code=ir_id).payload["ir_access_level"]

    @override_settings(PLAYER_REGISTRATION_ENFORCED=False)
    def test_before_go_live_the_previous_apps_explicit_level_still_works(self):
        self.assertEqual(self.register("OLD1", ir_access_level=6), AccessLevel.IR)
        self.assertEqual(self.register("OLD2", ir_access_level=3), AccessLevel.LDC)
        # Nonsense, or Admin/CTC, is not honoured.
        self.assertEqual(self.register("OLD3", ir_access_level=1), AccessLevel.PLAYER)
        self.assertEqual(self.register("OLD4", ir_access_level="abc"), AccessLevel.PLAYER)

    @override_settings(PLAYER_REGISTRATION_ENFORCED=False)
    def test_the_new_app_sends_no_level_and_gets_a_player(self):
        self.assertEqual(self.register("NEW1"), AccessLevel.PLAYER)

    # --- promotion --------------------------------------------------------

    def test_ldc_can_promote_player_in_their_tree(self):
        self.give_documents(self.player)
        r = self.promote(self.ldc, self.player)
        self.assertEqual(r.status_code, 200, r.content)
        self.player.refresh_from_db()
        self.assertEqual(self.player.ir_access_level, AccessLevel.IR)

    def test_ldc_cannot_promote_player_outside_their_tree(self):
        self.assertEqual(self.promote(self.ldc, self.stranger_player).status_code, 403)

    def test_ldc_can_promote_a_player_they_registered_elsewhere(self):
        IrPendingRequest.objects.create(
            action_type=IrPendingRequest.ActionType.REGISTER, requested_by=self.ldc,
            target_ir_code=self.stranger_player.ir_id, target_ir_name="Stranger",
            request_status=IrPendingRequest.Status.APPROVED, payload={})
        self.give_documents(self.stranger_player)
        self.assertEqual(self.promote(self.ldc, self.stranger_player).status_code, 200)

    def test_ldc_can_only_promote_to_ir_and_only_players(self):
        self.assertEqual(self.promote(self.ldc, self.player, AccessLevel.LS).status_code, 403)
        self.assertEqual(self.promote(self.ldc, self.peer, AccessLevel.GC).status_code, 403)

    def test_ls_cannot_promote(self):
        self.assertEqual(self.promote(self.ls, self.player).status_code, 403)

    def test_ctc_can_promote_and_demote_to_player(self):
        self.give_documents(self.player)
        self.assertEqual(self.promote(self.ctc, self.player, AccessLevel.LS).status_code, 200)
        self.assertEqual(self.promote(self.ctc, self.peer, AccessLevel.PLAYER).status_code, 200)
        self.peer.refresh_from_db()
        self.assertEqual(self.peer.ir_access_level, AccessLevel.PLAYER)

    # --- promotable list --------------------------------------------------

    def test_promotable_list_is_scoped(self):
        def ids(actor):
            r = self.client.get(f"/api/promotable_players/?requester_ir_id={actor.ir_id}")
            self.assertEqual(r.status_code, 200, r.content)
            return {p["ir_id"] for p in r.json()["results"]}
        self.assertEqual(ids(self.ldc), {"PPLAYER"})
        self.assertEqual(ids(self.ctc), {"PPLAYER", "PSTRANGE"})
        self.assertEqual(self.client.get(f"/api/promotable_players/?requester_ir_id={self.ls.ir_id}").status_code, 403)

    # --- data entry -------------------------------------------------------

    def test_player_cannot_add_data_even_for_self(self):
        self.assertFalse(self.player.can_add_data_for_ir(self.player))
        r = self.post(f"/api/add_uv/{self.player.ir_id}/", {"uv_count": 1, "requester_ir_id": self.player.ir_id})
        self.assertEqual(r.status_code, 403)

    # --- chat -------------------------------------------------------------

    def test_player_can_dm_upline_but_not_peers(self):
        def can(target):
            r = self.post("/api/chat_rooms/", {
                "requester_ir_id": self.player.ir_id, "room_type": "direct",
                "room_name": "dm", "initial_member_ir_ids": [target.ir_id],
            })
            return r.status_code in (200, 201)
        self.assertTrue(can(self.ls), "referrer")
        self.assertTrue(can(self.ldc), "upline further up")
        self.assertFalse(can(self.peer))
        self.assertFalse(can(self.stranger_player))

    def test_player_candidates_are_the_upline(self):
        r = self.client.get(f"/api/chat_candidates/?requester_ir_id={self.player.ir_id}")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual({c["ir_id"] for c in r.json()["candidates"]},
                         {"PLS", "PLDC", "PCTC", "PADMIN"})

    def test_player_cannot_create_groups(self):
        r = self.post("/api/chat_rooms/", {
            "requester_ir_id": self.player.ir_id, "room_type": "group", "room_name": "G",
            "initial_member_ir_ids": [self.ls.ir_id],
        })
        self.assertEqual(r.status_code, 403)


@override_settings(CACHES=LOCMEM)
class PlayerSystemCountTests(TestCase):
    """Players do not count toward the System Count until promoted."""

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, lvl, parent=None):
            ir = Ir(ir_id=ir_id, ir_name=ir_id, ir_email=f"{ir_id}@t.t", ir_password="x",
                    ir_access_level=lvl, status=True, parent_ir=parent)
            ir.save()
            return ir

        cls.admin = mk("CADMIN", AccessLevel.ADMIN)
        cls.ctc = mk("CCTC", AccessLevel.CTC, cls.admin)
        cls.ldc = mk("CLDC", AccessLevel.LDC, cls.ctc)
        cls.ir = mk("CIR", AccessLevel.IR, cls.ldc)
        cls.player = mk("CPLAYER", AccessLevel.PLAYER, cls.ldc)

    def count(self, ir):
        r = self.client.get(f"/api/downline_data/{ir.ir_id}/")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()["counts"]["total_viewable_irs"]

    def test_player_is_not_counted_until_promoted(self):
        before = {i.ir_id: self.count(i) for i in (self.admin, self.ctc, self.ldc)}
        self.assertEqual(before["CADMIN"], 4)  # admin, ctc, ldc, ir — not the player
        self.assertEqual(before["CCTC"], 2)    # ldc + ir

        self.player.ir_access_level = AccessLevel.IR
        self.player.save()
        self.assertEqual(self.count(self.admin), 5)
        self.assertEqual(self.count(self.ctc), 3)
