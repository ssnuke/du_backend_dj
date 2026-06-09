import mimetypes
import os
import uuid

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import (
    AccessLevel,
    ChatMessage,
    ChatMessageReaction,
    ChatMessageReceipt,
    ChatRoom,
    ChatRoomMember,
    ChatRoomType,
    ChatMessageType,
    Ir,
)


PAGE_SIZE_DEFAULT = 50


def _get_ir(ir_id):
    if not ir_id:
        return None
    try:
        return Ir.objects.get(ir_id=ir_id)
    except Ir.DoesNotExist:
        return None


def _can_create_group(ir):
    """Only LS and above can create group rooms."""
    return ir.ir_access_level <= AccessLevel.LS


def _is_room_member(room, ir):
    return ChatRoomMember.objects.filter(room=room, ir=ir).exists()


def _serialize_room(room, requester=None):
    last_message = room.messages.order_by("-id").first()
    unread_count = 0
    if requester:
        unread_count = room.messages.exclude(receipts__reader=requester).exclude(sender=requester).count()

    other_member = None
    if room.room_type == ChatRoomType.DIRECT and requester:
        other = ChatRoomMember.objects.filter(room=room).exclude(ir=requester).select_related("ir").first()
        if other:
            other_member = {
                "ir_id": other.ir.ir_id,
                "ir_name": other.ir.display_name or other.ir.ir_name,
            }

    pinned_msg_data = None
    if room.pinned_message_id:
        try:
            pm = room.pinned_message
            pinned_msg_data = {
                "id": pm.id,
                "sender_name": pm.sender.ir_name,
                "content": pm.content,
                "message_type": pm.message_type,
                "attachment_url": pm.attachment_url,
                "attachment_name": pm.attachment_name,
            }
        except Exception:
            pass

    # Last-message preview: for non-text types show a label
    preview = None
    if last_message:
        if last_message.is_deleted:
            preview = "🚫 Message deleted"
        elif last_message.message_type == ChatMessageType.IMAGE:
            preview = "📷 Photo"
        elif last_message.message_type == ChatMessageType.VIDEO:
            preview = "🎥 Video"
        elif last_message.message_type == ChatMessageType.FILE:
            preview = f"📎 {last_message.attachment_name or 'File'}"
        elif last_message.message_type == ChatMessageType.VOICE:
            preview = "🎤 Voice message"
        else:
            preview = last_message.content[:80]

    return {
        "id": room.id,
        "room_type": room.room_type,
        "room_name": room.room_name,
        "image_url": getattr(room, "image_url", None),
        "category": room.category,
        "created_by_ir_id": room.created_by.ir_id if room.created_by else None,
        "created_at": room.created_at,
        "updated_at": room.updated_at,
        "is_pinned": room.is_pinned,
        "pinned_at": room.pinned_at,
        "pinned_message": pinned_msg_data,
        "member_count": room.memberships.count(),
        "last_message_preview": preview,
        "last_message_at": (last_message.created_at if last_message else None),
        "unread_count": unread_count,
        "other_member": other_member,
    }


def _serialize_message(message):
    reply_to_data = None
    if message.reply_to_id:
        try:
            rt = message.reply_to
            reply_to_data = {
                "id": rt.id,
                "sender_name": rt.sender.ir_name,
                "content": rt.content if not rt.is_deleted else "",
                "is_deleted": rt.is_deleted,
            }
        except Exception:
            pass

    # Support both annotated read_count and live count
    read_count = getattr(message, 'read_count', None)
    if read_count is None:
        read_count = message.receipts.count()

    reactions = {}
    for r in ChatMessageReaction.objects.filter(message_id=message.id).select_related("ir"):
        reactions.setdefault(r.emoji, []).append({
            "ir_id": r.ir.ir_id,
            "ir_name": r.ir.chat_name,
        })

    return {
        "id": message.id,
        "room_id": message.room_id,
        "sender_ir_id": message.sender.ir_id,
        "sender_name": message.sender.chat_name,
        "message_type": message.message_type,
        "content": message.content,
        "attachment_url": message.attachment_url,
        "attachment_name": message.attachment_name,
        "attachment_size": message.attachment_size,
        "attachment_duration": message.attachment_duration,
        # Use isoformat strings — channel layer (Redis) cannot serialize datetime objects
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "read_count": read_count,
        "reply_to": reply_to_data,
        "is_deleted": message.is_deleted,
        "edited_at": message.edited_at.isoformat() if message.edited_at else None,
        "reactions": reactions,
    }


