import asyncio
import functools
import json
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from django.utils import timezone

from core.models import AccessLevel, ChatMessage, ChatMessageReaction, ChatMessageReceipt, ChatRoom, ChatRoomMember, Ir
from core.views.chat import invalidate_chat_rooms_cache, _room_member_ir_ids, _can_moderate_room

# Dedicated executor for outbound FCM network calls, kept separate from
# Django's shared thread-sensitive executor (the one `database_sync_to_async`
# uses process-wide). Without this, a slow push send would hold that single
# shared thread and stall unrelated DB-touching work across the whole process.
_fcm_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="fcm-send")

# Strong references to fire-and-forget background tasks (e.g. push
# notifications) so they aren't garbage-collected mid-flight — asyncio only
# holds a weak reference to tasks created via create_task.
_background_tasks = set()


def _spawn_background(coro):
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.room_id = self.scope["url_route"]["kwargs"]["room_id"]
        self.group_name = f"chat_room_{self.room_id}"

        query_string = self.scope.get("query_string", b"").decode()
        params = parse_qs(query_string)
        ir_id = (params.get("ir_id") or [None])[0]

        if not ir_id:
            await self.close(code=4001)
            return

        self.user_ir = await self._get_ir(ir_id)
        if not self.user_ir:
            await self.close(code=4002)
            return

        self.chat_room = await self._get_room_if_member(self.room_id, self.user_ir.ir_id)
        if not self.chat_room:
            await self.close(code=4003)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self._mark_present()

    async def disconnect(self, close_code):
        if hasattr(self, 'user_ir') and self.user_ir:
            await self._update_last_seen(self.user_ir.ir_id)
            await self._clear_present()
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    @database_sync_to_async
    def _mark_present(self):
        from core.chat.notify import mark_room_present
        mark_room_present(self.room_id, self.user_ir.ir_id)

    @database_sync_to_async
    def _clear_present(self):
        from core.chat.notify import clear_room_present
        clear_room_present(self.room_id, self.user_ir.ir_id)

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return

        try:
            payload = json.loads(text_data)
        except json.JSONDecodeError:
            await self._send_error("Invalid JSON payload")
            return

        event_type = payload.get("type")

        if event_type == "send_message":
            await self._handle_send_message(payload)
        elif event_type == "typing_start":
            await self._broadcast_typing(True)
        elif event_type == "typing_stop":
            await self._broadcast_typing(False)
        elif event_type == "mark_read":
            await self._handle_mark_read(payload)
        elif event_type == "edit_message":
            await self._handle_edit_message(payload)
        elif event_type == "delete_message":
            await self._handle_delete_message(payload)
        elif event_type == "rename_room":
            await self._handle_rename_room(payload)
        elif event_type == "add_members":
            await self._handle_add_members(payload)
        elif event_type == "remove_members":
            await self._handle_remove_members(payload)
        elif event_type == "pin_message":
            await self._handle_pin_message(payload)
        elif event_type == "unpin_message":
            await self._handle_unpin_message(payload)
        elif event_type == "buzz":
            await self._handle_buzz()
        elif event_type == "ping":
            await self._mark_present()  # refresh presence TTL on every heartbeat
            await self.send(text_data=json.dumps({"type": "pong"}))
        else:
            await self._send_error("Unsupported event type")

    async def _handle_send_message(self, payload):
        content = (payload.get("content") or "").strip()
        if not content:
            await self._send_error("content is required")
            return

        reply_to_id = payload.get("reply_to_id")
        mentioned_ir_ids = await self._validate_mentioned_ir_ids(
            self.room_id, self.user_ir.ir_id, payload.get("mentioned_ir_ids")
        )

        message = await self._create_message(self.chat_room, self.user_ir, content, reply_to_id, mentioned_ir_ids)

        async def _fanout_inbox_updates():
            # Push a lightweight room-preview update to every other member's
            # inbox channel so their room list refreshes in real time even
            # when they have a different room open (or no room open at all).
            # Concurrency is capped (CHAT_FANOUT_CONCURRENCY) instead of firing
            # one group_send per member all at once — a large room otherwise
            # bursts past the Redis channel layer's connection pool
            # (REDIS_CHANNEL_MAX_CONNECTIONS) in a single message send.
            member_ir_ids = await self._get_room_member_ir_ids(self.room_id, exclude_ir_id=self.user_ir.ir_id)
            if not member_ir_ids:
                return

            semaphore = asyncio.Semaphore(settings.CHAT_FANOUT_CONCURRENCY)

            async def _send_one(member_ir_id):
                async with semaphore:
                    await self.channel_layer.group_send(
                        f"user_inbox_{member_ir_id}",
                        {
                            "type": "room_preview_updated",
                            "room_id": message["room_id"],
                            "last_message_preview": message["content"],
                            "last_message_at": message["created_at"],
                            "sender_ir_id": message["sender_ir_id"],
                        },
                    )

            await asyncio.gather(*(_send_one(member_ir_id) for member_ir_id in member_ir_ids))

        # The primary room broadcast and the inbox fanout (member lookup +
        # per-member group_send) don't depend on each other — run them
        # concurrently instead of chaining awaits, so the fanout's DB query
        # doesn't sit in front of the broadcast everyone in the room is
        # waiting on.
        await asyncio.gather(
            self.channel_layer.group_send(
                self.group_name,
                {
                    "type": "message_created",
                    "message": message,
                },
            ),
            _fanout_inbox_updates(),
        )

        # Cache invalidation doesn't need to block message delivery — fire it
        # the same non-blocking way FCM push already is.
        _spawn_background(self._invalidate_room_cache_for_room(self.room_id))

        # Fire FCM push notifications to members who are not in this WS session.
        # Not awaited: push delivery (network-bound, can be slow/flaky) must
        # never block this connection from handling its next incoming frame.
        _spawn_background(self._notify_room_members(
            self.room_id,
            self.user_ir.ir_id,
            self.user_ir.chat_name,
            content,
            mentioned_ir_ids=mentioned_ir_ids,
        ))

    async def _broadcast_typing(self, is_typing):
        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "typing_updated",
                "ir_id": self.user_ir.ir_id,
                "ir_name": self.user_ir.chat_name,
                "is_typing": is_typing,
            },
        )

    async def _handle_mark_read(self, payload):
        message_ids = payload.get("message_ids") or []
        if not isinstance(message_ids, list) or not message_ids:
            await self._send_error("message_ids is required")
            return

        updated_ids = await self._mark_read(self.room_id, self.user_ir.ir_id, message_ids)
        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "read_receipts_updated",
                "message_ids": updated_ids,
                "reader_ir_id": self.user_ir.ir_id,
                "reader_name": self.user_ir.ir_name,
            },
        )
        if updated_ids:
            # Let the reader's own other tabs/devices know this room's unread
            # count changed, so their room list badge updates live instead of
            # only on next full mount/reload.
            await self.channel_layer.group_send(
                f"user_inbox_{self.user_ir.ir_id}",
                {"type": "own_read_state_updated", "room_id": self.room_id},
            )

    async def _handle_edit_message(self, payload):
        message_id = payload.get("message_id")
        new_content = (payload.get("content") or "").strip()
        if not message_id or not new_content:
            await self._send_error("message_id and content are required")
            return

        mentioned_ir_ids = await self._validate_mentioned_ir_ids(
            self.room_id, self.user_ir.ir_id, payload.get("mentioned_ir_ids")
        )

        updated, error = await self._edit_message(message_id, self.user_ir.ir_id, new_content, mentioned_ir_ids)
        if error:
            await self._send_error(error)
            return

        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "message_edited",
                "message": updated,
            },
        )

        # Fire FCM push notifications the same way a new message does — edits
        # previously never notified at all, silently even when the edit added
        # a fresh @mention. Not awaited, same reasoning as the send path.
        _spawn_background(self._notify_room_members(
            self.room_id,
            self.user_ir.ir_id,
            self.user_ir.chat_name,
            new_content,
            mentioned_ir_ids=mentioned_ir_ids,
        ))

    async def _handle_delete_message(self, payload):
        message_id = payload.get("message_id")
        if not message_id:
            await self._send_error("message_id is required")
            return

        deleted, error = await self._delete_message(message_id, self.user_ir.ir_id)
        if error:
            await self._send_error(error)
            return

        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "message_deleted",
                "message_id": message_id,
            },
        )

    async def _handle_rename_room(self, payload):
        room_name = (payload.get("room_name") or "").strip()
        if not room_name:
            await self._send_error("room_name is required")
            return

        updated_room = await self._rename_room(self.room_id, room_name)
        if not updated_room:
            await self._send_error("Unable to update room")
            return

        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "room_updated",
                "room": updated_room,
            },
        )

    async def _handle_add_members(self, payload):
        member_ir_ids = payload.get("member_ir_ids") or []
        show_history = bool(payload.get("show_history", True))
        if not isinstance(member_ir_ids, list) or not member_ir_ids:
            await self._send_error("member_ir_ids is required")
            return

        added_members, new_member_count, error = await self._add_members(
            self.room_id, self.user_ir.ir_id, member_ir_ids, show_history
        )
        if error:
            await self._send_error(error)
            return

        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "members_added",
                "added": added_members,
                "new_member_count": new_member_count,
                "by_ir_id": self.user_ir.ir_id,
            },
        )

    async def _handle_remove_members(self, payload):
        member_ir_ids = payload.get("member_ir_ids") or []
        if not isinstance(member_ir_ids, list) or not member_ir_ids:
            await self._send_error("member_ir_ids is required")
            return

        removed_ids, new_member_count, error = await self._remove_members(
            self.room_id, self.user_ir.ir_id, member_ir_ids
        )
        if error:
            await self._send_error(error)
            return

        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "members_removed",
                "removed": removed_ids,
                "new_member_count": new_member_count,
                "by_ir_id": self.user_ir.ir_id,
            },
        )

    async def _handle_pin_message(self, payload):
        message_id = payload.get("message_id")
        if not message_id:
            await self._send_error("message_id is required")
            return

        pinned, error = await self._pin_message(self.room_id, message_id)
        if error:
            await self._send_error(error)
            return

        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "message_pinned",
                "pinned_message": pinned,
                "by_ir_id": self.user_ir.ir_id,
            },
        )

    async def _handle_unpin_message(self, payload):
        await self._unpin_message(self.room_id)
        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "message_unpinned",
                "by_ir_id": self.user_ir.ir_id,
            },
        )

    # ── Channel layer event handlers (broadcast → client) ──────────────

    async def message_created(self, event):
        await self.send(text_data=json.dumps({"type": "message_created", "message": event["message"]}, default=str))

    async def typing_updated(self, event):
        await self.send(text_data=json.dumps({
            "type": "typing_updated",
            "ir_id": event["ir_id"],
            "ir_name": event["ir_name"],
            "is_typing": event["is_typing"],
        }))

    async def read_receipts_updated(self, event):
        await self.send(text_data=json.dumps({
            "type": "read_receipts_updated",
            "message_ids": event["message_ids"],
            "reader_ir_id": event["reader_ir_id"],
            "reader_name": event["reader_name"],
        }))

    async def message_edited(self, event):
        await self.send(text_data=json.dumps({"type": "message_edited", "message": event["message"]}, default=str))

    async def message_deleted(self, event):
        await self.send(text_data=json.dumps({"type": "message_deleted", "message_id": event["message_id"]}))

    async def room_updated(self, event):
        await self.send(text_data=json.dumps({"type": "room_updated", "room": event["room"]}, default=str))

    async def members_added(self, event):
        await self.send(text_data=json.dumps({
            "type": "members_added",
            "added": event["added"],
            "new_member_count": event.get("new_member_count"),
            "by_ir_id": event["by_ir_id"],
        }))

    async def members_removed(self, event):
        await self.send(text_data=json.dumps({
            "type": "members_removed",
            "removed": event["removed"],
            "new_member_count": event.get("new_member_count"),
            "by_ir_id": event["by_ir_id"],
        }))

    async def message_pinned(self, event):
        await self.send(text_data=json.dumps({
            "type": "message_pinned",
            "pinned_message": event["pinned_message"],
            "by_ir_id": event["by_ir_id"],
        }))

    async def message_unpinned(self, event):
        await self.send(text_data=json.dumps({
            "type": "message_unpinned",
            "by_ir_id": event["by_ir_id"],
        }))

    async def reaction_updated(self, event):
        await self.send(text_data=json.dumps({
            "type": "reaction_updated",
            "message_id": event["message_id"],
            "reactions": event["reactions"],
        }))

    async def _handle_buzz(self):
        """Broadcast an ephemeral buzz event to all room members — no DB record created."""
        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "buzz_received",
                "sender_ir_id": self.user_ir.ir_id,
                "sender_name": self.user_ir.ir_name,
            },
        )

    async def buzz_received(self, event):
        await self.send(text_data=json.dumps({
            "type": "buzz_received",
            "sender_ir_id": event["sender_ir_id"],
            "sender_name": event["sender_name"],
        }))

    async def _send_error(self, detail):
        await self.send(text_data=json.dumps({"type": "error", "detail": detail}))

    # ── DB helpers ──────────────────────────────────────────────────────

    @database_sync_to_async
    def _get_ir(self, ir_id):
        try:
            return Ir.objects.get(ir_id=ir_id, status=True)
        except Ir.DoesNotExist:
            return None

    @database_sync_to_async
    def _update_last_seen(self, ir_id):
        Ir.objects.filter(ir_id=ir_id).update(last_seen=timezone.now())

    @database_sync_to_async
    def _get_room_if_member(self, room_id, ir_id):
        # Single query: confirms membership and fetches the room in one round
        # trip, and lets the caller cache the room object for the connection's
        # lifetime instead of re-fetching it on every message.
        return ChatRoom.objects.filter(id=room_id, memberships__ir_id=ir_id).first()

    @database_sync_to_async
    def _validate_mentioned_ir_ids(self, room_id, sender_ir_id, candidate_ir_ids):
        from core.chat.notify import validate_mentioned_ir_ids
        return validate_mentioned_ir_ids(room_id, sender_ir_id, candidate_ir_ids)

    @database_sync_to_async
    def _create_message(self, room, sender, content, reply_to_id=None, mentioned_ir_ids=None):
        # room/sender are passed in from the connection-scoped cache
        # (self.chat_room / self.user_ir) rather than re-fetched here — they
        # were already validated in connect() and don't change for the life
        # of the socket, so re-querying them on every message was pure waste.
        reply_to = None
        reply_to_data = None
        if reply_to_id:
            try:
                reply_to = ChatMessage.objects.select_related("sender").get(id=reply_to_id, room_id=room.id)
                reply_to_data = {
                    "id": reply_to.id,
                    "sender_name": reply_to.sender.ir_name,
                    "content": reply_to.content if not reply_to.is_deleted else "",
                    "is_deleted": reply_to.is_deleted,
                }
            except ChatMessage.DoesNotExist:
                pass

        message = ChatMessage.objects.create(
            room=room,
            sender=sender,
            content=content,
            reply_to=reply_to,
        )
        if mentioned_ir_ids:
            message.mentioned.set(mentioned_ir_ids)
        room.save(update_fields=["updated_at"])
        # Use isoformat() strings — channels_redis cannot serialize datetime objects
        return {
            "id": message.id,
            "room_id": message.room_id,
            "sender_ir_id": sender.ir_id,
            "sender_name": sender.chat_name,
            "message_type": message.message_type,
            "content": message.content,
            "attachment_url": message.attachment_url,
            "attachment_name": message.attachment_name,
            "attachment_size": message.attachment_size,
            "attachment_duration": message.attachment_duration,
            "created_at": message.created_at.isoformat(),
            "read_count": 0,
            "reply_to": reply_to_data,
            "is_deleted": False,
            "edited_at": None,
            "reactions": {},
        }

    @database_sync_to_async
    def _mark_read(self, room_id, reader_ir_id, message_ids):
        messages = list(ChatMessage.objects.filter(room_id=room_id, id__in=message_ids))
        reader = Ir.objects.get(ir_id=reader_ir_id)
        receipts = [ChatMessageReceipt(message=message, reader=reader) for message in messages]
        ChatMessageReceipt.objects.bulk_create(receipts, ignore_conflicts=True)
        invalidate_chat_rooms_cache([reader_ir_id])
        return [message.id for message in messages]

    @database_sync_to_async
    def _edit_message(self, message_id, sender_ir_id, new_content, mentioned_ir_ids=None):
        try:
            message = ChatMessage.objects.select_related("sender").get(id=message_id)
        except ChatMessage.DoesNotExist:
            return None, "Message not found"

        if message.sender.ir_id != sender_ir_id:
            return None, "Not authorized to edit this message"

        if message.is_deleted:
            return None, "Cannot edit a deleted message"

        message.content = new_content
        message.edited_at = timezone.now()
        message.save(update_fields=["content", "edited_at"])
        message.mentioned.set(mentioned_ir_ids or [])
        invalidate_chat_rooms_cache(_room_member_ir_ids(message.room_id))

        return {
            "id": message.id,
            "content": message.content,
            "edited_at": message.edited_at.isoformat(),
            "reactions": {},
        }, None

    @database_sync_to_async
    def _delete_message(self, message_id, sender_ir_id):
        try:
            message = ChatMessage.objects.select_related("sender").get(id=message_id)
        except ChatMessage.DoesNotExist:
            return None, "Message not found"

        if message.sender.ir_id != sender_ir_id:
            return None, "Not authorized to delete this message"

        message.is_deleted = True
        message.content = ""
        message.save(update_fields=["is_deleted", "content"])
        invalidate_chat_rooms_cache(_room_member_ir_ids(message.room_id))

        return message.id, None

    @database_sync_to_async
    def _rename_room(self, room_id, room_name):
        room = ChatRoom.objects.get(id=room_id)
        room.room_name = room_name
        room.save(update_fields=["room_name", "updated_at"])
        invalidate_chat_rooms_cache(_room_member_ir_ids(room_id))
        return {
            "id": room.id,
            "room_name": room.room_name,
            "updated_at": room.updated_at.isoformat(),
        }

    @database_sync_to_async
    def _add_members(self, room_id, requester_ir_id, member_ir_ids, show_history=True):
        from core.models import ChatRoomType
        room = ChatRoom.objects.get(id=room_id)
        requester = Ir.objects.get(ir_id=requester_ir_id)

        if not ChatRoomMember.objects.filter(room=room, ir=requester).exists():
            return [], 0, "Not authorized for this room"

        is_elevated = requester.ir_access_level in (AccessLevel.ADMIN, AccessLevel.CTC, AccessLevel.LDC, AccessLevel.LS)
        if room.room_type == ChatRoomType.GROUP and room.created_by_id != requester_ir_id and not is_elevated:
            return [], 0, "Only the group owner or an LDC/CTC/Admin/LS can add members"

        candidates = list(Ir.objects.filter(ir_id__in=member_ir_ids, status=True))
        found_ids = {candidate.ir_id for candidate in candidates}
        missing = sorted(list(set(member_ir_ids) - found_ids))
        if missing:
            return [], 0, f"Invalid member IDs: {', '.join(missing)}"

        for candidate in candidates:
            if not requester.can_view_ir(candidate):
                return [], 0, f"Not authorized to add {candidate.ir_id}"

        existing = set(ChatRoomMember.objects.filter(room=room, ir_id__in=member_ir_ids).values_list("ir_id", flat=True))
        to_add = [candidate for candidate in candidates if candidate.ir_id not in existing]
        ChatRoomMember.objects.bulk_create(
            [
                ChatRoomMember(room=room, ir=candidate, added_by=requester, hide_history_before_join=not show_history)
                for candidate in to_add
            ]
        )
        room.save(update_fields=["updated_at"])
        invalidate_chat_rooms_cache(_room_member_ir_ids(room_id))

        added_members = [
            {"ir_id": candidate.ir_id, "ir_name": candidate.chat_name, "avatar_url": candidate.avatar_url}
            for candidate in to_add
        ]
        new_member_count = ChatRoomMember.objects.filter(room=room).count()
        return added_members, new_member_count, None

    @database_sync_to_async
    def _pin_message(self, room_id, message_id):
        try:
            message = ChatMessage.objects.select_related("sender").get(id=message_id, room_id=room_id)
        except ChatMessage.DoesNotExist:
            return None, "Message not found"
        if message.is_deleted:
            return None, "Cannot pin a deleted message"
        room = ChatRoom.objects.get(id=room_id)
        room.pinned_message = message
        room.save(update_fields=["pinned_message"])
        invalidate_chat_rooms_cache(_room_member_ir_ids(room_id))
        return {
            "id": message.id,
            "sender_name": message.sender.ir_name,
            "content": message.content,
            "message_type": message.message_type,
            "attachment_url": message.attachment_url,
            "attachment_name": message.attachment_name,
        }, None

    @database_sync_to_async
    def _unpin_message(self, room_id):
        ChatRoom.objects.filter(id=room_id).update(pinned_message=None)
        invalidate_chat_rooms_cache(_room_member_ir_ids(room_id))

    @database_sync_to_async
    def _remove_members(self, room_id, requester_ir_id, member_ir_ids):
        from core.models import ChatRoomType
        room = ChatRoom.objects.get(id=room_id)
        requester = Ir.objects.get(ir_id=requester_ir_id)

        if not ChatRoomMember.objects.filter(room=room, ir=requester).exists():
            return [], 0, "Not authorized for this room"

        # Same rule as the HTTP path (ChatRoomMembersRemove): owner, or an
        # Admin who is in the group. Both paths mutate the same table, so a
        # rule that lives in only one of them is not a rule.
        if not _can_moderate_room(room, requester):
            return [], 0, "Only the group owner or an Admin can remove members"

        current_ids = set(ChatRoomMember.objects.filter(room=room).values_list("ir_id", flat=True))
        removable_ids = set(member_ir_ids) & current_ids

        if len(current_ids - removable_ids) <= 0:
            return [], 0, "Room must have at least one member"

        ChatRoomMember.objects.filter(room=room, ir_id__in=list(removable_ids)).delete()
        room.save(update_fields=["updated_at"])
        invalidate_chat_rooms_cache(current_ids)
        new_member_count = len(current_ids - removable_ids)
        return sorted(list(removable_ids)), new_member_count, None

    @database_sync_to_async
    def _get_room_member_ir_ids(self, room_id, exclude_ir_id=None):
        qs = ChatRoomMember.objects.filter(room_id=room_id).values_list("ir_id", flat=True)
        if exclude_ir_id:
            qs = qs.exclude(ir_id=exclude_ir_id)
        return list(qs)

    @database_sync_to_async
    def _invalidate_room_cache_for_room(self, room_id):
        invalidate_chat_rooms_cache(_room_member_ir_ids(room_id))

    @database_sync_to_async
    def _gather_notification_targets(self, room_id, sender_ir_id, mentioned_ir_ids=None):
        """
        DB-only half of push notification prep: title, per-member token list,
        a token -> ir_id map (for pruning dead tokens after send), and the
        subset of tokens belonging to @mentioned members. Kept small and fast
        since it runs on Django's shared thread-sensitive executor; the slow
        network send itself runs elsewhere (see _notify_room_members). Shared
        with the REST attachment-message path in core/views/chat.py via
        core/chat/notify.py.
        """
        from core.chat.notify import gather_notification_targets
        return gather_notification_targets(room_id, sender_ir_id, mentioned_ir_ids)

    @database_sync_to_async
    def _prune_dead_tokens(self, bad_tokens_by_ir_id):
        """Remove permanently-invalid FCM tokens from the owning Ir rows."""
        from core.chat.notify import prune_dead_tokens
        prune_dead_tokens(bad_tokens_by_ir_id)

    async def _notify_room_members(self, room_id, sender_ir_id, sender_name, content, mentioned_ir_ids=None):
        """
        Send FCM push notifications to room members who are not actively
        watching this room. Called fire-and-forget (see _handle_send_message /
        _handle_edit_message) so its latency never blocks the WS connection.
        The actual network call runs on a dedicated executor (_fcm_executor),
        not Django's shared thread-sensitive one, so a slow push can't stall
        unrelated DB-touching work elsewhere in the process. @Mentioned
        members bypass mute and get a distinct "mentioned you" title — see
        gather_notification_targets/build_notification_batches in notify.py.
        """
        import logging
        logger = logging.getLogger(__name__)
        try:
            from core.chat.notify import build_notification_batches
            from core.utils.firebase_messaging import send_multicast, is_dead_token_error

            room_type, room_name, tokens, token_owner, mentioned_tokens = await self._gather_notification_targets(
                room_id, sender_ir_id, mentioned_ir_ids
            )
            if not tokens:
                return

            # Truncate message preview to 100 chars
            preview = content if len(content) <= 100 else content[:100] + "…"
            batches = build_notification_batches(tokens, mentioned_tokens, sender_name, room_type, room_name)

            loop = asyncio.get_event_loop()
            bad_tokens_by_ir_id = {}
            for batch_tokens, title in batches:
                result = await loop.run_in_executor(
                    _fcm_executor,
                    functools.partial(
                        send_multicast,
                        fcm_tokens=batch_tokens,
                        title=title,
                        body=preview,
                        data={
                            "notification_type": "chat_message",
                            "room_id": str(room_id),
                        },
                    ),
                )

                if result.get("failure"):
                    for detail in result.get("failure_details", []):
                        if is_dead_token_error(detail.get("code"), detail.get("message"), detail.get("exception_type")):
                            token = detail.get("token")
                            owner_ir_id = token_owner.get(token)
                            if owner_ir_id:
                                bad_tokens_by_ir_id.setdefault(owner_ir_id, set()).add(token)

            if bad_tokens_by_ir_id:
                logger.info(
                    "Pruning %d dead FCM token(s) across %d member(s) after chat push to room %s",
                    sum(len(v) for v in bad_tokens_by_ir_id.values()),
                    len(bad_tokens_by_ir_id),
                    room_id,
                )
                await self._prune_dead_tokens(bad_tokens_by_ir_id)
        except Exception:
            logger.exception("Failed to send chat FCM notifications for room %s", room_id)


