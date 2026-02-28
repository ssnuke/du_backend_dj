import json
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from core.models import ChatMessage, ChatMessageReceipt, ChatRoom, ChatRoomMember, Ir


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
        elif event_type == "rename_room":
            await self._handle_rename_room(payload)
        elif event_type == "add_members":
            await self._handle_add_members(payload)
        elif event_type == "remove_members":
            await self._handle_remove_members(payload)
        else:
            await self._send_error("Unsupported event type")

    async def _handle_send_message(self, payload):
        content = (payload.get("content") or "").strip()
        if not content:
            await self._send_error("content is required")
            return

        message = await self._create_message(self.room_id, self.user_ir.ir_id, content)

        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "message_created",
                "message": message,
            },
        )

    async def _broadcast_typing(self, is_typing):
        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "typing_updated",
                "ir_id": self.user_ir.ir_id,
                "ir_name": self.user_ir.ir_name,
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
        if not isinstance(member_ir_ids, list) or not member_ir_ids:
            await self._send_error("member_ir_ids is required")
            return

        added_ids, error = await self._add_members(self.room_id, self.user_ir.ir_id, member_ir_ids)
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

    async def _send_error(self, detail):
        await self.send(text_data=json.dumps({"type": "error", "detail": detail}))

    @database_sync_to_async
    def _get_ir(self, ir_id):
        try:
            return Ir.objects.get(ir_id=ir_id, status=True)
        except Ir.DoesNotExist:
            return None

    @database_sync_to_async
    def _is_room_member(self, room_id, ir_id):
        return ChatRoomMember.objects.filter(room_id=room_id, ir_id=ir_id).exists()

    @database_sync_to_async
    def _create_message(self, room_id, sender_ir_id, content):
        room = ChatRoom.objects.get(id=room_id)
        sender = Ir.objects.get(ir_id=sender_ir_id)
        message = ChatMessage.objects.create(room=room, sender=sender, content=content)
        room.save(update_fields=["updated_at"])
        return {
            "id": message.id,
            "room_id": message.room_id,
            "sender_ir_id": sender.ir_id,
            "sender_name": sender.ir_name,
            "message_type": message.message_type,
            "content": message.content,
            "created_at": message.created_at,
            "read_count": 0,
        }

    @database_sync_to_async
    def _mark_read(self, room_id, reader_ir_id, message_ids):
        messages = list(ChatMessage.objects.filter(room_id=room_id, id__in=message_ids))
        reader = Ir.objects.get(ir_id=reader_ir_id)
        receipts = [ChatMessageReceipt(message=message, reader=reader) for message in messages]
        ChatMessageReceipt.objects.bulk_create(receipts, ignore_conflicts=True)
        return [message.id for message in messages]

    @database_sync_to_async
    def _rename_room(self, room_id, room_name):
        room = ChatRoom.objects.get(id=room_id)
        room.room_name = room_name
        room.save(update_fields=["room_name", "updated_at"])
        return {
            "id": room.id,
            "room_name": room.room_name,
            "updated_at": room.updated_at,
        }

    @database_sync_to_async
    def _add_members(self, room_id, requester_ir_id, member_ir_ids):
        room = ChatRoom.objects.get(id=room_id)
        requester = Ir.objects.get(ir_id=requester_ir_id)

        if not ChatRoomMember.objects.filter(room=room, ir=requester).exists():
            return [], "Not authorized for this room"

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
            [ChatRoomMember(room=room, ir=candidate, added_by=requester) for candidate in to_add]
        )
        room.save(update_fields=["updated_at"])

        return [member.ir_id for member in to_add], None

    @database_sync_to_async
    def _remove_members(self, room_id, requester_ir_id, member_ir_ids):
        room = ChatRoom.objects.get(id=room_id)
        requester = Ir.objects.get(ir_id=requester_ir_id)

        if not ChatRoomMember.objects.filter(room=room, ir=requester).exists():
            return [], "Not authorized for this room"

        current_ids = set(ChatRoomMember.objects.filter(room=room).values_list("ir_id", flat=True))
        removable_ids = set(member_ir_ids) & current_ids

        if len(current_ids - removable_ids) <= 0:
            return [], "Room must have at least one member"

        ChatRoomMember.objects.filter(room=room, ir_id__in=list(removable_ids)).delete()
        room.save(update_fields=["updated_at"])
        return sorted(list(removable_ids)), None