class ChatRoomListCreate(APIView):
    def get(self, request):
        requester_ir_id = request.GET.get("requester_ir_id")
        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        room_ids = ChatRoomMember.objects.filter(ir=requester).values_list("room_id", flat=True)
        rooms = ChatRoom.objects.filter(id__in=room_ids).prefetch_related("messages", "memberships")
        data = [_serialize_room(room, requester=requester) for room in rooms]
        return Response({"rooms": data})

    def post(self, request):
        requester_ir_id = request.data.get("requester_ir_id")
        room_name = (request.data.get("room_name") or "").strip()
        room_type = request.data.get("room_type", ChatRoomType.GROUP)
        initial_member_ir_ids = request.data.get("initial_member_ir_ids") or []

        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        if room_type not in [ChatRoomType.DIRECT, ChatRoomType.GROUP]:
            return Response({"detail": "Invalid room_type"}, status=status.HTTP_400_BAD_REQUEST)

        if not room_name:
            return Response({"detail": "room_name is required"}, status=status.HTTP_400_BAD_REQUEST)

        if room_type == ChatRoomType.GROUP and not _can_create_group(requester):
            return Response({"detail": "Not authorized to create group rooms. LS and above only."}, status=status.HTTP_403_FORBIDDEN)

        member_ids = set(initial_member_ir_ids)
        member_ids.add(requester.ir_id)

        if room_type == ChatRoomType.DIRECT and len(member_ids) != 2:
            return Response({"detail": "Direct room must have exactly 2 members"}, status=status.HTTP_400_BAD_REQUEST)

        members = list(Ir.objects.filter(ir_id__in=member_ids, status=True))
        found_ids = {member.ir_id for member in members}
        missing = sorted(list(member_ids - found_ids))
        if missing:
            return Response({"detail": f"Invalid member IDs: {', '.join(missing)}"}, status=status.HTTP_400_BAD_REQUEST)

        for member in members:
            if not requester.can_view_ir(member):
                return Response({"detail": f"Not authorized to add {member.ir_id} to room"}, status=status.HTTP_403_FORBIDDEN)

        with transaction.atomic():
            room = ChatRoom.objects.create(
                room_type=room_type,
                room_name=room_name,
                created_by=requester,
            )
            ChatRoomMember.objects.bulk_create([
                ChatRoomMember(room=room, ir=member, added_by=requester)
                for member in members
            ])

        return Response(
            {
                "message": "Room created successfully",
                "room": _serialize_room(room, requester=requester),
            },
            status=status.HTTP_201_CREATED,
        )


class ChatRoomUpdate(APIView):
    def patch(self, request, room_id):
        requester_ir_id = request.data.get("requester_ir_id")
        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        room = get_object_or_404(ChatRoom, id=room_id)
        if not _is_room_member(room, requester):
            return Response({"detail": "Not authorized for this room"}, status=status.HTTP_403_FORBIDDEN)

        room_name = (request.data.get("room_name") or "").strip()
        if not room_name:
            return Response({"detail": "room_name is required"}, status=status.HTTP_400_BAD_REQUEST)

        room.room_name = room_name
        room.save(update_fields=["room_name", "updated_at"])

        return Response({
            "message": "Room name updated",
            "room": {
                "id": room.id,
                "room_name": room.room_name,
                "updated_at": room.updated_at,
            },
        })


