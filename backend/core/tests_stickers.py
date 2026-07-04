import io

from django.test import TestCase

from core.models import (
    AccessLevel,
    ChatMessageType,
    ChatRoom,
    ChatRoomMember,
    ChatRoomType,
    Ir,
    Sticker,
    StickerPack,
    StickerPackSubscription,
)


def _tiny_png_bytes():
    # Smallest valid 1x1 transparent PNG.
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6360000002000100000000000000004946454e44ae"
        "426082"
    )


class StickerPackModelTests(TestCase):
    def setUp(self):
        self.owner = Ir.objects.create(
            ir_id="OWN001", ir_name="Owner", ir_access_level=AccessLevel.LS, status=True
        )

    def test_create_pack_and_stickers(self):
        pack = StickerPack.objects.create(owner=self.owner, name="My Pack")
        s1 = Sticker.objects.create(pack=pack, image_url="https://example.com/a.png", emoji="😀")
        s2 = Sticker.objects.create(pack=pack, image_url="https://example.com/b.webm", is_animated=True)

        self.assertEqual(pack.stickers.count(), 2)
        self.assertEqual(list(pack.stickers.order_by("id")), [s1, s2])
        self.assertFalse(s1.is_animated)
        self.assertTrue(s2.is_animated)

    def test_deleting_pack_cascades_stickers(self):
        pack = StickerPack.objects.create(owner=self.owner, name="Temp Pack")
        Sticker.objects.create(pack=pack, image_url="https://example.com/a.png")
        pack_id = pack.id
        pack.delete()
        self.assertEqual(Sticker.objects.filter(pack_id=pack_id).count(), 0)