class UserInboxConsumer(AsyncWebsocketConsumer):
    """
    Per-user always-on WebSocket that delivers cross-room inbox events
    (new message previews, unread increments) so the room list updates
    in real time without the user needing to open each room.
    """

    async def connect(self):
        query_string = self.scope.get("query_string", b"").decode()
        params = parse_qs(query_string)
        ir_id = (params.get("ir_id") or [None])[0]

        if not ir_id:
            await self.close(code=4001)
            return

        user = await self._get_ir(ir_id)
        if not user:
            await self.close(code=4002)
            return

        self.ir_id = ir_id
        self.group_name = f"user_inbox_{ir_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        # Inbox socket is receive-only for real events — the client only ever
        # sends heartbeat pings, which need a reply so the frontend can detect
        # a half-open connection (see chatService.js sendInboxPing) and force
        # a reconnect instead of sitting on a dead socket indefinitely.
        if not text_data:
            return
        try:
            payload = json.loads(text_data)
        except (TypeError, ValueError):
            return
        if payload.get("type") == "ping":
            await self.send(text_data=json.dumps({"type": "pong"}))

    async def room_preview_updated(self, event):
        await self.send(text_data=json.dumps({
            "type": "room_preview_updated",
            "room_id": event["room_id"],
            "last_message_preview": event["last_message_preview"],
            "last_message_at": event["last_message_at"],
            "sender_ir_id": event["sender_ir_id"],
        }))

    async def own_read_state_updated(self, event):
        await self.send(text_data=json.dumps({
            "type": "own_read_state_updated",
            "room_id": event["room_id"],
        }))

    @database_sync_to_async
    def _get_ir(self, ir_id):
        try:
            return Ir.objects.get(ir_id=ir_id, status=True)
        except Ir.DoesNotExist:
            return None