class ChatRoomMembers(APIView):
    def get(self, request, room_id):
        requester_ir_id = request.GET.get("requester_ir_id")
        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        room = get_object_or_404(ChatRoom, id=room_id)
        if not _is_room_member(room, requester):
            return Response({"detail": "Not authorized for this room"}, status=status.HTTP_403_FORBIDDEN)

        memberships = ChatRoomMember.objects.filter(room=room).select_related("ir")
        return Response(
            {
                "members": [
                    {
                        "ir_id": membership.ir.ir_id,
                        "ir_name": membership.ir.ir_name,
                        "ir_access_level": membership.ir.ir_access_level,
                        "joined_at": membership.joined_at,
                    }
                    for membership in memberships
                ]
            }
        )


class ChatRoomMembersAdd(APIView):
    def post(self, request, room_id):
        requester_ir_id = request.data.get("requester_ir_id")
        member_ir_ids = request.data.get("member_ir_ids") or []

        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        room = get_object_or_404(ChatRoom, id=room_id)
        if not _is_room_member(room, requester):
            return Response({"detail": "Not authorized for this room"}, status=status.HTTP_403_FORBIDDEN)

        if room.room_type == ChatRoomType.GROUP and room.created_by_id != requester.ir_id:
            return Response({"detail": "Only the group owner can add members"}, status=status.HTTP_403_FORBIDDEN)

        if not member_ir_ids:
            return Response({"detail": "member_ir_ids is required"}, status=status.HTTP_400_BAD_REQUEST)

        members = list(Ir.objects.filter(ir_id__in=member_ir_ids, status=True))
        found_ids = {member.ir_id for member in members}
        missing = sorted(list(set(member_ir_ids) - found_ids))
        if missing:
            return Response({"detail": f"Invalid member IDs: {', '.join(missing)}"}, status=status.HTTP_400_BAD_REQUEST)

        for member in members:
            if not requester.can_view_ir(member):
                return Response({"detail": f"Not authorized to add {member.ir_id} to room"}, status=status.HTTP_403_FORBIDDEN)

        existing_member_ids = set(
            ChatRoomMember.objects.filter(room=room, ir_id__in=member_ir_ids).values_list("ir_id", flat=True)
        )

        to_add = [member for member in members if member.ir_id not in existing_member_ids]
        if to_add:
            ChatRoomMember.objects.bulk_create([
                ChatRoomMember(room=room, ir=member, added_by=requester)
                for member in to_add
            ])
            room.save(update_fields=["updated_at"])

        return Response(
            {
                "message": "Members added",
                "added": [member.ir_id for member in to_add],
                "skipped": sorted(list(existing_member_ids)),
            }
        )


class ChatRoomMembersRemove(APIView):
    def post(self, request, room_id):
        requester_ir_id = request.data.get("requester_ir_id")
        member_ir_ids = request.data.get("member_ir_ids") or []

        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        room = get_object_or_404(ChatRoom, id=room_id)
        if not _is_room_member(room, requester):
            return Response({"detail": "Not authorized for this room"}, status=status.HTTP_403_FORBIDDEN)

        if room.room_type == ChatRoomType.GROUP and room.created_by_id != requester.ir_id:
            return Response({"detail": "Only the group owner can remove members"}, status=status.HTTP_403_FORBIDDEN)

        if not member_ir_ids:
            return Response({"detail": "member_ir_ids is required"}, status=status.HTTP_400_BAD_REQUEST)

        current_member_ids = set(ChatRoomMember.objects.filter(room=room).values_list("ir_id", flat=True))
        removable_ids = set(member_ir_ids) & current_member_ids
        remaining_count = len(current_member_ids - removable_ids)
        if remaining_count <= 0:
            return Response({"detail": "Room must have at least one member"}, status=status.HTTP_400_BAD_REQUEST)

        ChatRoomMember.objects.filter(room=room, ir_id__in=list(removable_ids)).delete()
        room.save(update_fields=["updated_at"])

        return Response(
            {
                "message": "Members removed",
                "removed": sorted(list(removable_ids)),
            }
        )


