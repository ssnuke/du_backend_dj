from django.core.cache import cache
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection

from core.models import (
    AccessLevel,
    ChatMessage,
    ChatMessageReaction,
    ChatRoom,
    ChatRoomMember,
    ChatRoomType,
    Ir,
)


class ChatRoomListQueryCountTests(TestCase):
    """
    Guards against the _serialize_room N+1 regression: listing rooms must not
    issue more queries as the number of rooms grows.
    """

    def setUp(self):
        self.requester = Ir.objects.create(
            ir_id="REQ001", ir_name="Requester", ir_access_level=AccessLevel.LS, status=True
        )
        self.other = Ir.objects.create(
            ir_id="OTH001", ir_name="Other", ir_access_level=AccessLevel.LS, status=True
        )

    def _make_rooms(self, count):
        room_ids = []
        for i in range(count):
            room = ChatRoom.objects.create(
                room_type=ChatRoomType.GROUP, room_name=f"Room {i}", created_by=self.requester
            )
            ChatRoomMember.objects.create(room=room, ir=self.requester)
            ChatRoomMember.objects.create(room=room, ir=self.other)
            ChatMessage.objects.create(room=room, sender=self.other, content=f"hello {i}")
            room_ids.append(room.id)
        return room_ids

    def _list_rooms(self):
        return self.client.get("/api/chat_rooms/", {"requester_ir_id": self.requester.ir_id})

    def test_query_count_flat_as_rooms_grow(self):
        self._make_rooms(3)
        with CaptureQueriesContext(connection) as ctx_small:
            resp = self._list_rooms()
        self.assertEqual(resp.status_code, 200)
        small_count = len(ctx_small.captured_queries)

        self._make_rooms(15)
        with CaptureQueriesContext(connection) as ctx_large:
            resp = self._list_rooms()
        self.assertEqual(resp.status_code, 200)
        large_count = len(ctx_large.captured_queries)

        # Before the fix this scaled linearly with room count (~5 extra
        # queries/room). After the fix it should stay roughly constant.
        self.assertLess(large_count, small_count + 5)


class ChatMessageReactionQueryCountTests(TestCase):
    """
    Guards against the _serialize_message N+1 regression: reactions must be
    served from the prefetch cache, not re-queried per message.
    """

    def setUp(self):
        self.requester = Ir.objects.create(
            ir_id="REQ002", ir_name="Requester2", ir_access_level=AccessLevel.LS, status=True
        )
        self.other = Ir.objects.create(
            ir_id="OTH002", ir_name="Other2", ir_access_level=AccessLevel.LS, status=True
        )
        self.room = ChatRoom.objects.create(
            room_type=ChatRoomType.GROUP, room_name="Room", created_by=self.requester
        )
        ChatRoomMember.objects.create(room=self.room, ir=self.requester)
        ChatRoomMember.objects.create(room=self.room, ir=self.other)

    def _add_messages_with_reactions(self, count):
        for i in range(count):
            message = ChatMessage.objects.create(room=self.room, sender=self.other, content=f"msg {i}")
            ChatMessageReaction.objects.create(message=message, ir=self.requester, emoji="👍")

    def _fetch_messages(self):
        return self.client.get(
            f"/api/chat_rooms/{self.room.id}/messages/", {"requester_ir_id": self.requester.ir_id}
        )

    def test_query_count_flat_as_messages_grow(self):
        self._add_messages_with_reactions(3)
        with CaptureQueriesContext(connection) as ctx_small:
            resp = self._fetch_messages()
        self.assertEqual(resp.status_code, 200)
        small_count = len(ctx_small.captured_queries)

        self._add_messages_with_reactions(15)
        with CaptureQueriesContext(connection) as ctx_large:
            resp = self._fetch_messages()
        self.assertEqual(resp.status_code, 200)
        large_count = len(ctx_large.captured_queries)

        # Before the fix this scaled with message count (1 extra query per
        # reaction-bearing message). After the fix it should stay roughly flat.
        self.assertLess(large_count, small_count + 3)


