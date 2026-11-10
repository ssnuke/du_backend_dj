import json

from django.test import TestCase, override_settings

from core.models import AccessLevel, ChatMessage, ChatRoom, ChatRoomMember, ChatRoomType, Ir

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
IN_MEMORY_LAYER = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


@override_settings(CACHES=LOCMEM, CHANNEL_LAYERS=IN_MEMORY_LAYER)
class ChatAlbumTests(TestCase):
    """Photos sent together carry one album id, so every app can show them as one album."""

    @classmethod
    def setUpTestData(cls):
        mk = lambda i: Ir.objects.create(ir_id=i, ir_name=i, ir_email=f"{i}@t.t", ir_password="x",
                                         ir_access_level=AccessLevel.LS, status=True)
        cls.a, cls.b = mk("ALBA"), mk("ALBB")
        cls.room = ChatRoom.objects.create(room_type=ChatRoomType.GROUP, room_name="R", created_by=cls.a)
        for ir in (cls.a, cls.b):
            ChatRoomMember.objects.create(room=cls.room, ir=ir)

    def send(self, **body):
        body = {"requester_ir_id": "ALBA", "message_type": "image",
                "attachment_url": "https://example.com/p.jpg", "attachment_name": "p.jpg", **body}
        return self.client.post(f"/api/chat_rooms/{self.room.id}/messages/", data=json.dumps(body),
                                content_type="application/json")

    def test_a_batch_shares_one_album_id_and_it_comes_back_everywhere(self):
        ids = []
        for n in range(3):
            r = self.send(album_id="batch-123456789", content="Trip" if n == 0 else "")
            self.assertEqual(r.status_code, 201, r.content)
            self.assertEqual(r.json()["chat_message"]["album_id"], "batch-123456789")
            ids.append(r.json()["chat_message"]["id"])
        self.assertEqual(set(ChatMessage.objects.filter(id__in=ids).values_list("album_id", flat=True)), {"batch-123456789"})
        listed = self.client.get(f"/api/chat_rooms/{self.room.id}/messages/", {"requester_ir_id": "ALBB"}).json()["messages"]
        self.assertEqual({m["album_id"] for m in listed if m["id"] in ids}, {"batch-123456789"})

    def test_an_ordinary_message_has_no_album(self):
        r = self.send()
        self.assertIsNone(r.json()["chat_message"]["album_id"])

    def test_a_bad_album_id_is_ignored_not_stored(self):
        for bad in ("short", "has spaces in it!!", "x" * 40, 12345678, ["a"], ""):
            r = self.send(album_id=bad)
            self.assertEqual(r.status_code, 201, (bad, r.content))
            self.assertIsNone(r.json()["chat_message"]["album_id"], bad)

    def test_only_photos_can_belong_to_an_album(self):
        r = self.send(message_type="text", content="hello", attachment_url=None, attachment_name=None,
                      album_id="batch-123456789")
        self.assertIsNone(r.json()["chat_message"]["album_id"])