class ChatRoomMessages(APIView):
    def get(self, request, room_id):
        requester_ir_id = request.GET.get("requester_ir_id")
        before_id = request.GET.get("before_id")
        limit = request.GET.get("limit", PAGE_SIZE_DEFAULT)

        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        room = get_object_or_404(ChatRoom, id=room_id)
        if not _is_room_member(room, requester):
            return Response({"detail": "Not authorized for this room"}, status=status.HTTP_403_FORBIDDEN)

        try:
            limit = int(limit)
            if limit < 1 or limit > 100:
                limit = PAGE_SIZE_DEFAULT
        except (TypeError, ValueError):
            limit = PAGE_SIZE_DEFAULT

        qs = (
            ChatMessage.objects
            .filter(room=room)
            .select_related("sender", "reply_to", "reply_to__sender")
            .prefetch_related("reactions__ir")
            .annotate(read_count=Count("receipts"))
            .order_by("-id")   # newest first; ensures [:limit] gives the most-recent page
        )

        if before_id:
            try:
                before_id_int = int(before_id)
                qs = qs.filter(id__lt=before_id_int)
            except (TypeError, ValueError):
                return Response({"detail": "before_id must be an integer"}, status=status.HTTP_400_BAD_REQUEST)

        messages = list(qs[:limit])
        next_before_id = messages[-1].id if messages else None

        return Response(
            {
                "messages": [_serialize_message(message) for message in messages],
                "next_before_id": next_before_id,
                "has_more": len(messages) == limit,
            }
        )

    def post(self, request, room_id):
        requester_ir_id = request.data.get("requester_ir_id")
        message_type = request.data.get("message_type", "text")
        content = (request.data.get("content") or "").strip()

        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        room = get_object_or_404(ChatRoom, id=room_id)
        if not _is_room_member(room, requester):
            return Response({"detail": "Not authorized for this room"}, status=status.HTTP_403_FORBIDDEN)

        if message_type not in ChatMessageType.values:
            return Response({"detail": "Invalid message_type"}, status=status.HTTP_400_BAD_REQUEST)

        if message_type == ChatMessageType.TEXT and not content:
            return Response({"detail": "content is required for text messages"}, status=status.HTTP_400_BAD_REQUEST)

        attachment_url  = (request.data.get("attachment_url") or "").strip() or None
        attachment_name = (request.data.get("attachment_name") or "").strip() or None
        attachment_size = request.data.get("attachment_size")
        try:
            attachment_size = int(attachment_size) if attachment_size is not None else None
        except (TypeError, ValueError):
            attachment_size = None
        attachment_duration = request.data.get("attachment_duration")
        try:
            attachment_duration = float(attachment_duration) if attachment_duration is not None else None
        except (TypeError, ValueError):
            attachment_duration = None

        message = ChatMessage.objects.create(
            room=room,
            sender=requester,
            message_type=message_type,
            content=content,
            attachment_url=attachment_url,
            attachment_name=attachment_name,
            attachment_size=attachment_size,
            attachment_duration=attachment_duration,
        )
        room.save(update_fields=["updated_at"])

        serialized = _serialize_message(message)

        # Broadcast to all WebSocket connections in the room so every client
        # updates in real-time, not just on refresh.
        try:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f"chat_room_{room_id}",
                {"type": "message_created", "message": serialized},
            )
        except Exception:
            pass  # never block the response if the channel layer is unavailable

        return Response(
            {
                "message": "Message sent",
                "chat_message": serialized,
            },
            status=status.HTTP_201_CREATED,
        )


class ChatReadReceipts(APIView):
    def post(self, request, room_id):
        requester_ir_id = request.data.get("requester_ir_id")
        message_ids = request.data.get("message_ids") or []

        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        room = get_object_or_404(ChatRoom, id=room_id)
        if not _is_room_member(room, requester):
            return Response({"detail": "Not authorized for this room"}, status=status.HTTP_403_FORBIDDEN)

        if not message_ids:
            return Response({"detail": "message_ids is required"}, status=status.HTTP_400_BAD_REQUEST)

        valid_messages = list(ChatMessage.objects.filter(room=room, id__in=message_ids))
        receipts = [
            ChatMessageReceipt(message=message, reader=requester)
            for message in valid_messages
        ]
        ChatMessageReceipt.objects.bulk_create(receipts, ignore_conflicts=True)

        return Response(
            {
                "message": "Read receipts updated",
                "updated_count": len(valid_messages),
            }
        )


