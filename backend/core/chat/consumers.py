import json
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils import timezone

from core.models import AccessLevel, ChatMessage, ChatMessageReaction, ChatMessageReceipt, ChatRoom, ChatRoomMember, Ir


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

        is_member = await self._is_room_member(self.room_id, self.user_ir.ir_id)
        if not is_member:
            await self.close(code=4003)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'user_ir') and self.user_ir:
            await self._update_last_seen(self.user_ir.ir_id)
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

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
            await self.send(text_data=json.dumps({"type": "pong"}))
        else:
            await self._send_error("Unsupported event type")

    async def _handle_send_message(self, payload):
        content = (payload.get("content") or "").strip()
        if not content:
            await self._send_error("content is required")
            return

        reply_to_id = payload.get("reply_to_id")

        message = await self._create_message(self.room_id, self.user_ir.ir_id, content, reply_to_id)

        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "message_created",
                "message": message,
            },
        )

        # Push a lightweight room-preview update to every other member's inbox
        # channel so their room list refreshes in real time even when they have
        # a different room open (or no room open at all).
        member_ir_ids = await self._get_room_member_ir_ids(self.room_id, exclude_ir_id=self.user_ir.ir_id)
        for member_ir_id in member_ir_ids:
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

        # Fire FCM push notifications to members who are not in this WS session
        await self._notify_room_members(
            self.room_id,
            self.user_ir.ir_id,
            self.user_ir.chat_name,
            content,
        )

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

    async def _handle_edit_message(self, payload):
        message_id = payload.get("message_id")
        new_content = (payload.get("content") or "").strip()
        if not message_id or not new_content:
            await self._send_error("message_id and content are required")
            return

        updated, error = await self._edit_message(message_id, self.user_ir.ir_id, new_content)
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

        added_ids, error = await self._add_members(self.room_id, self.user_ir.ir_id, member_ir_ids, show_history)
        if error:
            await self._send_error(error)
            return

        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "members_added",
                "added": added_ids,
                "by_ir_id": self.user_ir.ir_id,
            },
        )

    async def _handle_remove_members(self, payload):
        member_ir_ids = payload.get("member_ir_ids") or []
        if not isinstance(member_ir_ids, list) or not member_ir_ids:
            await self._send_error("member_ir_ids is required")
            return

        removed_ids, error = await self._remove_members(self.room_id, self.user_ir.ir_id, member_ir_ids)
        if error:
            await self._send_error(error)
            return

        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "members_removed",
                "removed": removed_ids,
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
            "by_ir_id": event["by_ir_id"],
        }))

    async def members_removed(self, event):
        await self.send(text_data=json.dumps({
            "type": "members_removed",
            "removed": event["removed"],
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
    def _is_room_member(self, room_id, ir_id):
        return ChatRoomMember.objects.filter(room_id=room_id, ir_id=ir_id).exists()

    @database_sync_to_async
    def _create_message(self, room_id, sender_ir_id, content, reply_to_id=None):
        room = ChatRoom.objects.get(id=room_id)
        sender = Ir.objects.get(ir_id=sender_ir_id)

        reply_to = None
        reply_to_data = None
        if reply_to_id:
            try:
                reply_to = ChatMessage.objects.select_related("sender").get(id=reply_to_id, room_id=room_id)
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
        return [message.id for message in messages]

    @database_sync_to_async
    def _edit_message(self, message_id, sender_ir_id, new_content):
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

        return message.id, None

    @database_sync_to_async
    def _rename_room(self, room_id, room_name):
        room = ChatRoom.objects.get(id=room_id)
        room.room_name = room_name
        room.save(update_fields=["room_name", "updated_at"])
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
            return [], "Not authorized for this room"

        is_elevated = requester.ir_access_level in (AccessLevel.ADMIN, AccessLevel.CTC, AccessLevel.LDC, AccessLevel.LS)
        if room.room_type == ChatRoomType.GROUP and room.created_by_id != requester_ir_id and not is_elevated:
            return [], "Only the group owner or an LDC/CTC/Admin/LS can add members"

        candidates = list(Ir.objects.filter(ir_id__in=member_ir_ids, status=True))
        found_ids = {candidate.ir_id for candidate in candidates}
        missing = sorted(list(set(member_ir_ids) - found_ids))
        if missing:
            return [], f"Invalid member IDs: {', '.join(missing)}"

        for candidate in candidates:
            if not requester.can_view_ir(candidate):
                return [], f"Not authorized to add {candidate.ir_id}"

        existing = set(ChatRoomMember.objects.filter(room=room, ir_id__in=member_ir_ids).values_list("ir_id", flat=True))
        to_add = [candidate for candidate in candidates if candidate.ir_id not in existing]
        ChatRoomMember.objects.bulk_create(
            [
                ChatRoomMember(room=room, ir=candidate, added_by=requester, hide_history_before_join=not show_history)
                for candidate in to_add
            ]
        )
        room.save(update_fields=["updated_at"])

        return [member.ir_id for member in to_add], None

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

    @database_sync_to_async
    def _remove_members(self, room_id, requester_ir_id, member_ir_ids):
        from core.models import ChatRoomType
        room = ChatRoom.objects.get(id=room_id)
        requester = Ir.objects.get(ir_id=requester_ir_id)

        if not ChatRoomMember.objects.filter(room=room, ir=requester).exists():
            return [], "Not authorized for this room"

        if room.room_type == ChatRoomType.GROUP and room.created_by_id != requester_ir_id:
            return [], "Only the group owner can remove members"

        current_ids = set(ChatRoomMember.objects.filter(room=room).values_list("ir_id", flat=True))
        removable_ids = set(member_ir_ids) & current_ids

        if len(current_ids - removable_ids) <= 0:
            return [], "Room must have at least one member"

        ChatRoomMember.objects.filter(room=room, ir_id__in=list(removable_ids)).delete()
        room.save(update_fields=["updated_at"])
        return sorted(list(removable_ids)), None

    @database_sync_to_async
    def _get_room_member_ir_ids(self, room_id, exclude_ir_id=None):
        qs = ChatRoomMember.objects.filter(room_id=room_id).values_list("ir_id", flat=True)
        if exclude_ir_id:
            qs = qs.exclude(ir_id=exclude_ir_id)
        return list(qs)

    @database_sync_to_async
    def _notify_room_members(self, room_id, sender_ir_id, sender_name, content):
        """Send FCM push notifications to room members who are not actively watching this room."""
        import logging
        logger = logging.getLogger(__name__)
        try:
            from core.utils.firebase_messaging import send_multicast

            room = ChatRoom.objects.get(id=room_id)

            # Notification title: just sender name for DMs, "Name · Room" for groups
            if room.room_type == "direct":
                title = sender_name
            else:
                title = f"{sender_name} · {room.room_name}"

            # Collect FCM tokens for all members except the sender, skipping muted members
            members = ChatRoomMember.objects.filter(
                room_id=room_id
            ).exclude(ir__ir_id=sender_ir_id).select_related("ir")

            now = timezone.now()
            tokens = []
            for member in members:
                # Skip muted members (muted_until=None means muted forever)
                if member.is_muted:
                    if member.muted_until is None or member.muted_until > now:
                        continue
                    # Mute expired — clear it
                    member.is_muted = False
                    member.muted_until = None
                    member.save(update_fields=["is_muted", "muted_until"])

                ir = member.ir
                if ir.fcm_tokens and isinstance(ir.fcm_tokens, list):
                    valid = [t for t in ir.fcm_tokens if t and isinstance(t, str) and len(t) > 10]
                    tokens.extend(valid)

            if not tokens:
                return

            # Deduplicate while preserving order
            tokens = list(dict.fromkeys(tokens))

            # Truncate message preview to 100 chars
            preview = content if len(content) <= 100 else content[:100] + "…"

            send_multicast(
                fcm_tokens=tokens,
                title=title,
                body=preview,
                data={
                    "notification_type": "chat_message",
                    "room_id": str(room_id),
                },
            )
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
        # Inbox socket is receive-only for the server — ignore client messages
        pass

    async def room_preview_updated(self, event):
        await self.send(text_data=json.dumps({
            "type": "room_preview_updated",
            "room_id": event["room_id"],
            "last_message_preview": event["last_message_preview"],
            "last_message_at": event["last_message_at"],
            "sender_ir_id": event["sender_ir_id"],
        }))

    @database_sync_to_async
    def _get_ir(self, ir_id):
        try:
            return Ir.objects.get(ir_id=ir_id, status=True)
        except Ir.DoesNotExist:
            return None