class ChatRoomListCacheTests(TestCase):
    """
    The room-list response should be served from cache on a repeat request,
    and a mutation (new message) must invalidate that cache so the next
    request reflects it — not just wait out the TTL.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.requester = Ir.objects.create(
            ir_id="CACHEREQ1", ir_name="Requester", ir_access_level=AccessLevel.LS, status=True
        )
        self.other = Ir.objects.create(
            ir_id="CACHEOTH1", ir_name="Other", ir_access_level=AccessLevel.LS, status=True
        )
        self.room = ChatRoom.objects.create(
            room_type=ChatRoomType.GROUP, room_name="Room", created_by=self.requester
        )
        ChatRoomMember.objects.create(room=self.room, ir=self.requester)
        ChatRoomMember.objects.create(room=self.room, ir=self.other)

    def _list_rooms(self):
        return self.client.get("/api/chat_rooms/", {"requester_ir_id": self.requester.ir_id})

    def test_second_request_is_served_from_cache(self):
        with CaptureQueriesContext(connection) as ctx_first:
            resp1 = self._list_rooms()
        self.assertEqual(resp1.status_code, 200)
        self.assertGreater(len(ctx_first.captured_queries), 0)

        with CaptureQueriesContext(connection) as ctx_second:
            resp2 = self._list_rooms()
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.json(), resp1.json())
        # Only the requester_ir_id validation lookup should run — everything
        # else (room/message/member queries) must come from cache.
        self.assertLess(len(ctx_second.captured_queries), len(ctx_first.captured_queries))

    def test_new_message_invalidates_cache(self):
        self._list_rooms()  # populate cache

        post_resp = self.client.post(
            f"/api/chat_rooms/{self.room.id}/messages/",
            {"requester_ir_id": self.other.ir_id, "message_type": "text", "content": "hello"},
            content_type="application/json",
        )
        self.assertEqual(post_resp.status_code, 201)

        with CaptureQueriesContext(connection) as ctx:
            resp = self._list_rooms()
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(len(ctx.captured_queries), 0, "cache should have been invalidated by the new message")
        self.assertEqual(resp.json()["rooms"][0]["last_message_preview"], "hello")

    def test_marking_read_invalidates_cache_for_the_reader(self):
        message = ChatMessage.objects.create(room=self.room, sender=self.other, content="unread me")
        self._list_rooms()  # populate cache with unread_count=1 for self.requester

        self.assertEqual(self._list_rooms().json()["rooms"][0]["unread_count"], 1)

        read_resp = self.client.post(
            f"/api/chat_rooms/{self.room.id}/read_receipts/",
            {"requester_ir_id": self.requester.ir_id, "message_ids": [message.id]},
            content_type="application/json",
        )
        self.assertEqual(read_resp.status_code, 200)

        # Cache must reflect the read receipt immediately, not after the TTL.
        self.assertEqual(self._list_rooms().json()["rooms"][0]["unread_count"], 0)

    def test_mark_all_read_clears_badge_without_message_ids(self):
        # Simulates the manual "Mark as read" escape hatch: unlike
        # read_receipts/, this endpoint takes no message_ids at all — the
        # server resolves everything unread on its own, so it works even if
        # the client never loaded/rendered a single one of these messages.
        for i in range(5):
            ChatMessage.objects.create(room=self.room, sender=self.other, content=f"msg {i}")
        self.assertEqual(self._list_rooms().json()["rooms"][0]["unread_count"], 5)

        resp = self.client.post(
            f"/api/chat_rooms/{self.room.id}/mark_all_read/",
            {"requester_ir_id": self.requester.ir_id},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["updated_count"], 5)
        self.assertEqual(self._list_rooms().json()["rooms"][0]["unread_count"], 0)


class ChatRoomHiddenHistoryUnreadCountTests(TestCase):
    """
    A member added to a room with history hidden (hide_history_before_join)
    can never see or mark as read anything sent before they joined —
    ChatRoomMessages.get already excludes those from the message list. The
    unread_count shown on the room list must apply the same cutoff, otherwise
    it counts messages the member is permanently unable to reach, producing a
    badge that never reaches zero no matter how much they actually read.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.creator = Ir.objects.create(
            ir_id="HHCREATE1", ir_name="Creator", ir_access_level=AccessLevel.LS, status=True
        )
        self.newcomer = Ir.objects.create(
            ir_id="HHNEWCOM1", ir_name="Newcomer", ir_access_level=AccessLevel.LS, status=True
        )
        self.room = ChatRoom.objects.create(
            room_type=ChatRoomType.GROUP, room_name="Broadcast", created_by=self.creator
        )
        ChatRoomMember.objects.create(room=self.room, ir=self.creator)

        # Messages sent before the newcomer joins.
        ChatMessage.objects.create(room=self.room, sender=self.creator, content="old message 1")
        ChatMessage.objects.create(room=self.room, sender=self.creator, content="old message 2")

        # Newcomer joins with history hidden (joined_at is auto_now_add, so it
        # naturally lands after the messages created above).
        ChatRoomMember.objects.create(room=self.room, ir=self.newcomer, hide_history_before_join=True)

        # One message sent after the newcomer joined — this one they CAN see.
        self.visible_message = ChatMessage.objects.create(
            room=self.room, sender=self.creator, content="new message"
        )

    def _list_rooms_as(self, ir_id):
        return self.client.get("/api/chat_rooms/", {"requester_ir_id": ir_id})

    def test_unread_count_excludes_pre_join_hidden_history(self):
        resp = self._list_rooms_as(self.newcomer.ir_id)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["rooms"][0]["unread_count"], 1)

    def test_unread_count_reaches_zero_after_reading_the_visible_message(self):
        read_resp = self.client.post(
            f"/api/chat_rooms/{self.room.id}/read_receipts/",
            {"requester_ir_id": self.newcomer.ir_id, "message_ids": [self.visible_message.id]},
            content_type="application/json",
        )
        self.assertEqual(read_resp.status_code, 200)

        resp = self._list_rooms_as(self.newcomer.ir_id)
        self.assertEqual(resp.json()["rooms"][0]["unread_count"], 0)

    def test_unaffected_member_unread_count_still_counts_everything(self):
        # A member without hidden history (the creator) should be unaffected
        # by this cutoff — sanity check the fix didn't over-apply.
        resp = self._list_rooms_as(self.creator.ir_id)
        # creator sent all messages themselves, so nothing is unread for them
        self.assertEqual(resp.json()["rooms"][0]["unread_count"], 0)
