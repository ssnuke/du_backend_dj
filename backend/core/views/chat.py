from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import (
    AccessLevel,
    ChatMessage,
    ChatMessageReceipt,
    ChatRoom,
    ChatRoomMember,
    ChatRoomType,
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


def _can_create_room(ir):
    return ir.ir_access_level <= AccessLevel.LS


def _is_room_member(room, ir):
    return ChatRoomMember.objects.filter(room=room, ir=ir).exists()


def _serialize_room(room, requester=None):
    last_message = room.messages.order_by("-id").first()
    unread_count = 0
    if requester:
        unread_count = room.messages.exclude(receipts__reader=requester).exclude(sender=requester).count()

    return {
        "id": room.id,
        "room_type": room.room_type,
        "room_name": room.room_name,
        "category": room.category,
        "created_by_ir_id": room.created_by.ir_id if room.created_by else None,
        "created_at": room.created_at,
        "updated_at": room.updated_at,
        "is_pinned": room.is_pinned,
        "pinned_at": room.pinned_at,
        "member_count": room.memberships.count(),
        "last_message_preview": (last_message.content[:80] if last_message else None),
        "last_message_at": (last_message.created_at if last_message else None),
        "unread_count": unread_count,
    }


def _serialize_message(message):
    return {
        "id": message.id,
        "room_id": message.room_id,
        "sender_ir_id": message.sender.ir_id,
        "sender_name": message.sender.ir_name,
        "message_type": message.message_type,
        "content": message.content,
        "created_at": message.created_at,
        "read_count": message.receipts.count(),
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

        if not _can_create_room(requester):
            return Response({"detail": "Not authorized to create rooms. LS and above only."}, status=status.HTTP_403_FORBIDDEN)

        if room_type not in [ChatRoomType.DIRECT, ChatRoomType.GROUP]:
            return Response({"detail": "Invalid room_type"}, status=status.HTTP_400_BAD_REQUEST)

        if not room_name:
            return Response({"detail": "room_name is required"}, status=status.HTTP_400_BAD_REQUEST)

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

        qs = ChatMessage.objects.filter(room=room).select_related("sender").annotate(read_count=Count("receipts"))

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

        if message_type != "text":
            return Response({"detail": "Only text messages are supported in phase 1"}, status=status.HTTP_400_BAD_REQUEST)

        if not content:
            return Response({"detail": "content is required"}, status=status.HTTP_400_BAD_REQUEST)

        message = ChatMessage.objects.create(
            room=room,
            sender=requester,
            message_type=message_type,
            content=content,
        )
        room.save(update_fields=["updated_at"])

        return Response(
            {
                "message": "Message sent",
                "chat_message": _serialize_message(message),
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


class ChatCandidates(APIView):
    def get(self, request):
        requester_ir_id = request.GET.get("requester_ir_id")
        query = (request.GET.get("q") or "").strip()

        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        candidates_qs = requester.get_viewable_irs().filter(status=True).exclude(ir_id=requester.ir_id)
        if query:
            candidates_qs = candidates_qs.filter(Q(ir_name__icontains=query) | Q(ir_id__icontains=query))

        candidates = candidates_qs.order_by("ir_name")[:30]
        return Response(
            {
                "candidates": [
                    {
                        "ir_id": candidate.ir_id,
                        "ir_name": candidate.ir_name,
                        "ir_access_level": candidate.ir_access_level,
                    }
                    for candidate in candidates
                ]
            }
        )


class ChatRoomDelete(APIView):
    def delete(self, request, room_id):
        requester_ir_id = request.data.get("requester_ir_id")
        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        room = get_object_or_404(ChatRoom, id=room_id)
        
        # Only room creator or admins can delete
        if room.created_by != requester and requester.ir_access_level > AccessLevel.CTC:
            return Response({"detail": "Not authorized to delete this room"}, status=status.HTTP_403_FORBIDDEN)

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