def _get_upline_irs(requester, allowed_levels):
    """Walk up the parent_ir chain and return active Ir objects at the given access levels."""
    upline_ids = []
    current = requester.parent_ir
    seen = set()
    while current and current.ir_id not in seen:
        seen.add(current.ir_id)
        if current.ir_access_level in allowed_levels:
            upline_ids.append(current.ir_id)
        current = current.parent_ir
    return Ir.objects.filter(ir_id__in=upline_ids, status=True)


class ChatCandidates(APIView):
    def get(self, request):
        requester_ir_id = request.GET.get("requester_ir_id")
        query = (request.GET.get("q") or "").strip()
        exclude_room_id = request.GET.get("exclude_room_id")

        try:
            offset = max(0, int(request.GET.get("offset", 0)))
        except (ValueError, TypeError):
            offset = 0
        try:
            limit = min(max(1, int(request.GET.get("limit", 30))), 100)
        except (ValueError, TypeError):
            limit = 30

        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        base_qs = requester.get_viewable_irs().filter(status=True).exclude(ir_id=requester.ir_id)

        # LS can also message their upline LDCs and CTCs
        # LDC can also message their upline CTCs and Admin
        if requester.ir_access_level == AccessLevel.LS:
            upline_qs = _get_upline_irs(requester, [AccessLevel.LDC, AccessLevel.CTC])
            candidates_qs = (base_qs | upline_qs).distinct()
        elif requester.ir_access_level == AccessLevel.LDC:
            upline_qs = _get_upline_irs(requester, [AccessLevel.CTC, AccessLevel.ADMIN])
            candidates_qs = (base_qs | upline_qs).distinct()
        else:
            candidates_qs = base_qs

        if query:
            candidates_qs = candidates_qs.filter(Q(ir_name__icontains=query) | Q(ir_id__icontains=query))

        # Exclude members already in the given room (used by "Add Members" dialog)
        if exclude_room_id:
            existing_ids = ChatRoomMember.objects.filter(
                room_id=exclude_room_id
            ).values_list("ir_id", flat=True)
            candidates_qs = candidates_qs.exclude(ir_id__in=existing_ids)

        candidates_qs = candidates_qs.order_by("ir_name")
        total = candidates_qs.count()
        page = list(candidates_qs[offset: offset + limit])

        return Response(
            {
                "candidates": [
                    {
                        "ir_id": c.ir_id,
                        "ir_name": c.ir_name,
                        "ir_access_level": c.ir_access_level,
                    }
                    for c in page
                ],
                "total": total,
                "offset": offset,
                "limit": limit,
                "has_more": offset + limit < total,
            }
        )


class ChatRoomDelete(APIView):
    def delete(self, request, room_id):
        requester_ir_id = request.data.get("requester_ir_id")
        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        room = get_object_or_404(ChatRoom, id=room_id)

        if not _is_room_member(room, requester):
            return Response({"detail": "Not authorized for this room"}, status=status.HTTP_403_FORBIDDEN)

        # Only the group creator can delete a group; anyone can delete their own direct chat
        if room.room_type == ChatRoomType.GROUP and room.created_by_id != requester.ir_id:
            return Response({"detail": "Only the group owner can delete this group"}, status=status.HTTP_403_FORBIDDEN)

        room.delete()
        return Response({"message": "Room deleted successfully"}, status=status.HTTP_204_NO_CONTENT)


class ChatRoomPin(APIView):
    def post(self, request, room_id):
        requester_ir_id = request.data.get("requester_ir_id")
        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        room = get_object_or_404(ChatRoom, id=room_id)
        if not _is_room_member(room, requester):
            return Response({"detail": "Not authorized for this room"}, status=status.HTTP_403_FORBIDDEN)

        from django.utils import timezone
        room.is_pinned = True
        room.pinned_at = timezone.now()
        room.save(update_fields=["is_pinned", "pinned_at", "updated_at"])

        return Response({
            "message": "Room pinned",
            "room": {
                "id": room.id,
                "is_pinned": room.is_pinned,
                "pinned_at": room.pinned_at,
            },
        })