class StickerPackApiTests(TestCase):
    def setUp(self):
        self.owner = Ir.objects.create(
            ir_id="OWN002", ir_name="Owner2", ir_access_level=AccessLevel.LS, status=True
        )
        self.other = Ir.objects.create(
            ir_id="OTH003", ir_name="Other3", ir_access_level=AccessLevel.LS, status=True
        )

    def test_create_and_list_pack(self):
        resp = self.client.post(
            "/api/sticker_packs/", {"requester_ir_id": self.owner.ir_id, "name": "Reactions"}
        )
        self.assertEqual(resp.status_code, 201)
        pack_id = resp.json()["pack"]["id"]

        resp = self.client.get("/api/sticker_packs/", {"requester_ir_id": self.owner.ir_id})
        self.assertEqual(resp.status_code, 200)
        packs = resp.json()["packs"]
        self.assertEqual(len(packs), 1)
        self.assertEqual(packs[0]["id"], pack_id)
        self.assertEqual(packs[0]["stickers"], [])

    def test_list_only_returns_own_packs(self):
        StickerPack.objects.create(owner=self.owner, name="Mine")
        StickerPack.objects.create(owner=self.other, name="Not mine")

        resp = self.client.get("/api/sticker_packs/", {"requester_ir_id": self.owner.ir_id})
        packs = resp.json()["packs"]
        self.assertEqual(len(packs), 1)
        self.assertEqual(packs[0]["name"], "Mine")

    def test_upload_sticker_to_own_pack(self):
        pack = StickerPack.objects.create(owner=self.owner, name="Pack")
        image = io.BytesIO(_tiny_png_bytes())
        image.name = "sticker.png"

        resp = self.client.post(
            f"/api/sticker_packs/{pack.id}/stickers/",
            {
                "requester_ir_id": self.owner.ir_id,
                "file": image,
                "emoji": "🎉",
            },
            format="multipart",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        data = resp.json()["sticker"]
        self.assertEqual(data["emoji"], "🎉")
        self.assertFalse(data["is_animated"])
        self.assertEqual(pack.stickers.count(), 1)

        pack.refresh_from_db()
        self.assertEqual(pack.cover_sticker_id, pack.stickers.first().id)

    def test_upload_sticker_rejects_non_owner(self):
        pack = StickerPack.objects.create(owner=self.owner, name="Pack")
        image = io.BytesIO(_tiny_png_bytes())
        image.name = "sticker.png"

        resp = self.client.post(
            f"/api/sticker_packs/{pack.id}/stickers/",
            {"requester_ir_id": self.other.ir_id, "file": image},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(pack.stickers.count(), 0)

    def test_upload_sticker_rejects_disallowed_mime(self):
        pack = StickerPack.objects.create(owner=self.owner, name="Pack")
        bad_file = io.BytesIO(b"not a real doc")
        bad_file.name = "file.pdf"

        resp = self.client.post(
            f"/api/sticker_packs/{pack.id}/stickers/",
            {"requester_ir_id": self.owner.ir_id, "file": bad_file},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 400)

    def test_delete_pack_requires_ownership(self):
        pack = StickerPack.objects.create(owner=self.owner, name="Pack")

        resp = self.client.delete(
            f"/api/sticker_packs/{pack.id}/delete/",
            {"requester_ir_id": self.other.ir_id},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(StickerPack.objects.filter(id=pack.id).exists())

        resp = self.client.delete(
            f"/api/sticker_packs/{pack.id}/delete/",
            {"requester_ir_id": self.owner.ir_id},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(StickerPack.objects.filter(id=pack.id).exists())


class StickerMessageSendTests(TestCase):
    def setUp(self):
        self.sender = Ir.objects.create(
            ir_id="SND001", ir_name="Sender", ir_access_level=AccessLevel.LS, status=True
        )
        self.room = ChatRoom.objects.create(
            room_type=ChatRoomType.GROUP, room_name="Room", created_by=self.sender
        )
        ChatRoomMember.objects.create(room=self.room, ir=self.sender)

    def test_sending_a_sticker_message_is_accepted(self):
        resp = self.client.post(
            f"/api/chat_rooms/{self.room.id}/messages/",
            {
                "requester_ir_id": self.sender.ir_id,
                "message_type": "sticker",
                "attachment_url": "https://example.com/sticker.png",
                "attachment_name": "🎉",
            },
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        message = resp.json()["chat_message"]
        self.assertEqual(message["message_type"], ChatMessageType.STICKER)
        self.assertEqual(message["attachment_url"], "https://example.com/sticker.png")


class StickerPackSubscriptionTests(TestCase):
    def setUp(self):
        self.owner = Ir.objects.create(
            ir_id="PUBOWNER1", ir_name="Owner", ir_access_level=AccessLevel.LS, status=True
        )
        self.user = Ir.objects.create(
            ir_id="SUBUSER1", ir_name="Subscriber", ir_access_level=AccessLevel.LS, status=True
        )
        self.public_pack = StickerPack.objects.create(owner=self.owner, name="Official Pack", is_public=True)
        Sticker.objects.create(pack=self.public_pack, image_url="https://example.com/a.png")
        self.private_pack = StickerPack.objects.create(owner=self.owner, name="Private Pack", is_public=False)

    def test_browse_shows_only_public_packs_not_already_had(self):
        resp = self.client.get("/api/sticker_packs/browse/", {"requester_ir_id": self.user.ir_id})
        self.assertEqual(resp.status_code, 200)
        names = [p["name"] for p in resp.json()["packs"]]
        self.assertIn("Official Pack", names)
        self.assertNotIn("Private Pack", names)

    def test_owner_does_not_see_own_pack_in_browse(self):
        resp = self.client.get("/api/sticker_packs/browse/", {"requester_ir_id": self.owner.ir_id})
        names = [p["name"] for p in resp.json()["packs"]]
        self.assertNotIn("Official Pack", names)

    def test_subscribe_adds_pack_to_list_without_duplicating_stickers(self):
        resp = self.client.post(
            f"/api/sticker_packs/{self.public_pack.id}/subscribe/",
            {"requester_ir_id": self.user.ir_id},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        # No new Sticker/StickerPack rows were created — pure subscription.
        self.assertEqual(StickerPack.objects.count(), 2)
        self.assertEqual(Sticker.objects.count(), 1)

        list_resp = self.client.get("/api/sticker_packs/", {"requester_ir_id": self.user.ir_id})
        packs = list_resp.json()["packs"]
        self.assertEqual(len(packs), 1)
        self.assertEqual(packs[0]["id"], self.public_pack.id)
        self.assertFalse(packs[0]["is_own"])

        # Now excluded from browse since the user already has it.
        browse_resp = self.client.get("/api/sticker_packs/browse/", {"requester_ir_id": self.user.ir_id})
        self.assertNotIn(self.public_pack.id, [p["id"] for p in browse_resp.json()["packs"]])

    def test_subscribe_rejects_private_pack(self):
        resp = self.client.post(
            f"/api/sticker_packs/{self.private_pack.id}/subscribe/",
            {"requester_ir_id": self.user.ir_id},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(StickerPackSubscription.objects.count(), 0)

    def test_subscribe_is_idempotent(self):
        for _ in range(2):
            resp = self.client.post(
                f"/api/sticker_packs/{self.public_pack.id}/subscribe/",
                {"requester_ir_id": self.user.ir_id},
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 200)
        self.assertEqual(StickerPackSubscription.objects.count(), 1)

    def test_unsubscribe_removes_from_list_but_keeps_pack_intact(self):
        StickerPackSubscription.objects.create(ir=self.user, pack=self.public_pack)

        resp = self.client.post(
            f"/api/sticker_packs/{self.public_pack.id}/unsubscribe/",
            {"requester_ir_id": self.user.ir_id},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(StickerPackSubscription.objects.count(), 0)

        # Pack and its stickers still exist for the owner and anyone else.
        self.assertTrue(StickerPack.objects.filter(id=self.public_pack.id).exists())
        self.assertEqual(Sticker.objects.filter(pack=self.public_pack).count(), 1)

        list_resp = self.client.get("/api/sticker_packs/", {"requester_ir_id": self.user.ir_id})
        self.assertEqual(list_resp.json()["packs"], [])

    def test_owner_pack_marked_is_own_true(self):
        resp = self.client.get("/api/sticker_packs/", {"requester_ir_id": self.owner.ir_id})
        packs = {p["id"]: p for p in resp.json()["packs"]}
        self.assertTrue(packs[self.public_pack.id]["is_own"])
        self.assertTrue(packs[self.private_pack.id]["is_own"])
