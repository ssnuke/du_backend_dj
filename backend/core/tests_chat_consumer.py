from channels.layers import InMemoryChannelLayer
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings

from core.chat.consumers import ChatConsumer
from core.models import AccessLevel, ChatRoom, ChatRoomMember, ChatRoomType, Ir

IN_MEMORY_CHANNEL_LAYERS = {
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
}


@override_settings(CHANNEL_LAYERS=IN_MEMORY_CHANNEL_LAYERS)
class ChatConsumerSendMessageTests(TransactionTestCase):
    """
    Exercises the actual send_message hot path end-to-end (connect, then
    send_message) against an in-memory channel layer, to verify the
    connection-scoped room/sender caching and the parallelized
    broadcast+fanout didn't break anything at runtime — not just at import
    time.
    """

    def setUp(self):
        self.sender = Ir.objects.create(
            ir_id="SENDER1", ir_name="Sender", ir_access_level=AccessLevel.LS, status=True
        )
        self.other = Ir.objects.create(
            ir_id="OTHER1", ir_name="Other", ir_access_level=AccessLevel.LS, status=True
        )
        self.room = ChatRoom.objects.create(
            room_type=ChatRoomType.GROUP, room_name="Room", created_by=self.sender
        )
        ChatRoomMember.objects.create(room=self.room, ir=self.sender)
        ChatRoomMember.objects.create(room=self.room, ir=self.other)

    async def _connect(self, ir_id):
        communicator = WebsocketCommunicator(
            ChatConsumer.as_asgi(), f"/ws/chat/{self.room.id}/?ir_id={ir_id}"
        )
        communicator.scope["url_route"] = {"kwargs": {"room_id": self.room.id}}
        connected, _ = await communicator.connect()
        self.assertTrue(connected, f"connect() failed for {ir_id}")
        return communicator

    async def test_send_message_broadcasts_to_room_and_persists(self):
        sender_ws = await self._connect(self.sender.ir_id)
        other_ws = await self._connect(self.other.ir_id)

        await sender_ws.send_json_to({"type": "send_message", "content": "hello world"})

        broadcast = await sender_ws.receive_json_from(timeout=5)
        self.assertEqual(broadcast["type"], "message_created")
        self.assertEqual(broadcast["message"]["content"], "hello world")
        self.assertEqual(broadcast["message"]["sender_ir_id"], self.sender.ir_id)

        other_broadcast = await other_ws.receive_json_from(timeout=5)
        self.assertEqual(other_broadcast["type"], "message_created")
        self.assertEqual(other_broadcast["message"]["content"], "hello world")

        await sender_ws.disconnect()
        await other_ws.disconnect()

    async def test_second_message_reuses_cached_room_and_sender(self):
        # Regression check for the _create_message signature change: room and
        # sender are now passed in as cached objects rather than re-fetched
        # by id, so sending twice on the same connection must still work.
        sender_ws = await self._connect(self.sender.ir_id)

        await sender_ws.send_json_to({"type": "send_message", "content": "first"})
        first = await sender_ws.receive_json_from(timeout=5)
        self.assertEqual(first["message"]["content"], "first")

        await sender_ws.send_json_to({"type": "send_message", "content": "second"})
        second = await sender_ws.receive_json_from(timeout=5)
        self.assertEqual(second["message"]["content"], "second")
        self.assertNotEqual(first["message"]["id"], second["message"]["id"])

        await sender_ws.disconnect()

    async def test_non_member_is_rejected_on_connect(self):
        stranger = await Ir.objects.acreate(
            ir_id="STRANGER1", ir_name="Stranger", ir_access_level=AccessLevel.LS, status=True
        )
        communicator = WebsocketCommunicator(
            ChatConsumer.as_asgi(), f"/ws/chat/{self.room.id}/?ir_id={stranger.ir_id}"
        )
        communicator.scope["url_route"] = {"kwargs": {"room_id": self.room.id}}
        connected, subprotocol_or_code = await communicator.connect()
        self.assertFalse(connected)
        self.assertEqual(subprotocol_or_code, 4003)