class ChatRoomUnpin(APIView):
    def post(self, request, room_id):
        requester_ir_id = request.data.get("requester_ir_id")
        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        room = get_object_or_404(ChatRoom, id=room_id)
        if not _is_room_member(room, requester):
            return Response({"detail": "Not authorized for this room"}, status=status.HTTP_403_FORBIDDEN)

        room.is_pinned = False
        room.pinned_at = None
        room.save(update_fields=["is_pinned", "pinned_at", "updated_at"])

        return Response({
            "message": "Room unpinned",
            "room": {
                "id": room.id,
                "is_pinned": room.is_pinned,
                "pinned_at": room.pinned_at,
            },
        })


class ChatMessageEdit(APIView):
    def patch(self, request, room_id, message_id):
        requester_ir_id = request.data.get("requester_ir_id")
        new_content = (request.data.get("content") or "").strip()

        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        room = get_object_or_404(ChatRoom, id=room_id)
        if not _is_room_member(room, requester):
            return Response({"detail": "Not authorized for this room"}, status=status.HTTP_403_FORBIDDEN)

        if not new_content:
            return Response({"detail": "content is required"}, status=status.HTTP_400_BAD_REQUEST)

        message = get_object_or_404(ChatMessage, id=message_id, room=room)

        if message.sender != requester:
            return Response({"detail": "Not authorized to edit this message"}, status=status.HTTP_403_FORBIDDEN)

        if message.is_deleted:
            return Response({"detail": "Cannot edit a deleted message"}, status=status.HTTP_400_BAD_REQUEST)

        message.content = new_content
        message.edited_at = timezone.now()
        message.save(update_fields=["content", "edited_at"])

        return Response({
            "message": "Message updated",
            "chat_message": {
                "id": message.id,
                "content": message.content,
                "edited_at": message.edited_at,
            },
        })


class ChatMessageDelete(APIView):
    def delete(self, request, room_id, message_id):
        requester_ir_id = request.data.get("requester_ir_id")

        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        room = get_object_or_404(ChatRoom, id=room_id)
        if not _is_room_member(room, requester):
            return Response({"detail": "Not authorized for this room"}, status=status.HTTP_403_FORBIDDEN)

        message = get_object_or_404(ChatMessage, id=message_id, room=room)

        if message.sender != requester:
            return Response({"detail": "Not authorized to delete this message"}, status=status.HTTP_403_FORBIDDEN)

        message.is_deleted = True
        message.content = ""
        message.save(update_fields=["is_deleted", "content"])

        return Response({"message": "Message deleted", "message_id": message.id})


# ── Allowed MIME types ────────────────────────────────────────────────────────

_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/heic"}
_VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/webm", "video/x-matroska"}
_VOICE_TYPES = {"audio/webm", "audio/ogg", "audio/mpeg", "audio/mp4", "audio/wav", "audio/x-m4a"}
_FILE_TYPES   = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/plain",
    "text/csv",
    "application/zip",
}

def _detect_message_type(mime: str) -> str:
    if mime in _IMAGE_TYPES:
        return ChatMessageType.IMAGE
    if mime in _VIDEO_TYPES:
        return ChatMessageType.VIDEO
    if mime in _VOICE_TYPES:
        return ChatMessageType.VOICE
    return ChatMessageType.FILE


class ChatMessageUpload(APIView):
    """Upload a file attachment; returns the stored URL and detected message_type."""

    def post(self, request, room_id):
        requester_ir_id = request.data.get("requester_ir_id")
        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        room = get_object_or_404(ChatRoom, id=room_id)
        if not _is_room_member(room, requester):
            return Response({"detail": "Not authorized for this room"}, status=status.HTTP_403_FORBIDDEN)

        uploaded = request.FILES.get("file")
        if not uploaded:
            return Response({"detail": "file is required"}, status=status.HTTP_400_BAD_REQUEST)

        mime = uploaded.content_type or mimetypes.guess_type(uploaded.name)[0] or "application/octet-stream"
        allowed = _IMAGE_TYPES | _VIDEO_TYPES | _VOICE_TYPES | _FILE_TYPES
        if mime not in allowed:
            return Response({"detail": f"File type '{mime}' is not allowed"}, status=status.HTTP_400_BAD_REQUEST)

        max_size = 50 * 1024 * 1024  # 50 MB
        if uploaded.size > max_size:
            return Response({"detail": "File exceeds 50 MB limit"}, status=status.HTTP_400_BAD_REQUEST)

        ext = os.path.splitext(uploaded.name)[1].lower()
        filename = f"chat/{room_id}/{uuid.uuid4().hex}{ext}"
        saved_path = default_storage.save(filename, ContentFile(uploaded.read()))
        # default_storage.url() returns the Cloudinary CDN URL in production,
        # or a local /media/ path in development — works for both.
        file_url = default_storage.url(saved_path)

        return Response({
            "url": file_url,
            "name": uploaded.name,
            "size": uploaded.size,
            "message_type": _detect_message_type(mime),
        }, status=status.HTTP_201_CREATED)


class ChatMessagePin(APIView):
    """Pin a specific message in a room."""

    def post(self, request, room_id, message_id):
        requester_ir_id = request.data.get("requester_ir_id")
        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        room = get_object_or_404(ChatRoom, id=room_id)
        if not _is_room_member(room, requester):
            return Response({"detail": "Not authorized for this room"}, status=status.HTTP_403_FORBIDDEN)

        message = get_object_or_404(ChatMessage, id=message_id, room=room)
        if message.is_deleted:
            return Response({"detail": "Cannot pin a deleted message"}, status=status.HTTP_400_BAD_REQUEST)

        room.pinned_message = message
        room.save(update_fields=["pinned_message", "updated_at"])

        return Response({
            "message": "Message pinned",
            "pinned_message": {
                "id": message.id,
                "sender_name": message.sender.chat_name,
                "content": message.content,
                "message_type": message.message_type,
                "attachment_url": message.attachment_url,
                "attachment_name": message.attachment_name,
            },
        })


class ChatMessageUnpin(APIView):
    """Remove the pinned message from a room."""

    def delete(self, request, room_id, message_id):
        requester_ir_id = request.data.get("requester_ir_id")
        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        room = get_object_or_404(ChatRoom, id=room_id)
        if not _is_room_member(room, requester):
            return Response({"detail": "Not authorized for this room"}, status=status.HTTP_403_FORBIDDEN)

        room.pinned_message = None
        room.save(update_fields=["pinned_message", "updated_at"])

        return Response({"message": "Message unpinned"})


class ChatRoomImageUpload(APIView):
    """Upload or update a group room's avatar image."""

    def post(self, request, room_id):
        requester_ir_id = request.data.get("requester_ir_id")
        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        room = get_object_or_404(ChatRoom, id=room_id)
        if not _is_room_member(room, requester):
            return Response({"detail": "Not authorized for this room"}, status=status.HTTP_403_FORBIDDEN)

        uploaded = request.FILES.get("file")
        if not uploaded:
            return Response({"detail": "file is required"}, status=status.HTTP_400_BAD_REQUEST)

        mime = uploaded.content_type or ""
        if mime not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
            return Response({"detail": "Only image files are allowed"}, status=status.HTTP_400_BAD_REQUEST)

        if uploaded.size > 5 * 1024 * 1024:
            return Response({"detail": "Image must be under 5 MB"}, status=status.HTTP_400_BAD_REQUEST)

        ext = os.path.splitext(uploaded.name)[1].lower() or ".jpg"
        filename = f"chat/groups/{room_id}/{uuid.uuid4().hex}{ext}"
        saved_path = default_storage.save(filename, ContentFile(uploaded.read()))
        image_url = default_storage.url(saved_path)

        room.image_url = image_url
        room.save(update_fields=["image_url", "updated_at"])

        try:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f"chat_room_{room_id}",
                {"type": "room_updated", "room": {"id": room.id, "room_name": room.room_name, "image_url": image_url}},
            )
        except Exception:
            pass

        return Response({"image_url": image_url})


class ChatMessageReceiptDetail(APIView):
    """Return who has read a specific message and when."""

    def get(self, request, room_id, message_id):
        requester_ir_id = request.GET.get("requester_ir_id")
        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        room = get_object_or_404(ChatRoom, id=room_id)
        if not _is_room_member(room, requester):
            return Response({"detail": "Not authorized for this room"}, status=status.HTTP_403_FORBIDDEN)

        message = get_object_or_404(ChatMessage, id=message_id, room=room)
        receipts = ChatMessageReceipt.objects.filter(message=message).select_related("reader").order_by("read_at")

        return Response({
            "receipts": [
                {
                    "ir_id": r.reader.ir_id,
                    "ir_name": r.reader.chat_name,
                    "read_at": r.read_at.isoformat(),
                }
                for r in receipts
            ]
        })


class ChatMessageReactionView(APIView):
    """Add or remove a reaction on a message."""

    def post(self, request, room_id, message_id):
        requester_ir_id = request.data.get("requester_ir_id")
        emoji = (request.data.get("emoji") or "").strip()

        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)
        if not emoji:
            return Response({"detail": "emoji is required"}, status=status.HTTP_400_BAD_REQUEST)

        room = get_object_or_404(ChatRoom, id=room_id)
        if not _is_room_member(room, requester):
            return Response({"detail": "Not authorized for this room"}, status=status.HTTP_403_FORBIDDEN)

        message = get_object_or_404(ChatMessage, id=message_id, room=room)
        if message.is_deleted:
            return Response({"detail": "Cannot react to a deleted message"}, status=status.HTTP_400_BAD_REQUEST)

        reaction, created = ChatMessageReaction.objects.get_or_create(
            message=message, ir=requester, emoji=emoji
        )

        reactions = {}
        for r in ChatMessageReaction.objects.filter(message=message).select_related("ir"):
            reactions.setdefault(r.emoji, []).append({"ir_id": r.ir.ir_id, "ir_name": r.ir.chat_name})

        try:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f"chat_room_{room_id}",
                {
                    "type": "reaction_updated",
                    "message_id": message_id,
                    "reactions": reactions,
                },
            )
        except Exception:
            pass

        return Response({"reactions": reactions}, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    def delete(self, request, room_id, message_id):
        requester_ir_id = request.data.get("requester_ir_id")
        emoji = (request.data.get("emoji") or "").strip()

        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        room = get_object_or_404(ChatRoom, id=room_id)
        if not _is_room_member(room, requester):
            return Response({"detail": "Not authorized for this room"}, status=status.HTTP_403_FORBIDDEN)

        message = get_object_or_404(ChatMessage, id=message_id, room=room)
        ChatMessageReaction.objects.filter(message=message, ir=requester, emoji=emoji).delete()

        reactions = {}
        for r in ChatMessageReaction.objects.filter(message=message).select_related("ir"):
            reactions.setdefault(r.emoji, []).append({"ir_id": r.ir.ir_id, "ir_name": r.ir.chat_name})

        try:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f"chat_room_{room_id}",
                {
                    "type": "reaction_updated",
                    "message_id": message_id,
                    "reactions": reactions,
                },
            )
        except Exception:
            pass

        return Response({"reactions": reactions})


class IrDisplayNameUpdate(APIView):
    """Update the chat display name for a user."""

    def patch(self, request, ir_id):
        requester = _get_ir(ir_id)
        if not requester:
            return Response({"detail": "Invalid IR ID"}, status=status.HTTP_400_BAD_REQUEST)

        display_name = (request.data.get("display_name") or "").strip() or None
        if display_name and len(display_name) > 45:
            return Response({"detail": "Display name must be 45 characters or fewer"}, status=status.HTTP_400_BAD_REQUEST)

        requester.display_name = display_name
        requester.save(update_fields=["display_name"])
        return Response({"display_name": requester.display_name, "chat_name": requester.chat_name})
